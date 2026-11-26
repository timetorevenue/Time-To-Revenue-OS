"""Turn-local discovery and execution of existing capabilities."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable

import jsonschema


class CapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class Capability:
    id: str
    description: str
    scope: str
    authorization: str
    schemas: Callable[[], tuple[dict, dict]]
    execute: Callable[[dict], dict]
    availability: str = "available"
    metadata: dict = field(default_factory=dict)
    details: Callable[[], dict] | None = None
    auto_inspect: bool = False
    direct: bool = False


def _terms(text: str) -> set[str]:
    return {word.removesuffix("s") for word in re.findall(r"[a-z0-9]+", text.casefold())
            if word not in {"a", "an", "the", "of", "in", "to", "for", "and", "with", "my", "me"}}


def compact(value: dict, limit: int = 3600) -> dict:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) <= limit:
        return value
    # Keep receipt identity and an explicit truncation marker; never imply a complete result.
    return {**{key: value[key] for key in ("id", "status", "operation", "provider_verified") if key in value},
            "truncated": True,
            "excerpt": encoded.encode()[:limit].decode("utf-8", errors="ignore")}


class CapabilitySession:
    """Discover scoped capabilities and validate the chosen contract before execution."""

    def __init__(self, sources: tuple[Callable[[], list[Capability]], ...]):
        self.sources = sources
        self.discovered: dict[str, Capability] = {}
        self.loaded: dict[str, tuple[dict, dict]] = {}
        self.attempted: set[str] = set()

    def discover(self, query: str, *, inspect_best: bool = False) -> dict:
        terms = _terms(query)
        if not terms:
            raise CapabilityError("Give a short search describing the capability you need.")
        ranked = []
        for source in self.sources:
            for cap in source():
                headline = _terms(cap.id)
                score = 3 * len(terms & headline) + len(terms & _terms(cap.description))
                if query.casefold().strip() == cap.id.casefold():
                    score += 1000
                if score:
                    ranked.append((score, cap.id, cap))
        matches = [entry[2] for entry in sorted(ranked, key=lambda entry: (-entry[0], entry[1]))[:5]]
        self.discovered.update((cap.id, cap) for cap in matches)
        result = {"matches": [self._describe(cap) for cap in matches]}
        if matches and (inspect_best or matches[0].id == query.strip()) and matches[0].auto_inspect:
            cap = matches[0]
            if cap.availability in {"available", "conditional"}:
                try:
                    result["selected"] = {"id": cap.id, "input_schema": self._load(cap.id)[0]}
                except CapabilityError as exc:
                    result["matches"][0].update(availability="unavailable", reason=str(exc))
        return result

    @staticmethod
    def _describe(cap: Capability) -> dict:
        return {"id": cap.id, "description": cap.description, "scope": cap.scope,
                "authorization": cap.authorization, "availability": cap.availability, **cap.metadata,
                **({"execute_with_arguments": True} if cap.auto_inspect else {})}

    def select(self, ident: str) -> dict:
        """Resolve a vetted exact operation without ranking unrelated sources."""
        for source in self.sources:
            for cap in source():
                if cap.id == ident:
                    self.discovered[ident] = cap
                    return self._describe(cap)
        raise CapabilityError("That capability is unavailable in this scope.")

    def _load(self, ident: str) -> tuple[dict, dict]:
        cap = self.discovered.get(ident)
        if cap is None:
            raise CapabilityError("Discover the capability before requesting its schemas.")
        if cap.availability not in {"available", "conditional"}:
            raise CapabilityError(cap.metadata.get("reason") or "This capability is unavailable.")
        if ident not in self.loaded:
            inputs, outputs = cap.schemas()
            if len(json.dumps((inputs, outputs)).encode()) > 6500:
                raise CapabilityError("The schemas exceed the inline budget.")
            self.loaded[ident] = (inputs, outputs)
        return self.loaded[ident]

    def inspect(self, ident: str) -> dict:
        inputs, outputs = self._load(ident)
        cap = self.discovered[ident]
        return {"id": ident, "input_schema": inputs, "output_schema": outputs,
                **({"execution": compact(cap.details(), 2600)} if cap.details else {})}

    def run(self, ident: str, arguments: dict, *, direct=False) -> dict:
        cap = self.discovered.get(ident)
        if direct and (cap is None or not cap.direct):
            raise CapabilityError("This capability needs conversational execution.")
        if ident not in self.loaded and not (cap and (cap.auto_inspect or direct)):
            raise CapabilityError("Inspect the capability before executing it.")
        inputs, outputs = self._load(ident)
        if not isinstance(arguments, dict):
            raise CapabilityError("Arguments must be a JSON object.")
        try:
            jsonschema.validate(arguments, inputs)
        except jsonschema.ValidationError as exc:
            field = ".".join(map(str, exc.absolute_path)) or "arguments"
            required = ", ".join(key for key in inputs.get("required", []) if key not in arguments)
            unknown = ", ".join(sorted(arguments.keys() - inputs.get("properties", {}).keys()))[:160]
            detail = (f" Missing required fields: {required}." if exc.validator == "required" else
                      f" Unexpected fields: {unknown}." if exc.validator == "additionalProperties" else "")
            raise CapabilityError(f"Invalid {field}: {exc.validator} constraint.{detail}") from None
        digest = hashlib.sha256(json.dumps([ident, arguments], sort_keys=True).encode()).hexdigest()
        if digest in self.attempted:
            raise CapabilityError("These arguments were already attempted in this turn. Reuse the earlier result.")
        self.attempted.add(digest)
        result = self.discovered[ident].execute(arguments)
        try:
            jsonschema.validate(result, outputs)
        except jsonschema.ValidationError:
            raise CapabilityError("The result failed its output schema. Inspect the underlying receipt before retrying.") from None
        return result
