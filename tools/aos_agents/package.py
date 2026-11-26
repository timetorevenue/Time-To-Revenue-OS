"""Load and check one TAP v0.1 agent package from ``agents/packages/<name>/``.

A package is a plain directory. Loading validates the manifest against
agents/tap.schema.json, the pinned prompt hash, the pinned model against the
route table, both I/O schemas, and the handler's imports. Any failure refuses
the package; nothing runs from a package that did not load.

The import check is a lint, not a sandbox: handlers run in-process, so the
enforced boundary is that every read, write and model call goes through the
context object the runtime hands them.

Revisit: when the TAP schema or the handler contract changes. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

PACKAGES_DIR = Path("agents") / "packages"
TAP_SCHEMA_PATH = Path("agents") / "tap.schema.json"
PROMPT_PATH = "prompts/main.md"
HANDLER_PATH = "handler.py"
REF_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?:@(?P<version>\d+\.\d+\.\d+))?$")

# Modules a handler may import. Everything else (files, processes, network,
# environment) is reached only through the runtime context.
HANDLER_IMPORT_ALLOWLIST = frozenset({
    "__future__", "collections", "dataclasses", "datetime", "decimal", "functools", "hashlib",
    "html", "itertools", "json", "math", "re", "statistics", "string", "textwrap", "typing",
    "unicodedata",
})
HANDLER_FORBIDDEN_NAMES = frozenset({"open", "eval", "exec", "compile", "__import__", "breakpoint",
                                     "globals", "vars", "getattr", "setattr", "delattr"})
HANDLER_FORBIDDEN_ATTRIBUTES = frozenset({"environ", "__globals__", "__builtins__", "__subclasses__",
                                          "__class__", "__dict__", "_env", "_package"})


class PackageError(RuntimeError):
    """The package is missing, malformed or fails a load-time check."""


@dataclass(frozen=True)
class AgentPackage:
    name: str
    version: str
    kind: str
    directory: Path
    manifest: dict
    input_schema: dict
    output_schema: dict
    prompt: str = ""
    content_sha256: str = ""
    schemas: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def department(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def grants(self) -> frozenset[str]:
        return frozenset(self.manifest["capabilities"].get("grants") or [])

    @property
    def proposes(self) -> frozenset[str]:
        return frozenset(self.manifest["capabilities"].get("proposes") or [])

    @property
    def budget(self) -> dict:
        return dict(self.manifest["budget"])

    @property
    def model(self) -> dict:
        return dict(self.manifest.get("model") or {})


def parse_ref(ref: str) -> tuple[str, str | None]:
    match = REF_RE.match(str(ref or "").strip())
    if not match:
        raise PackageError(f"not an agent reference: {ref!r}")
    return match.group("name"), match.group("version")


def repo_root(root: Path | None = None) -> Path:
    return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]


def _load_json(path: Path, what: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError(f"{what} is unreadable: {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"{what} is not a JSON object: {path.name}")
    return value


def _inside(directory: Path, relative: str, what: str) -> Path:
    target = (directory / relative).resolve()
    if not target.is_relative_to(directory.resolve()) or not target.is_file():
        raise PackageError(f"{what} is missing from the package: {relative}")
    return target


def lint_handler(source: str) -> list[str]:
    """Return the reasons a handler source breaks the import/name allow-list."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in HANDLER_IMPORT_ALLOWLIST:
                    problems.append(f"line {node.lineno}: import {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in HANDLER_IMPORT_ALLOWLIST:
                problems.append(f"line {node.lineno}: import from {'.' * node.level}{node.module or ''} is not allowed")
        elif isinstance(node, ast.Name) and node.id in HANDLER_FORBIDDEN_NAMES:
            problems.append(f"line {node.lineno}: {node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in HANDLER_FORBIDDEN_ATTRIBUTES:
            problems.append(f"line {node.lineno}: .{node.attr} is not allowed")
    if not any(isinstance(node, ast.FunctionDef) and node.name == "run" for node in tree.body):
        problems.append("handler defines no top-level run(input, ctx)")
    return problems


def content_hash(directory: Path) -> str:
    """SHA-256 over the sorted package files; evidence and caches are excluded."""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory).as_posix()
        if relative.startswith(("evidence/", "evals/recordings/")) or "__pycache__" in relative or relative.endswith(".pyc"):
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def allowed_models(root: Path) -> list[str]:
    import aos_modelport

    return aos_modelport.allowed_models(root)


def load_package(ref: str, root: Path | None = None) -> AgentPackage:
    """Load ``name`` or ``name@version``. A version, when given, must be the installed one."""
    root = repo_root(root)
    name, version = parse_ref(ref)
    directory = root / PACKAGES_DIR / name
    manifest_path = directory / "agent.toml"
    if not manifest_path.is_file():
        raise PackageError(f"agent package is not installed: {name}")
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackageError(f"{name}: agent.toml is unreadable: {exc}") from exc

    tap_schema = _load_json(root / TAP_SCHEMA_PATH, "TAP schema")
    errors = sorted(jsonschema.Draft202012Validator(tap_schema).iter_errors(manifest), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.absolute_path) or "manifest"
        raise PackageError(f"{name}: agent.toml violates TAP 0.1 at {where}: {first.message[:200]}")

    agent = manifest["agent"]
    if agent["name"] != name:
        raise PackageError(f"{name}: directory name does not match agent.name {agent['name']!r}")
    if version is not None and version != agent["version"]:
        raise PackageError(f"{name}: version {version} requested, {agent['version']} installed")

    schemas: dict[str, dict] = {}
    for key in ("input_schema", "output_schema"):
        relative = manifest["io"][key]
        schema = _load_json(_inside(directory, relative, key), key)
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise PackageError(f"{name}: {relative} is not a valid JSON Schema: {exc.message[:200]}") from exc
        schemas[key] = schema
    extra: dict[str, dict] = {}
    schema_dir = directory / "schemas"
    if schema_dir.is_dir():
        for path in sorted(schema_dir.glob("*.json")):
            extra[path.name] = _load_json(path, "schema")

    prompt = ""
    kind = agent["kind"]
    if kind != "tool":
        model = manifest["model"]
        prompt_bytes = _inside(directory, PROMPT_PATH, "prompt").read_bytes()
        actual = hashlib.sha256(prompt_bytes).hexdigest()
        if actual != model["prompt_sha256"]:
            raise PackageError(f"{name}: prompt hash mismatch (manifest {model['prompt_sha256'][:12]}…, file {actual[:12]}…)")
        if model["id"] not in allowed_models(root):
            raise PackageError(f"{name}: model {model['id']} is not in the route table's supported models")
        if model.get("escalate_to") and model["escalate_to"] not in allowed_models(root):
            raise PackageError(f"{name}: escalation model {model['escalate_to']} is not supported")
        prompt = prompt_bytes.decode("utf-8")

    handler_source = _inside(directory, HANDLER_PATH, "handler").read_text(encoding="utf-8")
    problems = lint_handler(handler_source)
    if problems:
        raise PackageError(f"{name}: handler rejected: " + "; ".join(problems[:4]))

    return AgentPackage(
        name=name, version=agent["version"], kind=kind, directory=directory, manifest=manifest,
        input_schema=schemas["input_schema"], output_schema=schemas["output_schema"], prompt=prompt,
        content_sha256=content_hash(directory), schemas=extra,
    )


def load_handler(package: AgentPackage) -> Any:
    """Compile the already-linted handler and return its ``run`` callable."""
    path = package.directory / HANDLER_PATH
    source = path.read_text(encoding="utf-8")
    problems = lint_handler(source)
    if problems:
        raise PackageError(f"{package.name}: handler rejected: " + "; ".join(problems[:4]))
    namespace: dict[str, Any] = {"__name__": f"tap_handler_{package.name.replace('.', '_')}", "__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - linted package code
    return namespace["run"]


def installed(root: Path | None = None) -> list[str]:
    directory = repo_root(root) / PACKAGES_DIR
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if (path / "agent.toml").is_file())
