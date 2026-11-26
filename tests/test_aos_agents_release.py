"""M7 release readiness: canary evidence, Gold eligibility, offline staging and the rollback drill.

Zero model calls. Every test runs on a temporary copy of agents/ and a temporary runtime whose
jobs, receipts and ledger rows are SYNTHETIC (tests/fixtures/m7/m7_fixture.py). Fixture counts
prove the counting rules, never a real Gold result. The one live-evidence test only reads the
production job store, read-only, and skips when it is absent.

Revisit: when TAS levels, the acceptance record or the release states change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "tools", ROOT / "tests" / "fixtures" / "m7"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import m7_fixture as fx  # noqa: E402
from aos_agents import registry, release  # noqa: E402

CLASSIFY = "knowledge.source_classify"
PLANNER = "shared.planner"
LIVE = Path(os.environ.get("TTROS_M7_LIVE_ROOT", "/opt/ttros-core"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="aos-m7-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, self.runtime = fx.make_root(self.base)

    def evaluate(self, agent=CLASSIFY, target="active"):
        return release.evaluate(agent, self.root, runtime_root=self.runtime, target=target)

    def canaries(self, count, names=(CLASSIFY,), start=1, reliable=True):
        """``count`` accepted synthetic canaries; ``reliable`` also writes passing gate-7 evidence (a separate gate)."""
        if reliable:
            for name in names:
                fx.write_reliability(self.root, name)
        return fx.clean_canaries(self.root, self.runtime, count, names, start=start)


class Eligibility(Base):
    def test_19_clean_canaries_are_not_gold(self):
        self.canaries(19)
        report = self.evaluate()
        self.assertEqual(report["canaries"]["eligible"], 19)
        self.assertEqual(report["canaries"]["remaining_for_gold"], 1)
        self.assertEqual(report["level"], "silver")
        self.assertEqual(report["decision"], release.NOT_READY)
        self.assertTrue(report["gates"]["7"]["pass"])        # gate 7 passing never makes up a missing canary
        self.assertTrue(any("separately, 1 more clean real canary" in r for r in report["reasons"]))

    def test_20_distinct_clean_canaries_with_all_gates_are_ready(self):
        self.canaries(20)
        report = self.evaluate()
        self.assertEqual(report["canaries"]["eligible"], 20)
        self.assertEqual(report["level"], "gold")
        self.assertTrue(all(report["gates"][g]["pass"] for g in "1234567"))
        self.assertEqual(report["decision"], release.READY, report["blockers"] + report["reasons"])
        self.assertFalse(report["authorization"]["granted_by_this_check"])

    def test_one_multi_agent_run_counts_once_for_each_evidenced_package(self):
        self.canaries(1, names=fx.CANARY_FOUR)
        report = release.check(None, self.root, runtime_root=self.runtime)
        counts = {row["agent"].split("@")[0]: row["canaries"]["eligible"] for row in report["candidates"]}
        for name in fx.CANARY_FOUR:
            self.assertEqual(counts[name], 1, name)
        self.assertEqual(counts["crm.followup_draft"], 0)
        self.assertEqual(counts["research.synthesise"], 0)

    def test_duplicate_receipt_processing_cannot_inflate_the_count(self):
        jobs = self.canaries(3)
        for job in jobs:   # the same receipts imported again, under new record ids
            fx.add_acceptance(self.root, job, (CLASSIFY,), record_id=f"again-{job['id']}")
        fx.add_acceptance(self.root, jobs[0], (CLASSIFY,), record_id="third-time")
        report = self.evaluate()
        self.assertEqual(report["canaries"]["eligible"], 3)
        self.assertEqual(len(report["canaries"]["duplicates_ignored"]), 4)

    def test_runs_of_other_versions_or_bindings_never_share_counts(self):
        for number in range(1, 6):
            job = fx.add_job(self.root, self.runtime, number, (CLASSIFY,))
            fx.add_acceptance(self.root, job, (CLASSIFY,),
                              packages={CLASSIFY: fx.claim(self.root, CLASSIFY, binding="gpt-6-luna.medium.codex-cli")})
        for number in range(6, 9):
            job = fx.add_job(self.root, self.runtime, number, (CLASSIFY,))
            fx.add_acceptance(self.root, job, (CLASSIFY,), packages={CLASSIFY: fx.claim(self.root, CLASSIFY, version="0.2.0")})
        job = fx.add_job(self.root, self.runtime, 9, (CLASSIFY,), effort_override="high")   # ledger shows another effort
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        self.canaries(2, names=(PLANNER,), start=20)    # another agent's canaries
        report = self.evaluate()
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertEqual(len(report["canaries"]["incompatible"]), 8)
        excluded = report["canaries"]["excluded"]
        self.assertTrue(any("ledger shows gpt-6-luna/high" in r for item in excluded for r in item["reasons"]))
        self.assertEqual(self.evaluate(PLANNER)["canaries"]["eligible"], 2)


class EvidenceIntegrity(Base):
    def reasons(self, agent=CLASSIFY):
        report = self.evaluate(agent)
        return report, [reason for item in report["canaries"]["excluded"] for reason in item["reasons"]]

    def test_missing_receipt_or_job_fails_closed(self):
        job = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,))
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        (self.runtime / job["receipts"][0]["path"]).unlink()
        ghost = dict(job, id="AOS-2026-99999", finished_at=job["finished_at"])
        fx.add_acceptance(self.root, ghost, (CLASSIFY,))
        report, reasons = self.reasons()
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertTrue(any("receipt is missing" in r for r in reasons))
        self.assertTrue(any("job record unavailable" in r for r in reasons))

    def test_tampered_receipt_is_rejected(self):
        job = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,))
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        path = self.runtime / job["receipts"][0]["path"]
        path.write_text(path.read_text().replace("PASS", "PASS ", 1))
        report, reasons = self.reasons()
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertIn("receipt bytes do not match the sha256 in the job record", reasons)

    def test_failed_held_or_revised_outcomes_are_excluded(self):
        failed = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,), status="failed")
        step_failed = fx.add_job(self.root, self.runtime, 2, (CLASSIFY,), step_status={CLASSIFY: "failed"})
        revised = fx.add_job(self.root, self.runtime, 3, (CLASSIFY,), round_number=1)
        held = fx.add_job(self.root, self.runtime, 4, fx.CANARY_FOUR)
        not_accepted = fx.add_job(self.root, self.runtime, 5, (CLASSIFY,))
        for job in (failed, step_failed, revised):
            fx.add_acceptance(self.root, job, (CLASSIFY,))
        fx.add_acceptance(self.root, held, (CLASSIFY,), expected_held=())   # the held effect was not expected
        fx.add_acceptance(self.root, not_accepted, (CLASSIFY,), decision="not_accepted")
        report, reasons = self.reasons()
        self.assertEqual(report["canaries"]["eligible"], 0)
        for text in ("job status is failed, not done", "did not run ok in this job", "resumed or revised",
                     "was held, which the acceptance did not expect", "acceptance decision is not_accepted"):
            self.assertTrue(any(text in r for r in reasons), text)

    def test_fixture_simulated_and_replayed_runs_are_not_real_canaries(self):
        fixture_kind = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,))
        fx.add_acceptance(self.root, fixture_kind, (CLASSIFY,), kind="fixture")
        simulated = fx.add_job(self.root, self.runtime, 2, (CLASSIFY,), mark="simulated")
        fx.add_acceptance(self.root, simulated, (CLASSIFY,))
        replayed = fx.add_job(self.root, self.runtime, 3, (CLASSIFY,), replayed=True)
        fx.add_acceptance(self.root, replayed, (CLASSIFY,))
        report, reasons = self.reasons()
        self.assertEqual(report["canaries"]["eligible"], 0)
        for text in ("recorded as fixture, not real_canary", "job is marked simulated", "a step was replayed"):
            self.assertTrue(any(text in r for r in reasons), text)

    def test_hash_or_schema_change_invalidates_evidence(self):
        self.canaries(20)
        self.assertEqual(self.evaluate()["decision"], release.READY)
        schema = self.root / "agents" / "packages" / CLASSIFY / "schemas" / "output.json"
        data = json.loads(schema.read_text())
        data["description"] = "changed after acceptance"
        schema.write_text(json.dumps(data))
        report = self.evaluate()
        self.assertEqual(report["decision"], release.BLOCKED)
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertEqual(len(report["canaries"]["incompatible"]), 20)
        self.assertTrue(all("package hash" in row["differs"] for row in report["canaries"]["incompatible"]))
        self.assertIn("the package on disk differs from the registered package", report["blockers"])
        self.assertTrue(any("gate evidence is stale" in b for b in report["blockers"]))

    def test_missing_or_corrupt_ledger_is_reported_honestly(self):
        self.canaries(2)
        (self.runtime / "queue" / "token_ledger.jsonl").unlink()
        report, reasons = self.reasons()
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertFalse(report["canaries"]["ledger"]["available"])
        self.assertIn("ledger evidence unavailable: token ledger is missing", reasons)
        (self.runtime / "queue" / "token_ledger.jsonl").write_text("{not json\n")
        report, reasons = self.reasons()
        self.assertTrue(any("token ledger is unreadable" in r for r in reasons))
        job = fx.add_job(self.root, self.runtime, 30, (CLASSIFY,), ledger=False)
        (self.runtime / "queue" / "token_ledger.jsonl").write_text("")
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        report, reasons = self.reasons()
        self.assertTrue(any("ledger invocations differ" in r for r in reasons))


class Gate7IsNotTheCanaryRule(Base):
    """Gold = gates 1-7 AND 20 clean real canaries; neither requirement substitutes for the other."""

    def test_20_canaries_without_reliability_evidence_are_not_gold(self):
        self.canaries(20, reliable=False)
        report = self.evaluate()
        self.assertEqual(report["canaries"]["eligible"], 20)
        self.assertEqual(report["canaries"]["remaining_for_gold"], 0)
        self.assertFalse(report["gates"]["7"]["pass"])
        self.assertIn("no reliability evidence for this binding (gate 7 has not been run)", report["gates"]["7"]["problems"])
        self.assertEqual((report["level"], report["decision"]), ("silver", release.NOT_READY))
        self.assertTrue(any(r.startswith("gate 7 (reliability) not passed") for r in report["reasons"]))
        self.assertFalse(any("more clean real canary" in r for r in report["reasons"]))

    def test_passing_gate_7_without_canaries_is_not_gold(self):
        fx.write_reliability(self.root, CLASSIFY)
        report = self.evaluate()
        self.assertTrue(report["gates"]["7"]["pass"], report["gates"]["7"]["problems"])
        self.assertEqual((report["level"], report["decision"]), ("silver", release.NOT_READY))
        self.assertTrue(any("separately, 20 more clean real canary" in r for r in report["reasons"]))
        self.assertFalse(any(r.startswith("gate 7") for r in report["reasons"]))

    def test_every_gate_7_criterion_fails_closed(self):
        self.canaries(20, reliable=False)
        mixed = [{"case": f"case-{i}", "verdicts": ["ok", "ok", "needs_input"] if i < 3 else ["ok"] * 3} for i in range(5)]
        good_kill = {"signal": "SIGKILL", "resumed": True, "duplicate_effects": 0, "repeated_model_calls": 1}
        good_forced = {"kind": "auth", "outcome": "blocked", "truthful": True, "billed_fallback": False}
        variants = {
            "consistency 80%": {"repeats": mixed},
            "four cases": {"repeats": [{"case": f"c{i}", "verdicts": ["ok"] * 3} for i in range(4)]},
            "two repeats": {"repeats": [{"case": f"c{i}", "verdicts": ["ok"] * 2} for i in range(5)]},
            "replayed repeats": {"mode": "replay"},
            "duplicate effect": {"kill_resume": {**good_kill, "duplicate_effects": 1}},
            "two repeated calls": {"kill_resume": {**good_kill, "repeated_model_calls": 2}},
            "not killed": {"kill_resume": {**good_kill, "signal": "SIGTERM"}},
            "no kill probe": {"kill_resume": None},
            "billed fallback": {"forced_failure": {**good_forced, "billed_fallback": True}},
            "fallback unknown": {"forced_failure": {k: v for k, v in good_forced.items() if k != "billed_fallback"}},
            "untruthful": {"forced_failure": {**good_forced, "truthful": False}},
            "not blocked": {"forced_failure": {**good_forced, "outcome": "failed"}},
            "wrong failure": {"forced_failure": {**good_forced, "kind": "network"}},
            "no forced failure": {"forced_failure": None},
            "other package": {"package_sha256": "0" * 64},
            "marked synthetic": {"synthetic": True},
        }
        for label, override in variants.items():
            with self.subTest(label):
                fx.write_reliability(self.root, CLASSIFY, **override)
                report = self.evaluate()
                self.assertFalse(report["gates"]["7"]["pass"], label)
                self.assertEqual(report["decision"], release.NOT_READY, label)
                self.assertNotEqual(report["level"], "gold", label)
        fx.write_reliability(self.root, CLASSIFY, repeats=[{"case": f"case-{i}", "verdicts": ["ok", "ok", "x"] if i < 2 else ["ok"] * 3}
                                                          for i in range(5)])
        self.assertEqual(self.evaluate()["gates"]["7"]["same_verdict"], 0.867)
        self.assertEqual(self.evaluate()["decision"], release.READY)


class Authorization(Base):
    """A named approver never authorizes 'active'; the runtime permission authority does, and it holds."""

    def ready_root(self):
        self.canaries(20)
        shutil.copytree(self.runtime / "queue", self.root / "queue", dirs_exist_ok=True)   # promote() reads its own root
        self.assertEqual(release.evaluate(CLASSIFY, self.root, target="active")["decision"], release.READY)

    def test_unready_promotion_is_blocked(self):
        self.canaries(19)
        refused = registry.promote(CLASSIFY, "active", self.root, approved_by="Casey")
        self.assertFalse(refused["promoted"])
        self.assertIn("M7 READY", refused["reason"])

    def test_forged_or_named_approvals_do_not_authorize(self):
        self.ready_root()
        before = sha(self.root / "agents" / "registry.lock.json")
        for name in ("Casey", "Casey (explicit command)", "Casey 2026-11-16 approved active", "authorized", ""):
            with self.subTest(name):
                result = registry.promote(CLASSIFY, "active", self.root, approved_by=name)
                self.assertFalse(result["promoted"])
                self.assertIn("held by the runtime permission authority (approval_required)", result["reason"])
                self.assertIn("a named approver does not authorize it", result["reason"])
        self.assertEqual(sha(self.root / "agents" / "registry.lock.json"), before)
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "canary")

    def test_only_the_authority_decides_scratch_registry(self):
        import aos_authority

        self.ready_root()
        granted = {"authorized": True, "basis": "test_stub", "detail": "", "clarification_required": False}
        with mock.patch.object(aos_authority, "action_authorization", return_value=granted) as authority:
            unnamed = registry.promote(CLASSIFY, "active", self.root)
            self.assertFalse(unnamed["promoted"])                       # still recorded: who approved it
            done = registry.promote(CLASSIFY, "active", self.root, approved_by="Casey")
        self.assertTrue(done["promoted"], done["reason"])
        self.assertEqual(authority.call_args.kwargs["action"], "AGENT_RELEASE_ACTIVE")
        self.assertEqual(authority.call_args.kwargs["target"], f"{CLASSIFY}@0.1.0")
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "active")   # the scratch fixture root only

    def test_staged_manifest_carries_the_authority_verdict_not_the_name(self):
        self.canaries(20)
        staged = release.stage(CLASSIFY, self.base / "stage", self.root, runtime_root=self.runtime, approved_by="Casey")
        auth = json.loads(Path(staged["manifest"]).read_text())["authorization"]
        self.assertEqual(auth["named_approver"], "Casey")
        self.assertFalse(auth["authority"]["authorized"])
        self.assertEqual(auth["authority"]["basis"], "approval_required")


class CanaryAuthorization(Base):
    """'canary' is a release too: the runtime permission authority decides, never --approved-by."""

    def shadow_classify(self):
        result = registry.promote(CLASSIFY, "shadow", self.root)        # a step down needs no authorization
        self.assertTrue(result["promoted"], result["reason"])

    def test_named_or_forged_approvals_do_not_release_to_canary(self):
        self.shadow_classify()
        before = sha(self.root / "agents" / "registry.lock.json")
        for name in ("Casey", "Casey 2026-11-16 (explicit command)", "approved", "aos_authority: authorized", ""):
            with self.subTest(name):
                result = registry.promote(CLASSIFY, "canary", self.root, approved_by=name)
                self.assertFalse(result["promoted"])
                self.assertIn("canary is held by the runtime permission authority (approval_required)", result["reason"])
                self.assertEqual(result["authorization"]["action"], "AGENT_RELEASE_CANARY")
        self.assertEqual(sha(self.root / "agents" / "registry.lock.json"), before)
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "shadow")

    def test_cli_approved_by_does_not_release_to_canary(self):
        self.shadow_classify()
        code, text = CommandLine.run_cli(self, "promote", CLASSIFY, "--to", "canary", "--approved-by", "Casey")
        self.assertEqual(code, 1)
        self.assertIn("held by the runtime permission authority", json.loads(text)["reason"])
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "shadow")

    def test_only_the_authority_releases_to_canary_scratch_registry(self):
        import aos_authority

        self.shadow_classify()
        granted = {"authorized": True, "basis": "test_stub", "detail": "", "clarification_required": False}
        with mock.patch.object(aos_authority, "action_authorization", return_value=granted) as authority:
            self.assertFalse(registry.promote(CLASSIFY, "canary", self.root)["promoted"])   # who approved is still recorded
            done = registry.promote(CLASSIFY, "canary", self.root, approved_by="Casey")
        self.assertTrue(done["promoted"], done["reason"])
        self.assertEqual(authority.call_args.kwargs["action"], "AGENT_RELEASE_CANARY")
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "canary")

    def test_stepping_down_never_consults_the_authority(self):
        import aos_authority

        def must_not_be_asked(*args, **kwargs):
            raise AssertionError("a demotion must not need authorization")

        with mock.patch.object(aos_authority, "action_authorization", must_not_be_asked):
            for state in ("shadow", "candidate"):
                self.assertTrue(registry.promote(CLASSIFY, state, self.root)["promoted"], state)


class EvidenceIsNotIdentity(Base):
    """Gate-7 evidence never changes package identity; only the archive that bundles it changes."""

    def test_adding_gate_7_evidence_leaves_package_identity_and_canaries_intact(self):
        from aos_agents import factory
        from aos_agents.package import load_package

        self.canaries(20, reliable=False)
        identity = load_package(CLASSIFY, self.root).content_sha256
        entry_before = fx.registry_entry(self.root, CLASSIFY)
        path = fx.write_reliability(self.root, CLASSIFY, repack=False)
        self.assertEqual(load_package(CLASSIFY, self.root).content_sha256, identity)
        self.assertEqual(json.loads(path.read_text())["package_sha256"], identity)       # evidence pins identity, not itself
        checks = factory.verify(CLASSIFY, self.root)["checks"]
        self.assertTrue(checks["package_matches_registry"] and checks["evidence_matches_package"])
        self.assertTrue(checks.get("no_hand_edits", True))
        stale = self.evaluate()
        self.assertEqual(stale["decision"], release.BLOCKED)                             # the archive no longer reproduces
        self.assertTrue(any("release repack knowledge.source_classify" in b for b in stale["blockers"]))
        self.assertEqual(stale["canaries"]["eligible"], 20)                              # canary identity unaffected

        result = registry.repack(CLASSIFY, self.root)
        self.assertTrue(result["changed"])
        entry = fx.registry_entry(self.root, CLASSIFY)
        for key in ("package_sha256", "version", "state", "level", "binding", "built", "holdout", "evidence"):
            self.assertEqual(entry[key], entry_before[key], key)
        self.assertNotEqual(entry["archive"]["sha256"], entry_before["archive"]["sha256"])
        self.assertEqual(entry["history"][-1]["event"], "repacked")
        self.assertEqual(entry["history"][-1]["previous_archive_sha256"], entry_before["archive"]["sha256"])
        ready = self.evaluate()
        self.assertEqual(ready["decision"], release.READY)
        self.assertTrue(all(row["identity"]["verified"] for row in release.canary_evidence(self.root, self.runtime)["rows"]))
        self.assertFalse(registry.repack(CLASSIFY, self.root)["changed"])               # idempotent: no new event
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["history"][-1], entry["history"][-1])

    def test_repack_refuses_a_changed_package(self):
        schema = self.root / "agents" / "packages" / CLASSIFY / "schemas" / "output.json"
        schema.write_text(schema.read_text().replace("{", '{"description": "changed", ', 1))
        before = sha(self.root / "agents" / "registry.lock.json")
        with self.assertRaisesRegex(registry.RegistryError, "itself changed since it was registered"):
            registry.repack(CLASSIFY, self.root)
        self.assertEqual(sha(self.root / "agents" / "registry.lock.json"), before)

    def test_staged_manifest_pins_each_evidence_file_separately(self):
        self.canaries(20)
        staged = release.stage(CLASSIFY, self.base / "stage", self.root, runtime_root=self.runtime, approved_by="x")
        acceptance = json.loads(Path(staged["manifest"]).read_text())["acceptance"]
        reliability = acceptance["reliability_evidence"]
        self.assertEqual(reliability["sha256"], sha(self.root / reliability["path"]))
        self.assertEqual(acceptance["evidence_sha256"], sha(self.root / acceptance["evidence"]))
        self.assertNotEqual(reliability["path"], acceptance["evidence"])


class Provenance(Base):
    """A historical canary counts only when git history independently supports the pinned package."""

    def one(self, provenance="HEAD", **job):
        fx.write_reliability(self.root, CLASSIFY)
        made = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,), **job)
        fx.add_acceptance(self.root, made, (CLASSIFY,), provenance=provenance)
        return self.evaluate()

    def assertUnverified(self, report, text):
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertEqual(report["canaries"]["excluded"], [])
        reasons = [r for item in report["canaries"]["unverified"] for r in item["reasons"]]
        self.assertTrue(reasons and all(r.startswith("package identity unverified") for r in reasons), reasons)
        self.assertTrue(any(text in r for r in reasons), (text, reasons))

    def test_supported_identity_counts(self):
        report = self.one()
        self.assertEqual(report["canaries"]["eligible"], 1)
        row = next(r for r in release.canary_evidence(self.root, self.runtime)["rows"])
        self.assertTrue(row["identity"]["verified"])

    def test_missing_provenance_is_unverified(self):
        self.assertUnverified(self.one(provenance=None), "names no full provenance commit")

    def test_unknown_commit_is_unverified(self):
        self.assertUnverified(self.one(provenance="0" * 40), "is not in this repository")

    def test_commit_after_the_run_is_unverified(self):
        later = fx.commit(self.root, "later", date="2026-11-18T00:00:00+00:00", paths=())
        self.assertUnverified(self.one(provenance=later), "later than the run")

    def test_newest_commit_before_the_run_holding_another_package_is_unverified(self):
        first = fx.head(self.root)
        schema = self.root / "agents" / "packages" / CLASSIFY / "schemas" / "output.json"
        original = schema.read_text()
        schema.write_text(original.replace("{", '{"description": "changed", ', 1))
        fx.commit(self.root, "changed package", date="2026-11-16T13:00:00+00:00")
        schema.write_text(original)               # disk is the pinned package again; history says otherwise
        self.assertUnverified(self.one(provenance=first), "newest commit before the run")

    def test_commit_whose_package_or_registry_differs_is_unverified(self):
        schema = self.root / "agents" / "packages" / CLASSIFY / "schemas" / "output.json"
        original = schema.read_text()
        schema.write_text(original.replace("{", '{"description": "changed", ', 1))
        changed = fx.commit(self.root, "changed package", date="2026-11-16T13:00:00+00:00")
        schema.write_text(original)
        report = self.one(provenance=changed)
        self.assertUnverified(report, f"the package at {changed[:12]} hashes to")

    def test_commit_where_the_package_was_not_released_is_unverified(self):
        path = self.root / "agents" / "registry.lock.json"
        original = path.read_text()
        data = json.loads(original)
        data["agents"][CLASSIFY]["state"] = "shadow"
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        shadow = fx.commit(self.root, "demote", date="2026-11-16T13:00:00+00:00")
        path.write_text(original)
        self.assertUnverified(self.one(provenance=shadow), "not released")

    def test_no_git_history_is_unverified(self):
        commit = fx.head(self.root)
        shutil.rmtree(self.root / ".git")
        self.assertUnverified(self.one(provenance=commit), "no git history is available")


class Safety(Base):

    def test_staging_does_not_alter_the_registry_or_routing(self):
        from aos_jobs import refs_release_problem

        self.canaries(20)
        before = sha(self.root / "agents" / "registry.lock.json")
        route = refs_release_problem(self.root, [f"{CLASSIFY}@0.1.0"])
        result = release.stage(CLASSIFY, self.base / "stage", self.root, runtime_root=self.runtime)
        self.assertTrue(result["staged"], result["refused"])
        self.assertEqual(sha(self.root / "agents" / "registry.lock.json"), before)
        self.assertEqual(refs_release_problem(self.root, [f"{CLASSIFY}@0.1.0"]), route)
        with self.assertRaises(release.ReleaseError):
            release.stage(CLASSIFY, self.root / "agents" / "x", self.root, runtime_root=self.runtime)

    def test_cross_client_and_example_dealership_context_block(self):
        job = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,), scopes=("global", "client:acme"))
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        job = fx.add_job(self.root, self.runtime, 2, (CLASSIFY,), scopes=("example_dealership",))
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        report = self.evaluate()
        self.assertEqual(report["decision"], release.BLOCKED)
        self.assertEqual(report["canaries"]["eligible"], 0)
        text = " ".join(report["blockers"])
        self.assertIn("cross-client context", text)
        self.assertIn("isolation violation", text)

    def test_capability_violation_fails_closed(self):
        effects = [{"action": "SEND_EMAIL", "effect": "email.send", "status": "held"}]
        job = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,), effects=effects)
        fx.add_acceptance(self.root, job, (CLASSIFY,), expected_held=("SEND_EMAIL",))
        job = fx.add_job(self.root, self.runtime, 2, (CLASSIFY,), scopes=("client:other",))
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        report = self.evaluate()
        self.assertEqual(report["decision"], release.BLOCKED)
        self.assertTrue(any("effect email.send is not declared" in b for b in report["blockers"]))
        self.assertTrue(any("read scope 'client:other' is not granted" in b for b in report["blockers"]))

    def test_missing_context_or_bad_source_reference_is_not_accepted(self):
        job = fx.add_job(self.root, self.runtime, 1, (CLASSIFY,), named_ref="business_brain:other/note.md")
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        job = fx.add_job(self.root, self.runtime, 2, (CLASSIFY,), content_sha256="not-a-hash")
        fx.add_acceptance(self.root, job, (CLASSIFY,))
        report = self.evaluate()
        reasons = [r for item in report["canaries"]["excluded"] for r in item["reasons"]]
        self.assertEqual(report["canaries"]["eligible"], 0)
        self.assertTrue(any("was not the record read" in r for r in reasons))
        self.assertTrue(any("no valid source reference or content hash" in r for r in reasons))

    def test_no_operation_starts_a_model_process_or_network_call(self):
        self.canaries(20)
        real_popen, launched = subprocess.Popen, []

        def only_git(args, *rest, **kwargs):     # read-only git history queries are the only process allowed
            argv = list(args) if isinstance(args, (list, tuple)) else [args]
            launched.append(os.path.basename(str(argv[0])))
            if launched[-1] != "git" or any(word in argv for word in ("push", "commit", "fetch", "pull", "reset", "checkout")):
                raise AssertionError(f"M7 must not launch this process: {argv[:3]}")
            return real_popen(args, *rest, **kwargs)

        def forbidden(*args, **kwargs):
            raise AssertionError(f"M7 must not open connections or call models: {args[:1]}")

        import aos_modelport

        with mock.patch.object(subprocess, "Popen", only_git), \
                mock.patch.object(socket.socket, "connect", forbidden), mock.patch.object(aos_modelport, "call", forbidden):
            release.check(None, self.root, runtime_root=self.runtime)
            staged = release.stage(CLASSIFY, self.base / "stage", self.root, runtime_root=self.runtime, approved_by="fixture")
            outcome = release.drill(staged["manifest"], self.base / "scratch", self.root, runtime_root=self.runtime,
                                    fail_after_activation=True)
        self.assertEqual(outcome["phases"][-1], "rolled_back")
        self.assertEqual(set(launched), {"git"})


class ReleaseAndRollback(Base):
    def stage(self, **kwargs):
        self.canaries(20)
        return release.stage(CLASSIFY, self.base / "stage", self.root, runtime_root=self.runtime, **kwargs)

    def test_readiness_report_is_deterministic(self):
        self.canaries(20)
        first = release.check(None, self.root, runtime_root=self.runtime)
        second = release.check(None, self.root, runtime_root=self.runtime)
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        self.assertEqual(first, second)
        self.assertIn(f"{CLASSIFY}@0.1.0", first["summary"]["ready"])
        self.assertIn("READY", release.render(first))

    def test_dry_run_shows_the_route_change_without_applying_it(self):
        result = self.stage(approved_by="fixture")
        self.assertTrue(result["staged"])
        change = result["proposed_changes"][0]
        self.assertEqual((change["from"], change["to"]), ("canary", "active"))
        manifest = json.loads(Path(result["manifest"]).read_text())
        self.assertFalse(manifest["applied"])
        self.assertEqual(manifest["rollback"]["target_state"], "canary")
        self.assertEqual(len(manifest["acceptance"]["canaries"]), 20)
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "canary")

    def test_incompatible_or_stale_candidates_are_refused(self):
        self.canaries(20)
        for agent, text in (("research.synthesise", "readiness is NOT_READY"), (f"{CLASSIFY}@0.2.0", "readiness is BLOCKED"),
                            ("shared.cite_verify", "readiness is BLOCKED")):
            result = release.stage(agent, self.base / "s", self.root, runtime_root=self.runtime)
            self.assertFalse(result["staged"], agent)
            self.assertTrue(any(text in r for r in result["refused"]), (agent, result["refused"]))
        stale = release.stage(CLASSIFY, self.base / "s", self.root, runtime_root=self.runtime, expect_state="shadow")
        self.assertTrue(any("stale active-state assumption" in r for r in stale["refused"]))
        backwards = release.stage(CLASSIFY, self.base / "s", self.root, runtime_root=self.runtime, target="shadow")
        self.assertTrue(any("not a release step up" in r for r in backwards["refused"]))
        # A registry that moved after staging makes the manifest stale for the drill.
        staged = release.stage(CLASSIFY, self.base / "ok", self.root, runtime_root=self.runtime, approved_by="fixture")
        registry.promote(CLASSIFY, "shadow", self.root)
        drill = release.drill(staged["manifest"], self.base / "scratch", self.root, runtime_root=self.runtime)
        self.assertTrue(any("changed since staging" in r for r in drill["refused"]))

    def test_drill_reports_the_real_verdict_and_refuses_an_altered_manifest(self):
        staged = self.stage()
        drill = release.drill(staged["manifest"], self.base / "scratch", self.root, runtime_root=self.runtime)
        self.assertFalse(drill["authorization"]["authorized"])          # a scratch apply proves nothing about authority
        path = Path(staged["manifest"])
        manifest = json.loads(path.read_text())
        manifest["authorization"]["authority"]["authorized"] = True     # forged authorization
        path.write_text(json.dumps(manifest))
        drill = release.drill(path, self.base / "scratch2", self.root, runtime_root=self.runtime)
        self.assertIn("the release manifest was altered after staging", drill["refused"])

    def test_interrupted_staging_is_recoverable(self):
        staged = self.stage(approved_by="fixture")
        for index, point in enumerate(("before_write", "after_write")):
            outcome = release.drill(staged["manifest"], self.base / f"scratch-{index}", self.root, runtime_root=self.runtime,
                                    interrupt=point)
            self.assertEqual(outcome["phases"][-1], "recovered", outcome["phases"])
            self.assertTrue(outcome["result"]["restored_to_previous"])
            self.assertEqual(outcome["result"]["final_state"], "canary")

    def test_failed_release_restores_previous_route_and_keeps_evidence(self):
        staged = self.stage(approved_by="fixture")
        live_before = sha(self.root / "agents" / "registry.lock.json")
        fingerprint = release.Runtime(self.runtime).fingerprint()
        outcome = release.drill(staged["manifest"], self.base / "scratch", self.root, runtime_root=self.runtime,
                                fail_after_activation=True)
        self.assertEqual(outcome["phases"], ["applying", "applied", "post-activation check failed (simulated)", "rolled_back"])
        result = outcome["result"]
        self.assertEqual(result["final_state"], "canary")
        self.assertTrue(result["restored_to_previous"])
        self.assertEqual(result["route_now"], result["route_before"])
        for key in ("live_registry_unchanged", "jobs_receipts_ledger_unchanged", "package_evidence_unchanged", "acceptances_unchanged"):
            self.assertTrue(result[key], key)
        self.assertEqual(sha(self.root / "agents" / "registry.lock.json"), live_before)
        self.assertEqual(release.Runtime(self.runtime).fingerprint(), fingerprint)
        self.assertTrue((self.base / "scratch" / "rollback_report.json").is_file())

    def test_successful_drill_activates_only_the_scratch_copy(self):
        staged = self.stage(approved_by="fixture")
        outcome = release.drill(staged["manifest"], self.base / "scratch", self.root, runtime_root=self.runtime)
        self.assertEqual(outcome["phases"][-1], "verified")
        self.assertEqual(outcome["result"]["final_state"], "active")
        self.assertEqual(fx.registry_entry(self.root, CLASSIFY)["state"], "canary")


class CommandLine(Base):
    def run_cli(self, *argv):
        import importlib.util
        import contextlib
        import io

        spec = importlib.util.spec_from_file_location("aos_agent_cli", ROOT / "tools" / "aos-agent.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--root", str(self.root), *argv])
        return code, out.getvalue()

    def test_release_commands(self):
        self.canaries(20)
        code, text = self.run_cli("release", "check", CLASSIFY, PLANNER, "--runtime-root", str(self.runtime), "--text",
                                  "--out", str(self.base / "report"))
        self.assertEqual(code, 0)
        self.assertIn("READY     knowledge.source_classify@0.1.0", text)
        self.assertIn("NOT_READY shared.planner@0.1.0", text)
        self.assertTrue((self.base / "report" / "readiness.json").is_file())
        code, text = self.run_cli("release", "stage", CLASSIFY, "--out", str(self.base / "stage"), "--runtime-root",
                                  str(self.runtime), "--approved-by", "fixture")
        self.assertEqual(code, 0, text)
        manifest = json.loads(text)["manifest"]
        code, text = self.run_cli("release", "drill", manifest, "--scratch", str(self.base / "scratch"), "--runtime-root",
                                  str(self.runtime), "--fail-after-activation")
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)["phases"][-1], "rolled_back")


@unittest.skipUnless((LIVE / "queue" / "jobs" / "AOS-2026-10002.json").is_file(), "production job store not present")
class LiveEvidenceReadOnly(unittest.TestCase):
    """Reads the real M5 records without writing anything: genuine evidence, not a fixture."""

    def test_historical_m5_run_without_execution_attestation_is_unverified(self):
        before = release.Runtime(LIVE).fingerprint()
        report = release.check(list(fx.CANARY_FOUR), ROOT, runtime_root=LIVE)
        for row in report["candidates"]:
            self.assertEqual(row["canaries"]["eligible"], 0, row["agent"])
            self.assertEqual(row["canaries"]["remaining_for_gold"], 20)
            self.assertFalse(row["gates"]["7"]["pass"])                 # no package has gate-7 evidence
            self.assertEqual(row["decision"], release.NOT_READY)
            self.assertEqual(row["level"], "silver")
        for row in report["evidence_rows"]:
            if row["job"] == "AOS-2026-10002":
                self.assertFalse(row["identity"]["verified"], row["identity"])
                self.assertTrue(any("attestation" in p for p in row["identity"]["problems"]))
                self.assertEqual(row["identity"]["commit"], "ab6b2e06af1f56c045dac829b504df72ef4a1cab")
        self.assertEqual(release.Runtime(LIVE).fingerprint(), before)
