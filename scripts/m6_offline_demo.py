#!/usr/bin/env python3
"""M6 offline end-to-end demonstration on a FIXTURE root. Zero model calls.

  1. build the fixture root (copy of agents/ + synthetic AOS-2099-* job records; the
     linkedin_post handler rebuilt without the 544f65c closing-question fix)
  2. run M6 twice with the same clock; the reports must be byte-identical
  3. show signals, the ranked proposals and the measured before/after
  4. run the EXISTING morning brief in dry-run on the fixture root and show that it
     consumes the proposals (no delivery, no schedule, no write to the live root)

Usage: python3 scripts/m6_offline_demo.py [--keep DIR]
This is fixture proof, not live runtime proof.

Revisit: when the fixture or the M6 report changes. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for path in (REPO, REPO / "tools", REPO / "tests" / "fixtures" / "m6"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_fixture  # noqa: E402
from aos_agents import improve  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, tzinfo=dt.timezone.utc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", type=Path, default=None, help="build the fixture here (must be empty) and keep it")
    args = parser.parse_args(argv)
    base = args.keep or Path(tempfile.mkdtemp(prefix="m6-demo-"))
    try:
        fixture = base / "root"
        built = build_fixture.build(fixture)
        print(f"FIXTURE root={fixture} jobs={built['jobs']} active linkedin_post evidence: "
              f"level={built['prefix_evidence']['level']} failed={built['prefix_evidence']['failed']}")
        first = improve.run(fixture, now=NOW, scratch_parent=base, out_dir=base / "run-a")
        second = improve.run(fixture, now=NOW, scratch_parent=base, out_dir=base / "run-b")
        a = (base / "run-a" / first["run_id"] / "report.json").read_bytes()
        b = (base / "run-b" / second["run_id"] / "report.json").read_bytes()
        print(f"REPRODUCIBLE run_id={first['run_id']} identical_reports={a == b} "
              f"measurement_sha256={first['measurement_sha256'][:16]}")
        if a != b:
            return 1
        print(f"EVIDENCE generation_jobs={len(first['evidence']['jobs_generation'])} "
              f"holdout_jobs={first['evidence']['jobs_holdout']} out_of_scope_excluded={first['evidence']['jobs_excluded_out_of_scope']}")
        for signal in first["signals"]:
            if signal.get("actionable") or signal["type"] == "review_hold":
                print(f"SIGNAL {signal['type']} {json.dumps(signal['key'])} n={signal.get('occurrences')} "
                      f"actionable={signal.get('actionable')} holdout={signal.get('holdout_confirmation')} "
                      f"evidence={signal.get('evidence_ids')}" + (" POLICY HOLD (never proposed)" if signal.get("policy_required") else ""))
        print(f"CANDIDATES considered={first['candidates_considered']} proposed={len(first['proposals'])} (max {improve.MAX_PROPOSALS})")
        for proposal in first["proposals"]:
            ev = proposal["evaluation"]
            print(f"PROPOSAL {proposal['rank']} {proposal['kind']} {proposal['agent']}: {ev['status']} — {ev.get('reason')}")
            for split in ("train", "holdout"):
                m = (ev.get("measured") or {}).get(split)
                if m:
                    print(f"   {split}: passed {m['before']['passed']}/{m['before']['ran']} -> {m['after']['passed']}/{m['after']['ran']}; "
                          f"failed {m['before']['failed']} -> {m['after']['failed']}; safety failed {m['before']['safety_failed']} -> "
                          f"{m['after']['safety_failed']}; exact tokens {m['comparable_tokens']['before']} -> {m['comparable_tokens']['after']}")
        for proposal in first["not_proposed"]:
            print(f"NOT PROPOSED {proposal['kind']} {proposal['agent']}: {proposal['evaluation']['status']} ({proposal.get('not_proposed') or proposal['evaluation'].get('reason')})")
        print(f"GUARANTEES {json.dumps({k: v for k, v in first['guarantees'].items() if k != 'protected_digest'})}")

        # The existing morning brief, dry-run, reading the proposals from the fixture root's results folder.
        shutil.copytree(base / "run-a", fixture / improve.OUTPUT_DIR)
        brain = base / "brain"
        (brain / "prospects").mkdir(parents=True)
        import aos_morning_brief

        brief = aos_morning_brief.run(root=fixture, brain_root=brain, now=NOW, dry_run=True)
        consumed = [row for row in brief["findings"]["findings"] if row["rule"] == "agent_improvement_proposal"]
        print(f"MORNING BRIEF status={brief['status']} model_invocations={brief['model_invocations']} "
              f"proposal_findings={len(consumed)}")
        section = brief["artifact"].split("## Agent Factory proposals", 1)
        print("## Agent Factory proposals" + section[1].split("\n\n", 1)[0] if len(section) == 2 else "NO PROPOSAL SECTION")
        return 0 if consumed else 1
    finally:
        if args.keep is None:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
