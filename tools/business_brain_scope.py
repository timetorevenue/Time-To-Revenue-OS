"""Schema-backed, default-deny client scope enforcement for Business Brain reads.

All content-bearing callers validate a declared scope and exact source identity
here before opening a note, executing search SQL, loading evidence, or returning
a Graphify target.

Revisit: when a verified client identity or retrieval boundary is added. · Last touched: 2026-11-08.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

try:
    import business_brain
except ModuleNotFoundError:  # package import in unittest/IDE contexts
    from tools import business_brain


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "context" / "client_scope_registry.json"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "context" / "client_scope_registry.schema.json"


@lru_cache(maxsize=8)
def _checked_validator(schema_text: str):
    """Cache only the checked schema, never registry data or permission decisions."""
    schema = json.loads(schema_text)
    validator = jsonschema.validators.validator_for(schema)
    validator.check_schema(schema)
    return validator(schema)


class ClientScopeError(PermissionError):
    """A request lacks one exact, enabled scope or requests an unowned source."""


@dataclass(frozen=True)
class ScopeIdentity:
    scope_id: str
    kind: str


@dataclass(frozen=True)
class CaptureIdentityResolution:
    state: str
    client_scope: str | None = None
    scope_kind: str | None = None
    matched_by: tuple[str, ...] = ()


class ClientScopeRegistry:
    def __init__(
        self,
        *,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
        schema_path: Path = DEFAULT_SCHEMA_PATH,
        data: dict[str, Any] | None = None,
    ):
        self.registry_path = Path(registry_path)
        self.schema_path = Path(schema_path)
        try:
            self.data = data if data is not None else json.loads(self.registry_path.read_text(encoding="utf-8"))
            _checked_validator(self.schema_path.read_text(encoding="utf-8")).validate(self.data)
        except (OSError, json.JSONDecodeError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
            raise ClientScopeError(f"client scope registry is unavailable or invalid: {exc}") from exc
        if self.data.get("default_deny") is not True:
            raise ClientScopeError("client scope registry must be default-deny")

    def resolve_scope(self, client_scope: str | None) -> ScopeIdentity:
        raw = "" if client_scope is None else str(client_scope)
        if not raw or raw != raw.strip():
            raise ClientScopeError("client_scope is required and must be exact")
        record = self.data["scopes"].get(raw)
        if not isinstance(record, dict):
            raise ClientScopeError(f"unresolved client_scope: {raw}")
        if record.get("enabled") is not True:
            raise ClientScopeError(f"client_scope is disabled: {raw}")
        return ScopeIdentity(scope_id=raw, kind=str(record["kind"]))

    def _scope_record(self, client_scope: str | None) -> tuple[ScopeIdentity, dict[str, Any]]:
        identity = self.resolve_scope(client_scope)
        return identity, self.data["scopes"][identity.scope_id]

    @staticmethod
    def canonical_pointer(pointer: str) -> str:
        return business_brain.resolve_business_brain_pointer(pointer, require_exists=False).pointer

    @staticmethod
    def _safe_knowledge_path(pointer: str) -> bool:
        try:
            from tools.aos_indexer import is_excluded
        except ModuleNotFoundError:  # Hermes venv shadows `tools`
            from aos_indexer import is_excluded
        return not is_excluded(pointer.removeprefix("business_brain:"))

    def validate_brain_pointer(self, client_scope: str | None, pointer: str) -> str:
        identity, record = self._scope_record(client_scope)
        try:
            resolved = business_brain.resolve_business_brain_pointer(pointer, require_exists=False)
            canonical = resolved.pointer
        except business_brain.BusinessBrainPointerError as exc:
            raise ClientScopeError(str(exc)) from exc
        if not self._safe_knowledge_path(canonical) or canonical in set(self.data.get("denied_brain_pointers") or []):
            raise ClientScopeError(f"Business Brain pointer is explicitly unavailable: {canonical}")
        exact = set(record.get("brain_pointers") or [])
        prefixes = tuple(str(value) for value in record.get("brain_pointer_prefixes") or [])
        if canonical not in exact and not any(canonical.startswith(prefix) for prefix in prefixes):
            raise ClientScopeError(f"Business Brain pointer does not belong to {identity.scope_id}: {canonical}")
        lexical_path = business_brain.BUSINESS_BRAIN_ROOT / resolved.relative_path
        if resolved.resolved_path != lexical_path.absolute():
            raise ClientScopeError("symlinked knowledge targets are prohibited")
        return canonical

    def lexical_brain_gate(self, client_scope: str | None):
        """Scope-check a previously validated derived row without reopening its source."""
        _identity, record = self._scope_record(client_scope)
        denied = set(self.data.get("denied_brain_pointers") or [])
        exact = set(record.get("brain_pointers") or [])
        prefixes = tuple(str(value) for value in record.get("brain_pointer_prefixes") or [])

        def check(pointer: str) -> str | None:
            try:
                canonical = business_brain.lexical_business_brain_pointer(pointer)
            except business_brain.BusinessBrainPointerError:
                return None
            if (canonical in denied or not self._safe_knowledge_path(canonical)
                    or (canonical not in exact and not canonical.startswith(prefixes))):
                return None
            return canonical

        return check

    def resolve_brain_pointer(
        self,
        client_scope: str | None,
        pointer: str,
        *,
        root: Path | None = None,
    ) -> business_brain.ResolvedBusinessBrainPointer:
        canonical = self.validate_brain_pointer(client_scope, pointer)
        return business_brain.resolve_business_brain_pointer(canonical, root=root)

    def permitted_brain_pointers(self, client_scope: str | None) -> tuple[str, ...]:
        _identity, record = self._scope_record(client_scope)
        denied = set(self.data.get("denied_brain_pointers") or [])
        return tuple(pointer for pointer in record.get("brain_pointers") or [] if pointer not in denied)

    def validate_search_source(self, client_scope: str | None, source: str | None) -> ScopeIdentity:
        identity, record = self._scope_record(client_scope)
        raw = "" if source is None else str(source)
        if raw and raw not in {str(rule.get("source") or "") for rule in record.get("search_source_identities") or []}:
            raise ClientScopeError(f"search source does not belong to {identity.scope_id}: {raw}")
        return identity

    def scope_for_search_identity(self, source: str, path: str) -> str | None:
        if source == "business_brain" and not self._safe_knowledge_path(path):
            return None
        if path in set(self.data.get("denied_brain_pointers") or []):
            return None
        matches = []
        for scope_id, record in self.data.get("scopes", {}).items():
            if record.get("enabled") is not True:
                continue
            for rule in record.get("search_source_identities") or []:
                if rule.get("source") != source:
                    continue
                if path in set(rule.get("paths") or []):
                    matches.append(scope_id)
                prefix = str(rule.get("path_prefix") or "")
                if prefix and path.startswith(prefix):
                    matches.append(scope_id)
        return matches[0] if len(set(matches)) == 1 else None

    def validate_graph_namespace(self, client_scope: str | None, namespace: str) -> ScopeIdentity:
        identity, record = self._scope_record(client_scope)
        if namespace not in {str(rule.get("namespace") or "") for rule in record.get("graphify_targets") or []}:
            raise ClientScopeError(f"Graphify namespace does not belong to {identity.scope_id}: {namespace}")
        return identity

    def lexical_graph_gate(self, client_scope: str | None, namespace: str):
        """Return gate(pointer) -> canonical pointer | None, applying exactly the string rules of
        validate_graph_target (canonical form, denied list, brain_pointers/prefixes, graphify
        target paths/prefixes) with NO filesystem access. Namespace/scope errors raise as in
        validate_graph_namespace. It is a ranking pre-filter only: the returned notes must still
        pass validate_graph_target (symlink-escape resolution lives there)."""
        identity = self.validate_graph_namespace(client_scope, namespace)
        record = self.data["scopes"][identity.scope_id]
        denied = set(self.data.get("denied_brain_pointers") or [])
        exact = set(record.get("brain_pointers") or [])
        prefixes = tuple(str(value) for value in record.get("brain_pointer_prefixes") or [])
        graph_allowed: set[str] = set()
        graph_prefixes: list[str] = []
        for rule in record.get("graphify_targets") or []:
            if rule.get("namespace") == namespace:
                graph_allowed.update(rule.get("paths") or [])
                graph_prefixes.extend(str(value) for value in rule.get("path_prefixes") or [])
        graph_prefixes_t = tuple(graph_prefixes)

        def gate(pointer: str) -> str | None:
            try:
                canonical = business_brain.lexical_business_brain_pointer(pointer)
            except business_brain.BusinessBrainPointerError:
                return None
            if canonical in denied or not self._safe_knowledge_path(canonical):
                return None
            if canonical not in exact and not canonical.startswith(prefixes):
                return None
            if canonical not in graph_allowed and not canonical.startswith(graph_prefixes_t):
                return None
            return canonical

        return gate

    def validate_graph_target(self, client_scope: str | None, namespace: str, pointer: str) -> str:
        identity = self.validate_graph_namespace(client_scope, namespace)
        canonical = self.validate_brain_pointer(identity.scope_id, pointer)
        record = self.data["scopes"][identity.scope_id]
        allowed = set()
        prefixes = []
        for rule in record.get("graphify_targets") or []:
            if rule.get("namespace") == namespace:
                allowed.update(rule.get("paths") or [])
                prefixes.extend(str(value) for value in rule.get("path_prefixes") or [])
        if canonical not in allowed and not any(canonical.startswith(prefix) for prefix in prefixes):
            raise ClientScopeError(f"Graphify target does not belong to {identity.scope_id}: {canonical}")
        return canonical

    def validate_evidence_identity(self, client_scope: str | None, evidence_identity: str) -> str:
        identity, record = self._scope_record(client_scope)
        raw = str(evidence_identity or "")
        if not raw or raw not in set(record.get("evidence_identities") or []):
            raise ClientScopeError(f"evidence identity does not belong to {identity.scope_id}: {raw or '(missing)'}")
        return raw

    @staticmethod
    def capture_identity_hash(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""

    def resolve_capture_identity(
        self,
        *,
        sender: str | None = None,
        thread_id: str | None = None,
        sender_sha256: str | None = None,
        thread_sha256: str | None = None,
    ) -> CaptureIdentityResolution:
        """Resolve hashed capture metadata without opening message content."""
        values = {
            "sender": sender_sha256 or self.capture_identity_hash(sender),
            "thread": thread_sha256 or self.capture_identity_hash(thread_id),
        }
        matches_by_type: dict[str, set[str]] = {"sender": set(), "thread": set()}
        for scope_id, record in self.data.get("scopes", {}).items():
            if record.get("enabled") is not True:
                continue
            for identity in record.get("capture_identities") or []:
                match_type = str(identity.get("match_type") or "")
                if match_type in values and values[match_type] and identity.get("value_sha256") == values[match_type]:
                    matches_by_type[match_type].add(scope_id)
        if any(len(matches) > 1 for matches in matches_by_type.values()):
            return CaptureIdentityResolution(state="ambiguous")
        combined = set().union(*matches_by_type.values())
        if len(combined) > 1:
            return CaptureIdentityResolution(state="conflicting")
        if not combined:
            return CaptureIdentityResolution(state="unresolved")
        scope_id = next(iter(combined))
        identity = self.resolve_scope(scope_id)
        return CaptureIdentityResolution(
            state="matched",
            client_scope=scope_id,
            scope_kind=identity.kind,
            matched_by=tuple(sorted(key for key, matches in matches_by_type.items() if scope_id in matches)),
        )

    def validate_capture_evidence_reference(self, client_scope: str | None, reference: str) -> str:
        identity, record = self._scope_record(client_scope)
        raw = str(reference or "")
        prefixes = tuple(str(value) for value in record.get("capture_evidence_prefixes") or [])
        matching = tuple(prefix for prefix in prefixes if raw.startswith(prefix))
        if not raw or not matching:
            raise ClientScopeError(f"capture evidence does not belong to {identity.scope_id}")
        suffix = raw[len(max(matching, key=len)) :]
        if not suffix or not all(character.isalnum() or character in "-_" for character in suffix):
            raise ClientScopeError("capture evidence reference is malformed")
        return raw


def load_registry(path: Path | None = None) -> ClientScopeRegistry:
    return ClientScopeRegistry(registry_path=Path(path or DEFAULT_REGISTRY_PATH))
