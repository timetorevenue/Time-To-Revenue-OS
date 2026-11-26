"""Captured communications metadata: storage and the read projections search and Graphify use.

The independent Gmail poller reuses this storage. No proposal queue, job allocator
or model execution lives here. Raw evidence is never projected; only
CaptureMetadataProjection may enter search or Graphify.

Revisit: when the capture metadata contract changes. · Last touched: 2026-11-15.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

try:
    from aos_paths import aos_root, assert_authoritative_root
    from aos_queue_storage import durable_replace_text, fsync_directory
    from business_brain_scope import ClientScopeError, ClientScopeRegistry
except ModuleNotFoundError:  # package import in unittest/IDE contexts
    from tools.aos_paths import aos_root, assert_authoritative_root
    from tools.aos_queue_storage import durable_replace_text, fsync_directory
    from tools.business_brain_scope import ClientScopeError, ClientScopeRegistry


REPO_ROOT = aos_root()
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "capture" / "runtime"
TOKEN_USAGE_TEXT = "Token usage: no agent invocation"
SAFE_HEADER_KEYS = {"list_id_present", "auto_submitted", "precedence", "category", "ambiguous_candidate", "is_reply"}
METADATA_FIELDS = {
    "record_id",
    "reference_path",
    "client_scope",
    "linked_item_id",
    "subject_classification",
    "timestamp",
    "source_type",
    "triage_state",
    "proposal_state",
}


class CaptureError(RuntimeError):
    pass


class CaptureStorageError(CaptureError):
    pass


class LiveCaptureDisabled(CaptureError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "--", value.strip().lower()).strip("-")
    if not slug:
        raise CaptureStorageError("scope/provider key cannot be empty")
    return slug


def _history_sort_key(record: "CaptureEnvelope") -> tuple[int, str, str]:
    raw = str(record.history_id)
    return (int(raw) if raw.isdigit() else 2**63 - 1, raw, record.message_id)


@dataclass(frozen=True)
class CaptureEnvelope:
    provider: str
    message_id: str
    thread_id: str
    history_id: str
    timestamp: str
    sender: str
    subject: str
    source_type: str = "gmail"
    linked_item_id: str = ""
    headers: dict[str, Any] | None = None

    def validate(self) -> "CaptureEnvelope":
        if self.provider not in {"gmail_fixture", "gmail_composio"}:
            raise CaptureError("unsupported capture provider")
        for field_name in ("message_id", "thread_id", "history_id", "timestamp", "sender"):
            if not str(getattr(self, field_name) or "").strip():
                raise CaptureError(f"capture envelope requires {field_name}")
        headers = self.headers or {}
        unknown = set(headers) - SAFE_HEADER_KEYS
        if unknown:
            raise CaptureError(f"capture envelope contains unapproved header fields: {', '.join(sorted(unknown))}")
        return self

    @property
    def deduplication_key(self) -> str:
        return stable_hash(self.provider, self.message_id)

    @property
    def record_id(self) -> str:
        return f"cap-{self.deduplication_key[:24]}"


@dataclass(frozen=True)
class DeltaBatch:
    next_cursor: str
    envelopes: tuple[CaptureEnvelope, ...]


class DeltaAdapter(Protocol):
    provider_key: str
    live: bool

    def read_delta(self, cursor: str | None) -> DeltaBatch: ...


@dataclass(frozen=True)
class CaptureMetadataProjection:
    record_id: str
    reference_path: str
    client_scope: str
    linked_item_id: str
    subject_classification: str
    timestamp: str
    source_type: str
    triage_state: str
    proposal_state: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CaptureMetadataProjection":
        if set(value) != METADATA_FIELDS:
            extra = sorted(set(value) - METADATA_FIELDS)
            missing = sorted(METADATA_FIELDS - set(value))
            raise CaptureError(f"capture projection fields are not allowlisted; extra={extra}, missing={missing}")
        row = cls(**{key: str(value[key] or "") for key in METADATA_FIELDS})
        if not row.record_id.startswith("cap-"):
            raise CaptureError("capture projection record_id is malformed")
        if not row.reference_path.startswith(f"capture:{row.client_scope}:"):
            raise CaptureError("capture projection reference is not scope-bound")
        if row.source_type not in {"gmail", "gmail_fixture"}:
            raise CaptureError("capture projection source_type is unsupported")
        return row

    def to_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in sorted(METADATA_FIELDS)}


class CaptureStorage:
    """One permission-restricted, append-only capture runtime."""

    def __init__(self, root: Path = DEFAULT_RUNTIME_ROOT, *, repo_root: Path = REPO_ROOT):
        self.root = Path(root).resolve()
        self.repo_root = Path(repo_root).resolve()
        assert_authoritative_root(self.root.parent)
        try:
            self.root.relative_to(self.repo_root)
        except ValueError as exc:
            raise CaptureStorageError("capture runtime must stay inside the authoritative Linux repository") from exc
        self._ensure_directory(self.root)
        for child in ("control", "providers", "scopes"):
            self._ensure_directory(self.root / child)
        if not self.control_path.exists():
            self._replace_json(self.control_path, {"live_capture_enabled": False, "kill_switch": False})
        self.enforce_permissions()

    @property
    def control_path(self) -> Path:
        return self.root / "control" / "state.json"

    def _ensure_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)

    def _ensure_parent(self, path: Path) -> None:
        current = path.parent
        missing = []
        while current != self.root.parent and not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            self._ensure_directory(directory)
        for directory in [path.parent, *path.parent.parents]:
            if directory == self.root.parent:
                break
            if directory.exists():
                os.chmod(directory, 0o700)

    def _replace_json(self, path: Path, value: dict[str, Any]) -> None:
        self._ensure_parent(path)
        durable_replace_text(path, json.dumps(value, sort_keys=True) + "\n")
        os.chmod(path, 0o600)

    def _append_json(self, path: Path, value: dict[str, Any]) -> None:
        self._ensure_parent(path)
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        existed = path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            fsync_directory(path.parent)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaptureStorageError(f"invalid capture JSONL at {path.name}:{line_number}") from exc
            if not isinstance(value, dict):
                raise CaptureStorageError("capture JSONL record must be an object")
            rows.append(value)
        return rows

    def control_state(self) -> dict[str, bool]:
        try:
            value = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureStorageError("capture control state is unavailable") from exc
        if set(value) != {"live_capture_enabled", "kill_switch"} or not all(isinstance(value[key], bool) for key in value):
            raise CaptureStorageError("capture control state is malformed")
        return value

    def set_control_for_fixture(self, *, kill_switch: bool) -> None:
        """Local-only test control; never creates live activation authority."""
        self._replace_json(self.control_path, {"live_capture_enabled": False, "kill_switch": bool(kill_switch)})

    def set_control(self, *, live_capture_enabled: bool, kill_switch: bool) -> None:
        """Publish the two-field production control atomically."""
        self._replace_json(self.control_path, {
            "live_capture_enabled": bool(live_capture_enabled),
            "kill_switch": bool(kill_switch),
        })

    def provider_dir(self, provider_key: str) -> Path:
        path = self.root / "providers" / _safe_slug(provider_key)
        self._ensure_directory(path)
        return path

    def scope_dir(self, client_scope: str | None) -> Path:
        scope = client_scope or "_unresolved"
        path = self.root / "scopes" / _safe_slug(scope)
        for child in (path, path / "raw", path / "derived"):
            self._ensure_directory(child)
        return path

    def cursor(self, provider_key: str) -> str | None:
        path = self.provider_dir(provider_key) / "cursor.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureStorageError("capture cursor is invalid") from exc
        cursor = str(value.get("cursor") or "")
        return cursor or None

    def publish_cursor(self, provider_key: str, cursor: str, *, failure_injection: str | None = None) -> None:
        path = self.provider_dir(provider_key) / "cursor.json"
        self._ensure_parent(path)
        candidate = path.with_name(f".{path.name}.{uuid.uuid4().hex}.candidate")
        try:
            with candidate.open("w", encoding="utf-8") as handle:
                json.dump({"cursor": str(cursor), "updated_at": utc_now()}, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fchmod(handle.fileno(), 0o600)
                os.fsync(handle.fileno())
            if failure_injection == "during_cursor_publication":
                raise CaptureStorageError("injected cursor publication failure")
            os.replace(candidate, path)
            fsync_directory(path.parent)
            os.chmod(path, 0o600)
        finally:
            candidate.unlink(missing_ok=True)

    def processing_rows(self, provider_key: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self.provider_dir(provider_key) / "processing.jsonl")

    def append_processing(self, provider_key: str, value: dict[str, Any]) -> None:
        self._append_json(self.provider_dir(provider_key) / "processing.jsonl", value)

    def raw_records(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted((self.root / "scopes").glob("*/raw/records.jsonl")):
            rows.extend(self._read_jsonl(path))
        return rows

    def raw_record(self, record_id: str) -> dict[str, Any]:
        matches = [row for row in self.raw_records() if row.get("record_id") == record_id]
        if len(matches) != 1:
            raise CaptureStorageError("capture raw record is missing or duplicated")
        return matches[0]

    def append_raw(self, client_scope: str | None, value: dict[str, Any]) -> bool:
        record_id = str(value.get("record_id") or "")
        if any(row.get("record_id") == record_id for row in self.raw_records()):
            return False
        self._append_json(self.scope_dir(client_scope) / "raw" / "records.jsonl", value)
        return True

    def append_fixture_evidence(
        self,
        *,
        client_scope: str,
        record_id: str,
        body: str,
        thread_text: str,
        attachments: list[str],
    ) -> str:
        raw = self.raw_record(record_id)
        if raw.get("client_scope") != client_scope or raw.get("scope_state") != "matched":
            raise ClientScopeError("fixture evidence scope does not match the immutable raw record")
        path = self.scope_dir(client_scope) / "raw" / "evidence.jsonl"
        if any(row.get("record_id") == record_id for row in self._read_jsonl(path)):
            return str(raw["evidence_reference"])
        self._append_json(path, {
            "record_id": record_id,
            "body": str(body),
            "thread_text": str(thread_text),
            "attachments": [str(value) for value in attachments],
            "fixture_only": True,
        })
        return str(raw["evidence_reference"])

    def load_fixture_evidence(
        self,
        *,
        client_scope: str,
        reference: str,
        record_id: str,
        on_open: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        raw = self.raw_record(record_id)
        if raw.get("client_scope") != client_scope or raw.get("evidence_reference") != reference:
            raise ClientScopeError("capture evidence reference is cross-scope or invalid")
        if on_open:
            on_open("evidence", client_scope)
        path = self.scope_dir(client_scope) / "raw" / "evidence.jsonl"
        matches = [row for row in self._read_jsonl(path) if row.get("record_id") == record_id]
        if len(matches) != 1:
            raise CaptureStorageError("fixture evidence is missing or duplicated")
        return matches[0]

    def append_derived(self, client_scope: str | None, kind: str, value: dict[str, Any]) -> bool:
        if kind not in {"triage", "proposals", "metadata", "operational_receipts"}:
            raise CaptureStorageError("unknown capture derived record class")
        path = self.scope_dir(client_scope) / "derived" / f"{kind}.jsonl"
        if value in self._read_jsonl(path):
            return False
        self._append_json(path, value)
        return True

    def metadata_rows(self) -> list[CaptureMetadataProjection]:
        latest: dict[str, CaptureMetadataProjection] = {}
        for path in sorted((self.root / "scopes").glob("*/derived/metadata.jsonl")):
            for value in self._read_jsonl(path):
                row = CaptureMetadataProjection.from_mapping(value)
                latest[row.reference_path] = row
        return [latest[key] for key in sorted(latest)]

    def enforce_permissions(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            os.chmod(dirpath, 0o700)
            for name in dirnames:
                os.chmod(Path(dirpath) / name, 0o700)
            for name in filenames:
                os.chmod(Path(dirpath) / name, 0o600)

    def permission_report(self) -> dict[str, Any]:
        wrong = []
        for dirpath, _dirnames, filenames in os.walk(self.root):
            if (os.stat(dirpath).st_mode & 0o777) != 0o700:
                wrong.append(str(Path(dirpath)))
            for name in filenames:
                path = Path(dirpath) / name
                if (os.stat(path).st_mode & 0o777) != 0o600:
                    wrong.append(str(path))
        return {"directories": "0700", "files": "0600", "wrong": wrong, "ok": not wrong}


class FixtureDeltaAdapter:
    live = False

    def __init__(self, batch: DeltaBatch, *, provider_key: str = "gmail-fixture"):
        self.batch = batch
        self.provider_key = provider_key
        self.activity_count = 0

    def read_delta(self, cursor: str | None) -> DeltaBatch:
        self.activity_count += 1
        return self.batch


def capture_document(row: CaptureMetadataProjection, *, registry: ClientScopeRegistry) -> dict[str, Any]:
    registry.validate_search_source(row.client_scope, "capture_metadata")
    resolved = registry.scope_for_search_identity("capture_metadata", row.reference_path)
    if resolved != row.client_scope:
        raise ClientScopeError("capture projection search identity is not uniquely scoped")
    generated = f"Capture evidence {row.subject_classification} {row.triage_state} {row.proposal_state}"
    return {
        "path": row.reference_path,
        "title": generated,
        "kind": "capture_evidence",
        "source": "capture_metadata",
        "source_root": "capture/runtime/derived/metadata",
        "client_scope": row.client_scope,
        "mtime": dt.datetime.fromisoformat(row.timestamp.replace("Z", "+00:00")).timestamp(),
        "tags": ",".join(filter(None, ("capture_metadata", row.triage_state, row.proposal_state))),
        "snippet": generated,
        "body": "",
        "indexed_at": utc_now(),
        "size_bytes": 0,
    }


def load_capture_metadata(root: Path = DEFAULT_RUNTIME_ROOT) -> list[CaptureMetadataProjection]:
    root = Path(root)
    if not root.exists():
        return []
    return CaptureStorage(root, repo_root=root.resolve().parents[1]).metadata_rows()
