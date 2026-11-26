"""Read-only job status for the Shared Brain connector (ChatGPT and Claude).

David's background jobs (AOS-YYYY-<decimal>, plus preserved J-...) are read from ``queue/jobs/``; historical Work Queue items
(AOS-YYYY-<decimal>, before the 2026-11-15 clean break) from the preserved ``queue/work_items.jsonl``.
Adds only what a remote reader needs: lifecycle timestamps, a receipt-location allowlist and
secret scrubbing. Never writes, claims or dispatches anything.

Revisit: when the job record, receipt locations or the Shared Brain tool contract change.
Last touched: 2026-11-15.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from aos_job_ids import AOS_ID_RE, JOB_ID_RE as ALL_JOB_ID_RE, storage_key

ROOT = Path(os.environ.get("AOS_ROOT", "/opt/ttros-core")).resolve()
QUEUE_FILE = ROOT / "queue" / "work_items.jsonl"
ITEM_ID_RE = AOS_ID_RE
JOB_ID_RE = re.compile(r"^J-\d{8}T\d{6}Z-[0-9a-f]{8}$")
JOBS_DIR = ROOT / "queue" / "jobs"
MAX_RECEIPT_CHARS = 2_000


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _items() -> list[dict[str, Any]]:
    return _read_jsonl(QUEUE_FILE)


def _item_ref(item: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or "")[:240],
        "state": str(item.get("status") or ""),
        "owner": str(item.get("owner") or "unassigned"),
    }


def _find_item(item_id: str) -> dict[str, Any] | None:
    target = str(item_id or "").strip().upper()
    return next((row for row in _items() if str(row.get("id") or "").upper() == target), None)


def _latest_receipt(item: dict[str, Any]) -> dict[str, Any] | None:
    receipts = item.get("receipts")
    if not isinstance(receipts, list):
        return None
    values = [row for row in receipts if isinstance(row, dict) and row.get("path")]
    return values[-1] if values else None


# Receipts are only ever written under these two folders; anything else in a receipt row is
# refused rather than read, so a bad row cannot turn this tool into a repo file reader.
RECEIPT_PREFIXES = ("queue/receipts/", "workflows/queue_artifacts/")
RECEIPT_SUFFIXES = (".md", ".txt", ".json")
MAX_TITLE = 240

_TOKEN_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
)
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|client[_-]?secret|authorization)"
    r"[\"']?\s*[:=]\s*)(?!\[redacted\])[\"']?[^\s\"',;]{6,}")


def _scrub(text: str) -> tuple[str, int]:
    total = 0
    for pattern in _TOKEN_PATTERNS:
        text, count = pattern.subn("[redacted]", text)
        total += count
    text, count = _ASSIGNMENT.subn(r"\1[redacted]", text)
    return text, total + count


def _allowed_receipt(relative: str) -> bool:
    if not relative or relative.startswith("/") or "\\" in relative or ".." in Path(relative).parts:
        return False
    return relative.startswith(RECEIPT_PREFIXES) and relative.endswith(RECEIPT_SUFFIXES)


def _receipt(item: dict[str, Any]) -> dict[str, Any]:
    latest = _latest_receipt(item)
    total = sum(1 for row in item.get("receipts") or [] if isinstance(row, dict) and row.get("path"))
    if latest is None:
        return {"available": False, "reason": "no receipt", "receipt_count": total}
    relative = str(latest.get("path") or "")
    meta = {
        "path": relative,
        "status": str(latest.get("status") or ""),
        "created_at": latest.get("created_at"),
        "receipt_count": total,
    }
    if not _allowed_receipt(relative):
        return {"available": False, "reason": "receipt outside queue receipt folders", **meta}
    root = ROOT
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
        text = target.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return {"available": False, "reason": "unavailable", **meta}
    cap = MAX_RECEIPT_CHARS
    # Scrub the whole receipt before cutting, so a secret cannot straddle the cap.
    content, redactions = _scrub(text)
    return {"available": True, **meta, "content": content[:cap],
            "truncated": len(content) > cap, "redactions": redactions}


def _david_job(job_id: str) -> dict[str, Any]:
    try:
        job = json.loads((ROOT / "queue/jobs" / f"{storage_key(job_id)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"success": True, "found": False, "job_id": job_id}
    if (job.get("executor") or {}).get("external_mcp"):
        return {"success": True, "found": False, "job_id": job_id}
    detail, _ = _scrub(str(job.get("detail") or ""))
    summary, _ = _scrub(str((job.get("result") or {}).get("summary") or ""))
    return {
        "success": True, "found": True, "job_id": job_id, "title": str(job.get("request") or "")[:MAX_TITLE],
        "status": str(job.get("status") or ""), "owner": "david", "owner_type": "agent",
        "topic": job.get("topic"), "failure_class": job.get("failure_class") or "",
        "detail": detail[:MAX_RECEIPT_CHARS], "summary": summary[:MAX_RECEIPT_CHARS],
        "files": [row.get("path") for row in job.get("artifacts") or []],
        "timestamps": {"created_at": job.get("created_at"), "updated_at": job.get("updated_at"),
                       "finished_at": job.get("finished_at")},
        "source": "queue/jobs", "receipt": _receipt(job),
        "brain_context_used": job.get("brain_context_used") or [], "usage": job.get("usage"),
        "accounting": job.get("accounting"),
    }


def queue_status(job_id: str) -> dict[str, Any]:
    """Live state, timestamps and outcome for one David job, or a historical Work Queue item."""
    raw = str(job_id or "").strip()
    if raw.upper().startswith("AOS-"):
        raw = raw.upper()
    if ALL_JOB_ID_RE.fullmatch(raw):
        current = _david_job(raw)
        if current["found"] or JOB_ID_RE.fullmatch(raw):
            return current
    target = raw.upper()
    if not ITEM_ID_RE.match(target):
        return {"success": False, "found": False, "job_id": raw[:32],
                "error": "invalid job id; expected J-YYYYMMDDTHHMMSSZ-xxxxxxxx or AOS-YYYY-<decimal sequence, minimum four digits>"}
    # One snapshot of the queue file; every field below comes from the same read.
    item = _find_item(target)
    if item is None:
        return {"success": True, "found": False, "job_id": target}
    ref = _item_ref(item)
    claim = item.get("claim") if isinstance(item.get("claim"), dict) else {}
    dispatch = item.get("dispatch") if isinstance(item.get("dispatch"), dict) else {}
    return {
        "success": True,
        "found": True,
        "job_id": ref["id"],
        "title": ref["title"][:MAX_TITLE],
        "status": ref["state"],
        "owner": ref["owner"],
        "owner_type": str(item.get("owner_type") or ""),
        "claimed_by": claim.get("claimed_by"),
        "parent_id": item.get("parent_id"),
        "priority": item.get("priority"),
        "timestamps": {
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "accepted_at": dispatch.get("accepted_at"),
            "claimed_at": claim.get("claimed_at"),
            "worker_heartbeat_at": item.get("worker_heartbeat_at"),
        },
        "latest_receipt": _receipt(item),
        "source": "queue/work_items.jsonl",
    }
