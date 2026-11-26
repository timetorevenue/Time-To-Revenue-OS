#!/usr/bin/env python3
"""Company Command Centre end-to-end demonstration, offline, against fixtures only.

Builds a disposable demo root (fixture prospect ledger, fixture Business Brain vault and scope
registry, a disposable fixture website repository), then drives the real backend
(dashboard/backend/main.py), David, aos_jobs, aos_crm and aos_website through HTTP:

  CRM & Sales      Home attention -> record -> log interaction -> stage change mirrored into the
                   ledger by its validator -> next action -> overdue on Home -> idempotent retry ->
                   validator refusal -> scope refusal -> link to the website job
  Websites         Ask David (scripted model reply; no model call) -> job -> isolated branch -> tests
                   -> local preview -> receipt -> publish held; then a failing-tests request and an
                   out-of-scope request from the Dashboard

With --browser DIST it also serves the built Dashboard from the same process and drives it in
headless Chromium (Playwright), saving screenshots. Everything stops when the script exits.

No production file, model, connector, network service or schedule is used.

Usage: python3 scripts/command_centre_demo.py --out <dir> [--browser <vite dist dir>]
Revisit: when the CRM/website APIs or the Dashboard routes change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tools", ROOT / "dashboard" / "backend", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tests.command_centre_fixture import (  # noqa: E402
    PROSPECTS, crm_environment, git, make_site_repo, write_website_registry)

import aos_david  # noqa: E402
import aos_jobs  # noqa: E402

HEADLINE = "Grow your business. Not your workload."


class ScriptedDavid:
    """Stands in for David's model: delegates website requests, answers everything else plainly."""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt, **kw):
        if kw.get("agent") != "david":
            raise RuntimeError(f"demo forbids model calls by {kw.get('agent')}")
        self.calls += 1
        message = prompt.rsplit("Casey's message:\n", 1)[-1]
        value = {"action": "reply", "reply": "", "topic": "general", "query": "", "request": "", "records": [],
                 "people": [], "resume_job": "", "restrictions": [], "website_project": "",
                 "handoff": {name: "" for name in aos_david.HANDOFF_FIELDS}}
        if "headline" in message.lower() or "website" in message.lower():
            value.update(action="delegate", topic="fixture-site", request=message.strip()[:900], website_project="fixture-site",
                         reply="I'll make that change on a separate branch and show you the preview.")
        else:
            value["reply"] = "(Demo David: scripted reply, no model call.) I can start website changes and answer from records."
        return {"ok": True, "status": "ok", "json": value, "latency_seconds": 0.0, "invocation_id": f"demo-{self.calls}",
                "usage": {"available": True, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0},
                "model": "scripted-demo", "effort": "none", "agent": "david", "ledger": {"recorded": False},
                "failure_class": "", "detail": ""}


class Threads:
    def __init__(self):
        self.notes = {}

    def read(self, identity):
        return self.notes.get(identity, "")

    def write(self, identity, body, session_id):
        self.notes[identity] = body


@contextlib.contextmanager
def demo_backend(base: Path):
    """The real backend app wired to a disposable demo root and doubles for model and Brain threads."""
    import main as backend

    root = base / "runtime"
    root.mkdir(parents=True)
    with contextlib.ExitStack() as stack:
        paths = stack.enter_context(crm_environment(root))
        repo = make_site_repo(base / "fixture-site")
        write_website_registry(root, repo)
        (root / "queue/notifications.json").write_text(json.dumps({"allowlist": {"telegram": []}}))
        vault = paths["vault"]

        def brain_read(scope, pointer):
            path = vault / pointer.removeprefix("business_brain:")
            return {"content": path.read_text(), "provenance": {"path": pointer, "client_scope": scope, "fixture": True}}

        stack.enter_context(patch.object(backend, "BASE_DIR", root))
        port = ScriptedDavid()
        backend.start_david(model=port, search=lambda query: [], threads=Threads(), brain_read=brain_read,
                            perform=lambda *a: {"ok": False, "detail": "demo performs no connector action"},
                            send=lambda *a, **k: {"status": "unavailable"}, warm=False)
        try:
            yield backend, root, repo, port
        finally:
            backend.stop_david()


def wait_settled(client, job_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get(f"/api/website/jobs/{job_id}").json()
        if view["status"] in aos_jobs.SETTLED:
            return view
        time.sleep(0.1)
    raise RuntimeError(f"{job_id} did not settle")


def api_scenario(backend, root: Path, repo: Path, port) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(backend.app)
    report: dict = {"crm": {}, "websites": {}}
    alpha, beta = f"opp:{PROSPECTS[0][0]}", f"opp:{PROSPECTS[1][0]}"
    # -- CRM & Sales ------------------------------------------------------------------
    crm = report["crm"]
    home = client.get("/api/crm/home").json()
    crm["home_before"] = {"counts": home["counts"], "attention": [(a["rule"], a["record_id"]) for a in home["attention"]]}
    record = client.get(f"/api/crm/records/{alpha}").json()
    crm["record_sources"] = record["sources"]
    crm["record_basis"] = {"stage": record["stage_basis"], "fit": record["fit"]["basis"], "value": record["value"]}

    def post(kind, record_id, payload, key, basis="confirmed", refs=("casey:dashboard-entry",)):
        response = client.post("/api/crm/events", json={"kind": kind, "record_id": record_id, "payload": payload,
                                                         "basis": basis, "source_refs": list(refs), "request_key": key})
        return response.status_code, response.json()

    status, logged = post("interaction.log", alpha, {"channel": "email", "direction": "outbound", "occurred_on": "2026-11-05",
                                                     "summary": "Casey sent the first email himself (fixture)."}, "demo-int-1",
                          refs=("gmail:fixture-demo-1",))
    event_id = logged["event"]["event_id"]
    crm["interaction"] = {"status": status, "event_id": event_id, "created": logged["created"]}
    retry_status, retry = post("interaction.log", alpha, {"channel": "email", "direction": "outbound", "occurred_on": "2026-11-05",
                                                          "summary": "Casey sent the first email himself (fixture)."}, "demo-int-1",
                               refs=("gmail:fixture-demo-1",))
    crm["retry_same_key"] = {"status": retry_status, "created": retry["created"], "same_event": retry["event"]["event_id"] == event_id}
    status, staged = post("stage.change", alpha, {"stage": "sent", "interaction": event_id, "reason": "first touch sent"}, "demo-stage-1")
    crm["stage_change"] = {"status": status, "ledger_snapshot": staged["event"].get("ledger_snapshot")}
    status, nxt = post("next_action.set", alpha, {"text": "Follow up on the first email", "due": "2026-11-12"}, "demo-next-1")
    crm["next_action"] = {"status": status, "follow_up_due": nxt["record"]["follow_up_due"]}
    status, refused = post("stage.change", beta, {"stage": "rejected", "reason": "stale"}, "demo-stage-2")
    crm["validator_refusal"] = {"status": status, "detail": refused.get("detail", "")[:240]}
    crm["example_dealership_scope"] = client.get("/api/crm/overview", params={"scope": "client:example-dealership-sales-coach"}).status_code
    ledger_rows = [json.loads(line) for line in (root / "queue/prospects.jsonl").read_text().splitlines()]
    crm["ledger"] = {"rows": len(ledger_rows), "last": {k: ledger_rows[-1][k] for k in ("prospect_id", "status", "touch_count", "next_touch_due")}}
    from aos_crm import _ledger_validator

    crm["ledger_validator"] = _ledger_validator().validate_ledger(root / "queue/prospects.jsonl", root / "queue/prospects_schema.json")["status"]
    # -- Websites & Digital -----------------------------------------------------------
    web = report["websites"]
    head_before = git(repo, "rev-parse", "HEAD")
    answer = client.post("/api/dashboard/ask-david", json={"text": f"Change the homepage headline to '{HEADLINE}'",
                                                           "conversation_id": "dashboard-demo", "delivery_id": "demo-1"}).json()
    web["david_reply"] = answer.get("response")
    job_id = answer["job_id"]
    view = wait_settled(client, job_id)
    web["david_job"] = {key: view[key] for key in ("job_id", "status", "branch", "commit", "changed_files", "tests", "publish")}
    web["david_job"]["steps"] = [(s["id"], s["status"]) for s in view["steps"]]
    web["david_job"]["receipt"] = view["receipts"][0]["path"]
    web["david_job"]["receipt_text"] = (root / view["receipts"][0]["path"]).read_text()
    web["david_job"]["artifacts_verified"] = len(view["artifacts"])
    page = client.get(view["preview"]["url"])
    web["preview"] = {"status": page.status_code, "has_new_headline": HEADLINE in page.text,
                      "csp": page.headers.get("content-security-policy")}
    web["checkout_untouched"] = {"head_unchanged": git(repo, "rev-parse", "HEAD") == head_before,
                                 "clean": git(repo, "status", "--porcelain") == "", "remotes": git(repo, "remote") or "none"}
    for name, text in (("failing_tests", "Please remove the contact link from the homepage"),
                       ("out_of_scope", "Add a deploy workflow for the site")):
        created = client.post("/api/website/requests", json={"project": "fixture-site", "request": text, "request_key": f"demo-{name}"}).json()
        settled = wait_settled(client, created["job"]["job_id"])
        web[name] = {"job_id": settled["job_id"], "status": settled["status"], "failure_class": settled["failure_class"],
                     "publish": settled["publish"]["status"], "preview": bool(settled["preview"])}
    refused = client.post("/api/website/requests", json={"project": "unchecked-site", "request": "Change anything at all", "request_key": "demo-unchecked"})
    web["unregistered_checkout"] = {"status": refused.status_code, "detail": refused.json().get("detail")}
    # CRM <-> job link (fixture scenario: the headline change was for this opportunity's pitch).
    status, linked = post("link.add", alpha, {"note": "Homepage headline change requested for this pitch (fixture)"},
                          "demo-link-1", refs=(job_id,))
    crm["job_link"] = {"status": status, "jobs": [(j["id"], j["status"], j["receipts"]) for j in linked["record"]["jobs"]]}
    home = client.get("/api/crm/home").json()
    crm["home_after"] = {"counts": home["counts"], "attention": [(a["rule"], a["record_id"], a["reason"]) for a in home["attention"]]}
    report["model_calls"] = {"david_scripted": port.calls, "real_model_calls": 0}
    return report


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def browser_scenario(backend, dist: Path, out: Path) -> dict:
    """Serve the built Dashboard and the API from one local server; drive it headless."""
    import uvicorn
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright

    backend.app.mount("/assets", StaticFiles(directory=dist / "assets"), name="demo-assets")

    @backend.app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):  # only for non-API paths in this demo server
        return FileResponse(dist / "index.html")

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(backend.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    shots, errors = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            def shot(name):
                path = out / f"{name}.png"
                page.screenshot(path=str(path), full_page=True)
                shots.append(path.name)
            page.goto(f"{base}/home")
            page.wait_for_selector("[data-testid=crm-attention] .cc-attention")
            page.wait_for_selector("[data-department=websites] small:not(:has-text('Reading'))")
            shot("01-company-home")
            page.click("[data-testid=crm-attention] .cc-attention >> nth=0")
            page.wait_for_selector(".cc-record h3")
            shot("02-crm-record")
            page.fill(".cc-form >> nth=1 >> input >> nth=0", "Call to confirm the proposal outline")
            page.click(".cc-form >> nth=1 >> button.cc-button")
            page.wait_for_selector("text=Next action saved.")
            shot("03-crm-next-action-saved")
            page.goto(f"{base}/crm?filter=all")
            page.wait_for_selector(".cc-row")
            shot("04-crm-pipeline")
            page.goto(f"{base}/websites")
            page.wait_for_selector(".cc-request textarea")
            page.fill(".cc-request textarea", "Change the homepage headline to 'Make growth feel lighter.'")
            page.click(".cc-request button.cc-button >> nth=0")
            page.wait_for_selector(".cc-publish.held", timeout=30000)
            page.wait_for_selector("iframe.cc-preview")
            time.sleep(0.5)
            shot("05-website-job-ready-publish-held")
            page.goto(f"{base}/websites")
            page.wait_for_selector(".job-list-row")
            shot("06-website-requests")
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{base}/home")
            page.wait_for_selector("[data-testid=crm-attention] .cc-attention")
            shot("07-company-home-mobile")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return {"screenshots": shots, "page_errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--browser", type=Path, help="built Dashboard (vite dist) to drive headless")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "demo-root"
    if work.exists():
        shutil.rmtree(work)
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with demo_backend(work) as (backend, root, repo, port):
        report = api_scenario(backend, root, repo, port)
        if args.browser:
            report["browser"] = browser_scenario(backend, args.browser.resolve(), out)
    report.update(started_at=started, finished_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                  fixture_only=True, note="Fixture demonstration; not production acceptance.")
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
