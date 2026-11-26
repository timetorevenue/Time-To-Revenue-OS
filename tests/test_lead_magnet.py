"""Offline guide capture; Revisit: when subscriber or draft-only contracts change."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.command_centre_fixture import crm_environment
import aos_crm
import aos_jobs
import aos_lead_magnet as magnet
import main as backend


class FakeDraftAdapter:
    calls = []

    def __init__(self, *, root):
        self.root = root

    def create_draft(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "draft-created", "safe_draft_reference": "gmail-draft:fixture-only"}


class LeadMagnetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        context = crm_environment(self.root)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        FakeDraftAdapter.calls = []

    def capture(self, **extra):
        return magnet.capture(self.root, name="Fixture Visitor", email="Visitor@Example.invalid",
                              guide_consent=True, follow_up_consent=True, **extra)

    def test_capture_to_crm_job_draft_and_dashboard(self):
        result = self.capture(attribution={"utm_source": "linkedin", "utm_campaign": "guide_launch"})
        self.assertEqual(result["guide_url"], magnet.GUIDE)
        self.assertTrue(result["created"])
        self.assertEqual(self.capture(), {**result, "created": False})
        state = aos_crm.project(self.root)
        self.assertEqual(state["counts"]["events"], 4)
        contact = state["contacts"][result["contact_id"]]
        self.assertEqual(contact["email"], "visitor@example.invalid")
        self.assertIn(magnet.SOURCE, contact["sources"])
        self.assertTrue(contact["consent"]["follow_up"])
        self.assertEqual(contact["attribution"]["utm_source"], "linkedin")
        self.assertIsNone(result["opportunity_id"])
        self.assertFalse(any(oid.startswith("opp:guide-") for oid in state["opportunities"]))
        self.assertFalse(contact["subscription"].get("marketing_consent", False))
        with patch("connectors.gmail_draft_adapter.GmailDraftAdapter", FakeDraftAdapter):
            job = aos_jobs.execute(self.root, result["draft_job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["usage"]["model_calls"], 0)
        self.assertEqual(len(FakeDraftAdapter.calls), 1)
        self.assertEqual(FakeDraftAdapter.calls[0]["recipient"], "visitor@example.invalid")
        self.assertIn("Nothing sent", job["result"]["summary"])
        self.assertEqual(aos_crm.home_summary(self.root)["guide_leads"][0]["draft_job"], "done")
        self.assertEqual(aos_jobs.load(self.root, job["id"])["status"], "done")
        self.assertEqual(self.capture()["draft_job_id"], job["id"])
        self.assertEqual(len(FakeDraftAdapter.calls), 1)

    def test_consent_and_client_scope_are_enforced(self):
        for guide, follow in [(False, True), (None, True), (True, None)]:
            with self.subTest(guide=guide, follow=follow), self.assertRaises(magnet.CaptureError):
                magnet.capture(self.root, name="Fixture", email="x@example.invalid",
                               guide_consent=guide, follow_up_consent=follow)
        self.assertEqual(aos_crm.project(self.root)["counts"]["events"], 0)
        no_follow = magnet.capture(self.root, name="Fixture", email="x@example.invalid",
                                   guide_consent=True, follow_up_consent=False)
        self.assertIsNone(no_follow["draft_job_id"])
        widened = magnet.capture(self.root, name="Fixture", email="x@example.invalid",
                                 guide_consent=True, follow_up_consent=True)
        self.assertIsNone(widened["draft_job_id"])
        self.assertEqual(aos_jobs.list_jobs(self.root), [])
        other = magnet.capture(self.root, name="Fixture", email="x@example.invalid",
                               guide_consent=True, follow_up_consent=True, client_scope="client-a")
        self.assertNotEqual(other["contact_id"], no_follow["contact_id"])
        self.assertNotIn(other["contact_id"], aos_crm.project(self.root)["contacts"])
        self.assertNotIn(no_follow["contact_id"], aos_crm.project(self.root, scope="client-a")["contacts"])
        with self.assertRaises(aos_crm.CrmError):
            aos_crm.record_detail(self.root, other["contact_id"], scope="global")

    def test_revocation_before_job_execution_refuses_draft(self):
        result = self.capture()
        revised = magnet.capture(self.root, name="Fixture Visitor", email="visitor@example.invalid",
                                 guide_consent=True, follow_up_consent=False)
        self.assertFalse(revised["follow_up_consent"])
        self.assertIsNone(revised["draft_job_id"])
        self.assertFalse(aos_crm.record_detail(self.root, result["contact_id"])["consent"]["follow_up"])
        self.assertIsNone(revised["opportunity_id"])
        with patch("connectors.gmail_draft_adapter.GmailDraftAdapter", FakeDraftAdapter):
            job = aos_jobs.execute(self.root, result["draft_job_id"])
        self.assertEqual(job["status"], "refused")
        self.assertEqual(FakeDraftAdapter.calls, [])

    def test_partial_failure_replays_without_duplicate_contact_job_or_draft(self):
        original = aos_crm.record_event
        failed = False

        def interrupted(*args, **kwargs):
            nonlocal failed
            if kwargs.get("kind") == "link.add" and not failed:
                failed = True
                raise OSError("fixture interruption")
            return original(*args, **kwargs)

        with patch.object(aos_crm, "record_event", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.capture()
        result = self.capture()
        self.assertEqual(aos_crm.project(self.root)["counts"]["events"], 4)
        self.assertEqual(len(aos_jobs.list_jobs(self.root)), 1)
        with patch("connectors.gmail_draft_adapter.GmailDraftAdapter", FakeDraftAdapter):
            aos_jobs.execute(self.root, result["draft_job_id"])
            aos_jobs.execute(self.root, result["draft_job_id"])
        self.assertEqual(len(FakeDraftAdapter.calls), 1)

    def test_indeterminate_provider_result_is_visible_and_never_replayed(self):
        result = self.capture()
        class UncertainDraft:
            calls = 0
            def create_draft(self, **kwargs):
                self.calls += 1
                return {"status": "blocked-recovery", "canonical_status": "provider-call-pending"}
        provider = UncertainDraft()
        with patch("connectors.gmail_draft_adapter.GmailDraftAdapter", return_value=provider):
            failed = aos_jobs.execute(self.root, result["draft_job_id"])
            repeated = aos_jobs.execute(self.root, result["draft_job_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_class"], "draft_recovery_required")
        self.assertEqual(repeated["status"], "failed")
        self.assertEqual(provider.calls, 1)

    def test_private_backend_route_requires_token_and_pins_global_scope(self):
        with patch.object(backend, "BASE_DIR", self.root), patch.object(backend, "RUNNER", None), \
             patch.dict(os.environ, {"TTROS_LEAD_MAGNET_CAPTURE_TOKEN": "fixture-token"}):
            client = TestClient(backend.app)
            body = {"name": "Fixture Visitor", "email": "v@example.invalid", "guide_consent": True,
                    "follow_up_consent": False}
            self.assertEqual(client.post("/api/lead-magnet/capture", json=body).status_code, 403)
            response = client.post("/api/lead-magnet/capture", json={**body, "client_scope": "client-a"},
                                   headers={"x-ttros-capture-token": "fixture-token"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(response.json()["contact_id"], client.get("/api/crm/overview").json()["contacts"])
            self.assertNotIn(response.json()["contact_id"], client.get("/api/crm/overview?scope=client-a").json()["contacts"])


if __name__ == "__main__":
    unittest.main()
