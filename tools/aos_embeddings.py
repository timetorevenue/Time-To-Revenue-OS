"""Local embeddings for hybrid Business Brain search (2026-11-07).

Revisit: when the embedded scope, the model, or the chunking changes (bump EMBED_VERSION). · Last touched: 2026-11-07.

What is embedded: v3 historical topic notes and source INDEX files
(sources/historical_calls/<folder>/<note>.md), Tier 1 source cards (sources/intake/cards/), Tier 0
verified source passages (<record>.md#L<a>-L<b> rows of tools/aos_indexer) and canonical Brain files
(memory/, decisions/, prospects/, operating_context/). Whole originals (sources/intake/records/),
people hubs and everything outside the Brain stay keyword-only.

The text embedded is the keyword index's own title + body for that row, so the sanitising,
exclusions and client-scope rules of tools/aos_indexer.py apply unchanged.

Privacy and resources: the model (BAAI/bge-small-en-v1.5, ONNX, CPU) is read from a local
directory and this module has no network code. onnxruntime runs with 4 intra-op threads and
the CPU provider only. The model is loaded inside each refresh or search call and released
when the call returns; nothing stays resident.

Store: search/os_embeddings.db beside the keyword index. Rows are keyed by pointer and the
SHA-256 of the embedded text, so refresh() embeds only new or changed rows and drops rows
that left the scope. refresh() never loads the model when nothing changed, and every
function is a no-op while the store file does not exist (create it with `rebuild`).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gc
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import aos_indexer  # noqa: E402

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_DIR = Path(os.environ.get("TTROS_EMBED_MODEL_DIR") or Path.home() / ".local/share/ttros/models/bge-small-en-v1.5")
MODEL_FILES_SHA256 = {
    "model.onnx": "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
}
THREADS = 4
MAX_TOKENS = 512
BATCH = 16
CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200
# bge v1.5 retrieval: queries carry this instruction, passages do not.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
EMBED_VERSION = "emb-v1:bge-small-en-v1.5:cls:chunk1500/200"
STORE_NAME = "os_embeddings.db"
# A search embeds at most this many stale rows inline; more than that waits for a refresh.
SEARCH_CATCH_UP_MAX = 25

HISTORICAL_NOTE_RE = re.compile(r"^business_brain:sources/historical_calls/(?!cards/)[^/]+/[^/]+\.md$")
INTAKE_CARD_RE = re.compile(r"^business_brain:sources/intake/cards/[0-9a-f]{64}\.card\.md$")
CANONICAL_PREFIXES = aos_indexer.CANONICAL_PREFIXES
# Canonical files longer than one chunk are embedded one heading section at a time (2026-11-07);
# historical notes keep the fixed windows above. Part of the canonical rows' text hash, so a
# change here re-embeds only the canonical rows.
CANONICAL_CHUNKING = "sections-v1"
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


class EmbeddingUnavailable(RuntimeError):
    """The local model or its runtime is not usable; callers fall back to keyword search."""


def is_embeddable(pointer: str) -> bool:
    # Tier 1 cards and Tier 0 verified source passages (2026-11-12) join the v3 notes; whole originals stay keyword-only.
    return bool(HISTORICAL_NOTE_RE.match(pointer)) or bool(INTAKE_CARD_RE.match(pointer)) or aos_indexer.is_passage_pointer(pointer) or (
        pointer.startswith(CANONICAL_PREFIXES) and pointer.endswith(".md"))


def store_path(db_path: Path | None = None) -> Path:
    return aos_indexer.runtime_db_path(db_path).with_name(STORE_NAME)


def text_sha(title: str, body: str, pointer: str = "") -> str:
    version = f"{EMBED_VERSION}+{CANONICAL_CHUNKING}" if aos_indexer.is_canonical_pointer(pointer) else EMBED_VERSION
    return hashlib.sha256(f"{version}\n{title}\n{body}".encode("utf-8")).hexdigest()


def _windows(text: str) -> list[str]:
    step = CHUNK_CHARS - CHUNK_OVERLAP
    return [text[start:start + CHUNK_CHARS] for start in range(0, max(len(text) - CHUNK_OVERLAP, 1), step)]


def sections_for(body: str) -> list[tuple[str, str]]:
    """(heading, text) per Markdown heading section; text before the first heading is ("", text).

    A heading with no text of its own is carried into the next section rather than embedded alone.
    """
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in str(body or "").splitlines():
        if HEADING_RE.match(line):
            heading = line.strip().lstrip("#").strip()
            previous, lines = sections[-1]
            if not any(item.strip() for item in lines) and previous:
                heading = f"{previous} / {heading}"
                sections[-1] = (heading, [])
            else:
                sections.append((heading, []))
            continue
        sections[-1][1].append(line)
    out = [(heading, " ".join(" ".join(lines).split())) for heading, lines in sections]
    return [(heading, text) for heading, text in out if text]


def chunks_for(title: str, body: str, pointer: str = "") -> list[str]:
    """Title-prefixed chunks; a short note is one chunk.

    Historical notes: fixed windows. Canonical files longer than one chunk: one chunk per heading
    section (title and heading prefixed), a long section windowed within itself.
    """
    text = " ".join(str(body or "").split())
    if not text:
        return [title]
    if len(text) <= CHUNK_CHARS or not aos_indexer.is_canonical_pointer(pointer):
        return [f"{title}\n{window}" for window in _windows(text)]
    chunks = []
    for heading, section in sections_for(body):
        prefix = f"{title} — {heading}" if heading and heading != title else title
        chunks.extend(f"{prefix}\n{window}" for window in _windows(section))
    return chunks or [f"{title}\n{window}" for window in _windows(text)]


# --------------------------------------------------------------------------- model

_VERIFIED: dict[str, tuple[int, int]] = {}  # path -> (size, mtime_ns) whose SHA-256 matched


def _verify_model_file(path: Path, expected: str) -> None:
    """Pinned-hash check; the result is remembered per file size+mtime, not the model itself."""
    if not path.is_file():
        raise EmbeddingUnavailable(f"model file missing: {path}")
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    if _VERIFIED.get(str(path)) == signature:
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise EmbeddingUnavailable(f"model file hash mismatch: {path}")
    _VERIFIED[str(path)] = signature


class Embedder:
    """bge-small-en-v1.5 on onnxruntime CPU, 4 threads. Use as a context manager; closing releases it."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            import numpy  # noqa: F401
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingUnavailable(f"embedding runtime not installed: {exc}") from exc
        for name, expected in MODEL_FILES_SHA256.items():
            _verify_model_file(model_dir / name, expected)
        started = time.perf_counter()
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = THREADS
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self.session = ort.InferenceSession(str(model_dir / "model.onnx"), sess_options=options,
                                                providers=["CPUExecutionProvider"])
            self.inputs = {item.name for item in self.session.get_inputs()}
            self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        except Exception as exc:  # any load failure means keyword-only search, never a failed search
            raise EmbeddingUnavailable(f"model failed to load: {type(exc).__name__}: {exc}"[:300]) from exc
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()
        self.load_ms = round((time.perf_counter() - started) * 1000, 1)
        self.texts_embedded = 0

    def embed(self, texts: list[str]):
        import numpy as np
        out = []
        for start in range(0, len(texts), BATCH):
            encoded = self.tokenizer.encode_batch(texts[start:start + BATCH])
            feed = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64)}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
            hidden = self.session.run(None, feed)[0]
            cls = hidden[:, 0, :]
            out.append(cls / np.linalg.norm(cls, axis=1, keepdims=True))
            self.texts_embedded += len(encoded)
        return np.vstack(out).astype(np.float32) if out else np.zeros((0, 384), dtype=np.float32)

    def close(self) -> None:
        self.session = None
        self.tokenizer = None
        gc.collect()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------- store

def _connect_store(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS docs (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, chunks INTEGER NOT NULL, embedded_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS vectors (path TEXT NOT NULL, idx INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (path, idx))")
    conn.commit()
    return conn


@contextlib.contextmanager
def _refresh_lock(path: Path):
    with open(path.with_name(path.name + ".lock"), "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _eligible_rows(db_path: Path | None) -> dict[str, tuple[str, str]]:
    conn = aos_indexer.connect(db_path, readonly=True)
    try:
        # Filter paths before SQLite materializes bodies. Originals are keyword-only.
        patterns = [*(prefix + "*.md" for prefix in CANONICAL_PREFIXES),
                    aos_indexer.V3_NOTE_GLOB, "business_brain:sources/intake/cards/*.card.md",
                    "business_brain:sources/intake/records/" + aos_indexer.PASSAGE_GLOB]
        predicate = " OR ".join("path GLOB ?" for _ in patterns)
        rows = conn.execute(f"SELECT path, title, body FROM documents "
                            f"WHERE source='business_brain' AND ({predicate})", patterns).fetchall()
    finally:
        conn.close()
    return {row["path"]: (row["title"], row["body"]) for row in rows if is_embeddable(row["path"])}


def _plan(store: sqlite3.Connection, eligible: dict[str, tuple[str, str]]) -> tuple[list[str], list[str]]:
    stored = {row["path"]: row["sha256"] for row in store.execute("SELECT path, sha256 FROM docs")}
    changed = sorted(p for p, (title, body) in eligible.items() if stored.get(p) != text_sha(title, body, p))
    removed = sorted(set(stored) - set(eligible))
    return changed, removed


def _write(store: sqlite3.Connection, embedder: Embedder, eligible: dict, changed: list[str], removed: list[str]) -> None:
    for pointer in removed:
        store.execute("DELETE FROM vectors WHERE path = ?", (pointer,))
        store.execute("DELETE FROM docs WHERE path = ?", (pointer,))
    for pointer in changed:
        title, body = eligible[pointer]
        vectors = embedder.embed(chunks_for(title, body, pointer))
        store.execute("DELETE FROM vectors WHERE path = ?", (pointer,))
        store.executemany("INSERT INTO vectors(path, idx, vector) VALUES (?, ?, ?)",
                          [(pointer, i, v.tobytes()) for i, v in enumerate(vectors)])
        store.execute("INSERT OR REPLACE INTO docs(path, sha256, chunks, embedded_at) VALUES (?, ?, ?, ?)",
                      (pointer, text_sha(title, body, pointer), len(vectors), time.time()))
    store.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('embed_version', ?)", (EMBED_VERSION,))
    store.commit()


def refresh(db_path: Path | None = None, *, create: bool = False, embedder_factory=None) -> dict:
    """Bring the store in line with the keyword index. No-op without a store unless create=True."""
    started = time.perf_counter()
    embedder_factory = embedder_factory or Embedder
    path = store_path(db_path)
    if not path.exists() and not create:
        return {"status": "disabled", "reason": "no embedding store", "store": str(path)}
    if not aos_indexer.runtime_db_path(db_path).exists():
        return {"status": "skipped", "reason": "no keyword index", "store": str(path)}
    with _refresh_lock(path):
        store = _connect_store(path)
        try:
            eligible = _eligible_rows(db_path)
            changed, removed = _plan(store, eligible)
            result = {"status": "current", "eligible": len(eligible), "embedded": 0, "removed": len(removed),
                      "model_loaded": False, "store": str(path)}
            if changed:
                try:
                    with embedder_factory() as embedder:
                        _write(store, embedder, eligible, changed, removed)
                        result.update(model_loaded=True, model_load_ms=embedder.load_ms, chunks=embedder.texts_embedded)
                except EmbeddingUnavailable as exc:
                    return {**result, "status": "unavailable", "reason": str(exc), "pending": len(changed)}
                result.update(status="refreshed", embedded=len(changed))
            elif removed:
                _write(store, None, eligible, [], removed)
                result["status"] = "refreshed"
        finally:
            store.close()
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def safe_refresh(db_path: Path | None = None) -> dict:
    """refresh() for write hooks: never raises, so a Brain write is never failed by its derived index."""
    try:
        return refresh(db_path)
    except Exception as exc:  # the next refresh or search catches up
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[-500:]}



_QUERY_LOCK = threading.Lock()
_QUERY_EMBEDDER: Embedder | None = None


@contextlib.contextmanager
def query_embedder():
    """Reuse the existing ONNX session for repeated David lookups in the same process."""
    global _QUERY_EMBEDDER
    with _QUERY_LOCK:
        for name, expected in MODEL_FILES_SHA256.items():
            _verify_model_file(MODEL_DIR / name, expected)
        if _QUERY_EMBEDDER is None:
            _QUERY_EMBEDDER = Embedder()
        yield _QUERY_EMBEDDER


def vector_ranking(query: str, db_path: Path | None = None, *, embedder_factory=None, reuse_model: bool = False) -> dict:
    """Pointers ordered by cosine similarity to the query (best chunk per document).

    Returns {"used": False, "reason": ...} when the store or model is unavailable. A search
    also embeds up to SEARCH_CATCH_UP_MAX stale rows with the model it has already loaded.
    """
    embedder_factory = embedder_factory or (query_embedder if reuse_model else Embedder)
    path = store_path(db_path)
    if not path.exists():
        return {"used": False, "reason": "no embedding store"}
    try:
        import numpy as np
    except ImportError as exc:
        return {"used": False, "reason": f"embedding runtime not installed: {exc}"}
    try:
        with embedder_factory() as embedder:
            catch_up = {"stale": 0, "embedded": 0}
            eligible = _eligible_rows(db_path)
            store = _connect_store(path)
            try:
                changed, removed = _plan(store, eligible)
                catch_up["stale"] = len(changed) + len(removed)
                if (changed or removed) and len(changed) <= SEARCH_CATCH_UP_MAX:
                    with _refresh_lock(path):
                        eligible = _eligible_rows(db_path)
                        changed, removed = _plan(store, eligible)
                        _write(store, embedder, eligible, changed, removed)
                        catch_up["embedded"] = len(changed)
                rows = store.execute("SELECT path, vector FROM vectors").fetchall()
            finally:
                store.close()
            query_vector = embedder.embed([QUERY_INSTRUCTION + query])[0]
            load_ms = embedder.load_ms
    except EmbeddingUnavailable as exc:
        return {"used": False, "reason": str(exc)}
    if not rows:
        return {"used": False, "reason": "embedding store is empty"}
    matrix = np.frombuffer(b"".join(row["vector"] for row in rows), dtype=np.float32).reshape(len(rows), -1)
    scores = matrix @ query_vector
    best: dict[str, float] = {}
    for row, score in zip(rows, scores.tolist()):
        if row["path"] in eligible and score > best.get(row["path"], -2.0):
            best[row["path"]] = score
    ranking = sorted(best.items(), key=lambda item: (-item[1], item[0]))
    return {"used": True, "ranking": ranking, "model_load_ms": load_ms, "catch_up": catch_up}


def status(db_path: Path | None = None) -> dict:
    path = store_path(db_path)
    if not path.exists():
        return {"exists": False, "store": str(path)}
    store = _connect_store(path)
    try:
        docs = store.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunks = store.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        version = store.execute("SELECT value FROM meta WHERE key='embed_version'").fetchone()
        changed, removed = _plan(store, _eligible_rows(db_path))
    finally:
        store.close()
    return {"exists": True, "store": str(path), "docs": docs, "chunks": chunks,
            "embed_version": version[0] if version else None, "stale": len(changed), "orphaned": len(removed),
            "model": MODEL_NAME, "model_dir": str(MODEL_DIR), "threads": THREADS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local embeddings for hybrid Business Brain search")
    sub = parser.add_subparsers(dest="command", required=True)
    rebuild = sub.add_parser("rebuild", help="create the store if needed and embed every new or changed row")
    rebuild.add_argument("--dry-run", action="store_true", help="report what would be embedded; load nothing")
    sub.add_parser("refresh", help="embed new or changed rows (no-op without a store)")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "rebuild" and args.dry_run:
        eligible = _eligible_rows(None)
        path = store_path()
        if path.exists():
            store = _connect_store(path)
            try:
                changed, removed = _plan(store, eligible)
            finally:
                store.close()
        else:
            changed, removed = sorted(eligible), []
        areas: dict[str, int] = {}
        for pointer in changed:
            area = "historical_note" if HISTORICAL_NOTE_RE.match(pointer) else pointer.split(":", 1)[1].split("/", 1)[0]
            areas[area] = areas.get(area, 0) + 1
        payload = {"dry_run": True, "store": str(path), "store_exists": path.exists(), "eligible": len(eligible),
                   "would_embed": len(changed), "would_remove": len(removed), "by_area": areas,
                   "chunks": sum(len(chunks_for(*eligible[p], p)) for p in changed)}
    elif args.command == "rebuild":
        payload = refresh(create=True)
    elif args.command == "refresh":
        payload = refresh()
    else:
        payload = status()
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") not in {"unavailable", "failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
