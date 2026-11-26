"""David -> M1 -> M5 planner -> specialists, offline.

Everything on the path is the real code: David's turn loop, the job store, the M1
runtime and plan runner, M5's shared.planner handler and its specialists' handlers,
artifact verification, effect authorization and the completion notice. Only the model
replies and the Telegram/Composio transports are doubles. A specialist whose eval
recording matches the prompt the plan builds is answered with that recorded reply
(evals.RecordingPort); the planner's and David's replies are scripted.

Revisit: when the David reply schema, the job record or shared.planner changes.
· Last touched: 2026-11-15.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tools", ROOT / "dashboard" / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import aos_authority  # noqa: E402
import aos_david  # noqa: E402
import aos_jobs  # noqa: E402
from aos_agents import evals  # noqa: E402
from aos_agents.package import load_package  # noqa: E402

SYNTH = "research.synthesise@0.1.0"
NOTES = "business_brain:research/callback-notes.md"
QUESTION = "How quickly does a small service business need to return a missed call to keep the lead?"
CASES = {
    "synth": json.loads((ROOT / "agents/packages/research.synthesise/evals/cases.json").read_text()),
    "followup": json.loads((ROOT / "agents/packages/crm.followup_draft/evals/cases.json").read_text()),
}
BRAIN = {NOTES: next(c for c in CASES["synth"]["cases"] if c["id"] == "core-brainref-source")["context"][NOTES]}


def usage(i=100, o=20):
    return {"available": True, "input_tokens": i, "output_tokens": o, "cached_input_tokens": 0}


def reply(**fields):
    value = {"action": "reply", "reply": "", "topic": "", "query": "", "request": "", "records": [], "people": [],
             "resume_job": "", "restrictions": [], "handoff": {name: "" for name in aos_david.HANDOFF_FIELDS}}
    handoff = fields.pop("handoff", {})
    value.update(fields)
    value["handoff"].update(handoff)
    return value


def planner_plan(question=QUESTION, records=(NOTES,)):
    return {"decision": "plan", "reason": "Research brief on callback speed from the callback notes.", "missing": [],
            "tasks": [{"task": "research_brief", "records": list(records), "person": "", "title": "", "date": "",
                       "topic": "", "question": question, "cta": ""}]}


class Port:
    """ModelPort double. David's and the planner's replies are scripted; specialists replay recordings."""

    def __init__(self, david=None, planner=None):
        self.david = list(david or [])
        self.planner = planner or (lambda prompt: planner_plan())
        self.recordings: dict[str, evals.RecordingPort] = {}
        self.prompts: list[tuple[str, str, str]] = []
        self.gates: dict[str, threading.Event] = {}
        self.entered: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.count = 0

    def _ok(self, value, agent, i=100, o=20):
        with self.lock:
            self.count += 1
            n = self.count
        return {"ok": True, "status": "ok", "json": value, "usage": usage(i, o), "latency_seconds": 0.01,
                "invocation_id": f"modelport-test-{n}", "model": "gpt-6-luna", "effort": "low", "agent": agent,
                "ledger": {"recorded": False}, "failure_class": "", "detail": ""}

    def __call__(self, prompt, **kw):
        agent = kw.get("agent") or ""
        self.prompts.append((agent, kw.get("instructions") or "", prompt))
        if agent == "david":
            step = self.david.pop(0)
            return step(prompt) if callable(step) else self._ok(step, agent)
        if agent.startswith("shared.planner"):
            return self._ok(self.planner(prompt), agent, 300, 60)
        for marker, gate in self.gates.items():
            if marker in prompt:
                self.entered[marker].set()
                gate.wait(5)
        package = load_package(agent, ROOT)
        port = self.recordings.setdefault(agent, evals.RecordingPort(
            package.directory / "evals" / "recordings" / f"{evals.binding(package)}.json", None))
        result = port(prompt, **kw)
        with self.lock:
            self.count += 1
            result["invocation_id"] = f"modelport-test-{self.count}"
        return result


class Threads:
    def __init__(self, fail=False):
        self.notes: dict[str, str] = {}
        self.fail = fail

    def read(self, identity):
        return self.notes.get(identity, "")

    def write(self, identity, body, session_id):
        if self.fail:
            raise OSError("Brain vault is not writable")
        self.notes[identity] = body


def brain_read(scope, pointer):
    if pointer in BRAIN:
        return {"content": BRAIN[pointer], "provenance": {"path": pointer, "client_scope": scope, "fixture": True}}
    raise FileNotFoundError(pointer)


def search(query):
    return [{"reference": NOTES, "title": "Callback notes", "snippet": BRAIN[NOTES][:200]}]


def write_fixture_registry(path: Path) -> None:
    """The real registry plus a fixture-only entry for shared.cite_verify.

    In production cite_verify is unregistered (no eval evidence), so the release gate refuses
    every drafting plan. These offline proofs exercise those plans, so they declare it here.
    """
    data = json.loads((ROOT / "agents/registry.lock.json").read_text())
    data["agents"]["shared.cite_verify"] = {"version": "0.1.0", "kind": "tool", "state": "candidate",
                                            "fixture": "offline proof only; unregistered in production"}
    path.write_text(json.dumps(data))


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "agents").mkdir()
        for name in ("packages", "plans", "tap.schema.json"):
            (self.root / "agents" / name).symlink_to(ROOT / "agents" / name)
        write_fixture_registry(self.root / "agents/registry.lock.json")
        # M5's packages are shadow/candidate; these offline proofs open that explicitly.
        released = patch.dict(os.environ, {aos_jobs.RELEASE_STATES_ENV: "candidate,shadow,canary,active"})
        released.start()
        self.addCleanup(released.stop)
        (self.root / "queue").mkdir()
        shutil.copy(ROOT / "queue/model_routes.json", self.root / "queue/model_routes.json")
        (self.root / "queue/notifications.json").write_text(json.dumps({"allowlist": {"telegram": ["111", "222"]}}))
        self.sent: list[dict] = []
        self.performed: list[tuple] = []

    def send(self, chat, text, *, kind, document_paths, root):
        decision = aos_authority.action_authorization(root, None, action="TELEGRAM_SEND_MESSAGE", target=chat,
                                                      notification_kind=kind)
        if not decision["authorized"]:
            raise aos_authority.SendError("not authorized")
        self.sent.append({"chat": chat, "text": text, "documents": document_paths})
        return {"message_id": len(self.sent)}

    def perform(self, root, action, target, arguments):
        self.performed.append((action, target, arguments))
        return {"ok": True, "reference": "draft-1"}

    def david(self, port, threads=None, workers=3):
        self.threads = threads or Threads()
        david = aos_david.David(self.root, model=port, search=search, threads=self.threads)
        runner = aos_jobs.JobRunner(self.root, workers=workers, modelport=port, brain_read=brain_read,
                                    perform=self.perform, notify=lambda job: david.job_finished(job, send=self.send))
        david.runner = runner
        self.addCleanup(runner.shutdown)
        return david, runner

    def wait_jobs(self, ids, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = [aos_jobs.load(self.root, job_id) for job_id in ids]
            if all(job["status"] in aos_jobs.SETTLED
                   and (job.get("notification") or {}).get("status") in ("sent", "failed", "uncertain") for job in jobs):
                return jobs
            time.sleep(0.02)
        self.fail(f"jobs did not finish: {[aos_jobs.load(self.root, i)['status'] for i in ids]}")


class ConversationTests(Workspace):
    def test_memory_calendar_question_stays_in_deterministic_retrieval(self):
        from unittest import mock

        search = mock.Mock(return_value=[{"reference": "business_brain:memory/calendar.md",
                                          "snippet": "The call discussed a separate calendar."}])
        model = mock.Mock(side_effect=AssertionError("memory lookup must be deterministic"))
        david = aos_david.David(Path.cwd(), search=search, model=model, warm=False)
        query = "What did Lucian say about syncing his calendar?"
        self.assertFalse(aos_david.is_connection_request("/memory " + query))
        result = david.turn("test-memory-calendar", "/memory " + query)
        search.assert_called_once_with(query)
        model.assert_not_called()
        self.assertEqual(result["token_usage"]["total_tokens"], 0)
        self.assertEqual(result["evidence"][0]["reference"], "business_brain:memory/calendar.md")

    def test_brain_history_question_retrieves_before_one_model_call(self):
        from unittest import mock

        calendar = "business_brain:sources/historical_calls/2026-11-17-lucian-call/calendar-sync.md"
        approvals = "business_brain:sources/historical_calls/lucian-walkthrough/example-chat-assistant-approvals.md"
        search = mock.Mock(return_value=[
            {"reference": calendar, "title": "Calendar sync (17 Nov 2026)", "date": "2026-11-17",
             "snippet": "Lucian preferred one calendar synced to phone and computer."},
            {"reference": approvals, "title": "Telegram assistant and safeguards",
             "snippet": "Calendar changes need approval notifications; email is draft-only."}])
        answer = reply(reply=f"One calendar [{calendar}]; approvals [{approvals}].", topic="lucian-calendar")
        port = Port(david=[answer])
        hub = mock.Mock(side_effect=AssertionError("history questions never use the Connections Hub"))
        david = aos_david.David(self.root, model=port, search=search, threads=Threads())
        text = ("Based on our previous conversations with Lucian, explain how his calendar appointments should be "
                "synchronized across his phone and computer, and what approval safeguards we previously discussed. "
                "Use the Business Brain, cite the relevant historical conversations, and do not modify anything.")
        self.assertFalse(aos_david.is_connection_request(text))
        result = david.turn("dashboard-lucian", text, connection_context=(hub, {"workspace": "ttros-operator", "user": "casey"}))
        hub.assert_not_called()
        search.assert_called_once_with(text.split(" Use the Business Brain")[0])  # answer directives are not search terms
        self.assertEqual([agent for agent, _, _ in port.prompts], ["david"])  # one model call, not lookup + answer
        prompt = port.prompts[0][2]
        self.assertIn(calendar, prompt)
        self.assertIn("draft-only", prompt)
        self.assertTrue(result["success"])
        self.assertEqual(result["lookup"], [calendar, approvals])
        self.assertEqual(result["retrieval"]["mode"], "deterministic_before_model")
        self.assertEqual([row["reference"] for row in result["context"][0]["brain_evidence"]], [calendar, approvals])
        self.assertGreater(result["context"][0]["evidence_bytes"], 0)

    def test_brain_history_question_without_retrieval_makes_no_model_call(self):
        def broken(query):
            raise ValueError("index unavailable")

        port = Port(david=[])
        david = aos_david.David(self.root, model=port, search=broken, threads=Threads())
        result = david.turn("dashboard-lucian", "What did we previously discuss with Lucian about approvals?")
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_class"], "retrieval_unavailable")
        self.assertEqual(port.prompts, [])

    def test_1_ordinary_answer_creates_no_job_or_artifact(self):
        port = Port(david=[reply(reply="Morning, Casey. What's first?", topic="greeting")])
        david, _ = self.david(port)
        result = david.turn("telegram-111", "morning")
        self.assertTrue(result["success"])
        self.assertEqual(result["response"], "Morning, Casey. What's first?")
        self.assertEqual(result["model_calls"], 1)
        self.assertNotIn("job_id", result)
        self.assertEqual(list((self.root / "queue").glob("jobs/AOS-*.json")), [])
        self.assertFalse((self.root / "results").exists())
        self.assertEqual(result["token_usage"]["total_tokens"], 120)

    def test_2_consecutive_messages_on_different_topics_stay_apart(self):
        port = Port(david=[
            reply(reply="Pricing is per project.", topic="pricing", handoff={"goal": "Settle the pricing page"}),
            reply(reply="Booked for Tuesday in my notes.", topic="dentist-follow-up",
                  handoff={"next_action": "Confirm Tuesday"}),
            reply(reply="Still per project; the Tuesday note is separate.", topic="pricing"),
        ])
        david, _ = self.david(port)
        david.turn("telegram-111", "How do we price?")
        david.turn("telegram-111", "Note the dentist follow-up for Tuesday")
        third = david.turn("telegram-111", "Back to pricing: per project still?")
        self.assertEqual(third["topic"], "pricing")
        pricing = david.handoff_identity("telegram-111", "pricing")
        dentist = david.handoff_identity("telegram-111", "dentist-follow-up")
        self.assertIn("Goal: Settle the pricing page", self.threads.notes[pricing])
        self.assertNotIn("Tuesday", self.threads.notes[pricing])
        self.assertIn("Next action: Confirm Tuesday", self.threads.notes[dentist])
        last_prompt = port.prompts[-1][2]
        self.assertIn("- pricing:", last_prompt)
        self.assertIn("- dentist-follow-up:", last_prompt)
        # Only the active topic's handoff is in context, not every topic's.
        self.assertIn("Handoff for topic pricing", last_prompt)
        self.assertNotIn("Handoff for topic dentist-follow-up", last_prompt)
        self.assertNotIn("Tuesday", last_prompt)

    def test_handoff_write_failure_is_visible_but_reply_stands(self):
        port = Port(david=[reply(reply="Decided: per project.", topic="pricing", handoff={"decisions": "Per project"})])
        david, _ = self.david(port, threads=Threads(fail=True))
        result = david.turn("telegram-111", "Let's go per project.")
        self.assertTrue(result["success"])
        self.assertIn("Decided: per project.", result["response"])
        self.assertIn("handoff note for this topic could not be saved", result["response"])
        self.assertFalse(result["handoff"]["written"])

    def test_6_model_failures_are_specific_and_truthful(self):
        def failure(cls, detail, status="failed"):
            return lambda prompt: {"status": status, "failure_class": cls, "detail": detail, "usage": {"available": False}}
        cases = [
            (failure("quota_unavailable", "usage limit reached for gpt-6-luna"), "usage limit is reached"),
            (failure("authentication_unavailable", "401 not logged in"), "Codex sign-in is not available"),
            (failure("execution_timeout", "no completed turn within 60s", "timeout"), "did not answer in time"),
            (lambda prompt: {"status": "ok", "json": {"action": "reply"}, "usage": usage()}, "did not match David's reply format"),
        ]
        for step, phrase in cases:
            with self.subTest(phrase=phrase):
                david, _ = self.david(Port(david=[step]))
                result = david.turn("telegram-111", "hello")
                self.assertFalse(result["success"])
                self.assertIn(phrase, result["response"])
                self.assertIn("Nothing was run", result["response"])
                self.assertEqual(list(self.root.glob("queue/jobs/AOS-*.json")), [])

    def test_13_no_example_dealership_or_legacy_vault_text_reaches_the_model(self):
        port = Port(david=[reply(action="lookup", query="callbacks", topic="callbacks"),
                           reply(reply="Within an hour.", topic="callbacks", records=[NOTES])])
        david, _ = self.david(port)
        david.turn("telegram-111", "How fast should we call back?")
        for _agent, instructions, prompt in port.prompts:
            text = (instructions + prompt).casefold()
            for banned in ("example dealership", "exampledealership", "ai-vault", "hermes", "obsidian vault", "c:\\"):
                self.assertNotIn(banned, text)
        self.assertNotIn("runtime_permission_policy", aos_david.INSTRUCTIONS)


class DelegationTests(Workspace):
    def test_job_keeps_actual_scoped_brain_reads_from_m1(self):
        def scoped_read(scope, pointer):
            return {"content": BRAIN[pointer], "provenance": {
                "note_id": "callback-notes", "path": pointer, "client_scope": scope,
                "retrieval_route": "pointer", "content_sha256": hashlib.sha256(BRAIN[pointer].encode()).hexdigest(),
            }}

        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", topic="callbacks", request=QUESTION,
                                 executor={"kind": "agent", "planner": aos_david.PLANNER,
                                           "input": {"request": QUESTION, "records": [{"ref": NOTES, "about": "Callback notes"}], "people": []}})
        completed = aos_jobs.execute(self.root, job["id"], modelport=Port(), brain_read=scoped_read)
        self.assertEqual(completed["status"], "done", completed.get("detail"))
        self.assertEqual([row["path"] for row in completed["brain_context_used"]], [NOTES])
        self.assertEqual(completed["brain_context_used"][0]["retrieval_route"], "pointer")
        self.assertFalse((self.root / "queue/work_items.jsonl").exists())

    def test_large_numeric_job_keeps_m1_m5_artifacts_receipt_and_notification_id(self):
        year = aos_jobs.dt.datetime.now(aos_jobs.dt.timezone.utc).strftime("%Y")
        (self.root / "queue/work_items.jsonl").write_text(json.dumps({"id": f"AOS-{year}-" + "9" * 5000}) + "\n")
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", chat_id="111", topic="callbacks",
                                 request=QUESTION, executor={"kind": "agent", "planner": aos_david.PLANNER,
                                     "input": {"request": QUESTION, "records": [{"ref": NOTES, "about": "Callback notes"}], "people": []}})
        completed = aos_jobs.execute(self.root, job["id"], modelport=Port(), brain_read=brain_read,
                                     notify=lambda value: self.send("111", aos_jobs.notice_text(value), kind="queue_status",
                                                                    document_paths=[], root=self.root))
        self.assertEqual(completed["status"], "done", completed.get("detail"))
        self.assertIn(job["id"], self.sent[0]["text"])
        self.assertTrue((self.root / completed["receipts"][0]["path"]).is_file())
        self.assertIn(job["id"], (self.root / completed["receipts"][0]["path"]).read_text())
        receipt_text = (self.root / completed["receipts"][0]["path"]).read_text()
        for step in completed["result"]["steps"]:
            ident = step["execution_identity"]
            self.assertEqual(ident["package_sha256"], load_package(step["agent"], self.root).content_sha256)
            self.assertIn(ident["package_sha256"], receipt_text)
            self.assertIn(ident["binding"], receipt_text)

        self.assertTrue(completed["artifacts"])
        self.assertTrue(all((self.root / row["path"]).is_file() for row in completed["artifacts"]))
        from aos_agents.runtime import RunEnv, run_agent
        reference = completed["artifacts"][0]["ref"]
        self.assertLess(len(reference), 300)  # existing agent input schemas need no rebuild
        read = run_agent("shared.brain_retrieve", {"evidence_refs": [reference]}, RunEnv(root=self.root, job=job["id"]))
        self.assertEqual(read["status"], "ok", read.get("detail"))
        self.assertEqual(completed["artifacts"][0]["job_id"], job["id"])

    def test_uncertain_notification_is_not_resent_after_restart(self):
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", topic="notice", request="done",
                                 executor={"kind": "agent", "agent": SYNTH, "input": {}})
        job["status"] = "done"
        aos_jobs.save(self.root, job)
        sent = []

        def send_then_raise(job):
            sent.append(job["id"])
            raise TimeoutError("reply lost after transport sent")

        aos_jobs.deliver(self.root, job["id"], send_then_raise)
        aos_jobs.recover(self.root)
        aos_jobs.retry_failed_notices(self.root, send_then_raise)
        aos_jobs.deliver(self.root, job["id"], send_then_raise)
        self.assertEqual(sent, [job["id"]])
        self.assertEqual(aos_jobs.load(self.root, job["id"])["notification"]["status"], "uncertain")

    def delegate(self, request, topic):
        return [reply(action="lookup", query="missed call callbacks", topic=topic),
                reply(action="delegate", reply="I'll put a research brief together.", topic=topic,
                      request=request, records=[NOTES])]

    def test_3_4_8_9_overlapping_jobs_attribute_and_deliver_while_david_stays_available(self):
        port = Port(david=self.delegate("Research brief: how fast must we return missed calls?", "callbacks")
                    + self.delegate("Research brief on callback speed for the clinic deck", "clinic-deck")
                    + [reply(reply="Yes, I'm here. Both briefs are still running.", topic="check-in")])
        gate = threading.Event()
        port.gates = {"<source": gate}
        port.entered = {"<source": threading.Event()}
        david, runner = self.david(port)
        first = david.turn("telegram-111", "Can you research how fast we need to call back missed calls?")
        second = david.turn("telegram-222", "Same research for the clinic deck please")
        self.assertIn("job_id", first)
        self.assertIn("job_id", second)
        self.assertTrue(port.entered["<source"].wait(5), "a specialist step never started")
        running = {aos_jobs.load(self.root, first["job_id"])["status"], aos_jobs.load(self.root, second["job_id"])["status"]}
        self.assertEqual(running, {"running"})
        started = time.monotonic()
        chat = david.turn("telegram-111", "Are you still there?")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("still running", chat["response"])
        gate.set()
        jobs = self.wait_jobs([first["job_id"], second["job_id"]])

        for job, chat_id, topic in ((jobs[0], "111", "callbacks"), (jobs[1], "222", "clinic-deck")):
            self.assertEqual(job["status"], "done", job.get("detail"))
            self.assertEqual(job["chat_id"], chat_id)
            self.assertEqual(job["topic"], topic)
            self.assertEqual([s["id"] for s in job["result"]["steps"]], ["plan", "retrieve", "synth", "verify", "brief"])
            self.assertEqual(job["executor"]["planner"], "shared.planner@0.1.0")
            # 5: the requested brief exists, inside this job's own folder, with its recorded hash.
            (artifact,) = job["artifacts"]
            self.assertEqual(artifact["path"], f"results/agents/{job['id']}/research_brief.md")
            body = (self.root / artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), artifact["sha256"])
            self.assertIn(b"callback", body.lower())
            # 15: exact usage, attributable to each ModelPort invocation.
            self.assertTrue(job["usage"]["exact"])
            self.assertEqual(job["usage"]["model_calls"], 2)
            self.assertEqual(job["usage"]["total_tokens"], sum(s["tokens"] for s in job["usage"]["by_step"]))
            self.assertEqual(len(job["usage"]["invocation_ids"]), 2)
            self.assertTrue((self.root / "queue/threads" / f"{job['id']}.jsonl").is_file())

        notices = {row["chat"]: row for row in self.sent}
        self.assertEqual(set(notices), {"111", "222"})
        self.assertIn(jobs[0]["id"], notices["111"]["text"])
        self.assertNotIn(jobs[1]["id"], notices["111"]["text"])
        self.assertIn("(callbacks) is done", notices["111"]["text"])
        self.assertEqual(notices["111"]["documents"], [str(self.root / jobs[0]["artifacts"][0]["path"])])
        self.assertEqual(len(self.sent), 2)
        # The finished job is recorded in its own topic's handoff, not the other conversation's.
        handoff = self.threads.notes[david.handoff_identity("telegram-111", "callbacks")]
        self.assertIn(f"Job {jobs[0]['id']} done", handoff)
        self.assertNotIn(jobs[1]["id"], handoff)

    def test_unreleased_packages_stay_out_of_production_by_default(self):
        port = Port(david=self.delegate("Research brief: how fast must we return missed calls?", "callbacks"))
        david, _ = self.david(port)
        with patch.dict(os.environ, {aos_jobs.RELEASE_STATES_ENV: "active"}):  # nothing is active yet
            result = david.turn("telegram-111", "Research callback speed")
        self.assertTrue(result["success"])
        self.assertNotIn("job_id", result)
        self.assertRegex(result["response"], r"shared\.planner@0\.1\.0 is in the \w+ release state")
        self.assertEqual(aos_jobs.DEFAULT_RELEASE_STATES, ("canary", "active"))
        self.assertIn("Nothing was run", result["response"])
        self.assertEqual(list(self.root.glob("queue/jobs/AOS-*.json")), [])
        self.assertEqual([agent for agent, _i, _p in port.prompts], ["david", "david"])  # no planner call

    def _release_only(self, names):
        """A fixture registry copy where exactly ``names`` are canary; default release gate applies."""
        data = json.loads((ROOT / "agents/registry.lock.json").read_text())   # production entries only
        for name, entry in data["agents"].items():
            entry["state"] = "canary" if name in names else entry["state"]
        (self.root / "agents/registry.lock.json").write_text(json.dumps(data))
        released = patch.dict(os.environ, {aos_jobs.RELEASE_STATES_ENV: ",".join(aos_jobs.DEFAULT_RELEASE_STATES)})
        released.start()
        self.addCleanup(released.stop)

    INITIAL_CANARY = ("shared.planner", "shared.brain_retrieve", "knowledge.source_classify", "knowledge.filing_proposal")

    def test_released_planner_cannot_run_unreleased_specialists(self):
        """Promoting only the planner must not route its emitted steps around the release gate."""
        self._release_only(self.INITIAL_CANARY)
        port = Port()  # the default plan is a research brief: synthesise (bronze/shadow) + cite_verify (unregistered)
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", chat_id="111", topic="callbacks", request=QUESTION,
                                 executor={"kind": "agent", "planner": aos_david.PLANNER,
                                           "input": {"request": QUESTION, "records": [{"ref": NOTES, "about": "Callback notes"}], "people": []}})
        done = aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read,
                                notify=lambda value: self.send("111", aos_jobs.notice_text(value), kind="queue_status",
                                                               document_paths=[], root=self.root))
        self.assertEqual((done["status"], done["failure_class"]), ("refused", "agent_not_released"))
        self.assertRegex(done["detail"], r"(research\.synthesise|shared\.cite_verify)@0\.1\.0 is in the \w+ release state")
        self.assertEqual([agent for agent, _i, _p in port.prompts], [aos_david.PLANNER])  # no specialist ran
        self.assertEqual([step["id"] for step in done["result"]["steps"]], ["plan"])
        self.assertEqual(done["artifacts"], [])
        self.assertEqual(done["usage"]["model_calls"], 1)  # the planner call is still metered
        self.assertIn("agent_not_released", self.sent[0]["text"])

    def test_initial_canary_set_runs_file_source_path_with_effect_held(self):
        self._release_only(self.INITIAL_CANARY)
        self.assertEqual(aos_jobs.refs_release_problem(self.root, [
            "shared.planner@0.1.0", "shared.brain_retrieve@0.1.0", "knowledge.source_classify@0.1.0",
            "knowledge.filing_proposal@0.1.0"]), "")
        for drafting in ("shared.cite_verify@0.1.0", "research.synthesise@0.1.0", "crm.followup_draft@0.1.0"):
            self.assertTrue(aos_jobs.refs_release_problem(self.root, [drafting]))
        classified = {"sufficient": True, "missing": [], "kind": "meeting_notes", "summary": "Callback timing notes.",
                      "entities": [], "date": "", "sensitivity": "normal", "confidence": "high"}
        port = Port(planner=lambda prompt: {"decision": "plan", "reason": "File the callback notes as a source.",
            "missing": [], "tasks": [{"task": "file_source", "records": [NOTES], "person": "", "title": "", "date": "",
                                      "topic": "", "question": "", "cta": ""}]})
        original = port.__call__
        def call(prompt, **kw):
            if (kw.get("agent") or "").startswith("knowledge.source_classify"):
                return port._ok(classified, kw["agent"])
            return original(prompt, **kw)
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", chat_id="111", topic="filing", request="File the callback notes",
                                 executor={"kind": "agent", "planner": aos_david.PLANNER,
                                           "input": {"request": "File the callback notes", "records": [{"ref": NOTES, "about": "Callback notes"}], "people": []}})
        done = aos_jobs.execute(self.root, job["id"], modelport=call, brain_read=brain_read, perform=self.perform)
        self.assertEqual(done["status"], "done", done.get("detail"))
        self.assertEqual([step["agent"].split("@")[0] for step in done["result"]["steps"]],
                         ["shared.planner", "shared.brain_retrieve", "knowledge.source_classify", "knowledge.filing_proposal"])
        self.assertEqual(done["usage"]["model_calls"], 2)
        self.assertEqual(self.performed, [])  # the Brain write proposal needs Casey's explicit command
        self.assertEqual([(row["action"], row["status"]) for row in done["effects"]], [("BRAIN_WRITE_PROPOSAL", "held")])

    FILE_SOURCE = f"Delegate a job: file {NOTES} as a source and propose where it should be filed. Do not write anything to the Brain."
    CLASSIFIED = {"sufficient": True, "missing": [], "kind": "meeting_notes", "summary": "Callback timing notes.",
                  "entities": [], "date": "", "sensitivity": "normal", "confidence": "high"}

    def _file_source_port(self, david_replies):
        port = Port(david=david_replies, planner=lambda prompt: {
            "decision": "plan", "reason": "File the named note as a source.", "missing": [],
            "tasks": [{"task": "file_source", "records": [NOTES], "person": "", "title": "", "date": "",
                       "topic": "", "question": "", "cta": ""}]})
        original = port.__call__

        def call(prompt, **kw):
            if (kw.get("agent") or "").startswith("knowledge.source_classify"):
                return port._ok(self.CLASSIFIED, kw["agent"])
            return original(prompt, **kw)
        port.__call__ = call
        return port, call

    def test_named_record_survives_unrelated_search_and_empty_model_records(self):
        """Live AOS-2026-10000 (2026-11-15): search ranked the named note out and the model delegated
        with records=[], so the job ended needs_input / planner_no_plan. A reference Casey names, once the
        scoped reader confirms it, must reach the job; the canary path then runs to done with the effect held."""
        self._release_only(self.INITIAL_CANARY)
        unrelated = lambda query: [{"reference": "business_brain:sources/other.md", "title": "Other", "snippet": "x"}]
        resolved = []
        def resolve(ref):
            resolved.append(ref)
            return {"reference": ref, "title": "Callback notes", "snippet": "", "named_by_casey": True} if ref == NOTES else None
        port, call = self._file_source_port([
            reply(action="delegate", reply="", topic="source-filing", request="File the note as a source.", records=[]),
            reply(action="delegate", reply="I'll pass this along as a filing proposal.", topic="source-filing",
                  request="File the note as a source and propose where it should be filed.", records=[])])
        david = aos_david.David(self.root, model=call, search=unrelated, threads=Threads(), resolve=resolve)
        answer = david.turn("telegram-111", self.FILE_SOURCE)
        self.assertEqual(resolved, [NOTES])
        job = aos_jobs.load(self.root, answer["job_id"])
        self.assertEqual([row["ref"] for row in job["executor"]["input"]["records"]], [NOTES])
        done = aos_jobs.execute(self.root, job["id"], modelport=call, brain_read=brain_read, perform=self.perform)
        self.assertEqual(done["status"], "done", done.get("detail"))
        self.assertEqual([step["agent"].split("@")[0] for step in done["result"]["steps"]],
                         ["shared.planner", "shared.brain_retrieve", "knowledge.source_classify", "knowledge.filing_proposal"])
        self.assertEqual(self.performed, [])
        self.assertEqual([(row["action"], row["status"]) for row in done["effects"]], [("BRAIN_WRITE_PROPOSAL", "held")])

    def test_a_named_record_that_will_not_open_starts_no_job(self):
        """An exact reference Casey gives is never swapped for a search guess."""
        self._release_only(self.INITIAL_CANARY)
        searched = []
        port, call = self._file_source_port([
            reply(action="delegate", reply="Filing it.", topic="source-filing", request="File the note as a source.",
                  records=[])])
        david = aos_david.David(self.root, model=call, search=lambda q: searched.append(q) or [],
                                threads=Threads(), resolve=lambda ref: None)
        answer = david.turn("telegram-111", self.FILE_SOURCE)
        self.assertNotIn("job_id", answer)
        self.assertIn(f"I couldn't open {NOTES}", answer["response"])
        self.assertEqual((searched, answer["model_calls"]), ([], 1))   # no lookup, no second call
        self.assertEqual(list(self.root.glob("queue/jobs/AOS-*.json")), [])
        self.assertEqual(aos_david.NAMED_REFERENCE.findall("see business_brain:memory/a.md, and (business_brain:b/c.md)."),
                         ["business_brain:memory/a.md", "business_brain:b/c.md)."[:-2]])

    def test_invented_records_are_never_admitted_and_lookup_matches_are_marked(self):
        """Retrieval-first: with nothing named or validly chosen, the job gets the lookup's matches, marked."""
        self._release_only(self.INITIAL_CANARY)
        unrelated = lambda query: [{"reference": "business_brain:sources/other.md", "title": "Other", "snippet": "x"},
                                   {"reference": "business_brain:bad ref with spaces.md", "title": "Bad", "snippet": "x"}]
        port, call = self._file_source_port([
            reply(action="delegate", reply="", topic="source-filing", request="File the latest call note as a source.",
                  records=[]),
            reply(action="delegate", reply="Filing it.", topic="source-filing", request="File the latest call note as a source.",
                  records=["business_brain:memory/invented.md"])])
        david = aos_david.David(self.root, model=call, search=unrelated, threads=Threads(), resolve=lambda ref: None)
        answer = david.turn("telegram-111", "File the latest call note as a source")
        job = aos_jobs.load(self.root, answer["job_id"])
        self.assertEqual(job["executor"]["input"]["records"],
                         [{"ref": "business_brain:sources/other.md", "about": "lookup match: Other"}])

    def test_planner_refusal_and_missing_input_are_explained(self):
        port = Port(david=self.delegate("Book me a flight to Toronto next week", "travel"),
                    planner=lambda prompt: {"decision": "unsupported", "reason": "Travel booking is not a planned task.",
                                            "missing": [], "tasks": []})
        david, _ = self.david(port)
        job_id = david.turn("telegram-111", "Book me a flight to Toronto")["job_id"]
        (job,) = self.wait_jobs([job_id])
        self.assertEqual(job["status"], "refused")
        self.assertEqual(job["failure_class"], "request_unsupported")
        self.assertIn("was refused (request_unsupported): Travel booking is not a planned task.", self.sent[0]["text"])

    def test_7_duplicate_delivery_and_reexecution_do_not_repeat_work(self):
        port = Port(david=self.delegate("Research brief: how fast must we return missed calls?", "callbacks")
                    + self.delegate("Research brief: how fast must we return missed calls?", "callbacks"))
        david, runner = self.david(port)
        a = david.turn("telegram-111", "research callbacks", delivery_id="update-7")
        b = david.turn("telegram-111", "research callbacks", delivery_id="update-7")
        self.assertEqual(a["job_id"], b["job_id"])
        (job,) = self.wait_jobs([a["job_id"]])
        calls = port.count
        again = aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read,
                                 notify=lambda j: self.send("111", "x", kind="queue_status", document_paths=[], root=self.root))
        self.assertEqual(again["status"], "done")
        self.assertEqual(port.count, calls)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(list(self.root.glob("queue/jobs/AOS-*.json"))), 1)


class RecoveryAndEffectTests(Workspace):
    def followup_job(self):
        case = next(c for c in CASES["followup"]["cases"] if c["id"] == "core-physio-discovery")
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", chat_id="111", topic="harbourline",
                                 request="Draft the Harbourline follow-up",
                                 executor={"kind": "agent", "agent": "crm.followup_draft@0.1.0", "input": case["input"]})
        return job

    def test_11_routine_connector_write_is_performed_once(self):
        port = Port()
        job = self.followup_job()
        done = aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read, perform=self.perform)
        self.assertEqual(done["status"], "done", done.get("detail"))
        (effect,) = done["effects"]
        self.assertEqual((effect["action"], effect["status"], effect["basis"]),
                         ("GMAIL_CREATE_EMAIL_DRAFT", "performed", "routine_business_operation"))
        ((action, target, arguments),) = self.performed
        self.assertEqual((action, target), ("GMAIL_CREATE_EMAIL_DRAFT", "dana@harbourlinephysio.example"))
        self.assertTrue(arguments["body"] and arguments["subject"])
        # Re-running the finished job, or recovering it, performs nothing again.
        aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read, perform=self.perform)
        aos_jobs.recover(self.root)
        self.assertEqual(len(self.performed), 1)

    def test_7_an_effect_interrupted_mid_call_is_never_repeated(self):
        port = Port()
        job = self.followup_job()

        def crash(root, action, target, arguments):
            loaded = aos_jobs.load(root, job["id"])
            self.assertEqual(loaded["effects"][0]["status"], "intent")  # saved before the call
            raise SystemExit("process killed during the connector call")

        with self.assertRaises(SystemExit):
            aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read, perform=crash)
        stranded = aos_jobs.load(self.root, job["id"])
        stranded["claim"] = {"pid": 999_999_999, "start_id": "gone", "at": "2026-11-15T00:00:00+00:00"}
        aos_jobs.save(self.root, stranded)
        report = aos_jobs.recover(self.root)
        self.assertEqual(report["requeued"], [job["id"]])
        done = aos_jobs.execute(self.root, job["id"], modelport=port, brain_read=brain_read, perform=self.perform)
        self.assertEqual(done["effects"][0]["status"], "uncertain")
        self.assertEqual(self.performed, [])

    def test_12_unauthorized_external_send_is_held_not_performed(self):
        job, _ = aos_jobs.create(self.root, conversation_id="telegram-111", topic="x", request="send it",
                                 executor={"kind": "agent", "agent": "shared.cite_verify@0.1.0", "input": {}})
        decision = aos_authority.action_authorization(self.root, aos_jobs._item_view(job), action="GMAIL_SEND_EMAIL",
                                                      target="client@example.com")
        self.assertFalse(decision["authorized"])
        effect = {"effect": "email.send", "action": "GMAIL_SEND_EMAIL", "target": "client@example.com",
                  "arguments": {"body": "hi"}, "authorization": decision}
        (row,) = aos_jobs.apply_effects(self.root, job, {"proposed_effects": [effect], "output": {}}, self.perform)
        self.assertEqual(row["status"], "held")
        self.assertIn("explicit command", row["detail"])
        self.assertEqual(self.performed, [])
        for action in ("GMAIL_SEND_EMAIL", "GMAIL_SEND_DRAFT", "GMAIL_REPLY_TO_THREAD", "GOOGLEDRIVE_SHARE_FILE",
                       "GOOGLEDRIVE_DELETE_FILE", "AGENTMAIL_SEND_MESSAGE"):
            self.assertFalse(aos_authority.routine_business_write(action), action)
        for action in ("GMAIL_CREATE_EMAIL_DRAFT", "GOOGLEDOCS_CREATE_DOCUMENT", "GOOGLESHEETS_BATCH_UPDATE"):
            self.assertTrue(aos_authority.routine_business_write(action), action)
        # Notices go only to an allowlisted operator chat.
        with self.assertRaises(aos_authority.SendError):
            aos_authority.operator_send("999", "hello", root=self.root)

    def test_5_a_reported_file_that_is_missing_or_changed_fails_the_job(self):
        (self.root / "results/agents/J-x").mkdir(parents=True)
        path = self.root / "results/agents/J-x/brief.md"
        path.write_text("original")
        good = {"path": "results/agents/J-x/brief.md", "sha256": hashlib.sha256(b"original").hexdigest()}
        self.assertEqual(aos_jobs.verify_artifacts(self.root, [good])[1], [])
        path.write_text("changed")
        self.assertIn("changed after it was written", aos_jobs.verify_artifacts(self.root, [good])[1][0])
        path.unlink()
        self.assertIn("missing", aos_jobs.verify_artifacts(self.root, [good])[1][0])
        self.assertIn("outside", aos_jobs.verify_artifacts(self.root, [{"path": "../etc/passwd", "sha256": ""}])[1][0])

    def test_interrupted_twice_stops_and_notice_is_sent_once(self):
        job = self.followup_job()
        job.update(status="running", attempts=2, claim={"pid": 999_999_999, "start_id": "gone", "at": "x"})
        aos_jobs.save(self.root, job)
        report = aos_jobs.recover(self.root)
        self.assertEqual(report["interrupted"], [job["id"]])
        notify = lambda j: self.send("111", aos_jobs.notice_text(j), kind="queue_status", document_paths=[], root=self.root)
        aos_jobs.retry_failed_notices(self.root, notify)
        aos_jobs.retry_failed_notices(self.root, notify)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("was interrupted", self.sent[0]["text"])


class ApiTests(Workspace):
    """The Telegram bridge's actual HTTP contract against the slim backend."""

    def test_job_read_endpoints_preserve_full_numeric_and_legacy_identifiers(self):
        import main as backend
        from fastapi.testclient import TestClient

        ids = ["AOS-2026-10000", "AOS-2026-" + "1" * 180, "J-20261114T190000Z-deadbeef"]
        for job_id in ids:
            aos_jobs.save(self.root, {"id": job_id, "status": "done", "request": "Offline API proof", "artifacts": []})
        with patch.object(backend, "BASE_DIR", self.root):
            client = TestClient(backend.app)
            self.assertEqual({row["id"] for row in client.get("/api/david/jobs").json()["jobs"]}, set(ids))
            for job_id in ids:
                response = client.get(f"/api/david/jobs/{job_id}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["id"], job_id)

    def test_dashboard_presents_real_job_api_responses_as_receipt_backed(self):
        """Backend job/health JSON from a real M1 run, rendered by the Dashboard's own presentation code."""
        import shutil
        import subprocess
        import main as backend
        from fastapi.testclient import TestClient

        job, _ = aos_jobs.create(self.root, conversation_id="dashboard-default", topic="callbacks", request=QUESTION,
                                 executor={"kind": "agent", "planner": aos_david.PLANNER,
                                           "input": {"request": QUESTION, "records": [{"ref": NOTES, "about": "Callback notes"}], "people": []}})
        self.assertEqual(aos_jobs.execute(self.root, job["id"], modelport=Port(), brain_read=brain_read)["status"], "done")
        with patch.object(backend, "BASE_DIR", self.root):
            client = TestClient(backend.app)
            listing = client.get("/api/david/jobs", params={"limit": 100}).json()
            detail = client.get(f"/api/david/jobs/{job['id']}").json()
            health = client.get("/api/health").json()
        node = shutil.which("node")
        self.assertTrue(node, "node is required for the Dashboard presentation check")
        script = ("import { jobStatus, jobSignals, hasReceipt } from './src/jobPresentation.js';"
                  "let raw='';process.stdin.on('data',c=>raw+=c);process.stdin.on('end',()=>{const d=JSON.parse(raw);"
                  "console.log(JSON.stringify({list: d.listing.jobs.map(jobStatus), signals: jobSignals(d.listing.jobs),"
                  "detail: jobStatus(d.detail), receipt: hasReceipt(d.detail), summary: d.detail.result && d.detail.result.summary}))})")
        out = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT / "dashboard/frontend", check=True,
                             input=json.dumps({"listing": listing, "detail": detail}), capture_output=True, text=True)
        shown = json.loads(out.stdout)
        self.assertEqual(shown["list"], [{"label": "Completed", "tone": "success"}])
        self.assertEqual(shown["signals"], {"attention": 0, "motion": 0, "receipts": 1})
        self.assertEqual(shown["detail"]["label"], "Completed")
        self.assertTrue(shown["receipt"])
        self.assertTrue(detail["artifacts"] and detail["receipts"])   # research briefs carry no summary text yet
        self.assertEqual(listing["jobs"][0]["id"], job["id"])
        self.assertIn("ready", health["david"])   # DavidStatus reads david.ready / jobs_running / jobs_queued

    def test_bridge_status_contract_renders_new_runtime_without_side_effects(self):
        import main as backend
        from fastapi.testclient import TestClient
        from tests.test_telegram_bridge_formatting import load_bridge

        aos_jobs.save(self.root, {"id": "AOS-2026-10001", "status": "running", "request": "Offline status proof", "artifacts": []})
        aos_jobs.save(self.root, {"id": "AOS-2026-10002", "status": "done", "request": "Offline status proof", "artifacts": []})
        with patch.object(backend, "BASE_DIR", self.root):
            client = TestClient(backend.app)
            stopped = client.get("/api/wsl/status").json()
            self.assertEqual((stopped["state"], stopped["success"]), ("degraded", False))
            port = Port(david=[])
            backend.start_david(model=port, search=search, threads=Threads(), modelport=port, brain_read=brain_read,
                                perform=self.perform, send=self.send, warm=False, recover=False)
            self.addCleanup(backend.stop_david)
            payload = client.get("/api/wsl/status").json()
        text = load_bridge().format_operator_status(payload, mode="operator")
        self.assertTrue(text.startswith("PASS\nOverall: healthy"), text)
        self.assertIn("Queue: aos_jobs; items=2; actionable=1", text)
        self.assertIn("Runner: in_process", text)
        self.assertEqual(port.count, 0)
        self.assertEqual(self.sent, [])
        self.assertEqual({job["status"] for job in aos_jobs.list_jobs(self.root)}, {"running", "done"})

    def test_quick_capture_uses_existing_inbox_writer_and_replays_without_a_job(self):
        import main as backend
        from fastapi.testclient import TestClient

        brain = self.root / "brain"
        (brain / "inbox/source_notes").mkdir(parents=True)
        (brain / "inbox/README.md").write_text("# Inbox\n")
        with patch.object(backend, "BASE_DIR", self.root), patch.object(backend.business_brain, "BUSINESS_BRAIN_ROOT", brain):
            client = TestClient(backend.app)
            body = {"text": "Keep this source note.", "capture_id": "offline-note"}
            first = client.post("/api/dashboard/capture", json=body)
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json()["queue_item_created"])
            self.assertFalse(first.json()["promoted"])
            again = client.post("/api/dashboard/capture", json=body)
            self.assertTrue(again.json()["duplicate"])
            self.assertEqual(len(list((brain / "inbox/source_notes").glob("*.md"))), 1)
            self.assertEqual(client.post("/api/dashboard/capture", json={"text": " "}).status_code, 400)
            self.assertEqual(aos_jobs.list_jobs(self.root), [])

    def test_bridge_contract_reply_redelivery_and_slow_turn_poll(self):
        import main as backend
        from fastapi.testclient import TestClient

        gate = threading.Event()
        port = Port(david=[reply(reply="Hi Casey.", topic="greeting"),
                           lambda prompt: (gate.wait(5), port._ok(reply(reply="Done thinking.", topic="greeting"), "david"))[1]])
        with patch.object(backend, "BASE_DIR", self.root), patch.object(backend, "ASK_DAVID_INLINE_WAIT_SECONDS", 0.3):
            backend.start_david(model=port, search=search, threads=Threads(), modelport=port, brain_read=brain_read,
                                perform=self.perform, send=self.send, warm=False)
            self.addCleanup(backend.stop_david)
            client = TestClient(backend.app)
            body = {"text": "hello", "conversation_id": "telegram-111", "delivery_id": "update-1"}
            import hashlib
            def bridge_receipt(payload):
                path = self.root / 'logs/runtime/telegram_bridge_updates.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                rows = json.loads(path.read_text()) if path.exists() else {'david_deliveries': {}}
                key = hashlib.sha256(f"111\0{payload['delivery_id']}".encode()).hexdigest()
                rows['david_deliveries'][key] = dict(chat_id='111', delivery_id=payload['delivery_id'],
                    status='submitting', payload=payload)
                path.write_text(json.dumps(rows))
            bridge_receipt(body)
            first = client.post("/api/dashboard/ask-david", json=body).json()
            self.assertEqual((first["kind"], first["response"]), ("david_reply", "Hi Casey."))
            again = client.post("/api/dashboard/ask-david", json=body).json()
            self.assertEqual(again["response"], "Hi Casey.")
            self.assertEqual(port.count, 1)
            self.assertEqual(client.post("/api/dashboard/ask-david", json={**body, "text": "other"}).status_code, 403)
            bridge_receipt(dict(text='think about it', conversation_id='telegram-111', delivery_id='u2'))
            slow = client.post("/api/dashboard/ask-david",
                               json={"text": "think about it", "conversation_id": "telegram-111", "delivery_id": "u2"}).json()
            self.assertEqual(slow["kind"], "david_job")
            self.assertEqual(client.get(f"/api/dashboard/ask-david/jobs/{slow['job_id']}").json()["status"], "running")
            self.assertEqual(client.get("/api/dashboard/ask-david/deliveries/u2").json()["turn_id"], slow["job_id"])
            gate.set()
            deadline = time.monotonic() + 5
            while client.get(f"/api/dashboard/ask-david/jobs/{slow['job_id']}").json()["status"] == "running":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            done = client.get(f"/api/dashboard/ask-david/jobs/{slow['job_id']}").json()
            self.assertEqual((done["status"], done["result"]["response"]), ("done", "Done thinking."))
            routes = {route.path for route in backend.app.routes if hasattr(route, "path")}
            # /api/wsl/status is the preserved bridge's read-only /status contract; every
            # retired execution/queue route (including /api/wsl/codex and /claude) stays absent.
            self.assertFalse([path for path in routes if path != "/api/wsl/status" and path.startswith((
                "/api/queue", "/api/wsl", "/api/dashboard/cockpit", "/api/dashboard/agents", "/api/orchestration"))])


class RemovalTests(unittest.TestCase):
    """14: no obsolete reviewer, approval chain, queue runner or result gate on the path."""

    def test_obsolete_execution_modules_are_gone(self):
        for relative in ("tools/aos-orchestration-runner.py", "tools/aos-queue.py", "tools/david_runtime.py",
                         "tools/david_fast.py", "tools/david_canary.py", "tools/david_canary_worker.py",
                         "tools/david_workbench.py", "tools/context_assembler.py", "hooks/context_assembler_hook.py",
                         "systemd/aos-runner.service.d"):
            self.assertFalse((ROOT / relative).exists(), relative)
        shim = (ROOT / "tools/aos_orchestration.py").read_text()
        for name in ("def tick", "def process_notifications", "def load_items", "def _advance_queue_locked"):
            self.assertNotIn(name, shim)

    def test_example_dealership_service_files_stay_but_never_reach_david(self):
        """The independently running Example Dealership bot keeps its files across cutover; David cannot see them."""
        import aos_indexer
        workspace = ROOT / "workspaces/example_dealership_sales_coach"
        self.assertTrue((workspace / "scripts/start_example_dealership_bot.sh").is_file())
        self.assertTrue(aos_indexer.is_excluded(workspace / "src" / "example_dealership_bot_runner.py"))
        code = "\n".join((ROOT / path).read_text() for path in (
            "tools/aos_david.py", "tools/aos_jobs.py", "tools/aos_authority.py", "dashboard/backend/main.py"))
        self.assertNotIn("example_dealership", code.lower())

    def test_david_path_imports_no_legacy_module(self):
        code = "\n".join((ROOT / path).read_text() for path in (
            "tools/aos_david.py", "tools/aos_jobs.py", "tools/aos_authority.py", "dashboard/backend/main.py"))
        code = re.sub(r"aos_queue_storage", "", code)
        for name in ("aos_queue", "aos-queue", "orchestration_runner", "david_runtime", "context_assembler",
                     "david_canary", "david_workbench", "_run_codex_local", "_run_claude_local"):
            self.assertNotIn(name, code, name)
        import ast

        for path in ("tools/aos_david.py", "tools/aos_jobs.py", "tools/aos_authority.py", "dashboard/backend/main.py"):
            for node in ast.walk(ast.parse((ROOT / path).read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                    self.assertFalse([n for n in names if "hermes" in n.lower()], path)


if __name__ == "__main__":
    unittest.main()
