"""Phase 2 sales acceptance, isolated temporary state, real APIs, no runtime startup."""
import datetime as dt
from unittest.mock import patch

from tests.test_command_centre_crm import CrmCase, ALPHA
from tests.command_centre_fixture import NOW
import aos_crm

CONTACT = "contact:" + ALPHA.split(":", 1)[1]
SALES = "opp:synthetic-sales"


class SalesTests(CrmCase):
    def create(self, rid=SALES, name="Synthetic sales opportunity", **extra):
        return self.event("opportunity.create", rid, {"name": name, "contact": CONTACT, "workflow": "sales", **extra})

    def test_complete_http_sales_to_delivery_scenario(self):
        import main as backend
        from fastapi.testclient import TestClient
        with patch.object(backend, "BASE_DIR", self.root), patch.object(aos_crm, "_now", return_value=NOW), \
                patch.object(backend, "start_david", side_effect=AssertionError("no model runtime")), \
                patch("aos_jobs.create", side_effect=AssertionError("no jobs or external actions")):
            client = TestClient(backend.app)
            record = None
            counter = 0
            def save(kind, payload, refs=None):
                nonlocal record, counter
                counter += 1
                body = {"kind": kind, "record_id": SALES, "payload": payload, "request_key": f"phase2-{counter}",
                        "source_refs": refs or ["casey:dashboard-entry"]}
                if record:
                    body["expected_revision"] = record["revision"]
                result = client.post("/api/crm/events", json=body)
                self.assertEqual(result.status_code, 200, result.text)
                record = result.json()["record"]
            save("opportunity.create", {"name": "Synthetic sales opportunity", "contact": CONTACT, "workflow": "sales"})
            self.assertEqual(record["stage"], "discovery")
            self.assertEqual(record["contact_record"]["id"], CONTACT)
            save("stage.change", {"stage": "qualified", "reason": "Synthetic need qualified by Casey"})
            save("stage.change", {"stage": "proposal", "reason": "Synthetic scope proposed for review"})
            save("interaction.log", {"channel": "meeting", "direction": "internal", "summary": "Synthetic scope review", "occurred_on": "2026-11-15"})
            save("next_action.set", {"text": "Review synthetic proposal", "due": "2026-11-15", "owner": "casey"})
            save("value.set", {"amount": 2500, "currency": "CAD"})
            home = client.get("/api/crm/home").json()
            self.assertIn(SALES, next(group["record_ids"] for group in home["pipeline_summary"]["groups"] if group["stage"] == "proposal"))
            self.assertIn(SALES, [item["record_id"] for item in home["attention"] if item["rule"] == "overdue_follow_up"])
            save("next_action.set", {"text": "Review revised synthetic proposal", "due": "2026-11-18", "owner": "casey"})
            home = client.get("/api/crm/home").json()
            self.assertFalse([item for item in home["attention"] if item["record_id"] == SALES and item["rule"] == "overdue_follow_up"])
            save("stage.change", {"stage": "won", "reason": "Casey recorded synthetic acceptance"})
            home = client.get("/api/crm/home").json()
            self.assertEqual(home["pipeline_summary"]["recent_outcomes"][0]["stage"], "won")
            self.assertIn("handoff_needed", [r["rule"] for r in home["attention"] if r["record_id"] == SALES])
            pointer = "business_brain:prospects/" + ALPHA.split(":", 1)[1] + ".md"
            save("handoff.prepare", {"confirmed_scope": "Synthetic internal workflow review only", "next_delivery_action": "Casey reviews the synthetic kickoff checklist"}, [pointer])
            reopened = client.get(f"/api/crm/records/{SALES}").json()
            self.assertEqual(reopened["handoff"]["status"], "ready_for_review")
            self.assertEqual(reopened["handoff"]["client"]["id"], reopened["organization"])
            self.assertEqual(reopened["handoff"]["sources"], [pointer])
            self.assertEqual([row["stage"] for row in reopened["history"]], ["discovery", "qualified", "proposal", "won"])
            self.assertTrue(all(row.get("reason") for row in reopened["history"][1:]))
            self.assertEqual(len(reopened["events"]), 9)
            self.assertEqual(aos_crm.record_detail(self.root, SALES, now=NOW)["handoff"], reopened["handoff"])
            # Existing deterministic reader and HTTP use the exact same projection.
            self.assertEqual(client.get("/api/crm/overview").json()["pipeline_summary"], aos_crm.project(self.root, now=NOW)["pipeline_summary"])
            self.assertEqual(client.get("/api/crm/home").json()["pipeline_summary"]["open_values"], [])
            self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())

    def test_invalid_relationships_duplicates_and_scopes_are_atomic(self):
        for payload in ({"contact": "contact:missing"}, {"contact": "", "organization": ""}, {"owner": "other"}, {"workflow": "other"}, {"stage": "negotiation"}):
            with self.subTest(payload=payload), self.assertRaises(aos_crm.CrmError):
                self.create(**payload)
        self.assertEqual(self.events_text(), "")
        self.create()
        before = self.events_text()
        with self.assertRaisesRegex(aos_crm.CrmError, "duplicate opportunity"):
            self.create("opp:duplicate-id", name="SYNTHETIC SALES OPPORTUNITY")
        with self.assertRaisesRegex(aos_crm.CrmError, "already exists"):
            self.event("opportunity.create", SALES, {"name": "Other", "contact": CONTACT}, key="different-request")
        with self.assertRaisesRegex(aos_crm.CrmError, "unknown contact"):
            self.event("opportunity.create", "opp:cross-client", {"name": "Synthetic", "contact": CONTACT, "workflow": "sales"}, scope="client-a")
        with self.assertRaisesRegex(aos_crm.CrmError, "unknown CRM record"):
            self.event("opportunity.update", SALES, {"name": "Other"}, scope="client-a")
        self.assertEqual(self.events_text(), before)
        self.assertEqual(aos_crm.project(self.root, now=NOW)["counts"]["contacts"], 3)

    def test_stage_validation_lost_reason_and_recent_outcomes(self):
        self.create()
        for payload in ({"stage": "invoice_sent", "reason": "Invalid"}, {"stage": "won", "reason": "Skip"}, {"stage": "qualified"}, {"stage": "lost"}):
            with self.subTest(payload=payload), self.assertRaises(aos_crm.CrmError):
                self.event("stage.change", SALES, payload, key="invalid-stage")
        self.event("stage.change", SALES, {"stage": "lost", "reason": "Synthetic timing mismatch"})
        record = aos_crm.record_detail(self.root, SALES, now=NOW)
        self.assertEqual(record["outcome_reason"], "Synthetic timing mismatch")
        summary = aos_crm.home_summary(self.root, now=NOW)["pipeline_summary"]
        self.assertEqual(summary["recent_outcomes"][0]["reason"], "Synthetic timing mismatch")
        self.assertEqual(aos_crm.home_summary(self.root, now=NOW + dt.timedelta(days=8))["pipeline_summary"]["recent_outcomes"], [])

    def test_missing_and_nonfinite_values_and_currency_totals(self):
        self.create()
        self.assertEqual(aos_crm.home_summary(self.root, now=NOW)["pipeline_summary"]["open_without_value"], 4)
        for amount, currency in ((None, "CAD"), ("NaN", "CAD"), ("Infinity", "CAD"), (-1, "CAD"), (1, "CADjunk")):
            with self.subTest(amount=amount, currency=currency), self.assertRaises(aos_crm.CrmError):
                self.event("value.set", SALES, {"amount": amount, "currency": currency}, key="bad-value")
        self.event("value.set", SALES, {"amount": 1250.25, "currency": "CAD"}, basis="estimate")
        self.event("value.set", ALPHA, {"amount": 99.75, "currency": "USD"}, basis="confirmed", key="usd-value")
        summary = aos_crm.home_summary(self.root, now=NOW)["pipeline_summary"]
        self.assertEqual(summary["open_values"], [{"currency": "CAD", "basis": "estimate", "amount": 1250.25}, {"currency": "USD", "basis": "confirmed", "amount": 99.75}])
        self.assertEqual(summary["open_without_value"], 2)

    def test_stale_updates_handoffs_and_external_actions_are_refused(self):
        self.create()
        revision = aos_crm.record_detail(self.root, SALES, now=NOW)["revision"]
        self.event("opportunity.update", SALES, {"name": "Renamed synthetic", "owner": "casey"})
        with self.assertRaises(aos_crm.CrmConflict):
            aos_crm.record_event(self.root, client_scope="global", kind="opportunity.update", record_id=SALES,
                                 payload={"name": "Obsolete"}, expected_revision=revision, request_key="obsolete-edit", now=NOW)
        for kind, payload in (("handoff.prepare", {"confirmed_scope": "Unwon", "next_delivery_action": "No"}), ("email.send", {}), ("invoice.create", {}), ("project.create", {})):
            with self.subTest(kind=kind), self.assertRaises(aos_crm.CrmError):
                self.event(kind, SALES, payload)

    def test_stalled_and_missing_action_are_reproducible(self):
        self.create()
        self.event("stage.change", SALES, {"stage": "qualified", "reason": "Synthetic qualification"}, key="qualify")
        self.event("stage.change", SALES, {"stage": "proposal", "reason": "Synthetic proposal"}, key="propose")
        state = aos_crm.project(self.root, now=NOW + dt.timedelta(days=14))
        self.assertTrue(state["opportunities"][SALES]["stalled"])
        self.assertEqual({row["rule"] for row in state["attention"] if row["record_id"] == SALES}, {"no_next_action", "stalled_opportunity"})

    def test_negotiation_win_requires_interaction_and_handoff_requires_scope_evidence(self):
        self.create()
        self.event("stage.change", SALES, {"stage": "qualified", "reason": "Synthetic need"}, key="qualify")
        self.event("stage.change", SALES, {"stage": "proposal", "reason": "Synthetic proposal"}, key="propose")
        self.event("stage.change", SALES, {"stage": "negotiation", "reason": "Synthetic terms review"}, key="negotiate")
        with self.assertRaisesRegex(aos_crm.CrmError, "confirmed interaction"):
            self.event("stage.change", SALES, {"stage": "won", "reason": "No evidence yet"}, key="no-win")
        self.event("interaction.log", SALES, {"channel": "note", "direction": "internal", "summary": "Synthetic acceptance", "occurred_on": "2026-11-16"})
        self.event("stage.change", SALES, {"stage": "won", "reason": "Recorded synthetic acceptance"}, key="win-synthetic")
        for payload, refs in (({}, ("casey:dashboard-entry",)), ({"confirmed_scope": "Synthetic"}, ("casey:dashboard-entry",)),
                              ({"confirmed_scope": "Synthetic", "next_delivery_action": "Review"}, ())):
            with self.subTest(payload=payload, refs=refs), self.assertRaises(aos_crm.CrmError):
                self.event("handoff.prepare", SALES, payload, refs=refs, key="bad-handoff")
        with self.assertRaisesRegex(aos_crm.CrmError, "Brain reference refused"):
            self.event("handoff.prepare", SALES, {"confirmed_scope": "Synthetic", "next_delivery_action": "Review"}, refs=("business_brain:memory/client-a.md",))
        self.event("handoff.prepare", SALES, {"confirmed_scope": "Synthetic scope version 1", "next_delivery_action": "Review internally"}, key="handoff-v1")
        self.event("handoff.prepare", SALES, {"confirmed_scope": "Synthetic scope version 2", "next_delivery_action": "Review revised scope internally"}, key="handoff-v2")
        record = aos_crm.record_detail(self.root, SALES, now=NOW)
        self.assertEqual(record["handoff"]["confirmed_scope"], "Synthetic scope version 2")
        self.assertEqual(len([row for row in record["events"] if row["kind"] == "handoff.prepare"]), 2)
        self.assertEqual([row["stage"] for row in record["history"]], ["discovery", "qualified", "proposal", "negotiation", "won"])

    def test_sales_history_uses_business_date_and_existing_stage_vocabulary_stays_intact(self):
        created_at = dt.datetime(2026, 11, 17, 0, 30, tzinfo=dt.timezone.utc)
        aos_crm.record_event(self.root, client_scope="global", kind="opportunity.create", record_id=SALES,
                             payload={"name": "Synthetic date boundary", "contact": CONTACT, "workflow": "sales"},
                             request_key="date-boundary-create", now=created_at)
        record = aos_crm.record_detail(self.root, SALES, now=created_at)
        self.assertEqual((record["stage_date"], record["history"][0]["at"]), ("2026-11-16", "2026-11-16"))
        with self.assertRaises(aos_crm.CrmError):
            self.event("stage.change", ALPHA, {"stage": "qualified", "reason": "Cannot bypass ledger"})
        self.assertEqual(aos_crm.record_detail(self.root, ALPHA, now=NOW)["stage"], "drafted")
        self.assertEqual(self.ledger_before, (self.root / "queue/prospects.jsonl").read_text())
