"""Typed observations from TTROS's existing scoped event and record owners."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import operator
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import aos_jobs
from business_brain_scope import load_registry

UTC = dt.timezone.utc
COMPARE = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt, "lte": operator.le,
           "gt": operator.gt, "gte": operator.ge, "before_now": None}
SCALAR = {"type": ["string", "number", "boolean", "null"], "maxLength": 500}
FIELDS = {
    "gmail": ("sender", "thread_id", "is_reply"),
    "calendar": ("record_id", "status", "start", "end", "title"),
    "drive": ("record_id", "name"),
    "brain": ("record_id", "reference"),
    "crm": ("record_id", "kind", "status", "stage", "due", "amount", "currency", "completed"),
    "crm_attention": ("record_id", "rule", "due", "severity"),
    "crm_threshold": ("count",),
    "job": ("record_id", "status"),
}
INTERVAL = {"gmail": 60, "calendar": 300, "drive": 60, "brain": 60, "crm": 30,
            "crm_attention": 300, "crm_threshold": 300, "job": 10}


@dataclass(frozen=True)
class Event:
    source: str
    identity: str
    fields: dict
    reference: str
    summary: str
    recorded_at: str
    receipt: str | None = None

    def matches(self, tests: list[dict], now: dt.datetime) -> bool:
        for test in tests:
            field, op = test["field"], test["op"]
            if field not in self.fields:
                return False
            actual, expected = self.fields[field], test.get("value")
            if op == "before_now":
                try:
                    if len(actual) == 10:
                        matches = dt.date.fromisoformat(actual) < now.date()
                    else:
                        matches = dt.datetime.fromisoformat(actual.replace("Z", "+00:00")) < now
                except (TypeError, ValueError):
                    matches = False
            elif type(actual) == type(expected) or (type(actual) in (int, float) and type(expected) in (int, float)):
                try:
                    matches = COMPARE[op](actual, expected)
                except TypeError:
                    matches = False
            else:
                matches = False
            if not matches:
                return False
        return True


def _time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _line(value, limit=320):
    return " ".join(str(value or "").split())[:limit]


def _audit_batch(root, relative, source, state, transform):
    """Checkpoint a complete JSONL line only after its disposition is durable."""
    path = root / relative
    if not path.is_file():
        if state.get("offset"):
            raise aos_jobs.JobError(f"The {source} audit disappeared.")
        return
    with path.open("rb") as handle:
        offset = state.get("offset", 0)
        stat = path.stat()
        if (offset > stat.st_size or state.get("inode", stat.st_ino) != stat.st_ino):
            raise aos_jobs.JobError(f"The {source} audit was replaced. Edit the trigger to establish a new cursor.")
        handle.seek(offset)
        for _ in range(200):
            line = handle.readline(131073)
            if len(line) > 131072:
                raise aos_jobs.JobError(f"The {source} audit entry exceeded its supported size.")
            if not line.endswith(b"\n"):
                break
            yield transform(json.loads(line)), {"offset": handle.tell(), "inode": stat.st_ino}


GMAIL_AUDIT = Path("capture/runtime/scopes/global/raw/records.jsonl")


def prepared_cursor(root, source):
    if source == "crm":
        from aos_crm import EVENTS
        path = root / EVENTS
    elif source == "gmail":
        path = root / GMAIL_AUDIT
    else:
        return {}
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    return {"offset": stat.st_size, "inode": stat.st_ino}


def gmail_events(root, state):
    gate = load_registry(root / "context/client_scope_registry.json")

    def project(row):
        if row.get("provider") != "gmail_composio" or row.get("client_scope") != "global":
            return
        if gate.resolve_capture_identity(sender_sha256=row["sender_sha256"],
                                         thread_sha256=row["thread_sha256"]).client_scope != "global":
            return
        reference = row["evidence_reference"]
        gate.validate_capture_evidence_reference("global", reference)
        headers = row.get("safe_headers") or {}
        return Event("gmail", row["record_id"], {"sender": row["sender_sha256"],
                     "thread_id": row["provider_thread_id"], "is_reply": headers.get("is_reply") is True},
                     reference, "Incoming mail: " + _line(row.get("subject")), row["timestamp"])

    yield from _audit_batch(root, GMAIL_AUDIT, "gmail", state, project)


def crm_events(root, state):
    from aos_crm import EVENTS

    def project(row):
        if row.get("client_scope") != "global" or row.get("basis") != "confirmed":
            return
        data = row["payload"]
        fields = {k: data[k] for k in FIELDS["crm"] if k in data}
        if row["kind"] == "follow_up.complete":
            fields["completed"] = True
        fields.update(record_id=row["record_id"], kind=row["kind"])
        return Event("crm", row["event_id"], fields, f"crm:{row['record_id']}",
                     f"CRM recorded {row['kind']} on {row['record_id']}.", row["at"])

    yield from _audit_batch(root, EVENTS, "crm", state, project)


def _hub(root, binding):
    from ttros_connections import Connections, ServiceIdentity

    hub = Connections(ServiceIdentity(root))
    who = {"workspace": "ttros-operator", "user": binding["user"], "method": "operator_service"}
    with hub.identity.db() as db:
        if not db.execute("SELECT 1 FROM memberships WHERE user=? AND workspace=?",
                          (who["user"], who["workspace"])).fetchone():
            raise aos_jobs.JobError("The automation's Connections membership was revoked.")
    return hub, who


def bind(root, service, context):
    from ttros_connections import ConnectionError, Connections, ServiceIdentity, operator_binding

    if context is None or context[1]["workspace"] == "local-preview":
        hub = Connections(ServiceIdentity(root))
        who, ident = operator_binding(hub, service)
    else:
        hub, who = context
        rows = [row for row in hub.rows(who) if row["service"] == service and row["enabled"]
                and row["status"] == "ACTIVE" and row["access"]["mode"] == "Automatic"]
        if len(rows) != 1:
            raise ConnectionError(f"Select one active Automatic {service} account in Connections.")
        ident = rows[0]["id"]
    binding = {"user": who["user"], "connection": ident, "service": service}
    _check_binding(hub, who, binding)
    return binding


def _check_binding(hub, who, binding):
    row = hub.row(who, binding["connection"])
    if (row["service"] != binding["service"] or not row["enabled"]
            or row["access"]["mode"] != "Automatic" or row["status"] != "ACTIVE"):
        raise aos_jobs.JobError("The pinned Automatic Connections account is unavailable.")


def connected_read(root, binding, operation, args, job):
    hub, who = _hub(root, binding)
    _check_binding(hub, who, binding)
    return hub.execute(who, binding["connection"], operation, args, item=job, expected_mode="read")


def calendar_projection(value):
    from ttros_connections import calendar_events

    rows = [{"id": str(row["id"]), "title": _line(row.get("summary"), 120), "status": row.get("status"),
             "start": row.get("start"), "end": row.get("end")} for row in calendar_events(value)]
    return sorted(rows, key=lambda row: ((row["start"] or {}).get("dateTime") or (row["start"] or {}).get("date") or "",
                                         row["id"]))


def _source(root, reference):
    import shared_brain_read
    gate = load_registry(root / "context/client_scope_registry.json")
    gate.validate_brain_pointer("global", reference)
    result = shared_brain_read.read(reference)
    return result if result.get("success") else None


def _drive(root, target):
    # Only indexed provenance published by the existing scoped Drive watcher.
    reference = f"business_brain:sources/intake/drive/{target}.md"
    result = _source(root, reference)
    if not result:
        return
    match = re.search(r"```json\s*(.*?)\s*```", result["content"], re.S)
    if not match:
        return
    row = json.loads(match[1])
    if row.get("active") is not True or row.get("drive_id") != target:
        return
    evidence = row.get("source_record") or reference
    if not isinstance(evidence, str) or not _source(root, evidence):
        return
    return Event("drive", f"{target}:{row['revision']}", {"record_id": target, "name": row.get("original_name", "")},
                 evidence, "Drive captured " + _line(row.get("original_name")), row["captured_at"])


def _snapshot(root, job, trigger, now):
    source, target = trigger["source"], trigger.get("target")
    if source == "job":
        try:
            related = aos_jobs.load(root, target)
        except aos_jobs.JobError:
            return
        owner = (related["executor"].get("capability_owner") or "")
        if (related.get("automation")
                or related["conversation_id"] != job["conversation_id"]
                or (owner and owner != job["automation"]["owner"])
                or related["status"] not in aos_jobs.TERMINAL):
            return
        summary = (related.get("result") or {}).get("summary") or related.get("detail") or related["status"]
        return Event(source, f"{target}:{related['status']}", {"record_id": target, "status": related["status"]},
                     f"job:{target}", f"{target} {related['status']}. " + _line(summary),
                     related.get("finished_at") or related["updated_at"])
    if source == "drive":
        return _drive(root, target)
    if source == "brain":
        from brain_memory import file_sha256
        from shared_brain_read import _indexed_target

        load_registry(root / "context/client_scope_registry.json").validate_brain_pointer("global", target)
        path = _indexed_target(target)
        if path and (digest := file_sha256(path)):
            return Event(source, digest, {"record_id": target, "reference": target}, target,
                         f"Indexed Brain evidence changed: {target}", now.isoformat())
        return
    from ttros_connections import calendar_events

    receipt = connected_read(root, trigger["binding"], "event", {"event_id": target}, job)
    row = next((row for row in calendar_events(receipt["result"]) if row["id"] == target), None)
    if not row:
        raise aos_jobs.JobError("Calendar did not return the exact selected event.")
    start = row.get("start") or {}
    end = row.get("end") or {}
    fields = {"record_id": target, "status": row.get("status", ""), "title": _line(row.get("summary")),
              "start": start.get("dateTime") or start.get("date"), "end": end.get("dateTime") or end.get("date")}
    revision = row.get("etag") or json.dumps(fields, sort_keys=True)
    return Event(source, revision, fields, f"calendar:{target}",
                 f"Calendar recorded {fields['title'] or 'the selected event'} ({fields['status']}).",
                 row.get("updated") or now.isoformat(), receipt.get("id"))


def observe(root, job, now):
    """Yield observations paired with the cursor that includes them."""
    from aos_reminders import LOCAL_ZONE
    from zoneinfo import ZoneInfo

    rule = job["automation"]
    trigger, cursor = rule["trigger"], rule["cursor"]
    source = trigger["source"]
    if source == "drive" and not trigger.get("target"):
        database = root / "logs/runtime/drive_watcher/state.sqlite3"
        if not database.exists():
            return
        name = next(test["value"] for test in trigger["where"] if test["field"] == "name" and test["op"] == "eq")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("""SELECT id FROM nodes WHERE dirty=0 AND captured_version IS NOT NULL
                                 AND json_extract(data,'$.name')=? ORDER BY id LIMIT 51""", (name,)).fetchall()
        if len(rows) > 50:
            raise aos_jobs.JobError("Too many Drive files have this exact name. Use an exact Drive ID.")
        for (ident,) in rows:
            event = _drive(root, ident)
            if not event or event.identity in cursor.get("active", []):
                continue
            cursor = {"active": [*cursor.get("active", [])[-149:], event.identity]}
            if not trigger.get("include_current") and _time(event.recorded_at) < _time(rule["armed_at"]):
                event = None
            yield event, cursor
        return
    if source in {"crm", "gmail"}:
        rows = crm_events(root, cursor) if source == "crm" else gmail_events(root, cursor)
        for event, following in rows:
            if event and _time(event.recorded_at) < _time(rule["armed_at"]):
                event = None
            yield event, following
        return
    if source in {"crm_attention", "crm_threshold"}:
        from aos_crm import project

        attention = project(root, scope="global", now=now.astimezone(ZoneInfo(LOCAL_ZONE)))["attention"]
        if source == "crm_attention":
            # A due date is part of the identity, so the same overdue action is
            # reported once and a deliberately rescheduled action can surface again.
            seen = cursor.get("active", [])
            current = []
            for row in attention:
                ident = row["id"] + ":" + str(row["due"])
                current.append(ident)
                if ident not in seen:
                    summary = " ".join((row["title"] + ".", row["reason"], row["next_permitted_action"]))
                    event = Event(source, ident, {key: row.get(key) for key in FIELDS[source]},
                                  "crm:" + row["record_id"], summary, now.isoformat())
                    yield event, {"active": [*seen, ident]}
                    seen = [*seen, ident]
            yield None, {"active": current}
        else:
            count = sum(row["rule"] == trigger["target"] for row in attention)
            generation = cursor.get("generation", 0)
            event = Event(source, str(generation + 1), {"count": count}, "crm:attention",
                          f"CRM recorded {count} items marked {trigger['target']}.", now.isoformat())
            matched = event.matches(trigger["where"], now)
            rising = matched and not cursor.get("matched")
            yield (event if rising else None), {"matched": matched, "generation": generation + int(rising)}
        return
    event = _snapshot(root, job, trigger, now)
    if event is None:
        yield None, cursor
        return
    first = not cursor.get("revision")
    is_new = cursor.get("revision") != event.identity
    before_arm = source in {"drive", "job"} and _time(event.recorded_at) < _time(rule["armed_at"])
    matches = event.matches(trigger["where"], now)
    # Snapshot predicates can become true with time while the external revision
    # stays the same. Report a rising edge or a newly matching revision.
    eligible = matches and (is_new or not cursor.get("matched"))
    if first and not trigger.get("include_current") and (source in {"brain", "calendar"} or before_arm):
        eligible = False
    yield (event if eligible else None), {"revision": event.identity, "matched": matches}


def brief(root, job, now, binding, timezone):
    """Collect verified calendar facts and existing Brain/ICM exception findings."""
    from zoneinfo import ZoneInfo
    from brain_memory import VAULT_ROOT
    from morning_brief_detector import detect
    from ttros_connections import ConnectionError

    local = now.astimezone(ZoneInfo(timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)
    calendar = []
    receipt_id = None
    calendar_error = None
    if binding:
        try:
            read = connected_read(root, binding, "events",
                                  {"time_min": start.isoformat(), "time_max": end.isoformat()}, job)
            receipt_id = read["id"]
            calendar = [{"title": row["title"], "start": row["start"], "ref": "calendar:" + row["id"]}
                        for row in calendar_projection(read["result"]) if row["status"] != "cancelled"]
        except (ConnectionError, aos_jobs.JobError) as exc:
            calendar_error = type(exc).__name__
    findings = detect(root=root, brain_root=VAULT_ROOT, client_scope="global", now=now).to_dict()
    evidence = [{"title": item["title"], "reason": item["reason"], "next": item["next_permitted_action"],
                 "refs": item["supporting_references"][:3]} for item in findings["findings"][:6]]
    lines = [f"{start:%A, %B %d}"]
    for row in calendar[:3]:
        when = row["start"] or {}
        clock = when.get("dateTime") or when.get("date") or "Time unavailable"
        lines.append(f"{clock} {row['title']} [{row['ref']}]")
    if calendar_error:
        lines.append("Calendar unavailable. Check Connections.")
    for row in evidence[:3]:
        refs = ", ".join(row["refs"][:1])
        lines.append(f"{row['title']}: {row['reason']} {row['next']} [{refs}]")
    actionable = bool(calendar or evidence or calendar_error)
    fingerprint = hashlib.sha256(json.dumps(
        [calendar, calendar_error, [{"id": item["finding_id"], "state": item["current_state"],
                                     "refs": item["supporting_references"], "next": item["next_permitted_action"]}
                                    for item in findings["findings"]]], sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return {"summary": "\n".join(lines) if actionable else "No calendar entries or outstanding exceptions in the checked sources.",
            "calendar": calendar, "calendar_receipt": receipt_id, "calendar_error": calendar_error,
            "findings": evidence, "finding_count": findings["finding_count"], "quiet": not actionable,
            "fingerprint": fingerprint}
