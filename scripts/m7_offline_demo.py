#!/usr/bin/env python3
"""M7 offline demonstration: real readiness, then a SYNTHETIC Gold candidate staged, failed and rolled back.

Part 1 reads the real registry and, read-only, the production job store, receipts and ledger.
Part 2 builds a temporary root whose canary evidence is SYNTHETIC (tests/fixtures/m7) and is
labelled so everywhere: it proves the mechanics, never a real Gold result. No model is called,
nothing is promoted, and the live registry is checked unchanged at the end.

  python scripts/m7_offline_demo.py [--runtime-root /opt/ttros-core] [--out DIR]

Revisit: when release.py's commands or report shape change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "tools"), str(REPO / "tests" / "fixtures" / "m7")]

import m7_fixture as fx  # noqa: E402
from aos_agents import registry, release  # noqa: E402

AGENT = "knowledge.source_classify"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scrub(value, base: str):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)
    return json.loads(text.replace(base, "<scratch>"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime-root", type=Path, default=Path("/opt/ttros-core"))
    parser.add_argument("--out", type=Path, default=REPO / "proofs" / "m7_readiness_2026-11-16")
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    live_registry = REPO / "agents" / "registry.lock.json"
    registry_before = _sha(live_registry)
    log: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        log.append(text)

    # Part 1: genuine evidence only.
    say("== Part 1: REAL registered agents (registry in this checkout; jobs, receipts, ledger read-only) ==")
    if (args.runtime_root / "queue" / "jobs").is_dir():
        fingerprint = release.Runtime(args.runtime_root).fingerprint()
        real = release.check(None, REPO, runtime_root=args.runtime_root)
        (out / "real_readiness.json").write_text(json.dumps(real, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        (out / "real_readiness.txt").write_text(release.render(real))
        say(release.render(real))
        say(f"production job store unchanged by the read: {release.Runtime(args.runtime_root).fingerprint() == fingerprint}")
    else:
        say(f"runtime root {args.runtime_root} has no job store; real readiness not produced")

    # Part 2: synthetic evidence in a temporary root.
    base = Path(tempfile.mkdtemp(prefix="aos-m7-demo-"))
    try:
        root, runtime = fx.make_root(base)
        say("")
        say(f"== Part 2: {fx.SYNTHETIC} ==")
        fx.clean_canaries(root, runtime, 19, (AGENT,))
        partial = release.evaluate(AGENT, root, runtime_root=runtime)
        say(f"[2a] 19 synthetic clean canaries, no gate-7 evidence -> {partial['decision']} level={partial['level']} "
            f"eligible={partial['canaries']['eligible']} remaining={partial['canaries']['remaining_for_gold']}")
        for reason in partial["reasons"]:
            say(f"     - {reason}")
        fx.clean_canaries(root, runtime, 1, (AGENT,), start=50)
        twenty = release.evaluate(AGENT, root, runtime_root=runtime)
        say(f"[2b] 20 synthetic clean canaries, still no gate-7 evidence -> {twenty['decision']} level={twenty['level']}")
        for reason in twenty["reasons"]:
            say(f"     - {reason}")
        fx.write_reliability(root, AGENT)
        report = release.check([AGENT, "shared.planner"], root, runtime_root=runtime)
        again = release.check([AGENT, "shared.planner"], root, runtime_root=runtime)
        say(f"[2c] + SYNTHETIC passing gate-7 evidence; report reproducible: {report['report_sha256'] == again['report_sha256']} "
            f"({report['report_sha256'][:16]})")
        say(release.render(report))
        (out / "synthetic_readiness.json").write_text(json.dumps(_scrub(report, str(base)), indent=1, sort_keys=True) + "\n")

        # promote() reads the job store from its own root: give the temporary root the synthetic runtime.
        shutil.copytree(runtime / "queue", root / "queue", dirs_exist_ok=True)
        refused = registry.promote(AGENT, "active", root, approved_by="Casey (named approver, no command)")
        say(f"[2d] promote --to active on the temporary root with a named approver: promoted={refused['promoted']}")
        say(f"     reason: {refused['reason']}")

        stage_dir = base / "stage"
        staged = release.stage(AGENT, stage_dir, root, runtime_root=runtime, approved_by="SYNTHETIC demo approver")
        say(f"[2e] staged={staged['staged']} registry_unchanged={staged['registry_unchanged']} rollback_target={staged['rollback_target']} "
            f"authority={staged['authorization']['authority']['basis']} authorized={staged['authorization']['authority']['authorized']}")
        for change in staged["proposed_changes"]:
            say(f"     proposed: {json.dumps(change, sort_keys=True)}")
        shutil.copy(staged["manifest"], out / "synthetic_release_candidate.json")
        shutil.copy(staged["rollback"], out / "synthetic_rollback_manifest.json")

        failed = release.drill(staged["manifest"], base / "scratch-fail", root, runtime_root=runtime, fail_after_activation=True)
        say(f"[2f] scratch drill, simulated failure after activation: phases={failed['phases']} "
            f"(real authority verdict: authorized={failed['authorization']['authorized']})")
        say(f"     result={json.dumps(failed['result'], sort_keys=True)}")
        interrupted = release.drill(staged["manifest"], base / "scratch-interrupt", root, runtime_root=runtime, interrupt="after_write")
        say(f"[2g] interrupted after the registry write: phases={interrupted['phases']} "
            f"restored={interrupted['result']['restored_to_previous']}")
        (out / "synthetic_rollback_drill.json").write_text(
            json.dumps(_scrub({"failed_after_activation": failed, "interrupted": interrupted, "promote_active": refused}, str(base)),
                       indent=1, sort_keys=True) + "\n")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    unchanged = _sha(live_registry) == registry_before
    say("")
    say(f"live registry {live_registry.relative_to(REPO)} unchanged: {unchanged}")
    (out / "demo_output.txt").write_text("\n".join(log).replace(str(base), "<scratch>") + "\n")
    return 0 if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
