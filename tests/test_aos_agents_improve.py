"""M6 Agent Factory self-improvement: offline, propose-only. Zero model calls.

Fixture proof, not live proof: the job records are synthetic (AOS-2099-*); the packages,
golden cases and recorded replies are the repository's own. See tests/fixtures/m6/.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tools", ROOT / "tests" / "fixtures" / "m6"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_fixture  # noqa: E402
from aos_agents import improve  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, tzinfo=dt.timezone.utc)
POST = "marketing.linkedin_post@0.1.0"
SYNTH = "research.synthesise@0.1.0"


def _row(case_id, *, passed=True, failure_class="", tokens=8000, calls=1, exact=True, tags=()):
    return {"id": case_id, "tags": list(tags), "status": "ok" if passed else "failed", "failure_class": failure_class,
            "passed": passed, "failures": {} if passed else {"2": ["x"]}, "model_calls": calls, "tokens": tokens,
            "exact": exact, "output_tokens": 300, "repair": False, "seconds": 1.0, "not_recorded": False}


def _evidence(train=(), holdout=(), gen_jobs=(), hold_jobs=(), median=8000):
    package = {"ref": POST, "kind": "oneshot", "package_sha256": "x", "binding": "gpt-6-luna.low.codex-cli",
               "evidence": {"path": "e.json", "level": "silver", "mode": "replay",
                            "gate6": {"pass": True, "median_tokens": median}}, "train": list(train), "holdout": list(holdout), "spec": None}
    return {"client_scope": "global", "jobs": {"generation": list(gen_jobs), "holdout": list(hold_jobs)},
            "jobs_excluded_out_of_scope": 0, "packages": {"marketing.linkedin_post": package}, "packages_skipped": []}


def _job(n, *, status="done", failure_class="", agent=POST, step_status="ok", tokens=8000, exact=True, request="x"):
    return {"id": f"AOS-2099-{n:04d}", "status": status, "failure_class": failure_class, "request": request,
            "created_at": f"2099-01-01T00:{n:02d}:00+00:00",
            "result": {"steps": [{"id": "post", "agent": agent, "status": step_status, "failure_class": failure_class}]},
            "usage": {"by_step": [{"agent": agent, "step": "post", "model": "gpt-6-luna", "tokens": tokens, "exact": exact}]},
            "effects": []}


class FixtureRun(unittest.TestCase):
    """One fixture root and one M6 run shared by the end-to-end assertions."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="m6-test-"))
        cls.root = cls.base / "root"
        cls.built = build_fixture.build(cls.root)
        cls.protected_before = improve._tree_digest(cls.root, improve.PROTECTED)
        cls.report = improve.run(cls.root, now=NOW, out_dir=cls.base / "out", scratch_parent=cls.base)
        cls.report_text = Path(cls.report["report_path"]).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def signal(self, kind, **key):
        found = [s for s in self.report["signals"] if s["type"] == kind and all(s["key"].get(k) == v for k, v in key.items())]
        self.assertEqual(len(found), 1, f"{kind} {key}: {found}")
        return found[0]

    # -- detection and ranking --------------------------------------------------
    def test_detects_recurring_failure_cost_regression_gap_and_hold(self):
        failure = self.signal("recurring_failure", agent=POST, failure_class="validation_failed")
        self.assertTrue(failure["actionable"])
        self.assertEqual(failure["evidence_ids"], ["AOS-2099-0001", "AOS-2099-0002", "AOS-2099-0003", "core-gym-own-data"])
        self.assertEqual(failure["holdout_confirmation"], "confirmed")
        self.assertEqual(failure["holdout_ids"], ["AOS-2099-0017"])
        gap = self.signal("unsupported_planner_request")
        self.assertTrue(gap["actionable"])
        self.assertIn("quickbooks", gap["key"]["terms"])
        hold = self.signal("review_hold", action="BRAIN_WRITE_PROPOSAL")
        self.assertTrue(hold["policy_required"])
        self.assertFalse(hold["actionable"])

    def test_ranks_the_measured_improvement_first(self):
        first = self.report["proposals"][0]
        self.assertEqual((first["rank"], first["kind"], first["agent"]), (1, "spec_patch", POST))
        self.assertEqual(first["evaluation"]["status"], "improved")
        statuses = [p["evaluation"]["status"] for p in self.report["proposals"]]
        self.assertEqual(statuses, sorted(statuses, key=lambda s: improve.STATUS_RANK[s]))

    # -- three-candidate cap ----------------------------------------------------
    def test_never_more_than_three_proposals(self):
        self.assertEqual(self.report["candidates_considered"], 4)
        self.assertEqual(len(self.report["proposals"]), 3)
        self.assertEqual([p["rank"] for p in self.report["proposals"]], [1, 2, 3])
        cut = [c for c in self.report["not_proposed"] if c.get("not_proposed")]
        self.assertEqual([c["kind"] for c in cut], ["new_spec_needed"])
        self.assertEqual(cut[0]["agent"], "unnamed")   # no invented department or agent name
        self.assertNotIn("workbench", json.dumps(cut[0]))

    def test_rank_caps_any_number_of_candidates(self):
        signals = {"s": {"id": "s", "type": "recurring_failure", "occurrences": 3}}
        many = [{"id": f"cand-{i}", "signal": "s", "signals": ["s"], "evaluation": {"status": "improved"}} for i in range(7)]
        proposed, rest = improve.rank(many, signals)
        self.assertEqual(len(proposed), improve.MAX_PROPOSALS)
        self.assertEqual(len(rest), 4)

    # -- held-out separation ----------------------------------------------------
    def test_held_out_jobs_and_cases_never_feed_generation(self):
        held_jobs = set(self.report["evidence"]["jobs_holdout"])
        self.assertEqual(held_jobs, {"AOS-2099-0017", "AOS-2099-0018", "AOS-2099-0019", "AOS-2099-0020"})
        held_cases = {cid for p in self.report["evidence"]["packages"].values() for cid in p["holdout_cases"]}
        self.assertTrue(held_cases)
        for signal in self.report["signals"]:
            self.assertFalse(held_jobs & set(signal["evidence_ids"]), signal)
            self.assertFalse(held_cases & set(signal["evidence_ids"]), signal)
        for candidate in self.report["proposals"] + self.report["not_proposed"]:
            self.assertFalse((held_jobs | held_cases) & set(candidate["evidence_ids"]), candidate["id"])

    def test_a_failure_only_in_held_out_cases_raises_no_signal(self):
        evidence = _evidence(train=[_row(f"t{i}") for i in range(6)],
                             holdout=[_row("h1", passed=False, failure_class="validation_failed"),
                                      _row("h2", passed=False, failure_class="validation_failed"),
                                      _row("h3", passed=False, failure_class="validation_failed")])
        self.assertFalse([s for s in improve.detect(evidence, self.root) if s["type"] == "recurring_failure"])

    def test_evaluation_reports_train_and_holdout_separately(self):
        measured = self.report["proposals"][0]["evaluation"]["measured"]
        self.assertEqual(measured["train"]["before"]["failed"], ["core-gym-own-data"])
        self.assertEqual(measured["train"]["after"]["failed"], [])
        self.assertEqual(measured["holdout"]["comparable_cases"], 5)
        self.assertEqual(measured["holdout"]["before"]["failed"], measured["holdout"]["after"]["failed"])

    # -- reproducible before/after ----------------------------------------------
    def test_before_after_is_measured_and_reproducible(self):
        ev = self.report["proposals"][0]["evaluation"]
        self.assertEqual(ev["fixed_cases"], ["core-gym-own-data"])
        self.assertEqual(ev["new_failures"], [])
        self.assertEqual(ev["new_safety_failures"], [])
        self.assertEqual(ev["tokens_before"], ev["tokens_after"])
        again = improve.run(self.root, now=NOW, out_dir=self.base / "again", scratch_parent=self.base)
        self.assertEqual(again["run_id"], self.report["run_id"])
        self.assertEqual(Path(again["report_path"]).read_text(encoding="utf-8"), self.report_text)

    # -- sparse, missing and ambiguous evidence ---------------------------------
    def test_unreplayable_change_is_insufficient_evidence_not_an_outcome(self):
        pin = next(p for p in self.report["proposals"] if p["kind"] == "pin_candidate")
        self.assertEqual(pin["evaluation"]["status"], "insufficient_evidence")
        self.assertTrue(pin["evaluation"]["not_comparable"])
        self.assertNotIn("fixed_cases", pin["evaluation"])
        # Partial counts are never presented as a before/after.
        self.assertNotIn("measured", pin["evaluation"])
        self.assertNotIn("levels", pin["evaluation"])
        self.assertTrue(pin["evaluation"]["comparison"].startswith("not shown"))
        markdown = (Path(self.report["report_path"]).parent / pin["files"]["proposal"]).read_text(encoding="utf-8")
        self.assertIn("INSUFFICIENT EVIDENCE", markdown)
        self.assertNotIn("| passed |", markdown)
        text = next(p for p in self.report["proposals"] if p["kind"] == "precise_change")
        self.assertEqual(text["evaluation"]["status"], "insufficient_evidence")
        self.assertNotIn("measured", text["evaluation"])

    def test_too_few_comparable_cases_claim_nothing(self):
        def side(n):
            return {"cases": n, "ran": n, "passed": n, "failed": [], "safety_failed": [], "not_recorded": []}
        small = {"train": {"before": {**side(3), "failed": ["a"]}, "after": side(3), "comparable_cases": 3,
                           "not_comparable": [], "comparable_tokens": {"before": 100, "after": 100}},
                 "holdout": {"before": side(1), "after": side(1), "comparable_cases": 1, "not_comparable": [],
                             "comparable_tokens": {"before": 10, "after": 10}}}
        verdict = improve._verdict(small, {})
        self.assertEqual(verdict["status"], "insufficient_evidence")
        self.assertNotIn("fixed_cases", verdict)
        self.assertIn("provisional minimum", verdict["reason"])

    def test_report_states_provisional_thresholds_and_unfinished_generation(self):
        self.assertTrue(self.report["thresholds"]["status"].startswith("provisional"))
        self.assertTrue(any("autonomous patch generation is not implemented" in row for row in self.report["limitations"]))
        self.assertFalse(self.report["provenance"]["authoritative"])
        self.assertTrue(self.report["provenance"]["contains_fixture_evidence"])

    def test_sparse_evidence_is_observed_but_not_actionable(self):
        jobs = [_job(1, status="failed", failure_class="validation_failed", step_status="failed"),
                _job(2, status="failed", failure_class="validation_failed", step_status="failed")]
        signals = improve.detect(_evidence(gen_jobs=jobs), self.root)
        failure = next(s for s in signals if s["type"] == "recurring_failure")
        self.assertFalse(failure["actionable"])
        self.assertEqual(failure["occurrences"], 2)
        cost = next(s for s in signals if s["type"] == "cost_regression")
        self.assertFalse(cost["actionable"])
        self.assertIn("5 needed", cost["insufficient"])
        self.assertEqual(improve.generate(signals, _evidence(gen_jobs=jobs), self.root), [])

    def test_missing_baseline_and_ambiguous_requests(self):
        evidence = _evidence(gen_jobs=[_job(i, tokens=20000) for i in range(1, 7)])
        evidence["packages"]["marketing.linkedin_post"]["evidence"] = None
        cost = next(s for s in improve.detect(evidence, self.root) if s["type"] == "cost_regression")
        self.assertFalse(cost["actionable"])
        self.assertIn("no recorded acceptance baseline", cost["insufficient"])
        unrelated = [_job(i, status="refused", failure_class="request_unsupported", step_status="refused", request=text)
                     for i, text in enumerate(("Book a dentist appointment", "Translate the brochure to French",
                                               "Order printer toner"), start=1)]
        gaps = [s for s in improve.detect(_evidence(gen_jobs=unrelated), self.root) if s["type"] == "unsupported_planner_request"]
        self.assertEqual(len(gaps), 3)
        self.assertFalse(any(s["actionable"] for s in gaps))

    def test_too_few_jobs_hold_nothing_out(self):
        root = self.base / "few"
        (root / "queue/jobs").mkdir(parents=True)
        shutil.copytree(ROOT / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__", "dist"))
        for n in range(1, 4):
            job = _job(n)
            job["created_at"] = f"2099-01-01T00:0{n}:00+00:00"
            (root / "queue/jobs" / f"{job['id']}.json").write_text(json.dumps(job), encoding="utf-8")
        evidence = improve.collect(root)
        self.assertEqual(len(evidence["jobs"]["generation"]), 3)
        self.assertEqual(evidence["jobs"]["holdout"], [])

    # -- exact accounting --------------------------------------------------------
    def test_cost_regression_uses_exact_samples_only(self):
        cost = self.signal("cost_regression", agent=SYNTH, model="gpt-6-luna")
        self.assertTrue(cost["actionable"])
        self.assertEqual((cost["exact_samples"], cost["non_exact_excluded"]), (5, 1))
        self.assertEqual(cost["runtime_median_tokens"], 12050.0)
        self.assertEqual(cost["baseline_median_tokens"], 8102)
        self.assertEqual(cost["ratio"], round(12050 / 8102, 3))
        self.assertEqual(cost["holdout_confirmation"], "confirmed")
        outlier = self.signal("cost_outlier", agent=SYNTH)
        self.assertEqual(outlier["outlier_tokens"], {"edge-irrelevant-source-ignored": 15943})
        self.assertFalse(outlier["gate6"]["pass"])

    def test_all_non_exact_samples_claim_nothing(self):
        jobs = [_job(i, tokens=30000, exact=False) for i in range(1, 8)]
        cost = next(s for s in improve.detect(_evidence(gen_jobs=jobs), self.root) if s["type"] == "cost_regression")
        self.assertEqual(cost["exact_samples"], 0)
        self.assertIsNone(cost["runtime_median_tokens"])
        self.assertFalse(cost["actionable"])

    # -- no active mutation, no paid model ---------------------------------------
    def test_no_active_state_changes_and_no_model_or_ledger(self):
        self.assertEqual(improve._tree_digest(self.root, improve.PROTECTED), self.protected_before)
        self.assertEqual(self.report["guarantees"]["model_calls"], 0)
        self.assertTrue(self.report["guarantees"]["active_state_unchanged"])
        self.assertFalse((self.root / "queue/token_ledger.jsonl").exists())
        self.assertFalse((self.root / improve.OUTPUT_DIR).exists())   # written only where asked
        for candidate in self.report["proposals"] + self.report["not_proposed"]:
            self.assertNotIn('"live": true', json.dumps(candidate.get("change")).lower())

    def test_model_calls_fail_closed_inside_m6(self):
        import aos_modelport

        with improve._no_model_calls():
            with self.assertRaises(improve.ImproveError):
                aos_modelport.call("prompt", model="gpt-6-luna", effort="low", output_schema={})

    def test_refuses_to_write_into_active_state(self):
        for target in ("agents/factory/specs", "agents/packages", "queue/jobs", "agents"):
            with self.assertRaises(improve.ImproveError):
                improve.run(self.root, now=NOW, out_dir=self.root / target, write=False)

    # -- policy and isolation negatives ------------------------------------------
    def test_patch_outside_the_agent_spec_is_rejected(self):
        pool = self.base / "bad-pool" / "marketing.linkedin_post"
        pool.mkdir(parents=True)
        bad = {"pkg": "agents/packages/marketing.linkedin_post/handler.py",
               "policy": "rules/runtime_permission_policy.md",
               "other": "agents/factory/specs/shared.planner/handler.py"}
        for name, target in bad.items():
            (pool / f"{name}.diff").write_text(f"--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n-x\n+y\n", encoding="utf-8")
            (pool / f"{name}.json").write_text(json.dumps({"addresses": ["validation_failed"]}), encoding="utf-8")
        report = improve.run(self.root, now=NOW, pool_dir=pool.parent, write=False, scratch_parent=self.base)
        patches = [c for c in report["proposals"] + report["not_proposed"] if c["kind"] == "spec_patch"]
        self.assertEqual(len(patches), 3)
        for candidate in patches:
            self.assertEqual(candidate["evaluation"]["status"], "rejected", candidate)
            self.assertIn("outside agents/factory/specs/marketing.linkedin_post/", candidate["evaluation"]["reason"])
        self.assertFalse([p for p in report["proposals"] if p["kind"] == "spec_patch"])
        self.assertEqual(improve._tree_digest(self.root, improve.PROTECTED), self.protected_before)

    def test_policy_holds_are_never_proposed_away(self):
        hold = self.signal("review_hold", action="BRAIN_WRITE_PROPOSAL")
        for candidate in self.report["proposals"] + self.report["not_proposed"]:
            self.assertNotIn(hold["id"], candidate.get("signals") or [candidate["signal"]])

    def test_example_dealership_evidence_is_excluded_and_never_cited(self):
        self.assertEqual(self.report["evidence"]["jobs_excluded_out_of_scope"], 4)
        for n in range(21, 25):
            self.assertNotIn(f"AOS-2099-{n:04d}", self.report_text)
        self.assertNotIn("Example Dealership coaching post", self.report_text)
        scoped = improve.run(self.root, now=NOW, client_scope=build_fixture.NORTH_SHORE, write=False, scratch_parent=self.base)
        self.assertEqual(scoped["evidence"]["jobs_excluded_out_of_scope"], 20)
        # Packages that read the global Brain are out of a Example Dealership run (packages reading no scope stay);
        # nothing global is cited or proposed.
        for name in ("marketing.linkedin_post", "research.synthesise", "knowledge.source_classify"):
            self.assertNotIn(name, scoped["evidence"]["packages"])
        self.assertEqual(scoped["proposals"], [])
        self.assertFalse({f"AOS-2099-{n:04d}" for n in range(1, 21)} & set(json.dumps(scoped).split('"')))

    def test_refuses_a_example_dealership_workspace_root(self):
        with self.assertRaises(improve.ImproveError):
            improve.run(self.base / "workspaces" / "example_dealership_sales_coach", write=False)



class MorningBriefConsumption(unittest.TestCase):
    """The existing brief shows a fresh, valid M6 report and survives every bad one."""

    @classmethod
    def setUpClass(cls):
        import aos_morning_brief

        cls.brief = aos_morning_brief
        cls.base = Path(tempfile.mkdtemp(prefix="m6-brief-"))
        cls.root = cls.base / "root"
        build_fixture.build(cls.root)
        cls.brain = cls.base / "brain"
        (cls.brain / "prospects").mkdir(parents=True)
        cls.empty = cls.run_brief(cls)   # before any report exists
        report = improve.run(cls.root, now=NOW, scratch_parent=cls.base)   # default: <root>/results/factory_improvements
        cls.folder = Path(report["report_path"]).parent
        cls.run_id = report["run_id"]
        cls.saved = {path: path.read_bytes() for path in cls.folder.iterdir()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def tearDown(self):
        for path in self.folder.iterdir():
            if path not in self.saved:
                path.unlink()
        for path, data in self.saved.items():
            if path.is_symlink():
                path.unlink()
            path.chmod(0o644) if path.exists() else None
            path.write_bytes(data)
        (self.root / build_fixture.FIXTURE_MARKER).write_text("fixture\n", encoding="utf-8")

    def run_brief(self, now=NOW):
        return self.brief.run(root=self.root, brain_root=self.brain, now=now, dry_run=True)

    def proposals(self, result):
        return [r for r in result["findings"]["findings"] if r["rule"] == "agent_improvement_proposal"]

    def edit_report(self, **change):
        path = self.folder / "report.json"
        path.write_text(json.dumps({**json.loads(self.saved[path]), **change}), encoding="utf-8")

    def assert_rejected(self, result, reason):
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["model_invocations"], 0)
        self.assertFalse(self.proposals(result))
        self.assertNotIn("Agent Factory proposals", result["artifact"])
        rows = result["findings"]["generated_inputs"]
        self.assertEqual(rows[0]["status"], "rejected")
        self.assertIn(reason, json.dumps(rows[0]["rejected"]))
        self.assertFalse(result["findings"]["generated_artifact_used_as_input"])

    def test_fresh_report_is_shown_as_generated_not_authoritative(self):
        result = self.run_brief()
        rows = self.proposals(result)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["entity_path"], f"results/factory_improvements/{self.run_id}/proposal-1.md")
        self.assertTrue((self.root / rows[0]["entity_path"]).is_file())
        for row in rows:
            self.assertEqual(row["owner_class"], "casey_review")
            self.assertEqual(row["discovery"]["route"], "generated_m6_proposal")
            self.assertIs(row["discovery"]["authoritative"], False)
            self.assertIs(row["current_state"]["authoritative"], False)
            self.assertEqual(row["current_state"]["report_freshness"], "fresh")
        inputs = result["findings"]["generated_inputs"]
        self.assertEqual([(r["status"], r["authoritative"], r["run_id"]) for r in inputs], [("fresh", False, self.run_id)])
        self.assertTrue(result["findings"]["generated_artifact_used_as_input"])
        # The canonical source digest does not move because a generated report appeared.
        self.assertEqual(result["findings"]["source_state_sha256"], self.empty["findings"]["source_state_sha256"])
        self.assertIn("## Agent Factory proposals (propose-only)", result["artifact"])
        self.assertIn("not authoritative", result["artifact"])
        self.assertIn("spec_patch for marketing.linkedin_post@0.1.0 — improved (fresh)", result["artifact"])

    def test_missing_report_changes_nothing(self):
        self.assertFalse(self.proposals(self.empty))
        self.assertEqual(self.empty["findings"]["generated_inputs"], [])
        self.assertFalse(self.empty["findings"]["generated_artifact_used_as_input"])

    def test_malformed_report_is_ignored(self):
        (self.folder / "report.json").write_text("{not json", encoding="utf-8")
        self.assert_rejected(self.run_brief(), "unreadable")
        for change, reason in (({"propose_only": False}, "propose-only"), ({"proposals": "x"}, "malformed"),
                               ({"proposals": [{"rank": 1}] * 4}, "over the limit"), ({"run_id": "m6-x"}, "folder"),
                               ({"provenance": None}, "provenance"), ({"guarantees": {"model_calls": 1}}, "zero model calls")):
            self.edit_report(**change)
            self.assert_rejected(self.run_brief(), reason)

    def test_unreadable_report_is_ignored(self):
        import os

        if os.geteuid() == 0:
            self.skipTest("root can read a 000 file")
        (self.folder / "report.json").chmod(0)
        self.assert_rejected(self.run_brief(), "unreadable")

    def test_out_of_scope_report_is_ignored(self):
        self.edit_report(client_scope=build_fixture.NORTH_SHORE)
        self.assert_rejected(self.run_brief(), "is not 'global'")

    def test_report_from_another_root_is_ignored(self):
        provenance = json.loads(self.saved[self.folder / "report.json"])["provenance"]
        self.edit_report(provenance={**provenance, "source_root": str(self.base / "elsewhere")})
        self.assert_rejected(self.run_brief(), "another root")

    def test_fixture_report_needs_a_marked_fixture_root(self):
        (self.root / build_fixture.FIXTURE_MARKER).unlink()
        self.assert_rejected(self.run_brief(), "fixture evidence")

    def test_unsafe_proposal_paths_are_ignored(self):
        report = json.loads(self.saved[self.folder / "report.json"])
        report["proposals"][0]["files"] = {"proposal": "../../../etc/passwd"}
        (self.folder / "report.json").write_text(json.dumps(report), encoding="utf-8")
        self.assert_rejected(self.run_brief(), "unsafe")
        (self.folder / "report.json").write_bytes(self.saved[self.folder / "report.json"])
        target = self.folder / "proposal-1.md"
        target.unlink()
        target.symlink_to(self.base / "brain")
        self.assert_rejected(self.run_brief(), "unsafe")

    def test_old_report_is_marked_stale(self):
        result = self.run_brief(now=NOW + dt.timedelta(days=8))
        rows = self.proposals(result)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["title"].startswith("STALE ") for r in rows))
        self.assertEqual(rows[0]["current_state"]["report_freshness"], "stale")
        self.assertFalse(rows[0]["current_state"]["decision_waiting_on_casey"])
        self.assertIn("STALE, re-run M6 before acting", result["artifact"])
        self.assertEqual(result["findings"]["generated_inputs"][0]["status"], "stale")

    def test_report_made_before_a_package_change_is_stale(self):
        handler = self.root / "agents/packages/marketing.post_check/handler.py"
        original = handler.read_bytes()
        try:
            handler.write_bytes(original + b"\n# changed after the report\n")
            row = self.proposals(self.run_brief())[0]
            self.assertEqual(row["current_state"]["report_freshness"], "stale")
            self.assertIn("changed since the report", " ".join(row["current_state"]["stale_reasons"]))
        finally:
            handler.write_bytes(original)

    def test_reader_failure_never_breaks_the_brief(self):
        from unittest import mock

        import tools.aos_agents.improve as reader_module

        with mock.patch.object(reader_module, "read_latest_report", side_effect=RuntimeError("boom")):
            result = self.run_brief()
        self.assert_rejected(result, "reader unavailable")


if __name__ == "__main__":
    unittest.main()
