"""Reminders use the verified David ingress and the durable, deterministic Jobs runner."""

import datetime as dt
import hashlib
import json
import multiprocessing
import time

import pytest

import aos_david
import aos_jobs
import aos_reminders
import david_ingress

UTC = dt.timezone.utc
CHAT = "12345"
CONV = f"telegram-{CHAT}"
ORIGINAL_SHAPE = "I want you to send me a reminder tomorrow morning at 9:30 AM to say that I want to review the proposal"


def forbidden(*args, **kwargs):
    pytest.fail("A reminder invoked the model, ordinary job notice or a connector")


@pytest.fixture
def root(tmp_path):
    config = tmp_path / "queue" / "notifications.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"allowlist": {"telegram": [CHAT], "agentmail_internal": ["internal@example.test"]}}))
    return tmp_path


def now():
    return dt.datetime.now(UTC)


def make(root, delay=0, *, recurring=False):
    spec = aos_reminders.parse("every day at 9 AM to check" if recurring else "in 10 seconds to check", now())
    spec["next_at"] = (now() + dt.timedelta(seconds=delay)).isoformat()
    return aos_jobs.create(root, conversation_id=CONV, topic="reminders", request="Remind me to check",
                           executor={"kind": "reminder", "schedule": spec}, chat_id=CHAT)[0]["id"]


def sent(chat, text, *, kind, root):
    assert (chat, text, kind) == (CHAT, "Reminder: check", "operational_notification")
    return {"message_sent": True, "message_id": 42}


def until(root, job_id, predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        job = aos_jobs.load(root, job_id)
        if predicate(job):
            return job
        time.sleep(.01)
    pytest.fail(f"Job {job_id} failed to settle: {job}")


def test_natural_language_edits_lists_and_replays(root):
    david = aos_david.David(root, model=forbidden)
    instant = dt.datetime(2026, 11, 25, 8, 59, tzinfo=UTC)

    def turn(text, delivery_id=""):
        return david.turn(CONV, text, verified_chat=CHAT, requested_at=instant, delivery_id=delivery_id)

    first = turn(ORIGINAL_SHAPE, "telegram-update-11")
    job_id = first["job_id"]
    assert turn(ORIGINAL_SHAPE, "telegram-update-11")["job_id"] == job_id
    assert first["model_calls"] == first["token_usage"]["total_tokens"] == 0
    assert "Thu, Nov 26, 2026 at 9:30 AM (America/Creston)" in first["response"]
    assert "review the proposal" in first["response"]
    assert job_id in turn("What are my reminders?")["response"]
    assert "Fridays" in turn(f"Move reminder {job_id} to every Friday at 10 AM")["response"]
    assert "review the proposal" in turn(f"Edit reminder {job_id} to every Friday at 11 AM")["response"]
    assert "review the contract" in turn(f"Edit reminder {job_id} to say review the contract")["response"]
    assert turn(f"Cancel reminder {job_id}")["response"] == f"Cancelled reminder {job_id}."
    assert turn(f"Cancel reminder {job_id}")["response"] == f"Cancelled reminder {job_id}."
    assert "no active reminders" in turn("List my reminders")["response"]
    job = aos_jobs.execute(root, job_id, reminder_send=forbidden, notify=forbidden, modelport=forbidden)
    assert job["status"] == "cancelled"
    assert "Token usage: 0" in (root / job["receipts"][0]["path"]).read_text()
    assert david.turn(CONV, ORIGINAL_SHAPE)["denied"]
    assert david.turn("telegram-999", ORIGINAL_SHAPE, verified_chat="999")["denied"]
    assert david.turn("example-dealership", ORIGINAL_SHAPE, verified_chat=CHAT)["denied"]
    assert not aos_jobs.aos_authority.action_authorization(
        root, None, action="GMAIL_SEND_EMAIL", target="internal@example.test")["authorized"]


@pytest.mark.parametrize("text,expected", [
    ("tomorrow at 9:30 AM to call Sam", "2026-03-08T15:30:00+00:00"),
    ("to call Sam tomorrow at 9:30 AM", "2026-03-08T15:30:00+00:00"),
    ("to call Sam at 9:30 AM tomorrow", "2026-03-08T15:30:00+00:00"),
    ("to call Sam on Sunday at 9:30 AM", "2026-03-08T15:30:00+00:00"),
    ("tomorrow at 09:30 America/Edmonton to call Sam", "2026-03-08T15:30:00+00:00"),
    ("tomorrow afternoon at 3:30 to call Sam", "2026-03-08T21:30:00+00:00"),
    ("every Sunday at 2:30 AM to call Sam", "2026-03-15T08:30:00+00:00"),
    ("in an hour to call Sam", "2026-03-07T21:00:00+00:00"),
])
def test_local_wall_clock(text, expected):
    start = dt.datetime(2026, 3, 7, 20, tzinfo=UTC)
    result = aos_reminders.parse(text, start, default_zone="America/Edmonton")
    assert result["next_at"] == expected
    assert result["text"] == "call Sam"


def test_explicit_timezone_and_dst_fold_and_gap():
    start = dt.datetime(2026, 3, 7, 20, tzinfo=UTC)
    with pytest.raises(aos_reminders.ReminderError, match="does not exist"):
        aos_reminders.parse("tomorrow at 2:30 AM America/Edmonton to check", start)
    fold = dt.datetime(2026, 10, 31, 20, tzinfo=UTC)
    reminder = aos_reminders.parse("every Sunday at 1:30 AM America/New_York to check", fold)
    assert reminder["next_at"] == "2026-11-01T05:30:00+00:00"
    next_due = aos_reminders.next_occurrence(reminder, dt.datetime.fromisoformat(reminder["next_at"]))
    assert next_due.isoformat() == "2026-11-08T06:30:00+00:00"


def test_runner_restarts_and_sends_once_at_due_time(root):
    job_id = make(root, delay=.55)
    calls = []

    def record(*args, **kwargs):
        calls.append(now())
        return sent(*args, **kwargs)

    runner = aos_jobs.JobRunner(root, reminder_send=record, notify=forbidden, modelport=forbidden)
    runner.submit(job_id).result()
    assert aos_jobs.load(root, job_id)["status"] == "queued"
    assert not calls
    runner.shutdown(wait=True)

    first = aos_jobs.JobRunner(root, reminder_send=record, notify=forbidden, modelport=forbidden)
    second = aos_jobs.JobRunner(root, reminder_send=record, notify=forbidden, modelport=forbidden)
    try:
        first.start()
        second.start()
        job = until(root, job_id, lambda j: j["status"] == "done" and j.get("receipts"))
        first.submit(job_id).result()
        second.submit(job_id).result()
        assert len(calls) == 1 and calls[0] >= dt.datetime.fromisoformat(job["reminder"]["next_at"])
        assert job["last_delivery"]["message_id"] == 42
        receipt = (root / job["receipts"][0]["path"]).read_text()
        assert "message ID 42" in receipt and "Token usage: 0" in receipt
    finally:
        first.shutdown(wait=True)
        second.shutdown(wait=True)


def crash_child(root, job_id, marker, sending):
    def hang(*args, **kwargs):
        marker.set()
        time.sleep(30)

    if sending:
        aos_jobs.execute(root, job_id, reminder_send=hang)
    else:
        aos_jobs.claim(root, job_id)
        hang()


@pytest.mark.parametrize("sending", [False, True])
def test_process_death_before_or_during_send(root, sending):
    job_id = make(root, recurring=sending)
    context = multiprocessing.get_context("fork")
    marker = context.Event()
    proc = context.Process(target=crash_child, args=(root, job_id, marker, sending))
    proc.start()
    try:
        assert marker.wait(3)
    finally:
        proc.kill()
        proc.join(timeout=3)
    report = aos_jobs.recover(root)
    if sending:
        assert report["interrupted"] == [job_id]
        job = aos_jobs.execute(root, job_id, reminder_send=forbidden)
        assert job["status"] == "queued"
        assert job["last_delivery"]["status"] == "uncertain"
        assert job["last_delivery"]["message_id"] is None
        assert dt.datetime.fromisoformat(job["reminder"]["next_at"]) > now()
        assert "Telegram: uncertain" in (root / job["receipts"][0]["path"]).read_text()
    else:
        assert report["requeued"] == [job_id]
        assert aos_jobs.execute(root, job_id, reminder_send=sent)["status"] == "done"


def test_recurring_receipts_survive_cancel_and_receipt_projection_failure(root, monkeypatch):
    job_id = make(root, recurring=True)
    original = aos_jobs.write_receipt
    with monkeypatch.context() as patch:
        def fail(*a, **kw):
            raise OSError("receipt storage interrupted")

        patch.setattr(aos_jobs, "write_receipt", fail)
        with pytest.raises(OSError):
            aos_jobs.execute(root, job_id, reminder_send=sent)
    assert aos_jobs.load(root, job_id)["last_delivery"]["message_id"] == 42
    assert aos_jobs.write_receipt is original
    aos_jobs.recover(root)
    assert aos_jobs.execute(root, job_id, reminder_send=forbidden)["status"] == "queued"
    job = aos_jobs.change_reminder(root, job_id, conversation_id=CONV, chat_id=CHAT)
    assert [r["status"] for r in job["receipts"]] == ["done", "cancelled"]


@pytest.mark.parametrize("response,status", [
    ({"message_sent": False}, "failed"),
    ({"message_sent": True}, "uncertain"),
    ({}, "uncertain"),
])
def test_no_provider_confirmation_is_never_a_sent_receipt(root, response, status):
    job_id = make(root)
    job = aos_jobs.execute(root, job_id, reminder_send=lambda *a, **kw: response, notify=forbidden)
    assert job["status"] == job["last_delivery"]["status"] == status
    assert job["last_delivery"]["message_id"] is None
    aos_jobs.recover(root)
    aos_jobs.retry_failed_notices(root, forbidden)
    aos_jobs.execute(root, job_id, reminder_send=forbidden)


def test_revoked_destination_blocks_delivery(root):
    job_id = make(root)
    (root / "queue/notifications.json").write_text("{}")
    job = aos_jobs.execute(root, job_id, reminder_send=forbidden)
    assert job["status"] == "blocked"


def test_runner_checks_for_a_clock_step_before_sending(root, monkeypatch):
    job_id = make(root)
    checks = iter((0, 1))
    with monkeypatch.context() as patch:
        patch.setattr(aos_jobs, "_reminder_wait", lambda job: next(checks))
        job = aos_jobs.execute(root, job_id, reminder_send=forbidden)
    assert job["status"] == "queued" and job["notification"] is None
    result = aos_jobs.execute(root, job_id, reminder_send=lambda *a, **kw: {
        "message_sent": True, "message_id": 42, "message_date": 1795594380,
    })
    assert result["last_delivery"]["telegram_at"] == "2026-11-25T08:13:00+00:00"


def test_http_ingress_and_dashboard_projection(root, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend import main

    monkeypatch.setattr(main, "BASE_DIR", root)
    monkeypatch.setattr(main, "DAVID", aos_david.David(root, model=forbidden))
    monkeypatch.setattr(main, "TURNS", aos_david.TurnStore(root))
    client = TestClient(main.app)
    route = "/api/dashboard/ask-david"
    payload = {"text": ORIGINAL_SHAPE, "conversation_id": CONV, "delivery_id": "telegram-update-test"}
    assert client.post(route, json=payload).status_code == 403
    evidence = root / david_ingress.STATE
    evidence.parent.mkdir(parents=True)
    digest = hashlib.sha256(f"{CHAT}\0{payload['delivery_id']}".encode()).hexdigest()
    evidence.write_text(json.dumps({"david_deliveries": {digest: {
        "chat_id": CHAT, "delivery_id": payload["delivery_id"], "status": "submitting", "payload": payload,
    }}}))
    result = client.post(route, json=payload).json()
    assert result["success"] and result["action"] == "reminder_create"
    assert result["model_calls"] == 0 and "9:30 AM" in result["response"]
    assert client.post(route, json=payload).json()["job_id"] == result["job_id"]
    row, = client.get("/api/david/jobs").json()["jobs"]
    assert row["id"] == result["job_id"] and row["reminder"]["timezone"] == "America/Creston"


def test_pause_resume_skips_elapsed_recurrence_and_displays_in_jobs(root, monkeypatch):
    from dashboard.backend import main

    job_id = make(root, delay=-86400, recurring=True)
    david = aos_david.David(root, model=forbidden, search=forbidden)

    def turn(text):
        return david.turn(CONV, text, verified_chat=CHAT)

    assert aos_reminders.command(f"resume {job_id}") is None
    assert turn(f"Pause reminder {job_id}")["response"] == f"Paused reminder {job_id}."
    assert turn(f"Pause reminder {job_id}")["model_calls"] == 0
    assert f"{job_id} · Paused" in turn("List my reminders")["response"]
    assert turn(f"Edit reminder {job_id} to say check again")["response"].endswith("On Telegram: check again")
    assert turn(f"Resume reminder {job_id} now and email Sam")["reminder_error"]
    monkeypatch.setattr(main, "BASE_DIR", root)
    row, = main.david_jobs()["jobs"]
    assert row["status"] == "paused"
    assert row["reminder"]["text"] == "check again"

    runner = aos_jobs.JobRunner(root, reminder_send=forbidden, modelport=forbidden)
    try:
        runner.start()
        assert runner.submit(job_id).result()["status"] == "paused"
        assert turn(f"Resume reminder {job_id}")["response"].startswith(f"Resumed reminder {job_id}.")
        resumed = runner.submit(job_id).result()
        assert resumed["status"] == "queued"
        assert dt.datetime.fromisoformat(resumed["reminder"]["next_at"]) > now()
        assert "last_delivery" not in resumed
    finally:
        runner.shutdown(wait=True)
    turn(f"Pause reminder {job_id}")
    assert turn(f"Cancel reminder {job_id}")["response"] == f"Cancelled reminder {job_id}."


def test_resume_missed_one_off_reminder_is_due_immediately(root):
    job_id = make(root, delay=-10)
    david = aos_david.David(root, model=forbidden, search=forbidden)
    david.turn(CONV, f"Pause reminder {job_id}", verified_chat=CHAT)
    assert aos_jobs.execute(root, job_id, reminder_send=forbidden)["status"] == "paused"
    result = david.turn(CONV, f"Resume reminder {job_id}", verified_chat=CHAT)
    assert "It is due now." in result["response"]
    assert aos_jobs.execute(root, job_id, reminder_send=sent)["status"] == "done"
    assert aos_jobs.execute(root, job_id, reminder_send=forbidden)["status"] == "done"


def test_admitted_workflow_schedules_in_existing_jobs_runner(root, monkeypatch):
    import ttros_connections

    calls = []
    request = dict(conversation_id=CONV, topic="workflow", request="Read the approved connection",
                   executor={"kind": "connection_read", "action": "read"}, dedupe_key="workflow")
    with pytest.raises(aos_jobs.JobError, match="timezone-aware"):
        aos_jobs.create(root, **request, run_at=dt.datetime(2026, 11, 25))
    due = now() + dt.timedelta(seconds=.5)
    job, created = aos_jobs.create(root, **request, run_at=due)
    assert created and job["scheduled_at"] == due.isoformat()
    assert not aos_jobs.create(root, **request, run_at=due)[1]
    with pytest.raises(aos_jobs.JobError, match="different scheduled time"):
        aos_jobs.create(root, **request, run_at=due + dt.timedelta(days=1))

    def read(_root, running):
        assert running["id"] == job["id"]
        calls.append(now())
        return {"status": "ok", "output": {"summary": "Read verified"}, "usage": {"exact": True, "model_calls": 0}}

    monkeypatch.setattr(ttros_connections, "run_connection_job", read)
    runner = aos_jobs.JobRunner(root, modelport=forbidden, perform=forbidden)
    assert runner.submit(job["id"]).result()["status"] == "queued"
    runner.shutdown(wait=True)
    assert not calls
    restarted = aos_jobs.JobRunner(root, modelport=forbidden, perform=forbidden)
    try:
        restarted.start()
        done = until(root, job["id"], lambda row: row["status"] == "done")
        assert len(calls) == 1 and calls[0] >= due
        assert done["receipts"] and done["result"]["summary"] == "Read verified"
        assert restarted.submit(job["id"]).result()["status"] == "done"
        assert len(calls) == 1
    finally:
        restarted.shutdown(wait=True)
