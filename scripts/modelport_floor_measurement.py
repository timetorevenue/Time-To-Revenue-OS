#!/usr/bin/env python3
"""Measure the real token and latency floor of a ModelPort call.

Makes a small, fixed set of real subscription-authenticated calls (default 32)
through tools/aos_modelport.py and reports exact tokens and wall-clock time per
model, effort, backend and profile. Every call is recorded in the token ledger.
Also proves that a logged-out CLI produces a truthful stop with no fallback.

Usage:
  python3 scripts/modelport_floor_measurement.py --smoke      # one call
  python3 scripts/modelport_floor_measurement.py              # full matrix

Revisit: when ModelPort backends or the pinned model list change. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aos_modelport as modelport  # noqa: E402
from aos_codex_policy import CODEX_TARGET  # noqa: E402

OUT_DIR = ROOT / "proofs" / "modelport_m0_2026-11-14"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["category", "summary", "next_action", "confidence"],
    "properties": {
        "category": {"type": "string", "enum": ["sales", "delivery", "marketing", "operations", "other"]},
        "summary": {"type": "string"},
        "next_action": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

TASK = (
    "Classify the note into one category, summarise it in one sentence of at most 25 words, "
    "and name the single next action. Confidence is between 0 and 1.\n\nNote:\n"
)
# Synthetic notes: representative of a specialist's input, containing no real client data.
NOTES = [
    "Call with a regional plumbing firm. The owner says quotes take three days because the estimator re-types "
    "site notes into a spreadsheet. They lose jobs to faster competitors. He asked for a fixed-price proposal "
    "to automate quote drafting and wants it before the end of the month. Budget was not discussed.",
    "Weekly check-in on the intake assistant build. The form integration is finished and tested. The calendar "
    "hand-off fails for bookings made after 6pm because of a timezone conversion error. The client has asked "
    "to see a working demo on Thursday, so the fix has to land by Wednesday.",
    "Draft idea for next week: a short post on why small firms should measure time-to-first-reply before buying "
    "any automation. Use the anonymised example where response time fell from nine hours to twelve minutes. "
    "Needs a clear call to action pointing at the assessment page.",
    "The morning brief did not arrive today. The scheduler shows the job as failed after a credentials refresh. "
    "Nothing else looks affected. Someone needs to re-run the job and confirm tomorrow's run is scheduled.",
    "A referral partner introduced an accounting practice with eleven staff. They want fewer missed follow-ups "
    "after discovery calls. No meeting is booked yet; the partner suggested emailing the managing partner "
    "directly and offering two times next week.",
]

MATRIX = [
    # backend, profile, model, effort, runs
    ("exec", "default", "gpt-6-luna", "low", 2),
    ("exec", "lean", "gpt-6-luna", "low", 5),
    ("exec", "lean", "gpt-6-luna", "medium", 5),
    ("exec", "lean", "gpt-6.1-terra", "medium", 5),
    ("app-server", "lean", "gpt-6-luna", "low", 5),
    ("app-server", "lean", "gpt-6-luna", "medium", 5),
    ("app-server", "lean", "gpt-6.1-terra", "medium", 5),
]


def one_call(backend, profile, model, effort, index, session=None) -> dict:
    result = modelport.call(
        TASK + NOTES[index % len(NOTES)], model=model, effort=effort, output_schema=SCHEMA,
        backend=backend, profile=profile, session=session, agent="m0.floor_measurement", timeout=180,
    )
    usage = result["usage"]
    row = {
        "backend": backend, "profile": profile, "model": model, "effort": effort, "run": index + 1,
        "status": result["status"], "ok": result["ok"], "latency_seconds": result["latency_seconds"],
        "failure_class": result["failure_class"], "detail": result["detail"][:300],
        "ledger": result["ledger"], "invocation_id": result["invocation_id"],
        "category": (result["json"] or {}).get("category"),
    }
    if usage.get("available"):
        cached = usage.get("cached_input_tokens") or 0
        row.update(input=usage["input_tokens"], cached_input=cached, fresh_input=usage["input_tokens"] - cached,
                   output=usage["output_tokens"], reasoning=usage.get("reasoning_output_tokens"),
                   total=usage["input_tokens"] + usage["output_tokens"])
    print(json.dumps(row), flush=True)
    return row


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["backend"], row["profile"], row["model"], row["effort"]), []).append(row)
    summary = []
    for (backend, profile, model, effort), group in groups.items():
        good = [row for row in group if row["ok"] and "total" in row]
        entry = {"backend": backend, "profile": profile, "model": model, "effort": effort,
                 "runs": len(group), "ok": len(good)}
        if good:
            for key in ("total", "input", "fresh_input", "cached_input", "output", "latency_seconds"):
                values = [row[key] for row in good]
                entry[key] = {"median": round(statistics.median(values), 2), "min": min(values), "max": max(values)}
            # The first call of a group pays for an uncached prefix; later calls show the steady state.
            entry["first_call_total"] = good[0]["total"]
            entry["first_call_fresh_input"] = good[0]["fresh_input"]
        summary.append(entry)
    return summary


def scrub_limits(value):
    """Keep usage windows and percentages; drop account identity and reset-credit grants."""
    if isinstance(value, dict):
        return {key: scrub_limits(item) for key, item in value.items()
                if not any(term in key.lower() for term in ("account", "email", "userid", "user_id", "resetcredit"))}
    if isinstance(value, list):
        return [scrub_limits(item) for item in value]
    return value


def logged_out_probe() -> dict:
    """A CLI with no stored login must stop truthfully: no model call, no fallback."""
    with tempfile.TemporaryDirectory(prefix="aos-modelport-loggedout-") as empty_home:
        target = replace(CODEX_TARGET, codex_home=Path(empty_home))
        result = modelport.call(
            TASK + NOTES[0], model="gpt-6-luna", effort="low", output_schema=SCHEMA,
            target=target, agent="m0.logged_out_probe", timeout=60, record=False,
        )
    return {key: result[key] for key in ("status", "ok", "failure_class", "latency_seconds", "usage")} | {
        "detail": result["detail"][-300:], "json_is_none": result["json"] is None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--smoke", action="store_true", help="one lean exec call on gpt-6-luna low")
    parser.add_argument("--only", choices=("exec", "app-server"), help="run one backend's rows only")
    args = parser.parse_args()
    if args.smoke:
        row = one_call("exec", "lean", "gpt-6-luna", "low", 0)
        return 0 if row["ok"] else 1

    started = dt.datetime.now(dt.timezone.utc).isoformat()
    rows: list[dict] = []
    limits: dict = {}
    session = modelport.AppServerSession(profile="lean")
    try:
        if args.only != "exec":
            try:
                limits["before"] = scrub_limits(session.rate_limits())
            except Exception as exc:  # a missing snapshot must not stop the measurement
                limits["before_error"] = f"{type(exc).__name__}: {exc}"[:300]
        for backend, profile, model, effort, runs in MATRIX:
            if args.only and backend != args.only:
                continue
            for index in range(runs):
                rows.append(one_call(backend, profile, model, effort, index,
                                     session=session if backend == "app-server" else None))
        if args.only != "exec":
            try:
                limits["after"] = scrub_limits(session.rate_limits())
            except Exception as exc:
                limits["after_error"] = f"{type(exc).__name__}: {exc}"[:300]
            limits["app_server_startup_seconds"] = session.startup_seconds
    finally:
        session.close()
    probe = logged_out_probe()
    report = {
        "measured_at": started, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "modelport_version": modelport.MODELPORT_VERSION,
        "total_tokens_spent": sum(row.get("total", 0) for row in rows),
        "calls": len(rows), "calls_ok": sum(1 for row in rows if row["ok"]),
        "summary": summarise(rows), "logged_out_probe": probe, "rate_limits": limits, "rows": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.only}" if args.only else ""
    path = OUT_DIR / f"floor_measurement{suffix}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(path.relative_to(ROOT)), "summary": report["summary"],
                      "logged_out_probe": probe, "total_tokens_spent": report["total_tokens_spent"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
