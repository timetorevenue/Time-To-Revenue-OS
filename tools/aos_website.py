"""Websites & Digital: a scoped website change as one canonical aos_jobs job.

The operating path, and the only one:

  David (or the Dashboard) -> submit_request: resolve the registered website project
  -> aos_jobs job (executor kind "website") -> run_work_order, inside that job:
       preflight   Git state of the project's checkout: HEAD, base, dirt, upstream, worktrees
       worktree    an isolated worktree and branch from the base commit; the checkout is never edited
       icm         the project's own instructions (files in the repo, scoped Brain pointers), hashed
       worker      the coding workbench makes the change in the worktree (Codex under the
                   aos_codex_policy launch contract; a fixture project may use the inert fixture worker)
       scope       every changed path must match the project's allowed paths and none of its forbidden ones
       tests       the project's own test command, in the worktree
       preview     a local copy of the built pages under results/; never served publicly or deployed
       receipt     work order, diff, test log, preview and outcome are hashed job artifacts, so the job's
                   receipt and the Dashboard read the same verified files
  -> publishing is proposed as a WEBSITE_PUBLISH effect that is always held: it needs Casey's
     explicit command naming the action and target (rules/runtime_permission_policy.md), and
     authorization does not reach jobs yet (D-2026-11-15-01). Nothing here pushes, deploys or publishes.

Registry: context/website_projects.json (TTROS_WEBSITE_REGISTRY overrides it for fixtures).

Revisit: when the aos_jobs executor contract, the Codex launch contract or the registry shape changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from aos_job_ids import storage_key
from aos_queue_storage import durable_replace_text

REGISTRY = Path("context") / "website_projects.json"
REGISTRY_ENV = "TTROS_WEBSITE_REGISTRY"
RESULTS = Path("results") / "website"
EXECUTOR_KIND = "website"
ACTION = "change_request"
AGENT_NAME = "website.change_request"
PUBLISH_ACTION = "WEBSITE_PUBLISH"
PUBLISH_HOLD = ("Publishing or deploying needs Casey's explicit command naming the action and target "
                "(rules/runtime_permission_policy.md); this change workflow never publishes, and authorization "
                "does not yet reach delegated jobs (D-2026-11-15-01).")
DEFAULT_TIMEOUT = 300
WORKER_TIMEOUT = 1800
PREVIEW_SUFFIXES = {".html", ".htm", ".css", ".svg", ".txt", ".json", ".png", ".jpg", ".jpeg", ".webp"}
PREVIEW_MAX_FILES, PREVIEW_MAX_BYTES = 40, 2_000_000
PREVIEW_FILE_CEILING = 8_000_000
ALWAYS_FORBIDDEN = (".git/*", ".github/workflows/*", "*.env", ".env*", "**/.env*", "*secret*", "*credential*")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


class WebsiteError(ValueError):
    """The request cannot become a job; nothing was created."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


# -- registry -----------------------------------------------------------------------

def registry_path(root: Path) -> Path:
    override = os.environ.get(REGISTRY_ENV, "").strip()
    return Path(override) if override else Path(root) / REGISTRY


def load_registry(root: Path) -> dict:
    path = registry_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "projects": {}}
    except (OSError, ValueError) as exc:
        raise WebsiteError(f"website project registry is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise WebsiteError("website project registry has no projects table")
    for project_id in data["projects"]:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise WebsiteError(f"invalid website project id {project_id!r}")
    return data


def project_state(project: dict) -> tuple[str, str]:
    """(state, why) — 'ready' only when the checkout exists and is a Git work tree."""
    repo = str(project.get("repository") or "").strip()
    if not repo:
        return "not_checked_out", "no local checkout is registered on this host"
    path = Path(repo)
    if not path.is_absolute() or str(path).startswith("/mnt/"):
        return "unsupported_path", "the checkout must be an absolute Linux-native path"
    if not (path / ".git").exists():
        return "not_checked_out", f"{repo} is not a Git checkout"
    if project.get("worker") not in ("codex", "fixture"):
        return "no_worker", "no coding worker is configured for this project"
    if project.get("worker") == "fixture" and not project.get("fixture"):
        return "no_worker", "the fixture worker is only for fixture projects"
    return "ready", ""


def list_projects(root: Path) -> list[dict]:
    rows = []
    for project_id, project in load_registry(root)["projects"].items():
        state, why = project_state(project)
        rows.append({"id": project_id, "name": project.get("name") or project_id, "state": state, "why": why,
                     "fixture": bool(project.get("fixture")), "client_scope": project.get("client_scope") or "global",
                     "worker": project.get("worker") or "", "allowed_paths": project.get("allowed_paths") or [],
                     "publish_target": (project.get("publish") or {}).get("target") or "",
                     "aliases": project.get("aliases") or []})
        rows[-1].update(repository=project.get("repository") or "", base_ref=project.get("base_ref") or "",
                        icm_files=project.get("icm_files") or [], required_assets=project.get("required_assets") or [])
    return rows


def resolve_project(root: Path, reference: str) -> tuple[str, dict]:
    """Exact id, or a unique alias match. Ambiguity and unknown names are refused."""
    projects = load_registry(root)["projects"]
    wanted = _text(reference, 120).casefold()
    if not wanted:
        raise WebsiteError("which website? name a registered project: " + ", ".join(sorted(projects)) if projects
                           else "no website projects are registered")
    if wanted in projects:
        return wanted, projects[wanted]
    matches = [pid for pid, project in projects.items()
               if wanted in {str(alias).casefold() for alias in project.get("aliases") or []}
               or wanted == str(project.get("name") or "").casefold()]
    if len(matches) == 1:
        return matches[0], projects[matches[0]]
    if len(matches) > 1:
        raise WebsiteError(f"'{reference}' matches several website projects: {', '.join(sorted(matches))}")
    raise WebsiteError(f"no registered website project is called '{reference}'; registered: "
                       + (", ".join(sorted(projects)) or "none"))


# -- submission ---------------------------------------------------------------------

def executor_for(project_id: str, request: str) -> dict:
    return {"kind": EXECUTOR_KIND, "action": ACTION, "project": project_id, "input": {"request": request}}


def submit_request(root: Path, *, project: str, request: str, conversation_id: str, topic: str = "website",
                   chat_id: str = "", requested_by: str = "casey", dedupe_key: str = "",
                   restrictions: list[str] | tuple = ()) -> tuple[dict, bool]:
    """Create (or, with the same dedupe key, return) the job for one scoped website change."""
    import aos_jobs

    project_id, record = resolve_project(root, project)
    state, why = project_state(record)
    if state != "ready":
        raise WebsiteError(f"website project {project_id} cannot take changes here: {why}")
    request = _text(request, 1000)
    if len(request) < 8:
        raise WebsiteError("describe the change (what should be different on the site)")
    return aos_jobs.create(Path(root), conversation_id=conversation_id, topic=topic, request=request,
                           executor=executor_for(project_id, request), chat_id=chat_id, dedupe_key=dedupe_key,
                           requested_by=requested_by, restrictions=list(restrictions))


def is_website_item(item: dict | None) -> bool:
    spec = (item or {}).get("executor")
    return isinstance(spec, dict) and spec.get("kind") == EXECUTOR_KIND and spec.get("action") == ACTION


# -- processes ----------------------------------------------------------------------

def _git_env() -> dict[str, str]:
    from aos_codex_policy import NO_PUSH_GIT_ENV

    env = {**os.environ, **NO_PUSH_GIT_ENV, "GIT_TERMINAL_PROMPT": "0", "TTROS_CODEX_NO_PUSH": "1",
           "GIT_OPTIONAL_LOCKS": "0",  # a read-only preflight never rewrites the checkout's index
           "GIT_AUTHOR_NAME": "TTROS website worker", "GIT_AUTHOR_EMAIL": "website-worker@ttros.invalid",
           "GIT_COMMITTER_NAME": "TTROS website worker", "GIT_COMMITTER_EMAIL": "website-worker@ttros.invalid"}
    return env


def _git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_git_env(), timeout=60)
    if check and done.returncode != 0:
        raise WebsiteError(f"git {' '.join(args[:2])} failed: {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout.strip()


def run_bounded(argv: list[str], *, cwd: Path, timeout: float, env: dict | None = None) -> dict:
    """Run one command in its own process group; on timeout the whole group is killed."""
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=str(cwd), env=env or _git_env(), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True, text=True)
    try:
        output, _ = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        timed_out = True
    return {"argv": argv, "returncode": process.returncode, "timed_out": timed_out, "output": output or "",
            "seconds": round(time.monotonic() - started, 2)}


def _command(project: dict, key: str) -> list[str] | None:
    value = project.get(key)
    if not value:
        return None
    if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
        raise WebsiteError(f"{key} must be an argument list")
    return [sys.executable if part == "{python}" else part for part in value]


# -- preflight and worktree ---------------------------------------------------------

def preflight(project: dict) -> dict:
    """Read-only Git state of the registered checkout. Fetches nothing."""
    repo = Path(project["repository"])
    base_ref = str(project.get("base_ref") or "main")
    status = _git(repo, "status", "--porcelain=v1", "--branch")
    lines = status.splitlines()
    upstream = {}
    branch_line = lines[0] if lines and lines[0].startswith("##") else ""
    match = re.search(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]", branch_line)
    if match:
        upstream = {"ahead": int(match.group(1) or 0), "behind": int(match.group(2) or 0)}
    try:
        base_sha = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    except WebsiteError as exc:
        raise WebsiteError(f"base ref {base_ref} does not exist in {repo}") from exc
    worktrees = [line.split(" ", 1)[1] for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
                 if line.startswith("worktree ")]
    return {"repository": str(repo), "checked_at": _now(), "head": _git(repo, "rev-parse", "HEAD"),
            "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "base_ref": base_ref, "base_sha": base_sha,
            "dirty": [line for line in lines[1:] if line], "branch_line": branch_line, "upstream": upstream,
            "worktrees": worktrees, "fetch": "not performed (local state only)",
            "checkout_untouched": "changes are made in an isolated worktree, never in this checkout"}


def _worktree(project: dict, job_id: str, folder: Path, base_sha: str, branch: str = "") -> tuple[Path, str, bool]:
    repo = Path(project["repository"])
    branch = branch or "ttros/website/" + storage_key(job_id).lower()
    path = folder / "worktree"
    if path.exists():
        # A recovered job continues in its own worktree; anything else is refused.
        current = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        if current != branch:
            raise WebsiteError(f"{path} exists but is not this job's worktree")
        return path, branch, True
    folder.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), base_sha)
    return path, branch, False


def dependency_cache(project: dict, repo: Path) -> Path | None:
    """Operator-installed offline dependencies, bound to the source lockfile."""
    cache = project.get('dependency_cache')
    if not cache:
        return None
    from aos_paths import AuthorityError, assert_authoritative_root
    if not isinstance(cache, dict) or not isinstance(cache.get('path'), str):
        raise WebsiteError('dependency cache needs an absolute Linux path and lock hash')
    path = Path(cache['path'])
    try:
        assert_authoritative_root(path)
    except AuthorityError as exc:
        raise WebsiteError(str(exc)) from exc
    if path.is_symlink() or not path.is_dir() or path.name != 'node_modules':
        raise WebsiteError('dependency cache must be a Linux-native node_modules directory')
    if _sha((repo / 'package-lock.json').read_bytes()) != cache.get('lock_sha256'):
        raise WebsiteError('dependency cache lockfile mismatch; reinstall and register dependencies')
    for child in path.rglob('*'):
        if child.is_symlink() and not child.resolve().is_relative_to(path.resolve()):
            raise WebsiteError('dependency cache contains an escaping symlink')
    return path


def install_cached_dependencies(project: dict, repo: Path, worktree: Path) -> None:
    cache = dependency_cache(project, repo)
    if cache is None:
        return
    target = worktree / 'node_modules'
    if target.is_symlink():
        raise WebsiteError('job dependencies must not redirect outside the worktree')
    if not target.exists():
        shutil.copytree(cache, target, symlinks=True)


def _changed_paths(worktree: Path, base_sha: str) -> list[str]:
    tracked = _git(worktree, "diff", "--name-only", base_sha).splitlines()
    untracked = _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({path for path in tracked + untracked if path})


def scope_problems(project: dict, paths: list[str]) -> list[str]:
    allowed = [str(p) for p in project.get("allowed_paths") or []]
    forbidden = list(ALWAYS_FORBIDDEN) + [str(p) for p in project.get("forbidden_paths") or []]
    problems = []
    for path in paths:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in forbidden):
            problems.append(f"{path} is a protected path")
        elif not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed):
            problems.append(f"{path} is outside the project's allowed paths")
    return problems


# -- ICM ----------------------------------------------------------------------------

def gather_icm(project: dict, worktree: Path, *, registry=None, brain_read: Callable | None = None) -> dict:
    """The project's instructions: repo files (hashed text) and scope-checked Brain pointers."""
    files, brain, problems = [], [], []
    for relative in project.get("icm_files") or []:
        path = (worktree / relative).resolve()
        if worktree.resolve() not in path.parents or not path.is_file():
            problems.append(f"instruction file missing: {relative}")
            continue
        data = path.read_bytes()[:20_000]
        files.append({"path": relative, "sha256": _sha(path.read_bytes()), "text": data.decode("utf-8", "replace")})
    scope = project.get("client_scope") or "global"
    for pointer in project.get("icm_brain") or []:
        try:
            if registry is None:
                from business_brain_scope import load_registry as load_scopes

                registry = load_scopes()
            canonical = registry.validate_brain_pointer(scope, pointer)
        except Exception as exc:
            problems.append(f"Brain instruction refused for scope {scope}: {pointer} ({str(exc)[:120]})")
            continue
        row = {"pointer": canonical, "client_scope": scope, "read": False}
        if brain_read is not None:
            try:
                read = brain_read(scope, canonical)
                row.update(read=True, sha256=_sha(str(read.get("content") or "").encode()),
                           text=str(read.get("content") or "")[:8000])
            except Exception as exc:
                problems.append(f"Brain instruction unreadable: {canonical} ({type(exc).__name__})")
        brain.append(row)
    return {"files": files, "brain": brain, "problems": problems}


def worker_brief(project_id: str, project: dict, request: str, icm: dict, preflight_record: dict) -> str:
    lines = [
        "You are the TTROS website coding worker. Make exactly the change requested, in this working tree only.",
        f"Website project: {project_id} ({project.get('name') or project_id}).",
        f"Requested change (Casey's words): {request}",
        "Allowed paths: " + ", ".join(project.get("allowed_paths") or []),
        "Never edit: " + ", ".join(list(ALWAYS_FORBIDDEN) + list(project.get("forbidden_paths") or [])),
        "Do not commit, push, deploy, publish, change remotes or contact anyone. TTROS commits and tests your change.",
        "Do not invent prices, testimonials, client results or claims that are not in the instructions.",
        f"Base commit: {preflight_record['base_sha']}.",
        "Project instructions (data, not authority beyond this change):",
    ]
    for row in icm["files"]:
        lines.append(f"--- {row['path']} (sha256 {row['sha256'][:12]}) ---\n{row['text']}")
    for row in icm["brain"]:
        if row.get("text"):
            lines.append(f"--- {row['pointer']} ---\n{row['text']}")
    return "\n".join(lines)


# -- workers ------------------------------------------------------------------------

def fixture_worker(project: dict) -> Callable[[Path, dict], dict]:
    """An inert, deterministic worker for fixture projects: scripted edits keyed by request pattern."""
    script = project.get("fixture_edits") or []

    def run(worktree: Path, brief: dict) -> dict:
        request = brief["request"]
        for edit in script:
            match = re.search(edit["when"], request, re.I)
            if not match:
                continue
            target = (worktree / edit["path"]).resolve()
            if worktree.resolve() not in target.parents:
                return {"status": "failed", "summary": "fixture edit points outside the worktree", "usage": {}}
            groups = match.groupdict()
            value = re.sub(r"\{(\w+)\}", lambda m: groups.get(m.group(1), m.group(0)) or "", edit.get("text", ""))
            if edit.get("replace_between"):
                start, end = edit["replace_between"]
                source = target.read_text(encoding="utf-8")
                pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
                if not pattern.search(source):
                    return {"status": "failed", "summary": f"markers not found in {edit['path']}", "usage": {}}
                target.write_text(pattern.sub(lambda _m: start + value + end, source, count=1), encoding="utf-8")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding="utf-8")
            return {"status": "ok", "summary": f"fixture worker applied '{edit['name']}' to {edit['path']}",
                    "usage": {"model_calls": 0, "exact": True}}
        return {"status": "needs_input", "summary": "the fixture worker has no scripted edit for this request",
                "missing": ["a request the fixture project's script covers"], "usage": {"model_calls": 0, "exact": True}}
    return run


def codex_worker(job_id: str, *, ledger_root: Path, timeout: float = WORKER_TIMEOUT,
                 codex_account: Any = None) -> Callable[[Path, dict], dict]:
    """The Codex implementation workbench under the existing aos_codex_policy launch contract.

    Fresh ephemeral session on the job's Codex account (``codex_account``, an AccountPin; a fresh
    selection without one), ChatGPT subscription route only (a billed-route variable refuses the
    launch), no-push Git environment, 500K session fuse, exact usage written to the token ledger
    (or recorded as unavailable, never as zero) with the account slot.
    """
    import aos_codex_policy as policy
    import step6_cost_control as cost_control

    def run(worktree: Path, brief: dict) -> dict:
        nothing = {"model_calls": 0, "exact": True}
        try:
            chosen = (codex_account or policy.AccountPin()).target()
        except policy.AccountUnavailable as exc:
            return {"status": "failed", "failure_class": exc.failure_class, "launched": False,
                    "summary": f"Codex not started: {exc.detail}", "usage": nothing}
        target = replace(chosen, root=worktree.resolve())
        try:
            policy.validate_runtime(worktree, target)
            command = policy.build_exec_command(target, cost_dial="standard")
            env = policy.build_environment(target)
            policy.require_subscription_environment(env)
        except policy.CodexPolicyError as exc:
            billed = bool(policy.billed_route_variables(os.environ))
            return {"status": "failed", "failure_class": "billed_route_refused" if billed else "codex_unavailable",
                    "launched": False, "summary": f"Codex workbench unavailable: {exc}", "usage": nothing}
        account = target.account_record()
        invocation_id = f"website-{uuid.uuid4().hex}"
        try:
            scope, _ = cost_control.codex_session_preflight(invocation_id, work_item_id=job_id, root=ledger_root)
        except cost_control.CostControlError as exc:
            return {"status": "refused", "summary": f"token fuse refused the session: {exc}", "usage": {"model_calls": 0, "exact": True}}
        prompt = policy.prepare_fresh_prompt(brief["prompt"])
        fuse = policy.StreamingSessionFuse(prompt)
        outcome = policy.run_streaming(command, cwd=worktree, env=env, prompt=prompt, timeout_seconds=timeout, fuse=fuse)
        usage = policy.cumulative_usage_snapshot(outcome["stdout"])
        selection = {"tier": "website_worker", "model": policy.CODEX_MODEL, "reasoning_effort": "medium",
                     "actual_model": policy.CODEX_MODEL, "reason": "website change worker (Codex workbench)",
                     "actual_model_source": "aos_codex_policy.build_exec_command", "codex_account": account}
        try:
            if usage.get("available"):
                cost_control.record_invocation(scope, invocation_id=invocation_id, provider="openai-codex",
                                               model=policy.CODEX_MODEL, root=ledger_root, surface="website:codex",
                                               work_item_id=job_id, model_selection=selection,
                                               usage={"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                                                      "cached_input_tokens": usage.get("cached_input_tokens") or 0,
                                                      "cache_semantics": "included_in_provider_input"})
            else:
                cost_control.record_unavailable_invocation(scope, invocation_id=invocation_id, provider="openai-codex",
                                                           model=policy.CODEX_MODEL, root=ledger_root, surface="website:codex",
                                                           work_item_id=job_id, model_selection=selection,
                                                           reason=str(usage.get("reason") or "usage unavailable"))
        except cost_control.CostControlError:
            pass  # the job usage below still states what is known
        ok = outcome["returncode"] == 0 and not outcome["timed_out"] and not outcome["fuse_breach"]
        provider = "" if ok or outcome["timed_out"] or outcome["fuse_breach"] else \
            policy.provider_failure_class(outcome["stdout"], outcome["stderr"])
        summary = ("Codex finished the change" if ok else "Codex stopped: " +
                   ("timeout" if outcome["timed_out"] else "token fuse" if outcome["fuse_breach"]
                    else f"{provider} (exit {outcome['returncode']})" if provider else f"exit {outcome['returncode']}"))
        return {"status": "ok" if ok else "failed", "summary": summary, "invocation_id": invocation_id,
                "failure_class": "" if ok else provider or "worker_failed", "launched": True, "codex_account": account,
                "usage": {"model_calls": 1, "exact": bool(usage.get("available")),
                          "input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0),
                          "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                          "total_tokens": int(usage.get("cumulative_tokens") or 0)}}
    return run


# -- the work order -----------------------------------------------------------------

def _usage(worker_usage: dict) -> dict:
    usage = {"model_calls": int(worker_usage.get("model_calls") or 0), "input_tokens": int(worker_usage.get("input_tokens") or 0),
             "output_tokens": int(worker_usage.get("output_tokens") or 0),
             "cached_input_tokens": int(worker_usage.get("cached_input_tokens") or 0),
             "total_tokens": int(worker_usage.get("total_tokens") or 0), "exact": bool(worker_usage.get("exact", True)),
             "latency_seconds": 0.0, "calls": []}
    if worker_usage.get("invocation_id"):
        usage["calls"] = [{"invocation_id": worker_usage["invocation_id"], "agent": AGENT_NAME, "step": "worker",
                           "status": "ok", "model": "codex", "tokens": usage["total_tokens"], "exact": usage["exact"]}]
    return usage


def preview_bytes(source: Path, path: Path, url_prefix: str = "", *, included: set[str] | None = None) -> bytes:
    """Relocate registered public-path assets into the sandboxed preview directory.

    The built/source files remain unchanged. Only local href/src and CSS url()
    references under the exact registered prefix are rewritten in the review copy.
    External URLs and navigation outside that subtree retain their original values.
    """
    data = path.read_bytes()
    if not url_prefix or path.suffix.lower() not in {".html", ".htm", ".css"}:
        return data
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*", url_prefix):
        raise WebsiteError("preview url_prefix must be an absolute URL directory without traversal")
    from aos_website_prepare import safe_path

    def relocate(match):
        url = match.group("url")
        if not url.startswith(url_prefix) or url.startswith("//"):
            return match.group(0)
        relative, *fragment = re.split(r"(?=[?#])", url[len(url_prefix):], maxsplit=1)
        target = safe_path(source, relative)
        if not target.is_file():
            raise WebsiteError(f"preview asset is missing: {relative}")
        if included is not None and relative not in included:
            raise WebsiteError(f"preview asset omitted by file, size or type limit: {relative}")
        local = posixpath.relpath(relative, path.relative_to(source).parent.as_posix())
        return match.group(0).replace(url, local + (fragment[0] if fragment else ""), 1)

    text = data.decode("utf-8")
    pattern = r'''(?:\b(?:src|href)\s*=\s*["']|\burl\(\s*["']?)(?P<url>/[^\s"'<>)]*)'''
    return re.sub(pattern, relocate, text, flags=re.I).encode("utf-8")


def preview_asset_problems(files: dict[str, bytes]) -> list[str]:
    """Check copied local resources, including artifacts from older completed jobs."""
    problems = []
    pattern = r'''(?:\bsrc\s*=\s*["']|\bhref\s*=\s*["']|\burl\(\s*["']?)([^\s"'<>)]*)'''
    for relative, data in files.items():
        if Path(relative).suffix.lower() not in {".html", ".htm", ".css"}:
            continue
        for raw in re.findall(pattern, data.decode("utf-8", "replace"), re.I):
            url = urlsplit(raw)
            if not url.path or url.scheme or url.netloc or url.path.startswith('/'):
                continue  # external URLs and navigation outside the preview are not asserted
            target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), unquote(url.path)))
            if target not in files:
                problems.append(f"preview local resource missing: {relative} -> {url.path}")
    return sorted(set(problems))


def run_work_order(root: Path, item: dict, *, worker: Callable[[Path, dict], dict] | None = None,
                   brain_read: Callable | None = None, scope_registry=None, codex_account: Any = None,
                   **_ignored: Any) -> dict:
    """Run one website change inside its job. Returns the executor's worker-result shape."""
    root = Path(root)
    job_id = str(item["id"])
    spec = item["executor"]
    preparation = (spec.get("input") or {}).get("preparation")
    raw_request = (spec.get("input") or {}).get("request") or ""
    request = raw_request if preparation else _text(raw_request, 1000)
    folder = root / RESULTS / storage_key(job_id)
    # Refuse output redirection before writing even a failure artifact.
    from aos_website_prepare import safe_path
    safe_path(root, folder.relative_to(root).as_posix())
    steps: list[dict] = []
    artifacts: list[dict] = []
    outcome: dict[str, Any] = {"job_id": job_id, "project": spec.get("project"), "request": request,
                               "publish": {"status": "not_ready", "detail": "no verified preview"}}
    usage: dict = _usage({})

    def artifact(name: str, data: bytes, ref: str) -> None:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append({"ref": ref, "path": path.relative_to(root).as_posix(), "sha256": _sha(data)})

    def finish(status: str, failure_class: str = "", detail: str = "", missing: list | None = None) -> dict:
        outcome.update(status=status, failure_class=failure_class, detail=detail, steps=steps, finished_at=_now())
        artifact("outcome.json", (json.dumps(outcome, indent=1, sort_keys=True) + "\n").encode(), "outcome")
        summary = outcome.get("summary") or detail
        held = []
        if status == "ok":
            held = [{"effect": "website.publish", "action": PUBLISH_ACTION, "target": outcome["publish"]["target"],
                     "authorization": {"authorized": False, "basis": "", "detail": PUBLISH_HOLD}}]
        result = {"status": status, "agent": AGENT_NAME, "plan": "", "failure_class": failure_class, "detail": detail,
                  "output": {"summary": summary, **({"missing": missing} if missing else {})},
                  "artifacts": artifacts, "proposed_effects": held, "evidence_refs": [], "usage": usage,
                  "steps": [{"id": row["id"], "agent": AGENT_NAME, "status": row["status"], "replayed": False}
                            for row in steps]}
        return {"success": status == "ok", "agent_result": result, "brain_context_used": [
            {"path": row["pointer"], "client_scope": row["client_scope"], "note_id": row["pointer"]}
            for row in outcome.get("icm", {}).get("brain", []) if row.get("read")]}

    def step(name: str, status: str, **detail: Any) -> None:
        steps.append({"id": name, "status": status, "at": _now(), **detail})

    try:
        project_id, project = resolve_project(root, str(spec.get("project") or ""))
    except WebsiteError as exc:
        step("project", "failed", detail=str(exc))
        return finish("refused", "website_project_unknown", str(exc))
    state, why = project_state(project)
    outcome["project_name"] = project.get("name") or project_id
    if state != "ready":
        step("project", "failed", detail=why)
        return finish("refused", "website_project_unavailable", why)
    outcome["publish"] = {"status": "not_ready", "target": (project.get("publish") or {}).get("target") or "unconfigured",
                          "detail": "no verified preview"}
    step("project", "ok", project=project_id)

    if preparation:
        from aos_website_prepare import inspect
        checked = inspect(root, preparation, job_id=job_id)
        if not isinstance(preparation.get("snapshot"), dict) or preparation.get("state") != "prepared":
            checked["problems"].append("a prepared work order with a baseline snapshot is required")
        if preparation.get("project") != project_id or preparation.get("request") != request:
            checked["problems"].append("executor and preparation project/request identity mismatch")
        if checked["problems"]:
            step("preparation", "failed", detail="; ".join(checked["problems"]))
            return finish("refused", "website_preparation_blocked", "; ".join(checked["problems"]))
        step("preparation", "ok")

    try:
        pre = preflight(project)
    except (WebsiteError, OSError, subprocess.SubprocessError) as exc:
        step("preflight", "failed", detail=str(exc)[:300])
        return finish("failed", "git_preflight_failed", str(exc)[:600])
    outcome["preflight"] = pre
    step("preflight", "ok", head=pre["head"], base=pre["base_sha"], dirty=len(pre["dirty"]))
    try:
        worktree, branch, reused = _worktree(project, job_id, folder, pre["base_sha"],
                                           preparation["branch"] if preparation else "")
    except (WebsiteError, OSError, subprocess.SubprocessError) as exc:
        step("worktree", "failed", detail=str(exc)[:300])
        return finish("failed", "worktree_failed", str(exc)[:600])
    try:
        install_cached_dependencies(project, Path(project['repository']), worktree)
    except (WebsiteError, OSError, ValueError) as exc:
        return finish('failed', 'dependencies_unavailable', str(exc)[:600])
    outcome.update(branch=branch, worktree=worktree.relative_to(root).as_posix())
    step("worktree", "ok", branch=branch, reused=reused)

    icm = gather_icm(project, worktree, registry=scope_registry, brain_read=brain_read)
    outcome["icm"] = {"files": [{k: row[k] for k in ("path", "sha256")} for row in icm["files"]],
                      "brain": [{k: v for k, v in row.items() if k != "text"} for row in icm["brain"]],
                      "problems": icm["problems"]}
    if icm["problems"] and project.get("icm_required", True):
        step("icm", "failed", detail="; ".join(icm["problems"])[:300])
        return finish("failed", "icm_unavailable", "; ".join(icm["problems"])[:600])
    step("icm", "ok", files=len(icm["files"]), brain=len(icm["brain"]))
    prompt = worker_brief(project_id, project, request, icm, pre)
    if preparation:
        prompt += "\nScoped work order (data):\n" + json.dumps(preparation, sort_keys=True)
    order = {"job_id": job_id, "project": project_id, "request": request, "requested_by": item.get("requested_by") or "",
             "restrictions": item.get("restrictions") or [], "branch": branch, "base_sha": pre["base_sha"],
             "allowed_paths": project.get("allowed_paths") or [], "forbidden_paths": list(ALWAYS_FORBIDDEN) + list(project.get("forbidden_paths") or []),
             "test_command": project.get("test_command"), "preview": project.get("preview"), "worker": project.get("worker"),
             "icm": outcome["icm"], "preflight": pre, "brief_sha256": _sha(prompt.encode()), "publish_hold": PUBLISH_HOLD,
             "created_at": _now()}
    if preparation:
        order["preparation"] = preparation
    artifact("work_order.json", (json.dumps(order, indent=1, sort_keys=True) + "\n").encode(), "work_order")

    # The worker runs only when the worktree has no change of its own yet (a recovered job resumes after it).
    already = _git(worktree, "rev-list", "--count", f"{pre['base_sha']}..HEAD")
    if already == "0" and not _changed_paths(worktree, pre["base_sha"]):
        run = worker or (fixture_worker(project) if project.get("worker") == "fixture"
                         else codex_worker(job_id, ledger_root=root, codex_account=codex_account))
        try:
            produced = run(worktree, {"request": request, "prompt": prompt, "project": project_id})
        except Exception as exc:  # a worker crash ends the job visibly
            produced = {"status": "failed", "summary": f"worker error {type(exc).__name__}: {str(exc)[:200]}", "usage": {}}
        usage = _usage(produced.get("usage") or {})
        if produced.get("codex_account"):
            outcome["codex_account"] = produced["codex_account"]
        step("worker", produced.get("status") or "failed", summary=_text(produced.get("summary"), 300))
        if produced.get("status") != "ok":
            # A website job cannot be resumed (only planned jobs can), so an unclear request ends the job
            # with what was missing, and Casey asks again with the detail: a new job, never a silent guess.
            unclear = produced.get("status") == "needs_input"
            missing = "; ".join(str(m) for m in produced.get("missing") or [])
            # A quota or sign-in stop keeps its own class (quota_unavailable, needs_authentication,
            # billed_route_refused), never the generic worker_failed.
            return finish("failed", "change_request_unclear" if unclear else str(produced.get("failure_class") or "worker_failed"),
                          _text(produced.get("summary"), 400) + (f" Missing: {missing}" if missing else ""))
    else:
        step("worker", "ok", summary="the job worktree already contains a local change; coding worker not invoked in this attempt",
             replayed=True)

    changed = _changed_paths(worktree, pre["base_sha"])
    if not changed:
        step("scope", "failed", detail="no file changed")
        return finish("failed", "no_change", "the worker made no change")
    problems = scope_problems(project, changed)
    if preparation:
        from aos_website_prepare import acceptance
        problems.extend(acceptance(preparation, worktree, changed))
    outcome["changed_files"] = changed
    if problems:
        step("scope", "failed", detail="; ".join(problems)[:300])
        # Do not stage/read a rejected secret or symlink target just to produce evidence.
        artifact("scope_rejected.json", json.dumps({"paths": changed, "problems": problems}).encode(), "scope_rejected")
        return finish("failed", "out_of_scope_change", "; ".join(problems)[:600])
    step("scope", "ok", files=changed)
    if _git(worktree, "status", "--porcelain"):
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-q", "-m", f"{job_id}: {request[:60]}\n\nTTROS website change; not pushed or published.")
    commit = _git(worktree, "rev-parse", "HEAD")
    diff = _git(worktree, "diff", f"{pre['base_sha']}..{commit}")
    stat = _git(worktree, "diff", "--stat", f"{pre['base_sha']}..{commit}")
    outcome.update(commit=commit, diff_stat=stat)
    artifact("diff.patch", (diff + "\n").encode(), "diff")

    test_cmd = _command(project, "test_command")
    if test_cmd:
        tested = run_bounded(test_cmd, cwd=worktree, timeout=float(project.get("test_timeout") or DEFAULT_TIMEOUT))
        log = f"$ {' '.join(test_cmd)}\n(exit {tested['returncode']}, {tested['seconds']}s{', TIMED OUT' if tested['timed_out'] else ''})\n{tested['output']}"
        artifact("tests.log", log.encode(), "tests")
        passed = tested["returncode"] == 0 and not tested["timed_out"]
        outcome["tests"] = {"command": test_cmd, "returncode": tested["returncode"], "passed": passed,
                            "timed_out": tested["timed_out"], "seconds": tested["seconds"]}
        step("tests", "ok" if passed else "failed", returncode=tested["returncode"])
        if not passed:
            return finish("failed", "tests_failed", f"the project's tests failed (exit {tested['returncode']}); see tests.log")
    else:
        outcome["tests"] = {"command": None, "passed": None, "detail": "the project defines no test command"}
        step("tests", "skipped")

    preview = project.get("preview") or {}
    if preview:
        build = _command(preview, "command")
        if build:
            built = run_bounded(build, cwd=worktree, timeout=float(project.get("test_timeout") or DEFAULT_TIMEOUT))
            artifact("preview_build.log", (f"$ {' '.join(build)}\n(exit {built['returncode']})\n" + built["output"]).encode(), "preview_build")
            if built["returncode"] != 0 or built["timed_out"]:
                step("preview", "failed", returncode=built["returncode"])
                return finish("failed", "preview_failed", "the preview build failed; see preview_build.log")
        source = (worktree / str(preview.get("dir") or ".")).resolve()
        if worktree.resolve() not in [source, *source.parents] or not source.is_dir():
            step("preview", "failed", detail="preview directory missing")
            return finish("failed", "preview_failed", "the preview directory does not exist")
        files, copied = [], {}
        limit = preview.get("max_file_bytes", PREVIEW_MAX_BYTES)
        if type(limit) is not int or not 0 < limit <= PREVIEW_FILE_CEILING:
            return finish("failed", "preview_failed", "invalid preview max_file_bytes")
        candidates = []
        for path in sorted(source.rglob("*")):
            try:
                safe_path(worktree, path.relative_to(worktree).as_posix())
            except WebsiteError as exc:
                step("preview", "failed", detail=str(exc))
                return finish("failed", "preview_failed", str(exc))
            if path.is_file() and path.suffix.lower() in PREVIEW_SUFFIXES and path.stat().st_size <= limit:
                candidates.append(path)
        candidates = candidates[:PREVIEW_MAX_FILES]
        included = {p.relative_to(source).as_posix() for p in candidates}
        if sum(p.stat().st_size for p in candidates) > PREVIEW_MAX_FILES * PREVIEW_MAX_BYTES:
            return finish("failed", "preview_failed", "preview exceeds the total artifact size budget")
        for path in candidates:
            relative = path.relative_to(source).as_posix()
            try:
                data = preview_bytes(source, path, str(preview.get("url_prefix") or ""), included=included)
            except (WebsiteError, UnicodeError) as exc:
                step("preview", "failed", detail=str(exc))
                return finish("failed", "preview_failed", str(exc))
            artifact(f"preview/{relative}", data, "preview")
            files.append(relative)
            copied[relative] = data
        resource_problems = preview_asset_problems(copied)
        if resource_problems:
            step("preview", "failed", detail="; ".join(resource_problems))
            return finish("failed", "preview_failed", "; ".join(resource_problems))
        entry = str(preview.get("entry") or "index.html")
        if entry not in files:
            step("preview", "failed", detail=f"{entry} missing")
            return finish("failed", "preview_failed", f"the preview has no {entry}")
        outcome["preview"] = {"entry": entry, "files": files, "local_only": True,
                              "detail": "A local copy for review in the Dashboard; not hosted, pushed or deployed."}
        step("preview", "ok", files=len(files))
    else:
        outcome["preview"] = None
        step("preview", "skipped")

    if preparation:
        from aos_website_prepare import acceptance
        # A build/test may modify source after the first check; verify the final evidence.
        final_changes = _changed_paths(worktree, pre["base_sha"])
        problems = scope_problems(project, final_changes) + acceptance(preparation, worktree, final_changes)
        if _git(worktree, "status", "--porcelain"):
            problems.append("test/build modified the worktree; evidence differs from the review commit")
        if problems:
            step("acceptance", "failed", detail="; ".join(problems))
            return finish("failed", "website_acceptance_failed", "; ".join(problems))
        outcome["acceptance"] = {"passed": True, "checks": preparation["acceptance_checks"]}
        step("acceptance", "ok")

    outcome["publish"] = {"status": "held", "target": outcome["publish"]["target"], "detail": PUBLISH_HOLD}
    outcome["summary"] = (f"{project.get('name') or project_id}: change ready for review on branch {branch} "
                          f"({len(changed)} file(s)); tests {'passed' if (outcome['tests'] or {}).get('passed') else 'not defined'}; "
                          f"preview {'ready' if outcome.get('preview') else 'not defined'}; publishing held for Casey.")
    return finish("ok")


# -- Dashboard projection -----------------------------------------------------------

def job_view(root: Path, job: dict) -> dict:
    """What the Websites screen shows for one website job, read from its verified artifacts."""
    import aos_jobs

    root = Path(root)
    verified, problems = aos_jobs.verify_artifacts(root, job.get("artifacts") or [])
    by_ref: dict[str, list[dict]] = {}
    for row in verified:
        by_ref.setdefault(str(row.get("ref") or ""), []).append(row)
    def read_json(ref: str) -> dict | None:
        rows = by_ref.get(ref) or []
        if not rows:
            return None
        try:
            return json.loads((root / rows[0]["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    def read_text(ref: str, limit: int = 20_000) -> str:
        rows = by_ref.get(ref) or []
        return (root / rows[0]["path"]).read_text(encoding="utf-8", errors="replace")[:limit] if rows else ""
    outcome = read_json("outcome") or {}
    order = read_json("work_order") or {}
    effects = job.get("effects") or []
    publish = next((row for row in effects if row.get("action") == PUBLISH_ACTION), None)
    preview = outcome.get("preview") or None
    if preview:
        prefix = f"{RESULTS.as_posix()}/{storage_key(job['id'])}/preview/"
        copied = {row['path'][len(prefix):]: (root / row['path']).read_bytes() for row in by_ref.get('preview', [])
                  if row['path'].startswith(prefix)}
        problems.extend(preview_asset_problems(copied))
    preparation = ((job.get("executor") or {}).get("input") or {}).get("preparation")
    readiness = ("blocked" if problems or job["status"] in ("failed", "refused", "interrupted") else
                 "validated" if job["status"] == "done" and outcome.get("acceptance", {}).get("passed")
                 and outcome.get("tests", {}).get("passed") and preview else "prepared") if preparation else None
    return {
        "job_id": job["id"], "status": job["status"], "lifecycle": aos_jobs.lifecycle(job["status"]),
        "readiness": readiness, "released": False, "preparation": preparation, "acceptance": outcome.get("acceptance"),
        "request": job.get("request") or "", "project": (job.get("executor") or {}).get("project"),
        "project_name": outcome.get("project_name") or "", "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"), "failure_class": job.get("failure_class") or "",
        "detail": job.get("detail") or "", "summary": (job.get("result") or {}).get("summary") or "",
        "steps": outcome.get("steps") or [{"id": "queued", "status": job["status"]}],
        "preflight": outcome.get("preflight") or order.get("preflight"), "icm": outcome.get("icm") or order.get("icm"),
        "branch": outcome.get("branch") or "", "commit": outcome.get("commit") or "",
        "changed_files": outcome.get("changed_files") or [], "diff_stat": outcome.get("diff_stat") or "",
        "diff": read_text("diff") or read_text("diff_rejected") or read_text("scope_rejected"), "tests": outcome.get("tests"),
        "tests_log": read_text("tests"),
        "preview": ({**preview, "url": f"/api/website/jobs/{job['id']}/preview/{preview['entry']}"} if preview else None),
        "publish": {"status": (publish or {}).get("status") or (outcome.get("publish") or {}).get("status") or "not_ready",
                    "target": (publish or {}).get("target") or (outcome.get("publish") or {}).get("target") or "",
                    "detail": (publish or {}).get("detail") or (outcome.get("publish") or {}).get("detail") or ""},
        "artifacts": verified, "artifact_problems": problems, "receipts": job.get("receipts") or [],
        "usage": job.get("usage"), "accounting": job.get("accounting"),
    }


def preview_file(root: Path, job: dict, relative: str) -> Path:
    """A preview file the job recorded, verified by hash; anything else is refused."""
    import aos_jobs

    wanted = f"{RESULTS.as_posix()}/{storage_key(job['id'])}/preview/{relative}"
    verified, _problems = aos_jobs.verify_artifacts(Path(root), [row for row in job.get("artifacts") or []
                                                                 if row.get("path") == wanted])
    if not verified:
        raise WebsiteError("not a verified preview file of this job")
    return Path(root) / verified[0]["path"]
