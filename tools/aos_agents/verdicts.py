"""Declared, schema-validated reliability verdicts for TAS gate 7.

Gate 7 asks whether repeats of one case reach the same verdict. A whole-output hash cannot
answer that: two runs that make the same decision in different words hash differently. This
module derives the verdict from fields each agent DECLARES in ``agents/reliability_verdicts.json``
and compares them exactly. Nothing is normalised or treated as "close enough": two different
decision values are two different verdicts.

The declarations live outside every package directory on purpose: a package's content hash (its
identity, canary evidence and registry pin) does not change when a verdict is declared. Evidence
pins the exact declaration it was computed with (``spec_sha256``), so a later edit invalidates it.

A declaration names JSON pointers into the agent's output, each with a kind that must match the
output schema at that pointer:

  enum     the schema has ``enum`` or ``const``; the value must be one of them
  boolean  the schema has ``type: boolean``
  pattern  the schema is a string with ``pattern``; the value must match it
  label    a short, single-line string (schema maxLength, when set, <= 200); compared exactly

A ``*`` segment walks every element of an array, in order. ``unordered: true`` compares that
field as a set; it is allowed only where the schema array is ``uniqueItems``.

Fail closed: an agent without a declaration, a declaration that does not fit the schema, an
output that does not validate against the schema, or a missing, invalid or duplicated verdict
value raises ``VerdictError``; it is never counted as agreement.

Revisit: when an agent's output schema or decision changes, or a new agent needs gate 7.
· Last touched: 2026-11-17.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

DECLARATIONS_PATH = Path("agents") / "reliability_verdicts.json"
KINDS = ("enum", "boolean", "pattern", "label")
LABEL_MAX = 200


class VerdictError(ValueError):
    """The verdict cannot be determined without guessing."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load(root: Path) -> dict:
    path = Path(root) / DECLARATIONS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerdictError(f"verdict declarations unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise VerdictError("verdict declarations must have an 'agents' object")
    return data


def declaration(package, root: Path) -> dict:
    """The validated declaration for ``package`` plus its pinned digest."""
    spec = load(root)["agents"].get(package.name)
    if not isinstance(spec, dict) or not isinstance(spec.get("fields"), list) or not spec["fields"]:
        raise VerdictError(f"no reliability verdict is declared for {package.name}")
    problems = check(spec, package.output_schema)
    if problems:
        raise VerdictError(f"verdict declaration for {package.name} does not fit its output schema: {'; '.join(problems)}")
    fields = [{"pointer": f["pointer"], "kind": f["kind"], "unordered": bool(f.get("unordered"))} for f in spec["fields"]]
    return {"agent": package.name, "fields": fields,
            "spec_sha256": hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest()}


def _segments(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise VerdictError(f"pointer {pointer!r} must start with '/' and name a field")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _schema_at(schema: dict, segments: list[str]) -> tuple[dict | None, list[str], bool]:
    """Walk the schema; return (leaf schema, problems, nearest wildcard array is uniqueItems)."""
    node, problems, unique = schema, [], False
    for part in segments:
        if not isinstance(node, dict):
            return None, ["schema ends early"], unique
        if part == "*":
            if node.get("type") != "array" or not isinstance(node.get("items"), dict):
                return None, ["'*' must walk an array with an item schema"], unique
            unique = bool(node.get("uniqueItems"))
            node = node["items"]
            continue
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        if part not in properties:
            return None, [f"field {part!r} is not in the schema"], unique
        if part not in (node.get("required") or []):
            problems.append(f"field {part!r} is optional; a verdict field must be required")
        node = properties[part]
    return node, problems, unique


def check(spec: dict, schema: dict) -> list[str]:
    """Problems with a declaration against an output schema (empty = usable)."""
    problems: list[str] = []
    pointers = [f.get("pointer") for f in spec.get("fields") or [] if isinstance(f, dict)]
    if len(pointers) != len(spec.get("fields") or []):
        problems.append("each field must be an object")
    if len(set(map(str, pointers))) != len(pointers):
        problems.append("a pointer is declared twice (ambiguous)")
    for field in spec.get("fields") or []:
        if not isinstance(field, dict):
            continue
        pointer, kind = field.get("pointer"), field.get("kind")
        try:
            segments = _segments(pointer)
        except VerdictError as exc:
            problems.append(str(exc))
            continue
        if kind not in KINDS:
            problems.append(f"{pointer}: kind {kind!r} is not one of {', '.join(KINDS)}")
            continue
        leaf, found, unique = _schema_at(schema, segments)
        problems += [f"{pointer}: {p}" for p in found]
        if leaf is None:
            continue
        if field.get("unordered") and ("*" not in segments or not unique):
            problems.append(f"{pointer}: unordered is allowed only over a uniqueItems array")
        if kind == "enum" and not ("enum" in leaf or "const" in leaf):
            problems.append(f"{pointer}: kind enum needs a schema enum or const")
        elif kind == "boolean" and leaf.get("type") != "boolean":
            problems.append(f"{pointer}: kind boolean needs schema type boolean")
        elif kind == "pattern" and (leaf.get("type") != "string" or not isinstance(leaf.get("pattern"), str)):
            problems.append(f"{pointer}: kind pattern needs a string schema with a pattern")
        elif kind == "label" and (leaf.get("type") != "string" or int(leaf.get("maxLength") or 0) > LABEL_MAX):
            problems.append(f"{pointer}: kind label needs a short string schema (free text is not a verdict)")
    return problems


def _values(value: Any, segments: list[str], pointer: str) -> Any:
    if not segments:
        return value
    part, rest = segments[0], segments[1:]
    if part == "*":
        if not isinstance(value, list):
            raise VerdictError(f"{pointer}: expected an array")
        return [_values(item, rest, pointer) for item in value]
    if not isinstance(value, dict) or part not in value:
        raise VerdictError(f"{pointer}: missing")
    return _values(value[part], rest, pointer)


def _leaves(value: Any, depth: int):
    if depth == 0:
        yield value
        return
    for item in value:
        yield from _leaves(item, depth - 1)


def _check_leaf(value: Any, kind: str, leaf: dict, pointer: str) -> None:
    if kind == "enum":
        allowed = leaf["enum"] if "enum" in leaf else [leaf["const"]]
        if not any(value == a and type(value) is type(a) for a in allowed):   # 1 is not True
            raise VerdictError(f"{pointer}: {value!r} is not a declared value")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise VerdictError(f"{pointer}: not a boolean")
    elif kind == "pattern":
        if not isinstance(value, str) or re.search(leaf["pattern"], value) is None:
            raise VerdictError(f"{pointer}: does not match its schema pattern")
    elif kind == "label":
        if not isinstance(value, str) or len(value) > LABEL_MAX or "\n" in value or "\r" in value or value != value.strip():
            raise VerdictError(f"{pointer}: not a short single-line label")


def extract(package, root: Path, output: Any, *, declared: dict | None = None) -> dict:
    """The verdict of one run: {pointer: value}. Validates the whole output against its schema first."""
    from .runtime import _schema_error

    declared = declared or declaration(package, root)
    if not isinstance(output, dict):
        raise VerdictError("output is not an object")
    problem = _schema_error(output, package.output_schema)
    if problem:
        raise VerdictError(f"output does not validate against its schema: {problem}")
    verdict: dict[str, Any] = {}
    for field in declared["fields"]:
        pointer, kind = field["pointer"], field["kind"]
        segments = _segments(pointer)
        leaf, _, _ = _schema_at(package.output_schema, segments)
        value = _values(output, segments, pointer)
        for item in _leaves(value, segments.count("*")):
            _check_leaf(item, kind, leaf, pointer)
        if field["unordered"]:
            items = [_canonical(item) for item in value]
            if len(set(items)) != len(items):
                raise VerdictError(f"{pointer}: repeated element in an unordered set (ambiguous)")
            value = [json.loads(item) for item in sorted(items)]
        verdict[pointer] = value
    return verdict


def key(verdict: dict) -> str:
    """A stable identity for exact comparison: equal keys iff every declared value is equal."""
    return "verdict:" + hashlib.sha256(_canonical(verdict).encode("utf-8")).hexdigest()
