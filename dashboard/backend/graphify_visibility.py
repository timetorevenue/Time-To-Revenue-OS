"""Read-only Dashboard coverage over existing indexes and authorised Brain files.

Revisit: when the scope registry or published graph contract changes. Last touched: 2026-11-09.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import aos_indexer
import business_brain

SYSTEM_REPOSITORIES = (
    ("TTROS", "owner-gh-account/TTROS", "agentic_os_live"),
    ("Business Brain", "owner-gh-account/ttros-business-brain", "business_brain"),
)


def _stamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat() if value is not None else None


def authorised_files(vault, registry):
    """Enumerate only exact global reader identities and explicitly allowed subtrees.

    Never walk the vault root, follow symlinks, or descend excluded directories.
    No file bodies are read here. The Memory reader's scope gate remains authoritative.
    """
    registry.resolve_scope("global")
    rules = registry.data["scopes"]["global"].get("search_source_identities", [])
    candidates = {}
    roots = set()
    exact = set()
    record = registry.data["scopes"]["global"]
    reader_exact = set(record.get("brain_pointers", []))
    reader_prefixes = tuple(record.get("brain_pointer_prefixes", []))

    def allowed(pointer):
        try:
            canonical = business_brain.lexical_business_brain_pointer(pointer)
            return (canonical in reader_exact or canonical.startswith(reader_prefixes)) and registry.scope_for_search_identity("business_brain", canonical) == "global"
        except ValueError:
            return False

    for rule in rules:
        if rule.get("source") != "business_brain":
            continue
        exact.update(rule.get("paths", []))
        prefix = rule.get("path_prefix", "")
        if prefix.startswith("business_brain:") and prefix.endswith("/"):
            relative = prefix.removeprefix("business_brain:").rstrip("/")
            if relative and not any(p in {".", ".."} for p in relative.split("/")):
                roots.add(relative)
    # Resolve each allowed root once. Descendants come only from non-symlink
    # scandir entries; repeatedly resolving every ancestor on DrvFS costs ~1 min.
    for relative in sorted(roots):
        if any(relative.startswith(other + "/") for other in roots if other != relative):
            continue
        root = vault / relative
        if root.resolve() != root.absolute() or aos_indexer.is_excluded(relative):
            continue
        pending = [root]
        while pending:
            parent = pending.pop()
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if entry.name.startswith(".") or entry.is_symlink():
                            continue
                        path = Path(entry.path)
                        rel = path.relative_to(vault).as_posix()
                        if aos_indexer.is_excluded(rel):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            pointer = "business_brain:" + rel
                            if allowed(pointer):
                                candidates[pointer] = path
            except FileNotFoundError:
                continue
    # Exact identities outside a subtree still pass the full reader resolver.
    for pointer in sorted(exact - candidates.keys()):
        if not allowed(pointer):
            continue
        try:
            canonical = registry.validate_brain_pointer("global", pointer)
            path = vault / canonical.removeprefix("business_brain:")
            if path.resolve() == path.absolute() and path.is_file():
                candidates[canonical] = path
        except (ValueError, OSError):
            continue
    result = dict(sorted(candidates.items()))
    return result


def snapshot(*, vault, registry, graph_root, db_path, repositories):
    files = authorised_files(Path(vault), registry)
    warnings = []
    indexed, entity_paths, suggestions = {}, set(), []
    source_counts, source_latest = Counter(), {}
    configured = {r["source"] for r in registry.data["scopes"]["global"].get("search_source_identities", [])}
    index_available = False
    entity_built = None
    entity_available = False
    try:
        with closing(aos_indexer.connect(Path(db_path), readonly=True)) as conn:
            for row in conn.execute("SELECT path, source, title, mtime, size_bytes, indexed_at FROM documents WHERE client_scope = 'global'"):
                if registry.scope_for_search_identity(row["source"], row["path"]) != "global":
                    continue
                source_counts[row["source"]] += 1
                source_latest[row["source"]] = max(source_latest.get(row["source"], ""), row["indexed_at"])
                if row["source"] == "business_brain" and row["path"] in files:
                    indexed[row["path"]] = dict(row)
            index_available = True
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "brain_documents" in tables:
                entity_available = True
                entity_paths = {row[0] for row in conn.execute("SELECT path FROM brain_documents") if row[0] in files}
            if "entity_index_meta" in tables:
                row = conn.execute("SELECT value FROM entity_index_meta WHERE key='built_at'").fetchone()
                entity_built = row[0] if row else None
            if "graph_suggested_links" in tables:
                suggestions = [dict(row) for row in conn.execute(
                    "SELECT source_path, target_path, relation, status, reasons, source_sha256, target_sha256 FROM graph_suggested_links WHERE client_scope='global' AND status='suggested'")
                    if row["source_path"] in files and row["target_path"] in files]
    except sqlite3.Error:
        warnings.append("Search index unavailable or incomplete; coverage is unknown where indicated.")

    graph_path = Path(graph_root) / "document_graphs/ttros-business-brain/published/graph.json"
    graph, graph_built = None, None
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
            raise ValueError("invalid projection")
        graph_built = _stamp(graph_path.stat().st_mtime)
    except (OSError, ValueError, AttributeError):
        graph = None
        warnings.append("Business Brain graph is unavailable; document inventory remains usable.")
    nodes = {n["id"]: n for n in (graph or {}).get("nodes", [])
             if n.get("kind") == "note" and n.get("source_path") in files}
    activities = {n["id"]: n for n in (graph or {}).get("nodes", []) if n.get("kind") == "activity"}
    by_pointer = {n["source_path"]: n for n in nodes.values()}
    suggested_paths = {s[key] for s in suggestions for key in ("source_path", "target_path")}
    hashes = {}
    docs = {}
    for pointer, path in files.items():
        try:
            stat = path.stat()
            node, search = by_pointer.get(pointer), indexed.get(pointer)
            if node or pointer in suggested_paths:
                hashes[pointer] = hashlib.sha256(path.read_bytes()).hexdigest()
            docs[pointer] = {
                "path": pointer, "title": (search or {}).get("title") or (node or {}).get("title") or path.name,
                "folder": path.relative_to(vault).parent.as_posix(), "modified": _stamp(stat.st_mtime),
                "indexed_at": (search or {}).get("indexed_at"),
                "search_status": ("current" if search["mtime"] == stat.st_mtime and search["size_bytes"] == stat.st_size else "stale") if search else ("unindexed" if index_available else "unknown"),
                "entity_indexed": pointer in entity_paths if entity_available else None,
                "graph_status": ("current" if node.get("content_sha256") == hashes.get(pointer) else "stale") if node else ("unindexed" if graph is not None else "unknown"),
                "connections": [], "suggestions": [],
            }
        except OSError:
            warnings.append("An authorised document changed during inspection; refresh to retry.")
    edge_count = 0
    for edge in (graph or {}).get("edges", []):
        left, right = nodes.get(edge.get("source")), nodes.get(edge.get("target"))
        # Existing activity references are shown only beside an authorised note.
        activity = activities.get(edge.get("source")) or activities.get(edge.get("target"))
        note = left or right
        if activity and note and note["source_path"] in docs:
            docs[note["source_path"]]["connections"].append({
                "title": activity.get("title") or activity["id"], "activity_id": activity["id"], "relation": edge.get("relation"),
                "direction": "incoming" if right else "outgoing", "edge_kind": edge.get("edge_kind"),
                "confidence": edge.get("confidence"), "extractor": edge.get("extractor"),
            })
        # Heading containment is not a document relationship.
        if not left or not right or left["source_path"] not in docs or right["source_path"] not in docs:
            continue
        edge_count += 1
        for here, there, direction in ((left, right, "outgoing"), (right, left, "incoming")):
            docs[here["source_path"]]["connections"].append({
                "path": there["source_path"], "relation": edge.get("relation"), "direction": direction,
                "edge_kind": edge.get("edge_kind"), "confidence": edge.get("confidence"),
                "extractor": edge.get("extractor"),
            })
    for link in suggestions:
        left, right = link["source_path"], link["target_path"]
        if left not in docs or right not in docs:
            continue
        stale = hashes.get(left) != link["source_sha256"] or hashes.get(right) != link["target_sha256"]
        try:
            reasons = json.loads(link["reasons"])
        except (ValueError, TypeError):
            reasons = []
        for here, there in ((left, right), (right, left)):
            docs[here]["suggestions"].append({"path": there, "relation": link["relation"], "status": "suggested",
                                             "stale": stale, "reasons": reasons})
    documents = list(docs.values())
    coverage = []
    for label, identity, source in SYSTEM_REPOSITORIES:
        repo = next((r for r in repositories if r["id"].lower() == identity.lower()), None)
        coverage.append({"label": label, "repository": identity, "source": source,
                         "search_documents": source_counts[source] if index_available else None,
                         "code_graph": "published snapshot" if repo else "not indexed",
                         "graph_commit": repo.get("commit_hash") if repo else None,
                         "graph_built": repo.get("timestamp") if repo else None,
                         "document_graph": "published" if source == "business_brain" and graph is not None else "not published"})
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "warnings": sorted(set(warnings)),
        "sources": [{"source": source, "documents": source_counts[source] if index_available else None,
                     "last_indexed": source_latest.get(source)} for source in sorted(configured | set(source_counts))],
        "repository_coverage": coverage,
        "business_brain": {"documents": documents, "total": len(documents),
                           "search_indexed": len(indexed) if index_available else None,
                           "search_inventory_gap": source_counts["business_brain"] - len(indexed) if index_available else None,
                           "search_stale": sum(d["search_status"] == "stale" for d in documents) if index_available else None,
                           "graph_documents": sum(d["graph_status"] in {"current", "stale"} for d in documents) if graph is not None else None,
                           "graph_stale": sum(d["graph_status"] == "stale" for d in documents) if graph is not None else None,
                           "unlinked": sum(not d["connections"] for d in documents) if graph is not None else None,
                           "document_edges": edge_count if graph is not None else None,
                           "suggested_edges": sum(len(d["suggestions"]) for d in documents) // 2,
                           "graph_built": graph_built, "entity_built": entity_built,
                           "entity_documents": len(entity_paths) if entity_available else None},
        "provenance": "Global scope registry; authorised file metadata; Search/Entity Index; published Business Brain graph; existing suggested links. No rebuild or model invocation.",
    }
