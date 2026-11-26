"""Every API call the rendered Dashboard can make is served by this backend.

Walks the frontend import graph from main.jsx, maps each used api.js function to its
method + path, and checks it against the FastAPI route table. Legacy views kept on disk
but not rendered (old queue/cockpit panels) are excluded by reachability, not by name;
from DashboardV1 only the components App imports are followed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dashboard/frontend/src"
# Multi-component modules: only the components actually imported (and their JSX children) are followed.
LIBRARIES = (SRC / "views/DashboardV1.jsx", SRC / "components/DashboardKit.jsx")


def api_calls() -> dict[str, tuple[str, str]]:
    text = (SRC / "api.js").read_text()
    bases = dict(re.findall(r"const (\w+) = axios\.create\(\{ baseURL: '([^']+)'", text))
    calls: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        m = re.match(r"export const (\w+) = .*?\b(\w+)\.(get|post|put|patch|delete)\((['`])([^'`]+)\4", line)
        if m:
            calls[m[1]] = (m[3].upper(), bases.get(m[2], "/api") + m[5])
    for m in re.finditer(r"export (?:async )?(?:function|const) (\w+)[^\n]*\n((?:  .*\n)+?)\}", text):
        inner = re.search(r"\b(\w+)\.(get|post)\((['`])([^'`]+)\3", m[2])
        if inner and m[1] not in calls:
            calls[m[1]] = (inner[2].upper(), bases.get(inner[1], "/api") + inner[4])
    return calls


def _resolve(base: Path, spec: str) -> Path | None:
    p = (base.parent / spec).resolve()
    for candidate in (p, p.with_suffix(".js"), p.with_suffix(".jsx"), p / "index.js", p / "index.jsx"):
        if candidate.is_file():
            return candidate
    return None


def _components(text: str) -> dict[str, str]:
    parts = re.split(r"\n(?=(?:export )?(?:default )?function \w+)", text)
    return {m[1]: part for part in parts if (m := re.match(r"(?:export )?(?:default )?function (\w+)", part))}


def rendered_sources() -> list[tuple[str, str]]:
    """(label, source text) for every module reachable from main.jsx; libraries by component."""
    seen: set[Path] = set()
    todo, out = [SRC / "main.jsx"], []
    wanted: dict[Path, set[str]] = {library: set() for library in LIBRARIES}
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text()
        for names, spec in re.findall(r"""import\s+(?:\w+\s*,\s*)?\{?([^}'"]*)\}?\s*from\s*['"](\.[^'"]+)['"]""", text):
            target = _resolve(path, spec)
            if target in wanted:
                wanted[target] |= {n.strip() for n in names.split(",") if n.strip()}
            elif target:
                todo.append(target)
        out.append((str(path.relative_to(SRC)), text))
    for library, roots in wanted.items():
        components, pending, followed = _components(library.read_text()), list(roots), set()
        while pending:
            name = pending.pop()
            if name in followed or name not in components:
                continue
            followed.add(name)
            body = components[name]
            out.append((f"{library.relative_to(SRC)}#{name}", body))
            pending += [child for child in components if re.search(rf"<{child}\b", body)]
        if library.name == "DashboardV1.jsx":
            assert {"GraphifyPage", "RepoIngest"} <= followed
    return out


def backend_routes() -> list[tuple[str, set[str]]]:
    import main
    return [(route.path, set(getattr(route, "methods", None) or ()))
            for route in main.app.routes if getattr(route, "path", "").startswith("/api")]


def served(method: str, path: str, routes) -> bool:
    concrete = re.sub(r"\$\{[^}]+\}", "x", path)
    return any(re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", rp), concrete) and (method in ms or not ms) for rp, ms in routes)


def test_every_rendered_dashboard_call_has_a_backend_route():
    calls, routes = api_calls(), backend_routes()
    assert ("GET", "/api/david/jobs") in calls.values() and len(calls) > 40
    missing = sorted({f"{label}: {name} -> {calls[name][0]} {calls[name][1]}"
                      for label, text in rendered_sources() for name in calls
                      if label != "api.js" and re.search(rf"\b{name}\b", text) and not served(*calls[name], routes)})
    assert missing == []


def test_jobs_projection_carries_every_field_the_list_views_read():
    import inspect
    import main
    projection = inspect.getsource(main.david_jobs)
    for field in ("id", "status", "topic", "request", "created_at", "finished_at", "detail",
                  "artifacts", "effects", "usage", "accounting", "receipts", "notification"):
        assert f'"{field}"' in projection, field
