"""CRM & Sales for the Company Command Centre: one read projection and one writer.

Sources, and nothing else:
  queue/prospects.jsonl      the existing prospect ledger (append-only full snapshots, its own
                             schema and validator). Read for people, companies and pipeline.
  business_brain:prospects/  the canonical prospect pages, linked through the scope registry
  queue/crm/events.jsonl     CRM activity: interactions, next actions, follow-ups, records Casey
                             adds, links to jobs/receipts/evidence and value estimates. Append-only,
                             written only by ``record_event``.
  queue/jobs/                the canonical aos_jobs records a CRM item links to (read-only)

Writes go through ``record_event``: one validated event per change, idempotent on the
caller's ``request_key``, scoped by the client scope registry. A stage change on a
ledger-backed prospect is also appended to the prospect ledger as a new full snapshot, and
only when the whole resulting ledger passes the ledger's own validator; otherwise nothing
is written. Outreach-pilot rows keep their own event flow, so their stage is not changed here.

Every value carries a basis: confirmed (recorded evidence or Casey's own entry), estimate,
suggestion or draft. Nothing is inferred into a confirmed fact; a missing value is reported
missing. This module sends, publishes and contacts no one.

Revisit: when the prospect ledger schema, the status vocabulary or the scope registry changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import math
import re
from email.utils import parseaddr
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from aos_queue_storage import durable_replace_text
import crm_sales

EVENTS = Path("queue") / "crm" / "events.jsonl"
LEDGER = Path("queue") / "prospects.jsonl"
LEDGER_SCHEMA = Path("queue") / "prospects_schema.json"
VALIDATOR = Path(__file__).resolve().parent / "validate-prospect-ledger.py"

BASES = ("confirmed", "estimate", "suggestion", "draft")
INTERACTION_KINDS = ("email", "call", "meeting", "linkedin", "message", "note")
DIRECTIONS = ("inbound", "outbound", "internal")
# The prospect status vocabulary (queue/prospect_status_vocabulary.md) is the stage list.
STAGES = ("identified", "drafted", "sent", "touch_2", "touch_3", "replied_positive", "replied_negative",
          "no_response", "call_booked", "fit_call_done", "offer_sent", "won", "lost", "nurture",
          "withdrawn", "rejected", "do_not_contact")
CLOSED_STAGES = frozenset({"replied_negative", "no_response", "won", "lost", "rejected", "do_not_contact"})
# A stage that says Casey contacted or heard from someone needs that interaction on record first.
CONTACT_STAGES = frozenset({"sent", "touch_2", "touch_3", "replied_positive", "replied_negative", "call_booked",
                            "fit_call_done", "offer_sent", "won"})
TOUCH_DUE_DAYS = {"sent": 4, "touch_2": 5}
KINDS = ("offer.create", "purchase.create", "contact.create", "contact.consent", "contact.subscription", "organization.create", "opportunity.create", "relationship.link", "interaction.log",
         "stage.change", "next_action.set", "follow_up.complete", "value.set", "link.add", "opportunity.update", "handoff.prepare")
RECORD_PREFIXES = {"offer": "offer:", "purchase": "purchase:", "contact": "contact:", "organization": "org:", "opportunity": "opp:"}
ACTIVE_DAYS, DORMANT_DAYS = 14, 45
CRM_TIMEZONE = "America/Vancouver"
REF_RE = re.compile(r"^(?:queue/prospects\.jsonl#[A-Za-z0-9-]{3,120}|business_brain:[^\s]{3,300}|AOS-\d{4}-\d{3,}|"
                    r"queue/receipts/[A-Za-z0-9._/-]{3,200}|https://[^\s]{3,500}|(?:gmail|gcal|drive):[A-Za-z0-9_-]{3,200}|"
                    r"casey:dashboard-entry)$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


class CrmError(ValueError):
    """A CRM change was refused; nothing was written."""


class CrmConflict(CrmError):
    """The operator edited an older record version."""


# -- small helpers ------------------------------------------------------------------

def _now(now: dt.datetime | None = None) -> dt.datetime:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def slug(value: str) -> str:
    return SLUG_RE.sub("-", str(value or "").casefold()).strip("-")[:60]


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _registry(registry=None):
    if registry is not None:
        return registry
    from business_brain_scope import load_registry

    return load_registry()


def _scope(registry, client_scope: str) -> str:
    try:
        return registry.resolve_scope(client_scope).scope_id
    except PermissionError as exc:
        raise CrmError(f"scope refused: {exc}") from exc


@contextmanager
def _locked(root: Path):
    folder = Path(root) / EVENTS.parent
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "events.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ledger_validator():
    spec = importlib.util.spec_from_file_location("validate_prospect_ledger", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- ledger projection --------------------------------------------------------------

def ledger_rows(root: Path, scope: str) -> list[dict]:
    """Every snapshot in this scope, in ledger order (rows without a scope are global)."""
    return [row for row in _read_jsonl(Path(root) / LEDGER)
            if str(row.get("client_scope") or "global") == scope and row.get("prospect_id")]


def ledger_latest(root: Path, scope: str) -> dict[str, dict]:
    """Latest snapshot per prospect in this scope."""
    return {str(row["prospect_id"]): row for row in ledger_rows(root, scope)}


def _brain_link(registry, scope: str, pointer: str) -> str:
    if not pointer:
        return ""
    try:
        return registry.validate_brain_pointer(scope, pointer)
    except Exception:  # out of scope or not canonical: never linked
        return ""


def _ledger_records(rows: dict[str, dict], registry, scope: str,
                    snapshots: dict[str, list[dict]] | None = None) -> tuple[dict, dict, dict]:
    contacts, organizations, opportunities = {}, {}, {}
    for prospect_id, row in rows.items():
        ref = f"queue/prospects.jsonl#{prospect_id}"
        pilot = row.get("record_type") == "outreach_pilot_v1"
        company = str(row.get("company_name") or row.get("company") or "")
        org_id = "org:" + slug(row.get("company_domain") or company)
        person = str(row.get("person_name") or row.get("name") or "")
        contact_id = "contact:" + slug(prospect_id)
        brain = _brain_link(registry, scope, str(row.get("entity_page_path") or ""))
        sources = [ref] + ([brain] if brain else []) + ([row["signal_source_url"]] if row.get("signal_source_url") else [])
        organizations.setdefault(org_id, {
            "id": org_id, "type": "organization", "name": company, "domain": row.get("company_domain") or "",
            "basis": "confirmed", "origin": "prospect_ledger", "sources": [ref], "contacts": [], "opportunities": []})
        organizations[org_id]["contacts"].append(contact_id)
        organizations[org_id]["sources"] = sorted(set(organizations[org_id]["sources"]) | {ref})
        contacts[contact_id] = {
            "id": contact_id, "type": "contact", "name": person, "role": row.get("role") or "",
            "organization": org_id, "basis": "confirmed", "origin": "prospect_ledger", "sources": sources,
            "brain_page": brain, "do_not_contact": bool(row.get("do_not_contact") or row.get("opt_out")),
            "opportunities": [f"opp:{prospect_id}"]}
        opp_id = f"opp:{prospect_id}"
        organizations[org_id]["opportunities"].append(opp_id)
        due = row.get("next_touch_due")
        opportunities[opp_id] = {
            "id": opp_id, "type": "opportunity", "name": f"{company} — {person}".strip(" —"),
            "contact": contact_id, "organization": org_id, "stage": row.get("status") or "",
            "stage_date": row.get("status_date") or "", "stage_basis": "confirmed",
            "origin": "prospect_ledger", "ledger_kind": "outreach_pilot_v1" if pilot else "legacy",
            "prospect_id": prospect_id, "sources": sources, "brain_page": brain,
            "fit": {"score": row.get("score"), "tier": row.get("tier"), "basis": "estimate",
                    "note": "Prospecting score from the daily run; an estimate, not an outcome."},
            "signal": {"date": row.get("signal_date") or "", "url": row.get("signal_source_url") or "",
                       "summary": _text(row.get("wedge") or row.get("primary_signal") or "", 240)},
            "value": None,
            "cadence_due": due, "next_action": None, "follow_up_due": None,
            "suggested_next_action": ({"text": _text(row["next_action"], 240), "basis": "suggestion", "source": ref}
                                      if isinstance(row.get("next_action"), str) and row.get("next_action") else None),
            "do_not_contact": bool(row.get("do_not_contact") or row.get("opt_out")),
            "last_activity": row.get("last_contact_date") or row.get("status_date") or "",
            "interactions": [], "links": [], "history": [
                {"at": snap.get("status_date") or "", "stage": snap.get("status"), "basis": "confirmed", "source": ref}
                for snap in (snapshots or {}).get(prospect_id) or [row]]}
    return contacts, organizations, opportunities


# -- events -------------------------------------------------------------------------

def read_events(root: Path, scope: str) -> list[dict]:
    path = Path(root) / EVENTS
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise CrmError("CRM audit is unreadable; refusing to discard consent or suppression history") from exc
        if not isinstance(row, dict) or not {"event_id", "kind", "record_id", "client_scope", "at", "payload"} <= row.keys():
            raise CrmError("CRM audit event is malformed; recovery required")
        if row["client_scope"] == scope:
            rows.append(row)
    return rows


def _event_id(scope: str, kind: str, record_id: str, request_key: str) -> str:
    raw = json.dumps([scope, kind, record_id, request_key], separators=(",", ":"))
    return "CRM-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _check_refs(root: Path, registry, scope: str, refs: list, ledger: dict[str, dict]) -> list[str]:
    import aos_jobs

    checked = []
    for value in refs or []:
        ref = str(value or "").strip()
        if not REF_RE.fullmatch(ref):
            raise CrmError(f"unsupported source reference: {ref[:80]!r}")
        if ref.startswith("queue/prospects.jsonl#") and ref.split("#", 1)[1] not in ledger:
            raise CrmError(f"no prospect ledger record {ref} in scope {scope}")
        if ref.startswith("business_brain:"):
            try:
                ref = registry.validate_brain_pointer(scope, ref)
            except Exception as exc:
                raise CrmError(f"Brain reference refused for scope {scope}: {exc}") from exc
        if ref.startswith("AOS-"):
            try:
                aos_jobs.load(Path(root), ref)
            except aos_jobs.JobError as exc:
                raise CrmError(f"unknown job {ref}") from exc
        if ref.startswith("queue/receipts/"):
            target = (Path(root) / ref).resolve()
            if Path(root).resolve() not in target.parents or not target.is_file():
                raise CrmError(f"receipt not found: {ref}")
        checked.append(ref)
    return list(dict.fromkeys(checked))


def _validate_payload(kind: str, record_id: str, payload: dict, basis: str, known: dict, refs: list[str]) -> dict:
    """Return the normalised payload or raise. ``known`` maps record id -> projected record."""
    payload = dict(payload or {})
    if basis not in BASES:
        raise CrmError(f"basis must be one of {', '.join(BASES)}")
    def need(field: str, limit: int = 200) -> str:
        value = _text(payload.get(field), limit)
        if not value:
            raise CrmError(f"{field} is required")
        return value
    def date_field(field: str, required: bool = True) -> str:
        value = payload.get(field)
        if not value and not required:
            return ""
        parsed = _date(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")) else None
        if parsed is None:
            raise CrmError(f"{field} must be an ISO date (YYYY-MM-DD)")
        return parsed.isoformat()
    if kind in ("offer.create", "purchase.create"):
        noun = kind.split('.')[0]
        if basis != 'confirmed' or not record_id.startswith(RECORD_PREFIXES[noun]) or record_id in known:
            raise CrmError('Invalid or existing commerce record')
        if payload.get('site') != 'default-site' or payload.get('test_only') is not True:
            raise CrmError('Commerce is test-only for the registered EXCO site')
        out = {k: payload[k] for k in ('name', 'site', 'test_only', 'offer_id') if k in payload}
        out['name'] = need('name')
        if noun == 'purchase':
            for field in ('offer_id', 'session_id', 'account', 'fulfillment_job'):
                out[field] = need(field)
            if known.get(out['offer_id'], {}).get('type') != 'offer':
                raise CrmError('Unknown offer')
        return out
    if kind.endswith(".create"):
        noun = kind.split(".", 1)[0]
        if not record_id.startswith(RECORD_PREFIXES[noun]):
            raise CrmError(f"a new {noun} id starts with {RECORD_PREFIXES[noun]}")
        if record_id in known:
            raise CrmError(f"{record_id} already exists")
        if basis != "confirmed":
            raise CrmError("a record Casey adds is a confirmed entry; estimates belong on value.set")
        out = {"name": need("name")}
        if noun == "contact":
            out.update(role=_text(payload.get("role"), 120), organization=_text(payload.get("organization"), 80))
            if payload.get("email"):
                email = str(payload["email"]).strip().casefold()
                if parseaddr(email)[1] != email or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email) or len(email) > 254:
                    raise CrmError("email must be a valid address")
                if any(row.get("type") == "contact" and row.get("email") == email for row in known.values()):
                    raise CrmError("a contact with this email already exists in this scope")
                out["email"] = email
            if "consent" in payload:
                consent = payload["consent"]
                if not isinstance(consent, dict) or consent.get("guide_access") is not True or not isinstance(consent.get("follow_up"), bool):
                    raise CrmError("consent must record guide access and an explicit follow-up choice")
                out["consent"] = {"guide_access": True, "follow_up": consent["follow_up"],
                                  "notice_version": _text(consent.get("notice_version"), 80)}
            if "attribution" in payload:
                attribution = payload["attribution"]
                if not isinstance(attribution, dict):
                    raise CrmError("attribution must be a map")
                out["attribution"] = {key: _text(attribution.get(key), 80) for key in
                                      ("offer", "source_page", "utm_source", "utm_medium", "utm_campaign")}
            if out["organization"] and known.get(out["organization"], {}).get("type") != "organization":
                raise CrmError(f"unknown organization {out['organization']}")
        if noun == "organization":
            out["domain"] = _text(payload.get("domain"), 120).casefold()
        if noun == "opportunity":
            workflow = payload.get("workflow", "outreach")
            if workflow not in ("outreach", "sales"):
                raise CrmError("workflow must be outreach or sales")
            out.update(contact=_text(payload.get("contact"), 80), organization=_text(payload.get("organization"), 80),
                       stage=_text(payload.get("stage") or ("discovery" if workflow == "sales" else "identified"), 40),
                       workflow=workflow, owner=payload.get("owner", "casey"),
                       offer=_text(payload.get("offer"), 80))
            if out["stage"] not in (crm_sales.STAGES if workflow == "sales" else STAGES):
                raise CrmError(f"unknown stage {out['stage']}")
            if workflow == "sales" and out["stage"] != "discovery":
                raise CrmError("a new sales opportunity starts in discovery")
            if out["stage"] in CONTACT_STAGES:
                raise CrmError("a new opportunity starts before contact; log the interaction, then change the stage")
            for field, noun_type in (("contact", "contact"), ("organization", "organization")):
                if out[field] and known.get(out[field], {}).get("type") != noun_type:
                    raise CrmError(f"unknown {noun_type} {out[field]}")
            if not (out["contact"] or out["organization"]):
                raise CrmError("an opportunity names a contact or an organization")
            contact = known.get(out["contact"], {})
            if out["contact"] and not out["organization"]:
                out["organization"] = contact.get("organization") or ""
            if contact.get("organization") and out["organization"] != contact["organization"]:
                raise CrmError("contact does not belong to the selected organization")
            try:
                crm_sales.owner(out["owner"])
                crm_sales.duplicate(out["name"], out["contact"], out["organization"], known)
            except ValueError as exc:
                raise CrmError(str(exc)) from exc
        return out
    if record_id not in known:
        raise CrmError(f"unknown CRM record {record_id}")
    record = known[record_id]
    if kind == "contact.subscription":
        import aos_subscribers
        return aos_subscribers.validate_event(payload, record, refs, basis)
    if kind == "contact.consent":
        if record["type"] != "contact" or (record.get("consent") or {}).get("guide_access") is not True:
            raise CrmError("guide consent change requires an opted-in contact")
        if payload.get("follow_up") is not False:
            raise CrmError("this public route may only revoke follow-up consent")
        if not refs:
            raise CrmError("consent change requires a source")
        return {"follow_up": False, "notice_version": _text(payload.get("notice_version"), 80)}
    if kind == "relationship.link":
        if record["type"] != "contact":
            raise CrmError("relationships are recorded on a contact")
        other = need("organization", 80)
        if known.get(other, {}).get("type") != "organization":
            raise CrmError(f"unknown organization {other}")
        return {"organization": other, "role": _text(payload.get("role"), 120)}
    if record["type"] != "opportunity" and kind not in ("link.add",):
        raise CrmError(f"{kind} is recorded on an opportunity")
    if kind in ("opportunity.update", "handoff.prepare"):
        if basis != "confirmed":
            raise CrmError("opportunity edits and handoffs require confirmed operator entries")
        try:
            if kind == "handoff.prepare":
                return crm_sales.handoff(record, payload, known, refs)
            name = need("name")
            owner = crm_sales.owner(payload.get("owner", "casey"))
            crm_sales.duplicate(name, record.get("contact") or "", record.get("organization") or "", known, record_id)
            return {"name": name, "owner": owner}
        except ValueError as exc:
            raise CrmError(str(exc)) from exc
    if kind == "interaction.log":
        channel = need("channel", 20)
        direction = need("direction", 20)
        if channel not in INTERACTION_KINDS or direction not in DIRECTIONS:
            raise CrmError(f"channel is one of {', '.join(INTERACTION_KINDS)}; direction one of {', '.join(DIRECTIONS)}")
        if basis not in ("confirmed", "draft"):
            raise CrmError("an interaction either happened (confirmed) or is a draft not yet sent")
        if basis == "confirmed" and refs == []:
            raise CrmError("a confirmed interaction cites its source (a message, note, or casey:dashboard-entry)")
        return {"channel": channel, "direction": direction, "summary": need("summary", 600),
                "occurred_on": date_field("occurred_on")}
    if kind == "stage.change":
        stage = need("stage", 40)
        if record.get("workflow") == "sales":
            if basis != "confirmed":
                raise CrmError("a stage is a confirmed fact")
            try:
                return crm_sales.stage_change(record, payload, _text(payload.get("reason"), 300))
            except ValueError as exc:
                raise CrmError(str(exc)) from exc
        if stage not in STAGES:
            raise CrmError(f"unknown stage {stage}")
        if basis != "confirmed":
            raise CrmError("a stage is a confirmed fact; suggestions stay suggestions")
        if stage == record.get("stage"):
            raise CrmError(f"{record_id} is already {stage}")
        if stage == "lost" and not _text(payload.get("reason"), 300):
            raise CrmError("lost requires a reason")
        if record.get("ledger_kind") == "outreach_pilot_v1":
            raise CrmError("outreach-pilot prospects change stage through the pilot's own event flow, not here")
        rules = _ledger_validator()
        prior = str(record.get("stage") or "")
        if stage != "do_not_contact" and (prior in rules.TERMINAL or stage not in rules.ALLOWED_TRANSITIONS.get(prior, set())):
            allowed = sorted(rules.ALLOWED_TRANSITIONS.get(prior, set()))
            raise CrmError(f"{prior} cannot move to {stage}; allowed next stages: {', '.join(allowed) or 'none (closed)'}")
        if stage in CONTACT_STAGES:
            interaction = _text(payload.get("interaction"), 40)
            match = next((row for row in record.get("interactions") or [] if row["event_id"] == interaction), None)
            if not match or match["basis"] != "confirmed":
                raise CrmError(f"moving to {stage} needs the confirmed interaction it rests on (log it first)")
        return {"stage": stage, "reason": _text(payload.get("reason"), 300), "interaction": _text(payload.get("interaction"), 40)}
    if kind == "next_action.set":
        if basis not in ("confirmed", "suggestion"):
            raise CrmError("a next action is Casey's decision (confirmed) or a suggestion")
        owner = payload.get("owner", "casey")
        if owner != "casey":
            raise CrmError("owner must be casey (the current CRM operator)")
        return {"text": need("text", 300), "due": date_field("due"), "owner": owner}
    if kind == "follow_up.complete":
        if basis != "confirmed" or not record.get("next_action") or record["next_action"].get("completed_at"):
            raise CrmError("only an open confirmed next action can be completed")
        return {"note": _text(payload.get("note"), 300)}
    if kind == "value.set":
        if basis not in ("confirmed", "estimate"):
            raise CrmError("a value is confirmed (signed/quoted) or an estimate")
        if isinstance(payload.get("amount"), bool):
            raise CrmError("amount must be a number")
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError) as exc:
            raise CrmError("amount must be a number") from exc
        currency = str(payload.get("currency") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency) or not math.isfinite(amount) or amount < 0 or amount > 1e12:
            raise CrmError("currency must be a 3-letter code; amount must be finite and between 0 and 1,000,000,000,000")
        if not refs:
            raise CrmError("a value cites where it comes from")
        return {"amount": round(amount, 2), "currency": currency, "note": _text(payload.get("note"), 200)}
    if kind == "link.add":
        if not refs:
            raise CrmError("link.add needs at least one reference")
        return {"note": _text(payload.get("note"), 200)}
    raise CrmError(f"unknown CRM event kind {kind}")


def record_event(root: Path, *, client_scope: str, kind: str, record_id: str, payload: dict | None = None,
                 basis: str = "confirmed", source_refs: list | None = None, request_key: str,
                 actor: str = "casey", now: dt.datetime | None = None, registry=None,
                 expected_revision: str | None = None) -> tuple[dict, bool]:
    """Validate and append one CRM event. Returns (event, created); a repeat returns the first event."""
    root = Path(root)
    registry = _registry(registry)
    scope = _scope(registry, client_scope)
    kind = str(kind or "")
    if kind in ("offer.create", "purchase.create") and scope != "global":
        raise CrmError("EXCO commerce records require the registered global site scope")
    if kind not in KINDS:
        raise CrmError(f"unknown CRM event kind {kind!r}")
    request_key = _text(request_key, 160)
    if len(request_key) < 6:
        raise CrmError("request_key (6+ characters) is required so a retry never records twice")
    record_id = _text(record_id, 140)
    event_id = _event_id(scope, kind, record_id, request_key)
    requested = {"kind": kind, "record_id": record_id, "payload": payload or {}, "basis": basis,
                 "source_refs": list(source_refs or [])}
    with _locked(root):
        for row in read_events(root, scope):
            if row.get("event_id") == event_id:
                if row.get("request_sha256") != _digest(requested):
                    raise CrmError("request_key was already used for a different change; nothing recorded")
                return row, False
        ledger = ledger_latest(root, scope)
        state = project(root, scope=scope, registry=registry, now=now, ledger=ledger)
        known = {**state["contacts"], **state["organizations"], **state["opportunities"], **state["offers"], **state["purchases"]}
        if expected_revision is not None and known.get(record_id, {}).get("revision") != expected_revision:
            raise CrmConflict("This CRM record changed. Reload the record before saving; nothing recorded.")
        refs = _check_refs(root, registry, scope, source_refs or [], ledger)
        clean = _validate_payload(kind, record_id, payload or {}, basis, known, refs)
        at = _now(now)
        event = {"event_id": event_id, "kind": kind, "record_id": record_id, "client_scope": scope, "at": _iso(at),
                 "actor": _text(actor, 40) or "casey", "basis": basis, "payload": clean, "source_refs": refs,
                 "request_sha256": _digest(requested)}
        if kind == "stage.change" and known[record_id].get("origin") == "prospect_ledger":
            event["ledger_snapshot"] = _append_ledger_snapshot(root, ledger[known[record_id]["prospect_id"]],
                                                               clean["stage"], at.date(), event_id)
        path = root / EVENTS
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        durable_replace_text(path, existing + json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event, True


def _append_ledger_snapshot(root: Path, prior: dict, stage: str, today: dt.date, event_id: str) -> dict:
    """Append the next full snapshot only if the whole resulting ledger validates."""
    row = dict(prior)
    row.update(status=stage, status_date=max(today.isoformat(), str(prior.get("status_date") or "")),
               do_not_contact=stage == "do_not_contact")
    validator = _ledger_validator()
    expected = validator.EXPECTED_TOUCH_COUNT.get(stage)
    if expected is not None:
        row["touch_count"] = expected
    status_date = dt.date.fromisoformat(row["status_date"])
    row["next_touch_due"] = ((status_date + dt.timedelta(days=TOUCH_DUE_DAYS[stage])).isoformat()
                             if stage in TOUCH_DUE_DAYS else None)
    row["outcome_notes"] = _text(f"{(prior.get('outcome_notes') or '')} [{event_id}] stage {stage} logged via CRM".strip(), 600)
    path = Path(root) / LEDGER
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    candidate = path.with_name(f".{path.name}.crm-candidate")
    candidate.write_text(existing + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    try:
        verdict = validator.validate_ledger(candidate, Path(root) / LEDGER_SCHEMA)
    finally:
        candidate.unlink(missing_ok=True)
    if verdict["status"] != "PASS":
        raise CrmError("the prospect ledger validator refused this stage change; nothing was written: "
                       + "; ".join(verdict["errors"][-3:]))
    durable_replace_text(path, existing + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"path": str(LEDGER), "prospect_id": row["prospect_id"], "status": stage, "status_date": row["status_date"]}


# -- projection ---------------------------------------------------------------------

def _engagement(opportunity: dict, today: dt.date) -> str:
    if opportunity["stage"] in CLOSED_STAGES:
        return "closed"
    last = _date(opportunity.get("last_interaction"))
    if last is None:
        return "no recorded interaction"
    age = (today - last).days
    return "active" if age <= ACTIVE_DAYS else "cooling" if age <= DORMANT_DAYS else "dormant"


def project(root: Path, *, scope: str = "global", registry=None, now: dt.datetime | None = None,
            ledger: dict[str, dict] | None = None, job_reader: Callable[[str], dict | None] | None = None) -> dict:
    """The CRM as the Dashboard shows it, built only from the sources above."""
    root = Path(root)
    registry = _registry(registry)
    scope = _scope(registry, scope)
    today = _now(now).astimezone(ZoneInfo(CRM_TIMEZONE)).date()
    snapshots: dict[str, list[dict]] = {}
    for row in ledger_rows(root, scope):
        snapshots.setdefault(str(row["prospect_id"]), []).append(row)
    ledger = {pid: rows[-1] for pid, rows in snapshots.items()} if ledger is None else ledger
    contacts, organizations, opportunities = _ledger_records(ledger, registry, scope, snapshots)
    offers, purchases = {}, {}
    events = read_events(root, scope)
    for event in events:
        kind, rid, data = event["kind"], event["record_id"], event.get("payload") or {}
        meta = {"event_id": event["event_id"], "at": event["at"], "basis": event["basis"], "sources": event.get("source_refs") or []}
        business_day = dt.datetime.fromisoformat(event["at"]).astimezone(ZoneInfo(CRM_TIMEZONE)).date().isoformat()
        if kind in ('offer.create', 'purchase.create'):
            bucket = offers if kind == 'offer.create' else purchases
            bucket[rid] = {'id': rid, 'type': kind.split('.')[0], **data, **meta}
        elif kind == "organization.create":
            organizations[rid] = {"id": rid, "type": "organization", "name": data["name"], "domain": data.get("domain") or "",
                                  "basis": "confirmed", "origin": "crm_entry", "sources": meta["sources"] or ["casey:dashboard-entry"],
                                  "contacts": [], "opportunities": []}
        elif kind == "contact.create":
            contacts[rid] = {"id": rid, "type": "contact", "name": data["name"], "role": data.get("role") or "",
                             "email": data.get("email") or "", "consent": data.get("consent"),
                             "attribution": data.get("attribution"),
                             "organization": data.get("organization") or "", "basis": "confirmed", "origin": "crm_entry",
                             "sources": meta["sources"] or ["casey:dashboard-entry"], "brain_page": "",
                             "do_not_contact": False, "opportunities": []}
            if data.get("organization") in organizations:
                organizations[data["organization"]]["contacts"].append(rid)
        elif kind == "contact.subscription" and rid in contacts:
            import aos_subscribers
            aos_subscribers.apply_event(contacts[rid], event)
        elif kind == "contact.consent" and rid in contacts:
            contacts[rid]["consent"] = {**(contacts[rid].get("consent") or {}), **data}
        elif kind == "opportunity.create":
            opportunities[rid] = {"id": rid, "type": "opportunity", "name": data["name"], "contact": data.get("contact") or "",
                                  "organization": data.get("organization") or "", "stage": data["stage"],
                                  "workflow": data.get("workflow", "outreach"), "owner": data.get("owner", "casey"),
                                  "offer": data.get("offer") or "",
                                  "stage_date": business_day if data.get("workflow") == "sales" else event["at"][:10], "stage_basis": "confirmed", "origin": "crm_entry",
                                  "ledger_kind": "", "prospect_id": "", "sources": meta["sources"] or ["casey:dashboard-entry"],
                                  "brain_page": "", "fit": None, "signal": None, "value": None, "cadence_due": None,
                                  "next_action": None, "follow_up_due": None, "suggested_next_action": None,
                                  "do_not_contact": False, "last_activity": event["at"][:10], "interactions": [], "links": [],
                                  "history": [{"at": business_day if data.get("workflow") == "sales" else event["at"][:10], "stage": data["stage"], "basis": "confirmed",
                                               "source": event["event_id"]}]}
            for field, bucket in (("contact", contacts), ("organization", organizations)):
                if data.get(field) in bucket:
                    bucket[data[field]]["opportunities"].append(rid)
        elif kind == "relationship.link" and rid in contacts:
            contacts[rid]["organization"] = data["organization"]
            contacts[rid].setdefault("relationships", []).append({"organization": data["organization"], "role": data.get("role") or "", **meta})
            if rid not in organizations.get(data["organization"], {}).get("contacts", [rid]):
                organizations[data["organization"]]["contacts"].append(rid)
        elif rid in opportunities:
            opp = opportunities[rid]
            if kind == "opportunity.update":
                opp.update(name=data["name"], owner=data["owner"])
            elif kind == "handoff.prepare":
                opp["handoff"] = {**data, **meta}
            elif kind == "interaction.log":
                opp["interactions"].append({**meta, **data})
                if event["basis"] == "confirmed":
                    opp["last_interaction"] = max(str(opp.get("last_interaction") or ""), data["occurred_on"])
                    opp["last_activity"] = max(str(opp.get("last_activity") or ""), data["occurred_on"])
            elif kind == "stage.change":
                stage_day = business_day if opp.get("workflow") == "sales" else event["at"][:10]
                # For a ledger prospect the ledger's latest snapshot is the stage (this change was mirrored
                # into it, and a later workflow snapshot must win); the event adds the history and reason.
                mirrored = event.get("ledger_snapshot") or {}
                entry = next((row for row in opp["history"] if mirrored and row["stage"] == mirrored.get("status")
                              and row["at"] == mirrored.get("status_date") and not row.get("event_id")), None)
                if entry is not None:  # the ledger snapshot this change wrote: add the why, not a duplicate
                    entry.update(event_id=event["event_id"], reason=data.get("reason") or "", ledger=mirrored)
                else:
                    opp["history"].append({"at": stage_day, "stage": data["stage"], "basis": "confirmed",
                                           "source": event["event_id"], "event_id": event["event_id"],
                                           "reason": data.get("reason") or "", "ledger": mirrored or None})
                if opp.get("origin") != "prospect_ledger":
                    opp.update(stage=data["stage"], stage_date=stage_day, stage_basis="confirmed")
                if data["stage"] in ("won", "lost") and opp["stage"] == data["stage"]:
                    opp["outcome_reason"] = data.get("reason") or ""
            elif kind == "next_action.set":
                action = {"text": data["text"], "due": data["due"], "owner": data.get("owner", "casey"), **meta}
                if event["basis"] == "confirmed":
                    opp["next_action"] = action
                    opp["follow_up_due"] = data["due"]
                else:
                    opp["suggested_next_action"] = action
            elif kind == "follow_up.complete":
                if opp.get("next_action"):
                    opp["next_action"] = {**opp["next_action"], "completed_at": event["at"], "completion": data.get("note") or ""}
                opp["follow_up_due"] = None
            elif kind == "value.set":
                opp["value"] = {"amount": data["amount"], "currency": data["currency"], "basis": event["basis"],
                                "note": data.get("note") or "", **meta}
            elif kind == "link.add":
                opp["links"].append({"refs": meta["sources"], "note": data.get("note") or "", "at": event["at"],
                                     "event_id": event["event_id"]})
                opp["sources"] = list(dict.fromkeys(opp["sources"] + meta["sources"]))
        elif kind == "link.add" and (rid in contacts or rid in organizations):
            record = contacts.get(rid) or organizations[rid]
            record["sources"] = list(dict.fromkeys(record["sources"] + meta["sources"]))
    read_job = job_reader or (lambda job_id: _read_job(root, job_id))
    for opp in opportunities.values():
        opp["engagement"] = _engagement(opp, today)
        opp["open"] = opp["stage"] not in CLOSED_STAGES
        jobs = []
        for link in opp["links"]:
            for ref in link["refs"]:
                if ref.startswith("AOS-"):
                    job = read_job(ref)
                    if job:
                        jobs.append(job)
        opp["jobs"] = jobs
        opp["pipeline_group"] = crm_sales.group(opp["stage"])
        stage_day = _date(opp.get("stage_date"))
        opp["stalled"] = bool(opp["open"] and stage_day and (today - stage_day).days >= 14)
    # Historical guide-interest rows remain readable, but are not sales pipeline.
    pipeline_opportunities = {key: row for key, row in opportunities.items()
        if not (key.startswith("opp:guide-") and row.get("offer") == "4-prompt-project-workflow" and row.get("workflow") == "outreach" and
                row.get("stage") == "identified" and not row.get("interactions"))}
    for key, row in opportunities.items():
        row["classification"] = "opportunity" if key in pipeline_opportunities else "guide_interest"
    attention = _attention(pipeline_opportunities, contacts, today)
    import aos_subscribers
    subscribers = {}
    for contact in contacts.values():
        sub = contact.get("subscription")
        if sub:
            status = sub.get("status", "guide_only")
            if status == "pending" and _now(now) >= dt.datetime.fromisoformat(sub["expires_at"]):
                status = "expired"
            subscribers[contact["id"]] = {"contact_id": contact["id"], "name": contact["name"],
                "status": status, "email_verified": bool(sub.get("email_verified")),
                "marketing_eligible": aos_subscribers.eligible(contact),
                "suppression_reason": sub.get("suppression_reason"),
                "delivery_review_required": bool(sub.get("delivery_review_required")),
                "notice_version": sub.get("notice_version"), "verified_at": sub.get("verified_at"),
                "delivery_status": sub.get("delivery_status"), "last_event_id": sub.get("last_event_id")}

    for record in [*contacts.values(), *organizations.values(), *opportunities.values()]:
        record["revision"] = _digest(record)
    pipeline: dict[str, int] = {}
    for opp in pipeline_opportunities.values():
        pipeline[opp["stage"]] = pipeline.get(opp["stage"], 0) + 1
    return {"client_scope": scope, "as_of": today.isoformat(), "timezone": CRM_TIMEZONE,
            "read_at": _iso(_now(now)), "contacts": contacts, "organizations": organizations,
            "opportunities": opportunities, "offers": offers, "purchases": purchases, "attention": attention, "pipeline": pipeline,
            "pipeline_summary": crm_sales.summary(pipeline_opportunities, today),
            "subscribers": subscribers,
            "counts": {"contacts": len(contacts), "organizations": len(organizations),
                       "opportunities": len(pipeline_opportunities), "open_opportunities": sum(o["open"] for o in pipeline_opportunities.values()),
                       "subscribers": sum(s["marketing_eligible"] for s in subscribers.values()),
                       "subscription_pending": sum(s["status"] == "pending" for s in subscribers.values()),
                       "subscription_suppressed": sum(s["status"] == "suppressed" for s in subscribers.values()),
                       "subscription_review": sum(s["delivery_review_required"] for s in subscribers.values()),
                       "legacy_guide_interests": len(opportunities) - len(pipeline_opportunities),
                       "attention": len(attention), "events": len(events)},
            "sources": {"prospect_ledger": {"path": str(LEDGER), "records": len(ledger)},
                        "crm_events": {"path": str(EVENTS), "records": len(events)}},
            "value_note": "Opportunity values appear only where Casey recorded one; none are estimated automatically."}


def _read_job(root: Path, job_id: str) -> dict | None:
    import aos_jobs

    try:
        job = aos_jobs.load(root, job_id)
    except aos_jobs.JobError:
        return None
    return {"id": job["id"], "status": job["status"], "lifecycle": aos_jobs.lifecycle(job["status"]),
            "request": _text(job.get("request"), 160), "receipts": [row.get("path") for row in job.get("receipts") or []],
            "summary": _text((job.get("result") or {}).get("summary"), 240)}


def _attention(opportunities: dict, contacts: dict, today: dt.date) -> list[dict]:
    items = []
    def add(opp, rule, severity, reason, action, due=None, job=None):
        contact = contacts.get(opp.get("contact") or "", {})
        linked_sources = [ref for link in reversed(opp.get("links") or []) for ref in link["refs"]]
        items.append({"id": f"{rule}:{opp['id']}" + (f":{job['id']}" if job else ""), "rule": rule, "severity": severity, "record_id": opp["id"],
                      "title": opp["name"] or contact.get("name") or opp["id"], "reason": reason, "due": due,
                      "next_permitted_action": action, "sources": list(dict.fromkeys(linked_sources + opp["sources"]))[:5],
                      "owner": (opp.get("next_action") or {}).get("owner") or opp.get("owner"),
                      "status": rule, "last_interaction": opp.get("last_interaction"),
                      "job_id": job["id"] if job else None})
    for opp in opportunities.values():
        if not opp["open"]:
            if opp["stage"] == "won" and not opp.get("handoff"):
                add(opp, "handoff_needed", "medium", "Won opportunity needs a delivery handoff.", "Review scope and prepare a handoff.")
            continue
        if opp.get("do_not_contact"):
            continue
        # An explicit action supersedes ledger cadence, including when completed.
        due = (_date(opp.get("follow_up_due")) if opp.get("next_action")
               else _date(opp.get("cadence_due")))
        if due and due < today:
            add(opp, "overdue_follow_up", "high", f"Follow-up was due {due.isoformat()} ({(today - due).days} days overdue).",
                "Log what happened, or set a new follow-up date.", due.isoformat())
        elif due and due == today:
            add(opp, "follow_up_due_today", "medium", "Follow-up is due today.", "Log the follow-up when done.", due.isoformat())
        elif due and due <= today + dt.timedelta(days=7):
            add(opp, "upcoming_follow_up", "low", f"Follow-up is due {due.isoformat()}.",
                "Review or edit the next action.", due.isoformat())
        if opp["stage"] == "drafted":
            since = _date(opp.get("stage_date"))
            age = f" since {since.isoformat()} ({(today - since).days} days)" if since else ""
            add(opp, "draft_awaiting_casey", "medium", f"Outreach drafts are waiting for Casey's review{age}; nothing has been sent.",
                "Review the draft, then log a send you made yourself, or close the opportunity.")
        if not opp.get("next_action") or opp["next_action"].get("completed_at"):
            if opp.get("last_interaction") or opp["stage"] not in ("drafted", "identified"):
                add(opp, "no_next_action", "low", "Open opportunity has no next action.", "Set a next action and due date.")
        if opp["engagement"] == "dormant":
            add(opp, "dormant", "low", f"No confirmed interaction in more than {DORMANT_DAYS} days.",
                "Decide: follow up, nurture, or close.")
        if opp.get("stalled") and opp["pipeline_group"] in ("qualified", "proposal", "negotiation"):
            add(opp, "stalled_opportunity", "medium", f"No stage change since {opp['stage_date']} (14+ days).", "Review progress and the next action.")
        for job in opp.get("jobs") or []:
            if job["status"] in ("waiting_input", "failed", "refused", "interrupted"):
                add(opp, "linked_job_needs_attention", "medium", f"Linked job {job['id']} is {job['lifecycle']}.",
                    "Open the job.", job=job)
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda row: (order[row["severity"]], row.get("due") or "9999", row["title"]))
    return items


def record_detail(root: Path, record_id: str, *, scope: str = "global", registry=None, now=None) -> dict:
    state = project(root, scope=scope, registry=registry, now=now)
    for bucket in ("opportunities", "contacts", "organizations"):
        if record_id in state[bucket]:
            record = dict(state[bucket][record_id])
            record["as_of"] = state["as_of"]
            record["timezone"] = state["timezone"]
            if record["type"] == "contact":
                record["subscription_summary"] = state["subscribers"].get(record_id)
            record["attention"] = [row for row in state["attention"] if row["record_id"] == record_id]
            if record["type"] == "opportunity":
                record["contact_record"] = state["contacts"].get(record.get("contact") or "")
                record["organization_record"] = state["organizations"].get(record.get("organization") or "")
            if record["type"] in ("contact", "organization"):
                record["opportunity_records"] = [state["opportunities"][oid] for oid in record.get("opportunities") or []
                                                 if oid in state["opportunities"]]
            record["events"] = [row for row in read_events(root, state["client_scope"]) if row["record_id"] == record_id]
            return record
    raise CrmError(f"unknown CRM record {record_id}")


def home_summary(root: Path, *, scope: str = "global", registry=None, now=None, limit: int = 5) -> dict:
    """What Company Home shows: counts, the top attention items and where they came from."""
    state = project(root, scope=scope, registry=registry, now=now)
    guide_leads = [{"id": row["id"], "name": row["name"],
                    "offer": (row.get("attribution") or {}).get("offer"),
                    "draft_job": next((_read_job(root, ref).get("status") for ref in row.get("sources", [])
                                       if ref.startswith("AOS-") and _read_job(root, ref)), None),
                    "next_action": "Review consented draft" if (row.get("consent") or {}).get("follow_up") is True
                                   and not (row.get("subscription") or {}).get("suppressed")
                                   and not (row.get("subscription") or {}).get("delivery_review_required") else "No follow-up permission"}
                   for row in state["contacts"].values() if (row.get("attribution") or {}).get("offer")]
    return {"client_scope": state["client_scope"], "as_of": state["as_of"], "counts": state["counts"],
            "guide_leads": guide_leads[:limit], "subscribers": list(state["subscribers"].values())[:limit],
            "timezone": state["timezone"], "read_at": state["read_at"],
            "pipeline_summary": state["pipeline_summary"],
            "pipeline": state["pipeline"], "attention": state["attention"][:limit], "sources": state["sources"]}
