"""Read layer-2 universal methodology (universal/<domain>/<id>/METHOD.md) with provenance.

Universal knowledge is company-neutral and independent of the Business Brain, so it is available on an
empty installation. It is read-only here and carries ``layer: universal`` provenance; it is never
counted as company ``brain_context_used``. Installs may add their own methods by pointing
TTROS_UNIVERSAL_ROOT at another folder with the same layout.

Revisit: when the method format or manifest changes. · Last touched: 2026-11-26.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
MAX_BYTES = 65536
WORD = re.compile(r"[a-z0-9]+")


class UniversalKnowledgeError(ValueError):
    """A universal method is missing, malformed or outside the universal root."""


@dataclass(frozen=True)
class Method:
    id: str
    title: str
    version: int
    domain: str
    summary: str
    keywords: tuple
    path: str
    content: str
    sha256: str

    def provenance(self) -> dict:
        row = asdict(self)
        row.pop("content")
        row.pop("keywords")
        return {**row, "layer": "universal", "client_scope": None}


def default_root() -> Path:
    return Path(os.environ.get("TTROS_UNIVERSAL_ROOT") or REPO_ROOT / "universal")


def _load(path: Path, root: Path) -> Method:
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise UniversalKnowledgeError(f"{path.name} exceeds {MAX_BYTES} bytes")
    text = data.decode("utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise UniversalKnowledgeError(f"{path} has no front matter")
    meta = yaml.safe_load(match.group(1)) or {}
    missing = [k for k in ("id", "title", "version", "domain", "summary") if not meta.get(k)]
    if missing:
        raise UniversalKnowledgeError(f"{path} lacks {', '.join(missing)}")
    return Method(id=str(meta["id"]), title=str(meta["title"]), version=int(meta["version"]), domain=str(meta["domain"]),
                  summary=str(meta["summary"]), keywords=tuple(str(k).lower() for k in meta.get("keywords") or ()),
                  path=path.relative_to(root.parent).as_posix() if root.parent in path.parents else path.name,
                  content=text[match.end():].strip(), sha256=hashlib.sha256(data).hexdigest())


def list_methods(root: Path | None = None) -> list[Method]:
    root = Path(root) if root is not None else default_root()
    if not root.is_dir():
        return []
    methods = [_load(p, root) for p in sorted(root.rglob("METHOD.md")) if "_pending" not in p.relative_to(root).parts]
    ids = [m.id for m in methods]
    if len(ids) != len(set(ids)):
        raise UniversalKnowledgeError("duplicate universal method id")
    return methods


def read(method_id: str, root: Path | None = None) -> Method:
    for method in list_methods(root):
        if method.id == method_id:
            return method
    raise UniversalKnowledgeError(f"unknown universal method: {method_id}")


def find(query: str, *, domain: str | None = None, limit: int = 3, root: Path | None = None) -> list[Method]:
    """Deterministic keyword ranking over title, summary, keywords and body. No model call."""
    terms = set(WORD.findall(query.lower()))
    scored = []
    for method in list_methods(root):
        if domain and method.domain != domain:
            continue
        bag = set(WORD.findall(f"{method.title} {method.summary} {' '.join(method.keywords)}".lower()))
        body = set(WORD.findall(method.content.lower()))
        phrase = sum(2 for k in method.keywords if k in query.lower())
        score = 3 * len(terms & bag) + len(terms & body) + phrase
        if score:
            scored.append((-score, method.id, method))
    return [m for _, _, m in sorted(scored)[:max(1, min(limit, 8))]]
