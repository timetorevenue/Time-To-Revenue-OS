"""Durable automation definitions and occurrence admission for the existing Jobs runner."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jsonschema

import aos_authority
import aos_job_events as events
import aos_jobs
import aos_reminders
from aos_job_ids import JOB_ID_RE

UTC = dt.timezone.utc
MAX_ERRORS = 5
LATE = dt.timedelta(hours=1)
READS = {"googlecalendar": ("event", "events", "availability")}
ZERO = {"exact": True, "model_calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
        "output_tokens": 0, "total_tokens": 0}
TEXT = {"type": "string", "minLength": 1, "maxLength": 500}
OBJECT = {"type": "object"}
DEFINITION = {
    "type": "object", "additionalProperties": False, "required": ["title", "trigger", "action"],
    "properties": {
        "title": {**TEXT, "maxLength": 160}, "expires_at": TEXT, "notify": {"enum": ["dashboard", "telegram"]},
        "trigger": {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {
            "type": {"enum": ["schedule", "event"]}, "when": TEXT, "source": {"enum": list(events.FIELDS)},
            "target": TEXT, "once": {"type": "boolean"}, "include_current": {"type": "boolean"},
            "where": {"type": "array", "maxItems": 8, "items": {
                "type": "object", "additionalProperties": False, "required": ["field", "op"], "properties": {
                    "field": TEXT, "op": {"enum": list(events.COMPARE)}, "value": events.SCALAR}}}
        }},
        "action": {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {
            "type": {"enum": ["notify", "brief", "agent", "plan", "read"]},
            "ref": TEXT, "input": OBJECT, "service": {"enum": list(READS)}, "operation": TEXT,
            "evidence_field": TEXT, "timezone": TEXT
        }},
    },
}


def _now():
    return dt.datetime.now(UTC)


def visible(job, owner):
    executor = job.get("executor") or {}
    return (not (job.get("automation") or job.get("automation_origin") or executor.get("external_mcp"))
            or bool(owner and executor.get("capability_owner") == owner))


def scope_problem(root, job):
    if job.get("automation_origin"):
        parent = aos_jobs.load(root, job["automation_origin"])
        if parent["status"] == "cancelled":
            return "The parent automation was cancelled."
        job = parent
    rule = job["automation"]
    if external := (job.get("executor") or {}).get("external_mcp"):
        if not aos_authority.external_job_operator(external["identity"]):
            return "MCP Jobs access was revoked."
    conversation = job["conversation_id"]
    if conversation.startswith("telegram-"):
        if not aos_authority.reminder_authorized(root, conversation, conversation.removeprefix("telegram-")):
            return "The operator Telegram authorization was revoked."
    member = rule.get("operator_member")
    if member:
        from ttros_connections import ServiceIdentity

        with ServiceIdentity(root).db() as db:
            if not db.execute("SELECT 1 FROM memberships WHERE user=? AND workspace='ttros-operator'",
                              (member,)).fetchone():
                return "The operator workspace membership was revoked."
    return ""


def _date(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed
    except (ValueError, AttributeError):
        pass
    raise aos_jobs.JobError("Use an ISO date and time with an explicit UTC offset.")


def _binding(root, service, context):
    from ttros_connections import ConnectionError

    try:
        return events.bind(root, service, context)
    except ConnectionError as exc:
        raise aos_jobs.JobError(str(exc)) from exc


def _ref(action):
    return action.get("ref", "").removeprefix("jobs.").removeprefix(action["type"] + ".")


def _named_ref(request, action):
    name, _, version = _ref(action).partition("@")
    names = re.finditer(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])(?:@([0-9]+(?:\.[0-9]+){2}))?",
                        request, re.I)
    return any(match[1] in (None, version) for match in names)


def _forge_input(root, action, evidence=None):
    import aos_david_forge
    from aos_agents.package import load_package
    from aos_agents.plans import load_plan

    kind, ref = action["type"], action["ref"]
    if problem := aos_david_forge.admission(root, kind, ref):
        raise aos_jobs.JobError(problem)
    plan = load_plan(ref, root) if kind == "plan" else None
    package = load_package(ref, root) if plan is None else None
    contract = plan["input_schema"] if plan is not None else package.input_schema
    hashes = ([load_package(step["agent"], root).content_sha256 for step in plan["steps"]]
              if plan is not None else [package.content_sha256])
    digest = hashlib.sha256(json.dumps([plan, hashes], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if action.get("release_sha256") and action["release_sha256"] != digest:
        raise aos_jobs.JobError("The released workflow changed. Edit the automation to admit the new version.")
    args = dict(action.get("input", {}))
    field = action.get("evidence_field")
    if field:
        if field in args or field not in contract.get("properties", {}):
            raise aos_jobs.JobError("Choose an unfilled field in the released input contract for the event evidence.")
        field_schema = contract["properties"][field]
        types = field_schema.get("type", [])
        value = (evidence or "business_brain:HOME.md")
        args[field] = [value] if types == "array" or "array" in types else value
    try:
        jsonschema.validate(args, contract)
    except jsonschema.ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        detail = exc.message if exc.validator == "required" else f"{path or 'input'} violates {exc.validator}."
        raise aos_jobs.JobError("Released input contract: " + detail) from None
    return args, digest


def configure(root, definition, *, owner, conversation_id, context, now, verified_chat="",
              forced_restrictions=(), previous=None):
    if not owner or len(json.dumps(definition, ensure_ascii=False).encode()) > 16000:
        raise aos_jobs.JobError("Automations require an authenticated operator scope and a bounded definition.")
    try:
        jsonschema.validate(definition, DEFINITION)
    except jsonschema.ValidationError as exc:
        raise aos_jobs.JobError("Invalid automation definition at " + (".".join(map(str, exc.path)) or "root") + ".") from None
    if verified_chat and not aos_authority.reminder_authorized(root, conversation_id, verified_chat):
        raise aos_jobs.JobError("This Telegram chat is no longer authorized.")
    raw = definition["trigger"]
    action = dict(definition["action"])
    trigger = dict(raw)
    due = now
    expires = definition.get("expires_at")
    if expires and _date(expires) <= now:
        raise aos_jobs.JobError("The automation expiration must be in the future.")
    restrictions = sorted(set(forced_restrictions) | {"no_connector_writes", "no_brain_writes"})
    if raw["type"] == "schedule":
        if set(raw) != {"type", "when"}:
            raise aos_jobs.JobError("Supply only the timing for a scheduled trigger.")
        if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", raw["when"]):
            instant = _date(raw["when"]).astimezone(UTC)
            if instant <= now:
                raise aos_jobs.JobError("Choose a future execution time.")
            timing = {"timezone": "UTC", "weekdays": [], "local_time": None, "next_at": instant.isoformat()}
        else:
            timing = aos_reminders.parse(raw["when"], now)
            if timing.pop("text"):
                raise aos_jobs.JobError("Supply a time without the workflow text.")
        trigger["schedule"] = timing
        due = _date(timing["next_at"])
        if expires and _date(expires) <= due:
            raise aos_jobs.JobError("The automation expires before its first run.")
    else:
        source, target = raw.get("source"), raw.get("target", "")
        if "when" in raw or source not in events.FIELDS:
            raise aos_jobs.JobError("Choose one supported event source.")
        where = [dict(test) for test in raw.get("where", [])]
        trigger["where"] = where
        for test in where:
            if test["field"] not in events.FIELDS[source] or (test["op"] != "before_now") != ("value" in test):
                raise aos_jobs.JobError(f"Invalid predicate for {source}.")
            if source == "gmail" and test["field"] == "sender":
                value = test.get("value")
                if test["op"] != "eq" or not isinstance(value, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                    raise aos_jobs.JobError("Use an exact sender email address.")
                from business_brain_scope import load_registry
                gate = load_registry(root / "context/client_scope_registry.json")
                test["value"] = gate.capture_identity_hash(value)
        if source == "gmail" and not any(row["field"] in {"sender", "thread_id"} and row["op"] == "eq" for row in where):
            raise aos_jobs.JobError("Gmail watches require an exact sender or thread.")
        if source == "crm_threshold" and (not where or not re.fullmatch(r"[a-z_]{2,80}", target)):
            raise aos_jobs.JobError("Choose a CRM attention rule and a count predicate.")
        if source == "crm" and any(test["field"] == "amount" for test in where) and not any(
                test["field"] == "currency" and test["op"] == "eq" and re.fullmatch(r"[A-Z]{3}", str(test.get("value")))
                for test in where):
            raise aos_jobs.JobError("An amount condition requires an exact currency.")
        if source == "job":
            if not JOB_ID_RE.fullmatch(target):
                raise aos_jobs.JobError("Use an exact Job ID.")
            original = aos_jobs.load(root, target)
            if (original.get("automation")
                    or original["conversation_id"] != conversation_id
                    or original["executor"].get("capability_owner") not in (None, "", owner)):
                raise aos_jobs.JobError("Select an execution Job from this conversation.")
        drive_name = source == "drive" and not target and any(
            test["field"] == "name" and test["op"] == "eq" and isinstance(test.get("value"), str) and test["value"]
            for test in where)
        if source in {"drive", "calendar"} and not drive_name and not re.fullmatch(r"[A-Za-z0-9_\-]{1,220}", target):
            raise aos_jobs.JobError(f"Use the exact {source} record ID.")
        if source == "brain":
            from business_brain_scope import load_registry
            load_registry(root / "context/client_scope_registry.json").validate_brain_pointer("global", target)
        elif source not in {"drive", "calendar", "job", "crm_threshold"} and target:
            raise aos_jobs.JobError("Specify a target using a predicate for this source.")
        if source in {"calendar", "gmail"} and "no_connector_reads" in restrictions:
            raise aos_jobs.JobError("This trigger requires connector reads.")
        if source == "calendar":
            trigger["binding"] = (previous or {}).get("trigger", {}).get("binding") or _binding(root, "googlecalendar", context)
        if source in {"gmail", "crm"} and raw.get("include_current"):
            raise aos_jobs.JobError("Gmail and CRM audit watches start with new events.")
    kind = action["type"]
    permitted = {"notify": set(), "brief": {"timezone"}, "read": {"service", "operation", "input"},
                 "agent": {"ref", "input", "evidence_field"}, "plan": {"ref", "input", "evidence_field"}}
    if set(action) - (permitted[kind] | {"type"}):
        raise aos_jobs.JobError(f"Unexpected fields for the {kind} action.")
    if kind in {"agent", "plan"}:
        action["ref"] = _ref(action)
        if not re.fullmatch(r"[a-z][a-z0-9._-]+@[0-9]+(?:\.[0-9]+){2}", action.get("ref", "")):
            raise aos_jobs.JobError("Use the exact released specialist or workflow name and version.")
        if action.get("evidence_field") and raw.get("source") not in {"drive", "brain"}:
            raise aos_jobs.JobError("An evidence field requires a scoped Drive or Brain source.")
        action["release_sha256"] = _forge_input(root, action)[1]
    if kind == "notify" and raw["type"] != "event":
        raise aos_jobs.JobError("Use a reminder for timed messages or choose a scheduled workflow.")
    if kind == "brief":
        timezone = action.get("timezone") or trigger.get("schedule", {}).get("timezone") or aos_reminders.LOCAL_ZONE
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise aos_jobs.JobError("Choose an IANA timezone for the brief.") from None
        action["timezone"] = timezone
    if kind == "read":
        from ttros_connections import OPERATIONS
        service, operation = action.get("service"), action.get("operation")
        if operation not in READS.get(service, ()):
            raise aos_jobs.JobError("Choose an allowlisted named read.")
        try:
            jsonschema.validate(action.get("input", {}), OPERATIONS[service][operation][1])
        except jsonschema.ValidationError:
            raise aos_jobs.JobError("Arguments did not match the named connector read.") from None
    if kind in {"read", "brief"}:
        if "no_connector_reads" in restrictions and kind == "read":
            raise aos_jobs.JobError("This task disallows connector reads.")
        if "no_connector_reads" not in restrictions:
            service = "googlecalendar" if kind == "brief" else action["service"]
            action["binding"] = ((previous or {}).get("action", {}).get("binding") or _binding(root, service, context))
            if action["binding"]["service"] != service:
                action["binding"] = _binding(root, service, context)
    notify = definition.get("notify", "telegram" if verified_chat else "dashboard")
    chat = ""
    if notify == "telegram":
        configured = aos_authority.load_notifications(root)["telegram"]
        chat = verified_chat or (configured[0] if len(configured) == 1 else "")
        if not chat or not aos_authority.reminder_authorized(root, f"telegram-{chat}", chat):
            raise aos_jobs.JobError("Choose Dashboard or configure one authorized operator Telegram DM.")
    principal = context[1] if context else {}
    member = (previous or {}).get("operator_member") or (
        principal.get("user") if principal.get("workspace") == "ttros-operator" else None)
    return {"version": 1, "owner": owner, "operator_member": member,
            "definition": definition, "trigger": trigger, "action": action,
            "armed_at": now.isoformat(), "expires_at": expires, "cursor": events.prepared_cursor(root, raw.get("source")),
            "next_check": due.isoformat(), "chat": chat, "restrictions": restrictions,
            "last_job": None, "failures": 0, "missed": 0, "revision": 1, "generation": 1}


def create(root, definition, **kwargs):
    return create_admission(root, definition, **kwargs)[0]


def create_admission(root, definition, *, owner, conversation_id, context=None, now=None, verified_chat="",
                     forced_restrictions=(), delivery_key="", external_mcp=None):
    with aos_jobs._locked(root, "AOS-0000-0000"):
        if sum(bool(job.get("automation")) and
               job["status"] not in aos_jobs.TERMINAL for job in aos_jobs.list_jobs(root, limit=None)) >= 80:
            raise aos_jobs.JobError("This runtime already has 80 active or paused automations.")
        now = now or _now()
        rule = configure(root, definition, owner=owner, conversation_id=conversation_id, context=context,
                         now=now, verified_chat=verified_chat, forced_restrictions=forced_restrictions)
        # The ordinary allocation lock publishes the initial state and its idempotency key together.
        # Relative times are persisted in the Job, but are excluded from the immutable executor spec.
        dedupe = ""
        if delivery_key:
            dedupe = (f"mcp:{conversation_id}:{delivery_key}" if external_mcp
                      else f"automation:{owner}:{delivery_key}")
        executor = {"kind": "automation", "definition": definition, "capability_owner": owner}
        if external_mcp:
            executor["external_mcp"] = external_mcp
        return aos_jobs.create(root, conversation_id=conversation_id, topic="automation", request=definition["title"],
                               executor=executor,
                               automation=rule, dedupe_key=dedupe, restrictions=rule["restrictions"])


def view(root, job):
    rule = job["automation"]
    last = rule.get("last_job")
    result = {k: job.get(k) for k in ("id", "status", "request", "detail", "receipts", "usage")}
    result.update(definition={**rule["definition"], "notify": "telegram" if rule["chat"] else "dashboard"},
                  revision=rule["revision"],
                  next_execution=rule["trigger"]["schedule"]["next_at"]
                    if rule["trigger"]["type"] == "schedule" and job["status"] == "queued" else None,
                  next_check=rule["next_check"] if job["status"] == "queued" else None,
                  waiting_for=rule["trigger"].get("source"), missed=rule["missed"],
                  last_event=rule.get("last_event"), last_check=rule.get("last_check"), last_job=None)
    if last:
        child = aos_jobs.load(root, last)
        result["last_job"] = {k: child.get(k) for k in ("id", "status", "result", "receipts", "usage", "detail",
                                                         "failure_class", "finished_at", "notification")}
    return result


def change(root, job_id, action, *, owner, definition=None, now=None, context=None,
           verified_chat="", expected_revision=None):
    now = now or _now()
    with aos_jobs._locked(root, job_id):
        job = aos_jobs.load(root, job_id)
        rule = job.get("automation")
        if not rule or rule["owner"] != owner:
            raise aos_jobs.JobError("No automation with that ID in your scope.")
        if expected_revision is not None and expected_revision != rule["revision"]:
            raise aos_jobs.JobError("The automation has changed. Reload it before editing.")
        if job["status"] in aos_jobs.TERMINAL:
            raise aos_jobs.JobError("This automation has already ended.")
        if action == "pause":
            job["status"] = "paused"
        elif action == "resume":
            if job["status"] != "paused":
                raise aos_jobs.JobError("Pause the automation before resuming it.")
            if schedule := rule["trigger"].get("schedule"):
                if schedule["weekdays"] and not rule.get("pending"):
                    schedule["next_at"] = aos_reminders.next_occurrence(schedule, now).isoformat()
                rule["next_check"] = now.isoformat() if rule.get("pending") else schedule["next_at"]
            else:
                rule["next_check"] = now.isoformat()
            job.update(status="queued", detail="")
            rule["failures"] = 0
        elif action == "cancel":
            job.update(status="cancelled", detail="Cancelled future occurrences.", finished_at=now.isoformat())
        elif action == "edit":
            if rule.get("pending"):
                raise aos_jobs.JobError("An occurrence is pending admission. Resume or cancel it before editing.")
            updated = configure(root, definition, owner=owner, conversation_id=job["conversation_id"], context=context,
                                now=now, verified_chat=verified_chat, forced_restrictions=rule["restrictions"], previous=rule)
            saved_trigger = rule["definition"]["trigger"]
            if saved_trigger == definition["trigger"]:
                updated.update(cursor=rule["cursor"], armed_at=rule["armed_at"], next_check=rule["next_check"],
                               trigger=rule["trigger"])
            updated.update(last_job=rule.get("last_job"), last_event=rule.get("last_event"), missed=rule["missed"],
                           revision=rule["revision"], generation=rule["generation"] + int(saved_trigger != definition["trigger"]))
            job.update(automation=updated, request=definition["title"], detail="")
            rule = updated
        else:
            raise aos_jobs.JobError("Use pause, resume, edit or cancel.")
        rule["revision"] += 1
        if action == "cancel":
            aos_jobs.write_receipt(root, job)
        aos_jobs.save(root, job)
        return job


def _executor(root, job, event):
    rule = job["automation"]
    action = rule["action"]
    kind = action["type"]
    common = {"automation_origin": job["id"], "capability_owner": rule["owner"],
              "automation_identity": [rule["generation"], event["source"], event["identity"]]}
    if external := (job.get("executor") or {}).get("external_mcp"):
        common["external_mcp"] = external
    if kind in {"agent", "plan"}:
        if (action.get("evidence_field") and event.get("source") == "drive"
                and event["reference"].startswith("business_brain:sources/intake/drive/")):
            raise aos_jobs.JobError("The Drive capture has no indexed document text for the workflow.")
        args, digest = _forge_input(root, action, event.get("reference"))
        return {**common, "kind": "agent", kind: action["ref"], "david_capability": True,
                "input": args, "automation_release_sha256": digest}
    evidence = {key: event[key] for key in ("reference", "summary", "fields", "source_receipt") if key in event}
    previous = rule["last_job"] if kind == "brief" and event["source"] == "schedule" else None
    return {**common, "kind": "automation_action", "action": action, "event": evidence, "previous_job": previous}


def _close(root, job, status, detail, now):
    job.update(status=status, detail=detail, finished_at=now.isoformat())
    aos_jobs.write_receipt(root, job)


def tick(root, job_id, *, now=None):
    started = time.monotonic()
    now = now or _now()
    with aos_jobs._locked(root, job_id):
        job = aos_jobs.load(root, job_id)
        rule = job["automation"]
        if job["status"] != "queued":
            return job
        trigger = rule["trigger"]
        schedule = trigger.get("schedule")
        pending = rule.get("pending")
        if not pending and rule["expires_at"] and _date(rule["expires_at"]) <= now:
            _close(root, job, "done", "Automation expired.", now)
            aos_jobs.save(root, job)
            return job
        if _date(rule["next_check"]) > now:
            return job
        previous = aos_jobs.load(root, rule["last_job"]) if rule.get("last_job") else None
        if previous and previous["status"] not in aos_jobs.SETTLED:
            return job
        try:
            if problem := scope_problem(root, job):
                _close(root, job, "blocked", problem, now)
                aos_jobs.save(root, job)
                return job
            if pending is None:
                if schedule:
                    due = _date(schedule["next_at"])
                    after = aos_reminders.next_occurrence(schedule, now).isoformat() if schedule["weekdays"] else ""
                    if now - due >= LATE:
                        rule["missed"] += 1
                        if after:
                            schedule["next_at"] = after
                            rule["next_check"] = after
                        else:
                            _close(root, job, "done", "Missed its one-off window.", now)
                    else:
                        pending = {"identity": due.isoformat(), "source": "schedule", "next": after, "cursor": {}}
                else:
                    rule["next_check"] = (now + dt.timedelta(seconds=events.INTERVAL[trigger["source"]])).isoformat()
                    for event, cursor in events.observe(root, job, now):
                        if event and event.matches(trigger["where"], now):
                            pending = {"identity": event.identity, "source": event.source, "reference": event.reference,
                                       "summary": event.summary, "fields": event.fields, "observed_at": now.isoformat(),
                                       "source_receipt": event.receipt, "recorded_at": event.recorded_at,
                                       "next": "" if trigger.get("once") else rule["next_check"], "cursor": cursor}
                            break
                        rule["cursor"] = cursor
                if pending:
                    pending["executor"] = _executor(root, job, pending)
                    rule["pending"] = pending
                    aos_jobs.save(root, job)
            if pending:
                # Stable pending data survives a crash between child allocation and cursor advancement.
                child, created = aos_jobs.create(
                    root, conversation_id=job["conversation_id"], topic="automation-run", request=job["request"],
                    executor=pending.get("executor") or _executor(root, job, pending),
                    chat_id=rule["chat"], restrictions=rule["restrictions"],
                    dedupe_key=f"automation-run:{job_id}:{rule['generation']}:{pending['source']}:{pending['identity']}")
                rule["cursor"] = pending["cursor"]
                # An unpublished cursor may recover a newly allocated child.
                # An old event replay must not replace a later last-result link.
                if created or not previous or aos_jobs.order_key(child) > aos_jobs.order_key(previous):
                    rule.update(last_job=child["id"], last_event={k: pending[k] for k in
                                ("identity", "source", "reference", "observed_at", "recorded_at", "source_receipt") if k in pending})
                rule.pop("pending", None)
                if pending["next"]:
                    rule["next_check"] = pending["next"]
                    if schedule:
                        schedule["next_at"] = pending["next"]
                else:
                    _close(root, job, "done", f"Dispatched occurrence {child['id']}.", now)
            rule["failures"] = 0
            if job["status"] == "queued":
                job["detail"] = ""
        except Exception as exc:
            # Provider payloads are untrusted and can contain private data.
            from ttros_connections import ConnectionError
            rule["failures"] += 1
            message = str(exc)[:240] if isinstance(exc, (aos_jobs.JobError, ConnectionError)) else type(exc).__name__
            job["detail"] = "Automation check failed: " + message
            rule["next_check"] = (now + dt.timedelta(seconds=min(900, 30 * 2 ** rule["failures"]))).isoformat()
            if rule["failures"] >= MAX_ERRORS:
                job["status"] = "paused"
        rule["last_check"] = {"at": now.isoformat(), "elapsed_seconds": round(time.monotonic() - started, 3)}
        aos_jobs.save(root, job)
        return job


def run_action(root, job):
    started = time.monotonic()
    spec = job["executor"]
    action, event = spec["action"], spec["event"]
    if action["type"] == "notify":
        output = {"summary": event["summary"] + " [" + event["reference"] + "]",
                  "source": event["reference"], "source_receipt": event.get("source_receipt"),
                  "fields": event["fields"]}
    elif action["type"] == "brief":
        output = events.brief(root, job, _now(), action.get("binding"), action["timezone"])
        if previous_id := spec.get("previous_job"):
            previous = (aos_jobs.load(root, previous_id).get("result") or {}).get("output", {}).get("value") or {}
            if output.get("fingerprint") and previous.get("fingerprint") == output["fingerprint"]:
                output.update(quiet=True, unchanged=True)
    else:
        from aos_capabilities import compact
        receipt = events.connected_read(root, action["binding"], action["operation"], action.get("input", {}), job)
        result = receipt["result"]
        if action["operation"] in {"event", "events"}:
            result = {"events": events.calendar_projection(result)[:25]}
        output = {"summary": f"Read {action['binding']['service']} {action['operation']}. Receipt {receipt['id']}.",
                  "receipt": receipt["id"], "data": compact(result, 8000)}
    return {"status": "ok", "output": output, "usage": {**ZERO, "elapsed_seconds": round(time.monotonic() - started, 3)}}


def intent(text):
    """Select the automation capability from the current operator instruction only."""
    text = re.sub(r"^(?:(?:hey[, ]+)?David[, ]+|please\s+|(?:can|could|would|will) you\s+)+",
                  "", text.strip(), flags=re.I)
    exact = re.match(r"^/capability automations\.(create|list|change)(?:\s|$)", text)
    if exact:
        return exact[1], None
    if "\n" in text or re.match(r"^(?:never|do not|don't|how|why|show me how)\b", text, re.I):
        return None
    control = re.match(r"^(pause|resume|cancel|edit|change|reschedule|update|move)\s+(?:my |the )?"
                       r"(?:\w+\s+){0,2}(?:automation|scheduled workflow|monitor|watch)\b", text, re.I)
    if control:
        verb = control[1].casefold()
        return ("change", verb if verb in {"pause", "resume", "cancel"} else "edit")
    if re.fullmatch(r"(?:list|show(?: me)?|what are)\s+(?:all\s+|my\s+|the\s+)*"
                    r"(?:automations|scheduled workflows|conditional monitors|watches)[.!?]*", text, re.I):
        return "list", None
    if re.match(r"^when\s+(?:did|was|were|is|are|will)\b", text, re.I):
        return None
    if re.match(r"^schedule\s+(?:(?:an?|my|the)\s+)?(?:meeting|appointment|call|event|booking)\b", text, re.I):
        return None
    if re.match(r"^(?:every\s+|daily\b|weekly\b|when\s+|whenever\s+|monitor\s+|watch\s+|"
                r"(?:let me know|tell me|notify me|alert me)\s+(?:when|if|whenever)\b|"
                r"(?:create|set up|add)\s+(?:an?\s+|my\s+)?(?:automation|monitor|watch)\b|"
                r"schedule\s+(?![a-z ]{0,30}\breminder\b))", text, re.I):
        return "create", None
    if re.match(r"^(?:run|review|check|send me|prepare)\b.*\b(?:every\s+|daily\b|weekly\b)", text, re.I):
        return "create", None
    return None


CHANGE_FIELDS = DEFINITION["properties"]
CHANGE = {"type": "object", "required": ["job_id", "operation"], "additionalProperties": False,
          "properties": {"job_id": TEXT, "operation": {"enum": ["pause", "resume", "cancel", "edit"]},
                         "changes": {"type": "object", "additionalProperties": False, "properties": CHANGE_FIELDS}}}


def capabilities(david, *, request, selection, owner, conversation_id, context, verified_chat, delivery_key):
    from aos_capabilities import Capability, CapabilityError

    def invoke(action, args):
        if action != selection[0]:
            raise CapabilityError("This operation requires your current automation instruction.")
        try:
            if action == "list":
                rows = [view(david.root, row) for row in aos_jobs.list_jobs(david.root, limit=None)
                        if (row.get("automation") or {}).get("owner") == owner]
                return {"automations": [{k: row[k] for k in ("id", "status", "request", "next_execution", "waiting_for")}
                                        for row in rows[:20]]}
            if action == "create":
                typed_action = args["action"]
                if (typed_action["type"] in {"agent", "plan"} and typed_action.get("ref")
                        and not _named_ref(request, typed_action)):
                    raise aos_jobs.JobError("Name the released specialist or workflow in your request.")
                restrictions = ("no_connector_reads",) if re.search(
                    r"\b(?:no|without|do not use)\s+(?:any\s+)?(?:connectors?|connector reads|connected apps)\b",
                    request, re.I) else ()
                job = create(david.root, args, owner=owner, conversation_id=conversation_id, context=context,
                             verified_chat=verified_chat, delivery_key=delivery_key, forced_restrictions=restrictions)
            else:
                job_id, operation = args["job_id"], args["operation"]
                if job_id.casefold() not in request.casefold() or (selection[1] and selection[1] != operation):
                    raise aos_jobs.JobError("Name the exact automation ID and operation in your request.")
                job = aos_jobs.load(david.root, job_id)
                if (job.get("automation") or {}).get("owner") != owner:
                    raise aos_jobs.JobError("No automation with that ID in your scope.")
                saved = job["automation"]
                definition = {**saved["definition"], **args.get("changes", {})}
                if operation == "edit":
                    edited = definition["action"]
                    if (edited["type"] in {"agent", "plan"} and edited.get("ref")
                            and edited != saved["definition"]["action"]
                            and not _named_ref(request, edited)):
                        raise aos_jobs.JobError("Name the released specialist or workflow in your edit.")
                job = change(david.root, job_id, operation, owner=owner, definition=definition,
                             context=context, verified_chat=verified_chat, expected_revision=saved["revision"])
            if david.runner is not None:
                david.runner.submit(job["id"])
            row = view(david.root, job)
            return {k: row[k] for k in ("id", "status", "request", "next_execution", "waiting_for", "detail")}
        except (aos_jobs.JobError, aos_reminders.ReminderError, OSError, PermissionError) as exc:
            raise CapabilityError(str(exc)) from None

    schemas = {"create": DEFINITION, "list": {"type": "object", "additionalProperties": False}, "change": CHANGE}
    prompts = {"create": "Create a scheduled workflow, condition or event watch for Casey.",
               "list": "List scheduled workflows, event watches and conditional monitors.",
               "change": "Pause, resume, edit or cancel an existing automation by its exact Jobs ID."}

    def adapter(action):
        return Capability("automations." + action, prompts[action], "operator", "existing scoped Jobs",
                          lambda: (schemas[action], OBJECT), lambda args: invoke(action, args),
                          auto_inspect=True, direct=True)

    return [adapter(action) for action in schemas]


def reply(data):
    if "automations" in data:
        return "\n".join(reply(row) for row in data["automations"]) or "No automations in this conversation."
    timing = data.get("next_execution")
    waiting = data.get("waiting_for")
    state = {"queued": "Active", "done": "Ended", "cancelled": "Cancelled", "paused": "Paused"}.get(
        data["status"], data["status"])
    line = f"{data['id']} · {state} · {data['request']}"
    if timing:
        return line + "\nNext run " + timing
    if waiting and data["status"] == "queued":
        return line + "\nWatching " + waiting.replace("_", " ") + "."
    return line


def hint(selection, root):
    action = selection[0]
    if action == "change":
        return "Send only the fields Casey asked to change. Use the automation ID exactly as written."
    import aos_david_forge

    available = [" ".join(entry.id.split(".", 2)[1:]) for entry in aos_david_forge.capabilities(root, None)
                 if entry.availability in {"available", "conditional"}]
    return (
        "Use only the current request. Ask a brief question for a materially unclear date, time, action, or target. "
        "Default to America/Creston. An unspecified every morning means every day at 8 AM. "
        "Use every day at 8 AM, every Monday at 9 AM, or tomorrow at 9 AM as 'when'. "
        "Use brief for calendar and commitments. Use notify for an event notice or review of a named job result. "
        "Default delivery to the current surface. Use only the operator Telegram DM for Telegram. "
        "An event can use 'once' for the next match only. Calendar watches need exact event IDs. "
        "Drive accepts an exact ID or a name eq predicate; use evidence_field for the released input receiving the source. "
        "Gmail needs a sender eq email or thread_id eq, and is_reply eq true for replies. "
        "Use crm_attention with rule eq overdue_follow_up for overdue follow-ups. For a numeric CRM attention threshold use "
        "crm_threshold with target overdue_follow_up (or another CRM rule) and a count predicate. "
        "For Jobs use the exact target job ID and status done or failed. Event snapshots skip historical records by default. "
        "Automations can run a released agent/plan, scoped read or brief, or notify Casey. "
        "For a released target, put only its name@version in ref and its category in type. "
        "If the request asks for an external send, publication, deployment, spending, destructive work, or any other "
        "unsupported effect, ask for a supported action and do not arm a substitute. "
        "Released targets are " + ", ".join(available) + "."
    )
