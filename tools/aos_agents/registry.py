"""Agent registry: pack a package, record it as a candidate, and gate promotion.

The registry is one lock file, ``agents/registry.lock.json``. An entry records
which package content was evaluated, the level its evidence supported, where
its archive is and its release state. The state is a record only: nothing in
the running system routes work by it yet.

Promotion never trusts the lock file for the level. It re-reads the evidence
and compares it with the package as it is on disk, so a package that changed
after it was evaluated, or one with no evidence, cannot be promoted.

  candidate  registered with evidence; not used
  shadow     needs bronze (gates 1-3)
  canary     needs silver (gates 1-6)
  active     needs gold (M7 READY, aos_agents.release)

A step up into canary or active is a release: the runtime permission authority (aos_authority)
must authorize it. ``approved_by`` is recorded with the event but never authorizes on its own.
Stepping down is always allowed.

Identity versus evidence: ``package_sha256`` (package.content_hash) excludes ``evidence/`` and
``evals/recordings/``, so adding acceptance evidence (gates 1-6, gate 7) never changes a
package's identity, and the evidence files pin that hash rather than anything derived from them.
The archive bundles the evidence, so its hash does change; ``repack`` re-packs an unchanged
package and records only the new archive.

Revisit: when release states, level rules or the archive layout change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import evals
from .package import PackageError, load_package, repo_root

REGISTRY_PATH = Path("agents") / "registry.lock.json"
DIST_DIR = Path("agents") / "dist"          # build output; git-ignored, re-created by pack
ARCHIVE_FORMAT = "tar.gz"                   # the blueprint names tar.zst; no zstd is installed here
LEVEL_ORDER = ("none", "bronze", "silver", "gold")
PROMOTION_ORDER = ("candidate", "shadow", "canary", "active")
REQUIRED_LEVEL = {"shadow": "bronze", "canary": "silver", "active": "gold"}
NEEDS_APPROVAL = ("canary", "active")
HISTORY_KEPT = 30


class RegistryError(RuntimeError):
    """The package cannot be packed or registered as it stands."""


def _rank(state: str | None) -> int:
    return PROMOTION_ORDER.index(state) if state in PROMOTION_ORDER else -1


def level_at_least(level: str, required: str) -> bool:
    return level in LEVEL_ORDER and LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(required)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(root: Path | None = None) -> dict:
    path = repo_root(root) / REGISTRY_PATH
    if not path.is_file():
        return {"registry": "ttros-agents", "version": 1, "agents": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RegistryError(f"{REGISTRY_PATH.as_posix()} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise RegistryError(f"{REGISTRY_PATH.as_posix()} is not a registry")
    return data


def _save(root: Path, data: dict) -> None:
    path = root / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def pack(agent_ref: str, root: Path | None = None, *, out_dir: Path | None = None) -> dict:
    """Write ``<name>-<version>.tar.gz``: the package with its evidence and recorded replies.

    The archive is reproducible: the same files give the same bytes. A consumer can
    unpack it, compare the content hash and replay the evals on their own binding.
    """
    root = repo_root(root)
    gate1 = evals.check(agent_ref, root)
    if not gate1["pass"]:
        raise RegistryError(f"{agent_ref} fails gate 1 and is not packed: {'; '.join(gate1['problems'][:3])}")
    package = load_package(agent_ref, root)
    prefix = f"{package.name}-{package.version}"
    files = sorted(path for path in package.directory.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for path in files:
                data = path.read_bytes()
                info = tarfile.TarInfo(f"{prefix}/{path.relative_to(package.directory).as_posix()}")
                info.size, info.mtime, info.mode = len(data), 0, 0o644
                archive.addfile(info, io.BytesIO(data))
    target_dir = Path(out_dir) if out_dir is not None else root / DIST_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{prefix}.{ARCHIVE_FORMAT}"
    target.write_bytes(buffer.getvalue())
    shown = target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)
    return {"agent": package.ref, "archive": shown, "format": ARCHIVE_FORMAT, "bytes": len(buffer.getvalue()),
            "files": len(files), "archive_sha256": hashlib.sha256(buffer.getvalue()).hexdigest(),
            "package_sha256": package.content_sha256}


def register(agent_ref: str, root: Path | None = None, *, built: dict | None = None, archive: dict | None = None,
             holdout: dict | None = None) -> dict:
    """Record an evaluated package as a candidate. A package with no supporting evidence is refused."""
    root = repo_root(root)
    try:
        package = load_package(agent_ref, root)
    except PackageError as exc:
        raise RegistryError(str(exc)) from exc
    level = evals.current_level(package.ref, root)
    if level["level"] == "none":
        raise RegistryError(f"{package.ref} has no level to register: {level.get('reason')}")
    data = load(root)
    previous = data["agents"].get(package.name) or {}
    same = previous.get("package_sha256") == package.content_sha256 and previous.get("version") == package.version
    state = previous.get("state", "candidate") if same else "candidate"
    history = list(previous.get("history") or [])
    history.append({"at": _now(), "event": "registered", "version": package.version, "level": level["level"],
                    "state": state, "package_sha256": package.content_sha256})
    entry: dict[str, Any] = {
        "version": package.version, "kind": package.kind, "state": state, "level": level["level"],
        "binding": evals.binding(package), "package_sha256": package.content_sha256,
        "evidence": level.get("evidence"), "evaluated_at": level.get("evaluated_at"),
        "built": built or {"by": "hand"}, "holdout": holdout,
        "archive": {"path": archive["archive"], "sha256": archive["archive_sha256"], "bytes": archive["bytes"],
                    "format": archive["format"]} if archive else None,
        "history": history[-HISTORY_KEPT:],
    }
    data["agents"][package.name] = entry
    _save(root, data)
    return entry


def promote(agent_ref: str, to: str, root: Path | None = None, *, approved_by: str = "", note: str = "", release_command=None) -> dict:
    """Move a registered package to a release state, or refuse and say why. Never raises for a refusal."""
    root = repo_root(root)
    result: dict[str, Any] = {"agent": str(agent_ref), "to": to, "promoted": False, "reason": ""}

    def refuse(reason: str) -> dict:
        result["reason"] = reason
        return result

    if to not in PROMOTION_ORDER:
        return refuse(f"unknown state {to!r}; states are {', '.join(PROMOTION_ORDER)}")
    try:
        data = load(root)
        package = load_package(agent_ref, root)
    except (RegistryError, PackageError) as exc:
        return refuse(str(exc))
    result["agent"] = package.ref
    entry = data["agents"].get(package.name)
    if not entry:
        return refuse("not registered: build it with the Factory, or register an evaluated package with aos-agent.py register")
    result["from"] = entry.get("state")
    if to != "candidate":          # stepping back to candidate is always allowed
        level = evals.current_level(package.ref, root)
        result["level"] = level["level"]
        if level["level"] == "none":
            return refuse(f"evidence is missing or does not match the package: {level.get('reason')}")
        if entry.get("package_sha256") != package.content_sha256 or entry.get("version") != package.version:
            return refuse("the package differs from the registered candidate; rebuild or register it again")
        if to == "active":
            # Gold is awarded only by M7 from real canary evidence read from this root's job store.
            from . import release

            readiness = release.evaluate(package.ref, root, target="active")
            result["level"] = readiness.get("level", level["level"])
            if readiness["decision"] != release.READY:
                return refuse(f"active needs M7 READY (gold); M7 says {readiness['decision']}: "
                              + "; ".join((readiness["blockers"] + readiness["reasons"])[:3]))
        elif not level_at_least(level["level"], REQUIRED_LEVEL[to]):
            return refuse(f"{to} needs {REQUIRED_LEVEL[to]}; the evidence supports {level['level']}")
        if entry.get("holdout") is not None and not entry["holdout"].get("pass"):
            return refuse("the held-out cases did not pass")
        if to in NEEDS_APPROVAL and _rank(to) > _rank(entry.get("state")):
            # Releasing a package to production work (canary) or the default route (active) is a
            # consequential action: the runtime permission authority decides, never --approved-by.
            from . import release

            verdict = release.authorization(root, package.ref, to, release_command=release_command)
            result["authorization"] = verdict
            if not verdict["authorized"]:
                return refuse(f"{to} is held by the runtime permission authority ({verdict['basis']}): {verdict['detail']}; "
                              "a named approver does not authorize it")
        if to in NEEDS_APPROVAL and not approved_by.strip():
            return refuse(f"{to} needs Casey's approval: pass --approved-by with who approved it")
    entry["history"] = (list(entry.get("history") or []) + [{
        "at": _now(), "event": "promoted" if to != "candidate" else "demoted", "from": entry.get("state"), "to": to,
        "level": result.get("level", entry.get("level")), "approved_by": approved_by.strip(), "note": note.strip()[:200],
        **({"authorization": result["authorization"]} if result.get("authorization") else {})}])[-HISTORY_KEPT:]
    entry["state"] = to
    _save(root, data)
    result.update(promoted=True, reason="state recorded; nothing routes by release state yet")
    return result


def repack(agent_ref: str, root: Path | None = None, *, note: str = "") -> dict:
    """Re-pack a registered package whose evidence changed, and record only the new archive.

    The package must be byte-for-byte the registered one (same content hash and version);
    a changed package needs a rebuild and ``register``. State, level, built, holdout and
    package hash are left exactly as they are; one ``repacked`` history event names both archives.
    """
    root = repo_root(root)
    try:
        package = load_package(agent_ref, root)
    except PackageError as exc:
        raise RegistryError(str(exc)) from exc
    data = load(root)
    entry = data["agents"].get(package.name)
    if not entry:
        raise RegistryError(f"{package.ref} is not registered; register it first")
    if entry.get("package_sha256") != package.content_sha256 or entry.get("version") != package.version:
        raise RegistryError(f"{package.ref} itself changed since it was registered; rebuild and register it "
                            "(repack only follows evidence changes)")
    packed = pack(package.ref, root)
    previous = (entry.get("archive") or {}).get("sha256")
    changed = previous != packed["archive_sha256"]
    if changed:
        entry["archive"] = {"path": packed["archive"], "sha256": packed["archive_sha256"], "bytes": packed["bytes"],
                            "format": packed["format"]}
        entry["history"] = (list(entry.get("history") or []) + [{
            "at": _now(), "event": "repacked", "package_sha256": package.content_sha256, "state": entry.get("state"),
            "archive_sha256": packed["archive_sha256"], "previous_archive_sha256": previous,
            "note": (note.strip() or "evidence changed; package identity unchanged")[:200]}])[-HISTORY_KEPT:]
        _save(root, data)
    return {"agent": package.ref, "package_sha256": package.content_sha256, "archive_sha256": packed["archive_sha256"],
            "previous_archive_sha256": previous, "changed": changed, "state": entry.get("state")}


def listing(root: Path | None = None) -> list[dict]:
    """Registered agents with the level their evidence supports right now."""
    root = repo_root(root)
    rows = []
    for name, entry in sorted(load(root)["agents"].items()):
        now = evals.current_level(name, root)
        rows.append({"agent": f"{name}@{entry.get('version')}", "state": entry.get("state"),
                     "registered_level": entry.get("level"), "current_level": now["level"],
                     "built_by": (entry.get("built") or {}).get("by"), "evaluated_at": entry.get("evaluated_at"),
                     **({"stale": now.get("reason")} if now["level"] != entry.get("level") else {})})
    return rows
