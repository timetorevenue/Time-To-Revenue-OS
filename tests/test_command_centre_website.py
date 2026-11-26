"""Websites & Digital (tools/aos_website.py), offline against a disposable fixture site repository.

The real path runs end to end: David's turn (scripted model reply) -> aos_website.submit_request
-> aos_jobs job -> M1 dispatch -> isolated worktree -> inert fixture worker -> scope check ->
the site's own tests -> local preview -> hashed artifacts -> receipt -> held WEBSITE_PUBLISH effect
-> notice. The Codex worker is exercised once against a stub executable (no model).

Revisit: when aos_website's work order, the aos_jobs executor contract or David's schema changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.command_centre_fixture import ROOT, crm_environment, git, make_site_repo, write_website_registry

import aos_authority  # noqa: E402
import aos_codex_policy  # noqa: E402
import aos_david  # noqa: E402
import aos_jobs  # noqa: E402
import aos_website  # noqa: E402
from aos_job_ids import storage_key  # noqa: E402

HEADLINE = "Grow your business. Not your workload."
CONV = "telegram-111"


def david_reply(**fields):
    value = {"action": "reply", "reply": "", "topic": "", "query": "", "request": "", "records": [], "people": [],
             "resume_job": "", "restrictions": [], "website_project": "",
             "handoff": {name: "" for name in aos_david.HANDOFF_FIELDS}}
    value.update(fields)
    return value


class Port:
    """ModelPort double: David's replies are scripted; nothing else may call a model."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, **kw):
        self.calls.append((kw.get("agent"), prompt))
        if kw.get("agent") != "david":
            raise AssertionError(f"unexpected model call by {kw.get('agent')}")
        return {"ok": True, "status": "ok", "json": self.replies.pop(0), "latency_seconds": 0.01,
                "usage": {"available": True, "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 0},
                "invocation_id": f"modelport-test-{len(self.calls)}", "model": "gpt-6-luna", "effort": "low",
                "agent": "david", "ledger": {"recorded": False}, "failure_class": "", "detail": ""}


class Threads:
    def __init__(self):
        self.notes = {}

    def read(self, identity):
        return self.notes.get(identity, "")

    def write(self, identity, body, session_id):
        self.notes[identity] = body


class WebsiteCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "runtime"
        self.root.mkdir()
        env = crm_environment(self.root)
        self.paths = env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        self.repo = make_site_repo(Path(tmp.name) / "fixture-site")
        self.repo_head = git(self.repo, "rev-parse", "HEAD")
        write_website_registry(self.root, self.repo)
        (self.root / "queue/notifications.json").write_text(json.dumps({"allowlist": {"telegram": ["111"]}}))
        self.sent = []

    def send(self, chat, text, *, kind, document_paths, root):
        decision = aos_authority.action_authorization(root, None, action="TELEGRAM_SEND_MESSAGE", target=chat,
                                                      notification_kind=kind)
        if not decision["authorized"]:
            raise aos_authority.SendError("not authorized")
        self.sent.append({"chat": chat, "text": text, "documents": document_paths})
        return {"message_id": len(self.sent)}

    def brain_read(self, scope, pointer):
        path = self.paths["vault"] / pointer.removeprefix("business_brain:")
        return {"content": path.read_text(), "provenance": {"path": pointer, "client_scope": scope, "fixture": True}}

    def david(self, replies):
        port = Port(replies)
        david = aos_david.David(self.root, model=port, search=lambda q: [], threads=Threads())
        runner = aos_jobs.JobRunner(self.root, workers=2, brain_read=self.brain_read,
                                    perform=lambda *a: self.fail("no connector effect may be performed"),
                                    notify=lambda job: david.job_finished(job, send=self.send))
        david.runner = runner
        self.addCleanup(runner.shutdown, True)
        return david, port

    def settle(self, job_id, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = aos_jobs.load(self.root, job_id)
            if job["status"] in aos_jobs.SETTLED and (job.get("notification") or {}).get("status"):
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} did not settle: {aos_jobs.load(self.root, job_id)['status']}")

    def run_direct(self, request, project="fixture-site", worker=None):
        job, _ = aos_website.submit_request(self.root, project=project, request=request, conversation_id="dashboard-websites")
        if worker is not None:
            with patch.object(aos_website, "fixture_worker", lambda project: worker):
                return aos_jobs.execute(self.root, job["id"], brain_read=self.brain_read)
        return aos_jobs.execute(self.root, job["id"], brain_read=self.brain_read)

    def assert_checkout_untouched(self):
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.repo_head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertIn("Make your business run better.", (self.repo / "site/index.html").read_text())
        self.assertEqual(git(self.repo, "remote"), "")  # nothing to push to, and nothing pushed


class EndToEnd(WebsiteCase):
    def test_david_request_to_verified_receipt_preview_and_held_publish(self):
        request = f"Change the homepage headline to '{HEADLINE}'"
        david, port = self.david([david_reply(action="delegate", topic="example-site", request=request,
                                              website_project="fixture-site", reply="I'll make that change.")])
        answer = david.turn(CONV, request)
        self.assertTrue(answer["success"], answer)
        self.assertIn("Website projects (registered; data):\n- fixture-site: Fixture Co website", port.calls[0][1])
        self.assertIn("Publishing stays held", answer["response"])
        job = self.settle(answer["job_id"])
        self.assertEqual(job["status"], "done", job.get("detail"))
        self.assertEqual([step["id"] for step in job["result"]["steps"]],
                         ["project", "preflight", "worktree", "icm", "worker", "scope", "tests", "preview"])
        self.assertEqual([(row["action"], row["status"]) for row in job["effects"]], [("WEBSITE_PUBLISH", "held")])
        self.assertIn("D-2026-11-15-01", job["effects"][0]["detail"])
        verified, problems = aos_jobs.verify_artifacts(self.root, job["artifacts"])
        self.assertEqual((len(verified), problems), (len(job["artifacts"]), []))
        refs = {row["ref"] for row in job["artifacts"]}
        self.assertTrue({"work_order", "diff", "tests", "preview", "outcome"} <= refs)
        receipt = (self.root / job["receipts"][0]["path"]).read_text()
        self.assertTrue(receipt.startswith("PASS\nJob: " + job["id"]))
        self.assertIn("Effect: WEBSITE_PUBLISH held", receipt)
        self.assertIn("Brain: business_brain:memory/website_and_content.md scope=global", receipt)
        view = aos_website.job_view(self.root, job)
        self.assertEqual((view["publish"]["status"], view["tests"]["passed"]), ("held", True))
        self.assertEqual(view["changed_files"], ["site/index.html"])
        self.assertIn(f"+<h1>{HEADLINE}</h1>", view["diff"])
        preview = aos_website.preview_file(self.root, job, "index.html").read_text()
        self.assertIn(f"<h1>{HEADLINE}</h1>", preview)
        self.assertEqual(view["icm"]["files"][0]["path"], "AGENTS.md")
        # The registered checkout is untouched; the change lives on the job's own local branch.
        self.assert_checkout_untouched()
        branch = "ttros/website/" + storage_key(job["id"]).lower()
        self.assertEqual(git(self.repo, "rev-parse", branch), view["commit"])
        # One notice to Casey's allowlisted chat, with no files attached and the hold stated.
        (notice,) = self.sent
        self.assertEqual((notice["chat"], notice["documents"]), ("111", []))
        self.assertIn("WEBSITE_PUBLISH -> fixture-co.example (never deployed): held", notice["text"])
        self.assertEqual([agent for agent, _ in port.calls], ["david"])  # the fixture worker made no model call
        self.assertEqual(job["usage"]["model_calls"], 0)

    def test_unknown_and_unavailable_projects_start_no_job(self):
        david, _ = self.david([david_reply(action="delegate", request="Change the headline to 'x y z'", website_project="nope"),
                               david_reply(action="delegate", request="Change the headline to 'x y z'",
                                           website_project="unchecked-site")])
        first = david.turn("dashboard-1", "change the nope site")
        second = david.turn("dashboard-1", "change the unchecked site")
        self.assertIn("No job was started: no registered website project is called 'nope'", first["response"])
        self.assertIn("cannot take changes here: no local checkout is registered", second["response"])
        self.assertEqual(aos_jobs.list_jobs(self.root), [])
        self.assertNotIn("job_id", first)


class FailureHandling(WebsiteCase):
    def test_out_of_scope_change_fails_without_preview_or_publish_effect(self):
        job = self.run_direct("Add a deploy workflow to the site")
        self.assertEqual((job["status"], job["failure_class"]), ("failed", "out_of_scope_change"))
        self.assertIn(".github/workflows/deploy.yml is a protected path", job["detail"])
        self.assertEqual(job["effects"], [])
        view = aos_website.job_view(self.root, job)
        self.assertIsNone(view["preview"])
        self.assertIn("deploy.yml", view["diff"])
        self.assertTrue((self.root / job["receipts"][0]["path"]).read_text().startswith("NEEDS ATTENTION"))
        self.assert_checkout_untouched()

    def test_failing_site_tests_fail_the_job_with_the_log(self):
        job = self.run_direct("Please remove the contact link from the homepage")
        self.assertEqual((job["status"], job["failure_class"]), ("failed", "tests_failed"))
        view = aos_website.job_view(self.root, job)
        self.assertIn("navigation must link to contact.html", view["tests_log"])
        self.assertEqual((view["publish"]["status"], view["preview"]), ("not_ready", None))

    def test_unclear_request_ends_with_what_is_missing(self):
        job = self.run_direct("Make the website better somehow")
        self.assertEqual((job["status"], job["failure_class"]), ("failed", "change_request_unclear"))
        self.assertIn("Missing:", job["detail"])

    def test_icm_outside_the_projects_scope_stops_before_any_change(self):
        write_website_registry(self.root, self.repo, icm_brain=["business_brain:memory/example_dealership_sales_coach.md"])
        job = self.run_direct(f"Change the homepage headline to '{HEADLINE}'")
        self.assertEqual((job["status"], job["failure_class"]), ("failed", "icm_unavailable"))
        self.assertIn("example_dealership_sales_coach.md", job["detail"])
        self.assertNotIn("worker", [step["id"] for step in job["result"]["steps"]])

    def test_a_dashboard_retry_returns_the_same_job(self):
        first, created = aos_website.submit_request(self.root, project="fixture site", request=f"headline to '{HEADLINE}'",
                                                    conversation_id="dashboard-websites", dedupe_key="dash\0abc123")
        again, created_again = aos_website.submit_request(self.root, project="fixture-site", request=f"headline to '{HEADLINE}'",
                                                          conversation_id="dashboard-websites", dedupe_key="dash\0abc123")
        self.assertEqual((created, created_again, first["id"]), (True, False, again["id"]))
        with self.assertRaises(aos_jobs.JobError):
            aos_website.submit_request(self.root, project="fixture-site", request="headline to 'Something else'",
                                       conversation_id="dashboard-websites", dedupe_key="dash\0abc123")

    def test_a_crash_after_the_worker_does_not_run_the_worker_again(self):
        calls = []

        def worker(worktree, brief):
            calls.append(brief["request"])
            page = worktree / "site/index.html"
            page.write_text(page.read_text().replace("Make your business run better.", HEADLINE))
            return {"status": "ok", "summary": "edited", "usage": {"model_calls": 0, "exact": True}}

        job, _ = aos_website.submit_request(self.root, project="fixture-site", request=f"headline to '{HEADLINE}'",
                                            conversation_id="dashboard-websites")
        aos_jobs.claim(self.root, job["id"])

        class Died(BaseException):
            pass

        with patch.object(aos_website, "fixture_worker", lambda project: worker), \
                patch.object(aos_website, "scope_problems", side_effect=Died()):
            with self.assertRaises(Died):
                aos_website.run_work_order(self.root, aos_jobs._item_view(aos_jobs.load(self.root, job["id"])))
        current = aos_jobs.load(self.root, job["id"])
        current["claim"] = {"pid": 999999999, "start_id": "dead", "at": "2026-11-16T00:00:00+00:00"}
        aos_jobs.save(self.root, current)
        self.assertEqual(aos_jobs.recover(self.root)["requeued"], [job["id"]])
        with patch.object(aos_website, "fixture_worker", lambda project: worker):
            done = aos_jobs.execute(self.root, job["id"], brain_read=self.brain_read)
        self.assertEqual((done["status"], len(calls)), ("done", 1))
        worker_step = next(step for step in json.loads((self.root / next(
            row["path"] for row in done["artifacts"] if row["ref"] == "outcome")).read_text())["steps"] if step["id"] == "worker")
        self.assertTrue(worker_step.get("replayed"))


class CodexWorker(WebsiteCase):
    def test_codex_worker_runs_under_the_launch_contract_and_records_exact_usage(self):
        stub_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(stub_dir, ignore_errors=True))
        stub = stub_dir / "codex"
        stub.write_text("#!/bin/sh\necho \"$CODEX_HOME\" > \"$CODEX_HOME_SEEN\"\ncat > \"$PWD/.prompt-seen\"\nrm \"$PWD/.prompt-seen\"\n"
                        "sed -i 's#<h1>[^<]*</h1>#<h1>Stub Codex headline</h1>#' site/index.html\n"
                        "echo '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1200,\"output_tokens\":80,\"cached_input_tokens\":200}}'\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        write_website_registry(self.root, self.repo, worker="codex", fixture=False)
        target = dataclasses.replace(aos_codex_policy.CODEX_TARGET, executable=stub,
                                     linux_user=aos_codex_policy.effective_linux_user())
        # The job's account selection (aos_codex_policy.select_codex_account) chooses B here.
        home_b = stub_dir / "home-b"
        seen = stub_dir / "codex-home-seen"
        selection = aos_codex_policy.AccountSelection(slot="B", reason="fixture: A below threshold", basis="fixture",
                                                      checked_at="2026-11-16T00:00:00Z", threshold_percent=20)
        chosen = aos_codex_policy.account_target(aos_codex_policy.CodexAccount("B", home_b), target, selection)
        with patch.object(aos_codex_policy, "CODEX_TARGET", target), \
                patch.object(aos_codex_policy, "select_codex_account", return_value=chosen), \
                patch.dict(os.environ, {"CODEX_HOME_SEEN": str(seen)}):
            job = self.run_direct("Change the homepage headline to 'Stub Codex headline'")
        self.assertEqual(job["status"], "done", job.get("detail"))
        self.assertEqual(seen.read_text().strip(), str(home_b))          # the launched process got B's home
        self.assertEqual(job["codex_account"]["slot"], "B")
        ledger_rows = [json.loads(line) for line in (self.root / "queue/token_ledger.jsonl").read_text().splitlines()]
        self.assertEqual(ledger_rows[-1]["model_selection"]["codex_account"]["slot"], "B")
        self.assertIn("Codex account: B", (self.root / job["receipts"][0]["path"]).read_text())
        self.assertIn('forced_login_method="chatgpt"', aos_codex_policy.build_exec_command(chosen))
        self.assertEqual((job["usage"]["model_calls"], job["usage"]["total_tokens"], job["usage"]["exact"]), (1, 1280, True))
        ledger = [json.loads(line) for line in (self.root / "queue/token_ledger.jsonl").read_text().splitlines()]
        self.assertEqual([(row.get("item_id"), row.get("surface")) for row in ledger][-1:], [(job["id"], "website:codex")])
        self.assertIn("Token usage:", (self.root / job["receipts"][0]["path"]).read_text())
        self.assert_checkout_untouched()

    def _stub_and_target(self, body: str):
        stub_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(stub_dir, ignore_errors=True))
        stub = stub_dir / "codex"
        stub.write_text("#!/bin/sh\ntouch \"$CODEX_HOME_SEEN\"\ncat >/dev/null\n" + body)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        write_website_registry(self.root, self.repo, worker="codex", fixture=False)
        target = dataclasses.replace(aos_codex_policy.CODEX_TARGET, executable=stub,
                                     linux_user=aos_codex_policy.effective_linux_user())
        chosen = aos_codex_policy.account_target(aos_codex_policy.CodexAccount("A", stub_dir / "home-a"), target)
        return stub_dir / "launched", target, chosen

    def test_provider_usage_limit_is_quota_unavailable_not_worker_failed(self):
        launched, target, chosen = self._stub_and_target(
            "echo '{\"type\":\"turn.failed\",\"error\":{\"message\":\"You have hit your usage limit\"}}'\n"
            "echo 'ERROR: usage limit reached' >&2\nexit 1\n")
        with patch.object(aos_codex_policy, "CODEX_TARGET", target), \
                patch.object(aos_codex_policy, "select_codex_account", return_value=chosen), \
                patch.dict(os.environ, {"CODEX_HOME_SEEN": str(launched)}):
            job = self.run_direct("Change the homepage headline to 'Stub Codex headline'")
        self.assertTrue(launched.exists())
        self.assertEqual((job["status"], job["failure_class"]), ("failed", "quota_unavailable"))
        self.assert_checkout_untouched()

    def test_billed_route_variables_refuse_the_website_worker_before_launch(self):
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL"):
            with self.subTest(variable=name):
                launched, target, chosen = self._stub_and_target("exit 0\n")
                with patch.object(aos_codex_policy, "CODEX_TARGET", target), \
                        patch.object(aos_codex_policy, "select_codex_account", return_value=chosen), \
                        patch.dict(os.environ, {"CODEX_HOME_SEEN": str(launched), name: "not-real"}):
                    job = self.run_direct(f"Change the homepage headline to 'Billed {name}'")
                self.assertFalse(launched.exists())
                self.assertEqual((job["status"], job["failure_class"]), ("failed", "billed_route_refused"))
                self.assertIn(name, job["detail"])
        self.assert_checkout_untouched()

    def test_no_eligible_account_defers_the_website_job_without_launch(self):
        launched, target, _chosen = self._stub_and_target("exit 0\n")
        refusal = aos_codex_policy.AccountUnavailable("quota_unavailable", "A at 10%, B needs sign-in", accounts=[],
                                                      recheck_after=time.time() + 600)
        with patch.object(aos_codex_policy, "CODEX_TARGET", target), \
                patch.object(aos_codex_policy, "select_codex_account", side_effect=refusal), \
                patch.dict(os.environ, {"CODEX_HOME_SEEN": str(launched)}):
            job = self.run_direct("Change the homepage headline to 'Deferred headline'")
        self.assertFalse(launched.exists())
        self.assertEqual((job["status"], job["deferred"]["failure_class"], job["attempts"]), ("queued", "quota_unavailable", 0))
        self.assertFalse(job.get("receipts"))
        self.assert_checkout_untouched()


class Api(WebsiteCase):
    def test_routes_create_read_and_serve_only_verified_preview_files(self):
        import main as backend
        from fastapi.testclient import TestClient

        with patch.object(backend, "BASE_DIR", self.root), patch.object(backend, "RUNNER", None):
            client = TestClient(backend.app)
            projects = client.get("/api/website/projects").json()["projects"]
            self.assertEqual({row["id"]: row["state"] for row in projects},
                             {"fixture-site": "ready", "unchecked-site": "not_checked_out"})
            body = {"project": "fixture-site", "request": f"Change the homepage headline to '{HEADLINE}'", "request_key": "web-key-1"}
            created = client.post("/api/website/requests", json=body).json()
            again = client.post("/api/website/requests", json=body).json()
            job_id = created["job"]["job_id"]
            self.assertEqual((created["created"], again["created"], again["job"]["job_id"]), (True, False, job_id))
            aos_jobs.execute(self.root, job_id, brain_read=self.brain_read)
            view = client.get(f"/api/website/jobs/{job_id}").json()
            self.assertEqual((view["status"], view["publish"]["status"]), ("done", "held"))
            page = client.get(view["preview"]["url"])
            self.assertEqual(page.status_code, 200)
            self.assertIn(HEADLINE, page.text)
            self.assertIn("sandbox", page.headers["content-security-policy"])
            self.assertEqual(client.get(f"/api/website/jobs/{job_id}/preview/../work_order.json").status_code, 404)
            (aos_website.preview_file(self.root, aos_jobs.load(self.root, job_id), "index.html")).write_text("tampered")
            self.assertEqual(client.get(view["preview"]["url"]).status_code, 404)
            refused = client.post("/api/website/requests", json={**body, "project": "unchecked-site", "request_key": "web-key-2"})
            self.assertEqual(refused.status_code, 422)
            self.assertEqual(client.get("/api/website/jobs/AOS-2026-99999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
