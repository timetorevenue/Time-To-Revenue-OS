"""Scoped Business Brain retrieval, actual-read provenance, and work classification.

Revisit: when retrieval hierarchy or completion context rules change. · Last touched: 2026-11-08.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import aos_indexer
    import universal_knowledge
    from business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry
except ModuleNotFoundError:  # package import in unittest/IDE contexts
    from tools import aos_indexer, universal_knowledge
    from tools.business_brain_scope import ClientScopeError, ClientScopeRegistry, load_registry


KNOWLEDGE_ROUTES = {"pointer", "graphify", "graphify_suggested", "search", "direct_fallback"}
GRAPH_DISCOVERY_MODES = {"unclear", "cross_cutting", "relationship_dependent"}
BUSINESS_IMPLICATION_FIELDS = {
    "business_facing_skill",
    "business_output",
    "promotion_candidate",
    "pricing_implication",
    "positioning_implication",
    "offer_implication",
    "strategy_implication",
    "policy_implication",
    "client_commitment",
    "legal_implication",
    "financial_implication",
}


class BrainContextError(RuntimeError):
    """Mandatory scoped context could not be loaded or validated."""


def effective_discovery_mode(requested: str | None, *, graphify: bool) -> str:
    """Make the CLI's `--graphify` flag select graph discovery by default."""
    return requested or ("cross_cutting" if graphify else "explicit")


@dataclass(frozen=True)
class BrainContextUsed:
    note_id: str
    path: str
    client_scope: str
    retrieval_route: str
    content_sha256: str


@dataclass(frozen=True)
class BrainRead:
    content: str
    provenance: BrainContextUsed


@dataclass
class ScopedRetrievalResult:
    reads: list[BrainRead] = field(default_factory=list)
    graph_state: dict[str, Any] | None = None
    # Search hits the registry does not let this scope read (recorded, never opened), and the
    # ranking the search tier actually used ("hybrid" / "keyword-only"; None when no search ran).
    excluded: list[str] = field(default_factory=list)
    search_mode: str | None = None

    @property
    def brain_context_used(self) -> list[dict[str, Any]]:
        return [asdict(read.provenance) for read in self.reads]

    @property
    def contents(self) -> list[str]:
        return [read.content for read in self.reads]


def classify_work(work: dict[str, Any]) -> dict[str, Any]:
    """Classify deterministically; ambiguity is knowledge-sensitive and stops."""
    explicit_classification = str(work.get("context_classification") or work.get("classification") or "")
    affirmative_global_technical = (
        (work.get("technical_only") is True or explicit_classification == "technical_only")
        and explicit_classification in {"", "technical_only"}
        and str(work.get("client_scope") or "") == "global"
    )
    pointers = [str(value) for value in work.get("business_brain_pointers") or work.get("sources") or [] if str(value).startswith("business_brain:")]
    sensitive_reasons = []
    if pointers:
        sensitive_reasons.append("declared_business_brain_pointer")
    if work.get("client_scope") not in {None, ""} and not affirmative_global_technical:
        sensitive_reasons.append("declared_client_scope")
    for key in sorted(BUSINESS_IMPLICATION_FIELDS):
        if work.get(key):
            sensitive_reasons.append(key)
    implications = {str(value).strip().lower() for value in work.get("implications") or []}
    sensitive_reasons.extend(sorted(implications & {"pricing", "positioning", "offer", "strategy", "policy", "client_commitment", "legal", "financial"}))
    if sensitive_reasons:
        return {
            "classification": "knowledge_sensitive",
            "knowledge_sensitive": True,
            "requires_brain_context": True,
            "required_status_on_missing": "needs_input",
            "reasons": sensitive_reasons,
        }
    if (work.get("technical_only") is True or explicit_classification == "technical_only") and explicit_classification in {"", "technical_only"}:
        return {
            "classification": "technical_only",
            "knowledge_sensitive": False,
            "requires_brain_context": False,
            "brain_context_status": "not_applicable",
            "reasons": ["affirmative_technical_only"],
        }
    return {
        "classification": "ambiguous",
        "knowledge_sensitive": True,
        "requires_brain_context": True,
        "required_status_on_missing": "needs_input",
        "reasons": ["unclassified_defaults_to_knowledge_sensitive"],
    }


def validate_degraded_context(record: dict[str, Any], *, classification: dict[str, Any]) -> dict[str, Any]:
    required = {"missing_source", "reason_unavailable", "fallback_used", "why_safe", "client_scope"}
    missing = sorted(required - set(record))
    if missing:
        raise BrainContextError(f"degraded_context is incomplete: {', '.join(missing)}")
    if not record.get("explicit_safe_without_source"):
        raise BrainContextError("degraded_context requires an explicit safe-without-source contract")
    forbidden_reason = str(record.get("reason_unavailable") or "").lower()
    if any(term in forbidden_reason for term in ("unresolved scope", "classification", "cross-client", "pricing", "commitment")):
        raise BrainContextError("degraded_context cannot waive scope, classification, cross-client, pricing, or commitment context")
    if classification.get("classification") == "ambiguous":
        raise BrainContextError("degraded_context cannot waive failed classification")
    return record


def validate_brain_context_used(
    records: Iterable[dict[str, Any]],
    *,
    client_scope: str,
    registry: ClientScopeRegistry | None = None,
) -> list[dict[str, Any]]:
    gate = registry or load_registry()
    try:
        gate.resolve_scope(client_scope)
    except ClientScopeError as exc:
        raise BrainContextError(str(exc)) from exc
    validated = []
    for record in records:
        if record.get("client_scope") != client_scope:
            raise BrainContextError("brain_context_used contains a cross-client scope")
        try:
            pointer = gate.validate_brain_pointer(client_scope, str(record.get("path") or ""))
        except ClientScopeError as exc:
            raise BrainContextError(str(exc)) from exc
        route = str(record.get("retrieval_route") or "")
        if route not in KNOWLEDGE_ROUTES:
            raise BrainContextError(f"invalid brain_context_used retrieval route: {route}")
        if not str(record.get("note_id") or "").strip():
            raise BrainContextError("brain_context_used note_id is required")
        validated.append({**record, "path": pointer})
    return validated


def validate_completion_context(
    work: dict[str, Any],
    *,
    brain_context_used: Iterable[dict[str, Any]] = (),
    brain_context_status: str | None = None,
    degraded_context: dict[str, Any] | None = None,
    registry: ClientScopeRegistry | None = None,
) -> dict[str, Any]:
    classification = classify_work(work)
    if classification["classification"] == "technical_only":
        if brain_context_status != "not_applicable":
            raise BrainContextError("technical-only completion must record brain_context_status=not_applicable")
        return {"classification": classification, "brain_context_used": []}
    client_scope = str(work.get("client_scope") or "")
    records = validate_brain_context_used(brain_context_used, client_scope=client_scope, registry=registry)
    if records:
        return {"classification": classification, "brain_context_used": records}
    if degraded_context is not None:
        validate_degraded_context(degraded_context, classification=classification)
        return {"classification": classification, "brain_context_used": [], "degraded_context": degraded_context}
    raise BrainContextError("knowledge-sensitive completion requires actual scoped brain_context_used; status must be needs_input")


class ThreadEvidenceFixtureLoader:
    """Reusable scope-first interface; Block 2 callers supply fixtures only."""

    def __init__(self, registry: ClientScopeRegistry | None = None):
        self.registry = registry or load_registry()

    def load(self, *, client_scope: str | None, evidence_identity: str, loader: Callable[[str], Any]) -> Any:
        identity = self.registry.validate_evidence_identity(client_scope, evidence_identity)
        return loader(identity)


class ScopedBrainLoader:
    def __init__(
        self,
        *,
        registry: ClientScopeRegistry | None = None,
        vault_root: Path | None = None,
        graph_service: Any | None = None,
        search_module: Any = aos_indexer,
        opener: Callable[[Path], str] | None = None,
        search_db_path: Path | None = None,
        hybrid: bool = False,
    ):
        self.registry = registry or load_registry()
        # hybrid=True: the query-search tier uses keyword + local-embedding search (still exact=True,
        # path_only=True, same limit). The Graphify-tier cross-check stays exact keyword.
        self.hybrid = bool(hybrid)
        self.vault_root = Path(vault_root) if vault_root is not None else None
        self.graph_service = graph_service
        self.search_module = search_module
        self.opener = opener or (lambda path: path.read_text(encoding="utf-8", errors="strict"))
        self.search_db_path = search_db_path

    def _open(self, *, client_scope: str | None, pointer: str, route: str) -> BrainRead:
        if route not in KNOWLEDGE_ROUTES:
            raise BrainContextError(f"unsupported retrieval route: {route}")
        resolved = self.registry.resolve_brain_pointer(client_scope, pointer, root=self.vault_root)
        content = self.opener(resolved.resolved_path)
        fields, _body = self.search_module.parse_frontmatter(content)
        note_id = str(fields.get("id") or "").strip()
        if not note_id:
            raise BrainContextError(f"Business Brain note lacks stable id: {resolved.pointer}")
        return BrainRead(
            content=content,
            provenance=BrainContextUsed(
                note_id=note_id,
                path=resolved.pointer,
                client_scope=str(client_scope),
                retrieval_route=route,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ),
        )

    @staticmethod
    def _search_paths(result: dict[str, Any]) -> list[str]:
        paths = []
        for group in (result.get("groups") or {}).values():
            for row in group:
                pointer = str(row.get("path") or "")
                if pointer.startswith("business_brain:") and pointer not in paths:
                    paths.append(pointer)
        return paths

    def _readable(self, client_scope: str | None, paths: list[str]) -> tuple[list[str], list[str]]:
        """Split search hits into pointers this scope may read and excluded source strings.

        Ownership rules agree across Search, direct reads and Graphify. Rejected
        results remain visible in the retrieval disposition for diagnosis.
        """
        readable, excluded = [], []
        for pointer in paths:
            try:
                self.registry.validate_brain_pointer(client_scope, pointer)
            except ClientScopeError:
                excluded.append(f"{pointer}#route=search#excluded=outside_read_scope")
                continue
            readable.append(pointer)
        return readable, excluded

    def evidence(self, query: str, *, client_scope: str, limit: int = 6) -> dict:
        """Rank compact indexed evidence, then fill reserved slots with scoped graph neighbors."""
        from tools import aos_entity_index, shared_brain_read
        from tools.brain_memory import retirement_rules

        started = time.perf_counter()
        q = query.strip()[:300]
        if not q:
            return {"matches": [], "search_mode": None, "context_bytes": 0}
        limit = max(1, min(limit, 8))
        broad = len(q.split()) >= 10
        excerpt_chars = 700 if broad else 530
        direct_limit = max(1, limit - (2 if broad else 1))
        root = (self.vault_root or self.search_module.BUSINESS_BRAIN_ROOT).resolve()
        index = self.search_module
        db = index.runtime_db_path(self.search_db_path)
        result = index.search(q, source="business_brain", limit=limit * 5,
                              db_path=db, client_scope=client_scope, registry=self.registry,
                              hybrid=True, passages=True, evidence_only=True,
                              excerpt_chars=excerpt_chars, rerank=True)
        ranked = sorted((row for group in result["groups"].values() for row in group),
                        key=lambda row: row["retrieval_rank"])
        rules = retirement_rules(vault_root=root)
        checked: dict[str, bool] = {}
        passage_sources: set[str] = set()
        selected: list[dict] = []
        graph_state = None
        if not db.exists():
            raise BrainContextError("Business Brain search index is unavailable")
        conn = index.connect(db, readonly=True)
        try:
            def current(pointer: str) -> bool:
                if pointer in checked:
                    return checked[pointer]
                checked[pointer] = False
                rel = pointer.removeprefix("business_brain:")
                if rel in rules["retired_paths"] or any(rel.startswith(p) for p in rules["retired_prefixes"]):
                    return False
                try:
                    resolved = self.registry.resolve_brain_pointer(client_scope, pointer, root=root)
                    stat = resolved.resolved_path.stat()
                except (PermissionError, OSError, ValueError):
                    return False
                row = conn.execute(
                    "SELECT source_root, mtime, size_bytes FROM documents "
                    "WHERE path=? AND client_scope=? AND source='business_brain'", (pointer, client_scope)
                ).fetchone()
                checked[pointer] = bool(row and Path(row["source_root"]).resolve() == root and
                                        (stat.st_mtime, stat.st_size) == (row["mtime"], row["size_bytes"]))
                return checked[pointer]

            def add(row: dict, route: str) -> None:
                pointer = row["path"]
                if pointer in {item["reference"] for item in selected}:
                    return
                is_passage = index.is_passage_pointer(pointer)
                parent = pointer.split("#", 1)[0] if is_passage else pointer
                if index.INTAKE_RECORD_POINTER_RE.fullmatch(pointer) or not current(parent):
                    return
                if is_passage and parent in passage_sources:
                    return
                snippet = row.get("excerpt") or index.evidence_excerpt(row.get("body", ""), q, excerpt_chars)
                if not snippet:
                    return
                ref = row.get("source_ref") or ""
                selected.append({
                    "reference": pointer, "title": row["title"][:160], "snippet": snippet,
                    "retrieval_route": route, "search_mode": result.get("search_mode"),
                    "retrieval_rank": row.get("retrieval_rank"), "match": row.get("match"),
                    "source_ref": ref, "source_record": parent if is_passage else "",
                    "source_sha256": ref[7:71] if ref.startswith("source:") else "",
                    "excerpt_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
                    "relationship": row.get("relationship"),
                })
                if is_passage:
                    passage_sources.add(parent)

            for row in ranked:
                add(row, "search")
                if len(selected) == direct_limit:
                    break
            if self.graph_service is not None and selected:
                seeds = list(dict.fromkeys(item["source_record"] if index.is_passage_pointer(item["reference"])
                                           else item["reference"] for item in selected))
                try:
                    expansion = self.graph_service.expand_targets(
                        q, seeds[:4], limit=limit * 2, client_scope=client_scope, registry=self.registry)
                except (ClientScopeError, OSError, ValueError):
                    expansion = {"targets": [], "graph_state": "unavailable"}
                graph_state = expansion["graph_state"]
                for target in expansion["targets"]:
                    if len(selected) >= limit:
                        break
                    row = conn.execute("SELECT path, title, body FROM documents WHERE path=? AND client_scope=?",
                                       (target["path"], client_scope)).fetchone()
                    if row and current(target["path"]):
                        relationship = target.get("relationship", {})
                        note_ref = relationship.get("ref") if relationship.get("stated_in_path") == row["path"] else ""
                        add({**dict(row), "relationship": relationship, "source_ref": note_ref}, "graphify")
            for row in ranked:
                if len(selected) >= limit:
                    break
                add(row, "search")
        finally:
            conn.close()

        try:
            meta = aos_entity_index.document_meta([item["reference"] for item in selected], db_path=db)
        except sqlite3.OperationalError:
            meta = {}
        for item in selected:
            fields = meta.get(item["reference"], {})
            authority, label = shared_brain_read.authority_for(item["reference"], fields, retired=False)
            item.update(authority=authority, authority_label=label, client_scope=client_scope)
            for key in ("date", "status", "superseded_by", "source_record", "source_sha256", "source_drive_url"):
                if not item.get(key):
                    item[key] = fields.get(key, "")
        context_bytes = sum(len(item["snippet"].encode()) for item in selected)
        return {"matches": selected, "search_mode": result.get("search_mode"), "graph_state": graph_state,
                "context_bytes": context_bytes, "excerpt_budget_chars": excerpt_chars * limit,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "model_calls": 0, "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}

    def retrieve(
        self,
        *,
        work: dict[str, Any],
        pointers: Iterable[str] = (),
        query: str = "",
        discovery_mode: str = "explicit",
        direct_fallback: str | None = None,
        limit: int = 5,
    ) -> ScopedRetrievalResult:
        classification = classify_work(work)
        if classification["classification"] == "technical_only":
            return ScopedRetrievalResult()
        client_scope = work.get("client_scope")
        self.registry.resolve_scope(client_scope)
        explicit = [str(pointer) for pointer in pointers if str(pointer)]
        if explicit:
            return ScopedRetrievalResult(reads=[self._open(client_scope=client_scope, pointer=pointer, route="pointer") for pointer in explicit])

        graph_state = None
        if discovery_mode in GRAPH_DISCOVERY_MODES and query.strip() and self.graph_service is not None:
            graph_result = self.graph_service.query_targets(query, limit=limit, client_scope=client_scope, registry=self.registry)
            graph_state = {
                "state": graph_result.get("graph_state"),
                "fallback": graph_result.get("fallback"),
            }
            targets = [str(row.get("path") or "") for row in graph_result.get("targets") or []]
            # A note reached only through a corroborated suggested link says so in its provenance.
            target_routes = {str(row.get("path") or ""): "graphify_suggested" if row.get("suggested_only") else "graphify"
                             for row in graph_result.get("targets") or []}
            if targets:
                search_result = self.search_module.search(
                    query,
                    source="business_brain",
                    limit=limit,
                    db_path=self.search_db_path,
                    client_scope=client_scope,
                    registry=self.registry,
                    exact=True,
                    path_only=True,
                )
                search_paths, excluded = self._readable(client_scope, self._search_paths(search_result))
                if search_paths and not set(targets).intersection(search_paths):
                    return ScopedRetrievalResult(
                        reads=[self._open(client_scope=client_scope, pointer=pointer, route="search") for pointer in search_paths[:limit]],
                        graph_state=graph_state,
                        excluded=excluded,
                        search_mode=f"graphify-check:{search_result.get('search_mode')}",
                    )
                return ScopedRetrievalResult(
                    reads=[self._open(client_scope=client_scope, pointer=pointer, route=target_routes.get(pointer, "graphify")) for pointer in targets[:limit]],
                    graph_state=graph_state,
                    excluded=excluded,
                    search_mode=f"graphify-check:{search_result.get('search_mode')}",
                )

        excluded: list[str] = []
        search_mode = None
        if query.strip():
            search_result = self.search_module.search(
                query,
                source="business_brain",
                limit=limit,
                db_path=self.search_db_path,
                client_scope=client_scope,
                registry=self.registry,
                exact=True,
                path_only=True,
                **({"hybrid": True} if self.hybrid else {}),
            )
            search_mode = search_result.get("search_mode")
            paths, excluded = self._readable(client_scope, self._search_paths(search_result))
            if paths:
                return ScopedRetrievalResult(
                    reads=[self._open(client_scope=client_scope, pointer=pointer, route="search") for pointer in paths[:limit]],
                    graph_state=graph_state,
                    excluded=excluded,
                    search_mode=search_mode,
                )

        if direct_fallback:
            return ScopedRetrievalResult(
                reads=[self._open(client_scope=client_scope, pointer=direct_fallback, route="direct_fallback")],
                graph_state=graph_state,
                excluded=excluded,
                search_mode=search_mode,
            )
        error = BrainContextError("mandatory scoped Business Brain context was not found; status must be needs_input")
        error.excluded = excluded
        error.search_mode = search_mode
        raise error


@dataclass
class AssembledContext:
    """Universal methodology plus whatever company context exists, kept apart.

    ``universal`` never counts as company ``brain_context_used``; ``company`` carries the scoped
    provenance that validate_completion_context checks. ``company_status`` is "loaded", "empty"
    (a knowledge-sensitive request found nothing: company-specific claims must be marked
    unverified or the work returns needs_input) or "not_applicable" (affirmative technical-only work).
    """

    company: ScopedRetrievalResult
    company_status: str
    classification: dict[str, Any]
    universal: list[Any] = field(default_factory=list)

    @property
    def universal_provenance(self) -> list[dict[str, Any]]:
        return [method.provenance() for method in self.universal]

    @property
    def needs_company_context(self) -> bool:
        return self.company_status == "empty"


def assemble_context(
    loader: ScopedBrainLoader,
    *,
    work: dict[str, Any],
    query: str,
    domain: str | None = None,
    pointers: Iterable[str] = (),
    discovery_mode: str = "explicit",
    direct_fallback: str | None = None,
    limit: int = 5,
    universal_root: Path | None = None,
) -> AssembledContext:
    """Combine universal methodology with scoped company context at execution time.

    An empty Business Brain is a normal state: it yields ``company_status="empty"`` and the
    universal methods, never an error and never another company's content. Scope violations still
    raise; completion validation is unchanged and still demands real scoped company context for
    knowledge-sensitive work.
    """
    classification = classify_work(work)
    universal = universal_knowledge.find(query, domain=domain, limit=3, root=universal_root) if query.strip() else []
    if classification["classification"] == "technical_only":
        return AssembledContext(ScopedRetrievalResult(), "not_applicable", classification, universal)
    try:
        company = loader.retrieve(work=work, pointers=pointers, query=query, discovery_mode=discovery_mode,
                                  direct_fallback=direct_fallback, limit=limit)
    except BrainContextError as exc:
        empty = ScopedRetrievalResult(excluded=list(getattr(exc, "excluded", []) or []), search_mode=getattr(exc, "search_mode", None))
        return AssembledContext(empty, "empty", classification, universal)
    return AssembledContext(company, "loaded" if company.reads else "empty", classification, universal)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fresh-process scoped Business Brain retrieval proof")
    parser.add_argument("--client-scope", required=True)
    parser.add_argument("--pointer", action="append", default=[])
    parser.add_argument("--query", default="")
    parser.add_argument("--direct-fallback")
    parser.add_argument("--discovery-mode", choices=["explicit", *sorted(GRAPH_DISCOVERY_MODES)])
    parser.add_argument("--graphify", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    graph_service = None
    if args.graphify:
        from dashboard.backend.business_brain_graph import BusinessBrainGraphService
        graph_service = BusinessBrainGraphService()
    loader = ScopedBrainLoader(graph_service=graph_service)
    result = loader.retrieve(
        work={"client_scope": args.client_scope},
        pointers=args.pointer,
        query=args.query,
        discovery_mode=effective_discovery_mode(args.discovery_mode, graphify=args.graphify),
        direct_fallback=args.direct_fallback,
        limit=args.limit,
    )
    payload = {
        "status": "success",
        "fresh_process_pid": __import__("os").getpid(),
        "brain_context_used": result.brain_context_used,
        "content_count": len(result.reads),
        "query_present_in_opened_content": bool(args.query) and any(args.query.lower() in read.content.lower() for read in result.reads),
        "graph_state": result.graph_state,
        "token_usage_text": "Token usage: no agent invocation",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
