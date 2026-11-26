"""Failure-preserving Graphify projection for the canonical Business Brain.

Graphify is a derived selector, never the authority. Published targets contain
canonical logical paths and scores only; note bodies are never returned.

Revisit: when Graphify's Markdown/manifest contracts or the source-graph machine block change. · Last touched: 2026-11-12.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import sqlite3
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import business_brain
try:
    # Qualified first: TOOLS_DIR is unconditionally on sys.path by this point, so a bare-first
    # try always succeeds and never falls through, which is what let this module's own
    # `except ClientScopeError` bind to a second, distinct class from every other caller
    # (STEP X3 root cause). Qualified succeeds whenever this runs as part of the `dashboard`
    # package (repo root already on sys.path), matching that identity.
    from tools.business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry
except ModuleNotFoundError:
    # Standalone CLI use (`python3 dashboard/backend/business_brain_graph.py ...`): repo root
    # isn't on sys.path there, only TOOLS_DIR, so fall back to the bare name.
    from business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry

# A caller may still pass in an explicit ClientScopeRegistry built from the *other* spelling
# (e.g. a test fixture bare-importing business_brain_scope directly) -- catch both identities
# so add_candidate()'s scope-filter safety net works regardless of which one raised, without
# weakening what counts as out-of-scope (STEP X4).
try:
    from business_brain_scope import ClientScopeError as _OtherSpellingClientScopeError
except ModuleNotFoundError:
    _OtherSpellingClientScopeError = ClientScopeError
if _OtherSpellingClientScopeError is ClientScopeError:
    _CLIENT_SCOPE_ERRORS: tuple[type, ...] = (ClientScopeError,)
else:
    _CLIENT_SCOPE_ERRORS = (ClientScopeError, _OtherSpellingClientScopeError)
from validate_business_brain import (
    NONCANONICAL_PREFIXES, WIKI_LINK_RE, canonical_markdown, canonical_wiki_target, parse_frontmatter, resolve_wiki_target,
)


TOKEN_USAGE_TEXT = "Token usage: no agent invocation"
DEFAULT_GRAPHIFY_ROOT = (Path.home() / "graphify-brain")
DEFAULT_NAMESPACE = "ttros-business-brain"
DEFAULT_GRAPHIFY_PYTHON = (Path.home() / ".local/share/pipx/venvs/graphifyy/bin/python")
WORD_RE = re.compile(r"[a-z0-9]+")
AOS_ACTIVITY_RE = re.compile(r"\bAOS-[0-9]{4}-[0-9]{4,}\b", re.IGNORECASE)
TARGETED_ENTITY_TYPES = frozenset({"person", "prospect", "client", "offer", "commitment", "decision", "project", "experiment"})
SEMANTIC_RELATIONS = frozenset({"touched"})
# Revision-3 source notes carry a TTROS:MACHINE source-graph block (tools/source_interpret.py): the entities a note is
# about and the typed relationships the source states, each with an offset reference and a lexical evidence status.
# They project as DERIVED, non-authoritative entity nodes and edges with the stating note attached; an unevidenced
# ("cited"/"failed") or untyped ("related_to") relationship never becomes an edge.
SOURCE_GRAPH_EXTRACTOR = "ttros.source-graph"
DERIVED_NODE_KINDS = frozenset({"activity", "entity"})
ENTITY_EXCLUDED_PATHS = frozenset({
    "operating_context/current_priorities.md",
    "operating_context/executive_view.md",
    "operating_context/open_loops.md",
})


# Stage 1 (2026-11-08): rank on string-only scope rules, run the full scope check and a sha256
# check only on the returned notes. TTROS_GRAPH_LEXICAL_SCOPE: unset/0 = original behaviour,
# "shadow" = original result returned while the new path runs beside it and differences are
# logged, 1/on = new path (falls back to the original path if failures exceed the overscan).
SCOPE_OVERSCAN = 3
SHADOW_LOG_DEFAULT = Path(__file__).resolve().parents[2] / "logs" / "graph_scope_shadow.jsonl"


def lexical_scope_mode() -> str:
    value = os.environ.get("TTROS_GRAPH_LEXICAL_SCOPE", "").strip().lower()
    if value in {"1", "on", "true"}:
        return "on"
    if value == "shadow":
        return "shadow"
    return "off"


def _shadow_log(row: dict[str, Any]) -> None:
    try:
        path = Path(os.environ.get("TTROS_GRAPH_SHADOW_LOG") or SHADOW_LOG_DEFAULT)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": _iso_now(), **row}, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


# Incremental Graphify (2026-11-08). Per-note state lives in the Search SQLite (graph_* tables):
# revision, the sha256 of the published source manifest it describes, and each note's sha256, stat,
# raw Graphify counts and wiki-link candidates. A Brain write (brain_memory.write_transaction) or the
# Brain Git sweep's stat-only check calls apply_changes() for the changed notes only, which rewrites
# exactly what a full build would produce (tests assert byte equality). TTROS_GRAPH_INCREMENTAL=1 lets
# status() trust that state instead of hashing the whole vault; it needs TTROS_GRAPH_LEXICAL_SCOPE=1
# too, because that path re-runs validate_graph_target and a sha256 check on every returned note,
# which is what catches an out-of-band edit the sweep has not yet applied. Unset = full scan, as
# before. The nightly build stays the oracle: it rebuilds from scratch and records parity.
GRAPH_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_state (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (namespace, key));
CREATE TABLE IF NOT EXISTS graph_notes (
    namespace TEXT NOT NULL, relative_path TEXT NOT NULL, note_id TEXT NOT NULL, sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, raw_nodes INTEGER NOT NULL, raw_edges INTEGER NOT NULL,
    raw_refs INTEGER NOT NULL, PRIMARY KEY (namespace, relative_path)
);
CREATE TABLE IF NOT EXISTS graph_link_candidates (namespace TEXT NOT NULL, source_path TEXT NOT NULL, candidate TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_graph_link_candidates_candidate ON graph_link_candidates(namespace, candidate);
CREATE INDEX IF NOT EXISTS idx_graph_link_candidates_source ON graph_link_candidates(namespace, source_path);
"""


def incremental_mode() -> str:
    return "on" if os.environ.get("TTROS_GRAPH_INCREMENTAL", "").strip().lower() in {"1", "on", "true"} else "off"


def _live_state_db() -> Path:
    try:
        import aos_indexer
    except ImportError:  # pragma: no cover
        from tools import aos_indexer
    return aos_indexer.runtime_db_path(None)


_JSON_CACHE: dict[str, tuple[tuple[int, int, int], Any]] = {}


def _load_json_cached(path: Path) -> Any:
    """Parse a published artifact once per (inode, mtime_ns, size). Artifacts are only ever replaced
    atomically (new inode), so a changed file is always re-read. Callers must not mutate the result."""
    st = path.stat()
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _JSON_CACHE.get(str(path))
    if cached and cached[0] == key:
        return cached[1]
    value = json.loads(path.read_text(encoding="utf-8"))
    _JSON_CACHE[str(path)] = (key, value)
    return value


class BusinessBrainGraphError(RuntimeError):
    pass


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
    }


def targeted_entity_type(relative: str, fields: dict[str, str]) -> str | None:
    """Return a deliberate entity type, excluding narrative/navigation areas."""
    path = Path(relative)
    if (
        relative in ENTITY_EXCLUDED_PATHS
        or relative.startswith("sessions/")
        or path.name in {"README.md", "index.md", "MEMORY_INDEX.md"}
        or path.name.startswith("TTROS_")
    ):
        return None
    value = str(fields.get("type") or "").strip().lower()
    return value if value in TARGETED_ENTITY_TYPES else None


def frontmatter_sequence(text: str, field: str) -> tuple[str, ...]:
    """Read one top-level YAML sequence without widening the metadata parser."""
    if not text.startswith("---\n"):
        return ()
    end = text.find("\n---\n", 4)
    if end < 0:
        return ()
    lines = text[4:end].splitlines()
    values: list[str] = []
    collecting = False
    for line in lines:
        if not line.startswith((" ", "\t")):
            collecting = line.rstrip() == f"{field}:"
            continue
        if collecting:
            match = re.fullmatch(r"\s+-\s+([^#]+?)\s*", line)
            if match:
                values.append(match.group(1).strip().strip("'\""))
            elif line.strip():
                break
    return tuple(values)


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    return (edge["source"], edge["target"], edge["relation"], str(edge.get("stated_in") or ""))


def source_graph_projection(relative: str, record: dict[str, Any], title: str, body: str, *,
                            vault: Path, sha: str, cache: dict, registry: ClientScopeRegistry) -> tuple[list[dict], list[dict]]:
    """(entity nodes, derived edges) stated by one revision-3 source note's machine block; ([], []) otherwise."""
    if not relative.startswith("sources/historical_calls/") or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return [], []
    from source_passages import (PassageError, SOURCE_GRAPH_KEY, load_text,
                                 source_graph_block, source_relationship_evidence, split_lines)

    try:
        block = source_graph_block(body, sha)
        if not block:
            return [], []
        if block.get("relationships"):
            owner = registry.scope_for_search_identity("business_brain", record["source_path"])
            original = f"business_brain:sources/intake/records/{sha}.md"
            registry.resolve_brain_pointer(owner, original, root=vault)
            if sha not in cache:
                _, text = load_text(vault, sha, cache)
                cache[sha] = text, split_lines(text.encode("utf-8"))
            text, lines = cache[sha]
    except (PassageError, OSError, ValueError):
        return [], []
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def entity(key: Any) -> str | None:
        key = str(key or "")
        if not SOURCE_GRAPH_KEY.fullmatch(key):
            return None
        node_id = f"entity:{key}"
        nodes[node_id] = {"id": node_id, "kind": "entity", "title": key.split(":", 1)[1].replace("-", " ").title(),
                          "metadata": {"type": "entity", "entity_key": key}, "authoritative": False, "derived": True}
        return node_id

    common = {"confidence": "DERIVED", "edge_kind": "derived", "extractor": SOURCE_GRAPH_EXTRACTOR, "authoritative": False,
              "stated_by": "source", "stated_in": record["id"], "stated_in_path": record["source_path"]}
    for item in block.get("about") or []:
        node_id = entity(item.get("key")) if isinstance(item, dict) else None
        if node_id:
            name = str(item.get("name") or "")
            edges.append({"source": node_id, "target": record["id"], "relation": "about", **common, "entity_name": name,
                          "relationship_reason": f"{title} is about {name} (stated by the source; derived, not verified)"})
    for item in block.get("relationships") or []:
        try:
            evidence = source_relationship_evidence(item, sha, text, lines=lines)
        except (PassageError, ValueError):
            continue
        if not evidence:
            continue
        predicate = item["predicate"]
        subject, obj = entity(item.get("subject_key")), entity(item.get("object_key"))
        if not subject or not obj or subject == obj:
            continue
        names = str(item.get("subject") or ""), str(item.get("object") or "")
        edges.append({"source": subject, "target": obj, "relation": predicate, **common, "subject": names[0], "object": names[1],
                      **evidence,
                      "relationship_reason": f"{names[0]} {predicate.replace('_', ' ')} {names[1]}, as stated by the source in "
                                             f"{record['source_path']} ({evidence['ref']}); derived, not verified"})
    return list(nodes.values()), edges


def wiki_link_candidates(relative: str, body: str) -> set[str]:
    """Every note path one of this note's wiki links could resolve to (resolve_wiki_target tries
    the exact target, then the sibling path). A note whose links mention a path that appears or
    disappears is the only kind of unchanged note whose edges can change."""
    out: set[str] = set()
    for raw_target in WIKI_LINK_RE.findall(body):
        target = canonical_wiki_target(raw_target)
        if not target or target.startswith("/") or "\\" in target:
            continue
        out.add(target)
        out.add((Path(relative).parent / target).as_posix())
    return out


class BusinessBrainGraphService:
    def __init__(
        self,
        *,
        graphify_root: Path = DEFAULT_GRAPHIFY_ROOT,
        vault_root: Path | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        graphify_python: Path = DEFAULT_GRAPHIFY_PYTHON,
        extractor_script: Path | None = None,
        registry: ClientScopeRegistry | None = None,
        state_db: Path | None = None,
    ):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", namespace):
            raise BusinessBrainGraphError("namespace must be a safe lowercase slug")
        self.graphify_root = Path(graphify_root).resolve()
        self.vault_root = Path(vault_root or business_brain.BUSINESS_BRAIN_ROOT).resolve()
        self.namespace = namespace
        self.document_root = self.graphify_root / "document_graphs"
        self.namespace_root = self.document_root / namespace
        self.published = self.namespace_root / "published"
        self.history = self.namespace_root / ".history"
        self.receipts = self.namespace_root / "receipts"
        self.graphify_python = Path(graphify_python)
        self.extractor_script = extractor_script or TOOLS_DIR / "aos_graphify_markdown_extract.py"
        self.registry = registry or load_registry()
        # Incremental state goes in the live Search SQLite only for the live graph; any other graph
        # (tests, temp roots) gets state only when the caller passes its own database.
        if state_db is None and (
            self.graphify_root == DEFAULT_GRAPHIFY_ROOT.resolve()
            and namespace == DEFAULT_NAMESPACE
            and self.vault_root == Path(business_brain.BUSINESS_BRAIN_ROOT).resolve()
        ):
            state_db = _live_state_db()
        self.state_db = Path(state_db) if state_db else None

    def _published_target_allowlist(self) -> tuple[set[str], tuple[str, ...]]:
        allowed = set()
        prefixes = []
        denied = set(self.registry.data.get("denied_brain_pointers") or [])
        for record in self.registry.data.get("scopes", {}).values():
            if record.get("enabled") is not True:
                continue
            for target in record.get("graphify_targets") or []:
                if target.get("namespace") == self.namespace:
                    allowed.update(target.get("paths") or [])
                    prefixes.extend(str(value) for value in target.get("path_prefixes") or [])
        return allowed - denied, tuple(sorted(set(prefixes)))

    def _notes(self) -> list[Path]:
        allowed, prefixes = self._published_target_allowlist()
        denied = set(self.registry.data.get("denied_brain_pointers") or [])
        notes = []
        for path in canonical_markdown(self.vault_root):
            pointer = f"business_brain:{path.relative_to(self.vault_root).as_posix()}"
            if pointer not in denied and (pointer in allowed or any(pointer.startswith(prefix) for prefix in prefixes)):
                notes.append(path)
        if not notes:
            raise BusinessBrainGraphError("canonical Business Brain has no Markdown notes")
        return notes

    @staticmethod
    def _manifest_record(relative: str, raw: bytes) -> dict[str, Any]:
        fields, _body = parse_frontmatter(raw.decode("utf-8", errors="strict"))
        note_id = fields.get("id", "")
        if not note_id:
            raise BusinessBrainGraphError(f"missing or duplicate canonical note id: {relative}")
        record = {
            "id": note_id,
            "relative_path": relative,
            "source_path": f"business_brain:{relative}",
            "sha256": _sha256_bytes(raw),
            "size_bytes": len(raw),
            "type": fields.get("type") or None,
            "status": fields.get("status") or None,
        }
        entity_type = targeted_entity_type(relative, fields)
        if entity_type:
            record["entity_type"] = entity_type
        if fields.get("date"):
            record["date"] = fields["date"]
        return record

    @staticmethod
    def _manifest_from_files(files: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": "business_brain:README.md",
            "excludes": ["_backups/**"],
            "files": files,
            "aggregate_sha256": _sha256_bytes(_json_bytes(files)),
        }

    def source_manifest(self, *, stats: dict[str, tuple[int, int]] | None = None) -> dict[str, Any]:
        """Hash every in-scope note. stats, when given, collects (mtime_ns, size) taken BEFORE each
        read, so a write that lands after the read still shows up as a stat change later."""
        files = []
        ids = set()
        for path in self._notes():
            relative = path.relative_to(self.vault_root).as_posix()
            if stats is not None:
                st = path.stat()
                stats[relative] = (st.st_mtime_ns, st.st_size)
            record = self._manifest_record(relative, path.read_bytes())
            if record["id"] in ids:
                raise BusinessBrainGraphError(f"missing or duplicate canonical note id: {relative}")
            ids.add(record["id"])
            files.append(record)
        return self._manifest_from_files(files)

    def _run_graphify_structural(self, mirror: Path, output: Path) -> dict[str, Any]:
        if not self.graphify_python.is_file():
            raise BusinessBrainGraphError("installed Graphify Python runtime is unavailable")
        argv = [str(self.graphify_python), str(self.extractor_script), "--root", str(mirror), "--output", str(output)]
        result = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=120, check=False, env=_safe_env(), stdin=subprocess.DEVNULL)
        if result.returncode:
            raise BusinessBrainGraphError(f"Graphify structural Markdown extraction failed: {(result.stderr or result.stdout)[-3000:]}")
        try:
            raw = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessBrainGraphError("Graphify structural output is missing or invalid") from exc
        if not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("edges"), list):
            raise BusinessBrainGraphError("Graphify structural output lacks nodes/edges")
        return raw

    def _projection(
        self,
        source_manifest: dict[str, Any],
        raw_graph: dict[str, Any],
        *,
        resolution_files: list[dict[str, Any]] | None = None,
        link_candidates: dict[str, set[str]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Project notes in source_manifest. resolution_files (default: the same notes) is the full
        note set wiki links resolve against: an incremental update projects only the affected notes
        but must resolve their links exactly as a full build would. link_candidates, when given,
        collects for each projected note every path one of its wiki links could resolve to."""
        file_records = {row["relative_path"]: row for row in source_manifest["files"]}
        all_records = {row["relative_path"]: row for row in (resolution_files or source_manifest["files"])}
        note_nodes = []
        activity_nodes = []
        entity_nodes = []
        semantic_edges = []
        wiki_pairs = []
        heading_nodes = []
        structural_edges = []
        raw_file_ids = {}
        raw_reference_count = 0
        passage_cache: dict = {}

        for node in raw_graph["nodes"]:
            relative = str(node.get("source_file") or "")
            is_file_node = str(node.get("source_location") or "") == "L1" and str(node.get("label") or "").lower().endswith(".md")
            if relative not in file_records:
                if is_file_node and relative in all_records:
                    raw_file_ids[str(node.get("id"))] = all_records[relative]["id"]
                continue
            if is_file_node:
                raw_file_ids[str(node.get("id"))] = file_records[relative]["id"]
                continue
            label = str(node.get("label") or "").strip()
            if not label:
                continue
            stable = f"{file_records[relative]['id']}#heading-{len([item for item in heading_nodes if item['relative_path'] == relative]) + 1}"
            heading_nodes.append({
                "id": stable,
                "kind": "heading",
                "label": label,
                "relative_path": relative,
                "source_path": file_records[relative]["source_path"],
                "source_location": node.get("source_location"),
                "graphify_original_id": node.get("id"),
            })
            structural_edges.append({
                "source": file_records[relative]["id"],
                "target": stable,
                "relation": "contains",
                "confidence": "EXTRACTED",
                "edge_kind": "structural",
                "extractor": "graphify.markdown",
            })

        id_for_path = {relative: row["id"] for relative, row in all_records.items()}
        for relative, record in file_records.items():
            path = self.vault_root / relative
            text = path.read_text(encoding="utf-8", errors="strict")
            fields, body = parse_frontmatter(text)
            title = next((line.lstrip("#").strip() for line in body.splitlines() if line.startswith("#")), path.stem)
            metadata = {
                key: value for key, value in fields.items()
                if key in {"id", "type", "status", "date", "last_touched", "original_filename", "entities", "source_sha256"}
            }
            if record.get("entity_type"):
                metadata["entity_type"] = record["entity_type"]
            note_nodes.append({
                "id": record["id"],
                "kind": "note",
                "title": title,
                "relative_path": relative,
                "source_path": record["source_path"],
                "content_sha256": record["sha256"],
                "metadata": metadata,
            })
            if record.get("entity_type") == "prospect":
                for queue_id in frontmatter_sequence(text, "queue_ids"):
                    normalized = queue_id.upper()
                    if not AOS_ACTIVITY_RE.fullmatch(normalized):
                        continue
                    activity_id = f"activity:{normalized}"
                    activity_nodes.append({
                        "id": activity_id,
                        "kind": "activity",
                        "title": normalized,
                        "metadata": {"type": "activity", "reference": normalized},
                        "authoritative": False,
                    })
                    semantic_edges.append({
                        "source": activity_id,
                        "target": record["id"],
                        "relation": "touched",
                        "confidence": "DERIVED",
                        "edge_kind": "derived",
                        "extractor": "ttros.frontmatter.queue_ids",
                        "relationship_reason": f"{normalized} is declared in the prospect note's queue_ids frontmatter",
                        "target_path": record["source_path"],
                    })
            source_sha = str(fields.get("source_sha256") or "")
            derived_nodes, derived_edges = source_graph_projection(
                relative, record, title, body, vault=self.vault_root, sha=source_sha, cache=passage_cache, registry=self.registry,
            )
            entity_nodes.extend(derived_nodes)
            semantic_edges.extend(derived_edges)
            seen = set()
            if link_candidates is not None:
                link_candidates[relative] = wiki_link_candidates(relative, body)
            for raw_target in WIKI_LINK_RE.findall(body):
                target = resolve_wiki_target(raw_target, source=relative, paths=set(id_for_path))
                if target in id_for_path and target not in seen:
                    seen.add(target)
                    wiki_pairs.append((relative, target))

        raw_reference_pairs = set()
        for edge in raw_graph["edges"]:
            if edge.get("relation") != "references":
                continue
            raw_reference_count += 1
            source_id = raw_file_ids.get(str(edge.get("source")))
            target_id = raw_file_ids.get(str(edge.get("target")))
            if source_id and target_id:
                raw_reference_pairs.add((source_id, target_id))

        explicit_edges = []
        for source_path, target_path in sorted(set(wiki_pairs)):
            source_id, target_id = id_for_path[source_path], id_for_path[target_path]
            explicit_edges.append({
                "source": source_id,
                "target": target_id,
                "relation": "wiki_link",
                "confidence": "EXTRACTED",
                "edge_kind": "explicit",
                "extractor": "graphify.markdown+ttros-obsidian-resolution",
                "package_edge_confirmed": (source_id, target_id) in raw_reference_pairs,
                "source_path": f"business_brain:{source_path}",
            })

        deduplicated_activities = {node["id"]: node for node in activity_nodes + entity_nodes}
        nodes = sorted(note_nodes + list(deduplicated_activities.values()) + heading_nodes, key=lambda row: row["id"])
        edges = sorted(explicit_edges + semantic_edges + structural_edges, key=_edge_key)
        graph = {
            "schema_version": 2,
            "namespace": self.namespace,
            "source_manifest_sha256": _sha256_bytes(_json_bytes(source_manifest)),
            "directed": True,
            "nodes": nodes,
            "edges": edges,
        }
        projection = self._projection_manifest(
            graph,
            source_count=len(file_records),
            raw_totals=(len(raw_graph["nodes"]), len(raw_graph["edges"]), raw_reference_count),
            tokens=(int(raw_graph.get("input_tokens") or 0), int(raw_graph.get("output_tokens") or 0)),
        )
        return graph, projection

    def _projection_manifest(self, graph: dict[str, Any], *, source_count: int, raw_totals: tuple[int, int, int], tokens: tuple[int, int]) -> dict[str, Any]:
        nodes, edges = graph["nodes"], graph["edges"]
        note_nodes = [node for node in nodes if node.get("kind") == "note"]
        explicit_edges = [edge for edge in edges if edge.get("edge_kind") == "explicit"]
        entity_count = sum(1 for node in nodes if node.get("kind") == "entity")
        # Present only once a source note states entities, so a Brain without revision-3 notes projects byte-identically.
        source_graph = {"derived_entity_node_count": entity_count,
                        "source_relationship_edge_count": sum(1 for edge in edges if edge.get("extractor") == SOURCE_GRAPH_EXTRACTOR
                                                              and edge.get("relation") != "about")} if entity_count else {}
        return {**source_graph,
            "schema_version": 2,
            "namespace": self.namespace,
            "graphify_mode": "installed_graphify_structural_markdown",
            "graphify_cli_documents_mode": "requires external LLM key in graphify 0.9.11; TTROS uses the installed local Markdown extractor API",
            "source_count": source_count,
            "raw_graphify_node_count": raw_totals[0],
            "raw_graphify_edge_count": raw_totals[1],
            "raw_graphify_reference_count": raw_totals[2],
            "explicit_wiki_edge_count": len(explicit_edges),
            "derived_semantic_edge_count": sum(1 for edge in edges if edge.get("edge_kind") == "derived"),
            "supported_semantic_relations": sorted(SEMANTIC_RELATIONS),
            "targeted_entity_count": sum(1 for node in note_nodes if node["metadata"].get("entity_type")),
            "targeted_entity_types": sorted({node["metadata"]["entity_type"] for node in note_nodes if node["metadata"].get("entity_type")}),
            "package_confirmed_wiki_edge_count": sum(1 for edge in explicit_edges if edge["package_edge_confirmed"]),
            "ttros_repaired_wiki_edge_count": sum(1 for edge in explicit_edges if not edge["package_edge_confirmed"]),
            "stable_note_id_count": len(note_nodes),
            "heading_node_count": sum(1 for node in nodes if node.get("kind") == "heading"),
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "bodies_in_projection": False,
            "token_usage_text": TOKEN_USAGE_TEXT,
        }

    @staticmethod
    def _artifact_hashes(directory: Path) -> dict[str, str]:
        return {
            name: _sha256_bytes((directory / name).read_bytes())
            for name in ("graph.json", "source_manifest.json", "projection_manifest.json")
        }

    def _write_receipt(self, payload: dict[str, Any]) -> Path:
        path = self.receipts / f"{_stamp()}-{payload['operation']}-{payload['status']}.json"
        _atomic_write(path, _json_bytes(payload))
        return path

    def build(self) -> dict[str, Any]:
        self.document_root.mkdir(parents=True, exist_ok=True)
        self.namespace_root.mkdir(parents=True, exist_ok=True)
        with self._apply_lock():
            return self._build_locked()

    def _build_locked(self) -> dict[str, Any]:
        temp_root = Path(tempfile.mkdtemp(prefix=f".tmp-{self.namespace}-", dir=self.document_root))
        try:
            stats: dict[str, tuple[int, int]] = {}
            source_manifest = self.source_manifest(stats=stats)
            mirror = temp_root / "source"
            for record in source_manifest["files"]:
                source = self.vault_root / record["relative_path"]
                target = mirror / record["relative_path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            raw_path = temp_root / "raw_graphify.json"
            raw_graph = self._run_graphify_structural(mirror, raw_path)
            link_candidates: dict[str, set[str]] = {}
            graph, projection = self._projection(source_manifest, raw_graph, link_candidates=link_candidates)
            candidate = temp_root / "published"
            candidate.mkdir()
            _atomic_write(candidate / "graph.json", _json_bytes(graph))
            _atomic_write(candidate / "source_manifest.json", _json_bytes(source_manifest))
            _atomic_write(candidate / "projection_manifest.json", _json_bytes(projection))
            candidate_hashes = self._artifact_hashes(candidate)
            operation = "build"
            parity = self._oracle_parity(candidate_hashes)
            if self.published.is_dir() and self._artifact_hashes(self.published) == candidate_hashes:
                operation = "unchanged"
            else:
                moved_previous = None
                try:
                    if self.published.exists():
                        moved_previous = self.history / _stamp() / "published"
                        moved_previous.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(self.published, moved_previous)
                    self.published.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(candidate, self.published)
                except Exception:
                    if moved_previous and moved_previous.exists() and not self.published.exists():
                        os.replace(moved_previous, self.published)
                    raise
            receipt = {
                "status": "success",
                "operation": operation,
                "timestamp": _iso_now(),
                "namespace": self.namespace,
                "source_manifest_sha256": source_manifest["aggregate_sha256"],
                "artifact_hashes": candidate_hashes,
                "published": str(self.published),
                "token_usage_text": TOKEN_USAGE_TEXT,
            }
            if parity is not None:
                receipt["incremental_parity"] = parity
            receipt["incremental_state"] = self._sync_state(source_manifest, raw_graph, stats, link_candidates)
            receipt_path = self._write_receipt(receipt)
            return {**receipt, "receipt_path": str(receipt_path), "projection": projection}
        except Exception as exc:
            failure = {
                "status": "failed",
                "operation": "build",
                "timestamp": _iso_now(),
                "namespace": self.namespace,
                "error": str(exc),
                "previous_published_usable": self._published_is_usable(),
                "token_usage_text": TOKEN_USAGE_TEXT,
            }
            self._write_receipt(failure)
            if isinstance(exc, BusinessBrainGraphError):
                raise
            raise BusinessBrainGraphError(str(exc)) from exc
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _published_is_usable(self) -> bool:
        try:
            graph = _load_json_cached(self.published / "graph.json")
            manifest = _load_json_cached(self.published / "source_manifest.json")
            return isinstance(graph.get("nodes"), list) and isinstance(manifest.get("files"), list)
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

    def status(self) -> dict[str, Any]:
        if not self._published_is_usable():
            return {"state": "unavailable", "trusted": False, "fallback": "pointers_search", "reason": "published graph is missing or invalid"}
        if incremental_mode() == "on" and lexical_scope_mode() == "on":
            indexed = self._indexed_status()
            if indexed is not None:
                return indexed
        return self._full_status()

    def _full_status(self) -> dict[str, Any]:
        if not self._published_is_usable():
            return {"state": "unavailable", "trusted": False, "fallback": "pointers_search", "reason": "published graph is missing or invalid"}
        try:
            current = self.source_manifest()
            published = json.loads((self.published / "source_manifest.json").read_text(encoding="utf-8"))
        except (BusinessBrainGraphError, OSError, json.JSONDecodeError) as exc:
            return {"state": "unavailable", "trusted": False, "fallback": "pointers_search", "reason": str(exc)}
        if current.get("aggregate_sha256") != published.get("aggregate_sha256"):
            return {"state": "stale", "trusted": False, "fallback": "pointers_search", "reason": "source manifest changed since publication"}
        return {"state": "fresh", "trusted": True, "fallback": None, "source_manifest_sha256": current["aggregate_sha256"]}

    # ---- incremental Graphify -------------------------------------------------------------

    @contextlib.contextmanager
    def _apply_lock(self):
        self.namespace_root.mkdir(parents=True, exist_ok=True)
        with open(self.namespace_root / ".graph-apply.lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _allowlist_sha(self) -> str:
        allowed, prefixes = self._published_target_allowlist()
        denied = sorted(set(self.registry.data.get("denied_brain_pointers") or []))
        return _sha256_bytes(_json_bytes({"allowed": sorted(allowed), "prefixes": list(prefixes), "denied": denied}))

    def _state_conn(self) -> sqlite3.Connection:
        assert self.state_db is not None
        conn = sqlite3.connect(str(self.state_db), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.executescript(GRAPH_STATE_SCHEMA)
        return conn

    def _read_state(self, conn: sqlite3.Connection) -> dict[str, str]:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM graph_state WHERE namespace = ?", (self.namespace,))}

    def _state_matches(self, state: dict[str, str], manifest_bytes: bytes) -> str | None:
        """None when the state describes exactly the published manifest for this vault and scope
        registry; otherwise the reason it cannot be trusted."""
        if not state.get("manifest_file_sha256"):
            return "no incremental state"
        if state.get("vault_root") != str(self.vault_root):
            return "state belongs to another vault"
        if state.get("manifest_file_sha256") != _sha256_bytes(manifest_bytes):
            return "published manifest changed outside the incremental path"
        if state.get("allowlist_sha256") != self._allowlist_sha():
            return "client-scope graph targets changed since the state was written"
        return None

    def _clear_state(self, reason: str) -> None:
        if self.state_db is None:
            return
        try:
            conn = self._state_conn()
            try:
                conn.execute("DELETE FROM graph_state WHERE namespace = ?", (self.namespace,))
                conn.execute("INSERT INTO graph_state VALUES (?, 'invalid_reason', ?)", (self.namespace, reason[:500]))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    @staticmethod
    def _raw_counts_by_note(raw_graph: dict[str, Any]) -> dict[str, list[int]]:
        counts: dict[str, list[int]] = {}
        for node in raw_graph["nodes"]:
            counts.setdefault(str(node.get("source_file") or ""), [0, 0, 0])[0] += 1
        for edge in raw_graph["edges"]:
            row = counts.setdefault(str(edge.get("source_file") or ""), [0, 0, 0])
            row[1] += 1
            if edge.get("relation") == "references":
                row[2] += 1
        return counts

    def _sync_state(self, manifest: dict[str, Any], raw_graph: dict[str, Any], stats: dict[str, tuple[int, int]],
                    link_candidates: dict[str, set[str]]) -> dict[str, Any]:
        """After a full build: record per-note state so later changes can be applied incrementally.
        Never fails the build; any doubt leaves the state cleared, which means full scans."""
        if self.state_db is None:
            return {"status": "disabled"}
        files = manifest["files"]
        counts = self._raw_counts_by_note(raw_graph)
        known = {row["relative_path"] for row in files}
        unattributed = [key for key in counts if key not in known]
        if unattributed:
            reason = f"raw Graphify output has {len(unattributed)} items not attributable to one note"
            self._clear_state(reason)
            return {"status": "cleared", "reason": reason}
        try:
            manifest_bytes = (self.published / "source_manifest.json").read_bytes()
            if manifest_bytes != _json_bytes(manifest):
                reason = "published manifest differs from the build's own manifest"
                self._clear_state(reason)
                return {"status": "cleared", "reason": reason}
            conn = self._state_conn()
            try:
                previous = self._read_state(conn)
                revision = int(previous.get("revision") or 0) + 1
                conn.execute("BEGIN")
                for table in ("graph_state", "graph_notes", "graph_link_candidates"):
                    conn.execute(f"DELETE FROM {table} WHERE namespace = ?", (self.namespace,))
                conn.executemany(
                    "INSERT INTO graph_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(self.namespace, row["relative_path"], row["id"], row["sha256"], stats.get(row["relative_path"], (0, 0))[1],
                      stats.get(row["relative_path"], (0, 0))[0], *counts.get(row["relative_path"], [0, 0, 0])) for row in files],
                )
                conn.executemany(
                    "INSERT INTO graph_link_candidates VALUES (?, ?, ?)",
                    [(self.namespace, source, candidate) for source, values in link_candidates.items() for candidate in sorted(values)],
                )
                self._write_state_values(conn, {
                    "vault_root": str(self.vault_root),
                    "manifest_file_sha256": _sha256_bytes(manifest_bytes),
                    "allowlist_sha256": self._allowlist_sha(),
                    "revision": str(revision),
                    "input_tokens": str(int(raw_graph.get("input_tokens") or 0)),
                    "output_tokens": str(int(raw_graph.get("output_tokens") or 0)),
                    "synced_by": "full_build",
                    "updated_at": _iso_now(),
                })
                conn.commit()
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            self._clear_state(f"state sync failed: {exc}")
            return {"status": "cleared", "reason": str(exc)[:300]}
        return {"status": "synced", "revision": revision, "notes": len(files)}

    def _write_state_values(self, conn: sqlite3.Connection, values: dict[str, str]) -> None:
        conn.executemany("INSERT OR REPLACE INTO graph_state VALUES (?, ?, ?)", [(self.namespace, key, value) for key, value in values.items()])

    def _indexed_status(self) -> dict[str, Any] | None:
        if self.state_db is None or not self.state_db.exists():
            return None
        try:
            manifest_path = self.published / "source_manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            conn = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                state = self._read_state(conn)
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            return None
        if self._state_matches(state, manifest_bytes) is not None:
            return None
        manifest = _load_json_cached(manifest_path)
        return {"state": "fresh", "trusted": True, "fallback": None, "source_manifest_sha256": manifest["aggregate_sha256"],
                "revision": int(state.get("revision") or 0), "status_mode": "indexed"}

    def _oracle_parity(self, candidate_hashes: dict[str, str]) -> dict[str, Any] | None:
        """Nightly oracle: when the published graph came from incremental updates, say whether a
        from-scratch build reproduced it exactly."""
        if self.state_db is None or not self.published.is_dir():
            return None
        try:
            conn = self._state_conn()
            try:
                state = self._read_state(conn)
            finally:
                conn.close()
            if state.get("synced_by") != "incremental":
                return None
            published = self._artifact_hashes(self.published)
        except (OSError, sqlite3.Error):
            return None
        differing = sorted(name for name in candidate_hashes if published.get(name) != candidate_hashes[name])
        return {"checked_revision": int(state.get("revision") or 0), "equal": not differing, "differing_artifacts": differing}

    def _relative_for(self, value: str | Path) -> str | None:
        text = str(value)
        if text.startswith(business_brain.BUSINESS_BRAIN_PREFIX):
            text = text[len(business_brain.BUSINESS_BRAIN_PREFIX):]
        path = Path(text)
        if path.is_absolute():
            try:
                path = path.relative_to(self.vault_root)
            except ValueError:
                try:
                    path = path.resolve().relative_to(self.vault_root)
                except ValueError:
                    return None
        relative = path.as_posix()
        if not relative or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
            return None
        return relative

    def _in_scope(self, relative: str, allowed: set[str], prefixes: tuple[str, ...], denied: set[str]) -> bool:
        """The per-note form of _notes(): same rules as canonical_markdown + the graph allowlist,
        plus a refusal of anything that resolves outside the vault (symlink escape)."""
        if not relative.endswith(".md") or "_backups" in {part.lower() for part in relative.split("/")}:
            return False
        if relative.startswith(NONCANONICAL_PREFIXES):
            return False
        pointer = f"business_brain:{relative}"
        if pointer in denied or not (pointer in allowed or any(pointer.startswith(prefix) for prefix in prefixes)):
            return False
        path = self.vault_root / relative
        if not path.is_file():
            return False
        try:
            path.resolve().relative_to(self.vault_root)
        except ValueError:
            return False
        return True

    def apply_changes(self, paths: list[str] | tuple[str, ...] | set[str], *, origin: str = "write") -> dict[str, Any]:
        """Apply additions, edits, deletions and renames (old + new path) of the given notes to the
        published graph, touching only those notes and the notes whose links resolve to them."""
        started = time.perf_counter()
        if self.state_db is None:
            return {"status": "disabled", "reason": "no incremental state database"}
        relatives = sorted({rel for rel in (self._relative_for(value) for value in paths) if rel})
        if not relatives:
            return {"status": "unchanged", "applied": 0}
        with self._apply_lock():
            try:
                result = self._apply_locked(relatives, origin)
            except Exception as exc:
                # Leave the published graph as it was and drop the incremental state, so status()
                # goes back to the full scan and reports exactly what it reported before.
                self._clear_state(f"apply failed: {type(exc).__name__}: {exc}")
                self._write_receipt({"status": "failed", "operation": "incremental", "timestamp": _iso_now(), "namespace": self.namespace,
                                     "origin": origin, "error": str(exc)[:1000], "paths": relatives[:50], "token_usage_text": TOKEN_USAGE_TEXT})
                return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    def _apply_locked(self, relatives: list[str], origin: str) -> dict[str, Any]:
        manifest_path = self.published / "source_manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        conn = self._state_conn()
        try:
            state = self._read_state(conn)
            mismatch = self._state_matches(state, manifest_bytes)
            if mismatch is not None:
                return {"status": "unsynced", "reason": mismatch}
            manifest = json.loads(manifest_bytes)
            graph = json.loads((self.published / "graph.json").read_text(encoding="utf-8"))
            records = {row["relative_path"]: row for row in manifest["files"]}
            allowed, prefixes = self._published_target_allowlist()
            denied = set(self.registry.data.get("denied_brain_pointers") or [])

            changed: dict[str, tuple[bytes, os.stat_result]] = {}
            removed: set[str] = set()
            touched: dict[str, os.stat_result] = {}

            def read_note(relative: str) -> tuple[bytes, os.stat_result]:
                path = self.vault_root / relative
                st = path.stat()  # before the read: a later write shows up as a stat change
                return path.read_bytes(), st

            for relative in relatives:
                if not self._in_scope(relative, allowed, prefixes, denied):
                    if relative in records:
                        removed.add(relative)
                    continue
                raw, st = read_note(relative)
                if relative in records and records[relative]["sha256"] == _sha256_bytes(raw):
                    touched[relative] = st
                    continue
                changed[relative] = (raw, st)

            if not changed and not removed:
                if touched:
                    conn.executemany("UPDATE graph_notes SET mtime_ns = ?, size_bytes = ? WHERE namespace = ? AND relative_path = ?",
                                     [(st.st_mtime_ns, st.st_size, self.namespace, rel) for rel, st in touched.items()])
                    conn.commit()
                return {"status": "unchanged", "applied": 0, "revision": int(state.get("revision") or 0)}

            new_records = {relative: self._manifest_record(relative, raw) for relative, (raw, _st) in changed.items()}
            # Inbound edges change when a target path appears or vanishes, or a target's id changes.
            delta = (set(changed) - set(records)) | removed | {
                rel for rel in changed if rel in records and records[rel]["id"] != new_records[rel]["id"]}
            inbound: set[str] = set()
            if delta:
                marks = ",".join("?" for _ in delta)
                inbound = {row[0] for row in conn.execute(
                    f"SELECT DISTINCT source_path FROM graph_link_candidates WHERE namespace = ? AND candidate IN ({marks})",
                    (self.namespace, *sorted(delta)))}
            new_paths = (set(records) - removed) | set(changed)
            inbound = (inbound & new_paths) - set(changed)
            for relative in sorted(inbound):
                changed[relative] = read_note(relative)
                new_records[relative] = self._manifest_record(relative, changed[relative][0])
            affected = set(changed)

            merged = {rel: row for rel, row in records.items() if rel in new_paths and rel not in affected}
            merged.update(new_records)
            seen_ids: dict[str, str] = {}
            for relative, row in merged.items():
                if row["id"] in seen_ids:
                    raise BusinessBrainGraphError(f"missing or duplicate canonical note id: {relative}")
                seen_ids[row["id"]] = relative
            files = [merged[rel] for rel in sorted(merged, key=lambda value: (self.vault_root / value).parts)]
            new_manifest = self._manifest_from_files(files)

            # Extract only the affected notes, plus every note their links resolve to (so Graphify's
            # own file-to-file reference edges, i.e. package_edge_confirmed, come out as in a full build).
            mirror_set = set(affected)
            for relative in affected:
                body = parse_frontmatter(changed[relative][0].decode("utf-8", errors="strict"))[1]
                for raw_target in WIKI_LINK_RE.findall(body):
                    target = resolve_wiki_target(raw_target, source=relative, paths=set(merged))
                    if target in merged:
                        mirror_set.add(target)
            temp_root = Path(tempfile.mkdtemp(prefix=f".tmp-{self.namespace}-inc-", dir=self.document_root))
            try:
                mirror = temp_root / "source"
                for relative in sorted(mirror_set):
                    target = mirror / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if relative in changed:
                        target.write_bytes(changed[relative][0])
                    else:
                        shutil.copyfile(self.vault_root / relative, target)
                raw_graph = self._run_graphify_structural(mirror, temp_root / "raw_graphify.json")
                counts = self._raw_counts_by_note(raw_graph)
                link_candidates: dict[str, set[str]] = {}
                partial, _ = self._projection(self._manifest_from_files([new_records[rel] for rel in sorted(affected)]), raw_graph,
                                              resolution_files=files, link_candidates=link_candidates)

                old_ids = {records[rel]["id"] for rel in (affected | removed) if rel in records}
                gone_paths = {f"business_brain:{rel}" for rel in affected | removed}
                final_ids = {row["id"] for row in files}
                nodes = [node for node in graph["nodes"] if not (
                    (node.get("kind") == "note" and node["id"] in old_ids)
                    or (node.get("kind") == "heading" and node.get("source_path") in gone_paths))]
                edges = [edge for edge in graph["edges"] if not (
                    edge["source"] in old_ids
                    or (edge.get("relation") == "wiki_link" and edge.get("source_path") in gone_paths)
                    or (edge.get("relation") == "touched" and edge["target"] in old_ids)
                    or edge.get("stated_in") in old_ids)]
                activities = {node["id"]: node for node in nodes if node.get("kind") in DERIVED_NODE_KINDS}
                activities.update({node["id"]: node for node in partial["nodes"] if node.get("kind") in DERIVED_NODE_KINDS})
                nodes = [node for node in nodes if node.get("kind") not in DERIVED_NODE_KINDS] + [
                    node for node in partial["nodes"] if node.get("kind") not in DERIVED_NODE_KINDS]
                edges = edges + partial["edges"]
                # A link can only point at a note that still exists, an activity only where it is touched, and a
                # derived entity only where a remaining source note states it.
                edges = [edge for edge in edges if edge.get("relation") != "wiki_link" or edge["target"] in final_ids]
                referenced = {edge["source"] for edge in edges if edge.get("relation") == "touched"} | {
                    end for edge in edges if edge.get("extractor") == SOURCE_GRAPH_EXTRACTOR for end in (edge["source"], edge["target"])}
                nodes += [node for node_id, node in activities.items() if node_id in referenced]
                new_graph = {
                    "schema_version": 2,
                    "namespace": self.namespace,
                    "source_manifest_sha256": _sha256_bytes(_json_bytes(new_manifest)),
                    "directed": True,
                    "nodes": sorted(nodes, key=lambda row: row["id"]),
                    "edges": sorted(edges, key=_edge_key),
                }
                stored = {row["relative_path"]: (row["raw_nodes"], row["raw_edges"], row["raw_refs"]) for row in conn.execute(
                    "SELECT relative_path, raw_nodes, raw_edges, raw_refs FROM graph_notes WHERE namespace = ?", (self.namespace,))}
                for relative in affected:
                    stored[relative] = tuple(counts.get(relative, [0, 0, 0]))
                totals = tuple(sum(stored[rel][index] for rel in merged) for index in range(3))
                projection = self._projection_manifest(
                    new_graph, source_count=len(files), raw_totals=totals,  # type: ignore[arg-type]
                    tokens=(int(state.get("input_tokens") or 0), int(state.get("output_tokens") or 0)))

                candidate = temp_root / "published"
                candidate.mkdir()
                _atomic_write(candidate / "graph.json", _json_bytes(new_graph))
                _atomic_write(candidate / "source_manifest.json", _json_bytes(new_manifest))
                _atomic_write(candidate / "projection_manifest.json", _json_bytes(projection))
                for extra in self.published.iterdir():  # keep side artifacts (capture evidence projection)
                    if extra.is_file() and not (candidate / extra.name).exists():
                        shutil.copyfile(extra, candidate / extra.name)
                previous = temp_root / "previous"
                os.replace(self.published, previous)
                try:
                    os.replace(candidate, self.published)
                except Exception:
                    os.replace(previous, self.published)
                    raise
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)

            revision = int(state.get("revision") or 0) + 1
            conn.execute("BEGIN")
            gone = sorted(affected | removed)
            marks = ",".join("?" for _ in gone)
            conn.execute(f"DELETE FROM graph_notes WHERE namespace = ? AND relative_path IN ({marks})", (self.namespace, *gone))
            conn.execute(f"DELETE FROM graph_link_candidates WHERE namespace = ? AND source_path IN ({marks})", (self.namespace, *gone))
            conn.executemany("INSERT INTO graph_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                (self.namespace, rel, new_records[rel]["id"], new_records[rel]["sha256"], changed[rel][1].st_size,
                 changed[rel][1].st_mtime_ns, *stored[rel]) for rel in sorted(affected)])
            conn.executemany("INSERT INTO graph_link_candidates VALUES (?, ?, ?)", [
                (self.namespace, rel, candidate_path) for rel in sorted(affected) for candidate_path in sorted(link_candidates.get(rel, ()))])
            conn.executemany("UPDATE graph_notes SET mtime_ns = ?, size_bytes = ? WHERE namespace = ? AND relative_path = ?",
                             [(st.st_mtime_ns, st.st_size, self.namespace, rel) for rel, st in touched.items()])
            self._write_state_values(conn, {
                "manifest_file_sha256": _sha256_bytes((self.published / "source_manifest.json").read_bytes()),
                "revision": str(revision),
                "synced_by": "incremental",
                "updated_at": _iso_now(),
            })
            conn.commit()
        finally:
            conn.close()
        receipt = {
            "status": "success", "operation": "incremental", "timestamp": _iso_now(), "namespace": self.namespace, "origin": origin,
            "revision": revision, "changed": sorted(set(new_records) - inbound), "relinked": sorted(inbound), "removed": sorted(removed),
            "source_manifest_sha256": new_manifest["aggregate_sha256"], "token_usage_text": TOKEN_USAGE_TEXT,
        }
        self._write_receipt(receipt)
        return {**receipt, "applied": len(affected) + len(removed)}

    def detect_changes(self) -> dict[str, Any]:
        """Stat-only change detection (no hashing) for edits made outside write_transaction, e.g.
        on the Windows side. Notes whose (mtime_ns, size) moved, appeared or vanished go to
        apply_changes(), which hashes only those."""
        started = time.perf_counter()
        if self.state_db is None:
            return {"status": "disabled"}
        conn = self._state_conn()
        try:
            known = {row["relative_path"]: (row["mtime_ns"], row["size_bytes"]) for row in conn.execute(
                "SELECT relative_path, mtime_ns, size_bytes FROM graph_notes WHERE namespace = ?", (self.namespace,))}
            synced = bool(self._read_state(conn).get("manifest_file_sha256"))
        finally:
            conn.close()
        if not synced:
            return {"status": "unsynced", "reason": "no incremental state; the next full build writes it"}
        allowed, prefixes = self._published_target_allowlist()
        denied = set(self.registry.data.get("denied_brain_pointers") or [])
        candidates: list[str] = []
        present: set[str] = set()
        for path in canonical_markdown(self.vault_root):
            relative = path.relative_to(self.vault_root).as_posix()
            pointer = f"business_brain:{relative}"
            if pointer in denied or not (pointer in allowed or any(pointer.startswith(prefix) for prefix in prefixes)):
                continue
            present.add(relative)
            st = path.stat()
            if known.get(relative) != (st.st_mtime_ns, st.st_size):
                candidates.append(relative)
        candidates += sorted(set(known) - present)
        scan_ms = round((time.perf_counter() - started) * 1000, 1)
        if not candidates:
            return {"status": "unchanged", "scanned": len(present), "scan_ms": scan_ms}
        result = self.apply_changes(candidates, origin="stat_sweep")
        return {**result, "scanned": len(present), "stat_candidates": len(candidates), "scan_ms": scan_ms}

    def _published_note_hashes(self) -> dict[str, str]:
        manifest = _load_json_cached(self.published / "source_manifest.json")
        return {str(row["relative_path"]): str(row["sha256"]) for row in manifest.get("files") or []}

    def expand_targets(self, query: str, seed_paths: list[str], *, limit: int,
                       client_scope: str, registry: ClientScopeRegistry) -> dict:
        """Walk published source and wiki edges outward from ranked evidence."""
        gate = registry
        identity = gate.validate_graph_namespace(client_scope, self.namespace)
        try:
            graph = _load_json_cached(self.published / "graph.json")
            hashes = self._published_note_hashes()
        except (OSError, ValueError, KeyError):
            return {"targets": [], "graph_state": "unavailable"}
        by_id = {n["id"]: n for n in graph["nodes"]}
        by_path = {n["source_path"]: n["id"] for n in by_id.values() if n.get("kind") == "note"}
        from aos_indexer import INTAKE_RECORD_POINTER_RE
        seeds = [path for path in seed_paths if path in by_path and
                 (INTAKE_RECORD_POINTER_RE.fullmatch(path) or self._returned_note_ok(gate, identity, path, hashes, []))]
        excluded = set(seed_paths)
        terms = set(WORD_RE.findall(query.lower())) - {
            "what", "where", "when", "which", "does", "work", "with", "should", "include", "across", "about",
        }
        candidates: dict[str, dict] = {}
        visibility: dict[str, bool] = {}
        neighbors: dict[str, list[tuple[str, dict]]] = {}
        for edge in graph["edges"]:
            relation = edge["relation"]
            if relation == "wiki_link" or edge.get("extractor") == SOURCE_GRAPH_EXTRACTOR:
                a, b = edge["source"], edge["target"]
                neighbors.setdefault(a, []).append((b, edge))
                neighbors.setdefault(b, []).append((a, edge))
                stated = edge.get("stated_in")
                if stated and relation != "about":
                    neighbors.setdefault(stated, []).extend(((a, edge), (b, edge)))
                    neighbors[a].append((stated, edge))
                    neighbors[b].append((stated, edge))

        def offer(other: str, via: dict, seed: str, rank: int, hops: int) -> None:
            note = by_id.get(other, {})
            path = note.get("source_path", "")
            if (not path or path in excluded or path.startswith(("business_brain:people/", "business_brain:sources/intake/"))
                    or path.endswith("/INDEX.md")):
                return
            title_terms = set(WORD_RE.findall(str(note.get("title") or "").lower()))
            topical = {term for term in terms & title_terms if len(term) > 2}
            if not topical:
                return
            typed = via.get("extractor") == SOURCE_GRAPH_EXTRACTOR and via["relation"] != "about"
            evidence = {k: via[k] for k in ("relation", "edge_kind", "ref", "evidence_status", "evidence_quote",
                        "evidence_refs", "source_sha256", "stated_in_path", "source_authority") if k in via}
            evidence["seed_path"] = seed
            source_context = via["relation"] == "same_source"
            score = 3 * len(topical) + 2 * typed + 2 * source_context - rank * 0.25 - hops * 0.5
            if path not in candidates or score > candidates[path]["score"]:
                candidates[path] = {"path": path, "score": score, "relationship": evidence}

        def visible(node_id: str) -> bool:
            if node_id not in visibility:
                path = by_id.get(node_id, {}).get("source_path")
                visibility[node_id] = not path or self._returned_note_ok(gate, identity, path, hashes, [])
            return visibility[node_id]

        for rank, seed in enumerate(seeds):
            original = INTAKE_RECORD_POINTER_RE.fullmatch(seed)
            if original:
                try:
                    gate.validate_graph_target(client_scope, self.namespace, seed)
                except _CLIENT_SCOPE_ERRORS:
                    continue
                for node in by_id.values():
                    if node.get("kind") == "note" and node.get("metadata", {}).get("source_sha256") == original[1]:
                        offer(node["id"], {"relation": "same_source", "edge_kind": "structural",
                                           "source_sha256": original[1]}, seed, rank, 1)
                continue
            for other, edge in neighbors.get(by_path[seed], ()):
                offer(other, edge, seed, rank, 1)
                bridge = by_id.get(other, {})
                path = bridge.get("source_path", "")
                source_index = path.startswith("business_brain:sources/historical_calls/") and path.count("/") == 3 and path.endswith("/INDEX.md")
                person_hub = path.startswith("business_brain:people/") and not path.endswith("/INDEX.md")
                if bridge.get("kind") == "entity" or source_index or person_hub:
                    if visible(other):
                        for target, onward in neighbors.get(other, ()):
                            offer(target, onward, seed, rank, 2)

        targets = []
        for item in sorted(candidates.values(), key=lambda item: (-item["score"], item["path"])):
            stated = item["relationship"].get("stated_in_path")
            if stated and (stated not in by_path or not visible(by_path[stated])):
                continue
            if self._returned_note_ok(gate, identity, item["path"], hashes, []):
                targets.append(item)
            if len(targets) >= limit:
                break
        return {"targets": targets, "graph_state": "verified_candidates"}

    def query_targets(
        self,
        query: str,
        *,
        limit: int = 5,
        client_scope: str | None = None,
        registry: ClientScopeRegistry | None = None,
    ) -> dict[str, Any]:
        gate = registry or self.registry
        identity = gate.validate_graph_namespace(client_scope, self.namespace)
        state = self.status()
        if state["state"] != "fresh":
            return {"query": query, "targets": [], "graph_state": state["state"], "trusted_for_model": False, "fallback": {"route": "pointers_search", "reason": state["reason"]}, "token_usage_text": TOKEN_USAGE_TEXT}
        mode = lexical_scope_mode()
        graph = _load_json_cached(self.published / "graph.json")
        if mode == "off":
            return self._targets_from_graph(query, limit, gate, identity, graph, "full")[0]
        if mode == "on":
            result, _stats = self._targets_from_graph(query, limit, gate, identity, graph, "lexical")
            if result is not None:
                return result
            # More top-ranked candidates failed the full check than the overscan allows:
            # fail safe to the original exhaustive path rather than return a short list.
            return self._targets_from_graph(query, limit, gate, identity, graph, "full")[0]
        # shadow: the original path is authoritative and returned; the new path runs beside it.
        old, old_stats = self._targets_from_graph(query, limit, gate, identity, graph, "full")
        try:
            new, new_stats = self._targets_from_graph(query, limit, gate, identity, graph, "lexical")
        except Exception as exc:  # shadow must never break the real result
            _shadow_log({"event": "shadow_error", "error": f"{type(exc).__name__}: {exc}"[:300], "query_sha256": hashlib.sha256(str(query).encode()).hexdigest()})
            return old
        old_rows = [(row["path"], row["score"]) for row in old["targets"]]
        new_rows = [(row["path"], row["score"]) for row in new["targets"]] if new is not None else None
        _shadow_log({
            "event": "shadow_compare",
            "query_sha256": hashlib.sha256(str(query).encode()).hexdigest(),
            "equal": new_rows == old_rows,
            "old": old_rows,
            "new": new_rows,
            "old_validate_calls": old_stats["validate_calls"],
            "new_validate_calls": new_stats["validate_calls"],
            "new_fell_back": new is None,
        })
        return old

    def _targets_from_graph(self, query, limit, gate, identity, graph, strategy):
        """Rank graph targets. strategy 'full' is the original behaviour (full scope check on every
        candidate). 'lexical' ranks on string-only scope rules and runs the full check plus a sha256
        check against the published manifest only on the returned notes, refilling on failure; it
        returns (None, stats) when failures exceed the overscan budget."""
        stats = {"validate_calls": 0}
        lexical = strategy == "lexical"
        lex_gate = gate.lexical_graph_gate(identity.scope_id, self.namespace) if lexical else None
        normalized_query = str(query).lower()
        terms = set(WORD_RE.findall(normalized_query))
        minimum_term_matches = 2 if len(terms) >= 3 else 1
        requested_activities = {value.upper() for value in AOS_ACTIVITY_RE.findall(str(query))}
        headings: dict[str, list[str]] = {}
        for edge in graph["edges"]:
            if edge.get("relation") == "contains":
                headings.setdefault(edge["source"], []).append(edge["target"])
        nodes = {node["id"]: node for node in graph["nodes"]}
        seed_scores: dict[str, float] = {}
        seed_matches: dict[str, int] = {}
        for node in graph["nodes"]:
            if node.get("kind") not in {"note", "activity"}:
                continue
            if node.get("kind") == "activity" and not requested_activities:
                continue
            searchable = " ".join([
                str(node.get("title") or ""),
                str(node.get("relative_path") or ""),
                str(node.get("metadata") or ""),
            ] + [str(nodes.get(child, {}).get("label") or "") for child in headings.get(node["id"], [])]).lower()
            if requested_activities:
                reference = str((node.get("metadata") or {}).get("reference") or "").upper()
                if reference not in requested_activities:
                    continue
                score = 100.0
            else:
                searchable_terms = set(WORD_RE.findall(searchable))
                matched_terms = terms & searchable_terms
                if len(matched_terms) < minimum_term_matches:
                    continue
                title_terms = set(WORD_RE.findall(str(node.get("title") or "").lower()))
                path_terms = set(WORD_RE.findall(str(node.get("relative_path") or "").lower()))
                score = float(sum(3 if term in title_terms else 2 if term in path_terms else 1 for term in matched_terms))
            if not score:
                continue
            seed_scores[node["id"]] = score
            seed_matches[node["id"]] = len(terms) if requested_activities else len(matched_terms)

        candidates: dict[str, dict[str, Any]] = {}

        def add_candidate(node_id: str, score: float, reason: str, matched_term_count: int) -> None:
            node = nodes.get(node_id) or {}
            if node.get("kind") != "note":
                return
            if lexical:
                scoped_path = lex_gate(str(node.get("source_path") or ""))
                if scoped_path is None:
                    return
            else:
                stats["validate_calls"] += 1
                try:
                    scoped_path = gate.validate_graph_target(identity.scope_id, self.namespace, str(node.get("source_path") or ""))
                except _CLIENT_SCOPE_ERRORS:
                    return
            record = candidates.setdefault(scoped_path, {
                "path": scoped_path,
                "score": score,
                "relationship_reasons": [],
                "_matched_term_count": matched_term_count,
            })
            record["score"] = max(float(record["score"]), float(score))
            record["_matched_term_count"] = max(int(record["_matched_term_count"]), matched_term_count)
            if reason not in record["relationship_reasons"]:
                record["relationship_reasons"].append(reason)

        for node_id, score in seed_scores.items():
            add_candidate(node_id, score, "direct deterministic query match", seed_matches[node_id])
        for edge in graph["edges"]:
            relation = str(edge.get("relation") or "")
            if relation not in SEMANTIC_RELATIONS:
                continue
            source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
            if source in seed_scores:
                add_candidate(
                    target,
                    seed_scores[source] + 10.0,
                    f"one-hop {edge.get('edge_kind')} {relation}: {edge.get('relationship_reason')}",
                    seed_matches[source],
                )
            if target in seed_scores:
                add_candidate(
                    source,
                    seed_scores[target] + 5.0,
                    f"one-hop reverse {edge.get('edge_kind')} {relation}: {edge.get('relationship_reason')}",
                    seed_matches[target],
                )

        # One-hop over typed relationships stated by revision-3 source notes (derived, non-authoritative): a query
        # that names either endpoint in full returns the stating note, with the predicate in its reason. The note still
        # passes the same scope (and, lexically, hash) checks as any other candidate; entity nodes are never returned.
        for edge in graph["edges"]:
            if edge.get("extractor") != SOURCE_GRAPH_EXTRACTOR or edge.get("relation") == "about":
                continue
            ends = [str(edge.get("subject") or ""), str(edge.get("object") or "")]
            if not any((words := set(WORD_RE.findall(name.lower()))) and words <= terms for name in ends):
                continue
            stated = terms & set(WORD_RE.findall(" ".join([*ends, str(edge["relation"]).replace("_", " ")]).lower()))
            add_candidate(str(edge.get("stated_in") or ""), 10.0 + 3.0 * len(stated),
                          f"one-hop derived {edge['relation']}: {edge.get('relationship_reason')}", len(stated))

        # One-hop over corroborated suggested links (derived, status=suggested, same client scope,
        # both endpoints unchanged since the link was made). Ranked just below the note that led
        # there; the returned note still passes the same scope and hash checks as any other.
        suggestions = self._suggested_links(identity.scope_id)
        if suggestions:
            id_for_pointer = {str(node.get("source_path")): node["id"] for node in graph["nodes"] if node.get("kind") == "note"}
            for link in suggestions:
                reason = (f"suggested {link['relation']} (status=suggested, similarity {link['similarity']:.2f}, "
                          f"confidence {link['confidence']}): {'; '.join(link['reasons'])} [derived, not canonical]")
                for here, there in ((link["source_path"], link["target_path"]), (link["target_path"], link["source_path"])):
                    seed = id_for_pointer.get(here)
                    target_id = id_for_pointer.get(there)
                    if seed in seed_scores and target_id:
                        add_candidate(target_id, max(0.5, seed_scores[seed] - 0.5), reason, seed_matches[seed])

        def ranked_rows(pool: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            best_match_count = max((int(row["_matched_term_count"]) for row in pool.values()), default=0)
            return sorted(
                (
                    {key: value for key, value in row.items() if not key.startswith("_")}
                    for row in pool.values()
                    if int(row["_matched_term_count"]) == best_match_count
                ),
                key=lambda row: (-row["score"], row["path"]),
            )

        count = max(1, min(int(limit), 20))
        scope_check: dict[str, Any] | None = None
        if not lexical:
            targets = ranked_rows(candidates)[:count]
        else:
            published_hashes = self._published_note_hashes()
            budget = count + SCOPE_OVERSCAN
            verified: set[str] = set()
            hash_dropped: list[str] = []
            while True:
                top = ranked_rows(candidates)[:count]
                failed = []
                for row in top:
                    path = row["path"]
                    if path in verified:
                        continue
                    if stats["validate_calls"] >= budget:
                        return None, stats
                    stats["validate_calls"] += 1
                    if self._returned_note_ok(gate, identity, path, published_hashes, hash_dropped):
                        verified.add(path)
                    else:
                        failed.append(path)
                if not failed:
                    targets = top
                    break
                for path in failed:
                    candidates.pop(path, None)
            scope_check = {"strategy": "lexical_topk", "full_checks": stats["validate_calls"], "hash_dropped": hash_dropped}
        for row in targets:
            reasons = row.get("relationship_reasons") or []
            if reasons and all(str(reason).startswith("suggested ") for reason in reasons):
                row["suggested_only"] = True
        result = {
            "query": query,
            "targets": targets,
            "graph_state": "fresh",
            "trusted_for_model": True,
            "client_scope": identity.scope_id,
            "trust_note": "Targets passed the Block 2 scope registry before return; authoritative note loading remains separate.",
            "fallback": None if targets else {"route": "pointers_search", "reason": "Graphify found no sufficiently relevant scoped target"},
            "token_usage_text": TOKEN_USAGE_TEXT,
        }
        if scope_check is not None:
            result["scope_check"] = scope_check
        return result, stats

    def _suggested_links(self, scope_id: str) -> list[dict[str, Any]]:
        path = self.namespace_root / "suggested" / "suggested_links.json"
        try:
            payload = _load_json_cached(path)
            published = self._published_note_hashes()
        except (OSError, json.JSONDecodeError):
            return []
        out = []
        for link in payload.get("links") or []:
            if link.get("status") != "suggested" or link.get("client_scope") != scope_id:
                continue
            source, target = str(link.get("source_path") or ""), str(link.get("target_path") or "")
            prefix = business_brain.BUSINESS_BRAIN_PREFIX
            if (published.get(source[len(prefix):]) != link.get("source_sha256")
                    or published.get(target[len(prefix):]) != link.get("target_sha256")):
                continue  # an endpoint changed since the link was derived: stale, never used
            out.append(link)
        return out

    def _returned_note_ok(self, gate, identity, pointer: str, published_hashes: dict[str, str], hash_dropped: list[str]) -> bool:
        """Full scope check (filesystem resolve included) and sha256-vs-published-manifest check,
        run only on a note about to be returned."""
        try:
            canonical = gate.validate_graph_target(identity.scope_id, self.namespace, pointer)
        except _CLIENT_SCOPE_ERRORS:
            return False
        relative = canonical[len(business_brain.BUSINESS_BRAIN_PREFIX):]
        expected = published_hashes.get(relative)
        try:
            actual = _sha256_bytes((self.vault_root / relative).read_bytes())
        except OSError:
            hash_dropped.append(canonical)
            return False
        if expected is None or actual != expected:
            hash_dropped.append(canonical)
            return False
        return True

    def publish_capture_metadata(self, records: list[dict[str, Any]] | list[Any]) -> dict[str, Any]:
        """Publish typed evidence leaves beside, never inside, the Brain graph."""
        try:
            from aos_capture import CaptureMetadataProjection
        except ModuleNotFoundError:
            from tools.aos_capture import CaptureMetadataProjection
        rows = []
        for value in records:
            row = CaptureMetadataProjection.from_mapping(value.to_dict()) if hasattr(value, "to_dict") else CaptureMetadataProjection.from_mapping(dict(value))
            self.registry.validate_capture_evidence_reference(row.client_scope, row.reference_path)
            rows.append(row)
        nodes = []
        edges = []
        for row in sorted(rows, key=lambda item: item.reference_path):
            nodes.append({
                "id": f"capture-evidence:{row.record_id}",
                "kind": "capture_evidence",
                "reference_path": row.reference_path,
                "client_scope": row.client_scope,
                "linked_item_id": row.linked_item_id,
                "subject_classification": row.subject_classification,
                "timestamp": row.timestamp,
                "source_type": row.source_type,
                "triage_state": row.triage_state,
                "proposal_state": row.proposal_state,
                "authoritative": False,
            })
            if row.linked_item_id:
                edges.append({
                    "source": f"capture-evidence:{row.record_id}",
                    "target": f"work-item:{row.linked_item_id}",
                    "relation": "proposes",
                    "client_scope": row.client_scope,
                    "authoritative": False,
                })
        graph = {
            "schema_version": 1,
            "namespace": self.namespace,
            "projection": "capture_metadata_only",
            "source_authority": False,
            "nodes": nodes,
            "edges": edges,
            "raw_content_fields_serializable": False,
        }
        self.published.mkdir(parents=True, exist_ok=True)
        target = self.published / "capture_evidence.graph.json"
        _atomic_write(target, _json_bytes(graph))
        receipt = {
            "operation": "capture-metadata-projection",
            "status": "success",
            "created_at": _iso_now(),
            "namespace": self.namespace,
            "artifact": str(target),
            "sha256": _sha256_bytes(target.read_bytes()),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "metadata_only": True,
            "token_usage_text": TOKEN_USAGE_TEXT,
        }
        receipt_path = self._write_receipt(receipt)
        return {**receipt, "receipt": str(receipt_path)}

    def query_capture_targets(
        self,
        query: str,
        *,
        client_scope: str | None,
        limit: int = 10,
        registry: ClientScopeRegistry | None = None,
    ) -> dict[str, Any]:
        gate = registry or self.registry
        identity = gate.resolve_scope(client_scope)
        path = self.published / "capture_evidence.graph.json"
        if not path.is_file():
            return {"query": query, "targets": [], "graph_state": "unavailable", "fallback": "search"}
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessBrainGraphError("capture evidence projection is invalid") from exc
        terms = WORD_RE.findall(query.lower())
        ranked = []
        for node in graph.get("nodes") or []:
            if node.get("kind") != "capture_evidence" or node.get("client_scope") != identity.scope_id:
                continue
            reference = gate.validate_capture_evidence_reference(identity.scope_id, str(node.get("reference_path") or ""))
            searchable = " ".join(str(node.get(key) or "") for key in ("subject_classification", "triage_state", "proposal_state", "source_type")).lower()
            score = sum(1 for term in terms if term in searchable) if terms else 1
            if score:
                ranked.append({"path": reference, "score": float(score)})
        ranked.sort(key=lambda row: (-row["score"], row["path"]))
        return {"query": query, "targets": ranked[: max(1, min(int(limit), 20))], "graph_state": "fresh", "fallback": None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "status", "query", "detect"))
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--vault", type=Path, default=business_brain.BUSINESS_BRAIN_ROOT)
    parser.add_argument("--graphify-root", type=Path, default=DEFAULT_GRAPHIFY_ROOT)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--client-scope")
    args = parser.parse_args()
    service = BusinessBrainGraphService(graphify_root=args.graphify_root, vault_root=args.vault, namespace=args.namespace)
    if args.command == "build":
        result = service.build()
    elif args.command == "status":
        result = service.status()
    elif args.command == "detect":
        result = service.detect_changes()
    else:
        result = service.query_targets(args.query, client_scope=args.client_scope)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
