"""Bounded, offline CRM/Home acceptance. All records and writes use temporary fixtures."""
import datetime as dt
from zoneinfo import ZoneInfo
from unittest.mock import patch

from tests.test_command_centre_crm import CrmCase, ALPHA
from tests.command_centre_fixture import NOW
import aos_crm


class FollowupTests(CrmCase):
    def test_http_lifecycle_reconciles_home_and_persists_without_models_or_sending(self):
        import main as backend
        from fastapi.testclient import TestClient

        with patch.object(backend, "BASE_DIR", self.root), patch.object(aos_crm, "_now", return_value=NOW), \
                patch.object(backend, "start_david", side_effect=AssertionError("no model runtime")), \
                patch("aos_jobs.create", side_effect=AssertionError("no production jobs")):
            client = TestClient(backend.app)  # deliberately no lifespan/startup
            counter = 0

            def post(kind, rid, payload, **extra):
                nonlocal counter
                counter += 1
                return client.post("/api/crm/events", json={
                    "kind": kind, "record_id": rid, "payload": payload, "basis": "confirmed",
                    "source_refs": ["casey:dashboard-entry"], "request_key": f"lifecycle-{counter}", **extra})

            self.assertEqual(post("organization.create", "org:lifecycle", {"name": "Synthetic Lifecycle Co"}).status_code, 200)
            self.assertEqual(post("contact.create", "contact:lifecycle", {"name": "Synthetic Alex", "organization": "org:lifecycle"}).status_code, 200)
            result = post("opportunity.create", "opp:lifecycle", {"name": "Synthetic lifecycle opportunity",
                          "contact": "contact:lifecycle", "organization": "org:lifecycle"})
            self.assertEqual(result.status_code, 200, result.text)
            original = result.json()["record"]
            result = post("interaction.log", "opp:lifecycle", {"channel": "meeting", "direction": "internal",
                          "occurred_on": "2026-11-15", "summary": "Synthetic meeting, no external communication"},
                          expected_revision=original["revision"])
            self.assertEqual(result.status_code, 200, result.text)
            record = result.json()["record"]
            missing = client.get("/api/crm/home").json()
            self.assertIn("no_next_action", [r["rule"] for r in missing["attention"] if r["record_id"] == "opp:lifecycle"])
            result = post("next_action.set", "opp:lifecycle", {"text": "Review synthetic recap", "due": "2026-11-15", "owner": "casey"}, expected_revision=record["revision"])
            self.assertEqual(result.status_code, 200, result.text)
            record = result.json()["record"]
            home = client.get("/api/crm/home").json()
            item = next(r for r in home["attention"] if r["record_id"] == "opp:lifecycle")
            self.assertEqual((item["rule"], item["owner"], item["due"]), ("overdue_follow_up", "casey", "2026-11-15"))
            self.assertEqual(home["timezone"], "America/Vancouver")
            opened = client.get(f"/api/crm/records/{item['record_id']}").json()
            self.assertEqual(opened["contact_record"]["id"], "contact:lifecycle")
            self.assertEqual(opened["organization_record"]["id"], "org:lifecycle")
            self.assertEqual(item["sources"], ["casey:dashboard-entry"])
            result = post("next_action.set", "opp:lifecycle", {"text": "Review next week", "due": "2026-11-18", "owner": "casey"}, expected_revision=record["revision"])
            self.assertEqual(result.status_code, 200, result.text)
            updated = result.json()["record"]
            home = client.get("/api/crm/home").json()
            item = next(r for r in home["attention"] if r["record_id"] == "opp:lifecycle")
            self.assertEqual((item["rule"], item["due"]), ("upcoming_follow_up", "2026-11-18"))
            stale = post("follow_up.complete", "opp:lifecycle", {}, expected_revision=record["revision"])
            self.assertEqual(stale.status_code, 409)
            self.assertIn("Reload", stale.json()["detail"])
            self.assertEqual(post("follow_up.complete", "opp:lifecycle", {"note": "Reviewed locally"}, expected_revision=updated["revision"]).status_code, 200)
            home = client.get("/api/crm/home").json()
            self.assertFalse([r for r in home["attention"] if r["record_id"] == "opp:lifecycle" and "follow_up" in r["rule"]])
            reloaded = aos_crm.record_detail(self.root, "opp:lifecycle", now=NOW)
            self.assertTrue(reloaded["next_action"]["completed_at"])
            self.assertEqual((reloaded["id"], reloaded["stage"], reloaded["sources"]), (original["id"], original["stage"], original["sources"]))
            self.assertEqual(len(reloaded["events"]), 5)
            self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())

    def test_refused_inputs_leave_persistence_unchanged(self):
        cases = [
            ("opportunity.create", "opp:missing-contact", {"name": "Synthetic", "contact": "contact:missing"}, "unknown contact"),
            ("next_action.set", ALPHA, {"text": "Review", "due": "2026-11-18", "owner": "someone-else"}, "owner"),
            *[("next_action.set", ALPHA, {"text": "Review", "due": date}, "ISO date") for date in
              ("2026-02-30", "2026-11-18junk", "2026-11-18T00:00:00Z", "11/18/2026", "")],
            ("interaction.log", "opp:missing", {"summary": "Synthetic"}, "unknown CRM record"),
            ("email.send", ALPHA, {}, "unknown CRM event kind"),
            ("follow_up.complete", ALPHA, {}, "open confirmed"),
        ]
        for i, (kind, rid, payload, message) in enumerate(cases):
            with self.subTest(kind=kind, payload=payload), self.assertRaisesRegex(aos_crm.CrmError, message):
                self.event(kind, rid, payload, key=f"negative-{i}")
        self.assertEqual(self.events_text(), "")
        self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())

    def test_suggestions_do_not_replace_commitments_and_completion_does_not_revive_cadence(self):
        self.event("next_action.set", ALPHA, {"text": "Review", "due": "2026-11-15"})
        self.event("next_action.set", ALPHA, {"text": "Suggested", "due": "2026-12-30"}, basis="suggestion", key="suggestion-1")
        opp = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]
        self.assertEqual(opp["next_action"]["due"], "2026-11-15")
        self.assertEqual(opp["suggested_next_action"]["due"], "2026-12-30")
        # A legacy cadence date must not reappear after explicit completion.
        ledger = aos_crm.ledger_latest(self.root, "global")
        ledger[opp["prospect_id"]]["next_touch_due"] = "2026-11-01"
        self.event("follow_up.complete", ALPHA)
        state = aos_crm.project(self.root, now=NOW, ledger=ledger)
        self.assertFalse([r for r in state["attention"] if r["record_id"] == ALPHA and "follow_up" in r["rule"]])

    def test_business_day_and_seven_day_window(self):
        self.event("next_action.set", ALPHA, {"text": "Review", "due": "2026-11-16"})
        midnight = dt.datetime(2026, 11, 17, tzinfo=ZoneInfo(aos_crm.CRM_TIMEZONE)).astimezone(dt.timezone.utc)
        for timestamp, rule in [(midnight - dt.timedelta(minutes=1), "follow_up_due_today"),
                                (midnight, "overdue_follow_up")]:
            state = aos_crm.project(self.root, now=timestamp)
            self.assertIn(rule, [r["rule"] for r in state["attention"] if r["record_id"] == ALPHA])
        self.event("next_action.set", ALPHA, {"text": "Review", "due": "2026-11-24"}, key="outside-window")
        self.assertNotIn("upcoming_follow_up", [r["rule"] for r in aos_crm.project(self.root, now=NOW)["attention"]])

    def test_stale_revision_idempotent_retry_and_missing_sources(self):
        record = aos_crm.record_detail(self.root, ALPHA, now=NOW)
        args = dict(client_scope="global", kind="next_action.set", record_id=ALPHA,
                    payload={"text": "Review", "due": "2026-11-17"}, request_key="revision-retry",
                    expected_revision=record["revision"], now=NOW)
        event, created = aos_crm.record_event(self.root, **args)
        retry, duplicate = aos_crm.record_event(self.root, **args)
        self.assertEqual((created, duplicate, event["event_id"]), (True, False, retry["event_id"]))
        with self.assertRaises(aos_crm.CrmConflict):
            aos_crm.record_event(self.root, **{**args, "request_key": "stale-revision"})
        with self.assertRaisesRegex(aos_crm.CrmError, "cites its source"):
            self.event("interaction.log", ALPHA, {"channel": "call", "direction": "internal", "summary": "Synthetic", "occurred_on": "2026-11-16"}, refs=())
        with self.assertRaisesRegex(aos_crm.CrmError, "receipt not found"):
            self.event("link.add", ALPHA, refs=("queue/receipts/missing.md",))
        with self.assertRaisesRegex(aos_crm.CrmError, "unknown CRM record"):
            self.event("next_action.set", ALPHA, {"text": "Wrong scope", "due": "2026-11-17"}, scope="client-a", key="wrong-scope")

    def test_no_connected_data_is_known_zero(self):
        (self.root / "queue/prospects.jsonl").unlink()  # disposable fixture only
        summary = aos_crm.home_summary(self.root, now=NOW)
        self.assertEqual(summary["counts"]["opportunities"], 0)
        self.assertEqual(summary["attention"], [])
        self.assertEqual(summary["sources"]["crm_events"]["records"], 0)

    def test_home_exposes_linked_job_exceptions_and_new_evidence(self):
        import aos_jobs
        jobs = []
        for status in ("waiting_input", "failed"):
            job, _ = aos_jobs.create(self.root, conversation_id="synthetic-crm", topic="fixture", request="Synthetic draft only",
                                     executor={"kind": "agent", "agent": "crm.followup_draft@0.1.0", "input": {}})
            job["status"] = status
            aos_jobs.save(self.root, job)
            jobs.append(job["id"])
            self.event("link.add", ALPHA, refs=(job["id"],), key=f"link-{status}")
        pointer = "business_brain:prospects/2026-11-fixture-alpha-ada-example.md"
        self.event("link.add", ALPHA, refs=(pointer,), key="link-brain-evidence")
        summary = aos_crm.home_summary(self.root, now=NOW, limit=20)
        items = [r for r in summary["attention"] if r["rule"] == "linked_job_needs_attention"]
        self.assertEqual({r["job_id"] for r in items}, set(jobs))
        self.assertEqual(len({r["id"] for r in items}), 2)
        self.assertTrue(all(pointer in r["sources"] for r in items))
        detail = aos_crm.record_detail(self.root, ALPHA, now=NOW)
        self.assertTrue(set(jobs).issubset(detail["sources"]))
