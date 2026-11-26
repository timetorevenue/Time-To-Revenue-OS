"""David's background jobs: one JSON file per job, executed through the M1 agent runtime.

A job is one delegated request: its conversation and topic, the M1 executor spec
(planner, plan or agent, exactly as ``aos_agents.executor`` reads it), and what
happened. The file is the whole record:

  queue/jobs/<key>.json         full ID, state, claim, result, files, effects, usage
  queue/jobs/.sequence.json     durable per-year decimal high-water marks
  queue/receipts/<key>.md        closeout with the same full ID
  queue/jobs/keys/<sha>.json    optional dedupe key -> job id (created exclusively)
  queue/threads/<job_id>.jsonl  the M1 step journal (owned by aos_agents)

Guarantees, and nothing more:
  identity      AOS-YYYY-<decimal> has no digit ceiling; old J IDs remain readable
                allocation is serialized and reserved durably before job publication
                a dedupe key maps to exactly one job, including after a crash
  claiming      a job runs only after an atomic queued -> running transition under its lock
  recovery      a running job whose claiming process died is re-run once; M1 replays
                completed plan steps from its journal, so model work is not repeated
  attribution   conversation, chat and topic are fixed at creation and copied into the notice
  files         every artifact the result names must exist with the recorded sha256
  effects       an effect is performed at most once: its intent is saved before the call;
                an intent found after a crash is reported uncertain, never repeated
  delivery      one notice per settle point (paused or finished), sent at most once
  clarification an agent's needs_input pauses the job as waiting_input, which is not
                finished: resume() continues the same ID with what Casey supplied; each
                round keeps its own receipt and notice. A job recorded as needs_input
                before 2026-11-16 ended under the old rule and stays finished.
  usage         exact per-call usage and ModelPort invocation ids from the run
  account       one Codex account (slot A/B) per job, chosen at its first model launch
                (aos_codex_policy.AccountPin) and recorded; when no account may start a
                model the job is not failed: it goes back to queued with a ``deferred``
                note and the runner rechecks it at a bounded interval (MAX_DEFERRALS)

Authorization happens where the effect happens (aos_authority, reading the job's
``requested_by`` and ``restrictions``); there is no review step, receipt gate or second
queue. Short IDs retain their paths; long IDs use
a SHA-256 filesystem key, with the complete identifier in records and receipts.

Revisit: when the M1 result shape, the executor spec or the notice format changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from aos_queue_storage import durable_replace_text, process_start_identity, durable_create_directory
from aos_job_ids import JOB_ID_RE, ID_IN_TEXT_RE, decimal, highest, increment, storage_key
import aos_authority
import aos_reminders
from aos_authority import RESTRICTIONS

JOBS_DIR = Path("queue") / "jobs"
# Finished: the job will never change again. "needs_input" is the historical final status of a
# job that ended for missing input before jobs could pause (e.g. AOS-2026-10000); it is never reopened.
TERMINAL = frozenset({"done", "refused", "failed", "interrupted", "needs_input", "blocked", "cancelled", "uncertain"})
# Paused for Casey's answer. Not finished: resume() continues the same job.
WAITING = frozenset({"waiting_input"})
# A settle point: a receipt and one notice are due.
SETTLED = TERMINAL | WAITING
MAX_ROUNDS = 5   # a job can be resumed this many times
MAX_ATTEMPTS = 2
DEFAULT_WORKERS = 3
# A job refused a Codex account before any model launched is re-queued, not failed. The
# runner rechecks it no sooner than the refusal's recheck time (a usage window reset, or the
# authentication recheck), bounded below and above. After MAX_DEFERRALS automatic rechecks it
# stays queued (never failed or completed) and is tried again when the runner next starts.
MAX_DEFERRALS = 48
DEFERRAL_MIN_SECONDS = 60
DEFERRAL_MAX_SECONDS = 1800
# M5's release states (agents/registry.lock.json). Production work goes only to a package Casey
# has released this far; shadow and candidate packages stay out of production by default.
RELEASE_STATES_ENV = "TTROS_DAVID_RELEASE_STATES"
DEFAULT_RELEASE_STATES = ("canary", "active")


class JobError(RuntimeError):
    pass


FINISHED_AS = {"blocked": "blocked", "done": "done", "refused": "refused", "failed": "failed", "interrupted": "interrupted",
               "needs_input": "ended without running because it lacked input",
               "cancelled": "cancelled", "uncertain": "delivery uncertain"}


def lifecycle(status: str) -> str:
    """What a status means, in the words Casey and David's model are shown (never the raw token alone:
    "needs_input" on a finished job reads as a request for input, which misled the live M5 turns)."""
    if status in WAITING:
        return "paused, waiting for Casey's answer"
    if status in TERMINAL:
        return f"finished ({FINISHED_AS[status]}); it will never run again"
    return "in progress"


def _restrictions(values) -> list[str]:
    unknown = sorted({str(value) for value in values or []} - set(RESTRICTIONS))
    if unknown:
        raise JobError(f"unknown restriction(s): {', '.join(unknown)}")
    return [name for name in RESTRICTIONS if name in set(values or [])]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _dir(root: Path) -> Path:
    return Path(root) / JOBS_DIR


def _path(root: Path, job_id: str) -> Path:
    if not JOB_ID_RE.match(str(job_id or "")):
        raise JobError(f"not a job id: {job_id!r}")
    return _dir(root) / f"{storage_key(job_id)}.json"


@contextmanager
def _locked(root: Path, job_id: str):
    folder = _dir(root) / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{storage_key(job_id)}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load(root: Path, job_id: str) -> dict:
    try:
        job = json.loads(_path(root, job_id).read_text(encoding="utf-8"))
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise JobError("job file does not match its identifier")
        return job
    except FileNotFoundError as exc:
        raise JobError(f"unknown job {job_id}") from exc


def save(root: Path, job: dict) -> None:
    path = _path(root, job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    job["updated_at"] = _now()
    durable_replace_text(path, json.dumps(job, ensure_ascii=False, sort_keys=True, indent=1) + "\n")


def order_key(job):
    suffix = job["id"].rsplit("-", 1)[-1]
    number = decimal(suffix) if job["id"].startswith("AOS-") else suffix
    return str(job.get("created_at") or ""), len(number), number


def list_jobs(root: Path, *, conversation_id: str | None = None, limit: int | None = 20) -> list[dict]:
    jobs = []
    for path in _dir(root).glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict) or not JOB_ID_RE.fullmatch(str(job.get("id") or "")):
            continue
        if conversation_id is None or job.get("conversation_id") == conversation_id:
            jobs.append(job)
    jobs.sort(key=order_key, reverse=True)
    return jobs[:limit]


def _deduplicated(root: Path, key_hash: str) -> dict | None:
    key_path = _dir(root) / "keys" / (key_hash[:32] + ".json")
    if key_path.exists():
        try:
            job = load(root, json.loads(key_path.read_text(encoding="utf-8"))["job_id"])
        except (OSError, ValueError, KeyError) as exc:
            raise JobError("dedupe record is unreadable; refusing to create another job") from exc
        if job.get("dedupe_sha256") not in (None, "", key_hash):
            raise JobError("dedupe key digest collision; refusing to reuse a job")
        return job
    # A crash between publishing the job and the key must never duplicate work.
    return next((job for job in list_jobs(root, limit=None) if job.get("dedupe_sha256") == key_hash), None)


def by_request(root: Path, dedupe_key: str) -> dict | None:
    """Read an existing admission, including a job whose key publication was interrupted."""
    return _deduplicated(root, hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest())


def create(root: Path, *, conversation_id: str, topic: str, request: str, executor: dict,
           chat_id: str = "", dedupe_key: str = "", requested_by: str = "casey",
           restrictions: list[str] | tuple = (), run_at: dt.datetime | None = None,
           automation: dict | None = None) -> tuple[dict, bool]:
    """Persist admitted work, optionally as a one-off execution at an aware due time.

    Callers keep their existing admission checks. The runner applies the same executor,
    release, effect and notification rules when the job becomes due.
    """
    if not isinstance(executor, dict) or not (executor.get("kind") == "agent" or
            (executor.get("kind") == "factory" and executor.get("action") == "build_agent") or
            (executor.get("kind") == "website" and executor.get("action") == "change_request") or
            (executor.get("kind") == "product_launch" and executor.get("action") in ("draft", "fulfill")) or
            (executor.get("kind") == "connection_read" and executor.get("action") == "read") or
            (executor.get("kind") == "automation" and automation is not None) or
            (executor.get("kind") == "automation_action" and executor.get("automation_origin")) or
            (executor.get("kind") == "lead_magnet" and executor.get("action") == "draft_follow_up") or
            (executor.get("kind") == "reminder" and isinstance(executor.get("schedule"), dict)) or
            (executor.get("kind") == "website_manager" and executor.get("action") == "interpret")):
        raise JobError("executor must be an M1 agent, Factory build, website change, Website Manager turn or lead magnet draft spec")
    root = Path(root)
    restrictions = _restrictions(restrictions)
    if executor["kind"] == "reminder" and not aos_authority.reminder_authorized(root, conversation_id, str(chat_id or "")):
        raise JobError("Reminders require the authorized Telegram DM.")
    scheduled_at = None
    if run_at is not None:
        if executor["kind"] == "reminder":
            raise JobError("A reminder already has its own due time.")
        if not isinstance(run_at, dt.datetime) or run_at.utcoffset() is None:
            raise JobError("Workflow scheduling requires a timezone-aware due time.")
        scheduled_at = run_at.astimezone(dt.timezone.utc).isoformat()

    def same_request(existing):
        if existing.get("dedupe_sha256") not in (None, "", key_hash):
            raise JobError("dedupe key digest collision; refusing to reuse a job")
        original_executor = existing["executor"]
        if external := executor.get("external_mcp"):
            previous = original_executor.get("external_mcp") or {}
            if (existing["conversation_id"] == conversation_id
                    and previous.get("identity") == external["identity"]
                    and previous.get("request_sha256") == external["request_sha256"]):
                return existing, False
            raise JobError("dedupe key belongs to a different request")
        if (executor.get("automation_origin") and executor.get("automation_identity")
                and existing.get("dedupe_sha256") == key_hash
                and existing.get("automation_origin") == executor.get("automation_origin")
                and original_executor.get("automation_identity") == executor["automation_identity"]):
            # Re-observed events may have new receipt metadata. The first admitted
            # occurrence remains the only execution for this identity.
            return existing, False
        # A resumed job keeps what it was created with under "original"; a repeat matches that.
        created = {**existing, **(existing.get("original") or {})}
        fields = {"conversation_id": conversation_id, "topic": topic, "request": request[:4000],
                  "executor": executor, "chat_id": str(chat_id or ""), "requested_by": requested_by,
                  "restrictions": restrictions}
        if any((created.get(key) or ([] if key == "restrictions" else "")) != value for key, value in fields.items()):
            raise JobError("dedupe key belongs to a different request")
        if created.get("scheduled_at") != scheduled_at:
            raise JobError("dedupe key belongs to a different scheduled time")
        return existing, False

    # One durable store and one allocation lock for all submitters, including Factory.
    folder = _dir(root)
    durable_create_directory(folder, parents=True)
    with (folder / "allocation.lock").open("a") as allocation:
        fcntl.flock(allocation, fcntl.LOCK_EX)
        key_hash = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest() if dedupe_key else ""
        key_path = folder / "keys" / (key_hash[:32] + ".json") if key_hash else None
        if key_hash:
            if existing := _deduplicated(root, key_hash):
                if not key_path.exists():
                    durable_replace_text(key_path, json.dumps({"job_id": existing["id"]}) + "\n")
                return same_request(existing)
        job_id = _allocate_id(root)
        job = {
            "id": job_id, "status": "queued", "created_at": _now(), "requested_by": requested_by,
            "conversation_id": conversation_id, "chat_id": str(chat_id or ""), "topic": topic,
            "request": request[:4000], "executor": executor, "restrictions": restrictions, "attempts": 0,
            "claim": None, "result": None, "artifacts": [], "effects": [], "usage": None, "notification": None,
            "dedupe_sha256": key_hash, "round": 0,
        }
        if scheduled_at is not None:
            job["scheduled_at"] = scheduled_at
        if automation is not None:
            job["automation"] = automation
            job["usage"] = _usage_summary({"exact": True, "model_calls": 0, "input_tokens": 0,
                                          "cached_input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        if origin := executor.get("automation_origin"):
            job["automation_origin"] = origin
        if executor["kind"] == "reminder":
            job.update(reminder=executor["schedule"], usage=_usage_summary({
                "exact": True, "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cached_input_tokens": 0, "total_tokens": 0,
            }))
        save(root, job)
        if key_path:
            durable_replace_text(key_path, json.dumps({"job_id": job_id}) + "\n")
        return job, True


def _historical_highwater(root: Path, year: str) -> str:
    """Reserve IDs from existing state without migrating or executing any historical item."""
    maximum = "0"
    def reserve(text):
        nonlocal maximum
        for match in ID_IN_TEXT_RE.finditer(text):
            _, found_year, number = match.group().split("-", 2)
            if found_year == year:
                maximum = highest(maximum, number)

    for relative in ("queue/work_items.jsonl", "queue/orchestration_events.jsonl", "queue/run_ledger.jsonl",
                     "queue/token_ledger.jsonl"):
        path = root / relative
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    reserve(line)
    for relative in ("queue/receipts", "queue/threads", "results/agents", "queue/jobs"):
        folder = root / relative
        if folder.is_dir():
            for path in folder.rglob("*"):
                reserve(path.name)
                if relative == "queue/receipts" and path.is_file() and path.suffix in {".md", ".txt", ".json", ".pending"}:
                    text = path.read_text(encoding="utf-8")
                    if path.name.startswith(("task-deletion-", ".task-deletion-")):
                        json.loads(text)  # malformed deletion evidence must not permit identifier reuse
                    reserve(text)
                if relative == "queue/jobs" and path.parent == folder and path.suffix == ".json" and not path.name.startswith("."):
                    value = json.loads(path.read_text(encoding="utf-8"))
                    reserve(str(value.get("id") or ""))
    return maximum


def _allocate_id(root: Path) -> str:
    """Called only under allocation.lock. Persist a reservation before publishing a job."""
    year = dt.datetime.now(dt.timezone.utc).strftime("%Y")
    state_path = _dir(root) / ".sequence.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("version") != 1 or not isinstance(state.get("years"), dict):
                raise ValueError("invalid sequence record")
            for key, value in state["years"].items():
                if not re.fullmatch(r"[0-9]{4}", key):
                    raise ValueError("invalid sequence year")
                decimal(value)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise JobError("job sequence is unreadable; refusing to reuse an identifier") from exc
    else:
        state = {"version": 1, "years": {}}
    if year not in state["years"]:
        state["years"][year] = _historical_highwater(root, year)
    number = increment(state["years"][year])
    job_id = f"AOS-{year}-{number.zfill(4)}"
    while _path(root, job_id).exists():
        number = increment(number)
        job_id = f"AOS-{year}-{number.zfill(4)}"
    state["years"][year] = number
    durable_replace_text(state_path, json.dumps(state, sort_keys=True) + "\n")
    return job_id


def _claim_alive(claim: dict | None) -> bool:
    if not isinstance(claim, dict):
        return False
    try:
        return process_start_identity(int(claim["pid"])) == claim["start_id"]
    except (KeyError, TypeError, ValueError, OSError, ProcessLookupError):
        return False
    except Exception:
        return False


def claim(root: Path, job_id: str) -> dict | None:
    """queued -> running for this process, or None when the job is not runnable."""
    with _locked(root, job_id):
        job = load(root, job_id)
        if job["status"] != "queued" or job.get("automation"):
            return None
        if _scheduled_wait(job) > 0:
            return None
        pid = os.getpid()
        job.update(status="running", attempts=int(job.get("attempts") or 0) + 1,
                   claim={"pid": pid, "start_id": process_start_identity(pid), "at": _now()})
        save(root, job)
        return job


def recover(root: Path) -> dict[str, list[str]]:
    """Requeue jobs whose claiming process died (once); settle stranded deliveries and effects."""
    requeued, interrupted, undelivered = [], [], []
    for job in list_jobs(root, limit=None):
        job_id = job["id"]
        with _locked(root, job_id):
            job = load(root, job_id)
            if job.get("reminder"):
                if job["status"] == "running" and not _claim_alive(job.get("claim")):
                    if (job.get("notification") or {}).get("status") == "sending":
                        _settle_reminder(root, job, "uncertain", "The runner stopped during the Telegram send. This occurrence will not be repeated.")
                        interrupted.append(job_id)
                    else:
                        job.update(status="queued", claim=None, recovered_at=_now())
                        save(root, job)
                        requeued.append(job_id)
                _write_reminder_receipt(root, job)
                continue
            changed = False
            if job["status"] == "running" and not _claim_alive(job.get("claim")):
                if int(job.get("attempts") or 0) < MAX_ATTEMPTS:
                    job.update(status="queued", claim=None, recovered_at=_now())
                    requeued.append(job_id)
                else:
                    job.update(status="interrupted", failure_class="interrupted",
                               detail="The job was interrupted twice and was not run again.", claim=None)
                    interrupted.append(job_id)
                changed = True
            for effect in job.get("effects") or []:
                if effect.get("status") == "intent" and job["status"] != "running":
                    effect.update(status="uncertain", detail="interrupted after the effect started; not repeated")
                    changed = True
            notice = job.get("notification") or {}
            if notice.get("status") == "sending":
                notice.update(status="uncertain", detail="interrupted while sending; not resent")
                job["notification"] = notice
                changed = True
            elif job["status"] in SETTLED and not notice and not job.get("automation"):
                undelivered.append(job_id)
            if changed:
                write_receipt(root, job)
                save(root, job)
    return {"requeued": requeued, "interrupted": interrupted, "undelivered": undelivered + interrupted}


def _reminder_wait(job: dict) -> float:
    return _due_wait(job["reminder"]["next_at"])


def _due_wait(due_at: str) -> float:
    return (dt.datetime.fromisoformat(due_at) - dt.datetime.now(dt.timezone.utc)).total_seconds()


def _scheduled_wait(job: dict) -> float:
    if job.get("automation"):
        rule = job["automation"]
        due = _due_wait(rule["next_check"])
        return min(due, _due_wait(rule["expires_at"])) if rule.get("expires_at") and not rule.get("pending") else due
    if job.get("reminder"):
        return _reminder_wait(job)
    if due_at := job.get("scheduled_at"):
        return _due_wait(due_at)
    return 0


def _write_reminder_receipt(root: Path, job: dict) -> None:
    """Project the durable delivery outcome into the existing round receipt format."""
    outcome = job.get("last_delivery")
    if outcome and not any(row["round"] == outcome["round"] for row in job.get("receipts") or []):
        receipt = {**job, "status": outcome["job_status"], "round": outcome["round"],
                   "request": outcome["text"], "detail": outcome["detail"], "notification": outcome,
                   "result": None}
        write_receipt(root, receipt)
        job["receipts"] = receipt["receipts"]
        save(root, job)
    if job["status"] == "cancelled" and not any(row["round"] == job["round"] for row in job.get("receipts") or []):
        write_receipt(root, job)
        save(root, job)


def change_reminder(root: Path, job_id: str, *, conversation_id: str, chat_id: str,
                    schedule: dict | None = None, action: str | None = None) -> dict:
    """Change a reminder under the same lock used to claim it."""
    action = action or ("cancel" if schedule is None else "edit")
    if action not in {"cancel", "edit", "pause", "resume"}:
        raise JobError("Invalid reminder change.")
    if (schedule is not None) != (action == "edit"):
        raise JobError("Only a reminder edit takes a replacement schedule.")
    if not aos_authority.reminder_authorized(root, conversation_id, chat_id):
        raise JobError("Reminders require the authorized Telegram DM.")
    with _locked(root, job_id):
        job = load(root, job_id)
        if not job.get("reminder") or job.get("conversation_id") != conversation_id or job.get("chat_id") != chat_id:
            raise JobError("No reminder with that ID in this conversation.")
        _write_reminder_receipt(root, job)
        status = job["status"]
        if (action, status) in {("cancel", "cancelled"), ("pause", "paused"), ("resume", "queued")}:
            return job
        if status not in {"queued", "paused"}:
            raise JobError(f"Reminder {job_id} is {status} and cannot be changed.")
        if action == "pause":
            job.update(status="paused", paused_at=_now())
        elif action == "resume":
            reminder = job["reminder"]
            now = dt.datetime.now(dt.timezone.utc)
            if reminder["weekdays"] and dt.datetime.fromisoformat(reminder["next_at"]) <= now:
                job["reminder"] = {**reminder, "next_at": aos_reminders.next_occurrence(reminder, now).isoformat()}
            job.update(status="queued", paused_at=None)
        elif action == "cancel":
            stamp = _now()
            job.update(status="cancelled", settled_at=stamp, finished_at=stamp, failure_class="",
                       detail="Cancelled by Casey.", result=None, notification=None)
        else:
            reminder = job["reminder"]
            if schedule.get("text"):
                reminder = {**reminder, "text": schedule["text"]}
            job["reminder"] = {**reminder, **{key: value for key, value in schedule.items() if key != "text"}}
        save(root, job)
        _write_reminder_receipt(root, job)
        return job


def _settle_reminder(root: Path, job: dict, status: str, detail: str, message_id: int | None = None,
                     message_date: int | None = None) -> dict:
    """Commit the outcome and next occurrence together before rendering the receipt."""
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    reminder = job["reminder"]
    final = "done" if status == "sent" else status
    delivery = {"status": status, "job_status": final, "at": stamp, "due_at": reminder["next_at"],
                "round": job["round"], "message_id": message_id, "text": reminder["text"], "detail": detail}
    if type(message_date) is int and 0 < message_date < 253402300800:
        delivery["telegram_at"] = dt.datetime.fromtimestamp(message_date, dt.timezone.utc).isoformat()
    recurring = bool(reminder["weekdays"]) and status != "blocked"
    if recurring:
        due = dt.datetime.fromisoformat(reminder["next_at"])
        # After an outage, deliver the oldest pending occurrence once and skip elapsed repeats.
        following = aos_reminders.next_occurrence(reminder, max(now, due))
        job["reminder"] = {**reminder, "next_at": following.isoformat()}
    job.update(status="queued" if recurring else final, last_delivery=delivery, notification=delivery,
               detail=detail, claim=None, settled_at=stamp, finished_at=None if recurring else stamp,
               failure_class="" if status == "sent" else f"reminder_{status}",
               result={"summary": detail}, round=job["round"] + int(recurring))
    save(root, job)
    _write_reminder_receipt(root, job)
    return job


def _execute_reminder(root: Path, job: dict, send: Callable[..., dict]) -> dict:
    job_id = job["id"]
    with _locked(root, job_id):
        _write_reminder_receipt(root, job)
        chat = job["chat_id"]
        if not aos_authority.reminder_authorized(root, job["conversation_id"], chat):
            return _settle_reminder(root, job, "blocked", "The configured Telegram DM no longer authorizes delivery.")
        # Telegram sendMessage has no idempotency key. An interrupted intent is never resent.
        job["notification"] = {"status": "sending", "at": _now(), "due_at": job["reminder"]["next_at"]}
        save(root, job)
        # Check again after the durable write in case the host clock moved backwards.
        if _reminder_wait(job) > 0:
            job.update(status="queued", claim=None, notification=job.get("last_delivery"))
            save(root, job)
            return job
    try:
        result = send(chat, f"Reminder: {job['reminder']['text']}", kind="operational_notification", root=root)
        message_id = result.get("message_id")
        if result.get("message_sent") is True and type(message_id) is int and message_id > 0:
            outcome = ("sent", f"Telegram accepted the reminder (message {message_id}).", message_id, result.get("message_date"))
        elif result.get("message_sent") is False:
            outcome = ("failed", "Telegram did not accept the reminder.")
        else:
            outcome = ("uncertain", "Telegram returned no confirmed message ID. This occurrence will not be repeated.")
    except Exception as exc:
        outcome = ("uncertain", f"Telegram delivery could not be confirmed ({type(exc).__name__}). This occurrence will not be repeated.")
    with _locked(root, job_id):
        return _settle_reminder(root, job, *outcome)


# -- execution -------------------------------------------------------------------

def release_problem(root: Path, executor: dict) -> str:
    """Empty when every package the executor names is released for production use; else why not."""
    if executor.get("external_mcp") and executor.get("planner"):
        from aos_david_forge import admission

        return admission(root, "planner", executor["planner"])
    if executor.get("david_capability") is True:
        from aos_david_forge import admission

        kind = "plan" if executor.get("plan") else "agent"
        if digest := executor.get("automation_release_sha256"):
            from aos_automation import _forge_input

            try:
                _forge_input(root, {"type": kind, "ref": executor[kind], "input": executor["input"],
                                    "release_sha256": digest})
                return ""
            except JobError as exc:
                return str(exc)
        return admission(root, kind, executor.get(kind))
    if executor.get("kind") == "factory":
        return ""  # a build produces a candidate; it does not release or route that package
    if executor.get("kind") == "website":
        return ""  # no agent package runs: the coding workbench works in an isolated worktree (aos_website)
    if executor.get("kind") == "product_launch":
        return ""  # deterministic test-only launch adapter
    if executor.get("kind") == "lead_magnet":
        return ""  # deterministic consent-checked draft adapter; no model package
    if executor.get("kind") in {"connection_read", "automation_action"}:
        return ""  # scoped deterministic named read; registry grant checked at execution
    if executor.get("kind") == "website_manager":
        return ""  # deterministic first; its model route checks website.manager's release itself (aos_website_manager)
    refs = [executor[key] for key in ("planner", "agent") if executor.get(key)]
    if executor.get("plan"):
        try:
            from aos_agents.plans import load_plan

            refs += [step["agent"] for step in load_plan(executor["plan"], Path(root))["steps"]]
        except Exception as exc:
            return f"the plan cannot be loaded: {exc}"
    return refs_release_problem(root, refs)


def refs_release_problem(root: Path, refs: list[str]) -> str:
    """Empty when every named package is in an allowed release state; else why not.

    Also gates the specialists a released planner emits, before any of them runs.
    """
    allowed = {value.strip() for value in os.environ.get(RELEASE_STATES_ENV, ",".join(DEFAULT_RELEASE_STATES)).split(",")
               if value.strip()}
    try:
        registry = json.loads((Path(root) / "agents" / "registry.lock.json").read_text(encoding="utf-8")).get("agents") or {}
    except (OSError, ValueError):
        return "the agent registry is unreadable"
    for ref in refs:
        name = str(ref).split("@", 1)[0]
        state = (registry.get(name) or {}).get("state") or "unregistered"
        if state not in allowed:
            return (f"{ref} is in the {state} release state; David uses packages released to "
                    f"{' or '.join(sorted(allowed))} (aos-agent.py promote)")
    return ""


def _item_view(job: dict) -> dict:
    """The job as the M1 executor and the authorization policy read it."""
    return {"id": job["id"], "executor": job["executor"], "requested_by": job.get("requested_by") or "",
            "title": job.get("request", "")[:160], "restrictions": list(job.get("restrictions") or []),
            "conversation_id": job.get("conversation_id", "")}


def cancel(root: Path, job_id: str, *, conversation_id: str, owner: str) -> dict:
    """Cancel unclaimed or waiting work under the same lock as the runner's claim."""
    with _locked(root, job_id):
        job = load(root, job_id)
        if (job["conversation_id"] != conversation_id or not owner
                or job["executor"].get("capability_owner") != owner
                or job.get("automation") or job.get("reminder")):
            raise JobError("No execution Job with that ID in your scope.")
        if job["status"] == "cancelled":
            return job
        if job["status"] not in WAITING | {"queued"}:
            raise JobError(f"Job is {job['status']}. Only queued or waiting Jobs can be cancelled.")
        stamp = _now()
        job.update(status="cancelled", finished_at=stamp, settled_at=stamp, detail="Cancelled by the operator.",
                   notification={"status": "in_app", "at": stamp})
        # A waiting round already has a receipt. Preserve it and write the cancellation separately.
        if any(row.get("round") == job.get("round") for row in job.get("receipts") or []):
            job["round"] += 1
        write_receipt(root, job)
        save(root, job)
        return job


def resume(root: Path, job_id: str, *, conversation_id: str, records: list[dict] | tuple = (),
           people: list[dict] | tuple = (), answer: str = "", restrictions: list[str] | tuple = (),
           expected_round: int | None = None) -> dict:
    """Continue a job paused at waiting_input, on the same ID, with what Casey supplied.

    Records and people are added to the planner input (never removed); Casey's answer is
    appended to the request the planner reads; restrictions can only be added. The job
    is queued again; the caller submits it. Raises JobError when the job is not paused
    in this conversation or nothing new was supplied.
    """
    root = Path(root)
    restrictions = _restrictions(restrictions)
    with _locked(root, job_id):
        job = load(root, job_id)
        if job.get("conversation_id") != conversation_id:
            raise JobError("that job is not in this conversation")
        if job["status"] not in WAITING:
            raise JobError(f"job {job_id} is {job['status']} ({lifecycle(job['status'])}), not waiting for input")
        executor = job["executor"]
        if executor.get("kind") != "agent" or not executor.get("planner"):
            raise JobError("only a planned job can take more input")
        number = int(job.get("round") or 0)
        if expected_round is not None and expected_round != number:
            raise JobError("The Job round has changed. Read the current Job before replying.")
        if number >= MAX_ROUNDS:
            raise JobError(f"job {job_id} has already been resumed {MAX_ROUNDS} times")
        payload = dict(executor.get("input") or {})
        known_records = list(payload.get("records") or [])
        refs = {row["ref"] for row in known_records}
        added_records = [dict(row) for row in records if row.get("ref") and row["ref"] not in refs]
        known_people = list(payload.get("people") or [])
        names = {str(row.get("name") or "").casefold() for row in known_people}
        added_people = [dict(row) for row in people if row.get("name") and str(row["name"]).casefold() not in names]
        answer = " ".join(str(answer or "").split())[:400]
        added_restrictions = [name for name in restrictions if name not in (job.get("restrictions") or [])]
        if not (added_records or added_people or answer):
            raise JobError("nothing new was supplied for what the job is missing")
        answers = [row["answer"] for row in job.get("clarifications") or [] if row.get("answer")] + ([answer] if answer else [])
        payload.update(records=(known_records + added_records)[:8], people=(known_people + added_people)[:6])
        if answers:
            payload["request"] = (job["request"] + " Casey's answer: " + " / ".join(answers))[:1000]
        previous = {"round": number, "status": job["status"], "failure_class": job.get("failure_class") or "",
                    "detail": job.get("detail") or "", "missing": (job.get("result") or {}).get("missing"),
                    "usage": job.get("usage"), "settled_at": job.get("settled_at") or job.get("finished_at"),
                    "notification": job.get("notification")}
        job.setdefault("original", {"executor": executor, "restrictions": list(job.get("restrictions") or [])})
        job.update(status="queued", attempts=0, claim=None, executor={**executor, "input": payload},
                   restrictions=_restrictions(list(job.get("restrictions") or []) + added_restrictions),
                   result=None, failure_class="", detail="", usage=None, notification=None,
                   finished_at=None, settled_at=None, round=number + 1, resumed_at=_now(),
                   rounds=list(job.get("rounds") or []) + [previous],
                   clarifications=list(job.get("clarifications") or []) + [{
                       "round": number + 1, "at": _now(), "answer": answer,
                       "records": [row["ref"] for row in added_records],
                       "people": [row["name"] for row in added_people], "restrictions": added_restrictions}])
        save(root, job)
        return job


def verify_artifacts(root: Path, artifacts: list[dict]) -> tuple[list[dict], list[str]]:
    """Each artifact the run reports must exist inside the root with its recorded hash."""
    verified, problems = [], []
    base = Path(root).resolve()
    for row in artifacts or []:
        relative = str(row.get("path") or "")
        path = (base / relative).resolve()
        if not relative or base not in path.parents:
            problems.append(f"artifact path outside the workspace: {relative!r}")
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"reported file is missing: {relative}")
            continue
        if digest != row.get("sha256"):
            problems.append(f"reported file changed after it was written: {relative}")
            continue
        verified.append({"ref": row.get("ref"), "path": relative, "sha256": digest, "bytes": path.stat().st_size,
                         **({"job_id": row["job_id"]} if row.get("job_id") else {})})
    return verified, problems


def _find_draft(value: Any, subject: str) -> dict | None:
    if isinstance(value, dict):
        if isinstance(value.get("body"), str) and value.get("subject") == subject:
            return value
        for child in value.values():
            found = _find_draft(child, subject)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_draft(child, subject)
            if found:
                return found
    return None


def effect_arguments(effect: dict, output: Any) -> dict | None:
    """Connector arguments for a known effect, built from the run's own output."""
    action = str(effect.get("action") or "").upper()
    if action == "GMAIL_CREATE_EMAIL_DRAFT":
        draft = _find_draft(output, str(effect.get("subject") or ""))
        if draft is None:
            return None
        return {"recipient_email": effect.get("target"), "subject": draft["subject"], "body": draft["body"]}
    if isinstance(effect.get("arguments"), dict):
        return dict(effect["arguments"])
    return None


def connection_read(hub, principal: dict, connection: str, operation: str, arguments: dict | None = None) -> dict:
    """Read through the common Connections registry using trusted ingress context.

    Callers must supply the authenticated server context, never model-generated
    owner fields. The registry rechecks ownership, enablement and named operation.
    This adds no queue or external-write authority.
    """
    return hub.execute(principal, connection, operation, arguments)


def composio_perform(root: Path, action: str, target: str, arguments: dict, *, job_id: str = "", job: dict | None = None) -> dict:
    """Perform one authorized effect through the Composio Platform connection in the Connections Hub.

    The app comes from Composio's tool metadata, the account from the operator's Platform
    connection, and authority from the runtime permission policy (rechecked with this job).
    """
    from ttros_connections import ConnectionError as HubError, ProviderUnreachable, perform_operator
    try:
        receipt = perform_operator(root, str(action or "").strip().upper(), arguments or {}, item=job or {"id": job_id},
                                   target=target, idempotency_key=job_id)
    except ProviderUnreachable as exc:          # the provider may have acted: never repeated
        return {"ok": None, "reference": "", "detail": str(exc)[:600]}
    except HubError as exc:
        return {"ok": False, "reference": "", "detail": str(exc)[:600]}
    data = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    return {"ok": True, "reference": str(data.get("id") or data.get("message_id") or receipt["id"]),
            "receipt": receipt["id"], "detail": ""}


def _effect_key(job_id: str, index: int, effect: dict) -> str:
    raw = json.dumps([job_id, index, effect.get("effect"), effect.get("action"), effect.get("target")], sort_keys=True)
    return "E-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def apply_effects(root: Path, job: dict, result: dict, perform: Callable[[Path, str, str, dict], dict]) -> list[dict]:
    """Perform each authorized effect once; hold every other one with its reason."""
    records = {row["key"]: row for row in job.get("effects") or []}
    for index, effect in enumerate(result.get("proposed_effects") or []):
        key = _effect_key(job["id"], index, effect)
        prior = records.get(key)
        if prior and prior["status"] in ("performed", "held", "failed", "uncertain"):
            continue
        row = {"key": key, "effect": effect.get("effect"), "action": effect.get("action"),
               "target": effect.get("target"), "basis": (effect.get("authorization") or {}).get("basis", "")}
        if prior and prior["status"] == "intent":
            row.update(status="uncertain", detail="a previous attempt started this effect; not repeated")
        elif not (effect.get("authorization") or {}).get("authorized"):
            row.update(status="held", detail=(effect.get("authorization") or {}).get("detail")
                       or "needs Casey's explicit command naming this action and target")
        else:
            arguments = effect_arguments(effect, result.get("output"))
            if arguments is None:
                row.update(status="failed", detail="the run did not supply what this effect needs")
            else:
                row.update(status="intent", at=_now())
                records[key] = row
                job["effects"] = list(records.values())
                job["effect_attempts"] = list(job.get("effect_attempts") or []) + [{
                    "key": key, "action": row["action"], "target": row["target"], "at": _now()}]
                save(root, job)
                try:
                    outcome = perform(root, str(effect["action"]), str(effect.get("target") or ""), arguments)
                except Exception as exc:  # the adapter may have acted; never repeat
                    outcome = {"ok": None, "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
                row = dict(row)
                if outcome.get("ok") is True:
                    row.update(status="performed", reference=outcome.get("reference") or "")
                elif outcome.get("ok") is False:
                    row.update(status="failed", detail=str(outcome.get("detail") or "")[:300])
                else:
                    row.update(status="uncertain", detail=str(outcome.get("detail") or "")[:300])
        records[key] = row
    job["effects"] = list(records.values())
    return job["effects"]


def _usage_summary(usage: dict) -> dict:
    calls = usage.get("calls") or []
    fresh = (usage["input_tokens"] - usage["cached_input_tokens"]
             if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("cached_input_tokens"), int)
             else None)
    return {key: usage.get(key) for key in ("model_calls", "input_tokens", "output_tokens", "cached_input_tokens",
                                             "total_tokens", "exact", "elapsed_seconds")} | {
        "fresh_input_tokens": fresh,
        "invocation_ids": [call.get("invocation_id") for call in calls if call.get("invocation_id")],
        "by_step": [{key: call.get(key) for key in ("agent", "step", "status", "model", "tokens", "exact")} for call in calls],
    }


def _factory_result(root: Path, worker: dict) -> dict:
    """Adapt the existing M4 closeout to the same durable job record as M1/M5."""
    report = worker.get("factory_report") or {}
    if not report:
        raise JobError("executor returned no agent or Factory result")
    files = []
    for relative in worker.get("file_changes") or []:
        path = (root / relative).resolve()
        if path.is_relative_to(root.resolve()) and path.is_file():
            files.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    usage = worker.get("token_usage") or {}
    return {"status": "ok" if worker["success"] else "failed", "agent": worker.get("agent_ref"),
            "failure_class": worker.get("failure_class"), "detail": worker.get("failure_detail"),
            "output": {"summary": worker.get("output", "").splitlines()[1]}, "artifacts": files,
            "steps": [{"id": row["step"], "status": "ok" if row["ok"] else "failed"}
                      for row in report.get("steps") or []],
            "usage": {**{key: int(usage.get(key) or 0) for key in
                         ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")},
                      "model_calls": (report.get("tokens") or {}).get("live_calls", 0),
                      "exact": (report.get("tokens") or {}).get("exact", True)}}


def write_receipt(root: Path, job: dict) -> None:
    """One readable closeout per settle point, under the same ID as the job, journal and ledger.

    Round 0 is queue/receipts/<key>.md; a resumed job's later rounds add <key>.round-<n>.md,
    so an earlier receipt is never rewritten.
    """
    number = int(job.get("round") or 0)
    if job["status"] not in SETTLED or any(int(row.get("round") or 0) == number for row in job.get("receipts") or []):
        return
    if not job.get("reminder") and (root / "queue/token_ledger.jsonl").exists():
        from step6_cost_control import work_item_usage
        # Recovery replays steps with zero new usage. The ledger remains the lifetime total.
        job["accounting"] = work_item_usage(job["id"], root=root)
    head = "PASS" if job["status"] in {"done", "cancelled"} else "WAITING FOR CASEY" if job["status"] in WAITING else "NEEDS ATTENTION"
    lines = [head, f"Job: {job['id']}", f"Status: {job['status']} ({lifecycle(job['status'])})",
             f"Round: {number}", f"Request: {job.get('request') or ''}",
             str((job.get("result") or {}).get("summary") or ""), str(job.get("detail") or "")]
    if source := (job.get("executor") or {}).get("external_mcp"):
        lines.append(f"MCP source: {source['surface']} | {source['identity']} | workstream:{source['workstream_id']}")
    if job.get("reminder") and (delivery := job.get("notification")):
        lines.append(f"Telegram: {delivery['status']} | due {delivery['due_at']} | recorded {delivery['at']}"
                     + (f" | message ID {delivery['message_id']}" if delivery.get("message_id") else "")
                     + (f" | provider time {delivery['telegram_at']}" if delivery.get("telegram_at") else ""))
    for row in job.get("clarifications") or []:
        lines.append(f"Casey's answer (round {row['round']}): {row.get('answer') or ''}"
                     + (f" records={', '.join(row['records'])}" if row.get("records") else "")
                     + (f" people={', '.join(row['people'])}" if row.get("people") else ""))
    if job["status"] in WAITING:
        for item in (job.get("result") or {}).get("missing") or []:
            lines.append(f"Missing: {item}")
        lines.append("Next: Casey replies in the job's conversation; the same job continues.")
    for step in (job.get("result") or {}).get("steps") or []:
        ident = step.get("execution_identity")
        if ident:
            lines.append("Execution identity: " + json.dumps({"step": step.get("id"), "replayed": bool(step.get("replayed")),
                                                            **ident}, sort_keys=True, separators=(",", ":")))
    for row in job.get("artifacts") or []:
        lines.append(f"File: {row['path']} sha256={row['sha256']}")
    for row in job.get("brain_context_used") or []:
        lines.append(f"Brain: {row['path']} scope={row.get('client_scope') or ''}")
    for row in job.get("effects") or []:
        lines.append(f"Effect: {row.get('action')} {row['status']}")
    account = job.get("codex_account") or {}
    if account.get("slot"):
        lines.append(f"Codex account: {account['slot']} ({account.get('reason') or ''}; checked {account.get('checked_at') or 'n/a'}, "
                     f"basis {account.get('basis') or 'n/a'})")
    usage = job.get("usage") or {}
    accounting = job.get("accounting") or {}
    if accounting.get("invocation_count"):
        lines.append(f"Token usage: {accounting['known_tokens']} known across {accounting['invocation_count']} invocation(s)"
                     + (f"; {accounting['unknown_invocations']} unavailable" if accounting["unknown_invocations"] else " (exact)"))
    else:
        lines.append(f"Token usage: {usage.get('total_tokens')}" if usage.get("exact") else "Token usage: unavailable")
    content = "\n".join(lines) + "\n"
    relative = f"queue/receipts/{storage_key(job['id'])}" + (f".round-{number}" if number else "") + ".md"
    durable_replace_text(root / relative, content)
    job["receipts"] = list(job.get("receipts") or []) + [{
        "path": relative, "status": job["status"], "round": number, "created_at": _now(),
        "sha256": hashlib.sha256(content.encode()).hexdigest()}]


def _defer(root: Path, job: dict, refusal: Any) -> dict:
    """Back to queued: no model was launched, so the job did not run. The attempt is not consumed."""
    record = refusal.record() if hasattr(refusal, "record") else {"failure_class": "quota_unavailable", "detail": str(refusal)}
    previous = job.get("deferred") or {}
    job.update(status="queued", claim=None, attempts=max(0, int(job.get("attempts") or 1) - 1),
               failure_class="", detail=f"waiting for Codex capacity ({record['failure_class']}): {record['detail']}"[:1200],
               deferred={"count": int(previous.get("count") or 0) + 1, "at": _now(), **record})
    with _locked(root, job["id"]):
        save(root, job)
    return job


def execute(root: Path, job_id: str, *, modelport: Callable[..., dict] | None = None, backend: str = "exec",
            perform: Callable[[Path, str, str, dict], dict] | None = None,
            notify: Callable[[dict], dict] | None = None, brain_read: Callable | None = None,
            codex_account: Any = None, reminder_send: Callable[..., dict] | None = None) -> dict:
    """Claim and run one job through M1, verify its files, apply its effects and deliver its notice.

    ``codex_account`` is an aos_codex_policy.AccountPin (tests); production builds a fresh pin
    per run. An injected ``modelport`` owns its own routing and gets no pin."""
    started = time.monotonic()
    root = Path(root)
    if load(root, job_id).get("automation"):
        from aos_automation import tick

        return tick(root, job_id)
    job = claim(root, job_id)
    if job is None:
        return load(root, job_id)
    if job.get("reminder"):
        return _execute_reminder(root, job, reminder_send or aos_authority.operator_send)
    from aos_agents import executor

    deterministic_draft = job["executor"].get("kind") in ("lead_magnet", "product_launch", "connection_read", "automation_action")
    if codex_account is None and modelport is None and not deterministic_draft:
        from aos_codex_policy import AccountPin

        codex_account = AccountPin()
    try:
        if external := job["executor"].get("external_mcp"):
            if not aos_authority.external_job_operator(external["identity"]):
                job.update(status="blocked", failure_class="external_scope", detail="MCP Jobs access was revoked.",
                           finished_at=_now(), settled_at=_now(), claim=None)
                with _locked(root, job_id):
                    write_receipt(root, job)
                    save(root, job)
                return job
        if job.get("automation_origin"):
            from aos_automation import scope_problem
            if problem := scope_problem(root, job):
                job.update(status="blocked", failure_class="automation_scope", detail=problem,
                           finished_at=_now(), settled_at=_now(), claim=None,
                           notification={"status": "unavailable", "at": _now()})
                with _locked(root, job_id):
                    write_receipt(root, job)
                    save(root, job)
                return job
        unreleased = release_problem(root, job["executor"])
        if unreleased:
            job.update(status="refused", failure_class="agent_not_released", detail=unreleased, finished_at=_now(),
                       settled_at=_now(), claim=None)
            with _locked(root, job_id):
                write_receipt(root, job)
                save(root, job)
            return deliver(root, job_id, notify) if notify else job
        if job["executor"].get("kind") == "website_manager":
            import aos_website_manager
            worker = {"agent_result": aos_website_manager.run_job(root, job, modelport=modelport, codex_account=codex_account,
                                                                  package_root=job["executor"].get("package_root") and Path(job["executor"]["package_root"]))}
        elif deterministic_draft:
            if job["executor"]["kind"] == "automation_action":
                from aos_automation import run_action
                result = run_action(root, job)
            elif job['executor']['kind'] == 'connection_read':
                from ttros_connections import run_connection_job
                result = run_connection_job(root, job)
            elif job['executor']['kind'] == 'product_launch':
                import aos_product_launch
                result = aos_product_launch.run_job(root, job)
            else:
                import aos_lead_magnet
                result = aos_lead_magnet.run_draft_job(root, job)
            worker = {"agent_result": result}
        else:
            def admit(refs):
                if problem := refs_release_problem(root, refs):
                    return problem
                if external:
                    from aos_david_forge import package_problem

                    for ref in refs:
                        if problem := package_problem(root, ref, "external-plan"):
                            return problem
                return ""

            worker = executor.dispatch(root, _item_view(job), modelport=modelport, backend=backend,
                                       brain_read=brain_read, admit=admit,
                                       codex_account=codex_account)
        if codex_account is not None and codex_account.refused_before_launch:
            # No Codex account could start the model: nothing ran on a provider, nothing settled.
            return _defer(root, job, codex_account.refusal)
        if codex_account is not None and codex_account.granted is not None:
            job["codex_account"] = codex_account.granted.account_record()
        if worker.get("factory_report") is not None:
            job["factory_report"] = worker["factory_report"]
        result = worker.get("agent_result") or _factory_result(root, worker)
        status = str(result.get("status") or "failed")
        failure_class, detail = str(result.get("failure_class") or ""), str(result.get("detail") or "")
        if external and status == "ok":
            from aos_external_jobs import result_artifact

            result["artifacts"] = [*(result.get("artifacts") or []), result_artifact(root, job, result)]
        artifacts, problems = verify_artifacts(root, result.get("artifacts") or [])
        if status == "ok" and problems:
            status, failure_class, detail = "failed", "artifact_unverified", "; ".join(problems)[:600]
        if status == "ok" and not job.get("automation_origin"):
            if external:
                for effect in result.get("proposed_effects") or []:
                    effect["authorization"] = {"authorized": False, "basis": "mcp_job_scope",
                                               "detail": "External MCP jobs return proposals for the operator."}
            apply_effects(root, job, result, perform or
                          (lambda base, action, target, arguments: composio_perform(base, action, target, arguments, job_id=job_id, job=job)))
        output = result.get("output")
        # An agent asking for input pauses the job; it does not end it.
        final = "done" if status == "ok" else "waiting_input" if status == "needs_input" else status
        if final not in SETTLED:
            final, failure_class, detail = "failed", failure_class or "job_status_unknown", detail or f"unknown status {status!r}"
        structured = {}
        if final == "done" and (job["executor"].get("david_capability") or job.get("automation_origin")):
            from aos_capabilities import compact

            structured["output"] = compact({"value": output}, 12000)
        if job.get("automation_origin") and isinstance(output, dict) and output.get("quiet") is True:
            detail = "Findings were unchanged." if output.get("unchanged") else "No findings to notify."
            job["notification"] = {"status": "in_app", "at": _now(), "detail": detail}
        settled = _now()
        job.update(status=final, failure_class=failure_class, detail=detail[:1200],
                   result={**structured, "agent": result.get("agent") or result.get("plan"),
                           "summary": _result_summary(result, limit=1200 if job.get("automation_origin") else 600),
                           "steps": [{key: step.get(key) for key in ("id", "agent", "status", "replayed", "failure_class", "execution_identity")}
                                     for step in result.get("steps") or []],
                           "missing": (output or {}).get("missing") if status == "needs_input" and isinstance(output, dict) else None},
                   artifacts=artifacts, brain_context_used=worker.get("brain_context_used") or [],
                   usage=_usage_summary(result.get("usage") or {}), settled_at=settled,
                   finished_at=settled if final in TERMINAL else None, claim=None)
    except Exception as exc:  # a runtime bug ends this job visibly, never silently
        job.update(status="failed", failure_class="job_runtime_error", detail=f"{type(exc).__name__}: {str(exc)[:600]}",
                   finished_at=_now(), settled_at=_now(), claim=None)
    with _locked(root, job_id):
        if external:
            job["execution_elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_receipt(root, job)
        save(root, job)
    return deliver(root, job_id, notify) if notify else job


def _result_summary(result: dict, *, limit: int = 600) -> str:
    output = result.get("output")
    if isinstance(output, dict):
        for key in ("summary", "brief", "answer"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit]
            if isinstance(value, dict) and isinstance(value.get("summary"), str):
                return value["summary"][:600]
    return ""


def deliver(root: Path, job_id: str, notify: Callable[[dict], dict]) -> dict:
    """Send this settle point's notice (paused or finished) at most once."""
    with _locked(root, job_id):
        job = load(root, job_id)
        if job.get("reminder") or job.get("automation") or job["status"] not in SETTLED or (job.get("notification") or {}).get("status") in ("sent", "sending", "uncertain", "unavailable", "in_app"):
            return job
        job["notification"] = {"status": "sending", "at": _now()}
        save(root, job)
    try:
        sent = notify(job) or {}
        status = sent.get("status")
        notice = {"status": status if status in {"unavailable", "in_app"} else "sent",
                  "at": _now(), "message_id": sent.get("message_id")}
        if "handoff" in sent:
            notice["handoff"] = sent["handoff"]
    except Exception as exc:  # transport may have sent before raising; never resend automatically
        notice = {"status": "uncertain", "at": _now(), "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
    with _locked(root, job_id):
        job = load(root, job_id)
        job["notification"] = notice
        save(root, job)
    return job


def retry_failed_notices(root: Path, notify: Callable[[dict], dict]) -> list[str]:
    retried = []
    for job in list_jobs(root, limit=None):
        if not (job.get("reminder") or job.get("automation")) and job["status"] in SETTLED and (job.get("notification") or {}).get("status") in (None, "failed"):
            with _locked(root, job["id"]):
                current = load(root, job["id"])
                if (current.get("notification") or {}).get("status") == "failed":
                    current["notification"] = None
                    save(root, current)
            deliver(root, job["id"], notify)
            retried.append(job["id"])
    return retried


def notice_text(job: dict) -> str:
    """The completion message for the job's own conversation."""
    head = f"Job {job['id']}" + (f" ({job['topic']})" if job.get("topic") else "")
    request = " ".join(str(job.get("request") or "").split())[:160]
    lines = []
    status = job["status"]
    if status == "done":
        lines.append(f"{head} is done: {request}")
        result = job.get("result") or {}
        summary = result.get("summary")
        if summary:
            lines.append(summary)
        if str(result.get("agent", "")).split("@", 1)[0] == "shared.brain_retrieve":
            sources = ((result.get("output") or {}).get("value") or {}).get("sources") or []
            for source in sources[:4]:
                reference, digest = source.get("ref", ""), source.get("sha256", "")
                if (re.fullmatch(r"(?:business_brain:|artifact:)[A-Za-z0-9_./#:-]{1,280}", reference)
                        and re.fullmatch(r"[0-9a-f]{64}", digest)):
                    lines.append(f"Verified source: {reference} (SHA-256 {digest})")
            if len(sources) > 4:
                lines.append(f"{len(sources) - 4} more sources in the job result.")
    elif status == "needs_input":   # historical: ended before jobs could pause; never continued
        lines.append(f"{head} ended without running (it needed more input), under the rule in force before "
                     f"jobs could pause. It is finished and will not continue; ask again for a new job: {request}")
        if job.get("detail"):
            lines.append(job["detail"])
    elif status in WAITING:
        lines.append(f"{head} is paused until you answer: {request}")
        for item in (job.get("result") or {}).get("missing") or []:
            lines.append(f"- {item}")
        if not (job.get("result") or {}).get("missing") and job.get("detail"):
            lines.append(job["detail"])
        lines.append(f"Reply here with what is missing and {job['id']} continues under the same ID.")
    elif status == "refused":
        lines.append(f"{head} was refused ({job.get('failure_class') or 'refused'}): {job.get('detail') or request}")
    else:
        lines.append(f"{head} {('was interrupted' if status == 'interrupted' else 'failed')} "
                     f"({job.get('failure_class') or status}): {job.get('detail') or 'no detail'}")
    for row in job.get("artifacts") or []:
        lines.append(f"File: {row['path']} (verified, {row['bytes']} bytes)")
    for effect in job.get("effects") or []:
        lines.append(f"{effect['action']} -> {effect.get('target') or '-'}: {effect['status']}"
                     + (f" ({effect.get('detail')})" if effect.get("detail") else ""))
    usage = job.get("usage") or {}
    accounting = job.get("accounting") or {}
    if accounting.get("invocation_count"):
        lines.append(f"Token usage: {accounting['known_tokens']} known over {accounting['invocation_count']} model call(s)"
                     + (f" ({accounting['unknown_invocations']} usage unavailable)" if accounting["unknown_invocations"] else " (exact)"))
    elif usage.get("model_calls"):
        lines.append(f"Token usage: {usage.get('total_tokens')} total over {usage['model_calls']} model call(s)"
                     + ("" if usage.get("exact") else " (some usage unavailable)"))
    return "\n".join(lines)


class JobRunner:
    """A small thread pool in the David process. Jobs never block the conversation."""

    def __init__(self, root: Path, *, workers: int = DEFAULT_WORKERS, **execute_kwargs: Any):
        self.root = Path(root)
        self.execute_kwargs = execute_kwargs
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="david-job")
        self._futures: dict[str, Any] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._closed = False
        self._lock = threading.Lock()

    def submit(self, job_id: str):
        with self._lock:
            if self._closed:
                return None
            future = self._futures.get(job_id)
            if future is not None and not future.done():
                return future
            future = self.pool.submit(execute, self.root, job_id, **self.execute_kwargs)
            self._futures[job_id] = future
        # Outside the lock: a job that already finished runs the callback at once, in this thread.
        future.add_done_callback(lambda done, ident=job_id: self._recheck_later(ident, done))
        return future

    def recheck_delay(self, job: dict) -> float | None:
        """Seconds until a deferred job is tried again, or None when it waits for the next start."""
        if job.get("status") != "queued":
            return None
        if job.get("reminder"):
            return min(DEFERRAL_MAX_SECONDS, max(0.01, _scheduled_wait(job)))
        if job.get("automation"):
            return min(DEFERRAL_MAX_SECONDS, max(5, _scheduled_wait(job)))
        deferred = job.get("deferred") or {}
        if not deferred:
            if job.get("scheduled_at"):
                return min(DEFERRAL_MAX_SECONDS, max(0.01, _scheduled_wait(job)))
            return None
        if int(deferred.get("count") or 0) > MAX_DEFERRALS:
            return None
        try:
            due = dt.datetime.strptime(str(deferred.get("recheck_after")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            wait = (due - dt.datetime.now(dt.timezone.utc)).total_seconds()
        except ValueError:
            wait = DEFERRAL_MAX_SECONDS
        return float(min(DEFERRAL_MAX_SECONDS, max(DEFERRAL_MIN_SECONDS, wait, _scheduled_wait(job))))

    def _recheck_later(self, job_id: str, future) -> None:
        try:
            job = future.result()
        except Exception:
            # A receipt write can fail after the recurring job and its next due time were saved.
            job = load(self.root, job_id)
            if not (job.get("reminder") or job.get("automation")):
                return
        if job.get("automation") and (child := job["automation"].get("last_job")):
            occurrence = load(self.root, child)
            if occurrence["status"] == "queued" and not occurrence.get("deferred"):
                self.submit(child)
        with self._lock:
            if self._closed or self._futures.get(job_id) is not future:
                return
            self._futures.pop(job_id, None)
            if job.get("reminder") or job.get("scheduled_at") or job.get("automation"):
                # Edits can commit while an earlier runner callback is still pending.
                job = load(self.root, job_id)
            previous = self._timers.pop(job_id, None)
            if previous is not None:
                previous.cancel()
            delay = self.recheck_delay(job if isinstance(job, dict) else {})
            if delay is None:
                return
            timer = threading.Timer(delay, self._resubmit, args=(job_id,))
            timer.daemon = True
            self._timers[job_id] = timer
            timer.start()

    def _resubmit(self, job_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._timers.pop(job_id, None)
        self.submit(job_id)

    def start(self) -> dict:
        """Recover after a restart and resume queued work and undelivered notices."""
        report = recover(self.root)
        for job in list_jobs(self.root, limit=None):
            if job["status"] == "queued":
                self.submit(job["id"])
        notify = self.execute_kwargs.get("notify")
        if notify:
            self.pool.submit(retry_failed_notices, self.root, notify)
        return report

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            self._closed = True
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        self.pool.shutdown(wait=wait, cancel_futures=False)

