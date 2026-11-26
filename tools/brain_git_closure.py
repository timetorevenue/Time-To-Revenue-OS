"""Scoped Business Brain Git closure and recovery sweep.

Revisit: when the Shared Brain Git durability contract changes. Last touched: 2026-11-13.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from .business_brain import BUSINESS_BRAIN_ROOT as BRAIN_ROOT
else:
    from business_brain import BUSINESS_BRAIN_ROOT as BRAIN_ROOT

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "logs/runtime/brain_git_closure.json"
TIMEOUT = 20
BRAIN_ORIGIN = "git@github-ttros-brain:owner-gh-account/ttros-business-brain.git"
BRANCH_REF = "refs/heads/main"
PRIVATE_REPO_API = "https://api.github.com/repos/owner-gh-account/ttros-business-brain"


def enabled() -> bool:
    """Only the separately configured Brain sweep has a network write path."""
    return not codex_context() and os.environ.get("TTROS_BRAIN_AUTOPUSH") == "1"


def git(brain: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(brain), *args], capture_output=True,
                          text=True, timeout=TIMEOUT, check=False,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _state_path() -> Path:
    return Path(os.environ.get("TTROS_BRAIN_CLOSURE_STATE", str(STATE)))


def state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record(**changes: object) -> None:
    target = _state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    value = {**state(), **changes}
    fd, name = tempfile.mkstemp(prefix=".brain-closure-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def pending_count(brain: Path) -> int:
    result = git(brain, "rev-list", "--count", "origin/main..HEAD")
    return int(result.stdout.strip()) if result.returncode == 0 else -1


def codex_context() -> bool:
    return any(os.environ.get(key) for key in
               ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "TTROS_CODEX_NO_PUSH"))


MANUAL_CLOSURE_TRAILER = "TTROS-Closure: manual-non-codex"
AUTOMATIC_CLOSURE_TRAILER = "TTROS-Closure: validated-brain-sync"


def write_trailer(source: str) -> str:
    """Recognized ingestion paths retain eligibility across workbench-launched runs."""
    ingestion = source in {
        "drive-watcher:capture", "memory-exchange-import", "memory-exchange-import-v3",
        "memory-filing", "shared-brain-submit", "shared-brain-confirm",
    } or source.startswith(("source-intake:", "source-intake-semantic:"))
    return AUTOMATIC_CLOSURE_TRAILER if ingestion or not codex_context() else MANUAL_CLOSURE_TRAILER


def manual_closure_pending(brain: Path) -> bool:
    """Commit provenance survives process exit; a timer cannot close Codex work."""
    result = git(brain, "log", "--format=%B", "origin/main..HEAD")
    # Unknown provenance is not permission to push.
    return result.returncode != 0 or MANUAL_CLOSURE_TRAILER in result.stdout


def push_commit(brain: Path, sha: str) -> str:
    """Report a writer's sync status. The writer never invokes the network."""
    if codex_context():
        return "pending"
    result = git(brain, "show", "-s", "--format=%(trailers:key=TTROS-Closure)", sha)
    if result.returncode == 0 and result.stdout.strip() == AUTOMATIC_CLOSURE_TRAILER:
        return "pending"
    if manual_closure_pending(brain):
        return "pending_non_codex_closure"
    return "local_only"


def _private_origin() -> bool:
    # GitHub exposes public repositories anonymously; a verified private slug
    # is hidden. The authenticated Git remote must still exist and accept the push.
    try:
        with urlopen(Request(PRIVATE_REPO_API, headers={"Cache-Control": "no-cache"}), timeout=TIMEOUT):
            return False
    except HTTPError as exc:
        if exc.code == 404:
            return True
        raise


def _sync_tip(brain: Path, tip: str) -> tuple[str, str | None]:
    if brain.resolve() != BRAIN_ROOT.resolve():
        return "stopped_unverified_origin", None
    branch = git(brain, "symbolic-ref", "--quiet", "HEAD")
    if branch.returncode or branch.stdout.strip() != BRANCH_REF:
        return "stopped_non_main", None
    for args in (("remote", "get-url", "--all", "origin"), ("remote", "get-url", "--push", "--all", "origin")):
        remote = git(brain, *args)
        if remote.returncode or remote.stdout.strip() != BRAIN_ORIGIN:
            return "stopped_unverified_origin", None
    commits = git(brain, "log", "--format=%H%x09%P%x09%(trailers:key=TTROS-Closure,separator=%x2C)",
                  f"origin/main..{tip}")
    if commits.returncode:
        return "pending_git_error", None
    legacy_tip = os.environ.get("TTROS_BRAIN_SYNC_LEGACY_TIP", "")
    if not re.fullmatch(r"[0-9a-f]{40}", legacy_tip):
        legacy_tip = None
    for line in commits.stdout.splitlines():
        sha, parents, trailer = line.split("\t", 2)
        if len(parents.split()) != 1:
            return "pending_non_codex_closure", None
        if trailer.strip() == AUTOMATIC_CLOSURE_TRAILER:
            continue
        if (legacy_tip and trailer.strip() == MANUAL_CLOSURE_TRAILER
                and git(brain, "merge-base", "--is-ancestor", sha, legacy_tip).returncode == 0):
            continue
        return "pending_non_codex_closure", None
    if not _private_origin():
        return "stopped_unverified_origin", None
    pushed = git(brain, "push", "--no-verify", "--no-mirror", "--no-follow-tags", "--recurse-submodules=no",
                 "origin", f"{tip}:{BRANCH_REF}")
    if pushed.returncode:
        return "pending_push_failed", None
    upstream = git(brain, "ls-remote", "--exit-code", "origin", BRANCH_REF)
    if upstream.returncode or upstream.stdout.strip() != f"{tip}\t{BRANCH_REF}":
        return "pending_upstream_verification", None
    return "synced", tip


@contextmanager
def brain_lock(brain: Path):
    if __package__:
        from .brain_memory import brain_lock_path
    else:
        from brain_memory import brain_lock_path
    with brain_lock_path(brain).open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _alert(message: str) -> None:
    # Existing operator-only Telegram send path; no connector files are inspected.
    from tools.aos_orchestration import default_bridge_send
    recipients = json.loads((ROOT / "queue/notifications.json").read_text())["allowlist"]["telegram"]
    for recipient in recipients:
        default_bridge_send(str(recipient), message)


def sweep(brain: Path, *, alert=_alert) -> dict:
    """Fast-forward the private Brain branch and retry failures on the next poll."""
    verified_sha = None
    with brain_lock(brain):
        previous = state().get("last_sweep")
        try:
            fetched = git(brain, "fetch", "--no-tags", "origin", f"{BRANCH_REF}:refs/remotes/origin/main")
            if fetched.returncode:
                outcome = "pending_remote_unreachable"
            else:
                remote_ahead = git(brain, "rev-list", "--count", "HEAD..origin/main")
                if remote_ahead.returncode or int(remote_ahead.stdout.strip()) > 0:
                    outcome = "stopped_remote_diverged"
                else:
                    pending = pending_count(brain)
                    if pending == 0:
                        outcome = "synced"
                    elif pending < 0:
                        outcome = "pending_git_error"
                    elif manual_closure_pending(brain) and not os.environ.get("TTROS_BRAIN_SYNC_LEGACY_TIP"):
                        outcome = "pending_non_codex_closure"
                    elif enabled():
                        tip = git(brain, "rev-parse", "--verify", "HEAD")
                        if tip.returncode:
                            outcome = "pending_git_error"
                        else:
                            outcome, verified_sha = _sync_tip(brain, tip.stdout.strip())
                    else:
                        outcome = "pending_disabled"
        except (OSError, subprocess.TimeoutExpired, ValueError):
            outcome = "pending_remote_unreachable"
        updates = {"last_sweep": outcome}
        if verified_sha:
            updates["last_verified_push"] = verified_sha
        record(**updates)
    # One alert when the sweep stops, not one per five-minute tick while it stays stopped.
    if outcome == "stopped_remote_diverged" and previous != outcome:
        try:
            alert("TTROS Shared Brain Git sweep stopped: remote diverged. No push was attempted.")
        except Exception:
            record(last_alert="failed")
        else:
            record(last_alert="sent")
    result = {"result": outcome, "pending_count": pending_count(brain)}
    if verified_sha:
        result["upstream_sha"] = verified_sha
    return result


def status(brain: Path) -> dict:
    value = state()
    pending = pending_count(brain)
    return {"pending_count": pending,
            "warning": "three_or_more_unpushed_commits" if pending >= 3 else None,
            "manual_non_codex_closure": manual_closure_pending(brain),
            "last_sweep": value.get("last_sweep", "never"),
            "last_write": value.get("last_write")}


def main() -> int:
    from tools.brain_memory import VAULT_ROOT
    result = sweep(VAULT_ROOT)
    result["generative_model_invocations"] = 0
    result["generative_tokens"] = 0
    # Stat-only change detection for edits made outside write_transaction (e.g. on Windows):
    # changed notes go through the same incremental graph + suggested-link path. Never raises.
    try:
        from tools import brain_graph_links
        changes = brain_graph_links.sweep_changes()
        result["graph_changes"] = {key: (value.get("status") if isinstance(value, dict) else value) for key, value in changes.items()}
    except Exception as exc:  # the Git sweep's own result must still be reported
        result["graph_changes"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    print(json.dumps(result, sort_keys=True))
    return 2 if result["result"] == "stopped_remote_diverged" else 0


if __name__ == "__main__":
    raise SystemExit(main())
