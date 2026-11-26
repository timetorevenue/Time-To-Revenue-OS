#!/usr/bin/env python3
"""Stale-code detection for the long-running Dashboard backend (2026-11-07).

Revisit: when the backend's runtime code moves outside tools/, dashboard/backend/, hooks/ or
the repo-root *.py files, or the runtime release branch changes. · Last touched: 2026-11-18.

On 2026-11-07 aos-backend had been running since before that morning's search commits, so the
Dashboard served old ranking code and nobody could tell. The backend now takes a snapshot of
its runtime Python source (SHA-256 per file) when it starts; /api/health compares that snapshot
with the files on disk and reports `code.stale` plus the changed files.

`--restart-if-stale` is run by the aos-backend-stale.path user unit whenever git moves HEAD
(commit, pull, checkout, reset). It asks the running backend whether it is stale and, only if
so, restarts aos-backend and checks the new process reports `stale: false`. A backend that is
not stale is never restarted.

Release gate (2026-11-18): HEAD moves on a development branch are not releases. On 18 Nov the
live checkout was switched to a feature branch and three development commits restarted the
production backend. An automatic restart now happens only when the live checkout is on a runtime
branch (`main`, or TTROS_RUNTIME_BRANCHES, comma-separated). Elsewhere, including detached HEAD
or an unreadable branch, the restart is reported as `deferred` with the changed files and the
backend keeps running. The deferred code goes live on the next legitimate release (checking out
or fast-forwarding a runtime branch), an explicit `--restart-if-stale --release`, or systemd's
own crash recovery (Restart=always), all of which are unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIRS = ("tools", "dashboard/backend", "hooks")
SKIP_PARTS = {".venv", "__pycache__", "node_modules"}
HEALTH_URL = "http://127.0.0.1:8010/api/health"
DEFAULT_RUNTIME_BRANCHES = ("main",)
UNIT = "aos-backend"
MAX_LISTED = 20


def runtime_files(root: Path = ROOT) -> list[Path]:
    files = [path for path in root.glob("*.py") if path.is_file()]
    for name in RUNTIME_DIRS:
        for path in (root / name).rglob("*.py"):
            rel = path.relative_to(root)
            if SKIP_PARTS.intersection(rel.parts) or path.name.startswith("test_"):
                continue
            files.append(path)
    return sorted(files)


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def snapshot(root: Path = ROOT) -> dict:
    files = runtime_files(root)
    return {"root": str(root), "taken_at": time.time(),
            "files": {str(path.relative_to(root)): _sha(path) for path in files},
            "stats": {str(path.relative_to(root)): _stat(path) for path in files}}


def compare(snap: dict) -> dict:
    """Changed = content differs from the snapshot. Uncached; a file whose size and mtime are
    unchanged is not re-hashed, so a check costs a stat per file plus a hash per touched file."""
    root = Path(snap["root"])
    stats = snap.get("stats") or {}
    now = {}
    for path in runtime_files(root):
        rel = str(path.relative_to(root))
        same_stat = rel in stats and stats[rel] is not None and _stat(path) == tuple(stats[rel])
        now[rel] = snap["files"].get(rel) if same_stat else _sha(path)
    before = snap["files"]
    changed = sorted(rel for rel in set(before) | set(now) if before.get(rel) != now.get(rel))
    return {
        "stale": bool(changed),
        "changed_count": len(changed),
        "changed": changed[:MAX_LISTED],
        "files_checked": len(now),
        "started_at": snap["taken_at"],
        "checked_at": time.time(),
    }


class StalenessMonitor:
    """Snapshot at construction; every status() call compares with disk (no cache: a cached
    "current" answer once hid a change made seconds earlier from the restart unit)."""

    def __init__(self, root: Path = ROOT):
        self._snap = snapshot(root)

    def status(self) -> dict:
        try:
            result = compare(self._snap)
        except Exception as exc:  # health must never fail because of this check
            result = {"stale": None, "error": f"{type(exc).__name__}: {exc}"[:200]}
        if result.get("stale"):
            result["warning"] = ("Dashboard backend is running older code than is on disk; "
                                 "restart aos-backend (systemctl --user restart aos-backend).")
        return result


def _health(timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
        return json.load(response)


def runtime_branches(env=None) -> tuple[str, ...]:
    import os
    value = (os.environ if env is None else env).get("TTROS_RUNTIME_BRANCHES", "")
    branches = tuple(b.strip() for b in value.split(",") if b.strip())
    return branches or DEFAULT_RUNTIME_BRANCHES


def current_branch(root: Path = ROOT, run=subprocess.run) -> str | None:
    """Checked-out branch of the live checkout; None when detached or unreadable."""
    try:
        done = run(["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
                   capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def restart_if_stale(*, wait_seconds: float = 60.0, systemctl=subprocess.run, release: bool = False,
                     dry_run: bool = False, branch=current_branch, env=None) -> dict:
    try:
        code = (_health().get("code") or {})
    except Exception as exc:
        return {"action": "none", "reason": f"backend health unreachable: {type(exc).__name__}"}
    if code.get("stale") is not True:
        return {"action": "none", "reason": "backend code is current" if code.get("stale") is False
                else "backend does not report code staleness", "code": code}
    checked_out, allowed = branch(), runtime_branches(env)
    gate = {"branch": checked_out, "runtime_branches": list(allowed), "release": release}
    if not release and checked_out not in allowed:
        return {"action": "deferred", **gate, "changed": code.get("changed"), "changed_count": code.get("changed_count"),
                "reason": (f"live checkout is on {checked_out or 'a detached or unreadable HEAD'}, not a runtime branch; "
                           "development HEAD moves never restart production. The code goes live on the next "
                           "runtime-branch release or an explicit --restart-if-stale --release.")}
    if dry_run:
        return {"action": "would_restart", **gate, "changed": code.get("changed"), "changed_count": code.get("changed_count")}
    done = systemctl(["systemctl", "--user", "restart", UNIT], capture_output=True, text=True)
    if done.returncode != 0:
        return {"action": "restart_failed", "returncode": done.returncode, "stderr": done.stderr[-300:],
                "changed": code.get("changed")}
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            after = _health(timeout=2.0).get("code") or {}
        except Exception:
            continue
        return {"action": "restarted", **gate, "changed": code.get("changed"), "changed_count": code.get("changed_count"),
                "stale_after_restart": after.get("stale")}
    return {"action": "restarted", "changed": code.get("changed"), "stale_after_restart": None,
            "reason": f"health did not answer within {wait_seconds:.0f}s"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--restart-if-stale", action="store_true", help="restart aos-backend only if it reports stale code")
    parser.add_argument("--check", action="store_true", help="print the running backend's code status")
    parser.add_argument("--release", action="store_true",
                        help="operator-approved release: restart if stale even when not on a runtime branch")
    parser.add_argument("--dry-run", action="store_true", help="report what --restart-if-stale would do; never restart")
    args = parser.parse_args(argv)
    if args.restart_if_stale:
        result = restart_if_stale(release=args.release, dry_run=args.dry_run)
        print(json.dumps(result))
        return 0 if result["action"] in {"none", "restarted", "deferred", "would_restart"} and result.get("stale_after_restart") is not True else 1
    try:
        print(json.dumps(_health().get("code") or {"stale": None, "reason": "backend does not report code staleness"}, indent=2))
    except Exception as exc:
        print(json.dumps({"stale": None, "reason": f"backend health unreachable: {type(exc).__name__}"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
