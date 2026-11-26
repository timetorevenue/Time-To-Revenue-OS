"""TTROS backend: David, Shared Brain MCP and the Business Brain read/write surfaces.

David's conversational turns run through tools/aos_david.py (ModelPort, warm Codex
app-server) and delegated work runs as background jobs through the M1 agent runtime
(tools/aos_jobs.py). Everything else here serves the preserved foundations: search,
Memory/Brain browsing and saving, Memory Intake and uploads (source-aware import),
Graphify, connector status reads, the token fuse, Remote PC and the Shared Brain MCP.

Revisit: when David's API contract, a preserved surface or the authority boundary
changes. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, StrictBool, Field, ConfigDict
from starlette.concurrency import run_in_threadpool

BACKEND_DIR = Path(__file__).resolve().parent
TOOLS_DIR = BACKEND_DIR.parents[1] / "tools"
for _path in (TOOLS_DIR, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from aos_paths import AuthorityError, aos_root, assert_authoritative_root, resolved_path  # noqa: E402
from aos_queue_storage import durable_replace_text  # noqa: E402
import aos_david  # noqa: E402
import aos_reminders  # noqa: E402
import aos_embeddings  # noqa: E402
import aos_entity_index  # noqa: E402
import aos_indexer  # noqa: E402
import aos_authority  # noqa: E402
import ttros_init  # noqa: E402
import aos_jobs  # noqa: E402
import aos_automation  # noqa: E402
import aos_crm  # noqa: E402
import aos_lead_magnet  # noqa: E402
import aos_subscribers  # noqa: E402
import aos_website  # noqa: E402
import business_brain  # noqa: E402
import business_brain_context  # noqa: E402
import business_brain_inbox  # noqa: E402
import business_brain_scope  # noqa: E402
import latitude_telemetry  # noqa: E402
import runtime_code_staleness  # noqa: E402
import shared_brain_read  # noqa: E402
import source_intake  # noqa: E402
import uploads_store  # noqa: E402
from graphify_service import GRAPH_CSP, GraphifyError, GraphifyService, validate_github_url  # noqa: E402
import graphify_visibility  # noqa: E402
from remote_pc import require_operator as require_remote_operator, router as remote_pc_router  # noqa: E402
from step6_cost_control import CostControlError, Scope as Step6Scope  # noqa: E402
from step6_cost_control import fuse_status as step6_fuse_status, set_override as set_step6_override  # noqa: E402

BASE_DIR = aos_root()
QUEUE_DIR = BASE_DIR / "queue"
MEMORY_INTAKE_INBOX_DIR = QUEUE_DIR / "inbox"
MEMORY_INTAKE_RECEIPTS_DIR = QUEUE_DIR / "receipts" / "memory_intake"
BACKUP_RECEIPTS_FILE = QUEUE_DIR / "receipts" / "backups.jsonl"
LINUX_BACKUP_RECEIPTS_FILE = QUEUE_DIR / "receipts" / "linux-backups.jsonl"
GRAPHIFY_BRAIN_DIR = (Path.home() / "graphify-brain")
GRAPHIFY_CLONE_DIR = GRAPHIFY_BRAIN_DIR / "intake" / "cloned-repos"
GRAPHIFY_OUT_DIR = GRAPHIFY_BRAIN_DIR / "repo_graphs"
GRAPHIFY_RECEIPTS_DIR = GRAPHIFY_BRAIN_DIR / "receipts"
GRAPHIFY_SERVICE = GraphifyService(brain_root=GRAPHIFY_BRAIN_DIR, repo_root=BASE_DIR)
ASK_DAVID_INLINE_WAIT_SECONDS = float(os.environ.get("ASK_DAVID_INLINE_WAIT_SECONDS", "25"))
JOB_WORKERS = int(os.environ.get("TTROS_DAVID_JOB_WORKERS", str(aos_jobs.DEFAULT_WORKERS)))

# One David and one job pool per process; built at startup so tests can inject doubles first.
DAVID: aos_david.David | None = None
RUNNER: aos_jobs.JobRunner | None = None
TURNS: aos_david.TurnStore | None = None


def start_david(*, model=None, search=None, threads=None, modelport=None, perform=None, send=None,
                brain_read=None, warm: bool = True, recover: bool = True) -> dict:
    """Wire David to the M1 job runner. Returns the recovery report."""
    global DAVID, RUNNER, TURNS
    stop_david()
    DAVID = aos_david.David(BASE_DIR, model=model, search=search, threads=threads, warm=warm)
    execute = {"notify": lambda job: DAVID.job_finished(job, send=send), "modelport": modelport,
               "brain_read": brain_read}
    if perform is not None:
        execute["perform"] = perform
    RUNNER = aos_jobs.JobRunner(BASE_DIR, workers=JOB_WORKERS, **execute)
    DAVID.runner = RUNNER
    TURNS = aos_david.TurnStore(BASE_DIR)
    if warm and model is None:
        # Start the Codex app-server process now (initialize only; no model call, no tokens)
        # so the first message does not pay its startup. A failure here is retried on that message.
        threading.Thread(target=_prestart_warm_session, args=(DAVID,), name="david-warm", daemon=True).start()
    return RUNNER.start() if recover else {}


def _prestart_warm_session(david: aos_david.David) -> None:
    try:
        session = david._warm_session()
        if session is not None:
            session.start()
    except Exception:
        pass


def stop_david() -> None:
    global DAVID, RUNNER
    if RUNNER is not None:
        RUNNER.shutdown(wait=False)
    if DAVID is not None:
        DAVID.close()
    DAVID = RUNNER = None


@asynccontextmanager
async def lifespan(app):
    # A custom lifespan replaces Starlette's default one, and that default is what runs
    # handlers added with app.add_event_handler: the Shared Brain and LinkedIn MCP session
    # managers. Without running them here those mounts answer every request with HTTP 500.
    await app.router.startup()
    try:
        if DAVID is None:
            start_david()
        yield
    finally:
        stop_david()
        await app.router.shutdown()


app = FastAPI(title="TTROS API", version="0.2.0", lifespan=lifespan)
CODE_STALENESS = runtime_code_staleness.StalenessMonitor()
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:3010", "http://localhost:3010"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

if os.environ.get("TTROS_SHARED_BRAIN_HTTP", "").strip().lower() in {"1", "true", "yes"}:
    from shared_brain_http import install as install_shared_brain_http

    install_shared_brain_http(app, get_david=lambda: DAVID)

app.include_router(remote_pc_router)
from website_readiness import router as website_readiness_router  # noqa: E402
app.include_router(website_readiness_router(lambda: BASE_DIR, lambda: RUNNER))
from agent_release import router as agent_release_router  # noqa: E402
app.include_router(agent_release_router)
from website_studio import router as website_studio_router
app.include_router(website_studio_router(lambda: _david()))
from product_launch import router as product_launch_router
app.include_router(product_launch_router(lambda: BASE_DIR))
from dashboard_identity import COOKIE as DASHBOARD_SESSION_COOKIE, Identity, install_session_boundary, router as identity_router
from account_connections import router as connections_router
from current_website import router as current_website_router
DASHBOARD_IDENTITY = Identity(BASE_DIR)
install_session_boundary(app, DASHBOARD_IDENTITY)
app.include_router(identity_router(DASHBOARD_IDENTITY))
app.include_router(connections_router(DASHBOARD_IDENTITY, get_runner=lambda: RUNNER))
app.include_router(current_website_router(DASHBOARD_IDENTITY))
from website_studio_builds import router as studio_builds_router
from website_studio_sources import Sources as StudioSources
app.include_router(studio_builds_router(StudioSources(root=BASE_DIR), DASHBOARD_IDENTITY))
# Immutable releases of validated builds; publish/rollback need the verified operator's explicit command.
from website_studio_releases import router as studio_releases_router
app.include_router(studio_releases_router(StudioSources(root=BASE_DIR), DASHBOARD_IDENTITY))
# Live-source visual editor of the real website (pinned live commit); same operator/CSRF gates, no publishing.
from website_studio_live import router as studio_live_router
_studio_live = studio_live_router(StudioSources(root=BASE_DIR), DASHBOARD_IDENTITY)
app.include_router(_studio_live)
# Website Manager panel: each owner turn is a Job (deterministic ops first, website.manager on its pinned model
# only when released); proposals are validated against the owner's documents and applied only on accept.
from website_studio_manager import router as studio_manager_router
app.include_router(studio_manager_router(StudioSources(root=BASE_DIR), DASHBOARD_IDENTITY, _studio_live.live, jobs_root=BASE_DIR, package_root=BASE_DIR))

# One read-only LinkedIn MCP operation for ChatGPT, through the operator's Composio Platform connection.
if os.environ.get("TTROS_LINKEDIN_MCP_HTTP", "").strip().lower() in {"1", "true", "yes"}:
    from linkedin_profile_mcp import install as install_linkedin_profile_mcp

    install_linkedin_profile_mcp(app, lambda mode, subject, args: _platform_operator_read(subject, args))


def _read_jsonl_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    except OSError:
        return []
    return records


def _redacted_preview(text: str, limit: int = 4000) -> str:
    safe_lines = []
    secret_re = re.compile(r"(secret|token|api[_-]?key|oauth|password|credential|authorization|bearer)", re.IGNORECASE)
    for line in str(text or "").splitlines():
        safe_lines.append("[redacted sensitive line]" if secret_re.search(line) else line)
    return "\n".join(safe_lines)[:limit]


def _workflow_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@app.get("/api/health")
def health():
    jobs = aos_jobs.list_jobs(BASE_DIR, limit=200)
    return {
        "status": "ok", "version": "0.2.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "workspace": str(BASE_DIR),
        "david": {"ready": DAVID is not None, "warm_session": bool(DAVID and DAVID._session and DAVID._session.process),
                  "automation_version": 1 if RUNNER is not None else None,
                  "jobs_running": sum(job["status"] == "running" for job in jobs),
                  "jobs_queued": sum(job["status"] == "queued" for job in jobs)},
        "token_usage_text": "Token usage: no agent invocation",
        "code": CODE_STALENESS.status(),
    }


@app.get("/api/setup/status")
def setup_status():
    """First-run onboarding state (read-only): what is still unconfigured. Reads no company content."""
    return {**ttros_init.status(brain=business_brain.BUSINESS_BRAIN_ROOT, repo=BASE_DIR), "token_usage_text": "Token usage: no agent invocation"}


@app.get("/api/wsl/status")
def wsl_status():
    """Read-only status contract the preserved Telegram bridge's /status renders.

    Jobs run in this process's M1 pool; there is no separate runner process or HTTP
    workbench route. Reports state only: no agent, job, model or notification.
    """
    jobs = aos_jobs.list_jobs(BASE_DIR, limit=200)
    active = sum(job["status"] in {"queued", "running"} for job in jobs)
    healthy = DAVID is not None and RUNNER is not None and not CODE_STALENESS.status().get("stale")
    return {
        "success": healthy, "state": "healthy" if healthy else "degraded",
        "bridge": {"state": "running"},
        "queue": {"state": "aos_jobs", "items": len(jobs), "actionable": active},
        "runner": {"state": "in_process" if RUNNER is not None else "stopped"},
        "codex": {"state": "native_workbench"},
        "local_agent_route": {"state": "native_workbench"},
        "last_route_failure": None,
        "token_usage_text": "Token usage: no agent invocation",
    }


def _require_authority() -> Path:
    return assert_authoritative_root(BASE_DIR)


@app.middleware("http")
async def linux_authority_boundary(request: Request, call_next):
    """Reject every HTTP mutation before an endpoint can create side effects."""
    host = getattr(request, "headers", {}).get("host", "").split(":", 1)[0].lower()
    path = getattr(getattr(request, "url", None), "path", "")
    if host == "brain.core-site.example" and not path.startswith("/api/brain/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if path.startswith("/api/brain/") and host not in {"brain.core-site.example", "127.0.0.1", "localhost"}:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        try:
            _require_authority()
        except AuthorityError as exc:
            return JSONResponse(status_code=503, content={"success": False, "detail": str(exc)})
    return await call_next(request)


def _read_backup_receipts(path: Path | None = None) -> list[dict]:
    receipt_file = path or BACKUP_RECEIPTS_FILE
    if not receipt_file.exists():
        return []
    receipts = []
    for raw in receipt_file.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            receipts.append(record)
    return receipts


def _backup_status(now: datetime.datetime | None = None, receipts: list[dict] | None = None) -> dict:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    receipt_file = BACKUP_RECEIPTS_FILE
    if receipts is None:
        linux_records = _read_backup_receipts(LINUX_BACKUP_RECEIPTS_FILE)
        if linux_records:
            records = linux_records
            receipt_file = LINUX_BACKUP_RECEIPTS_FILE
        else:
            records = _read_backup_receipts()
    else:
        records = receipts
    if not records:
        return {
            "state": "no_receipts",
            "latest": None,
            "latest_receipt_path": _safe_relative(receipt_file),
            "latest_log_path": None,
            "stale_after_hours": 48,
            "needs_attention": False,
            "token_usage_text": "Token usage: no agent invocation",
        }
    latest = records[-1]
    latest_ts = _parse_record_timestamp(latest.get("ts"))
    latest_status = str(latest.get("status") or "").lower()
    if latest_status == "fail":
        state = "failed"
    elif latest_status == "success" and latest_ts and (now - latest_ts.astimezone(datetime.timezone.utc)).total_seconds() <= 48 * 3600:
        state = "fresh_success"
    else:
        state = "stale"
    log_path = latest.get("log_path")
    safe_log_path = None
    if log_path:
        try:
            safe_log_path = _safe_relative(Path(log_path))
        except Exception:
            safe_log_path = str(log_path)
    return {
        "state": state,
        "latest": {
            "ts": latest.get("ts"),
            "status": latest.get("status"),
            "target": latest.get("target"),
            "target_drive": latest.get("target_drive"),
            "target_label": latest.get("target_label"),
            "snapshot_path": latest.get("snapshot_path"),
            "sources": latest.get("sources") or [],
            "files_copied": latest.get("files_copied") if latest.get("files_copied") is not None else latest.get("files_total"),
            "bytes_copied": latest.get("bytes_copied") if latest.get("bytes_copied") is not None else latest.get("bytes"),
            "duration_s": latest.get("duration_s"),
            "dry_run": bool(latest.get("dry_run")),
            "errors": latest.get("errors") or [],
            "warnings": latest.get("warnings") or [],
            "authority": latest.get("authority") or "legacy_windows",
            "exclusions": latest.get("exclusions") or [],
            "readable_proof": bool(latest.get("readable_proof")),
            "token_usage_text": latest.get("token_usage_text") or "Token usage: no agent invocation",
        },
        "latest_receipt_path": _safe_relative(receipt_file),
        "latest_log_path": safe_log_path,
        "stale_after_hours": 48,
        "needs_attention": state in {"failed", "stale"},
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.get("/api/backups/status")
def backups_status():
    status = _backup_status()
    latitude_telemetry.trace("backup.status", "backup", status.get("state", "unknown"), needs_attention=status.get("needs_attention"))
    return status


@app.get("/api/search")
def api_search(q: str = "", type: str = "", tag: str = "", source: str = "", limit: int = 25, client_scope: str = ""):
    try:
        return aos_indexer.search(q, kind=type, tag=tag, source=source, limit=limit, client_scope=client_scope)
    except business_brain_scope.ClientScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/search/status")
def api_search_status():
    status = aos_indexer.status()
    latitude_telemetry.trace("search.status", "search_index", "ok", indexed=status.get("indexed"), path=status.get("path"))
    return status


@app.post("/api/search/reindex")
def api_search_reindex():
    result = aos_indexer.scan()
    latitude_telemetry.trace("search.reindex", "search_index", "ok", count=result.get("count"))
    return result


@app.post("/api/ingest/tick")
def api_ingest_tick():
    result = aos_indexer.ingest_tick()
    latitude_telemetry.trace("search.ingest_tick", "search_index", "ok", changed=result.get("changed"))
    return result


class GraphifyFetchRequest(BaseModel):
    url: str


class GraphifyRepositoryRequest(BaseModel):
    owner: str
    repository: str


class GraphifyActionRequest(GraphifyRepositoryRequest):
    action: str
    inputs: dict = {}


class DashboardMemorySave(BaseModel):
    path: str
    content: str
    expected_revision: str


WSL_ENV = 'export PATH="$HOME/.local/npm/bin:$HOME/.local/bin:$HOME/.composio:$PATH"'


COMPOSIO_PATH = "/home/operator/.composio:/home/operator/.local/bin:/home/operator/.composio:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/usr/lib/wsl/lib"


def _path_for_wsl_command(path) -> str:
    raw = str(path)
    if re.match(r"^[A-Za-z]:[\\/]", raw) or re.match(r"^/mnt/[a-z](?:/|$)", raw, re.IGNORECASE):
        raise AuthorityError("Windows and Windows-mounted paths are unsupported by the Linux backend runtime")
    return raw


def _quoted_linux_path(path: Path | str) -> str:
    raw = _path_for_wsl_command(path)
    return "'" + raw.replace("'", "'\"'\"'") + "'"


def _run_wsl(bash_cmd: str, timeout: int = 60) -> dict:
    """Compatibility helper for bounded commands in the authoritative Linux root."""
    root = _quoted_linux_path(BASE_DIR)
    full_cmd = f"{WSL_ENV}; export AOS_ROOT={root}; cd {root}; {bash_cmd}"
    try:
        result = subprocess.run(
            ["bash", "-lc", full_cmd],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        success = result.returncode == 0
        output = stdout if stdout else stderr
        return {
            "success": success,
            "output": output or "(no output)",
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": f"Command timed out after {timeout}s",
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "returncode": -1,
            "timed_out": True,
            "timeout_seconds": timeout,
        }
    except FileNotFoundError:
        return {"success": False, "output": "bash not found — Linux runtime unavailable", "returncode": -1}
    except Exception as e:
        return {"success": False, "output": f"Error: {e}", "returncode": -1}


def _run_agentic_os_clean_bash(bash_cmd: str, timeout: int = 60) -> dict:
    """Run directly in the current supported Linux runtime."""
    return _run_wsl(bash_cmd, timeout=timeout)


def _parse_record_timestamp(value: object) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _safe_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(resolved_path(BASE_DIR)).as_posix()
    except ValueError:
        return path.name


_DASHBOARD_MARKDOWN_BLOCK_RE = re.compile(
    r"(^|[/.])(\.env|.*secret.*|.*credential.*|.*password.*|.*token.*)|"
    r"north[\s_-]*shore|old[\s_-]*(?:ubuntu|hermes|vault|runtime)|legacy[_-]?harvest|old[_-]?zpc|\bzpc\b|"
    r"connectors?/.*(?:secret|credential|token|\.env)",
    re.IGNORECASE,
)


def _parse_frontmatter_value(value: str) -> object:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def _parse_markdown_frontmatter(text: str) -> dict:
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {"frontmatter": {}, "frontmatter_lines": [], "body": str(text or "")}
    closing = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing = index
            break
    if closing is None:
        return {"frontmatter": {}, "frontmatter_lines": [], "body": str(text or "")}

    metadata = {}
    frontmatter_lines = lines[1:closing]
    for line in frontmatter_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = _parse_frontmatter_value(value)
    return {
        "frontmatter": metadata,
        "frontmatter_lines": frontmatter_lines,
        "body": "\n".join(lines[closing + 1:]).lstrip("\n"),
    }


def _frontmatter_text_value(metadata: dict, key: str, default: str = "") -> str:
    value = metadata.get(key)
    if value is None:
        return default
    return str(value).strip()


def _render_markdown_frontmatter(frontmatter_lines: list[str], name: str, description: str, body: str) -> str:
    lines = list(frontmatter_lines or [])
    seen = set()

    def replace_or_append(key: str, value: str) -> None:
        safe_value = str(value or "").strip()
        serialized = json.dumps(safe_value) if any(ch in safe_value for ch in (":", "#", '"', "'")) else safe_value
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*:")
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = f"{key}: {serialized}"
                seen.add(key)
                return
        seen.add(key)
        lines.append(f"{key}: {serialized}")

    replace_or_append("name", name)
    replace_or_append("description", description)
    clean_body = str(body or "").strip("\n")
    return "---\n" + "\n".join(lines).rstrip() + "\n---\n" + clean_body + "\n"


def _markdown_title(text: str, fallback: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped[:90]
    return fallback


_MEMORY_NOTE_MAX_BYTES = 750 * 1024


def _dashboard_memory_note_path(pointer: str, *, registry=None) -> tuple[Path, object]:
    gate = registry or business_brain_scope.load_registry()
    resolved = gate.resolve_brain_pointer("global", pointer)
    target = resolved.resolved_path
    if target.suffix.lower() != ".md" or not target.is_file():
        raise ValueError("only existing canonical Business Brain Markdown notes are editable")
    if target.stat().st_size > _MEMORY_NOTE_MAX_BYTES:
        raise ValueError("Business Brain note exceeds the editor size limit")
    return target, gate


def _validate_dashboard_memory_content(content: str) -> None:
    if not isinstance(content, str):
        raise ValueError("Business Brain content must be text")
    if "\x00" in content:
        raise ValueError("Business Brain content contains an invalid NUL character")
    if len(content.encode("utf-8")) > _MEMORY_NOTE_MAX_BYTES:
        raise ValueError("Business Brain note exceeds the editor size limit")


def _atomic_business_brain_replace(path: Path, content: str) -> None:
    """Atomically replace one canonical vault file on DrvFS or native Linux."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # DrvFS may not support directory fsync; the file itself is flushed
            # and the replacement remains same-directory and atomic.
            pass
    finally:
        temporary.unlink(missing_ok=True)


@app.get("/api/composio/connections")
def composio_connections():
    """Read the current Composio connections without changing account state."""
    result = _run_wsl(
        f"export PATH={COMPOSIO_PATH}; composio connections list",
        timeout=30,
    )
    raw_output = result["output"]

    if result["success"]:
        try:
            return {
                "success": True,
                "format": "json",
                "connections": json.loads(raw_output),
            }
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "success": result["success"],
        "format": "raw",
        "raw": raw_output,
        "returncode": result["returncode"],
    }


class CockpitCaptureCreate(BaseModel):
    text: str
    capture_id: str | None = None


@app.post("/api/dashboard/capture")
def dashboard_capture(body: CockpitCaptureCreate):
    """Preserve quick notes through the existing validated inbox writer."""
    try:
        capture = business_brain_inbox.capture_text(
            body.text, source="cockpit_capture", capture_id=body.capture_id,
            root=business_brain.BUSINESS_BRAIN_ROOT,
        )
    except business_brain_inbox.InboxCaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "capture_id": capture.capture_id, "pointer": capture.pointer,
            "duplicate": capture.duplicate, "queue_item_created": False, "promoted": False,
            "token_usage_text": "Token usage: no agent invocation"}


class AskDavidReplyContext(BaseModel):
    """The Telegram message Casey replied to: bounded fields only, never the raw update."""
    text: str = ""
    message_id: int | None = None
    from_bot: bool = False


class AskDavidRequest(BaseModel):
    text: str
    conversation_id: str = "dashboard-default"
    delivery_id: str = ""
    source_refs: list[str] = []
    reply_context: AskDavidReplyContext | None = None


class MemoryIntakeIngestRequest(BaseModel):
    upload_id: str = ""
    inbox_path: str = ""


class Step6FuseOverride(BaseModel):
    scope_type: str
    scope_id: str
    active: bool
    reason: str = ""


_COMPOSIO_SLUG_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,15}$")


_COMPOSIO_READ_VERBS = frozenset({
    "GET", "LIST", "FETCH", "SEARCH", "FIND", "READ", "RETRIEVE", "QUERY", "LOOKUP", "VIEW", "DESCRIBE", "COUNT",
})


_COMPOSIO_MUTATION_VERBS = frozenset({
    "ACCEPT", "ADD", "APPEND", "APPROVE", "ARCHIVE", "ASSIGN", "BATCH", "BLOCK", "BOOK", "BULK", "CANCEL", "CHANGE",
    "CHARGE", "CLEAR", "CLOSE", "COMMENT", "CONNECT", "COPY", "CREATE", "DECLINE", "DELETE", "DISABLE", "DISCONNECT",
    "DISPATCH", "DUPLICATE", "EDIT", "EMPTY", "ENABLE", "EXECUTE", "EXPORT", "FOLLOW", "FORWARD", "GRANT", "IMPORT",
    "INSERT", "INVITE", "LABEL", "LIKE", "MARK", "MERGE", "MODIFY", "MOVE", "MUTE", "PATCH", "PAY", "PIN", "POST",
    "PUBLISH", "PUT", "REACT", "REGISTER", "REMOVE", "RENAME", "REPLACE", "REPLY", "REPORT", "REQUEST", "RESET",
    "RESHARE", "RESTORE", "REVOKE", "RUN", "SAVE", "SCHEDULE", "SEND", "SET", "SHARE", "STAR", "START", "STOP",
    "SUBMIT", "SUBSCRIBE", "SYNC", "TRANSFER", "TRASH", "TRIGGER", "UNARCHIVE", "UNFOLLOW", "UNPIN", "UNSTAR",
    "UNSUBSCRIBE", "UNTRASH", "UPDATE", "UPLOAD", "UPSERT", "WRITE",
})


def classify_composio_action(tool_slug: str) -> tuple[str, str]:
    """Return ("read", reason) only for a positively classified read; else refuse."""
    slug = str(tool_slug or "").strip().upper()
    if not _COMPOSIO_SLUG_RE.fullmatch(slug):
        return "invalid", "tool slug is not a well-formed Composio action name"
    tokens = slug.split("_")
    mutation = sorted(set(tokens) & _COMPOSIO_MUTATION_VERBS)
    if mutation:
        return "mutation", f"mutation verb {mutation[0]} is not executable from the Dashboard generic route"
    if not set(tokens) & _COMPOSIO_READ_VERBS:
        return "unknown", "action could not be verified as read-only"
    return "read", "explicit read verb and no mutation verb"


def _memory_intake_receipt_path(key: str) -> Path:
    safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", str(key or ""))[:120] or "receipt"
    return MEMORY_INTAKE_RECEIPTS_DIR / f"{safe_key}.json"


def _write_memory_intake_receipt(receipt: dict) -> Path:
    MEMORY_INTAKE_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _memory_intake_receipt_path(receipt["key"])
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _recent_memory_intake_receipts(limit: int = 20) -> list[dict]:
    if not MEMORY_INTAKE_RECEIPTS_DIR.is_dir():
        return []
    paths = sorted(MEMORY_INTAKE_RECEIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    for path in paths[:limit]:
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def _memory_intake_unfiled_items(limit: int = 50) -> list[dict]:
    """Files the existing watched Inbox has already discovered (search-indexed
    only, per queue/ingest_watch.json) that are not yet Business Brain sources.
    Read-only; never promotes anything -- the operator ingests explicitly."""
    root = MEMORY_INTAKE_INBOX_DIR
    if not root.is_dir():
        return []
    records_dir = business_brain.BUSINESS_BRAIN_ROOT / source_intake.RECORDS_RELATIVE
    items = []
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or path.is_symlink() or path.name.startswith(".")
                or path.is_relative_to(root / "staging")):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        items.append({
            "path": _safe_relative(path),
            "filename": path.name,
            "size": len(raw),
            "modified": datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "supported": path.suffix.lower() in uploads_store.INGESTABLE_SUFFIXES,
            "already_in_brain": (records_dir / f"{digest}.md").is_file(),
        })
    items.sort(key=lambda row: row["modified"], reverse=True)
    return items[:limit]


def _safe_memory_intake_inbox_path(raw: str) -> Path:
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("inbox_path must not be empty")
    unresolved = BASE_DIR / value
    # Symlink-ness must be checked on the path as given -- resolve() below
    # follows a symlink to its real target, which would otherwise let a
    # symlink-to-a-file-inside-the-inbox slip through untouched.
    if unresolved.is_symlink():
        raise ValueError("inbox_path must not be a symlink")
    target = unresolved.resolve()
    inbox_root = MEMORY_INTAKE_INBOX_DIR.resolve()
    try:
        target.relative_to(inbox_root)
    except ValueError as exc:
        raise ValueError("inbox_path must stay inside the watched inbox") from exc
    if not target.is_file():
        raise ValueError("inbox_path does not name a regular file")
    return target


def _verify_memory_intake_original(path: Path, pointer: str) -> tuple[str, Path]:
    """Verify capture preserved this upload byte-for-byte at its canonical digest path."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected_pointer = f"business_brain:{source_intake.RECORDS_RELATIVE}/{digest}.md"
    if pointer != expected_pointer:
        raise source_intake.SourceIntakeError(
            f"capture returned an unexpected source record: {pointer or 'unavailable'}"
        )
    record_path = business_brain.BUSINESS_BRAIN_ROOT / source_intake.RECORDS_RELATIVE / f"{digest}.md"
    if not record_path.is_file():
        raise source_intake.SourceIntakeError(f"captured Original is unavailable: {expected_pointer}")
    preserved = source_intake.extract_exact_bytes(record_path.read_text(encoding="utf-8", errors="strict"))
    if preserved != raw or hashlib.sha256(preserved).hexdigest() != digest:
        raise source_intake.SourceIntakeError(f"captured Original failed exact-byte verification: {expected_pointer}")
    return digest, record_path


def _run_memory_intake_capture(path: Path) -> dict:
    """Deterministic capture only; no model is called and no Hermes process is started. The one
    governed entrypoint every Memory Intake ingest action calls (Memory Intake page, Cockpit
    quick-drop, New & Unfiled, David's explicit ingest instruction): governed capture of the
    exact original, search/graph refresh, verification of the preserved bytes. Semantic enrichment
    is a separate, explicitly authorized step (tools/source_intake.py --mode semantic
    --semantic-payload) and is never run from here."""
    try:
        capture = source_intake.run_intake(path, mode="capture")
    except source_intake.SourceIntakeError as exc:
        return {"success": False, "status": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - ingestion failure must never crash the request
        # Git/index/transport trouble can clear on its own; the watcher retries it with bounded backoff.
        return {"success": False, "status": "failed", "retryable": True, "error": f"source intake failed safely: {exc}"}
    pointer = (capture.imported_pointers or capture.duplicate_pointers or (None,))[0]
    try:
        _digest, record_path = _verify_memory_intake_original(path, pointer)
    except Exception as exc:  # noqa: BLE001 - a capture without a verified Original is a failure
        return {
            "success": False, "status": "failed", "source_record": pointer,
            "original_preserved": False, "error": f"capture verification failed: {exc}",
            "token_usage_text": capture.token_usage_text,
        }

    return {
        "success": True,
        "status": "captured",
        "source_record": pointer,
        "original_preserved": True,
        "source_card_created": False,
        "source_card_present": False,
        "semantic": "not_requested",
        "search_updated": capture.search_status,
        "graph_updated": capture.graphify_status,
        "brain_commit": capture.brain_commit,
        "brain_commits": tuple(value for value in (capture.brain_commit,) if value),
        "model_invocations": capture.model_invocations,
        "token_usage_text": capture.token_usage_text,
    }


def _memory_intake_ingest_and_record(source_path: Path, *, filename: str, key: str,
                                     source_provenance: dict | None = None) -> dict:
    outcome = _run_memory_intake_capture(source_path)
    receipt = {
        "key": key,
        "filename": filename,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        **outcome,
        **(source_provenance or {}),
    }
    _write_memory_intake_receipt(receipt)
    latitude_telemetry.trace(
        "memory_intake.ingest", "source_intake", "ok" if outcome.get("success") else "failed",
        filename=filename, outcome_status=outcome.get("status"),
    )
    return receipt


def _ask_david_upload_ids(source_refs: list[str] | None) -> list[str]:
    return [str(ref).removeprefix("upload:") for ref in (source_refs or []) if str(ref).startswith("upload:")]


def _david_upload(upload_id, scope):
    namespace = hashlib.sha256(scope.encode()).hexdigest() if scope else ""
    if namespace:
        try:
            return (uploads_store.read_meta(BASE_DIR, upload_id, attachment_scope=namespace),
                    uploads_store.upload_file_path(BASE_DIR, upload_id, attachment_scope=namespace), namespace)
        except uploads_store.UploadError:
            pass
    return uploads_store.read_meta(BASE_DIR, upload_id), uploads_store.upload_file_path(BASE_DIR, upload_id), ""


_ASK_DAVID_EXPLICIT_INGEST_RE = re.compile(r"\b(ingest|add|save|import|put)\b.{0,40}\bbusiness\s+brain\b", re.IGNORECASE)


def _try_ask_david_explicit_ingest(text: str, source_refs: list[str] | None, *, scope="") -> dict | None:
    """Deterministic, zero-model short-circuit for 'Ingest this into the
    Business Brain' (or equivalent) when Casey has attached a file: routes the
    upload through the exact same governed source-intake path Memory Intake
    uses. Only fires when both an explicit ingestion phrase AND an attachment
    are present -- an attachment alone is source material for this request,
    never automatically written to the Brain (see _ask_david_attachment_context)."""
    upload_ids = _ask_david_upload_ids(source_refs)
    if not upload_ids or not _ASK_DAVID_EXPLICIT_INGEST_RE.search(text):
        return None
    results = []
    for upload_id in upload_ids:
        try:
            meta, source_path, namespace = _david_upload(upload_id, scope)
        except uploads_store.UploadError as exc:
            results.append({"success": False, "status": "failed", "error": str(exc), "upload_id": upload_id})
            continue
        filename = meta.get("original_filename") or source_path.name
        receipt = _memory_intake_ingest_and_record(source_path, filename=filename, key=f"ask-david-{upload_id}")
        meta["status"] = receipt["status"]
        meta["ingest_result"] = receipt
        uploads_store.write_meta(BASE_DIR, upload_id, meta, attachment_scope=namespace)
        results.append({**receipt, "upload_id": upload_id})
    return {
        "success": all(row.get("success") for row in results),
        "kind": "memory_intake_ingested",
        "results": results,
        "direct_reply": True,
        "queue_delta": 0,
        "model_process_count": 0,
        "worker_process_count": 0,
        "token_usage_text": (
            "Token usage: unavailable from current CLI output"
            if any(row.get("token_usage_text") == "Token usage: unavailable from current CLI output" for row in results)
            else "Token usage: no agent invocation"
        ),
    }


def _ask_david_attachment_context(source_refs: list[str] | None, limit: int = 6000, *, scope="") -> str:
    """Bounded, read-only text for attachments on THIS request only -- never
    written anywhere, never promoted to the Business Brain. Lets David use an
    attached file as source material for a normal conversational reply."""
    upload_ids = _ask_david_upload_ids(source_refs)
    if not upload_ids:
        return ""
    blocks = []
    for upload_id in upload_ids:
        try:
            meta, path, _ = _david_upload(upload_id, scope)
        except uploads_store.UploadError:
            continue
        filename = meta.get("original_filename") or path.name
        if not meta.get("supported", True):
            blocks.append(f"### Attached file: {filename}\n[Attached but not readable as text: {meta.get('support_note') or 'unsupported file type'}.]")
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                content = handle.read(limit)
        except OSError:
            continue
        blocks.append(f"### Attached file: {filename}\n{content[:limit]}")
    return "\n\n".join(blocks)


def _try_ask_david_workstream_answer(text: str, reply: AskDavidReplyContext | None) -> dict | None:
    """Deterministic, zero-model: Casey's Telegram reply to a TTROS Shared Brain workstream
    question is recorded on exactly the workstream/version named in the replied-to bot message
    (`shared_brain_workflow.answer`). None for any other message, so it reaches David unchanged."""
    if reply is None or not reply.from_bot or not reply.text.strip():
        return None
    if os.environ.get("TTROS_SHARED_BRAIN_WRITE", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    import shared_brain_workflow

    result = shared_brain_workflow.answer(reply.text[:4096], text)
    if not result.get("handled"):
        return None
    latitude_telemetry.trace(
        "ask_david.workstream_answer",
        "shared_brain",
        "ok" if result.get("success") else "rejected",
        workstream_id=result.get("workstream_id"),
        version=result.get("version"),
        reason=result.get("reason", ""),
        reply_message_id=reply.message_id,
    )
    return {
        "success": bool(result.get("success")),
        "kind": "shared_brain_workstream_answer",
        "response": result["message"],
        "output": result["message"],
        "workstream_answer": {key: value for key, value in result.items() if key not in {"handled", "message"}},
        "direct_reply": True,
        "queue_delta": 0,
        "model_process_count": 0,
        "worker_process_count": 0,
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.post("/api/uploads")
async def create_upload(request: Request, file: UploadFile = File(...),
                        david_conversation_id: str = Form("")):
    """One shared local upload mechanism, reused by Memory Intake, the Cockpit
    quick-drop card, and David attachments. Stores real bytes under
    queue/uploads/<server-generated id>/; the browser only ever gets that
    opaque id back, never a filesystem path it could resubmit."""
    attachment_scope = ""
    if david_conversation_id:
        if not _david_local_operator(request):
            DASHBOARD_IDENTITY.csrf(request)
        principal = _david_request_principal(request)
        if principal["workspace"] not in {"ttros-operator", "local-preview"}:
            raise HTTPException(403, "David attachments require operator scope.")
        try:
            aos_david.conversation_key(david_conversation_id)
            scope = _david_dashboard_artifact_scope(_david_member(principal), david_conversation_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        attachment_scope = hashlib.sha256(scope.encode()).hexdigest()
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > uploads_store.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"file exceeds the {uploads_store.MAX_UPLOAD_BYTES} byte limit")
        chunks.append(chunk)
    try:
        saved = uploads_store.save_upload(BASE_DIR, file.filename or "upload", b"".join(chunks),
                                          attachment_scope=attachment_scope)
    except uploads_store.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    latitude_telemetry.trace(
        "uploads.create", "uploads", "ok",
        upload_id=saved.upload_id, size=saved.size, supported=saved.supported,
    )
    return {
        "success": True,
        "upload_id": saved.upload_id,
        "filename": saved.filename,
        "size": saved.size,
        "supported": saved.supported,
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    """Bounded text preview for one shared upload -- Memory Intake's Preview
    action. Unsupported types (e.g. PDF/DOCX) get metadata with no preview
    body, never a decoded-garbage rendering."""
    try:
        meta = uploads_store.read_meta(BASE_DIR, upload_id)
        path = uploads_store.upload_file_path(BASE_DIR, upload_id)
    except uploads_store.UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    preview = ""
    if meta.get("supported"):
        try:
            preview = path.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            preview = ""
    return {**meta, "preview": preview}


@app.get("/api/memory-intake/unfiled-preview")
def memory_intake_unfiled_preview(path: str):
    """Bounded text preview for one New & Unfiled watched-Inbox file, strictly
    confined to queue/inbox/ -- the existing dashboard artifact reader's own
    allowlist deliberately excludes that tree, so this stays a separate,
    equally narrow reader rather than widening that one."""
    try:
        target = _safe_memory_intake_inbox_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        content = target.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"preview read failed: {exc}")
    return {"success": True, "path": _safe_relative(target), "preview": content}


@app.get("/api/memory-intake")
def memory_intake_snapshot():
    ready = [row for row in uploads_store.list_uploads(BASE_DIR) if row.get("status") == "ready"]
    unfiled = _memory_intake_unfiled_items()
    recent = _recent_memory_intake_receipts()
    try:
        integrity = source_intake.verify_intake_index_integrity(business_brain.BUSINESS_BRAIN_ROOT)
    except OSError as exc:
        integrity = {"ok": False, "index_present": False, "error": str(exc)}
    return {
        "ready": ready,
        "unfiled": unfiled,
        "recent": recent,
        "counts": {
            "ready": len(ready),
            "ingested_recent": sum(1 for row in recent if row.get("status") in {"ingested", "captured", "duplicate"}),
            "failed_recent": sum(1 for row in recent if row.get("status") in {"failed", "needs_attention"}),
        },
        "brain_index_integrity": integrity,
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.post("/api/memory-intake/ingest")
def memory_intake_ingest(body: MemoryIntakeIngestRequest):
    """The one governed ingestion action for the dashboard: reuses
    tools/source_intake.py's capture then semantic modes exactly as the CLI does. Exactly one
    of upload_id (a shared-upload attachment) or inbox_path (a file the
    existing watched Inbox already discovered) selects the source."""
    if bool(body.upload_id) == bool(body.inbox_path):
        raise HTTPException(status_code=422, detail="exactly one of upload_id or inbox_path is required")
    if body.upload_id:
        try:
            meta = uploads_store.read_meta(BASE_DIR, body.upload_id)
            source_path = uploads_store.upload_file_path(BASE_DIR, body.upload_id)
        except uploads_store.UploadError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        filename = meta.get("original_filename") or source_path.name
        key = f"upload-{body.upload_id}"
    else:
        try:
            source_path = _safe_memory_intake_inbox_path(body.inbox_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        filename = source_path.name
        key = f"inbox-{hashlib.sha256(body.inbox_path.encode('utf-8')).hexdigest()[:16]}"

    receipt = _memory_intake_ingest_and_record(source_path, filename=filename, key=key)
    if body.upload_id:
        meta["status"] = receipt["status"]
        meta["ingest_result"] = receipt
        uploads_store.write_meta(BASE_DIR, body.upload_id, meta)
    return receipt


@app.get("/api/dashboard/memory")
def dashboard_memory():
    root_pointer = "business_brain:README.md"
    try:
        registry = business_brain_scope.load_registry()
        root_resolution = registry.resolve_brain_pointer("global", root_pointer)
        loader = business_brain_context.ScopedBrainLoader(registry=registry)
    except (business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError) as exc:
        return {
            "brain": {
                "available": False,
                "root": root_pointer,
                "file_count": 0,
                "blocked_path_count": 0,
                "error": str(exc),
            },
            "files": [],
            "promotion_queue": [],
            "promotion_state": {
                "mode": "unavailable",
                "available": False,
                "reference_count": 0,
                "reason": "Promotion evaluation and writing are not active in Block 1.",
            },
        }
    vault = root_resolution.resolved_path.parent
    files = []
    backup_root = vault / "_backups"
    blocked = sum(1 for path in backup_root.rglob("*.md") if path.is_file()) if backup_root.is_dir() else 0
    context_used = []
    for pointer in registry.permitted_brain_pointers("global"):
        try:
            retrieved = loader.retrieve(work={"client_scope": "global"}, pointers=[pointer])
            read = retrieved.reads[0]
            path = registry.resolve_brain_pointer("global", pointer).resolved_path
            content = read.content
            frontmatter, body = aos_indexer.parse_frontmatter(content)
            files.append({
                "id": frontmatter.get("id"),
                "type": frontmatter.get("type"),
                "path": pointer,
                "title": _markdown_title(body, path.name),
                "modified": datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                "preview": _redacted_preview(content, 3000),
                "revisit": next((line.strip() for line in content.splitlines() if "Revisit:" in line), ""),
            })
            context_used.extend(retrieved.brain_context_used)
        except (OSError, business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError, business_brain_context.BrainContextError):
            continue
    files.sort(key=lambda item: item["modified"], reverse=True)
    promotion_references = []
    for record in _read_jsonl_file(BASE_DIR / "queue" / "run_ledger.jsonl"):
        values = record.get("memory_promotion")
        if isinstance(values, list):
            promotion_references.extend(str(value) for value in values if str(value).strip())
    promotion_receipts = []
    for path in sorted((BASE_DIR / "queue" / "receipts").glob("brain-promotion-*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("status") == "success":
            promotion_receipts.append({
                "receipt": _safe_relative(path),
                "write_id": record.get("write_id"),
                "target": record.get("target"),
                "completed_at": record.get("completed_at"),
            })
    return {
        "brain": {
            "available": True,
            "root": root_pointer,
            "index": "business_brain:index/MEMORY_INDEX.md",
            "file_count": len(files),
            "blocked_path_count": blocked,
            "denied_pointer_count": len(registry.data.get("denied_brain_pointers") or []),
        },
        "files": files,
        "brain_context_used": context_used,
        "promotion_queue": [],
        "promotion_state": {
            "mode": "operational",
            "available": True,
            "automatic_classes": ["generated_marker_section"],
            "review_route": "human_review",
            "reference_count": len(promotion_references) + len(promotion_receipts),
            "references": promotion_references[:20],
            "latest_safe_promotion": promotion_receipts[0] if promotion_receipts else None,
            "reason": "Scope-gated promotion machinery is available; queue titles and tags are never promotion state.",
        },
    }


@app.get("/api/dashboard/memory/note")
def dashboard_memory_note(path: str):
    try:
        target, _registry = _dashboard_memory_note_path(path)
        content = target.read_text(encoding="utf-8", errors="strict")
        frontmatter, body = aos_indexer.parse_frontmatter(content)
    except (business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Business Brain note load failed: {exc}")
    return {
        "success": True,
        "path": path,
        "id": frontmatter.get("id"),
        "type": frontmatter.get("type"),
        "title": _markdown_title(body, target.name),
        "content": content,
        "revision": _workflow_revision(content),
        "modified": datetime.datetime.fromtimestamp(target.stat().st_mtime).isoformat(),
    }


@app.post("/api/dashboard/memory/save")
def dashboard_save_memory(body: DashboardMemorySave):
    try:
        target, registry = _dashboard_memory_note_path(body.path)
        existing = target.read_text(encoding="utf-8", errors="strict")
        if not body.expected_revision or body.expected_revision != _workflow_revision(existing):
            raise RuntimeError("Business Brain note changed after it was loaded; reopen it before saving")
        _validate_dashboard_memory_content(body.content)
        _atomic_business_brain_replace(target, body.content)
        persisted = target.read_text(encoding="utf-8", errors="strict")
        if persisted != body.content:
            raise OSError("Business Brain save verification failed")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Business Brain note save failed: {exc}")

    refresh = {"search": "current", "graphify": "not_configured"}
    try:
        index_result = aos_indexer.index_one(str(target), registry=registry)
        refresh["search"] = "current" if index_result.get("status") == "success" else index_result.get("status", "skipped")
        refresh["embeddings"] = aos_embeddings.safe_refresh().get("status")
    except Exception as exc:  # the canonical save succeeded; report derived-index failure honestly
        refresh = {"search": "failed", "graphify": "not_configured", "error": str(exc)}

    frontmatter, markdown_body = aos_indexer.parse_frontmatter(persisted)
    return {
        "success": True,
        "path": body.path,
        "id": frontmatter.get("id"),
        "type": frontmatter.get("type"),
        "title": _markdown_title(markdown_body, target.name),
        "content": persisted,
        "revision": _workflow_revision(persisted),
        "modified": datetime.datetime.fromtimestamp(target.stat().st_mtime).isoformat(),
        "refresh": refresh,
    }


_MEMORY_GRAPH_CACHE: dict = {"mtime": None, "nodes": {}, "by_pointer": {}, "neighbours": {}}


class MemoryOpenRequest(BaseModel):
    path: str
    kind: str = "file"


def _memory_windows_path(path: Path) -> str:
    text = path.as_posix()
    match = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", text)
    if not match:
        return ""
    return f"{match.group(1).upper()}:" + (match.group(2) or "/").replace("/", "\\")


_MEMORY_EVIDENCE_TYPES = {"historical_source", "source", "source_card"}


def _memory_readable_pointer(pointer: str) -> Path:
    """Resolve a Business Brain pointer the global search scope may read (read-only)."""
    registry = business_brain_scope.load_registry()
    canonical = registry.canonical_pointer(pointer)
    if registry.scope_for_search_identity("business_brain", canonical) != "global":
        raise ValueError("pointer is outside the global Memory scope")
    return business_brain.resolve_business_brain_pointer(canonical).resolved_path


def _memory_edit_block(pointer: str, frontmatter: dict) -> str:
    """Empty when the existing /api/dashboard/memory/save gate accepts the note; never widens it.

    Preserved evidence (historical sources, source records, source cards) is
    additionally read-only here even when the registry would allow a write.
    """
    try:
        _dashboard_memory_note_path(pointer)
    except business_brain_scope.ClientScopeError:
        return "Read-only: this file is outside the editable Business Brain scope."
    except (business_brain.BusinessBrainPointerError, ValueError) as exc:
        return f"Read-only: {exc}"
    if str(frontmatter.get("type") or "").lower() in _MEMORY_EVIDENCE_TYPES or str(frontmatter.get("canonical_truth") or "").lower() == "false":
        return "Read-only: preserved source evidence is not edited from the Memory browser."
    return ""


def _memory_vault_dir(relative: str) -> Path:
    vault = Path(business_brain.BUSINESS_BRAIN_ROOT).resolve()
    clean = str(relative or "").strip().strip("/").replace("\\", "/")
    if any(part in {"..", ""} or part.startswith(".") for part in clean.split("/") if clean):
        raise ValueError("folder must be a plain Business Brain relative path")
    target = (vault / clean).resolve() if clean else vault
    target.relative_to(vault)
    if not target.is_dir():
        raise FileNotFoundError(clean)
    return target


def _memory_graph_neighbours(pointer: str) -> dict:
    graph_path = GRAPHIFY_BRAIN_DIR / "document_graphs" / "ttros-business-brain" / "published" / "graph.json"
    try:
        mtime = graph_path.stat().st_mtime
    except OSError:
        return {"available": False, "reason": "Business Brain Graphify projection is not published."}
    if _MEMORY_GRAPH_CACHE["mtime"] != mtime:
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False, "reason": "Business Brain Graphify projection is unreadable."}
        nodes = {node["id"]: node for node in graph.get("nodes", []) if node.get("kind") == "note"}
        by_pointer = {node.get("source_path"): node for node in nodes.values()}
        neighbours: dict[str, set] = {}
        for edge in graph.get("edges", []):
            if edge.get("relation") == "wiki_link" and edge.get("source") in nodes and edge.get("target") in nodes:
                neighbours.setdefault(edge["source"], set()).add(edge["target"])
                neighbours.setdefault(edge["target"], set()).add(edge["source"])
        _MEMORY_GRAPH_CACHE.update({"mtime": mtime, "nodes": nodes, "by_pointer": by_pointer, "neighbours": neighbours, "built": datetime.datetime.fromtimestamp(mtime).date().isoformat()})
    nodes = _MEMORY_GRAPH_CACHE["nodes"]
    node = _MEMORY_GRAPH_CACHE["by_pointer"].get(pointer)
    if node is None:
        return {"available": True, "in_graph": False, "graph_built": _MEMORY_GRAPH_CACHE.get("built"), "links": []}
    links = [{"path": nodes[other]["source_path"], "title": nodes[other].get("title") or nodes[other]["relative_path"]}
             for other in sorted(_MEMORY_GRAPH_CACHE["neighbours"].get(node["id"], ()))]
    return {"available": True, "in_graph": True, "node_id": node["id"], "graph_built": _MEMORY_GRAPH_CACHE.get("built"), "links": links}


@app.get("/api/memory/search")
def memory_search(q: str = "", limit: int = 30, offset: int = 0, casey_only: bool = False):
    started = time.perf_counter()
    refresh = aos_entity_index.ensure_current()
    entities = aos_entity_index.search_entities(q)
    result = aos_indexer.search(q, source="business_brain", client_scope="global", limit=limit, offset=offset,
                                hybrid=True, casey_only=casey_only) if q.strip() else {"groups": {}}
    rows = [row for group in result.get("groups", {}).values() for row in group]
    meta = aos_entity_index.document_meta([row["path"] for row in rows])
    knowledge, sources = [], []
    for row in rows:
        doc = meta.get(row["path"]) or {"role": "knowledge", "type_label": "Business Brain note", "date": "", "date_text": "", "entities": [], "canonical": False}
        authority, authority_label = shared_brain_read.authority_for(row["path"], doc)
        item = {**doc, "path": row["path"], "title": row.get("title") or doc.get("title"), "snippet": row.get("snippet", "")[:240],
                "authority": authority, "authority_label": authority_label, "match": row.get("match", "keyword")}
        (sources if doc.get("role") == "source" else knowledge).append(item)
    return {
        "query": q, "resolution": entities["resolution"], "entities": entities["entities"],
        "knowledge": knowledge, "sources": sources, "index_refresh": refresh.get("status"),
        "offset": result.get("offset", 0), "next_offset": result.get("next_offset"), "casey_only": casey_only,
        "hybrid": result.get("hybrid", {"requested": False, "used": False}),
        "search_mode": result.get("search_mode") if q.strip() else None,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2), "model_invoked": False,
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.get("/api/memory/entity")
def memory_entity(id: str):
    aos_entity_index.ensure_current()
    view = aos_entity_index.entity_view(id)
    if view is None:
        raise HTTPException(status_code=404, detail="entity not found in the derived entity index")
    return {**view, "model_invoked": False}


@app.get("/api/memory/document")
def memory_document(path: str):
    try:
        target = _memory_readable_pointer(path)
        if target.stat().st_size > _MEMORY_NOTE_MAX_BYTES:
            raise ValueError("document exceeds the reader size limit")
        content = target.read_text(encoding="utf-8", errors="replace")
    except (business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"document unavailable: {exc}")
    aos_entity_index.ensure_current()
    meta = aos_entity_index.document_meta([path]).get(path, {})
    frontmatter, body = aos_indexer.parse_frontmatter(content)
    edit_block = _memory_edit_block(path, frontmatter)
    return {
        "path": path,
        "title": meta.get("title") or _markdown_title(body, target.name),
        "content": content,
        "revision": _workflow_revision(content),
        "editable": not edit_block,
        "edit_block_reason": edit_block,
        "windows_path": _memory_windows_path(target),
        "windows_folder": _memory_windows_path(target.parent),
        "wsl_path": str(target),
        "modified": datetime.datetime.fromtimestamp(target.stat().st_mtime).isoformat(),
        "meta": meta,
        "frontmatter_type": frontmatter.get("type"),
        "graphify": _memory_graph_neighbours(path),
        "model_invoked": False,
    }


@app.get("/api/memory/browse")
def memory_browse(dir: str = ""):
    """List exactly one Business Brain folder (no recursive scan)."""
    try:
        target = _memory_vault_dir(dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="folder not found")
    vault = Path(business_brain.BUSINESS_BRAIN_ROOT).resolve()
    registry = business_brain_scope.load_registry()
    folders, files = [], []
    with os.scandir(target) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                relative = Path(entry.path).resolve().relative_to(vault).as_posix()
            except ValueError:
                continue  # link that leaves the vault
            if entry.is_dir():
                folders.append({"name": entry.name, "dir": relative})
            elif entry.is_file():
                pointer = f"business_brain:{relative}"
                readable = registry.scope_for_search_identity("business_brain", pointer) == "global"
                files.append({"name": entry.name, "path": pointer, "readable": readable, "size_bytes": entry.stat().st_size})
    folders.sort(key=lambda item: item["name"].lower())
    files.sort(key=lambda item: item["name"].lower())
    relative_dir = target.relative_to(vault).as_posix() if target != vault else ""
    return {"dir": relative_dir, "windows_path": _memory_windows_path(target), "folders": folders, "files": files, "model_invoked": False}


@app.post("/api/memory/open")
def memory_open(body: MemoryOpenRequest):
    """Open a readable Business Brain file (or its folder) with Windows Explorer."""
    try:
        if body.kind == "dir":
            target = _memory_vault_dir(body.path)
        else:
            if body.kind not in {"file", "folder"}:
                raise ValueError("kind must be file, folder or dir")
            target = _memory_readable_pointer(body.path)
            if not target.is_file():
                raise FileNotFoundError(body.path)
    except (business_brain.BusinessBrainPointerError, business_brain_scope.ClientScopeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="path not found")
    windows_path = _memory_windows_path(target)
    explorer = Path("/mnt/c/Windows/explorer.exe")
    if not windows_path or not explorer.is_file():
        raise HTTPException(status_code=500, detail="Windows Explorer is not reachable from this runtime")
    argv = [str(explorer), f"/select,{windows_path}"] if body.kind == "folder" else [str(explorer), windows_path]
    try:
        subprocess.Popen(argv, cwd="/mnt/c", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"local open failed: {exc}")
    return {"success": True, "windows_path": windows_path, "kind": body.kind, "model_invoked": False}


@app.get("/api/dashboard/graphify")
def dashboard_graphify():
    cli_path = shutil.which("graphify") or "/home/operator/.local/bin/graphify"
    repositories = GRAPHIFY_SERVICE.list_repositories()
    return {
        "available": Path(cli_path).is_file(),
        "installed": Path(cli_path).is_file(),
        "status": f"{len(repositories)} repository graph{'s' if len(repositories) != 1 else ''} available",
        "cli_path": cli_path,
        "version": GRAPHIFY_SERVICE._graphify_version(),
        "brain_root": str(GRAPHIFY_BRAIN_DIR),
        "graph_output_dir": str(GRAPHIFY_OUT_DIR),
        "data_inspected": True,
        "repos": repositories,
        "visibility": graphify_visibility.snapshot(
            vault=business_brain.BUSINESS_BRAIN_ROOT, registry=business_brain_scope.load_registry(),
            graph_root=GRAPHIFY_BRAIN_DIR, db_path=aos_indexer.runtime_db_path(), repositories=repositories,
        ),
        "model_invoked": False,
        "token_usage_text": "Token usage: no agent invocation",
    }


@app.get("/api/dashboard/repo-ingest")
def dashboard_repo_ingest():
    return {
        "available": True,
        "steps": ["Fetch", "Quarantine scan", "Code-only Graphify", "Validate + publish", "Available"],
        "repos": GRAPHIFY_SERVICE.list_repositories(),
        "note": "All ingest steps are deterministic and model-free. Existing repositories require the explicit repository-specific Re-fetch action.",
        "canonical_roots": {"clones": str(GRAPHIFY_CLONE_DIR), "outputs": str(GRAPHIFY_OUT_DIR), "receipts": str(GRAPHIFY_RECEIPTS_DIR)},
        "model_invoked": False,
        "token_usage_text": "Token usage: no agent invocation",
    }


def _graphify_http_error(exc: GraphifyError) -> HTTPException:
    detail = str(exc)
    status = 409 if "already exists" in detail or "requires an existing" in detail else 400
    return HTTPException(status_code=status, detail=detail)


@app.post("/api/graphify/fetch")
def graphify_fetch(body: GraphifyFetchRequest):
    """Explicit deterministic fetch. URL validation itself never clones or invokes a model."""
    try:
        validate_github_url(body.url)
        return {"success": True, "repository": GRAPHIFY_SERVICE.ingest(body.url, refetch=False), "model_invoked": False}
    except GraphifyError as exc:
        raise _graphify_http_error(exc) from exc


@app.post("/api/graphify/refetch")
def graphify_refetch(body: GraphifyFetchRequest):
    """Explicit repository-specific replacement fetch with atomic publication."""
    try:
        validate_github_url(body.url)
        return {"success": True, "repository": GRAPHIFY_SERVICE.ingest(body.url, refetch=True), "model_invoked": False}
    except GraphifyError as exc:
        raise _graphify_http_error(exc) from exc


@app.post("/api/graphify/rebuild")
def graphify_rebuild(body: GraphifyRepositoryRequest):
    try:
        return {"success": True, "repository": GRAPHIFY_SERVICE.rebuild(body.owner, body.repository), "model_invoked": False}
    except GraphifyError as exc:
        raise _graphify_http_error(exc) from exc


@app.post("/api/graphify/action")
def graphify_action(body: GraphifyActionRequest):
    """Run only installed deterministic Graphify query/explain/affected/path commands."""
    try:
        return {"success": True, **GRAPHIFY_SERVICE.action(body.owner, body.repository, body.action, body.inputs)}
    except (GraphifyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/graphify/artifacts/{owner}/{repository}/{artifact_path:path}")
def graphify_artifact(owner: str, repository: str, artifact_path: str):
    """Serve only approved, provenance-bound regular files below repo_graphs."""
    try:
        path, media_type = GRAPHIFY_SERVICE.artifact(owner, repository, artifact_path)
        content = path.read_bytes()
    except (GraphifyError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    if media_type.startswith("text/html"):
        headers["Content-Security-Policy"] = GRAPH_CSP
    if media_type == "application/json" and path.name == "graph.json":
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return Response(content=content, media_type=media_type, headers=headers)


def _platform_operator_read(tool_slug: str, arguments: dict | None = None) -> dict:
    """One read-only tool on the operator's Composio Platform connection (Connections Hub)."""
    from ttros_connections import ConnectionError as HubError, perform_operator
    try:
        receipt = perform_operator(BASE_DIR, str(tool_slug or "").strip().upper(), arguments or {}, expected_mode="read")
    except HubError as exc:
        return {"ok": False, "error": str(exc)[:600]}
    return {"ok": True, "result": receipt.get("result"), "receipt": receipt["id"]}


@app.get("/api/cost-control/status")
def cost_control_status(scope_type: str, scope_id: str):
    """Expose the one dial, exact usage/cost breakdown, and named-scope fuse."""
    try:
        return {"success": True, **step6_fuse_status(Step6Scope(scope_type, scope_id), root=BASE_DIR)}
    except CostControlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/cost-control/fuse-override")
def cost_control_fuse_override(body: Step6FuseOverride):
    """Set or revoke one visible, idempotent, scope-only fuse override."""
    try:
        result = set_step6_override(
            Step6Scope(body.scope_type, body.scope_id),
            active=body.active,
            reason=body.reason,
            root=BASE_DIR,
        )
        return {"success": True, **result}
    except CostControlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class ComposioAction(BaseModel):
    tool_slug: str
    json_args: dict


@app.post("/api/connectors/composio/action")
def composio_action(body: ComposioAction, request: Request):
    """Run one read-only Composio tool for a verified operator on the Platform connection.

    The caller must carry the same verified operator identity Remote PC requires
    (Cloudflare Access JWT or Tailscale Serve identity, allowlisted). The action must
    classify as read; mutations and unclassifiable slugs are refused before the adapter.
    """
    require_remote_operator(request, "Composio action")
    tool = str(body.tool_slug or "").strip().upper()
    if not isinstance(body.json_args, dict):
        raise HTTPException(status_code=422, detail="json_args must be a JSON object")
    kind, reason = classify_composio_action(tool)
    if kind != "read":
        raise HTTPException(status_code=403, detail=f"Composio action refused ({kind}): {reason}")
    response = _platform_operator_read(tool, body.json_args)
    response.setdefault("tool_slug", tool)
    response.setdefault("args", body.json_args)
    return response




# -- David ---------------------------------------------------------------------------------

def _david() -> aos_david.David:
    if DAVID is None or TURNS is None:
        raise HTTPException(status_code=503, detail="David is starting; try again in a moment.")
    return DAVID


def _ask_david_turn_run(record: dict, text: str, conversation_id: str, delivery_id: str, connection_context=None,
                        verified_chat: str = "", operator_scope: bool | None = None,
                        user_request: str | None = None, attachment_ids: tuple[str, ...] = (),
                        artifact_scope: str = "") -> None:
    try:
        result = _david().turn(
            conversation_id, text, delivery_id=delivery_id, connection_context=connection_context,
            verified_chat=verified_chat, requested_at=datetime.datetime.fromisoformat(record["created_at"]),
            operator_scope=operator_scope, capability_request=user_request, attachment_ids=attachment_ids,
            artifact_scope=artifact_scope, effect_key=record["turn_id"])
        outcome = {"status": "done", "result": result}
    except Exception as exc:  # recorded and reported, never raised into the thread machinery
        outcome = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:600]}"}
    TURNS.finish(record, outcome)


def _turn_ack(turn_id: str) -> dict:
    return {"success": True, "kind": "david_job", "job_id": turn_id, "status": "running",
            "message": "David is still working on that; the answer will follow here.",
            "poll_url": f"/api/dashboard/ask-david/jobs/{turn_id}", "token_usage_text": "Token usage: pending"}


def _turn_response(record: dict) -> dict:
    if record["status"] == "done":
        return record["result"]
    if record["status"] == "running":
        return _turn_ack(record["turn_id"])
    return {"success": False, "kind": "david_reply", "job_id": record["turn_id"], "status": record["status"],
            "response": f"David could not answer ({record['status']}): {record.get('error') or 'no reply'}. It was not retried."}


# One conversational entrypoint for the Telegram bridge and the Dashboard.
@app.post("/api/dashboard/ask-david")
def dashboard_ask_david(body: AskDavidRequest, request: Request):
    text = str(body.text or "").strip()
    connection_context = None
    reminder_request = aos_reminders.command(text) is not None
    automation_request = aos_automation.intent(text) is not None
    connection_request = not reminder_request and not automation_request and (
        text == '/connections' or text.startswith(('/connection ', '/capability connections.'))
        or aos_david.is_connection_request(text))
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if len(text.encode("utf-8")) > 8_000:
        raise HTTPException(status_code=422, detail="text must contain 8,000 UTF-8 bytes or fewer")
    if len(body.delivery_id) > 160:
        raise HTTPException(status_code=422, detail="delivery_id must contain 160 characters or fewer")
    try:
        aos_david.conversation_key(body.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    import david_ingress
    try:
        david_ingress.reject_retired_test(BASE_DIR, text)
    except ValueError as exc:
        return {'success': False, 'kind': 'david_reply', 'response': str(exc),
                'model_process_count': 0, 'queue_delta': 0}
    try:
        verified_chat = david_ingress.verify_telegram(BASE_DIR, text, body.conversation_id, body.delivery_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    from ttros_connections import Connections, ConnectionError
    operator_scope = True
    owner = ""
    conversation_id = body.conversation_id
    if verified_chat:
        operator_scope = verified_chat in aos_authority.load_notifications(BASE_DIR)["telegram"]
        if operator_scope and not reminder_request:
            try:
                connection_context = (Connections(DASHBOARD_IDENTITY), DASHBOARD_IDENTITY.telegram_operator(verified_chat))
            except ConnectionError as exc:
                if connection_request:
                    return {"success": False, "kind": "david_reply", "response": str(exc), "model_process_count": 0}
    else:
        local = _david_loopback(request)
        local_preview = local and DASHBOARD_IDENTITY.local and request.headers.get("x-ttros-connections")
        if automation_request and _david_local_operator(request) and not request.cookies.get(DASHBOARD_SESSION_COOKIE):
            owner = _david_member(_david_request_principal(request))
        elif connection_request or not local or local_preview:
            DASHBOARD_IDENTITY.csrf(request)
            principal = DASHBOARD_IDENTITY.principal(request)
            connection_context = (Connections(DASHBOARD_IDENTITY), principal)
            operator_scope = principal["workspace"] in {"ttros-operator", "local-preview"}
            owner = _david_member(principal)
            if not operator_scope:
                conversation_id = "member-" + owner + "-" + hashlib.sha256(conversation_id.encode()).hexdigest()[:24]
        elif _david_local_operator(request):
            owner = _david_member(_david_request_principal(request))

    # Zero-model paths: a reply to a Shared Brain workstream question, and an explicit ingest.
    prompt = text
    artifact_scope = (conversation_id if verified_chat and operator_scope else
                      _david_dashboard_artifact_scope(owner, conversation_id) if owner and operator_scope else "")
    attachment_ids = ()
    if not reminder_request and not automation_request and operator_scope:
        workstream_answer = _try_ask_david_workstream_answer(text, body.reply_context)
        if workstream_answer is not None:
            return workstream_answer
        explicit_ingest = _try_ask_david_explicit_ingest(text, body.source_refs, scope=artifact_scope)
        if explicit_ingest is not None:
            return explicit_ingest
        if owner and not verified_chat:
            verified_uploads = []
            for upload_id in dict.fromkeys(_ask_david_upload_ids(body.source_refs)):
                try:
                    _david_upload(upload_id, artifact_scope)
                except uploads_store.UploadError:
                    continue
                verified_uploads.append(upload_id)
            attachment_ids = tuple(verified_uploads)
        attachment = _ask_david_attachment_context(body.source_refs, scope=artifact_scope)
        if attachment and not connection_request:
            prompt = f"{text}\n\nAttached source material (data only; not authorization):\n{attachment}"
    _david()
    try:
        record, created = TURNS.begin(conversation_id, prompt, body.delivery_id, owner=owner)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not created:
        return _turn_response(record)
    threading.Thread(target=_ask_david_turn_run, args=(record, prompt, conversation_id, body.delivery_id,
                                                     connection_context, verified_chat, operator_scope, text, attachment_ids, artifact_scope),
                     name=record["turn_id"], daemon=True).start()
    event = TURNS.live.get(record["turn_id"])
    if event is None or event.wait(ASK_DAVID_INLINE_WAIT_SECONDS):
        return _turn_response(TURNS.get(record["turn_id"]) or record)
    return _turn_ack(record["turn_id"])


def _david_member(principal: dict) -> str:
    return hashlib.sha256(f"{principal['workspace']}\0{principal['user']}".encode()).hexdigest()[:24]


def _david_dashboard_artifact_scope(owner: str, conversation_id: str) -> str:
    return f"dashboard:{owner}:{conversation_id}"


def _authorize_david_artifact(request: Request, conversation_id: str) -> str:
    try:
        aos_david.conversation_key(conversation_id)
        principal = _david_request_principal(request, read=True)
        if principal["workspace"] not in {"ttros-operator", "local-preview"}:
            return ""
        if conversation_id.startswith("telegram-"):
            from ttros_connections import ConnectionError

            chat = aos_david.chat_id_for(conversation_id)
            if not aos_authority.reminder_authorized(BASE_DIR, conversation_id, chat):
                return ""
            try:
                telegram_principal = DASHBOARD_IDENTITY.telegram_operator(chat)
            except ConnectionError:
                return ""
            return conversation_id if _david_member(telegram_principal) == _david_member(principal) else ""
        return _david_dashboard_artifact_scope(_david_member(principal), conversation_id)
    except (HTTPException, ValueError):
        return ""


from david_artifacts import artifact_router as david_artifact_router  # noqa: E402

app.include_router(david_artifact_router(lambda: BASE_DIR, _authorize_david_artifact))

_DAVID_SPEECH_SLOT = threading.BoundedSemaphore()


def _david_local_operator(request: Request, *, read=False) -> bool:
    origin = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site", "same-origin")
    if not _david_loopback(request):
        return False
    if read:
        return origin in {None, "http://localhost:3010", "http://127.0.0.1:3010"} and fetch_site in {"same-origin", "none"}
    return (origin in {"http://localhost:3010", "http://127.0.0.1:3010"}
            and request.headers.get("x-ttros-connections") == "1" and fetch_site == "same-origin")


def _david_request_principal(request: Request, *, read=False) -> dict:
    try:
        return DASHBOARD_IDENTITY.principal(request)
    except HTTPException:
        if request.cookies.get(DASHBOARD_SESSION_COOKIE) or not _david_local_operator(request, read=read):
            raise
        return {"user": "local-operator", "workspace": "local-preview", "method": "local_preview"}


def _authorize_david_speech(request: Request) -> None:
    if _david_local_operator(request):
        return
    DASHBOARD_IDENTITY.csrf(request)
    if DASHBOARD_IDENTITY.principal(request)["workspace"] not in {"ttros-operator", "local-preview"}:
        raise HTTPException(403, "Dashboard operator access is required.")


def _david_speech_work(operation, *args):
    import david_speech

    if not _DAVID_SPEECH_SLOT.acquire(blocking=False):
        raise HTTPException(429, "Local speech is busy. Try again shortly or use text.")
    try:
        return operation(*args)
    except (david_speech.SpeechError, OSError) as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, david_speech.SpeechError)
                            else "Local speech processing failed.") from None
    finally:
        _DAVID_SPEECH_SLOT.release()


@app.post("/api/dashboard/ask-david/transcribe")
async def dashboard_david_transcribe(request: Request):
    _authorize_david_speech(request)
    import david_speech

    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    suffix = david_speech.MIME_SUFFIX.get(mime)
    if not suffix:
        raise HTTPException(415, "This recording format is unsupported. Try another browser or type your request.")
    data = bytearray()
    async for chunk in request.stream():
        if len(chunk) + len(data) > david_speech.MAX_BYTES:
            raise HTTPException(413, "The recording exceeds 10 MB.")
        data.extend(chunk)
    text, _ = await run_in_threadpool(_david_speech_work, david_speech.transcribe, data, suffix)
    return JSONResponse({"text": text}, headers={"Cache-Control": "no-store"})


class DavidSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@app.post("/api/dashboard/ask-david/speak")
def dashboard_david_speak(body: DavidSpeechRequest, request: Request):
    _authorize_david_speech(request)
    import david_speech

    text, _ = david_speech.spoken_text(body.text)
    if not text:
        raise HTTPException(422, "This reply has no speakable text.")

    def render():
        with tempfile.TemporaryDirectory(prefix="david-dashboard-speech-") as directory:
            voice = Path(directory) / "reply.wav"
            david_speech.synthesize(text, voice)
            return voice.read_bytes()

    return Response(_david_speech_work(render), media_type="audio/wav", headers={"Cache-Control": "no-store"})


def _david_loopback(request: Request) -> bool:
    from urllib.parse import urlsplit

    local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
    if (request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}
            or request.url.hostname not in local_hosts
            or any(name in request.headers for name in ("cf-ray", "cf-connecting-ip", "tailscale-user-login"))):
        return False
    origin = request.headers.get("origin")
    try:
        return origin is None or urlsplit(origin).hostname in local_hosts
    except ValueError:
        return False


def _check_david_turn_access(request: Request, record: dict) -> None:
    owner = record.get("owner")
    if owner:
        if _david_member(_david_request_principal(request, read=True)) != owner:
            raise HTTPException(status_code=404, detail="unknown turn")
    elif not _david_loopback(request):
        principal = DASHBOARD_IDENTITY.principal(request)
        if principal["workspace"] != "ttros-operator":
            raise HTTPException(status_code=404, detail="unknown turn")


@app.get("/api/dashboard/ask-david/jobs/{turn_id}")
def dashboard_ask_david_job(turn_id: str, request: Request):
    _david()
    record = TURNS.get(turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown job")
    _check_david_turn_access(request, record)
    return record


@app.get("/api/dashboard/ask-david/deliveries/{delivery_id}")
def dashboard_ask_david_delivery(delivery_id: str, request: Request):
    """Read-only recovery after an uncertain Telegram POST; never submits work."""
    _david()
    if not delivery_id or len(delivery_id) > 160:
        raise HTTPException(status_code=404, detail="unknown delivery")
    record = TURNS.by_delivery(delivery_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown delivery; not retried")
    _check_david_turn_access(request, record)
    return record


@app.get("/api/david/jobs")
def david_jobs(conversation_id: str = "", limit: int = 20, request: Request = None):
    jobs = aos_jobs.list_jobs(BASE_DIR, conversation_id=conversation_id or None, limit=None)
    visible = []
    for job in jobs:
        if (job.get("executor") or {}).get("external_mcp"):
            continue
        if (job.get("automation") or job.get("automation_origin")) and not _automation_access(request, job):
            continue
        if len(visible) < max(1, min(limit, 100)) or (job.get("automation") and job["status"] not in aos_jobs.TERMINAL):
            visible.append(job)
    return {"jobs": [{key: job.get(key) for key in ("id", "status", "topic", "request", "conversation_id", "created_at",
                                                    "finished_at", "failure_class", "detail", "artifacts", "effects",
                                                    "usage", "accounting", "receipts", "notification",
                                                    "reminder", "last_delivery", "scheduled_at", "automation_origin")}
                     | ({"automation": aos_automation.view(BASE_DIR, job)} if job.get("automation") else {})
                     for job in visible]}


def _automation_access(request, job):
    if request is None:
        return ""
    owner = _authorize_david_artifact(request, job["conversation_id"])
    return owner if owner and owner == job["executor"].get("capability_owner") else ""


@app.get("/api/david/jobs/{job_id}")
def david_job(job_id: str, request: Request = None):
    try:
        job = aos_jobs.load(BASE_DIR, job_id)
    except aos_jobs.JobError as exc:
        raise HTTPException(status_code=404, detail="unknown job") from exc
    if (job.get("executor") or {}).get("external_mcp") or (
            (job.get("automation") or job.get("automation_origin")) and not _automation_access(request, job)):
        raise HTTPException(status_code=404, detail="unknown job")
    return {**job, "automation": aos_automation.view(BASE_DIR, job)} if job.get("automation") else job


class AutomationChange(BaseModel):
    operation: str
    revision: int
    definition: dict | None = None


@app.patch("/api/david/jobs/{job_id}/automation")
def david_automation_change(job_id: str, body: AutomationChange, request: Request):
    from ttros_connections import Connections

    _david()
    if not _david_local_operator(request):
        DASHBOARD_IDENTITY.csrf(request)
    try:
        job = aos_jobs.load(BASE_DIR, job_id)
    except aos_jobs.JobError as exc:
        raise HTTPException(status_code=404, detail="unknown automation") from exc
    owner = _automation_access(request, job)
    if not job.get("automation") or not owner:
        raise HTTPException(status_code=404, detail="unknown automation")
    principal = _david_request_principal(request, read=True)
    chat = aos_david.chat_id_for(job["conversation_id"]) if job["conversation_id"].startswith("telegram-") else ""
    if chat:
        principal = DASHBOARD_IDENTITY.telegram_operator(chat)
    try:
        updated = aos_automation.change(BASE_DIR, job_id, body.operation, owner=owner, definition=body.definition,
                                        expected_revision=body.revision, context=(Connections(DASHBOARD_IDENTITY), principal),
                                        verified_chat=chat)
    except (aos_jobs.JobError, aos_reminders.ReminderError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    RUNNER.submit(job_id)
    return aos_automation.view(BASE_DIR, updated)


# -- Company Command Centre: CRM & Sales ----------------------------------------------
# One read projection and one validated writer (tools/aos_crm.py) over the prospect ledger,
# the CRM event log and linked jobs. Nothing here contacts anyone.

class CrmEventRequest(BaseModel):
    client_scope: str = "global"
    kind: str
    record_id: str
    payload: dict = {}
    basis: str = "confirmed"
    source_refs: list[str] = []
    request_key: str
    expected_revision: str | None = None


def _crm_refusal(exc: Exception) -> HTTPException:
    text = str(exc)
    if isinstance(exc, aos_crm.CrmConflict):
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=403 if text.startswith("scope refused") else 422, detail=text)


@app.get("/api/crm/overview")
def crm_overview(scope: str = "global"):
    try:
        return aos_crm.project(BASE_DIR, scope=scope)
    except aos_crm.CrmError as exc:
        raise _crm_refusal(exc) from exc


@app.get("/api/crm/home")
def crm_home(scope: str = "global"):
    try:
        return aos_crm.home_summary(BASE_DIR, scope=scope)
    except aos_crm.CrmError as exc:
        raise _crm_refusal(exc) from exc


@app.get("/api/crm/records/{record_id:path}")
def crm_record(record_id: str, scope: str = "global"):
    try:
        return aos_crm.record_detail(BASE_DIR, record_id, scope=scope)
    except aos_crm.CrmError as exc:
        if str(exc).startswith("unknown CRM record"):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise _crm_refusal(exc) from exc


@app.post("/api/crm/events")
def crm_event(body: CrmEventRequest):
    if body.kind == "contact.subscription":
        raise HTTPException(status_code=422, detail="Use the authenticated subscriber lifecycle endpoint")
    try:
        event, created = aos_crm.record_event(
            BASE_DIR, client_scope=body.client_scope, kind=body.kind, record_id=body.record_id, payload=body.payload,
            basis=body.basis, source_refs=body.source_refs, request_key=body.request_key, actor="operator",
            expected_revision=body.expected_revision)
        record = aos_crm.record_detail(BASE_DIR, event["record_id"], scope=event["client_scope"])
    except aos_crm.CrmError as exc:
        raise _crm_refusal(exc) from exc
    return {"success": True, "created": created, "event": event, "record": record}


class GuideCaptureRequest(BaseModel):
    name: str
    email: str
    guide_consent: StrictBool
    follow_up_consent: StrictBool
    attribution: dict = {}


@app.post("/api/lead-magnet/capture")
def lead_magnet_capture(body: GuideCaptureRequest, request: Request):
    # Only the site's server-side route holds this token. Public clients cannot pick a scope.
    token = os.environ.get("TTROS_LEAD_MAGNET_CAPTURE_TOKEN", "")
    supplied = request.headers.get("x-ttros-capture-token", "")
    if not token or not hmac.compare_digest(token, supplied):
        raise HTTPException(status_code=403, detail="capture endpoint unavailable")
    try:
        result = aos_lead_magnet.capture(BASE_DIR, name=body.name, email=body.email,
                                         guide_consent=body.guide_consent,
                                         follow_up_consent=body.follow_up_consent,
                                         attribution=body.attribution)
    except (aos_lead_magnet.CaptureError, aos_crm.CrmError, aos_jobs.JobError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result["draft_job_id"] and RUNNER is not None:
        RUNNER.submit(result["draft_job_id"])
    return result


# Revisit: when the website adapter adopts the newsletter transport contract.
# Disabled by default; none of these handlers sends email or submits a runner job.
def _subscriber_auth(request: Request, *, provider=False):
    if os.environ.get("TTROS_SUBSCRIBERS_ENABLED") != "true":
        raise HTTPException(status_code=503, detail="subscriber lifecycle disabled")
    key = "TTROS_BREVO_WEBHOOK_TOKEN" if provider else "TTROS_LEAD_MAGNET_CAPTURE_TOKEN"
    header = "x-ttros-brevo-token" if provider else "x-ttros-capture-token"
    expected = os.environ.get(key, "")
    supplied = request.headers.get(header, "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(status_code=403, detail="subscriber endpoint unavailable")


class SubscriberRequest(BaseModel):
    name: str
    email: str
    consent: StrictBool
    notice_version: str
    request_id: str
    model_config = ConfigDict(extra="forbid")


class SubscriberToken(BaseModel):
    token: str
    model_config = ConfigDict(extra="forbid")


class SubscriberDelivery(BaseModel):
    contact_id: str
    request_id: str
    status: str
    message_id: str
    receipt_id: str
    model_config = ConfigDict(extra="forbid")


class SubscriberUnsubscribeLink(BaseModel):
    contact_id: str
    request_id: str
    model_config = ConfigDict(extra="forbid")


class SubscriberProviderEvent(BaseModel):
    event: str
    message_id: str = Field(alias="message-id")
    email: str
    id: str | int
    ts_event: int | None = None
    date: str | None = None
    model_config = ConfigDict(extra="ignore")  # Brevo adds provider metadata.


def _subscriber_result(call):
    try:
        return call()
    except aos_crm.CrmError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/subscribers/request")
def subscriber_request(body: SubscriberRequest, request: Request):
    _subscriber_auth(request)
    response = JSONResponse(_subscriber_result(lambda: aos_subscribers.request_subscription(
        BASE_DIR, **body.model_dump())))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/subscribers/confirm")
def subscriber_confirm(body: SubscriberToken, request: Request):
    _subscriber_auth(request)
    return _subscriber_result(lambda: aos_subscribers.confirm(BASE_DIR, token=body.token))


@app.post("/api/subscribers/unsubscribe")
def subscriber_unsubscribe(body: SubscriberToken, request: Request):
    # Reached through the existing protected website relay; no direct public API exposure.
    _subscriber_auth(request)
    return _subscriber_result(lambda: aos_subscribers.unsubscribe(BASE_DIR, token=body.token))


@app.post("/api/subscribers/delivery")
def subscriber_delivery(body: SubscriberDelivery, request: Request):
    _subscriber_auth(request)
    return _subscriber_result(lambda: aos_subscribers.delivery(BASE_DIR, **body.model_dump()))


@app.post("/api/subscribers/unsubscribe-link")
def subscriber_unsubscribe_link(body: SubscriberUnsubscribeLink, request: Request):
    _subscriber_auth(request)
    response = JSONResponse(_subscriber_result(lambda: aos_subscribers.issue_unsubscribe(BASE_DIR, **body.model_dump())))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/subscribers/brevo-events")
def subscriber_brevo_event(body: SubscriberProviderEvent, request: Request):
    _subscriber_auth(request, provider=True)
    data = body.model_dump()
    event_id = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return _subscriber_result(lambda: aos_subscribers.provider_event(BASE_DIR,
        event=body.event, email=body.email, message_id=body.message_id, event_id=event_id, provider_receipt=data))


# -- Company Command Centre: Websites & Digital ----------------------------------------
# A website change is one aos_jobs job (tools/aos_website.py): isolated worktree, tests, local
# preview and a held publish effect. The Dashboard never publishes or deploys.

DASHBOARD_WEBSITE_CONVERSATION = "dashboard-websites"


class WebsiteChangeRequest(BaseModel):
    project: str
    request: str
    request_key: str


def _website_jobs(limit: int = 30) -> list[dict]:
    jobs = [job for job in aos_jobs.list_jobs(BASE_DIR, limit=None)
            if (job.get("executor") or {}).get("kind") == aos_website.EXECUTOR_KIND]
    return jobs[:limit]


@app.get("/api/website/projects")
def website_projects():
    try:
        projects = aos_website.list_projects(BASE_DIR)
    except aos_website.WebsiteError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    jobs = [aos_website.job_view(BASE_DIR, job) for job in _website_jobs()]
    return {"projects": projects, "jobs": [{key: row[key] for key in (
        "job_id", "status", "lifecycle", "request", "project", "project_name", "created_at", "finished_at",
        "failure_class", "summary", "publish", "tests", "preview", "branch", "receipts",
        "readiness", "released", "artifact_problems")} for row in jobs]}


@app.post("/api/website/requests")
def website_request(body: WebsiteChangeRequest):
    key = " ".join(str(body.request_key or "").split())
    if len(key) < 6:
        raise HTTPException(status_code=422, detail="request_key (6+ characters) is required")
    try:
        job, created = aos_website.submit_request(
            BASE_DIR, project=body.project, request=body.request, conversation_id=DASHBOARD_WEBSITE_CONVERSATION,
            topic="website", dedupe_key=f"{DASHBOARD_WEBSITE_CONVERSATION}\0{key}")
    except (aos_website.WebsiteError, aos_jobs.JobError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if RUNNER is not None and job["status"] == "queued":
        RUNNER.submit(job["id"])
    return {"success": True, "created": created, "job": aos_website.job_view(BASE_DIR, aos_jobs.load(BASE_DIR, job["id"]))}


def _website_job(job_id: str) -> dict:
    try:
        job = aos_jobs.load(BASE_DIR, job_id)
    except aos_jobs.JobError as exc:
        raise HTTPException(status_code=404, detail="unknown job") from exc
    if (job.get("executor") or {}).get("kind") != aos_website.EXECUTOR_KIND:
        raise HTTPException(status_code=404, detail="not a website job")
    return job


@app.get("/api/website/jobs/{job_id}")
def website_job(job_id: str):
    return aos_website.job_view(BASE_DIR, _website_job(job_id))


PREVIEW_CSP = "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; sandbox"


@app.get("/api/website/jobs/{job_id}/preview/{relative:path}")
def website_preview(job_id: str, relative: str):
    job = _website_job(job_id)
    try:
        path = aos_website.preview_file(BASE_DIR, job, relative)
    except aos_website.WebsiteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, headers={"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff",
                                       "Cache-Control": "no-store"})
