"""Sales rules/projections over aos_crm's existing event log; no storage or execution.

New CRM entries opt into workflow=sales. The prospect ledger's outreach vocabulary,
validator and transition rules remain authoritative for ledger-backed opportunities.
Handoffs are internal review snapshots, never project creation or client obligations.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

STAGES = ("discovery", "qualified", "proposal", "negotiation", "won", "lost")
TRANSITIONS = {
    "discovery": ("qualified", "lost"), "qualified": ("discovery", "proposal", "lost"),
    "proposal": ("qualified", "negotiation", "won", "lost"), "negotiation": ("proposal", "won", "lost"),
    "won": (), "lost": (),
}


def owner(value):
    if value != "casey":
        raise ValueError("owner must be casey (the current CRM operator)")
    return value


def duplicate(name, contact, organization, known, exclude=""):
    for record in known.values():
        if record.get("type") == "opportunity" and record["id"] != exclude:
            if (record["name"].casefold(), record.get("contact") or "", record.get("organization") or "") == (name.casefold(), contact, organization):
                raise ValueError(f"duplicate opportunity: open {record['id']} instead")


def stage_change(record, payload, reason):
    stage = payload.get("stage")
    if stage not in TRANSITIONS.get(record["stage"], ()):
        raise ValueError(f"{record['stage']} cannot move to {stage}; choose an allowed sales stage")
    if not reason:
        raise ValueError("a stage change requires a reason (including why business was lost)")
    if stage == "won" and not any(row["basis"] == "confirmed" for row in record.get("interactions", [])):
        raise ValueError("winning requires a confirmed interaction on record")
    return {"stage": stage, "reason": reason, "interaction": ""}


def handoff(record, payload, known, refs):
    if record["stage"] != "won":
        raise ValueError("a delivery handoff requires a won opportunity")
    def required(field):
        value = " ".join(str(payload.get(field) or "").split())
        if not value or len(value) > 2000:
            raise ValueError(f"{field} is required (at most 2000 characters)")
        return value
    if not refs:
        raise ValueError("a handoff cites evidence for the confirmed scope")
    client = known.get(record.get("organization")) or known.get(record.get("contact"))
    if not client:
        raise ValueError("handoff requires a linked client in this scope")
    return {"confirmed_scope": required("confirmed_scope"), "next_delivery_action": required("next_delivery_action"),
            "owner": owner(payload.get("owner", "casey")), "client": {"id": client["id"], "name": client["name"]},
            "opportunity_name": record["name"], "status": "ready_for_review"}


def group(stage):
    if stage in STAGES:
        return stage
    if stage == "offer_sent":
        return "proposal"
    return "outreach"


def summary(opportunities, today):
    """Counts and monetary subtotals are independently reproducible, never currency-converted."""
    groups = {stage: [] for stage in (*STAGES, "outreach")}
    amounts = {}
    recent = []
    for record in opportunities.values():
        groups[group(record["stage"])].append(record["id"])
        value = record.get("value")
        if record["open"] and value:
            key = (value["currency"], value["basis"])
            amounts[key] = amounts.get(key, Decimal(0)) + Decimal(str(value["amount"]))
        if record["stage"] in ("won", "lost"):
            try:
                age = (today - dt.date.fromisoformat(record["stage_date"][:10])).days
            except (ValueError, KeyError):
                continue
            if 0 <= age <= 7:
                recent.append({"record_id": record["id"], "title": record["name"], "stage": record["stage"],
                               "date": record["stage_date"], "reason": record.get("outcome_reason") or "Not recorded"})
    return {"groups": [{"stage": stage, "count": len(ids), "record_ids": sorted(ids)} for stage, ids in groups.items()],
            "open_values": [{"currency": currency, "basis": basis, "amount": float(amount)} for (currency, basis), amount in sorted(amounts.items())],
            "open_without_value": sum(record["open"] and not record.get("value") for record in opportunities.values()),
            "recent_outcomes": sorted(recent, key=lambda row: (row["date"], row["record_id"]), reverse=True),
            "note": "Recorded open opportunity values; estimates are not guaranteed revenue. Currencies and bases are separate."}
