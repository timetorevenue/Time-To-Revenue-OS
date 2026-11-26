"""Deterministic local SQLite FTS index for Agentic OS.

Revisit: when indexed source scopes or public status metadata change. · Last touched: 2026-08-01.
"""

from __future__ import annotations

import argparse
import hashlib
import datetime as dt
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    from aos_paths import aos_root, assert_authoritative_root, resolve_root_relative
    from aos_queue_storage import durable_append_text, durable_replace_text, fsync_directory, queue_write_lock
    from business_brain import BUSINESS_BRAIN_ROOT, business_brain_pointer_for_path
    from business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry
except ModuleNotFoundError:  # package import in unittest/IDE contexts
    from tools.aos_paths import aos_root, assert_authoritative_root, resolve_root_relative
    from tools.aos_queue_storage import durable_append_text, durable_replace_text, fsync_directory, queue_write_lock
    from tools.business_brain import BUSINESS_BRAIN_ROOT, business_brain_pointer_for_path
    from tools.business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry


TOKEN_USAGE_TEXT = "Token usage: no agent invocation"
LIVE_ROOT = aos_root()
DB_PATH = LIVE_ROOT / "search" / "os_index.db"
INGEST_CONFIG_PATH = LIVE_ROOT / "queue" / "ingest_watch.json"
INGEST_RECEIPT_PATH = LIVE_ROOT / "queue" / "receipts" / "ingestion.jsonl"
CLIENT_SCOPE_REGISTRY_PATH = LIVE_ROOT / "context" / "client_scope_registry.json"
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".jsonl"}
FILENAME_ONLY_EXTENSIONS = {".pdf", ".docx", ".xlsx"}
INDEXABLE_EXTENSIONS = TEXT_EXTENSIONS | FILENAME_ONLY_EXTENSIONS
MAX_TEXT_BYTES = 750_000

PROTECTED_SEGMENTS = {
    ".git",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "cache",
    "example_dealership_sales_coach",
    "_backups",
}
PROTECTED_PATH_PARTS = (
    "connectors/telegram_bridge",
    "workspaces/example_dealership_sales_coach",
    "queue/command_routes.json",
    "queue/model_routes.json",
    "capture/runtime",
    "capture/gmail",
    "queue/draft_runtime",
    "inbox/source_notes",
)
LEGACY_RE = re.compile(r"(old[_\s-]*(ubuntu|hermes|vault|runtime)|legacy|legacy_harvest|old[_\s-]*zpc|\bzpc\b)", re.I)
SECRET_PATH_RE = re.compile(r"(^|[/.])(\.env($|\.)|.*secret.*|.*token.*|.*credential.*|.*password.*)", re.I)
SENSITIVE_LINE_RE = re.compile(
    r"(api[_-]?key|secret|token|credential|password|authorization|bearer|private[_-]?key|north[_\s-]*shore)",
    re.I,
)
SECRET_CONTENT_RE = re.compile(r"(api[_-]?key|authorization|bearer|password|private[_-]?key|credential)", re.I)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_path_text(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def source_for_path(path: Path) -> tuple[str, Path]:
    resolved = path.resolve()
    try:
        resolved.relative_to(LIVE_ROOT.resolve())
        return "agentic_os_live", LIVE_ROOT
    except ValueError:
        pass
    try:
        resolved.relative_to(BUSINESS_BRAIN_ROOT.resolve())
        return "business_brain", BUSINESS_BRAIN_ROOT
    except ValueError:
        return "external", resolved.parent


def is_excluded(path: Path | str, *, root: Path | None = None) -> bool:
    path_obj = Path(path)
    text = normalize_path_text(path_obj)
    lowered = text.lower()
    parts = [part.lower() for part in path_obj.parts]
    if any(part in PROTECTED_SEGMENTS for part in parts):
        return True
    if any(part.startswith(".venv") for part in parts):
        return True
    if "example_dealership" in lowered or "example dealership" in lowered:
        return True
    if SECRET_PATH_RE.search(lowered):
        return True
    if LEGACY_RE.search(lowered):
        return True
    check_text = lowered
    if root is not None:
        try:
            check_text = path_obj.resolve().relative_to(root.resolve()).as_posix().lower()
        except ValueError:
            check_text = lowered
    return any(part in check_text for part in PROTECTED_PATH_PARTS)


def ensure_default_config() -> dict:
    if INGEST_CONFIG_PATH.exists():
        return json.loads(INGEST_CONFIG_PATH.read_text(encoding="utf-8"))
    assert_authoritative_root(LIVE_ROOT)
    config = {
        "inboxes": ["queue/inbox"],
        "watch_roots": ["queue/inbox", "results", "workflows", "queue/receipts"],
        "business_brain_read_index_only": True,
        "token_usage_text": TOKEN_USAGE_TEXT,
    }
    INGEST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    INGEST_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (LIVE_ROOT / "queue" / "inbox").mkdir(parents=True, exist_ok=True)
    return config


def runtime_db_path(db_path: Path | None = None) -> Path:
    return db_path or DB_PATH


def connect(db_path: Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    db_path = runtime_db_path(db_path)
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    assert_authoritative_root(db_path.parent)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            source_root TEXT NOT NULL,
            mtime REAL NOT NULL,
            tags TEXT NOT NULL,
            snippet TEXT NOT NULL,
            body TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "client_scope" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN client_scope TEXT NOT NULL DEFAULT ''")
    if "content_sha256" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN content_sha256 TEXT NOT NULL DEFAULT ''")
    if "participants" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN participants TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
        USING fts5(path, title, kind, source, tags, snippet, body, content='documents', content_rowid='id')
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_client_scope ON documents(client_scope)")
    conn.commit()
    return conn


def reset_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM documents_fts")
    conn.execute("DELETE FROM documents")
    conn.commit()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip().strip("'\"")
    return data, text[match.end() :]


# The preserved original bytes of an intake record or Drive provenance node (tools/source_intake.py). They stay in
# the file as evidence; the derived searchable text, snippet, embeddings and passages never include the base64.
# (Literal markers: source_intake imports this module.)
SOURCE_BYTES_RE = re.compile(r"<!-- TTROS:SOURCE-BYTES:BEGIN -->.*?<!-- TTROS:SOURCE-BYTES:END -->", re.DOTALL)
SOURCE_BYTES_PLACEHOLDER = "[Exact source bytes preserved in the file; excluded from search text.]"


def strip_source_bytes(text: str) -> str:
    return SOURCE_BYTES_RE.sub(SOURCE_BYTES_PLACEHOLDER, text)


def sanitize_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if SENSITIVE_LINE_RE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:180] or fallback
        if stripped:
            return stripped[:180]
    return fallback


def tags_from_frontmatter(frontmatter: dict[str, str]) -> list[str]:
    raw = frontmatter.get("tags") or frontmatter.get("tag") or ""
    raw = raw.strip("[]")
    return [tag.strip(" '\"") for tag in raw.split(",") if tag.strip(" '\"")]


def kind_for_path(path: Path, source: str) -> str:
    rel = relative_to_root(path, LIVE_ROOT).lower() if source == "agentic_os_live" else path.name.lower()
    if source == "business_brain":
        return "memory"
    if rel.startswith("queue/receipts/"):
        return "receipt"
    if rel.startswith("queue/") and path.suffix.lower() == ".jsonl":
        return "queue_item"
    if rel.startswith("workflows/") or rel.startswith("skills/"):
        return "workflow"
    if rel.startswith("memory_index/") or rel.startswith("context/") or rel.startswith("decisions/"):
        return "memory"
    if rel.startswith("results/") or rel.startswith("logs/") or rel.startswith("packets/") or "artifact" in rel:
        return "artifact"
    return "file"


def document_from_path(path: Path, *, registry: ClientScopeRegistry | None = None) -> dict | None:
    if not path.is_file() or is_excluded(path):
        return None
    suffix = path.suffix.lower()
    if suffix not in INDEXABLE_EXTENSIONS:
        return None
    source, root = source_for_path(path)
    if source == "external":
        return None
    if is_excluded(path, root=root):
        return None
    pointer = business_brain_pointer_for_path(path, root=BUSINESS_BRAIN_ROOT) if source == "business_brain" else f"{source}:{relative_to_root(path, root)}"
    gate = registry or load_registry(CLIENT_SCOPE_REGISTRY_PATH)
    client_scope = gate.scope_for_search_identity(source, pointer)
    if client_scope is None:
        return None
    stat = path.stat()
    rel = relative_to_root(path, root)
    body = ""
    tags: list[str] = []
    participants = ""
    content_sha256 = ""
    if suffix in TEXT_EXTENSIONS:
        raw_bytes = path.read_bytes()
        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        raw = raw_bytes[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        frontmatter, content = parse_frontmatter(raw)
        tags = tags_from_frontmatter(frontmatter)
        participants = frontmatter.get("participants_text") or ""
        body = sanitize_text(strip_source_bytes(content))
        title = frontmatter.get("title") or frontmatter.get("name") or title_from_text(body, path.stem.replace("_", " ").title())
    else:
        title = path.stem.replace("_", " ").title()
        body = ""
    snippet = " ".join(body.split())[:600] if body else f"{path.suffix.lower()[1:].upper()} file indexed by filename only."
    return {
        "path": pointer,
        "title": title,
        "kind": kind_for_path(path, source),
        "source": source,
        "source_root": str(root),
        "client_scope": client_scope,
        "mtime": stat.st_mtime,
        "tags": ",".join(tags),
        "snippet": snippet,
        "body": body,
        "indexed_at": utc_now(),
        "size_bytes": stat.st_size,
        "participants": participants,
        "content_sha256": content_sha256,
    }


def upsert_document(conn: sqlite3.Connection, doc: dict) -> bool:
    existing = conn.execute("SELECT id FROM documents WHERE path = ?", (doc["path"],)).fetchone()
    values = (
        doc["path"],
        doc["title"],
        doc["kind"],
        doc["source"],
        doc["source_root"],
        doc["client_scope"],
        doc["mtime"],
        doc["tags"],
        doc["snippet"],
        doc["body"],
        doc["indexed_at"],
        doc["size_bytes"],
        doc.get("participants") or "",
        doc.get("content_sha256") or "",
    )
    if existing:
        rowid = existing["id"]
        conn.execute(
            """
            UPDATE documents
            SET path=?, title=?, kind=?, source=?, source_root=?, client_scope=?, mtime=?, tags=?, snippet=?, body=?, indexed_at=?, size_bytes=?, participants=?, content_sha256=?
            WHERE id=?
            """,
            (*values, rowid),
        )
        conn.execute("DELETE FROM documents_fts WHERE rowid=?", (rowid,))
    else:
        cur = conn.execute(
            """
            INSERT INTO documents(path, title, kind, source, source_root, client_scope, mtime, tags, snippet, body, indexed_at, size_bytes, participants, content_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        rowid = cur.lastrowid
    conn.execute(
        """
        INSERT INTO documents_fts(rowid, path, title, kind, source, tags, snippet, body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rowid, doc["path"], doc["title"], doc["kind"], doc["source"], doc["tags"], doc["snippet"], doc["body"]),
    )
    return True


INTAKE_RECORD_POINTER_RE = re.compile(r"\Abusiness_brain:sources/intake/records/([0-9a-f]{64})\.md\Z")
# A passage row is the record pointer plus #L<a>-L<b> (line window) or #C<start>-<end> (code-point offsets of a
# long line's segments; tools/source_passages.py documents the convention).
PASSAGE_POINTER_RE = re.compile(r"\Abusiness_brain:sources/intake/records/([0-9a-f]{64})\.md#(?:L(\d+)-L(\d+)|C(\d+)-(\d+))\Z")
PASSAGE_GLOB = "*.md#[LC][0-9]*"
PASSAGE_EXCLUDE_SQL = f"d.path NOT GLOB '{PASSAGE_GLOB}'"


def is_passage_pointer(pointer: str) -> bool:
    return bool(PASSAGE_POINTER_RE.match(str(pointer)))


def passage_source_ref(pointer: str) -> str | None:
    """The stable ``source:<sha>#L<a>-L<b>`` or ``source:<sha>#C<start>-<end>`` reference of a passage row's pointer."""
    match = PASSAGE_POINTER_RE.match(str(pointer))
    if not match:
        return None
    if match.group(2):
        return f"source:{match.group(1)}#L{match.group(2)}-L{match.group(3)}"
    return f"source:{match.group(1)}#C{match.group(4)}-{match.group(5)}"


def passage_documents(record_doc: dict) -> list[dict]:
    """Search rows for the exact-byte-verified passages of one preserved intake original.

    The preserved bytes are re-hashed against the record's SHA-256 first (tools/source_passages.load_source);
    a record that does not verify yields no passages. Each row inherits the record's client scope, so it is
    visible exactly where the record is. The pointer is the record pointer plus ``#L<a>-L<b>``, or ``#C<start>-<end>``
    for the segment rows of a line too long to be one row (tools/source_passages.passage_windows).
    """
    match = INTAKE_RECORD_POINTER_RE.match(record_doc["path"])
    if not match:
        return []
    try:
        from source_passages import PassageError, load_text, passage_windows, split_lines
    except ModuleNotFoundError:
        from tools.source_passages import PassageError, load_text, passage_windows, split_lines
    try:
        sha, source_text = load_text(BUSINESS_BRAIN_ROOT, match.group(1))
    except PassageError:
        return []
    lines = split_lines(source_text.encode("utf-8"))
    heading_at = {number: line.lstrip("#").strip() for number, line in enumerate(lines, 1) if re.match(r"\s{0,3}#{1,6}\s+\S", line)}
    docs = []
    for label, start, end, text in passage_windows(source_text):
        heading = next((heading_at[n] for n in range(start, 0, -1) if n in heading_at), "")
        body = sanitize_text(text)
        if not body.strip():
            continue
        title = " · ".join(part for part in (record_doc["title"][:140], heading[:100], label) if part)
        docs.append({
            **record_doc,
            "path": f"{record_doc['path']}#{label}",
            "title": title,
            "tags": "source_passage",
            "snippet": " ".join(body.split())[:600],
            "body": body,
            "size_bytes": len(text.encode("utf-8")),
        })
    return docs


def delete_passages(conn: sqlite3.Connection, record_pointer: str) -> int:
    rows = [row["id"] for row in conn.execute(
        "SELECT id FROM documents WHERE path GLOB ?", (record_pointer.replace("[", "[[]") + "#[LC][0-9]*",))]
    for rowid in rows:
        conn.execute("DELETE FROM documents_fts WHERE rowid=?", (rowid,))
        conn.execute("DELETE FROM documents WHERE id=?", (rowid,))
    return len(rows)


def upsert_with_passages(conn: sqlite3.Connection, doc: dict) -> int:
    """Upsert a document; for an intake original also replace its verified passage rows. Returns rows written."""
    upsert_document(conn, doc)
    if not INTAKE_RECORD_POINTER_RE.match(doc["path"]):
        return 1
    delete_passages(conn, doc["path"])
    passages = passage_documents(doc)
    for passage in passages:
        upsert_document(conn, passage)
    return 1 + len(passages)


def iter_indexable(root: Path):
    if not root.exists():
        return
    staging = LIVE_ROOT / "queue/inbox/staging"
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if not is_excluded(current / name, root=root)
            and not (current / name).resolve().is_relative_to(staging.resolve())
        ]
        for name in filenames:
            path = current / name
            if is_excluded(path, root=root) or path.resolve().is_relative_to(staging.resolve()):
                continue
            if path.suffix.lower() in INDEXABLE_EXTENSIONS:
                yield path


def _previous_index_snapshot(db_path: Path, target: Path) -> bool:
    if not db_path.exists():
        return False
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return True


def scan(
    db_path: Path | None = None,
    *,
    roots: list[Path] | None = None,
    registry: ClientScopeRegistry | None = None,
    failure_injection: str | None = None,
    capture_runtime_root: Path | None = None,
) -> dict:
    db_path = runtime_db_path(db_path)
    ensure_default_config()
    gate = registry or load_registry(CLIENT_SCOPE_REGISTRY_PATH)
    default_roots = roots is None
    roots = roots or [LIVE_ROOT, BUSINESS_BRAIN_ROOT]
    indexed = 0
    skipped = 0
    failures = []
    start = time.perf_counter()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = db_path.parent / f".{db_path.name}.{uuid.uuid4().hex}.candidate"
    previous = db_path.parent / f".{db_path.name}.{uuid.uuid4().hex}.previous"
    published = False
    retained_previous = db_path.exists()
    try:
        conn = connect(candidate)
        reset_index(conn)
        for root in roots:
            if not root.exists():
                continue
            for path in iter_indexable(root):
                try:
                    doc = document_from_path(path, registry=gate)
                    if not doc:
                        skipped += 1
                        continue
                    indexed += upsert_with_passages(conn, doc)
                except Exception as exc:  # complete candidate, but never publish a partial index
                    failures.append({"path": str(path), "error": str(exc)})
        projection_root = Path(capture_runtime_root) if capture_runtime_root is not None else (LIVE_ROOT / "capture" / "runtime" if default_roots else None)
        if projection_root is not None and projection_root.exists():
            try:
                try:
                    from aos_capture import capture_document, load_capture_metadata
                except ModuleNotFoundError:
                    from tools.aos_capture import capture_document, load_capture_metadata
                for projection in load_capture_metadata(projection_root):
                    try:
                        upsert_document(conn, capture_document(projection, registry=gate))
                        indexed += 1
                    except ClientScopeError:
                        skipped += 1
            except Exception as exc:
                failures.append({"path": str(projection_root), "error": str(exc)})
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        if failures:
            raise RuntimeError("candidate index contains source failures")
        if failure_injection == "before_publish":
            raise RuntimeError("injected search publication failure")
        with queue_write_lock(LIVE_ROOT):
            had_previous = _previous_index_snapshot(db_path, previous)
            try:
                os.replace(candidate, db_path)
                Path(str(db_path) + "-wal").unlink(missing_ok=True)
                Path(str(db_path) + "-shm").unlink(missing_ok=True)
                fsync_directory(db_path.parent)
                if failure_injection == "after_publish":
                    raise RuntimeError("injected search validation failure")
                validation = connect(db_path, readonly=True)
                validation.execute("SELECT client_scope, COUNT(*) FROM documents GROUP BY client_scope").fetchall()
                validation.close()
                published = True
            except Exception:
                if had_previous and previous.exists():
                    os.replace(previous, db_path)
                    Path(str(db_path) + "-wal").unlink(missing_ok=True)
                    Path(str(db_path) + "-shm").unlink(missing_ok=True)
                    fsync_directory(db_path.parent)
                raise
        retained_previous = not published and retained_previous
        status_value = "success"
    except Exception as exc:
        if not failures:
            failures.append({"path": str(db_path), "error": str(exc)})
        status_value = "failed"
    finally:
        for path in (candidate, previous, Path(str(candidate) + "-wal"), Path(str(candidate) + "-shm")):
            path.unlink(missing_ok=True)
    return {
        "status": status_value,
        "indexed": indexed,
        "skipped": skipped,
        "failures": failures[:25],
        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
        "indexed_at": utc_now(),
        "db_path": str(db_path),
        "published": published,
        "retained_previous": retained_previous,
        "token_usage_text": TOKEN_USAGE_TEXT,
    }


def index_one(path_text: str, db_path: Path | None = None, *, registry: ClientScopeRegistry | None = None) -> dict:
    db_path = runtime_db_path(db_path)
    ensure_default_config()
    path = Path(path_text)
    if not path.is_absolute():
        path = resolve_root_relative(path_text, root=LIVE_ROOT)
    doc = document_from_path(path, registry=registry)
    if not doc:
        return {"status": "skipped", "path": str(path), "token_usage_text": TOKEN_USAGE_TEXT}
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        stat = path.stat()
        if (stat.st_mtime, stat.st_size) != (doc["mtime"], doc["size_bytes"]):
            return {"status": "pending", "path": doc["path"], "reason": "source changed during indexing",
                    "token_usage_text": TOKEN_USAGE_TEXT}
        existing = conn.execute(
            "SELECT content_sha256, source_root, client_scope, mtime, size_bytes FROM documents WHERE path=?",
            (doc["path"],),
        ).fetchone()
        if (doc["content_sha256"] and existing and all(existing[key] == doc[key]
              for key in ("content_sha256", "source_root", "client_scope"))):
            if (existing["mtime"], existing["size_bytes"]) != (doc["mtime"], doc["size_bytes"]):
                conn.execute("UPDATE documents SET mtime=?, size_bytes=? WHERE path=?",
                             (doc["mtime"], doc["size_bytes"], doc["path"]))
            written = 0
        else:
            written = upsert_with_passages(conn, doc)
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "path": doc["path"], "kind": doc["kind"], "rows": written, "token_usage_text": TOKEN_USAGE_TEXT}


def remove_one(path_text: str, db_path: Path | None = None) -> dict:
    """Drop the search row of a file that no longer exists (a deleted Brain note)."""
    db_path = runtime_db_path(db_path)
    path = Path(path_text)
    if not path.is_absolute():
        path = resolve_root_relative(path_text, root=LIVE_ROOT)
    if path.exists():
        return {"status": "refused", "reason": "file still exists", "path": str(path), "token_usage_text": TOKEN_USAGE_TEXT}
    source, root = source_for_path(path)
    if source == "external":
        return {"status": "skipped", "path": str(path), "token_usage_text": TOKEN_USAGE_TEXT}
    # The pointer helper requires an existing file, so project the (deleted) path directly.
    pointer = f"{source}:{relative_to_root(path, root)}"
    conn = connect(db_path)
    try:
        rows = [row["id"] for row in conn.execute("SELECT id FROM documents WHERE path = ?", (pointer,))]
        for rowid in rows:
            conn.execute("DELETE FROM documents_fts WHERE rowid=?", (rowid,))
            conn.execute("DELETE FROM documents WHERE id=?", (rowid,))
        rows += [None] * delete_passages(conn, pointer)
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "path": pointer, "removed": len(rows), "token_usage_text": TOKEN_USAGE_TEXT}


# Search ranking (2026-11-07). Memory Ingest v3 stores each source as topic notes and a
# source INDEX in sources/historical_calls/<folder>/, plus the raw original in
# sources/intake/records/. The original contains every word, so plain bm25 put it above the
# notes that answer the question. Records sit in a lower tier (they are still returned, and
# win only when nothing else matches); v3 notes and source indexes count double.
# Ordering only: which documents match, and every scope rule, are unchanged.
V3_NOTE_GLOB = "business_brain:sources/historical_calls/*/*.md"
INTAKE_RECORD_GLOB = "business_brain:sources/intake/records/*"
V3_NOTE_WEIGHT = 2.0
RANK_TIER_SQL = f"(CASE WHEN d.path GLOB '{INTAKE_RECORD_GLOB}' AND d.path NOT GLOB '{PASSAGE_GLOB}' THEN 1 ELSE 0 END)"
RANK_WEIGHT_SQL = (
    f"(CASE WHEN d.path GLOB '{V3_NOTE_GLOB}'"
    " AND d.path NOT GLOB 'business_brain:sources/historical_calls/*/*/*'"
    " AND d.path NOT GLOB 'business_brain:sources/historical_calls/cards/*'"
    f" THEN {V3_NOTE_WEIGHT} ELSE 1.0 END)"
)


# Hybrid search (2026-11-07): keyword order above is one ranking; local-embedding similarity
# (tools/aos_embeddings.py) is the other. Reciprocal rank fusion merges them, then the same
# ordering rules are re-applied on top: a named person's hub first, intake originals in the
# lower tier. Embedding candidates pass the same WHERE filters as keyword rows.
RRF_K = 30
RRF_WEIGHTS = {"keyword": 1.0, "semantic": 3.0}  # chosen on odd questions, checked on even (tune_fusion.txt)
HYBRID_POOL = 100
EXACT_PROMOTE = 5  # a whole-query phrase matching at most this many documents is an identifier/title lookup
INTAKE_RECORD_PREFIX = "business_brain:sources/intake/records/"
# Canonical Brain areas (also the canonical half of what tools/aos_embeddings.py embeds).
CANONICAL_PREFIXES = tuple(f"business_brain:{area}" for area in ("memory/", "decisions/", "prospects/", "operating_context/"))
# Canonical-first (2026-11-07, hybrid only): a canonical file that lands in the fused top
# CANONICAL_WINDOW moves above historical evidence. 10 is the Dashboard/connector first page,
# fixed before measuring, not tuned. A canonical file outside the window keeps its place, so
# weak OR-matches on common words do not lift every canonical file to the top.
CANONICAL_WINDOW = 10
SEARCH_MODE_HYBRID = "hybrid"
SEARCH_MODE_KEYWORD_ONLY = "keyword-only"
MAX_OFFSET = 500
# Casey's own conversations: v3 notes whose participants_text names participants (not "none").
CASEY_ONLY_SQL = "(d.participants != '' AND lower(d.participants) != 'none')"


PERSON_HUB_PREFIX = "business_brain:people/"
PEOPLE_NAVIGATION_SLUGS = {"index", "needs-identity"}  # people/INDEX.md, people/NEEDS_IDENTITY.md


def _name_norm(value: str) -> str:
    # The same normalisation aos_entity_index.norm uses for person names.
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def person_hub_pointers(conn: sqlite3.Connection, query: str, scope_id: str) -> list[str]:
    """People hubs whose full name is the query or a run of words inside it (2-4 words).

    A hub qualifies only when its slug is that name and its indexed title is the same name,
    so INDEX.md / NEEDS_IDENTITY.md and first-name-only queries never qualify.
    """
    words = _name_norm(query).split()
    names = {" ".join(words[i:i + n]) for n in (2, 3, 4) for i in range(len(words) - n + 1)}
    if not names:
        return []
    candidates = {f"{PERSON_HUB_PREFIX}{name.replace(' ', '-')}.md": name for name in names
                  if name.replace(" ", "-") not in PEOPLE_NAVIGATION_SLUGS}
    marks = ",".join("?" for _ in candidates)
    rows = conn.execute(f"SELECT path, title FROM documents WHERE client_scope = ? AND path IN ({marks})",
                        [scope_id, *candidates]).fetchall()
    return sorted(row["path"] for row in rows if _name_norm(row["title"]) == candidates[row["path"]])


def quote_fts_query(query: str) -> str:
    terms = re.findall(r"[\w.-]+", query)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def quote_exact_fts_query(query: str) -> str:
    terms = re.findall(r"[\w.-]+", query)
    return f'"{" ".join(terms[:24])}"' if terms else '""'


def public_row(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["modified"] = dt.datetime.fromtimestamp(float(value["mtime"]), dt.timezone.utc).isoformat().replace("+00:00", "Z")
    value["tags"] = [tag for tag in str(value.get("tags") or "").split(",") if tag]
    value.pop("body", None)
    value.pop("mtime", None)
    return value


# A v3 note's navigation line: "[[...records/<sha>|...]] · Lines L7-L17" (line-cited notes) or "· Spans C120-480"
# (revision-3 notes, code-point offsets); the first range names the note's original passage.
NOTE_ORIGINAL_RE = re.compile(r"sources/intake/records/([0-9a-f]{64})\|[^\]]*\]\]\s*·\s*(?:Lines (L\d+)-(L\d+)|Spans (C\d+-\d+))")
CARD_POINTER_RE = re.compile(r"\Abusiness_brain:sources/intake/cards/([0-9a-f]{64})\.card\.md\Z")
_EXCERPT_TERM = re.compile(r"[a-z0-9][a-z0-9'-]{1,}")
_EXCERPT_STOP = frozenset("the and for that this with from are was were have has had you your not but they them their what "
                          "when where which who how why does did can could should would about into than then also is it of "
                          "to in on a an be as at by or if".split())


def query_excerpt(text: str, query: str, chars: int) -> str:
    """The ``chars``-long run of whole lines (sentences for one long line) that best covers the query's rarest terms.

    Deterministic and local: terms are weighted by 1/(occurrences in this text), the best-scoring window is
    chosen, and with no term match the text opens at its start. No model is involved.
    """
    text = str(text or "").strip()
    if len(text) <= chars:
        return text
    terms = {t for t in _EXCERPT_TERM.findall(query.lower()) if t not in _EXCERPT_STOP}
    pieces = [piece for line in text.splitlines() for piece in (re.split(r"(?<=[.!?])\s+", line) if len(line) > chars // 2 else [line])]
    lower = [piece.lower() for piece in pieces]
    weight = {}
    for term in terms:
        count = sum(piece.count(term) for piece in lower)
        if count:
            weight[term] = 1.0 / count
    best, best_score = 0, -1.0
    for first in range(len(pieces)):
        size, score, seen = 0, 0.0, set()
        for last in range(first, len(pieces)):
            size += len(pieces[last]) + 1
            if size > chars and last > first:
                break
            for term in weight:
                if term in lower[last] and term not in seen:
                    seen.add(term)
                    score += weight[term]
        if score > best_score:
            best, best_score = first, score
    if best_score > 0:  # open on the first piece that actually carries a term
        while not any(term in lower[best] for term in weight):
            best += 1
    out, size = [], 0
    for piece in pieces[best:]:
        if size + len(piece) + 1 > chars and out:
            break
        out.append(piece)
        size += len(piece) + 1
    return "\n".join(out)[:chars]


def evidence_excerpt(body: str, query: str, chars: int) -> str:
    """Exclude source navigation and questions from a note's evidence excerpt."""
    cleaned = re.sub(r"<!-- TTROS:MACHINE:.*?:BEGIN -->.*?<!-- TTROS:MACHINE:.*?:END -->", "", body, flags=re.S)
    cleaned = re.sub(r"(?ms)^## (?:Questions this answers|Related notes[^\n]*|Source)\s*$.*?(?=^## |\Z)", "", cleaned)
    if re.search(r"(?m)^## Summary\s*$", cleaned):
        cleaned = re.sub(r"(?ms)\A.*?(?=^## Summary\s*$)", "", cleaned, count=1)
    return query_excerpt(cleaned, query, chars)


def search(
    query: str,
    *,
    kind: str = "",
    tag: str = "",
    source: str = "",
    limit: int = 25,
    db_path: Path | None = None,
    client_scope: str | None = None,
    registry: ClientScopeRegistry | None = None,
    exact: bool = False,
    path_only: bool = False,
    hybrid: bool = False,
    offset: int = 0,
    casey_only: bool = False,
    passages: bool = False,
    excerpt_chars: int = 0,
    rerank: bool = False,
    evidence_only: bool = False,
) -> dict:
    """``passages=True`` also returns exact-byte-verified source passages (``source:<sha>#L<a>-L<b>`` or ``#C<a>-<b>``); other
    callers never see them. ``excerpt_chars`` > 0 adds a query-centred ``excerpt`` to every non-path_only row. ``rerank`` (hybrid only)
    reorders the fused top RERANK_POOL by rare-query-term coverage, see ``rerank_fused``."""
    db_path = runtime_db_path(db_path)
    start = time.perf_counter()
    gate = registry or load_registry(CLIENT_SCOPE_REGISTRY_PATH)
    scope = gate.validate_search_source(client_scope, source)
    if not db_path.exists():
        return {"query": query, "count": 0, "groups": {key: [] for key in ("files", "receipts", "queue_items", "prompts_skills_workflows", "memory", "artifacts")}, "latency_ms": 0.0, "token_usage_text": TOKEN_USAGE_TEXT}
    conn = connect(db_path, readonly=True)
    limit = max(1, min(int(limit or 25), 100))
    offset = max(0, min(int(offset or 0), MAX_OFFSET))
    base_select = "d.path, d.kind, d.source, d.client_scope" if path_only else "d.*"
    # Filters shared by the keyword query and the embedding candidates.
    filters = ["d.client_scope = ?"]
    filter_params: list[object] = [scope.scope_id]
    if kind:
        filters.append("d.kind = ?")
        filter_params.append(kind)
    if source:
        filters.append("d.source = ?")
        filter_params.append(source)
    if tag:
        filters.append("(',' || d.tags || ',') LIKE ?")
        filter_params.append(f"%,{tag},%")
    if not passages:
        filters.append(PASSAGE_EXCLUDE_SQL)
    if evidence_only:
        filters.append(f"(d.path NOT GLOB '{INTAKE_RECORD_GLOB}' OR d.path GLOB '{PASSAGE_GLOB}')")
        filters.append("d.path NOT GLOB 'business_brain:sources/historical_calls/*/INDEX.md'")
    if casey_only:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "participants" not in columns:
            conn.close()
            raise ValueError("search index predates the participants column; rescan the index")
        filters.append(CASEY_ONLY_SQL)
    params: list[object] = []
    order_params: list[object] = []
    where = list(filters[:1])
    params.extend(filter_params[:1])
    join = ""
    select = base_select
    hubs: list[str] = []
    # exact=True with hybrid: the keyword half is the exact phrase match, and every exact hit
    # stays ahead of embedding-only candidates (fuse_rankings exact_first), so the hybrid result
    # always starts with the keyword exact result in its own order.
    use_hybrid = bool(hybrid and query.strip())
    if query.strip():
        fts = quote_exact_fts_query(query) if exact else quote_fts_query(query)
        if not fts:
            fts = '""'
        join = "JOIN documents_fts f ON d.id = f.rowid"
        where.append("documents_fts MATCH ?")
        params.append(fts)
        select = base_select + ", bm25(documents_fts) AS rank"
        order = f"{RANK_TIER_SQL}, rank * {RANK_WEIGHT_SQL}, d.mtime DESC"
        hubs = person_hub_pointers(conn, query, scope.scope_id)
        if hubs:  # a named person's hub comes first; bound as parameters, never interpolated
            order = f"(CASE WHEN d.path IN ({','.join('?' for _ in hubs)}) THEN 0 ELSE 1 END), " + order
            order_params = hubs
    else:
        order = "d.mtime DESC"
    where.extend(filters[1:])
    params.extend(filter_params[1:])
    sql = f"SELECT {select} FROM documents d {join}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend(order_params)
    hybrid_info: dict = {"requested": bool(hybrid), "used": False}
    if hybrid and not query.strip():
        hybrid_info["reason"] = "empty query"
    if use_hybrid:
        params.extend([max(offset + limit + 1, HYBRID_POOL), 0])
        keyword_rows = conn.execute(sql, params).fetchall()
        try:
            from aos_embeddings import vector_ranking
        except ModuleNotFoundError:
            from tools.aos_embeddings import vector_ranking
        try:
            vectors = vector_ranking(query, db_path, **({"reuse_model": True} if evidence_only else {}))
        except Exception as exc:  # a broken embedding store must never break keyword search
            vectors = {"used": False, "reason": f"{type(exc).__name__}: {exc}"[-300:]}
        hybrid_info.update({key: value for key, value in vectors.items() if key != "ranking"})
        semantic_rows: list = []
        if vectors.get("used"):
            pool = [pointer for pointer, _score in vectors["ranking"] if passages or not is_passage_pointer(pointer)][:HYBRID_POOL * 3]
            if pool:
                vsql = (f"SELECT {base_select}, NULL AS rank FROM documents d WHERE " + " AND ".join(filters)
                        + f" AND d.path IN ({','.join('?' for _ in pool)})")
                found = {row["path"]: row for row in conn.execute(vsql, [*filter_params, *pool]).fetchall()}
                semantic_rows = [found[pointer] for pointer in pool if pointer in found][:HYBRID_POOL]
        exact_rows: list = []
        if not exact:
            # Title / Drive-ID lookups: the whole query as one phrase. Rare phrase hits are promoted so the
            # semantic half cannot bury them; a phrase common to many documents is ordinary ranking.
            phrase = quote_exact_fts_query(query)
            if phrase != '""':
                exact_rows = conn.execute(
                    f"SELECT {base_select}, bm25(documents_fts) AS rank FROM documents d JOIN documents_fts f ON d.id = f.rowid "
                    "WHERE " + " AND ".join([filters[0], "documents_fts MATCH ?", *filters[1:]])
                    + f" ORDER BY {RANK_TIER_SQL}, rank * {RANK_WEIGHT_SQL}, d.mtime DESC LIMIT ?",
                    [filter_params[0], phrase, *filter_params[1:], EXACT_PROMOTE + 1]).fetchall()
                if len(exact_rows) > EXACT_PROMOTE:
                    exact_rows = []
        fused = fuse_rankings(keyword_rows, semantic_rows, hubs, exact_first=exact, exact_rows=exact_rows)
        if rerank and not path_only:
            fused = rerank_fused(conn, query, fused, hubs, frozenset(row["path"] for row in keyword_rows[:RERANK_KEYWORD_KEEP]),
                                 evidence_only=evidence_only)
        selected = [row for row, _match in fused[offset:offset + limit]]
        matches = {row["path"]: match for row, match in fused[offset:offset + limit]}
        has_more = len(fused) > offset + limit
    else:
        params.extend([limit + 1, offset])
        selected = conn.execute(sql, params).fetchall()
        has_more = len(selected) > limit
        selected = selected[:limit]
        matches = {}
    bodies = {row["path"]: row["body"] for row in selected} if (excerpt_chars and not path_only) else {}
    rows = [dict(row) if path_only else public_row(row) for row in selected]
    for row in rows:
        ref = None if path_only else passage_source_ref(row["path"])
        if ref:
            row["source_ref"] = ref
        if bodies:
            body = bodies.get(row["path"], "")
            build_excerpt = evidence_excerpt if evidence_only and not is_passage_pointer(row["path"]) else query_excerpt
            row["excerpt"] = build_excerpt(body, query, int(excerpt_chars))
            if "source_ref" not in row:  # a v3 note names its original and line range; a card is keyed by the original's SHA
                note = NOTE_ORIGINAL_RE.search(body[:2500])
                card = CARD_POINTER_RE.match(row["path"])
                if note:
                    row["source_ref"] = (f"source:{note.group(1)}#{note.group(2)}-{note.group(3)}" if note.group(2)
                                         else f"source:{note.group(1)}#{note.group(4)}")
                elif card:
                    row["source_ref"] = f"source:{card.group(1)}"
    if use_hybrid:
        for row in rows:
            row["match"] = matches.get(row["path"], "keyword")
    # Which ranking actually produced these results. "keyword-only" whenever hybrid was asked for
    # and the embedding half was not used (no store, model failed to load, runtime missing, ...).
    search_mode = SEARCH_MODE_HYBRID if hybrid_info.get("used") else SEARCH_MODE_KEYWORD_ONLY
    hybrid_info["mode"] = search_mode
    if not path_only:
        attach_suggested_links(conn, rows)
    conn.close()
    groups: dict[str, list[dict]] = {
        "files": [],
        "receipts": [],
        "queue_items": [],
        "prompts_skills_workflows": [],
        "memory": [],
        "artifacts": [],
    }
    for rank, row in enumerate(rows, offset + 1):
        if is_excluded(row["path"]):
            continue
        bucket = {
            "receipt": "receipts",
            "queue_item": "queue_items",
            "workflow": "prompts_skills_workflows",
            "memory": "memory",
            "artifact": "artifacts",
        }.get(row["kind"], "files")
        row["retrieval_rank"] = rank
        groups[bucket].append(row)
    return {
        "query": query,
        "count": sum(len(items) for items in groups.values()),
        "groups": groups,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if has_more else None,
        "hybrid": hybrid_info,
        "search_mode": search_mode,
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "token_usage_text": TOKEN_USAGE_TEXT,
    }


SUGGESTED_LINKS_PER_RESULT = 3


def attach_suggested_links(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Give each Business Brain result its corroborated suggested links (tools/brain_graph_links.py):
    derived, status=suggested, only within the result's own client scope, with reasons and evidence."""
    by_path = {row["path"]: row for row in rows if str(row.get("path") or "").startswith("business_brain:")}
    if not by_path:
        return
    marks = ",".join("?" for _ in by_path)
    try:
        links = conn.execute(
            f"SELECT * FROM graph_suggested_links WHERE status = 'suggested' AND (source_path IN ({marks}) OR target_path IN ({marks})) "
            "ORDER BY similarity DESC, source_path, target_path", [*by_path, *by_path]).fetchall()
    except sqlite3.OperationalError:  # no links table yet
        return
    for link in links:
        for here, there in ((link["source_path"], link["target_path"]), (link["target_path"], link["source_path"])):
            row = by_path.get(here)
            if row is None or row.get("client_scope") != link["client_scope"]:
                continue
            attached = row.setdefault("suggested_links", [])
            if len(attached) < SUGGESTED_LINKS_PER_RESULT:
                attached.append({
                    "path": there, "relation": link["relation"], "status": link["status"], "similarity": link["similarity"],
                    "confidence": link["confidence"], "reasons": json.loads(link["reasons"]),
                    "evidence": json.loads(link["evidence"]), "authoritative": False,
                })


RERANK_POOL = 40
RERANK_KEYWORD_KEEP = 15
RERANK_PRIOR_WEIGHT = 0.5   # fused-position prior against term coverage; fixed before measuring
RERANK_PRIOR_SCALE = 8.0    # prior = 1 / (1 + position / scale)
RERANK_BIGRAM_BONUS = 0.15
RERANK_MAX_TERMS = 12
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")


def _stem(token: str) -> str:
    return token[:5]


def rerank_fused(conn: sqlite3.Connection, query: str, fused: list[tuple], hubs: list[str], keep: frozenset = frozenset(), *,
                 evidence_only: bool = False) -> list[tuple]:
    """Reorder the fused top RERANK_POOL by how much of the question's rare vocabulary each candidate covers.

    Reciprocal-rank fusion lets the semantic half bury a document that holds the question's one rare word
    (a name, a figure, a product). Here every candidate is scored locally and deterministically:
    coverage = share of the question's idf-weighted terms (document frequency from the keyword index, matched on
    a 5-character stem) found in its title and body, plus a small bonus per adjacent query word pair found
    adjacent, blended with the fused-position prior. Named-person hubs stay first and whole originals stay in
    the last tier. A passage is scored on its section heading and lines only (its title repeats the source's title). The pool is the
    fused top RERANK_POOL plus any candidate in ``keep`` (the keyword half's best hits, which the heavier
    semantic weight can push down). Candidates beyond the pool keep their fused order. No model is called.
    """
    words = [w for w in _TOKEN_RE.findall(query.lower()) if w not in _EXCERPT_STOP and len(w) > 1][:RERANK_MAX_TERMS]
    terms = list(dict.fromkeys(words))
    if not terms or len(fused) < 2:
        return fused
    total = max(1, conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    weight: dict[str, float] = {}
    for term in terms:
        try:
            df = conn.execute("SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?", (f'"{term}"',)).fetchone()[0]
        except sqlite3.OperationalError:
            df = 0
        weight[term] = math.log(1 + total / (1 + df))
    norm = sum(weight.values()) or 1.0
    pairs = [(a, b) for a, b in zip(words, words[1:]) if a != b]
    chosen = [position < RERANK_POOL or row["path"] in keep for position, (row, _match) in enumerate(fused)]
    head = [(position, item) for position, item in enumerate(fused) if chosen[position]]
    tail = [item for position, item in enumerate(fused) if not chosen[position]]
    hub_set = set(hubs)
    scored = []
    for position, (row, match) in head:
        if is_passage_pointer(row["path"]):  # the section heading is the title's middle part; the source title is not evidence
            parts = row["title"].split(" · ")
            own = f"{' '.join(parts[1:-1])} {row['body']}"
        else:
            own = f"{row['title']} {row['body']}"
        tokens = _TOKEN_RE.findall(own.lower())
        stems = {_stem(token) for token in tokens}
        found = sum(weight[t] for t in terms if _stem(t) in stems) / norm
        if evidence_only and not is_passage_pointer(row["path"]):
            title_stems = {_stem(token) for token in _TOKEN_RE.findall(row["title"].lower())}
            found += 0.3 * sum(weight[t] for t in terms if _stem(t) in title_stems) / norm
        adjacent = {(_stem(a), _stem(b)) for a, b in zip(tokens, tokens[1:])}
        bonus = RERANK_BIGRAM_BONUS * sum(1 for a, b in pairs if (_stem(a), _stem(b)) in adjacent)
        prior = 1.0 / (1.0 + position / RERANK_PRIOR_SCALE)
        record_tier = row["path"].startswith(INTAKE_RECORD_PREFIX) and not is_passage_pointer(row["path"])
        scored.append((0 if row["path"] in hub_set else 1, 1 if record_tier else 0,
                       -(RERANK_PRIOR_WEIGHT * prior + found + bonus), position, row, match))
    scored.sort(key=lambda item: item[:4])
    return [(row, match) for *_key, row, match in scored] + tail


def is_canonical_pointer(pointer: str) -> bool:
    return str(pointer).startswith(CANONICAL_PREFIXES)


def fuse_rankings(keyword_rows: list, semantic_rows: list, hubs: list[str], *, exact_first: bool = False,
                  exact_rows: list = ()) -> list[tuple]:
    """Reciprocal rank fusion, then the ordering rules on top.

    Order: a named person's hub first; with exact_first, every exact keyword hit next in keyword
    order; then canonical files that reached the fused top CANONICAL_WINDOW; then everything else
    by fused score, intake originals in the last tier.
    """
    score: dict[str, float] = {}
    by_path: dict[str, object] = {}
    sources: dict[str, set] = {}
    for label, ranked in (("keyword", keyword_rows), ("semantic", semantic_rows)):
        for position, row in enumerate(ranked, 1):
            pointer = row["path"]
            score[pointer] = score.get(pointer, 0.0) + RRF_WEIGHTS[label] / (RRF_K + position)
            by_path.setdefault(pointer, row)
            sources.setdefault(pointer, set()).add(label)
    hub_set = set(hubs)
    exact_rank = {row["path"]: position for position, row in enumerate(keyword_rows)} if exact_first else {}
    for position, row in enumerate(exact_rows):  # rare whole-query phrase hits (see search); may lie outside the pool
        score.setdefault(row["path"], 0.0)
        by_path.setdefault(row["path"], row)
        sources.setdefault(row["path"], set()).add("keyword")
        exact_rank.setdefault(row["path"], position)

    def base_key(pointer: str) -> tuple:
        return (
            0 if pointer in hub_set else 1,
            1 if pointer.startswith(INTAKE_RECORD_PREFIX) and not is_passage_pointer(pointer) else 0,
            -score[pointer],
            -float(by_path[pointer]["mtime"]) if "mtime" in by_path[pointer].keys() else 0.0,
            pointer,
        )

    base = sorted(by_path, key=base_key)
    promoted = {pointer for pointer in base[:CANONICAL_WINDOW] if is_canonical_pointer(pointer)}
    ordered = sorted(base, key=lambda pointer: (
        0 if pointer in hub_set else 1,
        (0, exact_rank[pointer]) if pointer in exact_rank else (1, 0),
        0 if pointer in promoted else 1,
        base_key(pointer)[1:],
    ))
    return [(by_path[pointer], "+".join(sorted(sources[pointer]))) for pointer in ordered]


def status(db_path: Path | None = None) -> dict:
    db_path = runtime_db_path(db_path)
    if not db_path.exists():
        return {
            "exists": False,
            "files_indexed": 0,
            "last_scan_time": None,
            "last_ingestion_receipt": None,
            "failures_count": 0,
            "db_path": str(db_path),
            "token_usage_text": TOKEN_USAGE_TEXT,
        }
    conn = connect(db_path, readonly=True)
    row = conn.execute("SELECT COUNT(*) AS count, MAX(indexed_at) AS last_scan_time FROM documents").fetchone()
    latest_receipt = None
    failures_count = 0
    if INGEST_RECEIPT_PATH.exists():
        for line in INGEST_RECEIPT_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            latest_receipt = record
            if record.get("status") not in {"success", "skipped"}:
                failures_count += 1
    # Ingestion receipts are durable history and can legitimately predate the
    # Linux-authority migration.  Status is an operator surface, so expose the
    # stable logical identity only; absolute source paths are neither needed to
    # assess health nor safe to present as a current runtime authority.
    public_receipt = None
    if latest_receipt is not None:
        public_receipt = {
            key: value for key, value in latest_receipt.items()
            if key not in {"source_path", "source_root"}
        }
    return {
        "exists": True,
        "files_indexed": int(row["count"] or 0),
        "last_scan_time": row["last_scan_time"],
        "last_ingestion_receipt": public_receipt,
        "failures_count": failures_count,
        "db_path": str(db_path),
        "token_usage_text": TOKEN_USAGE_TEXT,
    }


def load_watch_roots() -> list[Path]:
    config = ensure_default_config()
    roots = []
    for value in config.get("watch_roots") or config.get("inboxes") or ["queue/inbox"]:
        try:
            roots.append(resolve_root_relative(value, root=LIVE_ROOT))
        except Exception:
            continue
    return roots


def write_ingestion_receipt(record: dict) -> None:
    assert_authoritative_root(LIVE_ROOT)
    durable_append_text(LIVE_ROOT, INGEST_RECEIPT_PATH, json.dumps(record, sort_keys=True) + "\n")


def ingested_source_paths() -> set[str]:
    if not INGEST_RECEIPT_PATH.exists():
        return set()
    paths = set()
    for line in INGEST_RECEIPT_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        source_path = str(record.get("source_path") or "")
        if source_path:
            paths.add(source_path)
    return paths


def is_inbox_path(path: Path) -> bool:
    config = ensure_default_config()
    for value in config.get("inboxes") or ["queue/inbox"]:
        try:
            inbox = resolve_root_relative(value, root=LIVE_ROOT)
            path.resolve().relative_to(inbox.resolve())
            return True
        except Exception:
            continue
    return False


def ingest_tick(db_path: Path | None = None) -> dict:
    db_path = runtime_db_path(db_path)
    ensure_default_config()
    processed = []
    indexed = 0
    already_receipted = ingested_source_paths()
    candidates = []
    for root in load_watch_roots():
        if not root.exists() or BUSINESS_BRAIN_ROOT.resolve() in root.resolve().parents:
            continue
        for path in iter_indexable(root):
            if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
                continue
            if path.resolve() == INGEST_RECEIPT_PATH.resolve():
                continue
            candidates.append(path)
    for path in candidates:
        result = index_one(str(path), db_path=db_path)
        indexed += 1
        if not is_inbox_path(path) or str(path) in already_receipted:
            continue
        source, source_root = source_for_path(path)
        record = {
            "timestamp": utc_now(),
            "source_path": str(path),
            "indexed_path": result.get("path", str(path)),
            "status": result.get("status", "success"),
            "kind": result.get("kind", kind_for_path(path, source)),
            "source": source,
            "source_root": str(source_root),
            "token_usage_text": TOKEN_USAGE_TEXT,
        }
        write_ingestion_receipt(record)
        processed.append(record)
    return {
        "status": "success",
        "processed": processed,
        "count": len(processed),
        "indexed": indexed,
        "token_usage_text": TOKEN_USAGE_TEXT,
    }


def artifacts(*, kind: str = "", tag: str = "", source: str = "", limit: int = 50, db_path: Path | None = None, client_scope: str | None = None, registry: ClientScopeRegistry | None = None) -> dict:
    result = search("", kind=kind, tag=tag, source=source, limit=limit, db_path=db_path, client_scope=client_scope, registry=registry)
    items = []
    for group_name in ("artifacts", "receipts", "queue_items", "prompts_skills_workflows", "memory", "files"):
        items.extend(result["groups"][group_name])
    items.sort(key=lambda item: item.get("modified") or "", reverse=True)
    return {"items": items[:limit], "latency_ms": result["latency_ms"], "token_usage_text": TOKEN_USAGE_TEXT}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agentic OS local deterministic search index")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    one = sub.add_parser("index")
    one.add_argument("path")
    watch = sub.add_parser("watch")
    watch.add_argument("--once", action="store_true")
    query = sub.add_parser("search")
    query.add_argument("q")
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--client-scope", required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "scan":
        payload = scan()
    elif args.command == "index":
        payload = index_one(args.path)
    elif args.command == "watch":
        payload = ingest_tick()
    elif args.command == "search":
        payload = search(args.q, limit=args.limit, client_scope=args.client_scope)
    elif args.command == "status":
        payload = status()
    else:
        parser.error("unknown command")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
