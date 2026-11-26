"""Classify repository paths into the three TTROS layers (software, universal, private).

Deterministic and model-free. The map lives in context/layers.json; anything it does not
match is private. Universal knowledge must also be registered in universal/manifest.json
with a redistribution decision, and only ``cleared`` entries are shareable.

    python3 tools/ttros_layers.py classify PATH [PATH...]
    python3 tools/ttros_layers.py check [--staged | --all]     # exit 1 on a layering violation
    python3 tools/ttros_layers.py shareable                     # tracked paths eligible for export review

Revisit: when a new top-level directory is added. · Last touched: 2026-11-26.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYERS_PATH = ROOT / "context" / "layers.json"
MANIFEST_PATH = ROOT / "universal" / "manifest.json"
ORDER = ("private", "universal", "software")
REDISTRIBUTION = {"cleared", "pending", "private"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def glob_re(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        char = pattern[i]
        out += "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
        i += 1
    return re.compile("^" + out + "$")


@lru_cache(maxsize=4)
def _compiled(text: str):
    layers = json.loads(text)
    if layers.get("default") != "private":
        raise ValueError("layers.json default must be private")
    return {name: [glob_re(p) for p in layers.get(name, [])] for name in ORDER}


def load_rules(path: Path = LAYERS_PATH):
    return _compiled(path.read_text(encoding="utf-8"))


def classify(path: str, rules=None) -> str:
    rel = path.replace("\\", "/").lstrip("./") if not path.startswith("/") else path
    if rel.startswith("/") or ".." in rel.split("/"):
        return "private"
    rules = rules or load_rules()
    for name in ORDER:
        if any(rx.match(rel) for rx in rules[name]):
            return name
    return "private"


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": {}}


def cleared_universal(manifest: dict | None = None) -> set[str]:
    """Universal paths cleared AND reviewed by the owner (a dated human decision). Everything else stays private."""
    manifest = manifest if manifest is not None else load_manifest()
    return {path for path, row in (manifest.get("entries") or {}).items()
            if row.get("redistribution") == "cleared" and DATE_RE.match(str(row.get("owner_review") or ""))}


def validate_manifest(root: Path = ROOT) -> list[str]:
    problems = []
    manifest = load_manifest(root / "universal" / "manifest.json")
    entries = manifest.get("entries") or {}
    on_disk = {p.relative_to(root).as_posix() for p in (root / "universal").rglob("*.md")
               if p.name != "README.md" and "_pending" not in p.parts}
    for path in sorted(on_disk - set(entries)):
        problems.append(f"{path}: universal knowledge file is not registered in universal/manifest.json")
    for path, row in sorted(entries.items()):
        if path not in on_disk:
            problems.append(f"{path}: manifest entry has no file")
        if row.get("redistribution") not in REDISTRIBUTION:
            problems.append(f"{path}: redistribution must be one of {sorted(REDISTRIBUTION)}")
        if row.get("redistribution") == "cleared" and not (row.get("authorship") == "original" and row.get("licence")):
            problems.append(f"{path}: cleared requires authorship=original and a licence")
        if not row.get("owner_review"):
            problems.append(f"{path}: owner_review (date or 'pending') is required")
    return problems


def tracked(root: Path = ROOT, staged: bool = False) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"] if staged else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def check(paths: list[str], root: Path = ROOT) -> list[str]:
    """Violations: universal files must be registered; nothing under company/ may be tracked as shareable."""
    problems = validate_manifest(root)
    rules = load_rules(root / "context" / "layers.json")
    for path in paths:
        layer = classify(path, rules)
        if path.startswith("company/") and layer != "private":
            problems.append(f"{path}: company/ must classify as private")
        if layer == "universal" and path.endswith(".md") and not path.endswith("README.md") \
                and path != "universal/manifest.json" and path not in (load_manifest(root / "universal" / "manifest.json").get("entries") or {}):
            problems.append(f"{path}: universal file missing from manifest")
    return sorted(set(problems))


def shareable(paths: list[str], root: Path = ROOT) -> list[str]:
    rules = load_rules(root / "context" / "layers.json")
    cleared = cleared_universal(load_manifest(root / "universal" / "manifest.json"))
    out = []
    for path in paths:
        layer = classify(path, rules)
        if layer == "software" or (layer == "universal" and (path in cleared or path in ("universal/README.md", "universal/manifest.json"))):
            out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("paths", nargs="+")
    k = sub.add_parser("check")
    k.add_argument("--staged", action="store_true")
    k.add_argument("--all", action="store_true")
    sub.add_parser("shareable")
    args = parser.parse_args(argv)
    if args.cmd == "classify":
        for path in args.paths:
            print(f"{classify(path):9s} {path}")
        return 0
    paths = tracked(staged=getattr(args, "staged", False) and not getattr(args, "all", False))
    if args.cmd == "check":
        problems = check(paths)
        for problem in problems:
            print("VIOLATION", problem)
        print(f"checked {len(paths)} paths; {len(problems)} violation(s)")
        return 1 if problems else 0
    for path in shareable(paths):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
