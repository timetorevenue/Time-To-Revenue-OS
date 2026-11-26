"""Subscriber lifecycle in the existing append-only CRM, with no sending capability.

Revisit: when consent notices, transport receipts or suppression policy change.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from pathlib import Path

import aos_crm

NOTICE = "newsletter-2026-11-18"
NOTICE_TEXT = "Email me practical business improvement advice and Example Company updates. I can unsubscribe at any time."
SOURCE = "https://www.core-site.example/get-guide"
TOKEN_RE = re.compile(r"[a-f0-9]{64}")
SUPPRESSIONS = {"unsubscribe", "hard_bounce", "soft_bounce", "complaint", "blocked"}


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _time(now=None):
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        raise aos_crm.CrmError("timezone-aware time required")
    return value.astimezone(dt.timezone.utc)


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{6,100}", value):
        raise aos_crm.CrmError("stable request_id required (6-100 safe characters)")
    return value


def validate_event(payload, contact, refs, basis):
    """Called by the sole CRM writer; do not grant consent through generic edits."""
    if contact.get("type") != "contact" or basis != "confirmed" or not refs:
        raise aos_crm.CrmError("subscription events require a confirmed contact and source")
    action = payload.get("action")
    allowed = {
        "requested": {"action", "request_id", "notice_version", "notice_text", "consent", "token_hash", "unsubscribe_hash", "expires_at"},
        "verified": {"action", "request_id", "token_hash"},
        "expired": {"action", "request_id"},
        "suppressed": {"action", "reason", "message_id", "provider_event_id"},
        "delivery": {"action", "request_id", "status", "message_id"},
        "guide_verified": {"action", "method"},
        "unsubscribe_issued": {"action", "request_id", "unsubscribe_hash"},
        "stale_delivery": {"action", "request_id", "message_id", "provider_event_id"},
        "provider_unmatched": {"action", "reason", "message_id", "provider_event_id"},
    }
    fields = set(payload) - {"provider_receipt"}
    if "provider_receipt" in payload:
        receipt = payload["provider_receipt"]
        if not isinstance(receipt, dict) or set(receipt) != {"event", "message_id", "email", "id", "ts_event", "date"}:
            raise aos_crm.CrmError("invalid provider evidence")
        if any(len(str(v)) > 254 for v in receipt.values()):
            raise aos_crm.CrmError("provider evidence exceeds bound")
    if action not in allowed or fields != allowed[action]:
        raise aos_crm.CrmError("invalid subscription event fields")
    sub = contact.get("subscription") or {}
    if action == "requested":
        if payload["consent"] is not True or payload["notice_version"] != NOTICE or payload["notice_text"] != NOTICE_TEXT:
            raise aos_crm.CrmError("explicit current newsletter consent required")
        _key(payload["request_id"])
        if any(not re.fullmatch(r"[a-f0-9]{64}", str(payload[x])) for x in ("token_hash", "unsubscribe_hash")):
            raise aos_crm.CrmError("invalid token digest")
        if not dt.datetime.fromisoformat(payload["expires_at"]).tzinfo:
            raise aos_crm.CrmError("expiry requires timezone")
        if sub.get("suppressed") or sub.get("delivery_review_required") or contact.get("do_not_contact"):
            raise aos_crm.CrmError("suppressed contact cannot resubscribe through capture")
    elif action in ("verified", "expired", "delivery"):
        if sub.get("request_id") != payload["request_id"]:
            raise aos_crm.CrmError("stale verification request")
        if action == "verified" and (sub.get("status") != "pending" or sub.get("suppressed") or
                                      payload["token_hash"] != sub.get("token_hash")):
            raise aos_crm.CrmError("verification not pending")
        if action == "delivery":
            if payload["status"] not in ("accepted", "failed", "unknown", "delivered"):
                raise aos_crm.CrmError("invalid delivery status")
            mid = payload["message_id"]
            if not isinstance(mid, str) or len(mid) > 200 or "\n" in mid or "\r" in mid:
                raise aos_crm.CrmError("invalid message id")
            if payload["status"] in ("accepted", "delivered") and not mid:
                raise aos_crm.CrmError("provider message id required")
    elif action in ("suppressed", "provider_unmatched"):
        if payload["reason"] not in SUPPRESSIONS:
            raise aos_crm.CrmError("unsupported suppression")
        for field in ("message_id", "provider_event_id"):
            if not isinstance(payload[field], str) or len(payload[field]) > 200 or any(c in payload[field] for c in "\r\n"):
                raise aos_crm.CrmError("invalid suppression receipt")
    elif action == "unsubscribe_issued":
        _key(payload["request_id"])
        if not eligible(contact) or not re.fullmatch(r"[a-f0-9]{64}", str(payload["unsubscribe_hash"])):
            raise aos_crm.CrmError("unsubscribe link requires an eligible subscriber and token digest")
    elif action == "guide_verified" and payload["method"] != "website-server-confirmed-guide":
        raise aos_crm.CrmError("unsupported guide ownership proof")
    return dict(payload)


def apply_event(contact, event):
    data = event["payload"]
    sub = dict(contact.get("subscription") or {})
    action = data["action"]
    if action == "requested":
        sub.update(data, status="pending", marketing_consent=False, consent_requested=True,
                   requested_at=event["at"], verified_at=None, delivery_status="not_sent")
    elif action == "verified":
        sub.update(status="confirmed", marketing_consent=not sub.get("suppressed", False),
                   verified_at=event["at"], email_verified=True)
    elif action == "expired":
        if sub.get("status") == "pending":
            sub.update(status="expired", marketing_consent=False)
    elif action == "suppressed":
        sub["suppression_reasons"] = sorted(set(sub.get("suppression_reasons", [])) | {data["reason"]})
        sub.update(status="suppressed", suppressed=True, marketing_consent=False,
                   suppression_reason=data["reason"], suppressed_at=event["at"])
        contact["consent"] = {**(contact.get("consent") or {}), "follow_up": False}
    elif action == "provider_unmatched":
        sub["delivery_review_required"] = True
        sub["marketing_consent"] = False
    elif action == "delivery":
        sub.update(delivery_status=data["status"], delivery_at=event["at"])
        if data["message_id"]:
            sub["message_id"] = data["message_id"]
    elif action == "guide_verified":
        sub.update(email_verified=True, guide_verified_at=event["at"], verification_method=data["method"])
    elif action == "unsubscribe_issued":
        sub["unsubscribe_hash"] = data["unsubscribe_hash"]
    sub["last_event_id"] = event["event_id"]
    contact["subscription"] = sub


def _event(root, contact_id, payload, key, scope="global", now=None, actor="website"):
    return aos_crm.record_event(root, client_scope=scope, kind="contact.subscription", record_id=contact_id,
                               payload=payload, source_refs=[SOURCE], request_key=key, now=now, actor=actor)


def _contact(root, name, email, scope, request_id):
    # Same CRM identity for guide and newsletter; never a second contact ledger.
    email = str(email or "").strip().casefold()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email) or len(email) > 254:
        raise aos_crm.CrmError("valid email required")
    state = aos_crm.project(root, scope=scope)
    found = next((c for c in state["contacts"].values() if c.get("email") == email), None)
    if found:
        return found
    identity = _hash(f"{state['client_scope']}\0{email}")[:24]
    cid = f"contact:guide-{identity}"
    aos_crm.record_event(root, client_scope=scope, kind="contact.create", record_id=cid,
                         payload={"name": name, "email": email}, source_refs=[SOURCE],
                         request_key=f"subscriber-contact-{identity}", actor="website")
    return aos_crm.record_detail(root, cid, scope=scope)


def request_subscription(root: Path, *, name, email, consent, notice_version, request_id, scope="global", now=None):
    """Return a one-time transport envelope, never call Brevo or allocate a send job.

    Lost envelopes must not be automatically replayed: same request returns status only.
    A new explicit request_id rotates the token; old confirmations become invalid.
    """
    from aos_lead_magnet import _capture_lock
    _key(request_id)
    if consent is not True or notice_version != NOTICE:
        raise aos_crm.CrmError("explicit current newsletter consent required")
    now = _time(now)
    with _capture_lock(Path(root)):
        contact = _contact(root, name, email, scope, request_id)
        history = aos_crm.record_detail(root, contact["id"], scope=scope)["events"]
        prior = next((e for e in history if e["kind"] == "contact.subscription" and
                      e["payload"].get("action") == "requested" and e["payload"].get("request_id") == request_id), None)
        sub = contact.get("subscription") or {}
        if prior:
            return {"contact_id": contact["id"], "request_id": request_id, "status": sub.get("status"), "replayed": True, "transport": None}
        if sub.get("suppressed") or sub.get("delivery_review_required") or contact.get("do_not_contact"):
            raise aos_crm.CrmError("suppressed contact cannot request marketing")
        if sub.get("status") == "confirmed":
            return {"contact_id": contact["id"], "request_id": request_id, "status": "confirmed", "replayed": True, "transport": None}
        token, unsubscribe = secrets.token_hex(32), secrets.token_hex(32)
        expiry = (now + dt.timedelta(minutes=15)).isoformat()
        _event(root, contact["id"], {"action": "requested", "request_id": request_id, "consent": True,
               "notice_version": NOTICE, "notice_text": NOTICE_TEXT, "token_hash": _hash(token),
               "unsubscribe_hash": _hash(unsubscribe), "expires_at": expiry}, f"subscribe-{request_id}", scope, now)
        return {"contact_id": contact["id"], "request_id": request_id, "status": "pending", "replayed": False,
                "transport": {"verification_token": token, "unsubscribe_token": unsubscribe,
                              "expires_at": expiry, "recipient": contact["email"], "purpose": "newsletter_verification",
                              "notice_version": NOTICE, "notice_text": NOTICE_TEXT}}


def _token_contact(root, token, scope, field):
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise aos_crm.CrmError("invalid or expired token")
    hashed = _hash(token)
    state = aos_crm.project(root, scope=scope)
    contact = next((c for c in state["contacts"].values() if (c.get("subscription") or {}).get(field) == hashed), None)
    if not contact and field == "unsubscribe_hash":
        # An older valid unsubscribe link still revokes after a pending-token rotation.
        prior = next((e for e in aos_crm.read_events(root, state["client_scope"])
                      if e["kind"] == "contact.subscription" and e["payload"].get(field) == hashed), None)
        if prior:
            contact = state["contacts"].get(prior["record_id"])
    if not contact:
        raise aos_crm.CrmError("invalid or expired token")
    return contact


def confirm(root, *, token, scope="global", now=None):
    from aos_lead_magnet import _capture_lock
    now = _time(now)
    with _capture_lock(Path(root)):
        c = _token_contact(root, token, scope, "token_hash")
        sub = c["subscription"]
        if sub.get("suppressed") or sub.get("delivery_review_required") or c.get("do_not_contact"):
            raise aos_crm.CrmError("suppressed contact")
        if sub["status"] == "confirmed":
            return {"contact_id": c["id"], "status": "confirmed", "replayed": True}
        if sub["status"] != "pending" or now >= dt.datetime.fromisoformat(sub["expires_at"]):
            if sub["status"] == "pending":
                _event(root, c["id"], {"action": "expired", "request_id": sub["request_id"]},
                       f"expire-{sub['request_id']}", scope, now)
            raise aos_crm.CrmError("invalid or expired token")
        _event(root, c["id"], {"action": "verified", "request_id": sub["request_id"], "token_hash": _hash(token)},
               f"verify-{sub['request_id']}", scope, now)
        return {"contact_id": c["id"], "status": "confirmed", "replayed": False}


def unsubscribe(root, *, token, scope="global", now=None):
    from aos_lead_magnet import _capture_lock
    with _capture_lock(Path(root)):
        c = _token_contact(root, token, scope, "unsubscribe_hash")
        _event(root, c["id"], {"action": "suppressed", "reason": "unsubscribe", "message_id": "", "provider_event_id": ""},
               f"unsubscribe-{_hash(token)}", scope, now, "subscriber")
        return {"status": "suppressed"}


def delivery(root, *, contact_id, request_id, status, message_id, receipt_id, scope="global", now=None):
    """Record the existing transport's actual receipt, not an intention to send."""
    from aos_lead_magnet import _capture_lock
    _key(receipt_id)
    with _capture_lock(Path(root)):
        _event(root, contact_id, {"action": "delivery", "request_id": request_id, "status": status,
                                "message_id": message_id}, f"delivery-{receipt_id}", scope, now, "brevo-transport")
    return {"status": "recorded"}


def provider_event(root, *, event, message_id, email, event_id, scope="global", now=None, provider_receipt=None):
    """Authenticated adapter maps supported Brevo transactional events; never clear suppression."""
    from aos_lead_magnet import _capture_lock
    mapping = {"hard_bounce": "hard_bounce", "soft_bounce": "soft_bounce", "spam": "complaint",
               "unsubscribed": "unsubscribe", "blocked": "blocked", "invalid": "blocked", "hardBounce": "hard_bounce",
               "softBounce": "soft_bounce", "delivered": "delivered"}
    if event not in mapping or not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:@<>-]{1,180}", event_id):
        raise aos_crm.CrmError("unsupported provider event")
    with _capture_lock(Path(root)):
        state = aos_crm.project(root, scope=scope)
        c = next((c for c in state["contacts"].values() if c.get("email") == str(email).strip().casefold()), None)
        if not c:
            raise aos_crm.CrmError("unknown delivery")
        receipts = aos_crm.record_detail(root, c["id"], scope=scope)["events"]
        match = next((e for e in receipts if e["kind"] == "contact.subscription" and
                      e["payload"].get("action") == "delivery" and e["payload"].get("message_id") == message_id and
                      e["payload"].get("status") == "accepted"), None)
        if not match or not message_id:
            if event == "delivered" or not message_id:
                raise aos_crm.CrmError("uncorrelated delivery")
            # An authenticated callback can arrive before the send response is recorded.
            # Quarantine, with durable evidence, rather than losing a suppression.
            payload = {"action": "provider_unmatched", "reason": mapping[event],
                       "message_id": message_id, "provider_event_id": event_id}
            if provider_receipt is not None:
                payload["provider_receipt"] = provider_receipt
            _event(root, c["id"], payload, f"unmatched-{_hash(event_id)}", scope, now, "brevo-webhook")
            return {"status": "held_for_delivery_review"}
        if event == "delivered":
            # Old or reordered delivery never overwrites the current request or suppression.
            if match["payload"]["request_id"] != (c.get("subscription") or {}).get("request_id"):
                payload = {"action": "stale_delivery", "request_id": match["payload"]["request_id"],
                           "message_id": message_id, "provider_event_id": event_id}
                if provider_receipt is not None:
                    payload["provider_receipt"] = provider_receipt
                _event(root, c["id"], payload, f"brevo-{_hash(event_id)}", scope, now, "brevo-webhook")
                return {"status": "ignored_stale_delivery"}
            payload = {"action": "delivery", "request_id": match["payload"]["request_id"],
                       "status": "delivered", "message_id": message_id}
        else:
            payload = {"action": "suppressed", "reason": mapping[event], "message_id": message_id, "provider_event_id": event_id}
        if provider_receipt is not None:
            payload["provider_receipt"] = provider_receipt
        _event(root, c["id"], payload, f"brevo-{_hash(event_id)}", scope, now, "brevo-webhook")
        return {"status": "recorded"}


def eligible(contact):
    sub = contact.get("subscription") or {}
    return bool(sub.get("status") == "confirmed" and sub.get("marketing_consent") is True and
                sub.get("email_verified") and not sub.get("suppressed") and not sub.get("delivery_review_required") and not contact.get("do_not_contact"))


def brevo_verification_payload(envelope, *, sender):
    """Pure payload builder for the existing Brevo POST /v3/smtp/email adapter.

    The website must add newsletter-specific POST confirmation/unsubscribe relays
    before these links can be published. This function does not fetch or send.
    """
    if not isinstance(sender, str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", sender):
        raise aos_crm.CrmError("approved sender required")
    if envelope.get("purpose") != "newsletter_verification" or envelope.get("notice_version") != NOTICE:
        raise aos_crm.CrmError("wrong transport purpose")
    if any(not TOKEN_RE.fullmatch(str(envelope.get(k, ""))) for k in ("verification_token", "unsubscribe_token")):
        raise aos_crm.CrmError("invalid transport token")
    verify = "https://www.core-site.example/get-guide/verify#newsletter=" + envelope["verification_token"]
    revoke = "https://www.core-site.example/get-guide/unsubscribe#" + envelope["unsubscribe_token"]
    return {"sender": {"email": sender, "name": "Example Company"},
            "to": [{"email": envelope["recipient"]}], "subject": "Confirm your Example Company subscription",
            "textContent": f"You requested: {NOTICE_TEXT}\n\nConfirm only if you made this request:\n{verify}\n\n"
                           f"This link expires at {envelope['expires_at']}. No newsletter permission is active until confirmed.\n"
                           f"Cancel or unsubscribe: {revoke}\nThe guide remains freely available without subscribing."}


def issue_unsubscribe(root, *, contact_id, request_id, scope="global", now=None):
    """Prepare a link for an approved future draft; never send, grant or reset consent."""
    from aos_lead_magnet import _capture_lock
    _key(request_id)
    with _capture_lock(Path(root)):
        contact = aos_crm.record_detail(root, contact_id, scope=scope)
        if not eligible(contact):
            raise aos_crm.CrmError("subscriber is not eligible")
        if any(e["kind"] == "contact.subscription" and e["payload"].get("action") == "unsubscribe_issued" and
               e["payload"].get("request_id") == request_id for e in contact["events"]):
            return {"replayed": True, "unsubscribe_url": None}
        token = secrets.token_hex(32)
        _event(root, contact_id, {"action": "unsubscribe_issued", "request_id": request_id, "unsubscribe_hash": _hash(token)},
               f"unsubscribe-link-{request_id}", scope, now, "draft-preparation")
        return {"replayed": False, "unsubscribe_url": "https://www.core-site.example/get-guide/unsubscribe#" + token}
