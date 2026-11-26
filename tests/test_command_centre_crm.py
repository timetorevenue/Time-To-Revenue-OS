"""CRM & Sales (tools/aos_crm.py and its Dashboard API), offline against isolated fixtures.

Covers: projection from the prospect ledger with source links and basis labels, persistence
and reload, idempotency, confirmed-vs-draft rules, ledger mirroring through the ledger's own
validator (and refusal when it fails), scope isolation, job linkage and the HTTP routes.

Revisit: when aos_crm's event kinds or the prospect ledger rules change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.command_centre_fixture import NOW, PROSPECTS, ROOT, crm_environment

import aos_crm  # noqa: E402
import aos_jobs  # noqa: E402

ALPHA, BETA, GAMMA = (f"opp:{row[0]}" for row in PROSPECTS)


class CrmCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = crm_environment(self.root)
        self.paths = env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        self.ledger_before = (self.root / "queue/prospects.jsonl").read_text()

    def event(self, kind, record_id, payload=None, *, basis="confirmed", refs=("casey:dashboard-entry",), key=None,
              scope="global"):
        return aos_crm.record_event(self.root, client_scope=scope, kind=kind, record_id=record_id, payload=payload or {},
                                    basis=basis, source_refs=list(refs), request_key=key or f"{kind}-{record_id}-k1",
                                    now=NOW)

    def events_text(self):
        path = self.root / "queue/crm/events.jsonl"
        return path.read_text() if path.exists() else ""


class ProjectionTests(CrmCase):
    def test_ledger_people_companies_and_pipeline_with_sources_and_basis(self):
        state = aos_crm.project(self.root, now=NOW)
        self.assertEqual(state["counts"]["opportunities"], 3)
        self.assertEqual(state["pipeline"], {"drafted": 3})
        opp = state["opportunities"][ALPHA]
        self.assertEqual(opp["stage_basis"], "confirmed")
        self.assertEqual(opp["fit"]["basis"], "estimate")  # a prospecting score is never an outcome
        self.assertIsNone(opp["value"])                    # no value is ever invented
        self.assertIn(f"queue/prospects.jsonl#{PROSPECTS[0][0]}", opp["sources"])
        self.assertIn(f"business_brain:prospects/{PROSPECTS[0][0]}.md", opp["sources"])
        contact = state["contacts"][opp["contact"]]
        self.assertEqual((contact["name"], state["organizations"][contact["organization"]]["name"]),
                         ("Ada Example", "Fixture Alpha Ltd"))
        rules = {(row["rule"], row["record_id"]) for row in state["attention"]}
        self.assertIn(("draft_awaiting_casey", ALPHA), rules)
        self.assertIn("nothing has been sent", state["attention"][0]["reason"])

    def test_out_of_scope_brain_page_is_not_linked(self):
        data = json.loads(self.paths["registry_path"].read_text())
        data["scopes"]["global"]["brain_pointer_prefixes"] = []
        self.paths["registry_path"].write_text(json.dumps(data))
        opp = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]
        self.assertEqual(opp["brain_page"], "")
        self.assertFalse(any(ref.startswith("business_brain:") for ref in opp["sources"]))


class WriteTests(CrmCase):
    def test_interaction_next_action_and_overdue_follow_up_persist_and_reload(self):
        logged, created = self.event("interaction.log", ALPHA, {"channel": "call", "direction": "outbound",
                                                                "summary": "Fixture call; agreed to talk next week.",
                                                                "occurred_on": "2026-11-01"})
        self.assertTrue(created)
        self.event("next_action.set", ALPHA, {"text": "Send the fixture recap", "due": "2026-11-10"})
        state = aos_crm.project(self.root, now=NOW)  # a fresh read of the files, as after a restart
        opp = state["opportunities"][ALPHA]
        self.assertEqual(opp["interactions"][0]["event_id"], logged["event_id"])
        self.assertEqual(opp["next_action"]["text"], "Send the fixture recap")
        self.assertEqual(opp["engagement"], "cooling")
        overdue = [row for row in state["attention"] if row["rule"] == "overdue_follow_up"]
        self.assertEqual([(row["record_id"], row["due"]) for row in overdue], [(ALPHA, "2026-11-10")])
        self.assertIn("6 days overdue", overdue[0]["reason"])
        self.event("follow_up.complete", ALPHA, {"note": "recap sent by Casey from his own mail"}, key="done-key-1")
        state = aos_crm.project(self.root, now=NOW)
        self.assertFalse([row for row in state["attention"] if row["rule"] == "overdue_follow_up"])
        self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())

    def test_a_retry_with_the_same_key_records_once_and_a_reused_key_is_refused(self):
        payload = {"channel": "email", "direction": "inbound", "summary": "Fixture reply", "occurred_on": "2026-11-15"}
        first, created = self.event("interaction.log", GAMMA, payload, key="retry-key-1")
        again, created_again = self.event("interaction.log", GAMMA, payload, key="retry-key-1")
        self.assertEqual((created, created_again, first["event_id"]), (True, False, again["event_id"]))
        self.assertEqual(len(self.events_text().splitlines()), 1)
        with self.assertRaisesRegex(aos_crm.CrmError, "different change"):
            self.event("interaction.log", GAMMA, {**payload, "summary": "Different"}, key="retry-key-1")
        self.assertEqual(len(self.events_text().splitlines()), 1)

    def test_confirmed_facts_need_sources_and_drafts_stay_drafts(self):
        with self.assertRaisesRegex(aos_crm.CrmError, "cites its source"):
            self.event("interaction.log", ALPHA, {"channel": "email", "direction": "outbound", "summary": "x",
                                                  "occurred_on": "2026-11-15"}, refs=())
        draft, _ = self.event("interaction.log", ALPHA, {"channel": "email", "direction": "outbound",
                                                         "summary": "Draft follow-up, not sent", "occurred_on": "2026-11-16"},
                              basis="draft", refs=())
        opp = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]
        self.assertEqual(opp["engagement"], "no recorded interaction")  # a draft is not contact
        with self.assertRaisesRegex(aos_crm.CrmError, "needs the confirmed interaction"):
            self.event("stage.change", ALPHA, {"stage": "sent", "interaction": draft["event_id"]})
        with self.assertRaisesRegex(aos_crm.CrmError, "estimate"):
            self.event("value.set", ALPHA, {"amount": 5000, "currency": "CAD"}, basis="suggestion")
        with self.assertRaisesRegex(aos_crm.CrmError, "cites"):
            self.event("value.set", ALPHA, {"amount": 5000, "currency": "CAD"}, basis="estimate", refs=())
        self.event("value.set", ALPHA, {"amount": 5000, "currency": "cad", "note": "Casey's rough estimate"}, basis="estimate")
        value = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]["value"]
        self.assertEqual((value["amount"], value["currency"], value["basis"]), (5000.0, "CAD", "estimate"))

    def test_stage_change_is_mirrored_into_the_ledger_only_when_its_validator_passes(self):
        sent, _ = self.event("interaction.log", ALPHA, {"channel": "email", "direction": "outbound",
                                                        "summary": "Casey sent touch 1 himself", "occurred_on": "2026-11-16"},
                             refs=("gmail:fixture-message-1",))
        event, _ = self.event("stage.change", ALPHA, {"stage": "sent", "interaction": sent["event_id"],
                                                      "reason": "Casey sent it"})
        self.assertEqual(event["ledger_snapshot"]["status"], "sent")
        rows = [json.loads(line) for line in (self.root / "queue/prospects.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[-1]["status"], rows[-1]["touch_count"], rows[-1]["next_touch_due"]), ("sent", 1, "2026-11-20"))
        validator = aos_crm._ledger_validator()
        self.assertEqual(validator.validate_ledger(self.root / "queue/prospects.jsonl",
                                                   self.root / "queue/prospects_schema.json")["status"], "PASS")
        state = aos_crm.project(self.root, now=NOW)
        self.assertEqual((state["opportunities"][ALPHA]["stage"], state["opportunities"][ALPHA]["cadence_due"]),
                         ("sent", "2026-11-20"))
        # Beta is a tier-A signal older than 90 days: the ledger validator refuses, and nothing is written.
        ledger, events = (self.root / "queue/prospects.jsonl").read_text(), self.events_text()
        with self.assertRaisesRegex(aos_crm.CrmError, "validator refused.*older than 90 days"):
            self.event("stage.change", BETA, {"stage": "rejected", "reason": "stale"})
        self.assertEqual(((self.root / "queue/prospects.jsonl").read_text(), self.events_text()), (ledger, events))
        with self.assertRaisesRegex(aos_crm.CrmError, "cannot move to won"):
            self.event("stage.change", GAMMA, {"stage": "won"})

    def test_a_later_ledger_snapshot_from_another_workflow_wins_over_an_older_crm_stage(self):
        sent, _ = self.event("interaction.log", ALPHA, {"channel": "email", "direction": "outbound",
                                                        "summary": "touch 1", "occurred_on": "2026-11-16"})
        self.event("stage.change", ALPHA, {"stage": "sent", "interaction": sent["event_id"]})
        rows = [json.loads(line) for line in (self.root / "queue/prospects.jsonl").read_text().splitlines()]
        later = {**rows[-1], "status": "touch_2", "touch_count": 2, "status_date": "2026-11-20", "next_touch_due": "2026-11-25"}
        with (self.root / "queue/prospects.jsonl").open("a") as handle:
            handle.write(json.dumps(later) + "\n")
        opp = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]
        self.assertEqual((opp["stage"], opp["cadence_due"]), ("touch_2", "2026-11-25"))
        self.assertEqual([(row["stage"], bool(row.get("event_id"))) for row in opp["history"]],
                         [("drafted", False), ("sent", True), ("touch_2", False)])

    def test_outreach_pilot_rows_keep_their_own_flow(self):
        with (self.root / "queue/prospects.jsonl").open("a") as handle:
            handle.write(json.dumps({"record_type": "outreach_pilot_v1", "prospect_id": "EXCO-A-fixture-pilot",
                                     "person_name": "Pia Example", "company_name": "Fixture Pilot", "status": "drafted",
                                     "status_date": "2026-11-01"}) + "\n")
        with self.assertRaisesRegex(aos_crm.CrmError, "pilot's own event flow"):
            self.event("stage.change", "opp:EXCO-A-fixture-pilot", {"stage": "rejected"})

    def test_records_casey_adds_and_relationships(self):
        self.event("organization.create", "org:fixture-delta", {"name": "Fixture Delta", "domain": "Delta.example"})
        self.event("contact.create", "contact:dee-example", {"name": "Dee Example", "role": "COO",
                                                              "organization": "org:fixture-delta"})
        self.event("opportunity.create", "opp:fixture-delta-pilot", {"name": "Fixture Delta pilot",
                                                                      "contact": "contact:dee-example",
                                                                      "organization": "org:fixture-delta"})
        with self.assertRaisesRegex(aos_crm.CrmError, "starts before contact"):
            self.event("opportunity.create", "opp:fixture-x", {"name": "x", "organization": "org:fixture-delta",
                                                               "stage": "won"})
        with self.assertRaisesRegex(aos_crm.CrmError, "already exists"):
            self.event("organization.create", "org:fixture-delta", {"name": "again"}, key="dup-key-1")
        state = aos_crm.project(self.root, now=NOW)
        self.assertEqual(state["organizations"]["org:fixture-delta"]["contacts"], ["contact:dee-example"])
        self.assertEqual(state["opportunities"]["opp:fixture-delta-pilot"]["stage"], "identified")
        opp = state["opportunities"]["opp:fixture-delta-pilot"]
        self.assertEqual((opp["origin"], opp["engagement"], opp["next_action"]), ("crm_entry", "no recorded interaction", None))
        # Past first contact, an open opportunity without a next action needs attention.
        logged, _ = self.event("interaction.log", "opp:fixture-delta-pilot", {
            "channel": "meeting", "direction": "outbound", "summary": "Fixture intro meeting", "occurred_on": "2026-11-12"})
        self.event("stage.change", "opp:fixture-delta-pilot", {"stage": "drafted"})
        with self.assertRaisesRegex(aos_crm.CrmError, "cannot move to call_booked"):
            self.event("stage.change", "opp:fixture-delta-pilot", {"stage": "call_booked", "interaction": logged["event_id"]}, key="to-booked")
        self.event("stage.change", "opp:fixture-delta-pilot", {"stage": "sent", "interaction": logged["event_id"]}, key="to-sent")
        state = aos_crm.project(self.root, now=NOW)
        self.assertIn(("no_next_action", "opp:fixture-delta-pilot"), {(r["rule"], r["record_id"]) for r in state["attention"]})
        self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())  # CRM-only record


class ScopeAndLinkTests(CrmCase):
    def test_scope_isolation(self):
        self.event("organization.create", "org:client-a-only", {"name": "Client A internal"}, scope="client-a")
        self.assertNotIn("org:client-a-only", aos_crm.project(self.root, now=NOW)["organizations"])
        self.assertIn("org:client-a-only", aos_crm.project(self.root, scope="client-a", now=NOW)["organizations"])
        self.assertEqual(aos_crm.project(self.root, scope="client-a", now=NOW)["counts"]["opportunities"], 0)
        with self.assertRaisesRegex(aos_crm.CrmError, "unknown CRM record"):
            self.event("interaction.log", ALPHA, {"channel": "note", "direction": "internal", "summary": "x",
                                                  "occurred_on": "2026-11-16"}, scope="client-a")
        with self.assertRaisesRegex(aos_crm.CrmError, "scope refused"):
            aos_crm.project(self.root, scope="client-disabled", now=NOW)
        with self.assertRaisesRegex(aos_crm.CrmError, "scope refused"):
            aos_crm.project(self.root, scope="client:example-dealership-sales-coach", now=NOW)
        with self.assertRaisesRegex(aos_crm.CrmError, "Brain reference refused"):
            self.event("link.add", ALPHA, refs=("business_brain:memory/client-a.md",))
        with self.assertRaisesRegex(aos_crm.CrmError, "Brain reference refused"):
            self.event("link.add", ALPHA, refs=("business_brain:memory/example_dealership_sales_coach.md",), key="ns-key-1")

    def test_job_link_shows_live_job_state_and_unknown_jobs_are_refused(self):
        with self.assertRaisesRegex(aos_crm.CrmError, "unknown job"):
            self.event("link.add", ALPHA, refs=("AOS-2026-10999",))
        job, _ = aos_jobs.create(self.root, conversation_id="dashboard-crm", topic="crm", request="Draft a fixture follow-up",
                                 executor={"kind": "agent", "agent": "crm.followup_draft@0.1.0", "input": {}})
        self.event("link.add", ALPHA, {"note": "follow-up draft job"}, refs=(job["id"],))
        opp = aos_crm.project(self.root, now=NOW)["opportunities"][ALPHA]
        self.assertEqual([(row["id"], row["status"]) for row in opp["jobs"]], [(job["id"], "queued")])
        job.update(status="waiting_input")
        aos_jobs.save(self.root, job)
        attention = aos_crm.project(self.root, now=NOW)["attention"]
        self.assertIn(("linked_job_needs_attention", ALPHA), {(r["rule"], r["record_id"]) for r in attention})


class ApiTests(CrmCase):
    def test_http_routes_read_write_and_refuse(self):
        import main as backend
        from fastapi.testclient import TestClient

        with patch.object(backend, "BASE_DIR", self.root):
            client = TestClient(backend.app)
            home = client.get("/api/crm/home").json()
            self.assertEqual(home["counts"]["opportunities"], 3)
            self.assertEqual(home["attention"][0]["rule"], "draft_awaiting_casey")
            body = {"kind": "interaction.log", "record_id": ALPHA, "basis": "confirmed",
                    "payload": {"channel": "meeting", "direction": "internal", "summary": "Fixture meeting",
                                "occurred_on": "2026-11-16"}, "source_refs": ["casey:dashboard-entry"], "request_key": "http-1"}
            first = client.post("/api/crm/events", json=body).json()
            second = client.post("/api/crm/events", json=body).json()
            self.assertEqual((first["created"], second["created"]), (True, False))
            self.assertEqual(first["record"]["interactions"][0]["summary"], "Fixture meeting")
            record = client.get(f"/api/crm/records/{ALPHA}").json()
            self.assertEqual(record["contact_record"]["name"], "Ada Example")
            self.assertEqual(client.get("/api/crm/records/opp:missing").status_code, 404)
            self.assertEqual(client.get("/api/crm/overview", params={"scope": "client:example-dealership-sales-coach"}).status_code, 403)
            bad = client.post("/api/crm/events", json={**body, "request_key": "http-2", "payload": {"channel": "fax"}})
            self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
