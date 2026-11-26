"""Exercise signed HTTP MCP calls through the same Jobs runner and released Forge code."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest

import aos_david
import aos_jobs
from aos_agents import executor
import shared_brain_http
import shared_brain_checkpoint
import shared_brain_queue

ROOT = Path(__file__).resolve().parents[1]
HOME = "business_brain:HOME.md"
WORKSTREAM = "stage5d-acceptance"
RETRIEVE = "jobs.agent.shared.brain_retrieve@0.1.0"
ORIGIN = "access-fixture.example"
AUDIENCE = "stage5d-fixture"
ALICE = "operator@example.com"
BOB = "other-operator@example.com"
BRAIN_ONLY = "reader@example.com"
HEADERS = {"accept": "application/json, text/event-stream"}


@pytest.fixture
def service(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "agents", tmp_path / "agents", ignore=shutil.ignore_patterns("dist", "__pycache__"))
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key()))
    monkeypatch.setitem(shared_brain_http._jwk_clients, ORIGIN, jwks)
    monkeypatch.setenv("TTROS_BRAIN_ACCESS_AUTH_DOMAIN", ORIGIN)
    monkeypatch.setenv("TTROS_BRAIN_ACCESS_AUD", AUDIENCE)
    monkeypatch.setenv("TTROS_REMOTE_PC_IDENTITIES", f"{ALICE},{BOB}")
    # A Brain identity outside the verified operator allowlist must remain read-only here.
    monkeypatch.setenv("TTROS_SHARED_BRAIN_JOBS_IDENTITIES", f"{ALICE},{BOB},{BRAIN_ONLY}")
    monkeypatch.setattr(shared_brain_http, "WRITE_ENABLED", True)
    monkeypatch.setattr(shared_brain_http, "SUBMIT_ENABLED", True)
    monkeypatch.setattr(shared_brain_queue, "ROOT", tmp_path)
    forbidden = Mock(side_effect=AssertionError("The deterministic Job reached ModelPort or an effect adapter"))
    runner = aos_jobs.JobRunner(tmp_path, modelport=forbidden, perform=forbidden)
    david = aos_david.David(tmp_path, model=forbidden, warm=False)
    david.runner = runner
    app = FastAPI()
    shared_brain_http.install(app, get_david=lambda: david)

    def header(email, audience=AUDIENCE):
        value = jwt.encode({"email": email, "iss": f"https://{ORIGIN}", "aud": audience,
                            "exp": int(time.time()) + 300}, private, algorithm="RS256")
        return {"cf-access-jwt-assertion": value}

    with TestClient(app, base_url="https://brain.core-site.example") as client:
        def rpc(method, params=None, *, surface="chatgpt", identity=ALICE, audience=AUDIENCE):
            auth = header(identity, audience) if identity else {}
            return client.post(f"/api/brain/{surface}/", headers={**HEADERS, **auth},
                               json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

        def tool(name, args=None, **options):
            response = rpc("tools/call", {"name": name, "arguments": args or {}}, **options)
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert not result.get("isError"), result
            return result["structuredContent"]

        try:
            yield SimpleNamespace(rpc=rpc, tool=tool, runner=runner, david=david, root=tmp_path, forbidden=forbidden)
        finally:
            runner.shutdown(wait=True)
            david.close()
            forbidden.assert_not_called()


def settled(service, job_id, **options):
    until = time.monotonic() + 12
    while time.monotonic() < until:
        job = service.tool("ttros_jobs", {"workstream_id": WORKSTREAM, "job_id": job_id, "include_result": True}, **options)
        if job["status"] in aos_jobs.SETTLED:
            return job
        time.sleep(0.1)
    pytest.fail(f"Job {job_id} did not settle")


def test_signed_mcp_released_job_and_continuity(service, monkeypatch):
    rpc, tool = service.rpc, service.tool
    init = {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "stage5d", "version": "1"}}
    assert rpc("initialize", init, identity=None).status_code == 401
    assert rpc("initialize", init, audience="incorrect").status_code == 401
    assert rpc("initialize", init).json()["result"]["serverInfo"]["name"] == "TTROS Shared Brain"
    legacy = {"read", "search", "entity", "resume", "checkpoint", "submit", "queue_status"}
    assert {row["name"] for row in rpc("tools/list", identity=BRAIN_ONLY).json()["result"]["tools"]} == legacy
    operator_tools = {row["name"] for row in rpc("tools/list").json()["result"]["tools"]}
    assert operator_tools - legacy == {
        "ttros_discover", "ttros_submit_job", "ttros_jobs", "ttros_job_artifact", "ttros_schedule_job", "ttros_manage_job"}
    denied = rpc("tools/call", {"name": "ttros_jobs", "arguments": {"workstream_id": WORKSTREAM}}, identity=BRAIN_ONLY)
    assert denied.json()["result"]["isError"]
    assert tool("read", {"reference": HOME}, identity=BRAIN_ONLY)["reference"] == HOME
    assert tool("search", {"query": "Business Brain", "limit": 2})["matches"]
    assert tool("entity", {"query_or_id": "person:casey-linden"})["success"]
    assert tool("resume", {"workstream_id": "example-reminders-20260101"})["success"]

    began = time.monotonic()
    assert RETRIEVE in {row["id"] for row in tool("ttros_discover", {"query": RETRIEVE})["matches"]}
    discovery_seconds = round(time.monotonic() - began, 3)
    assert "evidence_refs" in tool("ttros_discover", {"capability": RETRIEVE})["input_schema"]["required"]
    assert tool("ttros_discover", {"capability": "jobs.plan.knowledge.ingest@0.1.0"})["success"]
    assert tool("ttros_discover", {"capability": "jobs.david"})["success"]
    args = {"workstream_id": WORKSTREAM, "request_id": "signed-release-001", "capability": RETRIEVE,
            "inputs": {"evidence_refs": [HOME]}}
    assert not tool("ttros_submit_job", {**args, "capability": "jobs.plan.marketing.content@0.1.0"})["success"]
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        admissions = list(pool.map(lambda surface: tool("ttros_submit_job", args, surface=surface), ("chatgpt", "claude")))
    assert sorted(entry["duplicate"] for entry in admissions) == [False, True]
    job_id = admissions[0]["job_id"]
    assert all(entry["job_id"] == job_id for entry in admissions)
    assert not tool("ttros_submit_job", {**args, "inputs": {"evidence_refs": ["business_brain:index/NOW.md"]}})["success"]

    finished = settled(service, job_id, surface="claude")
    assert finished["status"] == "done", finished
    assert finished["execution_usage"]["model_calls"] == 0
    assert finished["result"]["value"]["refs"] == [HOME]
    assert finished["receipt"]["status"] == "done"
    assert job_id in finished["receipt"]["content"]
    ref = finished["artifacts"][0]["ref"]
    get_artifact = {"workstream_id": WORKSTREAM, "job_id": job_id, "reference": ref}
    started_read = time.monotonic()
    assert tool("ttros_jobs", {"workstream_id": WORKSTREAM, "job_id": job_id})["status"] == "done"
    status_seconds = round(time.monotonic() - started_read, 3)
    started_read = time.monotonic()
    artifact = tool("ttros_job_artifact", get_artifact, surface="claude")
    artifact_seconds = round(time.monotonic() - started_read, 3)
    document = json.loads(artifact["content"])
    assert HOME in document["output"]["value"]["refs"]
    assert document["job_id"] == job_id and document["source"] == finished["source"]["surface"]
    assert hashlib.sha256(artifact["content"].encode()).hexdigest() == finished["artifacts"][0]["sha256"]

    assert not tool("ttros_job_artifact", {**get_artifact, "workstream_id": "different-stream"})["success"]
    assert not tool("ttros_job_artifact", get_artifact, identity=BOB)["success"]
    assert not tool("ttros_job_artifact", {**get_artifact, "reference": "file:///etc/passwd"})["success"]
    assert not tool("queue_status", {"job_id": job_id}, identity=BRAIN_ONLY)["found"]
    assert len(tool("ttros_jobs", {"workstream_id": WORKSTREAM})["jobs"]) == 1

    request = f"/external-job {job_id} {WORKSTREAM}"
    david = service.david.turn("verified-dashboard", request, connection_context=(None, {"workspace": "ttros-operator", "email": ALICE}))
    assert david["success"] and david["external_job"]["job_id"] == job_id and david["model_calls"] == 0
    assert not service.david.turn("local-preview", request)["success"]
    assert not service.david.turn("verified-dashboard", request,
                                  connection_context=(None, {"workspace": "ttros-operator", "email": BOB}))["success"]

    vault = service.root / "brain"
    (vault / ".git").mkdir(parents=True)
    monkeypatch.setattr(shared_brain_checkpoint, "VAULT_ROOT", vault)
    monkeypatch.setattr(shared_brain_checkpoint, "_mirror", lambda *_: "fixture")
    fields = dict.fromkeys(shared_brain_checkpoint.FIELDS, "")
    fields.update(goal="Prove continuity.", done=f"Retrieved HOME.md. Evidence {finished['evidence_ref']}.",
                  next_action="Read the Job result.")
    saved = tool("checkpoint", {"workstream_id": WORKSTREAM, "expected_version": 0, "fields": fields})
    assert saved["note"]["authenticated_identity"] == ALICE
    resumed = tool("resume", {"workstream_id": WORKSTREAM}, surface="claude")
    assert resumed["note"]["done"] == fields["done"]

    print(json.dumps({"job": job_id, "model_usage": finished["execution_usage"], "receipt_sha256": finished["receipt"]["sha256"],
                      "artifact_sha256": artifact["sha256"], "elapsed_seconds": round(time.monotonic() - began, 3),
                      "discovery_seconds": discovery_seconds, "status_seconds": status_seconds,
                      "artifact_seconds": artifact_seconds,
                      "execution_seconds": finished["execution_elapsed_seconds"]}, sort_keys=True))
    artifact_path = service.root / aos_jobs.load(service.root, job_id)["artifacts"][0]["path"]
    artifact_path.write_text("tampered")
    assert not tool("ttros_job_artifact", get_artifact)["success"]


def test_schedule_and_management(service):
    tool = service.tool
    args = {"workstream_id": WORKSTREAM, "request_id": "schedule-mcp-001", "capability": RETRIEVE,
            "inputs": {"evidence_refs": [HOME]}}
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
    rule = tool("ttros_schedule_job", {**args, "when": future})
    assert rule["status"] == "queued"
    command = {"job_id": rule["job_id"], "workstream_id": WORKSTREAM, "revision": rule["revision"]}
    assert not tool("ttros_manage_job", {**command, "operation": "cancel"}, identity=BOB)["success"]
    paused = tool("ttros_manage_job", {**command, "operation": "pause"})
    assert paused["status"] == "paused"
    assert not tool("ttros_manage_job", {**command, "operation": "cancel"})["success"]
    due = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=3)).isoformat()
    edited = tool("ttros_manage_job", {**command, "operation": "edit", "revision": paused["revision"], "when": due})
    armed = tool("ttros_manage_job", {**command, "operation": "resume", "revision": edited["revision"]})
    assert armed["status"] == "queued"
    dispatched = settled(service, rule["job_id"])
    child = settled(service, dispatched["occurrence_job_id"])
    assert child["status"] == "done" and child["receipt"]["status"] == "done"
    assert child["execution_usage"]["model_calls"] == 0
    assert tool("ttros_schedule_job", {**args, "when": future})["duplicate"]
    assert len(tool("ttros_jobs", {"workstream_id": WORKSTREAM})["jobs"]) == 2
    print(json.dumps({"schedule": rule["job_id"], "occurrence": child["job_id"], "usage": child["execution_usage"],
                      "execution_seconds": child["execution_elapsed_seconds"]}, sort_keys=True))


def test_effect_is_held_and_cancel_is_durable(service, monkeypatch):
    def proposed(*_, **__):
        return {"agent_result": {"status": "ok", "output": {"proposal": "fixture"}, "artifacts": [],
                                 "usage": executor.empty_usage(), "proposed_effects": [
                                     {"effect": "gmail.send", "action": "GMAIL_SEND_EMAIL", "target": "person@example.com",
                                      "authorization": {"authorized": True, "basis": "untrusted fixture approval"}}]}}

    monkeypatch.setattr(executor, "dispatch", proposed)
    tool = service.tool
    args = {"workstream_id": WORKSTREAM, "request_id": "effect-mcp-001", "capability": RETRIEVE,
            "inputs": {"evidence_refs": [HOME]}}
    started = tool("ttros_submit_job", args)
    finished = settled(service, started["job_id"])
    assert finished["effects"] == [{"action": "GMAIL_SEND_EMAIL", "status": "held", "basis": "mcp_job_scope"}]
    # Hold the runner's submit callback so the cancel/claim boundary is deterministic.
    monkeypatch.setattr(service.runner, "submit", lambda _: None)
    queued = tool("ttros_submit_job", {**args, "request_id": "cancel-mcp-001"})
    command = {"workstream_id": WORKSTREAM, "job_id": queued["job_id"], "operation": "cancel"}
    assert tool("ttros_manage_job", command)["status"] == "cancelled"
    assert tool("ttros_manage_job", command)["status"] == "cancelled"
    assert aos_jobs.claim(service.root, queued["job_id"]) is None
    assert settled(service, queued["job_id"])["receipt"]["status"] == "cancelled"


def test_waiting_for_evidence_and_queued_job_revocation(service, monkeypatch):
    tool = service.tool
    args = {"workstream_id": WORKSTREAM, "request_id": "planner-mcp-001", "capability": "jobs.david",
            "inputs": {"request": "Classify this source and propose where it should be filed."}}
    job = tool("ttros_submit_job", args)
    waiting = settled(service, job["job_id"])
    assert waiting["status"] == "waiting_input" and waiting["missing"]
    assert waiting["execution_usage"]["model_calls"] == 0
    command = {"workstream_id": WORKSTREAM, "job_id": job["job_id"], "operation": "answer",
               "revision": waiting["round"], "evidence_refs": ["artifact:unknown/file.md"]}
    assert not tool("ttros_manage_job", command)["success"]
    monkeypatch.setattr(service.runner, "submit", lambda _: None)
    queued = tool("ttros_manage_job", {**command, "evidence_refs": [HOME]})
    assert queued["status"] == "queued" and queued["round"] == waiting["round"] + 1
    assert not tool("ttros_manage_job", {**command, "evidence_refs": [HOME]})["success"]
    monkeypatch.setenv("TTROS_SHARED_BRAIN_JOBS_IDENTITIES", BOB)
    blocked = aos_jobs.execute(service.root, job["job_id"], modelport=service.forbidden)
    assert blocked["status"] == "blocked" and blocked["failure_class"] == "external_scope"
