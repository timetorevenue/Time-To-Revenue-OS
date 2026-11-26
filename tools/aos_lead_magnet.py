"""One scoped website guide capture into the existing CRM and aos_jobs draft path.

The guide remains a public existing asset. The form records a visitor's explicit
choice, returns access immediately, and queues only a consented draft. No send API.

Revisit: when the website verification or subscriber permission contract changes.
"""

from __future__ import annotations

import fcntl
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

import aos_crm
import aos_jobs

OFFER = "4-prompt-project-workflow"
SOURCE = "https://core-site.example/resources#4-prompt-project-workflow"
GUIDE = "/guides/4-prompt-project-workflow"
NOTICE_VERSION = "guide-opt-in-2026-11-17"


class CaptureError(ValueError):
    pass


@contextmanager
def _capture_lock(root: Path):
    path = root / "queue" / "crm" / "lead-magnet.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        raise CaptureError("Enter a valid email address")
    return email


def capture(root: Path, *, name: str, email: str, guide_consent: bool,
            follow_up_consent: bool, client_scope: str = "global",
            attribution: dict | None = None) -> dict:
    """Replayable local transaction; every step has a stable CRM/job dedupe key."""
    if guide_consent is not True or not isinstance(follow_up_consent, bool):
        raise CaptureError("Choose guide access and explicitly choose whether follow-up is allowed")
    name = " ".join(str(name or "").split())
    if not name or len(name) > 120:
        raise CaptureError("Enter your name (up to 120 characters)")
    email = _email(email)
    attribution = attribution or {}
    if not isinstance(attribution, dict):
        raise CaptureError("Invalid attribution")
    source = {"offer": OFFER, "source_page": "/get-guide"}
    for key in ("utm_source", "utm_medium", "utm_campaign"):
        value = str(attribution.get(key) or "")
        if value and (len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)):
            raise CaptureError("Invalid campaign attribution")
        source[key] = value
    root = Path(root)
    with _capture_lock(root):
        # Resolve scope before any write. A global visitor cannot choose another scope.
        state = aos_crm.project(root, scope=client_scope)
        scope = state["client_scope"]
        identity = hashlib.sha256(f"{scope}\0{email}".encode()).hexdigest()[:24]
        contact_id = next((c["id"] for c in state["contacts"].values() if c.get("email") == email), f"contact:guide-{identity}")
        contact = state["contacts"].get(contact_id)
        if contact and contact.get("email") != email:
            raise CaptureError("Contact identity collision")
        if not contact:
            _, created = aos_crm.record_event(
                root, client_scope=scope, kind="contact.create", record_id=contact_id,
                payload={"name": name, "email": email,
                         "attribution": source,
                         "consent": {"guide_access": True, "follow_up": follow_up_consent,
                                     "notice_version": NOTICE_VERSION}},
                source_refs=[SOURCE], request_key=f"guide-contact-{identity}", actor="website")
        else:
            created = False
        consent = aos_crm.record_detail(root, contact_id, scope=scope).get("consent") or {}
        if consent.get("follow_up") is True and follow_up_consent is False:
            aos_crm.record_event(root, client_scope=scope, kind="contact.consent", record_id=contact_id,
                                 payload={"follow_up": False, "notice_version": NOTICE_VERSION},
                                 source_refs=[SOURCE], request_key=f"guide-revoke-{identity}", actor="website")
            consent = {**consent, "follow_up": False}
        # A later form submission cannot silently widen previously declined consent.
        import aos_subscribers
        aos_subscribers._event(root, contact_id, {"action": "guide_verified", "method": "website-server-confirmed-guide"},
                               f"guide-verified-{identity}", scope)
        current = aos_crm.record_detail(root, contact_id, scope=scope)
        can_draft = consent.get("follow_up") is True and not (current.get("subscription") or {}).get("suppressed") \
                    and not (current.get("subscription") or {}).get("delivery_review_required") and not current.get("do_not_contact")
        job_id = None
        if can_draft:
            job, _ = aos_jobs.create(
                root, conversation_id="website-lead-magnet", topic="lead-magnet",
                request=f"Prepare review-only follow-up draft for {OFFER} opt-in {identity}",
                executor={"kind": "lead_magnet", "action": "draft_follow_up", "client_scope": scope,
                          "contact_id": contact_id, "opportunity_id": "", "offer": OFFER},
                dedupe_key=f"lead-magnet-draft\0{scope}\0{identity}", requested_by="website")
            job_id = job["id"]
            aos_crm.record_event(root, client_scope=scope, kind="link.add", record_id=contact_id,
                                 payload={"note": "Consented follow-up draft job; review before any send"},
                                 source_refs=[job_id], request_key=f"guide-job-link-{identity}", actor="website")
        aos_crm.record_event(root, client_scope=scope, kind="link.add", record_id=contact_id,
                             payload={"note": "Existing free guide access offered on confirmation page"},
                             source_refs=[SOURCE, "https://core-site.example" + GUIDE],
                             request_key=f"guide-access-{identity}", actor="website")
        return {"guide_url": GUIDE, "contact_id": contact_id, "opportunity_id": None,
                "draft_job_id": job_id, "follow_up_consent": can_draft, "created": created,
                "next_action": "Review consented follow-up draft before any send" if can_draft
                               else "Record guide interest; no follow-up permission"}



def run_draft_job(root: Path, job: dict, draft_adapter=None) -> dict:
    """aos_jobs deterministic executor. GmailDraftAdapter exposes only draft creation."""
    spec = job.get("executor") or {}
    scope = spec.get("client_scope") or ""
    if spec.get("action") != "draft_follow_up" or spec.get("offer") != OFFER:
        return {"status": "refused", "failure_class": "invalid_draft_job", "detail": "unsupported offer or action"}
    try:
        contact = aos_crm.record_detail(root, spec.get("contact_id") or "", scope=scope)
        subscription = contact.get("subscription") or {}
        if (subscription.get("suppressed") or subscription.get("delivery_review_required") or contact.get("do_not_contact") or not subscription.get("email_verified") or
                (contact.get("attribution") or {}).get("offer") != OFFER or
                (contact.get("consent") or {}).get("follow_up") is not True):
            raise CaptureError("follow-up consent or CRM relationship is missing")
        if not contact.get("email"):
            raise CaptureError("contact email is missing")
        if draft_adapter is None:
            from connectors.gmail_draft_adapter import GmailDraftAdapter
            draft_adapter = GmailDraftAdapter(root=root)
        receipt = draft_adapter.create_draft(
            work_item_id=job["id"], message_identity=f"{OFFER}:{contact['id']}",
            recipient=contact["email"], subject="Your 4-Prompt Project Workflow guide",
            body=(f"Hi {contact['name']},\n\nYou requested the 4-Prompt Project Workflow guide. "
                  f"You can read it here: https://core-site.example{GUIDE}\n\n"
                  "If you have questions about applying it to your business, feel free to reply.\n\nCasey"))
        status = receipt.get("canonical_status") or receipt.get("status")
        if status != "draft-created":
            return {"status": "failed", "failure_class": "draft_recovery_required",
                    "detail": f"Draft adapter status {status}; review its private recovery state before retrying"}
        return {"status": "ok", "agent": "lead-magnet-draft", "output": {
            "summary": f"Follow-up draft prepared for review; {receipt.get('safe_draft_reference') or 'draft reference unavailable'}. Nothing sent."},
            "usage": {"model_calls": 0, "total_tokens": 0, "exact": True}}
    except (aos_crm.CrmError, CaptureError) as exc:
        return {"status": "refused", "failure_class": "consent_or_scope_refused", "detail": str(exc)}
