import hashlib
import json
import mimetypes
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import fcntl

WORKSPACE = Path("/opt/ttros-core")
BRIDGE_DIR = WORKSPACE / "connectors" / "telegram_bridge"
ENV_FILE = BRIDGE_DIR / ".env"
ALLOWED_FILE = BRIDGE_DIR / "allowed_chats.json"
LOG_DIR = WORKSPACE / "logs"
RUNTIME_DIR = LOG_DIR / "runtime"
UPDATE_STATE_FILE = RUNTIME_DIR / "telegram_bridge_updates.json"
BRIDGE_LOCK_FILE = RUNTIME_DIR / "telegram_bridge.lock"
PILOT_ID = "example_dealership_motors_b_sales_demo"
BACKEND = "http://127.0.0.1:8010"
DASHBOARD_ORIGIN = "https://ttros.core-site.example"
TOOLS_DIR = WORKSPACE / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import business_brain
import business_brain_inbox
import business_brain_scope
import aos_authority

LOG_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def load_token():
    if not ENV_FILE.exists():
        raise RuntimeError(".env file not found")
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip().strip('"').strip("'")
        if key in {"TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN", "TOKEN"}:
            return value
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in raw and "TOKEN" in raw.upper():
            return raw.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("Telegram token variable not found in .env")


TOKEN = load_token()
API = f"https://api.telegram.org/bot{TOKEN}"
BOT_ID = int(TOKEN.split(":", 1)[0]) if TOKEN.split(":", 1)[0].isdigit() else None


def _parse_chat_ids(raw):
    ids = []
    for part in re.split(r"[,;\s]+", raw or ""):
        value = part.strip()
        if re.fullmatch(r"-?\d+", value):
            ids.append(int(value))
    return ids


def _allowed_file():
    path = os.environ.get("TELEGRAM_ALLOWED_CHATS_FILE", "").strip()
    return Path(path).expanduser() if path else ALLOWED_FILE


def _env_allowed():
    operator_ids = _parse_chat_ids(os.environ.get("TELEGRAM_OPERATOR_CHAT_IDS", ""))
    pilots = {}
    for item in re.split(r"[,;\n]+", os.environ.get("TELEGRAM_PILOT_CHAT_IDS", "")):
        if not item.strip() or ":" not in item:
            continue
        chat_id, pilot_id = item.split(":", 1)
        chat_id = chat_id.strip()
        pilot_id = pilot_id.strip()
        if re.fullmatch(r"-?\d+", chat_id) and pilot_id:
            pilots[chat_id] = pilot_id
    return {"operator_chat_ids": operator_ids, "pilots": pilots}


def api(method, data=None, timeout=60):
    body = urllib.parse.urlencode(data or {}).encode("utf-8")
    req = urllib.request.Request(f"{API}/{method}", data=body)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


_CLOSEOUT_FIELDS = ("Files touched", "Validation", "Connector access", "Token usage", "Blockers", "Next action")
_RAW_METADATA = re.compile(
    r"(?i)(?:session\s*id|prompt\s*dump|command\s*transcript|raw\s+(?:codex|claude|hermes\s+)?transcript|sandbox\s+(?:metadata|mode|permissions))"
)
_AOS_ID_RE = re.compile(r"\bAOS-[0-9]{4}-[0-9]{4,}\b")
_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
STATUS_BACKEND_TIMEOUT_SECONDS = 1.5
STATUS_SEND_TIMEOUT_SECONDS = 3
# David's executive route measured ~33s end to end (2026-08-11 proof). The
# delegate default of 20s would time out every call, so it gets its own budget.
DAVID_TIMEOUT_SECONDS = 180
DAVID_JOB_POLL_SECONDS = 2
DAVID_JOB_WAIT_SECONDS = 3 * 60 * 60
MAX_RECORDED_UPDATE_IDS = 512
_CAPTURE_COMMAND_RE = re.compile(r"^/(?:inbox|capture)(?:@[A-Za-z0-9_]+)?(?:\s+([\s\S]*))?$", re.IGNORECASE)
_SUPPORTED_DOCUMENT_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".rtf", ".csv", ".json", ".yaml", ".yml"}
_SUPPORTED_DOCUMENT_MIME_TYPES = {
    "text/plain", "text/markdown", "text/csv", "application/json", "application/pdf",
    "application/rtf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_UPDATE_STATE_LOCK = threading.Lock()
_AGENT_REQUEST_LOCK = threading.Lock()
_ACTIVE_AGENT_REQUESTS = set()
_BRIDGE_LOCK_HANDLE = None


def compact_telegram_closeout(text, success=None):
    """Make every outbound Telegram message a compact, single-line-field closeout."""
    raw = str(text or "").strip()
    first_line = raw.splitlines()[0].strip().upper() if raw else ""
    passed = success if success is not None else first_line != "NEEDS ATTENTION"
    values = {}
    for field in _CLOSEOUT_FIELDS:
        match = re.search(rf"(?im)^\s*{re.escape(field)}\s*:\s*([^\r\n]*)", raw)
        value = match.group(1).strip(" -*\t")[:700] if match else ""
        if value and not _RAW_METADATA.search(value):
            values[field] = value
    defaults = {
        "Files touched": "None reported",
        "Validation": raw.splitlines()[0][:700] if raw and not _RAW_METADATA.search(raw.splitlines()[0]) else ("Completed" if passed else "Failed"),
        "Connector access": "No connector action reported",
        "Token usage": "unavailable from current CLI output",
        "Blockers": "None" if passed else "See local logs",
        "Next action": "None" if passed else "Review local logs",
    }
    work_item = _extract_work_item_id(raw) or "unavailable"
    title = _extract_work_item_title(raw)
    final_state = _extract_final_state(raw, passed)
    if title and work_item != "unavailable":
        status = "done" if passed else "failed"
        summary = (
            f"{title} completed with final state {final_state}."
            if passed else f"{title} needs attention after final state {final_state}."
        )
        next_action = "None" if passed else "Review local logs"
        return "\n".join(
            [
                f"[{work_item} — {title}]",
                f"Work item: {work_item}",
                f"Status: {status}",
                f"Summary: {summary}",
                f"Next action: {next_action}",
                "Receipt: attached",
                f"Final state: {final_state}",
            ]
            + [f"{field}: {values.get(field) or defaults[field]}" for field in _CLOSEOUT_FIELDS]
        )
    return "\n".join(
        ["PASS" if passed else "NEEDS ATTENTION", f"Work item: {work_item}", f"Final state: {final_state}"]
        + [f"{field}: {values.get(field) or defaults[field]}" for field in _CLOSEOUT_FIELDS]
    )


def _extract_work_item_id(text):
    match = _AOS_ID_RE.search(str(text or ""))
    return match.group(0) if match else ""


def _extract_work_item_title(text):
    raw = str(text or "")
    bracket = re.search(r"(?m)^\s*\[([^\]\r\n]+)\]\s*$", raw)
    if bracket:
        bracket_text = bracket.group(1).strip()
        bracket_text = re.sub(r"^AOS-[0-9]{4}-[0-9]{4,}\s+[—-]\s+", "", bracket_text)
        if bracket_text and not _AOS_ID_RE.fullmatch(bracket_text):
            return bracket_text[:160]
    for pattern in (
        r"(?im)^\s*Work item title\s*:\s*([^\r\n]+)",
        r"(?im)^\s*Task title\s*:\s*([^\r\n]+)",
        r"(?im)^\s*Title\s*:\s*([^\r\n]+)",
    ):
        match = re.search(pattern, raw)
        if match:
            title = re.sub(r"\s+", " ", match.group(1).strip(" -*\t"))
            if title and not _AOS_ID_RE.fullmatch(title):
                return title[:160]
    return ""


def _extract_final_state(text, passed):
    raw = str(text or "")
    for pattern in (
        r"(?im)^\s*Final state\s*:\s*([^\r\n]+)",
        r"(?im)^\s*Final status\s*:\s*([^\r\n]+)",
        r"(?im)^\s*Status\s*:\s*([^\r\n]+)",
    ):
        match = re.search(pattern, raw)
        if match:
            return match.group(1).strip(" -*\t")[:120]
    return "done" if passed else "needs_attention"


def _multipart_api(method, fields, files, timeout=60):
    boundary = f"----aos{int(time.time() * 1000)}"
    chunks = []
    for key, value in (fields or {}).items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for key, path in (files or {}).items():
        file_path = Path(path)
        filename = file_path.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8"))
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        chunks.append(file_path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"{API}/{method}",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def send_document(chat_id, document_path, caption="", *, receipt=None):
    path = Path(document_path)
    if not path.is_file():
        log(f"send_document_missing chat={chat_id} path={path}")
        return False
    try:
        result = _multipart_api(
            "sendDocument",
            {"chat_id": str(chat_id), "caption": str(caption or "")[:1024]},
            {"document": str(path)},
            timeout=60,
        )
        sent = result.get("ok") is True
        message_id = (result.get("result") or {}).get("message_id")
        if sent and receipt is not None and type(message_id) is int:
            receipt["message_id"] = message_id
        return sent
    except Exception as e:
        log(f"send_document_error chat={chat_id} path={path} error={type(e).__name__}")
        return False


# Revisit: when Telegram citation/entity handling changes. Last touched: 2026-11-09.
_BRAIN_REF = r"business_brain:[^\n`<>\[\]()]*?\.(?:md|json|yaml|yml|txt|csv|pdf|docx|html)(?![\w/-])"
_BRAIN_LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*(`?" + _BRAIN_REF + r"`?)\s*\)")
_CITATION_RE = re.compile(
    r"https?://[^\s<>`]+|" + _BRAIN_REF + r"|(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.md\b"
)


def david_citation_payload(text):
    """Keep logical references copyable; code entities suppress filename autolinks.

    No path is inferred from a basename. Web URLs pass through unchanged.
    Telegram entity offsets/lengths count UTF-16 code units, not Python characters.
    """
    text = _BRAIN_LINK_RE.sub(lambda match: match[1].strip('`'), text)
    text = re.sub(r"`(" + _BRAIN_REF + r")`", r"\1", text)
    entities = []
    for match in _CITATION_RE.finditer(text):
        value = match[0]
        is_url = value.startswith(("https://", "http://"))
        if is_url:
            value = value.rstrip(".,;:!?")
            while value.endswith(")") and value.count(")") > value.count("("):
                value = value[:-1]
        entities.append({
            "type": "url" if is_url else "code",
            "offset": len(text[:match.start()].encode("utf-16-le")) // 2,
            "length": len(value.encode("utf-16-le")) // 2,
        })
    return text, entities


# Revisit: when Brain scope or Telegram callbacks change. Last touched: 2026-11-09.
def brain_message_references(text, entities):
    """Only complete code entities from the original message are citation targets."""
    encoded = text.encode("utf-16-le")
    pointers = []
    for entity in entities:
        if entity.get("type") != "code":
            continue
        offset, length = entity.get("offset"), entity.get("length")
        if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
            continue
        if (offset + length) * 2 > len(encoded):
            continue
        value = encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="replace")
        if value.startswith("business_brain:") and value not in pointers:
            pointers.append(value)
    return pointers


def brain_callback_data(pointer):
    # A selector, never a path supplied by the callback. Fits Telegram's 64-byte limit.
    return "brain:" + hashlib.sha256(pointer.encode("utf-8")).hexdigest()[:32]


def resolve_telegram_brain_record(pointer):
    """David's Telegram conversation has global scope; never infer a client scope."""
    registry = business_brain_scope.load_registry()
    resolved = registry.resolve_brain_pointer("global", pointer)
    if registry.scope_for_search_identity("business_brain", resolved.pointer) != "global":
        raise PermissionError("record is outside global scope")
    if Path(resolved.relative_path).suffix.lower() != ".md":
        raise ValueError("only Markdown Brain records can be opened")
    info = resolved.resolved_path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DOCUMENT_BYTES:
        raise ValueError("record is not a bounded regular file")
    return resolved


def brain_reply_markup(text, entities):
    rows = []
    for pointer in brain_message_references(text, entities):
        try:
            resolved = resolve_telegram_brain_record(pointer)
        except (OSError, ValueError, PermissionError):
            continue
        rows.append([
            {"text": "📄 Open Brain record", "callback_data": brain_callback_data(resolved.pointer)},
            {"text": "View in Dashboard", "url": DASHBOARD_ORIGIN + "/memory?record="
             + urllib.parse.quote(resolved.pointer, safe="")},
        ])
    if len(rows) > 1:
        for index, row in enumerate(rows, 1):
            row[0]["text"] += f" {index}"
            row[1]["text"] += f" {index}"
    return {"inline_keyboard": rows} if rows else None


def _brain_record_bytes(resolved):
    """Open beneath the vault with no symlink traversal, including parent swaps."""
    fd = os.open(business_brain.BUSINESS_BRAIN_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(resolved.relative_path).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DOCUMENT_BYTES:
                raise ValueError("record is not a bounded regular file")
            content = stream.read(_MAX_DOCUMENT_BYTES + 1)
            if len(content) > _MAX_DOCUMENT_BYTES:
                raise ValueError("record grew beyond size limit")
            return content
    finally:
        os.close(fd)


def handle_brain_callback(query):
    """Resolve one citation from Telegram's original bot message, without a store."""
    notice = "Cannot open this Brain record. Request a fresh citation or check record access."
    acknowledged = False
    delivered = False
    try:
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        author = message.get("from") or {}
        chat_id = chat.get("id")
        allowed = load_allowed().get("operator_chat_ids", [])
        if (type(chat_id) is not int or chat_id not in allowed or chat.get("type") != "private"
                or sender.get("id") != chat_id or sender.get("is_bot") is not False
                or BOT_ID is None or author.get("id") != BOT_ID or author.get("is_bot") is not True
                or message.get("forward_origin") or message.get("forward_date")
                or not message.get("text", "").startswith("[David]")):
            raise PermissionError("operator or original bot message required")
        selector = query.get("data", "")
        matches = [pointer for pointer in brain_message_references(message["text"], message.get("entities") or [])
                   if brain_callback_data(pointer) == selector]
        buttons = (message.get("reply_markup") or {}).get("inline_keyboard") or []
        if len(matches) != 1 or not any(button.get("callback_data") == selector for row in buttons for button in row):
            raise ValueError("citation selector absent from original message")
        resolved = resolve_telegram_brain_record(matches[0])
        # Stop Telegram's spinner before a potentially slow document upload.
        api("answerCallbackQuery", {"callback_query_id": query.get("id", ""),
                                    "text": "Opening Brain record…"}, timeout=10)
        acknowledged = True
        content = _brain_record_bytes(resolved)
        # Temporary upload snapshot prevents the multipart path from reopening a changed
        # vault target. It is removed immediately and is not a separate record store.
        with tempfile.TemporaryDirectory(prefix="aos-brain-open-") as directory:
            snapshot = Path(directory) / Path(resolved.relative_path).name
            snapshot.write_bytes(content)
            delivered = send_document(chat_id, snapshot, caption=resolved.pointer)
            if not delivered:
                notice = "Record delivery failed. Tap again to retry."
    except Exception as exc:
        log(f"brain_callback_refused error={type(exc).__name__}")
    try:
        if acknowledged:
            if not delivered:
                send(chat_id, notice, preserve_format=True)
        else:
            api("answerCallbackQuery", {"callback_query_id": query.get("id", ""), "text": notice}, timeout=10)
    except Exception as exc:
        log(f"brain_callback_answer_error error={type(exc).__name__}")


def send(chat_id, text, preserve_format=False, document_paths=None, api_timeout=20):
    text = str(text or "").strip()
    if not preserve_format and not is_queue_specific_output(text):
        text = compact_telegram_closeout(text)
    entities = []
    if text.startswith("[David]"):
        text, entities = david_citation_payload(text)
    if len(text) > 3500:
        cut = 3400
        # Do not leave a partial, inaccurate citation at the trim boundary.
        encoded = text.encode("utf-16-le")
        for entity in entities:
            start = len(encoded[:entity["offset"] * 2].decode("utf-16-le"))
            end = len(encoded[:(entity["offset"] + entity["length"]) * 2].decode("utf-16-le"))
            if start < cut < end:
                cut = start
        text = text[:cut] + "\n\n[trimmed]"
        limit = len(text[:cut].encode("utf-16-le")) // 2
        entities = [entity for entity in entities if entity["offset"] + entity["length"] <= limit]
    payload = {"chat_id": str(chat_id), "text": text}
    if entities:
        payload["entities"] = json.dumps(entities)
    if text.startswith("[David]"):
        markup = brain_reply_markup(text, entities)
        if markup:
            payload["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    try:
        response = api("sendMessage", payload, timeout=api_timeout)
        message_sent = bool(response.get("ok"))
        message_id = (response.get("result") or {}).get("message_id")
    except Exception as e:
        log(f"send_error chat={chat_id} error={type(e).__name__}")
        message_sent = False
        message_id = None
    caption = _receipt_caption(text)
    documents = []
    for document_path in document_paths or []:
        receipt = {"path": str(document_path)}
        receipt["sent"] = send_document(chat_id, document_path, caption=caption, receipt=receipt)
        documents.append(receipt)
    return {"message_sent": message_sent, "message_id": message_id, "documents": documents}


def _receipt_caption(text):
    raw = str(text or "")
    title = _extract_work_item_title(raw)
    id_match = _AOS_ID_RE.search(raw)
    final_state = _extract_final_state(raw, True)
    status_suffix = f" {final_state}" if final_state else ""
    if title and id_match:
        return f"{id_match.group(0)} — {title}{status_suffix} receipt"
    if id_match:
        return f"{id_match.group(0)}{status_suffix} receipt"
    return "Receipt"


def send_completion(chat_id, text, document_paths=None, preserve_format=True):
    send(chat_id, text, preserve_format=preserve_format, document_paths=document_paths or [])


def log(message):
    stamp = datetime.now(timezone.utc).isoformat()
    with (LOG_DIR / "telegram_bridge.log").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": stamp, "message": message}, ensure_ascii=False) + "\n")


def load_allowed():
    allowed_file = _allowed_file()
    data = _env_allowed()
    if not allowed_file.exists():
        return data
    try:
        file_data = json.loads(allowed_file.read_text(encoding="utf-8"))
    except Exception:
        return data

    file_operator_ids = []
    if "operator_chat_ids" in file_data:
        raw_operator_ids = file_data.get("operator_chat_ids")
        if isinstance(raw_operator_ids, list):
            file_operator_ids = [
                int(chat_id)
                for chat_id in raw_operator_ids
                if re.fullmatch(r"-?\d+", str(chat_id))
            ]
        else:
            file_operator_ids = _parse_chat_ids(str(raw_operator_ids or ""))
    elif re.fullmatch(r"-?\d+", str(file_data.get("operator_chat_id", ""))):
        file_operator_ids = [int(file_data["operator_chat_id"])]

    data["operator_chat_ids"].extend(
        chat_id for chat_id in file_operator_ids if chat_id not in data["operator_chat_ids"]
    )
    data.setdefault("pilots", {})
    if isinstance(file_data.get("pilots"), dict):
        data["pilots"].update({
            str(chat_id): str(pilot_id)
            for chat_id, pilot_id in file_data["pilots"].items()
            if re.fullmatch(r"-?\d+", str(chat_id))
        })
    return data


def save_report(chat_id, text, source="natural_language"):
    pilots_dir = WORKSPACE / "pilots" / PILOT_ID
    pilots_dir.mkdir(parents=True, exist_ok=True)
    report_file = pilots_dir / "sales_reports.jsonl"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "pilot_id": PILOT_ID,
        "source": source,
        "text": text.strip()
    }
    with report_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def get_backend_status(timeout=STATUS_BACKEND_TIMEOUT_SECONDS):
    req = urllib.request.Request(f"{BACKEND}/api/wsl/status", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        payload = json.loads(res.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backend status returned a non-object response")
    return payload


def _capture_requested(message, text):
    return bool(_CAPTURE_COMMAND_RE.match(text or "") or _is_forwarded(message))


def _is_forwarded(message):
    return any(key in message for key in ("forward_origin", "forward_date", "forward_from", "forward_sender_name", "forward_from_chat"))


def _capture_text_payload(text):
    match = _CAPTURE_COMMAND_RE.match(text or "")
    return (match.group(1) or "") if match else str(text or "")


def _telegram_capture_id(message, kind):
    identity = f"{message.get('chat', {}).get('id', '')}:{message.get('message_id', '')}:{kind}"
    return f"telegram-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _message_timestamp(message):
    try:
        return datetime.fromtimestamp(int(message.get("date")), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _download_telegram_file(file_id, *, max_bytes=business_brain_inbox.MAX_ATTACHMENT_BYTES):
    """Download one Telegram-owned file without logging its protected URL."""
    try:
        result = api("getFile", {"file_id": str(file_id)}, timeout=20).get("result") or {}
        file_path = str(result.get("file_path") or "")
        if not file_path or ".." in Path(file_path).parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", file_path):
            raise ValueError
        request = urllib.request.Request(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}")
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(max_bytes + 1)
    except Exception as exc:
        raise business_brain_inbox.InboxCaptureError("Telegram attachment download failed") from exc
    if len(data) > max_bytes:
        raise business_brain_inbox.InboxCaptureError("Telegram attachment exceeds the inbox size limit")
    return data


def _document_supported(document):
    name = business_brain_inbox.sanitize_filename(document.get("file_name") or "attachment.bin")
    mime = str(document.get("mime_type") or "").lower()
    return Path(name).suffix.lower() in _SUPPORTED_DOCUMENT_EXTENSIONS and (
        not mime or mime == "application/octet-stream" or mime in _SUPPORTED_DOCUMENT_MIME_TYPES
    )


def _declared_file_size(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        raise business_brain_inbox.InboxCaptureError("Telegram attachment size is invalid")


def _transcribe_voice_local(audio_path):
    """Run only an explicitly configured local argv adapter; never use cloud STT."""
    configured = os.environ.get("AOS_VOICE_TRANSCRIBE_COMMAND", "").strip()
    if not configured:
        return None, "unavailable: configure a local Whisper-compatible AOS_VOICE_TRANSCRIBE_COMMAND containing {audio}"
    try:
        argv = shlex.split(configured)
    except ValueError as exc:
        raise business_brain_inbox.InboxCaptureError("local voice transcription command is invalid") from exc
    if not argv or not any("{audio}" in value for value in argv):
        raise business_brain_inbox.InboxCaptureError("local voice transcription command must contain {audio}")
    argv = [value.replace("{audio}", str(audio_path)) for value in argv]
    try:
        result = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise business_brain_inbox.InboxCaptureError("local voice transcription failed") from exc
    transcript = str(result.stdout or "").strip()
    if result.returncode or not transcript:
        raise business_brain_inbox.InboxCaptureError("local voice transcription failed")
    return transcript[:business_brain_inbox.MAX_TEXT_CHARS], "complete"


def capture_operator_message(message):
    """Capture one explicit or forwarded operator message without queue routing."""
    text = str(message.get("text") or message.get("caption") or "")
    payload = _capture_text_payload(text)
    forwarded = _is_forwarded(message)
    stamp = _message_timestamp(message)
    root = business_brain.BUSINESS_BRAIN_ROOT
    base_metadata = {"telegram_forwarded": forwarded}
    document = message.get("document") if isinstance(message.get("document"), dict) else None
    voice = message.get("voice") if isinstance(message.get("voice"), dict) else None

    if document:
        if not _document_supported(document):
            raise business_brain_inbox.InboxCaptureError("unsupported Telegram document type")
        if _declared_file_size(document.get("file_size")) > business_brain_inbox.MAX_ATTACHMENT_BYTES:
            raise business_brain_inbox.InboxCaptureError("Telegram attachment exceeds the inbox size limit")
        capture_id = _telegram_capture_id(message, "file")
        data = _download_telegram_file(document.get("file_id"))
        return business_brain_inbox.capture_attachment(
            data,
            original_filename=document.get("file_name") or "attachment.bin",
            mime_type=document.get("mime_type") or "application/octet-stream",
            capture_id=capture_id,
            captured_at=stamp,
            body=payload or "Attachment captured without transformation.",
            metadata={**base_metadata, "telegram_message_type": "file"},
            root=root,
        )

    if voice:
        if _declared_file_size(voice.get("file_size")) > business_brain_inbox.MAX_ATTACHMENT_BYTES:
            raise business_brain_inbox.InboxCaptureError("Telegram attachment exceeds the inbox size limit")
        capture_id = _telegram_capture_id(message, "voice")
        data = _download_telegram_file(voice.get("file_id"))
        with tempfile.NamedTemporaryFile(prefix="aos-voice-", suffix=".ogg") as temporary:
            temporary.write(data)
            temporary.flush()
            try:
                transcript, transcription_status = _transcribe_voice_local(Path(temporary.name))
            except business_brain_inbox.InboxCaptureError as exc:
                transcript, transcription_status = None, f"failed: {exc}"
        body = transcript or (payload if payload else "Voice note captured; transcription is unavailable on this host.")
        return business_brain_inbox.capture_attachment(
            data,
            original_filename="voice-note.ogg",
            mime_type=voice.get("mime_type") or "audio/ogg",
            capture_id=capture_id,
            captured_at=stamp,
            body=body,
            content_type="voice",
            metadata={
                **base_metadata,
                "telegram_message_type": "voice",
                "transcription_status": transcription_status,
            },
            root=root,
        )

    if not payload.strip():
        raise business_brain_inbox.InboxCaptureError("use /inbox <text>, attach a supported file, or forward content")
    return business_brain_inbox.capture_text(
        payload,
        source="telegram_bot",
        capture_id=_telegram_capture_id(message, "text"),
        captured_at=stamp,
        content_type="forwarded_text" if forwarded else "text",
        metadata={**base_metadata, "telegram_message_type": "text"},
        root=root,
    )


def format_operator_status(payload, mode="unregistered", chat_mode="david"):
    """Render only bounded operational fields from the backend status contract."""
    data = payload if isinstance(payload, dict) else {}
    bridge = data.get("bridge") if isinstance(data.get("bridge"), dict) else {}
    queue = data.get("queue") if isinstance(data.get("queue"), dict) else {}
    runner = data.get("runner") if isinstance(data.get("runner"), dict) else {}
    codex = data.get("codex") if isinstance(data.get("codex"), dict) else {}
    local_route = data.get("local_agent_route") if isinstance(data.get("local_agent_route"), dict) else {}
    failure = data.get("last_route_failure") if isinstance(data.get("last_route_failure"), dict) else None
    state = "healthy" if data.get("state") == "healthy" and data.get("success") is True else "degraded"
    failure_text = "none recorded"
    if failure:
        failure_class = str(failure.get("failure_class") or "unclassified")[:80]
        stage = str(failure.get("stage") or "unknown stage")[:80]
        failure_text = f"{failure_class} at {stage}"
    return "\n".join((
        "PASS" if state == "healthy" else "NEEDS ATTENTION",
        f"Overall: {state}",
        f"Bridge: live handler; backend_process={str(bridge.get('state') or 'unknown')[:80]}; mode={mode}",
        f"Conversation mode: {chat_mode}",
        "Backend: ready",
        f"Queue: {str(queue.get('state') or 'unknown')[:80]}; items={int(queue.get('items') or 0)}; actionable={int(queue.get('actionable') or 0)}",
        f"Runner: {str(runner.get('state') or 'unknown')[:80]}",
        f"Codex: {str(codex.get('state') or 'unknown')[:80]}",
        f"Local-agent readiness: {str(local_route.get('state') or 'unknown')[:80]}",
        f"Last route failure: {failure_text}",
        "Token usage: no agent invocation",
    ))


def backend_unavailable_status(mode="unregistered", reason="unavailable", chat_mode="david"):
    return "\n".join((
        "NEEDS ATTENTION",
        "Overall: degraded",
        f"Bridge: live handler; backend_process=unknown; mode={mode}",
        f"Conversation mode: {chat_mode}",
        "Backend: unavailable",
        "Queue: unknown",
        "Runner: unknown",
        "Codex: unknown",
        "Local-agent readiness: degraded",
        f"Last route failure: backend_status_{str(reason or 'unavailable')[:80]}",
        "Token usage: no agent invocation",
    ))


# Existing business lane keys accepted by /delegate; these select queue owners.
DELEGATION_LANES = {
    "revenue": "revenue",
    "marketing": "marketing",
    "delivery": "delivery",
    "operations": "operations",
    "ops": "operations",
}

# next_async_item() in the orchestration runner only ever considers items carrying
# this tag. Without it an item is created and then never dispatches.
ASYNC_DISPATCH_TAG = "async_dispatch"
QUEUE_CREATE_ROUTE = "/api/queue/items"
ASK_DAVID_ROUTE = "/api/dashboard/ask-david"
DELEGATE_TIMEOUT_SECONDS = 20

# /chain has to go through David because decomposing the objective is the point.
# Routed as an explicit /work request so the backend conversation-path guard
# ("only an explicit /work request may create or execute work") does not apply.


def post_backend_json(route, payload, timeout=DELEGATE_TIMEOUT_SECONDS):
    """POST one JSON body to the local backend and return the decoded response."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BACKEND}{route}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def format_david_reply(result):
    """Render one ask-david response for Telegram, tagged with the active mode."""
    data = result if isinstance(result, dict) else {}
    kind = str(data.get("kind") or "")
    if kind == "queue_created":
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        item_id = str(item.get("id") or data.get("item_id") or "unavailable")
        owner = str(data.get("owner") or item.get("owner") or "unassigned")
        lines = [f"[David] Queued {item_id} to {owner}."]
        handed = str(data.get("handed_objective") or "").strip()
        if handed:
            lines.append(f"Queued objective: \"{handed}\"")
        else:
            lines.append("Queued objective: (not reported by the backend)")
        note = str(data.get("david_note") or "").strip()
        if note:
            lines.append("")
            lines.append(note)
        lines.append("")
        lines.append("Check progress with /status.")
        return "\n".join(lines)
    body = ""
    for key in ("response", "output", "message", "text", "summary"):
        candidate = str(data.get(key) or "").strip()
        if candidate:
            body = candidate
            break
    if not body:
        body = f"David replied with no text (kind={kind or 'unknown'})."
    return f"[David] {body}"


REPLY_CONTEXT_TEXT_LIMIT = 4096


def reply_context_for(message):
    """Bounded view of the message Casey replied to, or None when it is not a reply.

    Only text, message_id and from_bot cross to the backend (AskDavidRequest.reply_context);
    the raw reply_to_message never does.
    """
    reply = message.get("reply_to_message") if isinstance(message, dict) else None
    if not isinstance(reply, dict) or not reply:
        return None
    message_id = reply.get("message_id")
    sender = reply.get("from") if isinstance(reply.get("from"), dict) else {}
    return {
        "text": str(reply.get("text") or reply.get("caption") or "")[:REPLY_CONTEXT_TEXT_LIMIT],
        "message_id": message_id if isinstance(message_id, int) and not isinstance(message_id, bool) else None,
        "from_bot": sender.get("is_bot") is True,
    }


def _david_delivery_key(chat_id, delivery_id):
    return hashlib.sha256(f"{chat_id}\0{delivery_id}".encode()).hexdigest()


def _david_delivery(chat_id, delivery_id, **changes):
    """Use the existing atomic update receipt store, preserving update deduplication."""
    key = _david_delivery_key(chat_id, delivery_id)
    with _UPDATE_STATE_LOCK:
        state = _load_update_state()
        deliveries = state.setdefault("david_deliveries", {})
        record = deliveries.get(key, {})
        if changes:
            record = {**record, "chat_id": str(chat_id), "delivery_id": delivery_id, **changes}
            deliveries[key] = record
            sent_keys = [k for k, v in deliveries.items() if v.get("status") == "sent"]
            for old_key in sent_keys[:-MAX_RECORDED_UPDATE_IDS]:
                # Retain the deduplication tombstone even when compacting old text.
                deliveries[old_key] = {k: v for k, v in deliveries[old_key].items()
                                       if k in {"chat_id", "delivery_id", "status", "message_id"}}
            _save_update_state(state)
        return dict(record)


def _edit_david_reply(chat_id, message_id, text):
    # Editing a known message is idempotent even after an uncertain network result.
    text = str(text)
    if len(text) > 3400:
        cut = 3380
        for match in _CITATION_RE.finditer(text):
            if match.start() < cut < match.end():
                cut = match.start()
                break
        text = text[:cut] + "\n\n[trimmed]"
    body, entities = david_citation_payload(text)
    payload = {"chat_id": str(chat_id), "message_id": message_id, "text": body}
    if entities:
        payload["entities"] = json.dumps(entities)
    markup = brain_reply_markup(body, entities)
    if markup:
        payload["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    try:
        result = api("editMessageText", payload, timeout=20)
    except urllib.error.HTTPError as exc:
        if exc.code == 400 and b"message is not modified" in exc.read(4096):
            return True
        raise
    return bool(result.get("ok"))


def _voice_reply(chat_id, delivery_id, text, record):
    """One speech attempt per delivery. Telegram has no sendVoice idempotency key."""
    import david_speech

    spoken, needs_text = david_speech.spoken_text(text.removeprefix("[David] "))
    needs_text = needs_text or bool(_CITATION_RE.search(text))
    prior = record.get("voice_status")
    if prior == "sent":
        return record["voice_message_id"], needs_text, ""
    if prior == "sending":
        _david_delivery(chat_id, delivery_id, voice_status="uncertain")
        prior = "uncertain"
    if prior in {"failed", "uncertain"}:
        return None, True, record.get("voice_error") or "Telegram could not confirm the spoken reply."
    if not spoken:
        return None, True, ""
    try:
        with tempfile.TemporaryDirectory(prefix="david-tts-") as directory:
            output = Path(directory) / "david.ogg"
            with _SPEECH_LOCK:
                metrics = david_speech.synthesize(spoken, output)
            if not _private_david_authorized(int(chat_id)):
                return None, True, ""
            _david_delivery(chat_id, delivery_id, voice_status="sending", synthesis=metrics)
            started = time.monotonic()
            try:
                response = _multipart_api("sendVoice", {"chat_id": str(chat_id)}, {"voice": output}, timeout=20)
                sent = response.get("result") if isinstance(response, dict) else None
                message_id = sent.get("message_id") if isinstance(sent, dict) else None
                confirmed = type(message_id) is int and response.get("ok") is True
            except (OSError, ValueError):
                confirmed = False
            if not confirmed:
                _david_delivery(chat_id, delivery_id, voice_status="uncertain")
                return None, True, "Telegram could not confirm the spoken reply."
            _david_delivery(chat_id, delivery_id, voice_status="sent", voice_message_id=message_id,
                            voice_send_seconds=round(time.monotonic() - started, 3))
            return message_id, needs_text, ""
    except (david_speech.SpeechError, OSError) as exc:
        error = str(exc) if isinstance(exc, david_speech.SpeechError) else "Local speech synthesis failed."
        _david_delivery(chat_id, delivery_id, voice_status="failed", voice_error=error)
        return None, True, error


def _private_david_authorized(chat_id):
    return (chat_id in load_allowed().get("operator_chat_ids", [])
            and aos_authority.reminder_authorized(WORKSPACE, f"telegram-{chat_id}", str(chat_id)))


_SPEECH_LOCK = threading.Lock()


def _reply_mode(chat_id, *, audio, state=None):
    if state is None:
        with _UPDATE_STATE_LOCK:
            state = _load_update_state()
    selected = state.get("david_reply_modes", {}).get(str(chat_id), "auto")
    return ("both" if audio else "text") if selected == "auto" else selected


def _reply_preference(chat_id, text):
    parts = text.lower().split()
    if len(parts) == 2 and parts[1] in {"auto", "text", "voice", "both"}:
        with _UPDATE_STATE_LOCK:
            state = _load_update_state()
            state.setdefault("david_reply_modes", {})[str(chat_id)] = parts[1]
            _save_update_state(state)
        send(chat_id, f"[David] Reply preference set to {parts[1]}.", preserve_format=True)
        return
    send(chat_id, "[David] Use /replies auto, /replies text, /replies voice or /replies both. "
         "Auto gives text for typed requests and voice plus text for audio.", preserve_format=True)


def _private_operator(message, chat_id):
    return (message["chat"].get("type") == "private"
            and (message.get("from") or {}).get("id") == chat_id and _private_david_authorized(chat_id))


def _operator_turn(msg, chat_id, text, delivery_id):
    """Make private operator turns durable before Telegram advances the polling offset."""
    if _capture_requested(msg, text) or not _private_operator(msg, chat_id):
        return None
    if type(msg.get("message_id")) is not int:
        return None
    stable_id = f"telegram-message-{chat_id}-{msg['message_id']}"
    context = reply_context_for(msg)
    if msg.get("voice") or msg.get("audio") or msg.get("document"):
        import david_speech

        audio = david_speech.telegram_audio(msg)
        if audio is not None:
            return {"status": "audio_pending", "audio": audio, "reply_context": context,
                    "chat_id": str(chat_id), "delivery_id": stable_id, "source_update": delivery_id}
    if not text or (text.startswith("/") and not text.startswith("/capability ")):
        return None
    task = re.sub(r"^\s*david\s*[,:]\s*", "", text, flags=re.I).strip()
    if not task:
        return None
    if len(task.encode("utf-8")) > 8000:
        return {"status": "ready", "result": {"kind": "david_reply", "success": False,
                "response": "The message exceeds 8,000 UTF-8 bytes. Please split it across messages."},
                "chat_id": str(chat_id), "delivery_id": stable_id, "source_update": delivery_id}
    payload = {"text": task, "conversation_id": f"telegram-{chat_id}", "delivery_id": stable_id}
    if context:
        payload["reply_context"] = context
    return {"status": "prepared", "payload": payload,
            "chat_id": str(chat_id), "delivery_id": stable_id, "source_update": delivery_id}


def _prepare_audio(chat_id, delivery_id, record):
    import david_speech

    audio = record["audio"]
    try:
        if audio.get("error"):
            raise david_speech.SpeechError(audio["error"])
        with _SPEECH_LOCK:
            started = time.monotonic()
            try:
                data = _download_telegram_file(audio["file_id"], max_bytes=david_speech.MAX_BYTES)
            except business_brain_inbox.InboxCaptureError:
                raise david_speech.SpeechError("Telegram did not provide the audio download.") from None
            download_seconds = round(time.monotonic() - started, 3)
            transcript, metrics = david_speech.transcribe(data, audio["suffix"])
        payload = {"text": transcript, "conversation_id": f"telegram-{chat_id}", "delivery_id": delivery_id}
        if record.get("reply_context"):
            payload["reply_context"] = record["reply_context"]
        return _david_delivery(chat_id, delivery_id, status="prepared", audio=None, payload=payload,
                               transcription=metrics, audio_download_seconds=download_seconds)
    except (david_speech.SpeechError, OSError) as exc:
        error = str(exc) if isinstance(exc, david_speech.SpeechError) else "Local transcription storage failed."
        return _david_delivery(chat_id, delivery_id, status="ready", audio=None, reply_mode="text",
                               result={"kind": "david_reply", "success": False,
                                       "response": error + " Please resend the recording or type your request."})


def _finish_david_delivery(chat_id, delivery_id, result):
    record = _david_delivery(chat_id, delivery_id)
    if record.get("status") in {"sent", "uncertain"}:
        return
    if "source_update" in record and not _private_david_authorized(int(chat_id)):
        _david_delivery(chat_id, delivery_id, status="refused", payload=None, result=None)
        return
    text = format_david_reply(result)
    message_id = record.get("message_id")
    reply_mode = record.get("reply_mode")
    if reply_mode in {"voice", "both"} and result.get("success") and _private_david_authorized(int(chat_id)):
        _david_delivery(chat_id, delivery_id, status="ready", result=result)
        voice_id, needs_text, voice_error = _voice_reply(chat_id, delivery_id, text, record)
        if "source_update" in record and not _private_david_authorized(int(chat_id)):
            _david_delivery(chat_id, delivery_id, status="refused", payload=None, result=None)
            return
        if voice_id and reply_mode == "voice" and not needs_text:
            if message_id and not _edit_david_reply(chat_id, message_id, "[David] Voice reply below."):
                raise RuntimeError("Telegram edit failed")
            _record_david_sent(chat_id, delivery_id, voice_id, result)
            return
        if voice_error:
            text = f"{text}\n\n{voice_error}"
    if message_id:
        _david_delivery(chat_id, delivery_id, status="ready", result=result)
        if not _edit_david_reply(chat_id, message_id, text):
            raise RuntimeError("Telegram edit failed")
    else:
        # Telegram sendMessage has no idempotency key. Persist before sending and
        # fail closed on ambiguity; never repeat a potentially delivered send.
        _david_delivery(chat_id, delivery_id, status="uncertain", result=result)
        sent = send(chat_id, text, preserve_format=True)
        if not sent.get("message_sent"):
            return
        message_id = sent.get("message_id")
    _record_david_sent(chat_id, delivery_id, message_id, result)


def _record_david_sent(chat_id, delivery_id, message_id, result):
    record = _david_delivery(chat_id, delivery_id)
    elapsed = time.time() - record.get("received_at", time.time())
    _david_delivery(chat_id, delivery_id, status="sent", message_id=message_id, result=result,
                    elapsed_seconds=round(max(0, elapsed), 3))
    log(f"david_reply_delivered chat={chat_id} kind={result.get('kind')} delivery_id={delivery_id}")


def _run_david_request(chat_id, text, delivery_id, reply_context=None):
    """One submission; resume persisted jobs without replaying the model request."""
    try:
        record = _david_delivery(chat_id, delivery_id)
        if record.get("status") in {"sent", "uncertain", "refused"}:
            return
        if "source_update" in record and not _private_david_authorized(int(chat_id)):
            _david_delivery(chat_id, delivery_id, status="refused", audio=None, payload=None, result=None)
            return
        if record.get("status") == "audio_pending":
            record = _prepare_audio(int(chat_id), delivery_id, record)
            if not _private_david_authorized(int(chat_id)):
                _david_delivery(chat_id, delivery_id, status="refused", audio=None, payload=None, result=None)
                return
        if record.get("status") == "ready":
            _finish_david_delivery(chat_id, delivery_id, record["result"])
            return
        if record.get("status") == "submitting":
            # Recover read-only. Reposting an unknown request could repeat an intake
            # or workstream mutation that completed before the connection failed.
            try:
                delivery_path = urllib.parse.quote(delivery_id, safe="")
                with urllib.request.urlopen(f"{BACKEND}/api/dashboard/ask-david/deliveries/{delivery_path}", timeout=10) as response:
                    recovered = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    _david_delivery(chat_id, delivery_id, status="uncertain")
                    return
                raise
            record = _david_delivery(chat_id, delivery_id, status="pending", job_id=recovered["job_id"])
        if not record or record.get("status") == "prepared":
            payload = record.get("payload") or {"text": text, "conversation_id": f"telegram-{chat_id}", "delivery_id": delivery_id}
            if reply_context:
                payload["reply_context"] = reply_context
            reply_mode = record.get("reply_mode") or _reply_mode(chat_id, audio=False)
            _david_delivery(chat_id, delivery_id, status="submitting", payload=payload,
                            reply_mode=reply_mode, received_at=record.get("received_at", time.time()))
            result = post_backend_json(ASK_DAVID_ROUTE, payload, timeout=DAVID_TIMEOUT_SECONDS)
            if result.get("kind") != "david_job":
                _david_delivery(chat_id, delivery_id, status="ready", result=result)
                _finish_david_delivery(chat_id, delivery_id, result)
                return
            job_id = str(result.get("job_id") or "")
            if not re.fullmatch(r"DJ-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", job_id):
                raise ValueError("invalid David job id")
            if "source_update" in record and not _private_david_authorized(int(chat_id)):
                _david_delivery(chat_id, delivery_id, status="refused", payload=None, result=None)
                return
            _david_delivery(chat_id, delivery_id, status="pending", job_id=job_id)
            # The persisted marker prevents duplicate acknowledgements after restart.
            _david_delivery(chat_id, delivery_id, ack_attempted=True)
            ack = send(chat_id, format_david_reply(result), preserve_format=True)
            if ack.get("message_sent") and isinstance(ack.get("message_id"), int):
                record = _david_delivery(chat_id, delivery_id, message_id=ack["message_id"])
            else:
                record = _david_delivery(chat_id, delivery_id)
        deadline = time.monotonic() + DAVID_JOB_WAIT_SECONDS
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(f"{BACKEND}/api/dashboard/ask-david/jobs/{record['job_id']}")
                with urllib.request.urlopen(request, timeout=10) as response:
                    job = json.loads(response.read().decode())
                status = job.get("status")
                if status == "done":
                    _finish_david_delivery(chat_id, delivery_id, job["result"])
                    return
                if status in {"failed", "interrupted"}:
                    _finish_david_delivery(chat_id, delivery_id, {"kind": "david_reply", "success": False,
                        "response": f"David job {record['job_id']} {status}: {job.get('error') or 'No completed response.'} It was not retried."})
                    return
            except (OSError, ValueError, KeyError, RuntimeError):
                # Safe GET/edit retry; POST/model submission is never repeated.
                log(f"david_job_poll_pending delivery_id={delivery_id}")
            time.sleep(DAVID_JOB_POLL_SECONDS)
    except Exception as exc:
        # Leave a receipt for recovery; never send another untracked message.
        log(f"david_request_pending failure={type(exc).__name__} delivery_id={delivery_id}")
    finally:
        with _AGENT_REQUEST_LOCK:
            _ACTIVE_AGENT_REQUESTS.discard(delivery_id)


def resume_david_requests():
    with _UPDATE_STATE_LOCK:
        records = list(_load_update_state().get("david_deliveries", {}).values())
    for record in records:
        if record.get("status") in {"audio_pending", "prepared", "pending", "ready", "submitting"}:
            dispatch_david_request(record["chat_id"], "", delivery_id=record["delivery_id"])


def dispatch_david_request(chat_id, task, source="telegram", delivery_id="", reply_context=None):
    """Single-flight one David delivery while keeping Telegram polling responsive."""
    request_id = str(delivery_id or "").strip()
    if not request_id:
        digest = hashlib.sha256(f"{chat_id}\0david\0{task}".encode("utf-8")).hexdigest()
        request_id = f"{source}-david-{digest}"
    with _AGENT_REQUEST_LOCK:
        if request_id in _ACTIVE_AGENT_REQUESTS:
            log(f"david_request_inflight_duplicate delivery_id={request_id}")
            return False
        _ACTIVE_AGENT_REQUESTS.add(request_id)
    worker = threading.Thread(
        target=_run_david_request,
        args=(chat_id, task, request_id, reply_context),
        name=f"telegram-david-{request_id[-16:]}",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        with _AGENT_REQUEST_LOCK:
            _ACTIVE_AGENT_REQUESTS.discard(request_id)
        raise
    return True


def _delegation_title(task):
    """First sentence of the request, bounded to the queue title limit."""
    stripped = str(task or "").strip()
    first = re.split(r"(?<=[.!?])\s+", stripped)[0].strip() if stripped else ""
    return (first or stripped)[:150]


def handle_delegate(chat_id, text):
    """Create one queue item owned by a business lane. No model in the loop."""
    parts = text.split(" ", 2)
    usage = "Use: /delegate revenue|marketing|delivery|operations <task>"
    if len(parts) < 3 or not parts[2].strip():
        send(chat_id, usage, preserve_format=True)
        return
    lane = DELEGATION_LANES.get(parts[1].strip().lower())
    if not lane:
        send(chat_id, f"Unknown lane: {parts[1].strip()}\n{usage}", preserve_format=True)
        return

    task = parts[2].strip()
    payload = {
        "title": _delegation_title(task),
        "owner": lane,
        "priority": "normal",
        "tags": ASYNC_DISPATCH_TAG,
        "source": "telegram/delegate",
        "context": task,
        "definition_of_done": (
            "The task described in context is complete and a receipt records what was done."
        ),
    }
    try:
        result = post_backend_json(QUEUE_CREATE_ROUTE, payload)
    except Exception as exc:
        log(f"delegate_failed lane={lane} error={type(exc).__name__}")
        send(
            chat_id,
            f"NEEDS ATTENTION\nDelegation failed: {type(exc).__name__}. No work item was created.",
            preserve_format=True,
        )
        return

    item = result.get("item") if isinstance(result, dict) else None
    item_id = (item or {}).get("id") or "unavailable"
    log(f"delegate_created item={item_id} lane={lane}")
    send(
        chat_id,
        f"Queued {item_id} to {lane}.\nThe runner picks it up within about five seconds.\nCheck progress with /status.",
        preserve_format=True,
    )


def handle_chain(chat_id, text, source="telegram", delivery_id=""):
    """Ask David to decompose an objective and file it as a depends_on sequence."""
    objective = text.split(" ", 1)[1].strip() if " " in text else ""
    if not objective:
        send(chat_id, "Use: /chain <objective to break into steps>", preserve_format=True)
        return
    # Keep planning on the same David entrypoint as ordinary conversation.
    dispatch_david_request(
        chat_id,
        f"Plan this objective as a sequence using the existing Work Queue, then report its job references: {objective}",
        source=source,
        delivery_id=delivery_id,
    )


def handle_operator(chat_id, text, source="telegram", delivery_id="", reply_context=None):
    if text.startswith("/delegate"):
        handle_delegate(chat_id, text)
        return

    if text.startswith("/chain"):
        handle_chain(chat_id, text, source=source, delivery_id=delivery_id)
        return

    if text.startswith("/work "):
        parts = text.split(" ", 2)
        if len(parts) < 3 or parts[1].lower() not in {"codex", "claude"}:
            send(
                chat_id,
                "Use: /work codex|claude <task>\nTo hand work to a business lane use /delegate instead.",
                preserve_format=True,
            )
            return
        target = parts[1].lower()
        task = parts[2].strip()
        dispatch_david_request(
            chat_id,
            f"/work {target} {task}",
            source=source,
            delivery_id=delivery_id,
        )
        return

    if text.startswith("/pilot_add "):
        parts = text.split()
        if len(parts) >= 3:
            data = load_allowed()
            data.setdefault("pilots", {})[parts[1]] = parts[2]
            allowed_file = _allowed_file()
            allowed_file.parent.mkdir(parents=True, exist_ok=True)
            allowed_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            send(chat_id, "Pilot added.", preserve_format=True)
        else:
            send(chat_id, "Use: /pilot_add <chat_id> <pilot_id>", preserve_format=True)
        return

    if text.startswith("/"):
        send(
            chat_id,
            "Commands:\n"
            "/status\n"
            "/inbox <text>\n"
            "/delegate <lane> <task>  - hand work to revenue, marketing, delivery, operations\n"
            "/chain <objective>       - David breaks it into steps and runs them in order\n"
            "/work codex|claude <task>\n"
            "/pilot_add <chat_id> <pilot_id>",
            preserve_format=True,
        )
        return

    # Addressing David is optional; there is only one operator conversation path.
    remainder = re.sub(r"^\s*david\s*[,:]\s*", "", text, flags=re.I).strip()
    if not remainder:
        send(chat_id, "[David] I'm here.", preserve_format=True)
        return
    dispatch_david_request(chat_id, remainder, source=source, delivery_id=delivery_id,
                           reply_context=reply_context)


def handle_message(msg, source="telegram", delivery_id="", update_id=None, edited=False):
    chat = msg.get("chat") or {}
    chat_id = int(chat.get("id"))
    text = (msg.get("text") or msg.get("caption") or "").strip()

    allowed = load_allowed()
    is_operator = chat_id in set(allowed.get("operator_chat_ids", []))
    pilot_id = allowed.get("pilots", {}).get(str(chat_id))
    # An edited operator message can describe a different action under the same identity.
    # Accept operator corrections as new messages.
    record = _operator_turn(msg, chat_id, text, delivery_id) if is_operator and not edited else None
    if update_id is not None and not claim_update(update_id, record=record):
        log(f"duplicate_update update_id={update_id}")
        return
    if edited and is_operator:
        return
    if record:
        if update_id is None:
            with _UPDATE_STATE_LOCK:
                state = _load_update_state()
                _stage_operator_turn(state, record)
                _save_update_state(state)
        dispatch_david_request(chat_id, "", delivery_id=record["delivery_id"])
        return
    if is_operator and text.split(maxsplit=1)[:1] == ["/replies"] and _private_operator(msg, chat_id):
        _reply_preference(chat_id, text)
        return

    if text.startswith("/status"):
        mode = "operator" if is_operator else ("pilot" if pilot_id else "unregistered")
        chat_mode = "david" if is_operator else "report-only"
        try:
            status = format_operator_status(get_backend_status(), mode=mode, chat_mode=chat_mode)
        except Exception as exc:
            status = backend_unavailable_status(
                mode=mode,
                reason=type(exc).__name__,
                chat_mode=chat_mode,
            )
        send(chat_id, status, preserve_format=True, api_timeout=STATUS_SEND_TIMEOUT_SECONDS)
        return

    if text.startswith("/whoami"):
        send(chat_id, f"chat_id={chat_id}", api_timeout=STATUS_SEND_TIMEOUT_SECONDS)
        return

    if is_operator and _capture_requested(msg, text):
        try:
            capture = capture_operator_message(msg)
            acknowledgement = "Already captured ✓" if capture.duplicate else "Captured ✓"
        except business_brain_inbox.InboxCaptureError as exc:
            acknowledgement = f"Capture failed: {exc}"
        send(chat_id, acknowledgement, preserve_format=True)
        return

    if not text:
        return

    if is_operator:
        handle_operator(
            chat_id,
            text,
            source=source,
            delivery_id=delivery_id,
            reply_context=reply_context_for(msg),
        )
        return

    if pilot_id == PILOT_ID:
        if text.startswith("/report "):
            save_report(chat_id, text[len("/report "):], source="slash_report")
            send(chat_id, "Report saved.")
            return
        if text.startswith("/"):
            send(chat_id, "Send a normal sales update, or use /report <text>.")
            return
        save_report(chat_id, text, source="natural_language")
        send(chat_id, "Report saved.")
        return

    if text.startswith("/"):
        send(chat_id, f"Unregistered chat. Send /whoami to get chat_id={chat_id}")


def _load_update_state():
    try:
        payload = json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("invalid Telegram update state; refusing replay")
    return payload


def _save_update_state(state):
    UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=".telegram_bridge_updates.",
            suffix=".tmp", dir=UPDATE_STATE_FILE.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(state, handle, separators=(",", ":"))
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, UPDATE_STATE_FILE)
    directory_fd = os.open(UPDATE_STATE_FILE.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_recorded_update_ids():
    payload = _load_update_state()
    return sorted({value for value in payload.get("processed_update_ids", [])
                   if isinstance(value, int) and not isinstance(value, bool) and value >= 0})


def _stage_operator_turn(state, record):
    deliveries = state.setdefault("david_deliveries", {})
    key = _david_delivery_key(record["chat_id"], record["delivery_id"])
    if key not in deliveries:
        audio = record["status"] == "audio_pending"
        deliveries[key] = {**record, "received_at": time.time(),
                           "reply_mode": _reply_mode(record["chat_id"], audio=audio, state=state)}


def claim_update(update_id, *, record=None):
    """Durably claim an update, preserving pending David deliveries across restarts."""
    value = int(update_id)
    with _UPDATE_STATE_LOCK:
        state = _load_update_state()
        recorded = _load_recorded_update_ids()
        if value in recorded:
            return False
        if record:
            _stage_operator_turn(state, record)
        state["processed_update_ids"] = sorted([*recorded, value])[-MAX_RECORDED_UPDATE_IDS:]
        _save_update_state(state)
    return True


def acquire_bridge_singleton():
    """Hold one canonical Linux bridge lock without killing unrelated processes."""
    global _BRIDGE_LOCK_HANDLE
    BRIDGE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = BRIDGE_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    os.fsync(handle.fileno())
    _BRIDGE_LOCK_HANDLE = handle
    return True


def main():
    if not acquire_bridge_singleton():
        print("PASS bridge_already_running", flush=True)
        return
    recorded = _load_recorded_update_ids()
    offset = (recorded[-1] + 1) if recorded else 0
    me = api("getMe", {}, timeout=20).get("result", {})
    allowed = load_allowed().get("operator_chat_ids", [])
    print(f"PASS bridge_live bot=@{me.get('username')} operator_configured={bool(allowed)}", flush=True)
    with _UPDATE_STATE_LOCK:
        state = _load_update_state()
        state["bridge_runtime"] = {
            "pid": os.getpid(), "bot": me.get("username"),
            "operator_ready": any(_private_david_authorized(int(chat_id)) for chat_id in allowed),
            "bridge_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "speech_sha256": hashlib.sha256((TOOLS_DIR / "david_speech.py").read_bytes()).hexdigest(),
        }
        _save_update_state(state)
    log("bridge_live")
    resume_david_requests()
    while True:
        try:
            resume_david_requests()
            res = api("getUpdates", {"timeout": "45", "offset": str(offset),
                        "allowed_updates": json.dumps(["message", "edited_message", "callback_query"])}, timeout=60)
            for update in res.get("result", []):
                update_id = int(update.get("update_id", 0))
                if update.get("callback_query"):
                    if claim_update(update_id):
                        handle_brain_callback(update["callback_query"])
                else:
                    msg = update.get("message") or update.get("edited_message")
                    if msg:
                        handle_message(msg, delivery_id=f"telegram-update-{update_id}", update_id=update_id,
                                       edited=bool(update.get("edited_message")))
                offset = max(offset, update_id + 1)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"loop_error {type(e).__name__}")
            time.sleep(3)


if __name__ == "__main__":
    main()
