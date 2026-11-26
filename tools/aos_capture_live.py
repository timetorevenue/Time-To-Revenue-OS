"""Independent metadata-only Gmail capture foundation; no model or job execution.

Provider normalization, allowlist and bounded history are retained from main
2901088. Existing capture storage/cursor/evidence identities remain authoritative.
A future M1/M5 Gmail specialist can consume the scoped metadata projections.
Revisit: when Gmail history or the capture evidence contract changes. · 2026-11-15.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import fcntl
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(os.environ.get("AOS_ROOT", Path(__file__).resolve().parents[1])).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.aos_capture import (CaptureStorage, CaptureEnvelope, CaptureMetadataProjection,
    CaptureError, DeltaBatch, LiveCaptureDisabled, utc_now)
from tools.aos_queue_storage import durable_replace_text
from tools.business_brain_scope import load_registry

PROVIDER_KEY = "gmail-live"
PROVIDER_NAME = "gmail_composio"
LOCK_PATH = ROOT / "capture/runtime/control/poll.lock"
ACTIVATION_CONTRACT = {"approved": True, "contract": "gmail_history_metadata_read_only"}
ALLOWED_ACTIONS = {"GMAIL_GET_PROFILE", "GMAIL_LIST_HISTORY", "GMAIL_FETCH_EMAILS", "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID"}
PROHIBITED_LABELS = {"SPAM", "TRASH", "SENT"}
MAX_HISTORY_PAGES = 5
MAX_HISTORY_MESSAGES_PER_POLL = 25
MAX_BOOTSTRAP_RESULTS = 100

RESYNC_PATH = Path("control/gmail_history_resync.json")   # under capture/runtime
RESYNC_OVERLAP = dt.timedelta(hours=24)        # re-read before the anchor; dedup absorbs repeats
RESYNC_WINDOW = dt.timedelta(days=1)           # first window per poll; halved while truncated
RESYNC_MIN_WINDOW = dt.timedelta(minutes=15)
RESYNC_RESULTS = MAX_HISTORY_MESSAGES_PER_POLL  # same per-poll message bound as history draining
RESYNC_CALLS = 4                               # listing calls per poll while narrowing a window
_EXPIRED = re.compile(r"\b404\b|not ?found|notfound|start_?history_?id|history ?id", re.I)


class HistoryCursorExpired(CaptureError):
    """Gmail rejected the stored history checkpoint itself; recover by bounded time-window resync."""


class MessageGone(CaptureError):
    """Gmail answered 404 notFound for one listed message: it was deleted after it was listed.

    Nothing about it can be captured any more, so the poll skips it and counts it instead of
    failing closed forever on the same history entry (production 2026-11-15)."""


def _sha(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

class PollLock:
    def __init__(self, path: Path = LOCK_PATH):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "PollLock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = self.path.open("a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise LiveCaptureDisabled("capture poll already running") from exc
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()

class ComposioReadOnlyExecutor:
    """Validated read-only calls through the repository's one Composio spine."""

    def __init__(self):
        self.action_counts: Counter[str] = Counter()

    @staticmethod
    def _validate(action: str, payload: dict[str, Any]) -> None:
        if action not in ALLOWED_ACTIONS:
            raise CaptureError("Composio action is outside the read-only Gmail allowlist")
        if payload.get("user_id", "me") != "me":
            raise CaptureError("Gmail user_id must remain the connected mailbox alias")
        if action == "GMAIL_LIST_HISTORY":
            if payload.get("label_id") != "INBOX" or payload.get("history_types") != ["messageAdded"]:
                raise CaptureError("Gmail history scope must be INBOX message additions only")
            if not str(payload.get("start_history_id") or "").isdigit():
                raise CaptureError("Gmail history start cursor is invalid")
            if int(payload.get("max_results", 0)) not in range(1, 501):
                raise CaptureError("Gmail history page size is invalid")
        elif action == "GMAIL_FETCH_EMAILS":
            if payload.get("label_ids") != ["INBOX"]:
                raise CaptureError("Gmail bootstrap must be INBOX-only")
            if payload.get("include_payload") is not False or payload.get("include_spam_trash") is not False:
                raise CaptureError("Gmail bootstrap must be metadata-only and exclude spam/trash")
            if int(payload.get("max_results", 0)) not in range(1, MAX_BOOTSTRAP_RESULTS + 1):
                raise CaptureError("Gmail bootstrap size is invalid")
        elif action == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID":
            if payload.get("format") != "metadata" or not str(payload.get("message_id") or ""):
                raise CaptureError("Gmail item retrieval must be one metadata-only message")

    def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One read-only Gmail tool on the operator's Composio Platform Gmail connection."""
        self._validate(action, payload)
        tools = str(Path(__file__).resolve().parent)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        from ttros_connections import ConnectionError as HubError, ToolFailed, perform_operator
        self.action_counts[action] += 1
        try:
            receipt = perform_operator(ROOT, action, payload, expected_mode="read")
        except ToolFailed as exc:
            self._raise_expired(action, payload, {"error": exc.provider_error}, None)
            raise CaptureError("Composio read-only Gmail action failed") from None
        except HubError as exc:
            raise CaptureError(f"Composio read-only Gmail action failed: {str(exc)[:200]}") from None
        data = receipt.get("result")
        if not isinstance(data, dict):
            raise CaptureError("Composio read-only Gmail result lacked object data")
        return data

    @staticmethod
    def _raise_expired(action: str, payload: dict[str, Any], response: Any, value: Any) -> None:
        """Gmail answers an expired/unknown startHistoryId with 404 on the first history page.

        Only that exact shape is classified; the provider's text never reaches a receipt.
        """
        texts = [str(item) for source in (response, value) if isinstance(source, dict)
                 for item in (source.get("error"), source.get("stderr")) if item]
        if action == "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID":
            if any(re.search(r"\b404\b", text) for text in texts) and any(
                    re.search(r"notfound|not found", text, re.I) for text in texts):
                raise MessageGone("Gmail message no longer exists")
            return
        if action != "GMAIL_LIST_HISTORY" or payload.get("page_token"):
            return
        if any(_EXPIRED.search(text) for text in texts) and any(
                re.search(r"\b404\b|not ?found|notfound", text, re.I) for text in texts):
            raise HistoryCursorExpired("Gmail history checkpoint is no longer valid")

def _labels(value: dict[str, Any]) -> set[str]:
    raw = value.get("labelIds") or value.get("label_ids") or value.get("labels") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).upper() for item in raw if str(item)}

def _headers(value: dict[str, Any]) -> dict[str, str]:
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    raw = value.get("headers") or payload.get("headers") or []
    headers: dict[str, str] = {}
    if isinstance(raw, dict):
        return {str(k).lower(): str(v) for k, v in raw.items()}
    for row in raw if isinstance(raw, list) else []:
        if isinstance(row, dict) and row.get("name"):
            headers[str(row["name"]).lower()] = str(row.get("value") or "")
    return headers

def _text_field(value: dict[str, Any], *names: str) -> str:
    for name in names:
        raw = value.get(name)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            candidate = raw.get("email") or raw.get("address") or raw.get("value")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""

def _timestamp(value: dict[str, Any], headers: dict[str, str]) -> str:
    raw = _text_field(value, "timestamp", "received_at", "date", "internalDate", "internal_date")
    if raw.isdigit():
        number = int(raw)
        if number > 10_000_000_000:
            number //= 1000
        return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    candidate = raw or headers.get("date", "")
    if candidate:
        try:
            parsed = email.utils.parsedate_to_datetime(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = dt.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
    return utc_now()

def _message_identity(value: dict[str, Any]) -> tuple[str, str]:
    message_id = _text_field(value, "id", "messageId", "message_id")
    thread_id = _text_field(value, "threadId", "thread_id")
    return message_id, thread_id

def _normalize_message(
    value: dict[str, Any],
    *,
    history_id: str,
    mailbox_sender_sha256: str,
) -> CaptureEnvelope | None:
    nested = value.get("message") if isinstance(value.get("message"), dict) else value
    labels = _labels(nested)
    if labels and ("INBOX" not in labels or labels.intersection(PROHIBITED_LABELS)):
        return None
    headers = _headers(nested)
    message_id, thread_id = _message_identity(nested)
    sender_raw = _text_field(nested, "sender", "from", "from_address") or headers.get("from", "")
    sender = email.utils.parseaddr(sender_raw)[1].strip().lower()
    if not message_id or not thread_id or not sender:
        return None
    if _sha(sender) == mailbox_sender_sha256:
        return None
    subject = _text_field(nested, "subject") or headers.get("subject", "")
    safe_headers = {
        "list_id_present": bool(headers.get("list-id")),
        "auto_submitted": headers.get("auto-submitted", ""),
        "precedence": headers.get("precedence", ""),
        "category": _text_field(nested, "category"),
        "ambiguous_candidate": False,
        "is_reply": bool(headers.get("in-reply-to") or headers.get("references")),
    }
    return CaptureEnvelope(
        provider=PROVIDER_NAME,
        message_id=message_id,
        thread_id=thread_id,
        history_id=str(history_id),
        timestamp=_timestamp(nested, headers),
        sender=sender,
        subject=subject,
        source_type="gmail",
        headers=safe_headers,
    ).validate()

def _history_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("history") or data.get("histories") or data.get("history_records") or []
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

def _added_refs(rows: Iterable[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    refs: list[tuple[str, dict[str, Any]]] = []
    seen = set()
    for row in rows:
        history_id = str(row.get("id") or row.get("historyId") or row.get("history_id") or "")
        additions = row.get("messagesAdded") or row.get("messages_added") or []
        for addition in additions if isinstance(additions, list) else []:
            if not isinstance(addition, dict):
                continue
            message = addition.get("message") if isinstance(addition.get("message"), dict) else addition
            message_id, _thread_id = _message_identity(message)
            if not message_id or message_id in seen:
                continue
            labels = _labels(message)
            if labels and ("INBOX" not in labels or labels.intersection(PROHIBITED_LABELS)):
                continue
            refs.append((history_id, message))
            seen.add(message_id)
    return refs

def _bounded_history_refs(
    refs: list[tuple[str, dict[str, Any]]],
) -> tuple[list[tuple[str, dict[str, Any]]], str, bool]:
    """Take a bounded prefix without splitting one Gmail history checkpoint.

    Advancing only to the last completely consumed history ID lets subsequent
    polls drain a backlog without skipping messages or overrunning the
    scheduler's bounded execution window.
    """
    selected: list[tuple[str, dict[str, Any]]] = []
    last_history = ""
    offset = 0
    while offset < len(refs):
        history_id = refs[offset][0]
        end = offset + 1
        while end < len(refs) and refs[end][0] == history_id:
            end += 1
        group = refs[offset:end]
        if selected and len(selected) + len(group) > MAX_HISTORY_MESSAGES_PER_POLL:
            break
        selected.extend(group)
        last_history = history_id or last_history
        offset = end
        if len(selected) >= MAX_HISTORY_MESSAGES_PER_POLL:
            break
    return selected, last_history, offset < len(refs)

def _bootstrap_messages(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("messages", "emails", "items"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [row for row in raw if isinstance(row, dict)]
    return []

def _metadata_message(executor: ComposioReadOnlyExecutor, value: dict[str, Any]) -> dict[str, Any] | None:
    """Metadata for one listed message, or None when Gmail says it was deleted since listing."""
    message_id, _thread_id = _message_identity(value)
    if not message_id:
        raise CaptureError("Gmail metadata row lacked a message identifier")
    try:
        return executor.execute("GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {
            "user_id": "me",
            "message_id": message_id,
            "format": "metadata",
        })
    except MessageGone:
        return None

def _profile(executor: ComposioReadOnlyExecutor) -> tuple[str, str]:
    profile = executor.execute("GMAIL_GET_PROFILE", {"user_id": "me"})
    history_id = str(profile.get("historyId") or profile.get("history_id") or "")
    mailbox = _text_field(profile, "emailAddress", "email_address", "email").lower()
    if not history_id.isdigit() or not mailbox:
        raise CaptureError("Gmail profile lacked a usable checkpoint or mailbox identity")
    return history_id, _sha(mailbox)

def _prepare_delta(
    executor: ComposioReadOnlyExecutor,
    *,
    cursor: str | None,
    bootstrap_hours: int,
) -> tuple[DeltaBatch, dict[str, Any]]:
    current_history, mailbox_hash = _profile(executor)
    envelopes: list[CaptureEnvelope] = []
    history_entries = 0
    history_backlog_truncated = False
    messages_gone = 0
    boundary: str | None = None
    if cursor is None:
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=bootstrap_hours)
        boundary = start.isoformat().replace("+00:00", "Z")
        data = executor.execute("GMAIL_FETCH_EMAILS", {
            "user_id": "me",
            "label_ids": ["INBOX"],
            "query": f"after:{int(start.timestamp())}",
            "include_payload": False,
            "include_spam_trash": False,
            "ids_only": False,
            "verbose": False,
            "max_results": MAX_BOOTSTRAP_RESULTS,
        })
        if data.get('nextPageToken') or data.get('next_page_token'):
            raise CaptureError('Gmail bootstrap was truncated; cursor remains unchanged')
        rows = _bootstrap_messages(data)
        history_entries = len(rows)
        for row in rows:
            candidate = _normalize_message(
                row,
                history_id=current_history,
                mailbox_sender_sha256=mailbox_hash,
            )
            if candidate is None:
                detailed = _metadata_message(executor, row)
                messages_gone += detailed is None
                candidate = None if detailed is None else _normalize_message(
                    detailed,
                    history_id=current_history,
                    mailbox_sender_sha256=mailbox_hash,
                )
            if candidate is not None:
                envelopes.append(candidate)
    else:
        page_token = ""
        refs: list[tuple[str, dict[str, Any]]] = []
        next_history = current_history
        last_row_history = ""
        more_pages = False
        for _page in range(MAX_HISTORY_PAGES):
            payload: dict[str, Any] = {
                "user_id": "me",
                "start_history_id": cursor,
                "history_types": ["messageAdded"],
                "label_id": "INBOX",
                "max_results": 100,
            }
            if page_token:
                payload["page_token"] = page_token
            data = executor.execute("GMAIL_LIST_HISTORY", payload)
            rows = _history_rows(data)
            history_entries += len(rows)
            refs.extend(_added_refs(rows))
            for row in rows:
                row_id = str(row.get("id") or row.get("historyId") or row.get("history_id") or "")
                if row_id.isdigit():
                    last_row_history = row_id
            next_history = str(data.get("historyId") or data.get("history_id") or next_history)
            page_token = str(data.get("nextPageToken") or data.get("next_page_token") or "")
            if not page_token:
                break
        else:
            more_pages = True
        refs, bounded_history, history_backlog_truncated = _bounded_history_refs(refs)
        if more_pages:
            # The fetched pages are an ordered prefix of a larger backlog. Advance only
            # through what this poll consumed: the last whole checkpoint taken, or the
            # last fetched row when every addition on these pages was taken/filtered.
            # Never jump to the mailbox head; fail closed if no forward checkpoint exists.
            checkpoint = bounded_history if history_backlog_truncated else last_row_history
            if not checkpoint.isdigit() or int(checkpoint) <= int(cursor):
                raise CaptureError("Gmail history pagination exceeded the bounded page limit")
            bounded_history, history_backlog_truncated = checkpoint, True
        seen = set()
        for history_id, ref in refs:
            message_id, _thread_id = _message_identity(ref)
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            detailed = _metadata_message(executor, ref)
            messages_gone += detailed is None
            candidate = None if detailed is None else _normalize_message(
                detailed,
                history_id=history_id or next_history,
                mailbox_sender_sha256=mailbox_hash,
            )
            if candidate is not None:
                envelopes.append(candidate)
        current_history = bounded_history if history_backlog_truncated else next_history
    unique = {row.message_id: row for row in envelopes}
    return DeltaBatch(next_cursor=current_history, envelopes=tuple(unique.values())), {
        "bootstrap_boundary_utc": boundary,
        "bootstrap_hours": bootstrap_hours if cursor is None else 0,
        "history_entries_received": history_entries,
        "authorized_envelopes": len(unique),
        "history_backlog_truncated": bool(cursor is not None and history_backlog_truncated),
        "messages_gone": messages_gone,
    }


def capture_batch(storage: CaptureStorage, batch: DeltaBatch, *, registry=None) -> dict:
    """Publish global/quarantined metadata before advancing the existing cursor.

    Caller holds poll.lock. Replay repairs a raw-only interrupted write. Client
    matches are excluded before opening any scope storage, never relabelled global.
    No proposal queue, automatic Brain promotion or specialist execution is involved.
    """
    registry = registry or load_registry()
    registry.resolve_scope("global")
    previous = storage.cursor(PROVIDER_KEY)
    if (not batch.next_cursor.isdigit() or (previous and
            (not previous.isdigit() or (len(batch.next_cursor.lstrip('0') or '0'), batch.next_cursor.lstrip('0') or '0') <
             (len(previous.lstrip('0') or '0'), previous.lstrip('0') or '0')))):
        raise CaptureError("invalid or regressing Gmail cursor")
    counts = dict(records_added=0, records_deduplicated=0, records_excluded=0, records_quarantined=0)
    processing = {row['record_id']: row for row in storage.processing_rows(PROVIDER_KEY)}
    for envelope in batch.envelopes:
        envelope.validate()
        resolution = registry.resolve_capture_identity(sender=envelope.sender, thread_id=envelope.thread_id)
        scope = resolution.client_scope
        if scope and scope != "global":
            # Preserve recoverable provider provenance, without opening client
            # storage or placing its subject/content in global context. This is
            # the existing provider audit journal, never runnable work.
            if envelope.record_id not in processing:
                storage.append_processing(PROVIDER_KEY, dict(record_id=envelope.record_id,
                    deduplication_key=envelope.deduplication_key, client_scope=scope,
                    provider_message_id=envelope.message_id, provider_thread_id=envelope.thread_id,
                    history_id=envelope.history_id, timestamp=envelope.timestamp,
                    disposition='excluded_from_global_capture', captured=False, processed_at=utc_now()))
                processing[envelope.record_id] = {'client_scope': scope}
            counts['records_excluded'] += 1
            continue
        if resolution.state != "matched":
            scope = None
            counts['records_quarantined'] += 1
        if envelope.record_id in processing and processing[envelope.record_id].get('client_scope') != scope:
            raise CaptureError('existing capture scope changed; explicit reconciliation required')
        folder = storage.scope_dir(scope)
        reference = f"capture:{scope or '_unresolved'}:{envelope.record_id}"
        raw_path = folder / "raw/records.jsonl"
        existing = [r for r in storage._read_jsonl(raw_path) if r.get('record_id') == envelope.record_id]
        if len(existing) > 1:
            raise CaptureError("duplicate capture identity")
        if not existing:
            storage._append_json(raw_path, dict(schema_version=1, record_id=envelope.record_id,
                deduplication_key=envelope.deduplication_key, provider=envelope.provider,
                provider_message_id=envelope.message_id, provider_thread_id=envelope.thread_id,
                history_id=envelope.history_id, timestamp=envelope.timestamp,
                sender_sha256=registry.capture_identity_hash(envelope.sender),
                thread_sha256=registry.capture_identity_hash(envelope.thread_id),
                subject=envelope.subject, source_type=envelope.source_type,
                linked_item_id=envelope.linked_item_id, safe_headers=dict(envelope.headers or {}),
                scope_state=resolution.state, client_scope=scope, scope_kind=resolution.scope_kind,
                matched_by=list(resolution.matched_by), evidence_reference=reference))
        raw = existing[0] if existing else storage._read_jsonl(raw_path)[-1]
        # Capture IDs must retain their original scope and source provenance.
        if raw.get('client_scope') != scope or raw.get('evidence_reference') != reference:
            raise CaptureError("capture identity conflicts with existing scope")
        classification = subject_classification(str(raw.get('subject') or ''))
        day = dt.datetime.fromisoformat(raw['timestamp'].replace('Z', '+00:00')).date().isoformat()
        evidence = storage.repo_root / 'capture/gmail' / day / (scope or '_unresolved') / (envelope.record_id + '.json')
        if not evidence.exists():
            # Existing private, content-free evidence contract and stable path.
            evidence.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            durable_replace_text(evidence, json.dumps(dict(schema_version=1,
                expires='never; point-in-time capture evidence', record_id=envelope.record_id,
                evidence_reference=reference, client_scope=scope or '_unresolved',
                scope_state=raw['scope_state'], timestamp=raw['timestamp'], source_type='gmail',
                subject_classification=classification, triage_state='captured', route='metadata_only',
                linked_item_id=raw.get('linked_item_id', ''), contains_message_content=False,
                external_actions=0), sort_keys=True) + '\n')
            os.chmod(evidence, 0o600)
        else:
            prior = json.loads(evidence.read_text())
            if (prior.get('record_id') != envelope.record_id or prior.get('evidence_reference') != reference
                    or prior.get('client_scope') != (scope or '_unresolved')
                    or prior.get('contains_message_content') is not False):
                raise CaptureError('existing Gmail evidence conflicts with capture identity')
        if scope:
            registry.validate_capture_evidence_reference(scope, reference)
            row = CaptureMetadataProjection(envelope.record_id, reference, scope,
                raw.get('linked_item_id', ''), classification, raw['timestamp'],
                raw['source_type'], 'captured', 'captured_no_review')
            storage.append_derived(scope, 'metadata', row.to_dict())
        if envelope.record_id not in processing:
            storage.append_processing(PROVIDER_KEY, dict(record_id=envelope.record_id,
                deduplication_key=envelope.deduplication_key, client_scope=scope, processed_at=utc_now()))
            processing[envelope.record_id] = {'client_scope': scope}
        counts['records_deduplicated' if existing else 'records_added'] += 1
    storage.publish_cursor(PROVIDER_KEY, batch.next_cursor)
    return dict(status='success', cursor_advanced=previous != batch.next_cursor,
                content_opened=False, model_invocations=0, **counts)


def subject_classification(subject: str) -> str:
    text = subject.casefold()
    if any(word in text for word in ('newsletter', 'unsubscribe', 'weekly digest')):
        return 'newsletter'
    if any(word in text for word in ('notification', 'alert', 'automated')):
        return 'notification'
    if any(word in text for word in ('approved', 'go ahead', 'please proceed')):
        return 'inbound_claimed_approval'
    return 'business_message'


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(text: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _start_resync(storage: CaptureStorage, executor, *, expired_cursor: str, bootstrap_hours: int,
                  now: dt.datetime) -> dict[str, Any]:
    """Record a durable resync plan. Nothing is captured or advanced here.

    The anchor is the newest already-captured Gmail message (then the cursor's last
    publication time), minus an overlap; history after the resync start resumes from the
    mailbox checkpoint read now. Existing records, cursor and provenance stay untouched.
    """
    resume_history, _mailbox = _profile(executor)
    times = [_parse_time(row.timestamp) for row in storage.metadata_rows() if row.source_type == "gmail"]
    times = [value for value in times if value is not None]
    anchor_source = "last_captured_message"
    if not times:
        try:
            published = json.loads((storage.provider_dir(PROVIDER_KEY) / "cursor.json").read_text(encoding="utf-8"))
            times = [value for value in [_parse_time(published.get("updated_at") or "")] if value is not None]
            anchor_source = "cursor_published_at"
        except (OSError, ValueError):
            times = []
    if times:
        since = min(max(times), now) - RESYNC_OVERLAP
    else:
        since, anchor_source = now - dt.timedelta(hours=bootstrap_hours), "unanchored_bootstrap_window"
    state = {"reason": "history_cursor_expired", "expired_cursor": expired_cursor, "resume_history_id": resume_history,
             "started_at": _iso(now), "since": _iso(since), "anchor": anchor_source}
    durable_replace_text(storage.root / RESYNC_PATH, json.dumps(state, sort_keys=True) + "\n")
    os.chmod(storage.root / RESYNC_PATH, 0o600)
    return state


def _resync_delta(executor, state: dict[str, Any], *, cursor: str) -> tuple[DeltaBatch, dict[str, Any], dict[str, Any]]:
    """One bounded INBOX metadata window, oldest first. Returns the batch, receipt fields and the next plan."""
    since, started = _parse_time(state["since"]), _parse_time(state["started_at"])
    resume = str(state["resume_history_id"])
    if since is None or started is None or not resume.isdigit() or not cursor.isdigit() or int(resume) < int(cursor):
        raise CaptureError("Gmail history resync state is invalid; explicit reconciliation required")
    _current, mailbox_hash = _profile(executor)
    finish = started + dt.timedelta(hours=1)          # overlaps the resumed history; dedup absorbs repeats
    width = dt.timedelta(seconds=int(state.get("width_seconds") or RESYNC_WINDOW.total_seconds()))
    data: dict[str, Any] | None = None
    for _attempt in range(RESYNC_CALLS):
        end = min(since + width, finish)
        listed = executor.execute("GMAIL_FETCH_EMAILS", {
            "user_id": "me", "label_ids": ["INBOX"],
            "query": f"after:{int(since.timestamp())} before:{int(end.timestamp()) + 1}",
            "include_payload": False, "include_spam_trash": False, "ids_only": False, "verbose": False,
            "max_results": RESYNC_RESULTS,
        })
        if not (listed.get("nextPageToken") or listed.get("next_page_token")):
            data = listed
            break
        if width <= RESYNC_MIN_WINDOW:
            raise CaptureError("Gmail resync window is still truncated at its minimum width; cursor unchanged")
        width = max(width / 2, RESYNC_MIN_WINDOW)
    receipt = {"mode": "history_resync", "resync_window_start": _iso(since), "resync_anchor": state.get("anchor"),
               "resync_complete": False}
    if data is None:
        # Dense backlog: keep the narrowed width so the next poll continues; nothing is skipped.
        plan = dict(state, width_seconds=int(width.total_seconds()))
        return DeltaBatch(next_cursor=cursor, envelopes=()), dict(receipt, resync_window_end=None,
                                                                    authorized_envelopes=0), plan
    envelopes, gone = [], 0
    for row in _bootstrap_messages(data):
        candidate = _normalize_message(row, history_id=resume, mailbox_sender_sha256=mailbox_hash)
        if candidate is None:
            detailed = _metadata_message(executor, row)
            gone += detailed is None
            candidate = None if detailed is None else _normalize_message(
                detailed, history_id=resume, mailbox_sender_sha256=mailbox_hash)
        if candidate is not None:
            envelopes.append(candidate)
    complete = end >= finish
    unique = {row.message_id: row for row in envelopes}
    grown = min(width * 2, RESYNC_WINDOW) if len(unique) < RESYNC_RESULTS // 2 else width
    plan = dict(state, since=_iso(end), width_seconds=int(grown.total_seconds()))
    batch = DeltaBatch(next_cursor=resume if complete else cursor, envelopes=tuple(unique.values()))
    return batch, dict(receipt, resync_window_end=_iso(end), resync_complete=complete,
                       authorized_envelopes=len(unique), messages_gone=gone), plan


def _resync_poll(storage: CaptureStorage, executor, *, registry) -> dict[str, Any]:
    path = storage.root / RESYNC_PATH
    state = json.loads(path.read_text(encoding="utf-8"))
    batch, metadata, plan = _resync_delta(executor, state, cursor=storage.cursor(PROVIDER_KEY) or "")
    result = capture_batch(storage, batch, registry=registry)   # records before the cursor/plan advance
    if metadata["resync_complete"]:
        path.unlink(missing_ok=True)
    else:
        durable_replace_text(path, json.dumps(plan, sort_keys=True) + "\n")
    result.update(metadata)
    return result


def poll_once(*, scheduled: bool, bootstrap_hours: int = 24, root: Path = ROOT,
              executor=None, registry=None, now: dt.datetime | None = None) -> dict:
    """Existing timer entrypoint. Activation/control and provider state are reused."""
    root = Path(root)
    storage = CaptureStorage(root / 'capture/runtime', repo_root=root)
    executor = executor or ComposioReadOnlyExecutor()
    try:
        with PollLock(root / 'capture/runtime/control/poll.lock'):
            state = storage.control_state()
            activation = root / 'capture/runtime/control/activation.json'
            if state['kill_switch'] or not state['live_capture_enabled'] or not activation.exists():
                result = dict(status='disabled', cursor_advanced=False, records_added=0)
            else:
                if json.loads(activation.read_text()) != ACTIVATION_CONTRACT:
                    raise CaptureError('capture activation contract mismatch')
                hours = max(1, min(24, bootstrap_hours))
                cursor = storage.cursor(PROVIDER_KEY)
                if (storage.root / RESYNC_PATH).exists():
                    result = _resync_poll(storage, executor, registry=registry)
                else:
                    try:
                        batch, metadata = _prepare_delta(executor, cursor=cursor, bootstrap_hours=hours)
                    except HistoryCursorExpired:
                        if cursor is None:
                            raise
                        _start_resync(storage, executor, expired_cursor=cursor, bootstrap_hours=hours,
                                      now=now or dt.datetime.now(dt.timezone.utc))
                        result = _resync_poll(storage, executor, registry=registry)
                    else:
                        result = capture_batch(storage, batch, registry=registry)
                        result.update(metadata)
    except LiveCaptureDisabled:
        result = dict(status='already_running', cursor_advanced=False, records_added=0)
    except Exception as exc:
        # Payloads, provider IDs and authentication errors stay out of receipts.
        result = dict(status='failed', cursor_advanced=False, error_class=type(exc).__name__)
    # Content-free per-action call counts (as main's receipts carry) separate a
    # provider/transport failure from a bounded-paging or local defect.
    counts = getattr(executor, 'action_counts', None)
    result.update(scheduled=scheduled, observed_at=utc_now(), model_invocations=0,
                  index_status='awaiting_existing_ingestion',
                  provider_actions=dict(sorted(counts.items())) if isinstance(counts, Counter) else {})
    storage._append_json(storage.root / 'control/poll_receipts.jsonl', result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    poll = sub.add_parser('poll')
    poll.add_argument('--scheduled', action='store_true')
    poll.add_argument('--bootstrap-hours', type=int, default=24)
    args = parser.parse_args(argv)
    result = poll_once(scheduled=args.scheduled, bootstrap_hours=args.bootstrap_hours)
    print(json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
