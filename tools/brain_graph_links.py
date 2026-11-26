"""Automatic, corroborated suggested links between Business Brain notes (derived data only).

When a note is written (brain_memory.write_transaction) or changed outside a transaction (the Brain
Git sweep's stat-only check), after_brain_change():
  1. applies the change to the published Graphify graph incrementally (BusinessBrainGraphService);
  2. (sweep only) refreshes the Search row, Entity Index and embeddings the write path already
     refreshes for a transaction;
  3. drops every suggested link touching the changed notes, then for each changed note still present
     takes its top LINK_TOP_K neighbours by local ONNX similarity (tools/aos_embeddings.py,
     BAAI/bge-small-en-v1.5) within the same client scope, at or above LINK_MIN_SIMILARITY;
  4. keeps a neighbour only when the Entity Index, ICM classification or source metadata corroborate
     it, types the link, and stores it with status "suggested";
  5. exports the links to the Graphify namespace (suggested/suggested_links.json), where graph
     retrieval reads them as one-hop suggestions.

Typing (vocabulary: about_person, about_company, about_project, decision_about, advice_on,
evidence_for, same_source, related_to). Produced here:
  same_source    both notes come from the same Memory Ingest package / source folder (source metadata)
  decision_about one note is ICM-classified explicit_decision and both carry the same person or
                 prospect entity in declared metadata (the decision names who it is about)
  related_to     similarity plus a shared Entity Index entity
Not produced, because no evidence for that meaning exists in the index today: about_person /
about_company / about_project (endpoints would be entities, which the Entity Index already links),
advice_on (no ICM advice class), evidence_for (needs an explicit claim/support pointer). Similarity
alone never creates a link. Links never touch canonical notes, never cross client scopes and never
change a note's authority; each carries both endpoints' authority labels.

Revisit: when the ICM gains an advice class or notes carry explicit evidence pointers. · Last touched: 2026-11-08.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import aos_embeddings  # noqa: E402
import aos_indexer  # noqa: E402

LINK_MIN_SIMILARITY = 0.80  # bge-small cosine, best chunk pair; live top-5 neighbour median is 0.856
LINK_TOP_K = 3
RELATION_VOCABULARY = ("about_person", "about_company", "about_project", "decision_about", "advice_on",
                       "evidence_for", "same_source", "related_to")
PRODUCED_RELATIONS = ("decision_about", "same_source", "related_to")
SYMMETRIC_RELATIONS = {"same_source", "related_to"}
STRONG_ENTITY_TYPES = {"person", "prospect"}
METADATA_BASES = {"card entities metadata", "entity_key metadata", "participants metadata", "prospect note"}
STATUS_SUGGESTED = "suggested"
EXPORT_NAME = "suggested_links.json"
BRAIN = "business_brain:"
TOKEN_USAGE_TEXT = "Token usage: no agent invocation"

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_suggested_links (
    source_path TEXT NOT NULL, target_path TEXT NOT NULL, relation TEXT NOT NULL, status TEXT NOT NULL,
    similarity REAL NOT NULL, confidence TEXT NOT NULL, reasons TEXT NOT NULL, evidence TEXT NOT NULL,
    source_sha256 TEXT NOT NULL, target_sha256 TEXT NOT NULL, client_scope TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (source_path, target_path)
);
CREATE INDEX IF NOT EXISTS idx_graph_suggested_links_target ON graph_suggested_links(target_path);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _graph_module():
    try:
        from dashboard.backend import business_brain_graph
    except ImportError:  # backend runs with dashboard/backend on sys.path as flat modules
        import business_brain_graph
    return business_brain_graph


def _pointer(value: str) -> str:
    return value if value.startswith(BRAIN) else f"{BRAIN}{value}"


def _file_sha(vault: Path, pointer: str) -> str | None:
    try:
        return hashlib.sha256((vault / pointer[len(BRAIN):]).read_bytes()).hexdigest()
    except OSError:
        return None


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(aos_indexer.runtime_db_path(db_path)), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _documents(conn: sqlite3.Connection) -> dict[str, str]:
    """pointer -> client_scope for every indexed Business Brain note."""
    return {row["path"]: row["client_scope"] for row in conn.execute(
        "SELECT path, client_scope FROM documents WHERE source = 'business_brain'")}


def _entities(conn: sqlite3.Connection, pointers: Iterable[str]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    values = sorted(set(pointers))
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        try:
            rows = conn.execute(
                f"SELECT l.path, l.entity_id, l.basis, e.display_name, e.entity_type FROM entity_links l "
                f"JOIN entities e USING (entity_id) WHERE l.path IN ({','.join('?' for _ in chunk)})", chunk).fetchall()
        except sqlite3.OperationalError:  # Entity Index not built yet
            return {}
        for row in rows:
            out.setdefault(row["path"], {})[row["entity_id"]] = {
                "name": row["display_name"], "type": row["entity_type"], "basis": row["basis"]}
    return out


def _icm(conn: sqlite3.Connection, pointers: Iterable[str]) -> dict[str, dict]:
    values = sorted(set(pointers))
    if not values:
        return {}
    try:
        rows = conn.execute(
            f"SELECT path, role, knowledge_type, canonical, package_id FROM brain_documents WHERE path IN ({','.join('?' for _ in values)})",
            values).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {row["path"]: dict(row) for row in rows}


def _source_folder(pointer: str) -> str:
    parts = pointer[len(BRAIN):].split("/")
    return "/".join(parts[:3]) if len(parts) >= 4 and parts[0] == "sources" and parts[1] == "historical_calls" else ""


def _vectors(path: Path):
    import numpy as np
    if not path.exists():
        return None, {}
    store = sqlite3.connect(str(path), timeout=30)
    try:
        rows = store.execute("SELECT path, vector FROM vectors ORDER BY path, idx").fetchall()
    finally:
        store.close()
    if not rows:
        return None, {}
    matrix = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(len(rows), -1)
    index: dict[str, list[int]] = {}
    for position, row in enumerate(rows):
        index.setdefault(row[0], []).append(position)
    return matrix, index


def classify_pair(source: str, target: str, *, similarity: float, entities: dict, icm: dict) -> dict | None:
    """Corroborate and type one candidate pair; None when nothing but similarity supports it."""
    left, right = entities.get(source, {}), entities.get(target, {})
    shared = sorted(set(left) & set(right))
    strong = [eid for eid in shared if left[eid]["type"] in STRONG_ENTITY_TYPES]
    info_s, info_t = icm.get(source, {}), icm.get(target, {})
    reasons: list[str] = []
    evidence: dict[str, Any] = {"entity_links": [], "icm": {
        "source": info_s.get("knowledge_type") or "unclassified", "target": info_t.get("knowledge_type") or "unclassified"}}
    for eid in shared:
        evidence["entity_links"].append({"entity_id": eid, "name": left[eid]["name"], "type": left[eid]["type"],
                                         "source_basis": left[eid]["basis"], "target_basis": right[eid]["basis"]})
        reasons.append(f"shared {left[eid]['type']} entity: {left[eid]['name']} (Entity Index)")
    package = info_s.get("package_id") or ""
    same_package = bool(package) and package == (info_t.get("package_id") or "")
    folder = _source_folder(source)
    same_folder = bool(folder) and folder == _source_folder(target)
    if same_package:
        evidence["package_id"] = package
        reasons.insert(0, f"same Memory Ingest package: {package}")
    elif same_folder:
        evidence["source_folder"] = folder
        reasons.insert(0, f"same source folder: {folder}")
    if not reasons:
        return None
    relation, src, dst = "related_to", source, target
    if same_package or same_folder:
        relation = "same_source"
    else:
        for decision, other in ((source, target), (target, source)):
            if (icm.get(decision, {}).get("knowledge_type") == "explicit_decision"
                    and any(entities[decision][eid]["basis"] in METADATA_BASES and entities[other][eid]["basis"] in METADATA_BASES
                            for eid in strong)):
                relation, src, dst = "decision_about", decision, other
                reasons.insert(0, "ICM classifies the source note as an explicit decision naming the shared entity in metadata")
                break
    if relation in SYMMETRIC_RELATIONS and dst < src:
        src, dst = dst, src
    confidence = "medium" if (strong or same_package or same_folder) else "low"
    authority = {pointer: ("canonical" if icm.get(pointer, {}).get("canonical") else "historical_evidence"
                           if icm.get(pointer, {}).get("knowledge_type") == "historical_evidence" else "noncanonical")
                 for pointer in (src, dst)}
    evidence["authority"] = {"source": authority[src], "target": authority[dst]}
    return {"source_path": src, "target_path": dst, "relation": relation, "status": STATUS_SUGGESTED,
            "similarity": round(similarity, 4), "confidence": confidence, "reasons": reasons, "evidence": evidence}


def update_links(pointers: Iterable[str], *, db_path: Path | None = None, embed_db: Path | None = None,
                 vault: Path | None = None, graphify_root: Path | None = None) -> dict[str, Any]:
    """Re-derive the suggested links of the given notes (added, edited or deleted). The Graphify
    export goes to graphify_root, or to the live graph only when db_path is the live index."""
    started = time.perf_counter()
    vault = Path(vault or aos_indexer.BUSINESS_BRAIN_ROOT)
    changed = sorted({_pointer(str(p)) for p in pointers})
    conn = connect(db_path)
    try:
        removed = 0
        for pointer in changed:
            removed += conn.execute("DELETE FROM graph_suggested_links WHERE source_path = ? OR target_path = ?",
                                    (pointer, pointer)).rowcount
        docs = _documents(conn)
        live = [p for p in changed if p in docs and aos_embeddings.is_embeddable(p)]
        created: list[dict] = []
        uncorroborated = 0
        if live:
            matrix, index = _vectors(Path(embed_db) if embed_db else aos_embeddings.store_path(db_path))
            if matrix is not None:
                candidates_all = [q for q in index if q in docs and aos_embeddings.is_embeddable(q)]
                entities = _entities(conn, candidates_all)
                icm = _icm(conn, candidates_all)
                for pointer in live:
                    if pointer not in index:
                        continue
                    scope = docs[pointer]
                    scores = matrix[index[pointer]] @ matrix.T
                    best = []
                    for other in candidates_all:
                        if other == pointer or docs.get(other) != scope:
                            continue
                        value = float(scores[:, index[other]].max())
                        if value >= LINK_MIN_SIMILARITY:
                            best.append((value, other))
                    best.sort(key=lambda item: (-item[0], item[1]))
                    for value, other in best[:LINK_TOP_K]:
                        link = classify_pair(pointer, other, similarity=value, entities=entities, icm=icm)
                        if link is None:
                            uncorroborated += 1
                            continue
                        source_sha, target_sha = _file_sha(vault, link["source_path"]), _file_sha(vault, link["target_path"])
                        if not source_sha or not target_sha:
                            continue
                        link.update(source_sha256=source_sha, target_sha256=target_sha, client_scope=scope, created_at=_now())
                        conn.execute("INSERT OR REPLACE INTO graph_suggested_links VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                            link["source_path"], link["target_path"], link["relation"], link["status"], link["similarity"],
                            link["confidence"], json.dumps(link["reasons"]), json.dumps(link["evidence"], sort_keys=True),
                            source_sha, target_sha, scope, link["created_at"]))
                        created.append(link)
        conn.commit()
        exported = export(conn, graphify_root=graphify_root) if (graphify_root or db_path is None) else {"status": "skipped"}
    finally:
        conn.close()
    return {"status": "updated", "notes": len(changed), "removed": removed, "created": len(created),
            "uncorroborated_skipped": uncorroborated, "links": [
                {k: link[k] for k in ("source_path", "target_path", "relation", "similarity", "confidence")} for link in created],
            "export": exported, "duration_ms": round((time.perf_counter() - started) * 1000, 1), "token_usage_text": TOKEN_USAGE_TEXT}


def rows(conn: sqlite3.Connection, *, pointers: Iterable[str] | None = None, client_scope: str | None = None) -> list[dict]:
    sql = "SELECT * FROM graph_suggested_links"
    params: list[Any] = []
    clauses = []
    if pointers is not None:
        values = sorted(set(pointers))
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        clauses.append(f"(source_path IN ({marks}) OR target_path IN ({marks}))")
        params += values + values
    if client_scope is not None:
        clauses.append("client_scope = ?")
        params.append(client_scope)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    out = []
    for row in conn.execute(sql + " ORDER BY source_path, target_path", params):
        record = dict(row)
        record["reasons"] = json.loads(record["reasons"])
        record["evidence"] = json.loads(record["evidence"])
        out.append(record)
    return out


def export(conn: sqlite3.Connection, *, graphify_root: Path | None = None) -> dict[str, Any]:
    """Write the links into the live Graphify namespace as derived data beside the graph."""
    graph = _graph_module()
    root = Path(graphify_root or graph.DEFAULT_GRAPHIFY_ROOT) / "document_graphs" / graph.DEFAULT_NAMESPACE / "suggested"
    links = rows(conn)
    payload = {"schema_version": 1, "namespace": graph.DEFAULT_NAMESPACE, "derived": True, "authoritative": False,
               "status_vocabulary": [STATUS_SUGGESTED], "relation_vocabulary": list(RELATION_VOCABULARY),
               "produced_relations": list(PRODUCED_RELATIONS), "min_similarity": LINK_MIN_SIMILARITY, "top_k": LINK_TOP_K,
               "embedding_model": aos_embeddings.MODEL_NAME, "links": links, "token_usage_text": TOKEN_USAGE_TEXT}
    root.mkdir(parents=True, exist_ok=True)
    target = root / EXPORT_NAME
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "links": len(links)}


def rebuild_all(*, db_path: Path | None = None, embed_db: Path | None = None, graphify_root: Path | None = None) -> dict[str, Any]:
    """Recompute every note's links (nightly oracle / first activation)."""
    conn = connect(db_path)
    try:
        pointers = [p for p in _documents(conn) if aos_embeddings.is_embeddable(p)]
        conn.execute("DELETE FROM graph_suggested_links")
        conn.commit()
    finally:
        conn.close()
    return update_links(pointers, db_path=db_path, embed_db=embed_db, graphify_root=graphify_root)


def _refresh_derived_indexes(changed: list[str], removed: list[str]) -> None:
    """What write_transaction does after a commit, for edits that bypassed it."""
    import aos_entity_index
    vault = aos_indexer.BUSINESS_BRAIN_ROOT
    for relative in changed:
        aos_indexer.index_one(str(vault / relative))
    for relative in removed:
        aos_indexer.remove_one(str(vault / relative))
    aos_entity_index.ensure_current()
    aos_embeddings.safe_refresh()


def after_brain_change(paths: Iterable[str], *, origin: str = "write") -> dict[str, Any]:
    """Graph + links update for changed Brain notes. Never raises: derived data must never fail a
    Brain write; the next sweep, build or nightly run catches up."""
    result: dict[str, Any] = {"origin": origin}
    relatives = sorted({str(p)[len(BRAIN):] if str(p).startswith(BRAIN) else str(p) for p in paths})
    try:
        graph = _graph_module().BusinessBrainGraphService()
        result["graph"] = graph.apply_changes(relatives, origin=origin)
    except Exception as exc:
        result["graph"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    try:
        result["links"] = update_links(relatives)
    except Exception as exc:
        result["links"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return result


def sweep_changes() -> dict[str, Any]:
    """Stat-only detection for edits outside write_transaction (called by the Brain Git sweep)."""
    try:
        graph = _graph_module().BusinessBrainGraphService()
        detected = graph.detect_changes()
    except Exception as exc:
        return {"graph": {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}}
    out: dict[str, Any] = {"graph": detected}
    if detected.get("status") == "success":
        changed, removed = list(detected.get("changed") or []), list(detected.get("removed") or [])
        try:
            _refresh_derived_indexes(changed, removed)
            out["links"] = update_links(changed + removed)
        except Exception as exc:
            out["links"] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("rebuild", "sweep", "show"))
    parser.add_argument("pointer", nargs="?")
    args = parser.parse_args(argv)
    if args.command == "rebuild":
        result = rebuild_all()
        result.pop("links", None)
    elif args.command == "sweep":
        result = sweep_changes()
    else:
        conn = connect()
        try:
            result = {"links": rows(conn, pointers=[_pointer(args.pointer)] if args.pointer else None)}
        finally:
            conn.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
