#!/usr/bin/env python3
"""Read-only clean-break cutover preflight. It changes nothing: no git write, no service action.

pre   (after the maintenance stops, before any main change): proves main is exactly the
      known HEAD + independently owned WIP, that only the restorable files block an
      ff-only merge, that no untracked path collides, and that the units are stopped.
post  (after ff-only and the manual start): proves main == candidate, the owned
      addendum and untracked inventory survived, Example Dealership files are unchanged.

Revisit: when the cutover commit, preserved WIP commit or unit set changes. · 2026-11-15.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MAIN = Path("/opt/ttros-core")
EXPECTED_MAIN = "29010887833ea72f7f9fe5c067004a81b3bd6ba4"
CANDIDATE_REF = "clean-break/fable-david"
WIP_COMMIT = "57261b73a9c9695213e770d7d4853be2ef23bb5a"      # repair/late-canary-origin-20261115
OWNED_ADDENDUM = "workflows/queue_artifacts/2026-11-13_david_cli_receipt.md"  # another owner; kept dirty
THREE_WAY = "dashboard/backend/main.py"                       # main HEAD + WIP delta, not the WIP blob
STOPPED_UNITS = ("aos-backend-stale.path", "aos-backend.service", "aos-runner.service", "aos-bridge.service",
                 "aos-gmail-capture.timer", "aos-morning-brief.timer", "aos-claude-handoff.timer",
                 "aos-brain-git-sweep.timer")
UNTOUCHED_UNITS = ("aos-example-dealership.service",)               # independent; must stay running throughout


def git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode().strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dirty_tracked(repo: Path) -> list[str]:
    rows = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no", binary=True).split(b"\0")
    return sorted(row[3:].decode() for row in rows if row)


def untracked(repo: Path) -> list[str]:
    rows = git(repo, "ls-files", "--others", "--exclude-standard", "-z", binary=True).split(b"\0")
    return sorted(row.decode() for row in rows if row)


def expected_wip_bytes(repo: Path, path: str, head: str) -> bytes:
    if path != THREE_WAY:
        return git(repo, "show", f"{WIP_COMMIT}:{path}", binary=True)
    # Reproduce main HEAD + the WIP commit's delta exactly, without touching the work tree.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name, rev in (("ours", head), ("base", f"{WIP_COMMIT}~1"), ("theirs", WIP_COMMIT)):
            files[name] = Path(tmp) / name
            files[name].write_bytes(git(repo, "show", f"{rev}:{path}", binary=True))
        merged = subprocess.run(["git", "merge-file", "-p", str(files["ours"]), str(files["base"]), str(files["theirs"])],
                                capture_output=True)
        if merged.returncode:
            raise RuntimeError(f"{path}: WIP delta no longer applies cleanly to main HEAD")
        return merged.stdout


def unit_state(unit: str) -> str:
    out = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def pre(repo: Path, candidate: str, *, check_units: bool) -> dict:
    problems: list[str] = []
    head = git(repo, "rev-parse", "HEAD")
    target = git(repo, "rev-parse", candidate)
    if head != EXPECTED_MAIN:
        problems.append(f"main HEAD is {head}, expected {EXPECTED_MAIN}")
    if git(repo, "merge-base", head, target) != head:
        problems.append("candidate is not a fast-forward of main HEAD")
    changed = set(git(repo, "diff", "--name-only", head, target).splitlines())
    dirty = dirty_tracked(repo)
    restore, mismatched = [], []
    for path in dirty:
        if path == OWNED_ADDENDUM:
            if path in changed:
                problems.append(f"candidate changes the independently owned {path}")
            continue
        if path not in changed and path == THREE_WAY:
            problems.append(f"{path} is dirty but the candidate does not change it; owner decision needed")
            continue
        # A dirty file the candidate leaves alone is restored to committed code only when its
        # bytes are preserved in the WIP commit; otherwise it belongs to an unknown owner.
        try:
            expected = expected_wip_bytes(repo, path, head)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            mismatched.append(f"{path}: {exc}")
            continue
        if sha((repo / path).read_bytes()) != sha(expected):
            mismatched.append(f"{path}: work tree differs from main HEAD + {WIP_COMMIT[:7]}")
        else:
            restore.append(path)
    problems += mismatched
    added = set(git(repo, "diff", "--name-only", "--diff-filter=A", head, target).splitlines())
    collisions = sorted(added & set(untracked(repo)))
    problems += [f"untracked path would be overwritten: {path}" for path in collisions]
    units = {}
    if check_units:
        units = {unit: unit_state(unit) for unit in STOPPED_UNITS + UNTOUCHED_UNITS}
        problems += [f"{unit} is {state}; stop it first" for unit, state in units.items()
                     if unit in STOPPED_UNITS and state not in {"inactive", "failed"}]
        problems += [f"{unit} is {state}; it must keep running" for unit, state in units.items()
                     if unit in UNTOUCHED_UNITS and state != "active"]
    return {"phase": "pre", "ok": not problems, "problems": problems, "main_head": head, "candidate": target,
            "restore_exactly": restore, "kept_dirty": [p for p in dirty if p == OWNED_ADDENDUM],
            "untracked_count": len(untracked(repo)), "units": units,
            "next_commands": [] if problems else [
                f"git -C {repo} restore --source=HEAD --worktree -- " + " ".join(restore),
                f"git -C {repo} merge --ff-only {target}"]}


def post(repo: Path, candidate: str, *, addendum_sha256: str, min_untracked: int) -> dict:
    problems = []
    head, target = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", candidate)
    if head != target:
        problems.append(f"main HEAD {head} != candidate {target}")
    dirty = dirty_tracked(repo)
    if dirty != [OWNED_ADDENDUM]:
        problems.append(f"unexpected tracked dirt after merge: {dirty}")
    if addendum_sha256 and sha((repo / OWNED_ADDENDUM).read_bytes()) != addendum_sha256:
        problems.append("the independently owned receipt addendum changed")
    count = len(untracked(repo))
    if count < min_untracked:
        problems.append(f"untracked inventory shrank: {count} < {min_untracked}")
    if git(repo, "diff", "--name-only", EXPECTED_MAIN, head, "--", "workspaces/example_dealership_sales_coach"):
        problems.append("Example Dealership workspace files changed")
    return {"phase": "post", "ok": not problems, "problems": problems, "main_head": head, "untracked_count": count}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", choices=("pre", "post"))
    parser.add_argument("--repo", type=Path, default=MAIN)
    parser.add_argument("--candidate", default=CANDIDATE_REF)
    parser.add_argument("--no-units", action="store_true", help="skip systemd checks (fixtures only)")
    parser.add_argument("--addendum-sha256", default="")
    parser.add_argument("--min-untracked", type=int, default=0)
    args = parser.parse_args(argv)
    if args.phase == "pre":
        report = pre(args.repo, args.candidate, check_units=not args.no_units)
    else:
        report = post(args.repo, args.candidate, addendum_sha256=args.addendum_sha256, min_untracked=args.min_untracked)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
