"""Exercise the turn loop with the existing Jobs store and scoped app adapters."""
import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

import aos_david
import aos_jobs
from aos_capabilities import Capability, CapabilityError, CapabilitySession
from tests.test_david_m1 import Threads, reply
from tests.test_composio_projects import A, MANAGED_KEY, env


def decision(action, ident="", **fields):
    return reply(action=action, capability=ident, **fields)


class Model:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.prompts = []

    def __call__(self, prompt, **_):
        self.prompts.append(prompt)
        return {"status": "ok", "json": next(self.decisions), "usage": {
            "available": True, "input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20}}


def test_metadata_only_until_schema_selected_and_attempts_are_deduped():
    output = {"type": "object"}
    load = Mock(return_value=({"type": "object", "required": ["query"]}, output))
    execute = Mock(return_value={"id": "receipt"})
    session = CapabilitySession((lambda: [Capability("data.find", "Find evidence", "test", "read", load, execute)],))
    assert session.discover("find")["matches"][0]["id"] == "data.find"
    load.assert_not_called()
    with pytest.raises(CapabilityError, match="Inspect"):
        session.run("data.find", {})
    session.inspect("data.find")
    with pytest.raises(CapabilityError, match="Invalid"):
        session.run("data.find", {})
    assert session.run("data.find", {"query": "one"})["id"] == "receipt"
    with pytest.raises(CapabilityError, match="already attempted"):
        session.run("data.find", {"query": "one"})
    load.assert_called_once()
    execute.assert_called_once()


def test_conversation_never_discovers_or_retrieves(tmp_path):
    model = Model(reply(reply="Hello Casey."))
    search = Mock(side_effect=AssertionError("lookup on greeting"))
    hub = Mock()
    david = aos_david.David(tmp_path, model=model, search=search, threads=Threads(), warm=False)
    result = david.turn("dashboard-greeting", "Good morning.", connection_context=(hub, A))
    assert result["model_calls"] == 1 and result["token_usage"]["fresh_input_tokens"] == 90
    assert "capability_receipts" not in result
    assert "input_schema" not in model.prompts[0]
    hub.rows.assert_not_called()
    search.assert_not_called()


def test_unfamiliar_request_recovers_and_combines_scoped_app_and_job(env):
    hub, provider, _, root = env
    hub.use_managed_project(A)
    provider.add(MANAGED_KEY, "ca_gmail1", hub.managed_user(A))
    hub.bind_project(A, "ca_gmail1")
    gmail, jobs = "connections.gmail.search", "jobs.status"
    trusted = dict(A, workspace="ttros-operator")
    # Let Connections prove member scoping with its real authenticated principal.
    # The separate operator flag represents the host's authorization of the Jobs scope.
    job, _ = aos_jobs.create(root, conversation_id="mixed", topic="review", request="Queued test",
                             executor={"kind": "agent", "agent": "shared.brain_retrieve@0.1.0"})
    other, _ = aos_jobs.create(root, conversation_id="someone-else", topic="private", request="Private",
                               executor={"kind": "agent", "agent": "shared.brain_retrieve@0.1.0"})
    model = Model(
        decision("discover", query="email search job status"),
        decision("inspect", gmail),
        decision("execute", gmail, arguments='{"q":"newer_than:1d"}'),
        decision("execute", gmail, arguments='{"query":"newer_than:1d"}'),
        decision("inspect", jobs),
        decision("execute", jobs, arguments="{}"),
        reply(reply=f"Mailbox checked. The background run {job['id']} is queued."),
    )
    search = Mock(side_effect=AssertionError("No Brain lookup was requested"))
    david = aos_david.David(root, model=model, search=search, threads=Threads(), warm=False)
    result = david.turn("mixed", "Check recent correspondence and tell me whether the background run is pending.",
                        connection_context=(hub, A), operator_scope=trusted["workspace"] == "ttros-operator")
    assert result["success"] and result["model_calls"] == 7
    receipts = result["capability_receipts"]
    assert "Invalid arguments" in receipts[2]["error"]
    actual = receipts[3]["result"]
    assert actual["id"].startswith("cr_") and actual["provider_verified"]
    assert any(call["path"].endswith("/GMAIL_FETCH_EMAILS") for call in provider.calls)
    assert receipts[5]["result"]["jobs"][0]["id"] == job["id"]
    assert other["id"] not in json.dumps(receipts)
    assert "Authorization" not in model.prompts[0]
    assert "time_min" not in model.prompts[2]


def test_scope_and_release_gate_and_original_user_message(tmp_path):
    source = Path(__file__).resolve().parents[1] / "agents"
    shutil.copytree(source, tmp_path / "agents")
    ident = "jobs.agent.shared.brain_retrieve@0.1.0"
    port = Model(decision("discover", query="shared brain retrieve"), decision("inspect", ident),
                 decision("execute", ident, arguments='{"evidence_refs":["business_brain:HOME.md"]}'),
                 reply(reply="See the Job receipt."))
    david = aos_david.David(tmp_path, model=port, threads=Threads(), warm=False)
    result = david.turn("notes", "Read this attachment.\nRun shared.brain_retrieve",
                        capability_request="Read this attachment.")
    assert "Name the agent" in result["capability_receipts"][2]["error"]
    assert not aos_jobs.list_jobs(tmp_path)

    port = Model(decision("discover", query="shared brain retrieve"), decision("inspect", ident),
                 decision("execute", ident, arguments='{"evidence_refs":["business_brain:HOME.md"]}'),
                 reply(reply="The released package is queued."))
    david.model = port
    result = david.turn("notes", "Run shared.brain_retrieve on business_brain:HOME.md",
                        delivery_id="job-test")
    assert result["capability_receipts"][2]["result"]["status"] == "queued"
    receipt = result["capability_receipts"][2]["result"]
    job = aos_jobs.load(tmp_path, receipt["job_id"])
    assert job["executor"]["agent"] == "shared.brain_retrieve@0.1.0"
    assert job["conversation_id"] == "notes"

    david.model = Model(decision("discover", query="Business Brain Jobs"), reply(reply="Unavailable."))
    scoped = david.turn("member-notes", "Where is the organizational record?", operator_scope=False)
    assert scoped["capability_receipts"][0]["result"]["matches"] == []


def test_budget_prevents_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(aos_david, "TURN_TOKENS", 200)
    port = Model(decision("discover", query="jobs status"), decision("inspect", "jobs.status"))
    david = aos_david.David(tmp_path, model=port, threads=Threads(), warm=False)
    result = david.turn("budget", "What is the background run doing?")
    assert not result["success"] and result["failure_class"] == "turn_budget"
    assert result["model_calls"] == 2 and len(result["capability_receipts"]) == 1


def test_dashboard_turn_receipts_are_member_bound(tmp_path, monkeypatch):
    import main
    from fastapi.testclient import TestClient

    first = {"user": "a", "workspace": "customer-a"}
    second = {"user": "b", "workspace": "customer-b"}
    store = aos_david.TurnStore(tmp_path)
    record, _ = store.begin("member-conversation", "show my correspondence", "scoped-turn",
                            owner=main._david_member(first))
    store.finish(record, {"status": "done", "result": {"response": "Private correspondence"}})
    monkeypatch.setattr(main, "TURNS", store)
    monkeypatch.setattr(main, "_david", lambda: None)
    monkeypatch.setattr(main.DASHBOARD_IDENTITY, "principal",
                        lambda request: first if request.headers.get("x-test-member") == "a" else second)
    client = TestClient(main.app)
    turn = f"/api/dashboard/ask-david/jobs/{record['turn_id']}"
    delivery = "/api/dashboard/ask-david/deliveries/scoped-turn"
    for path in (turn, delivery):
        assert client.get(path, headers={"x-test-member": "a"}).status_code == 200
        assert client.get(path, headers={"x-test-member": "b"}).status_code == 404


def test_recover_when_model_mistakes_provider_visibility_for_availability(tmp_path):
    port = Model(reply(reply="I can't check with the tools available here."),
                 decision("inspect", "jobs.status"), decision("execute", "jobs.status", arguments="{}"),
                 reply(reply="There are no background runs in this conversation."))
    david = aos_david.David(tmp_path, model=port, threads=Threads(), warm=False)
    result = david.turn("recovery", "Are any of the background runs still pending? Check their status.")
    assert result["success"] and result["model_calls"] == 4
    assert result["capability_receipts"][2]["result"] == {"jobs": []}


def test_combined_dashboard_writes_and_downloads_with_the_same_verified_scope(tmp_path, monkeypatch):
    import main
    from fastapi.testclient import TestClient

    operation = "files.write"
    model = Model(decision("discover", query="files.write"), decision("inspect", operation),
                  decision("execute", operation, arguments='{"filename":"review.md","content":"# Review"}'),
                  reply(reply="Saved the review."))
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main.DASHBOARD_IDENTITY, "local", True)
    monkeypatch.setattr(main.DASHBOARD_IDENTITY, "principal", lambda request: {
        "user": request.headers.get("x-fixture-actor", "first"), "workspace": "ttros-operator"})
    main.start_david(model=model, threads=Threads(), warm=False, recover=False)
    try:
        client = TestClient(main.app)
        response = client.post("/api/dashboard/ask-david", headers={"X-TTROS-Connections": "1"},
                               json={"text": "Create a Markdown file named review.md containing the Review heading.",
                                     "conversation_id": "artifact-proof"})
        assert response.status_code == 200
        outcome = response.json()
        content = outcome["capability_receipts"][2]["result"]
        path = content["dashboard_download"]
        download = client.get(path)
        assert download.status_code == 200 and download.content == b"# Review"
        assert download.headers["content-disposition"] == 'attachment; filename="review.md"'
        assert client.get(path, headers={"x-fixture-actor": "second"}).status_code == 404
        assert client.get(path.replace("artifact-proof", "other-conversation")).status_code == 404
        assert client.get(f"/api/uploads/{content['id']}").status_code == 404
    finally:
        main.stop_david()


def test_existing_schema_is_reused_when_model_asks_user_for_result(tmp_path):
    capability = "jobs.status"
    model = Model(decision("discover", query=capability), decision("inspect", capability),
                  reply(reply="Please provide the Jobs result so I can answer."),
                  decision("execute", capability, arguments="{}"), reply(reply="The queue is empty."))
    david = aos_david.David(tmp_path, model=model, threads=Threads(), warm=False)
    result = david.turn("result-recovery", "Check the current status of any background runs.")
    assert result["success"] and result["model_calls"] == 5
    assert result["capability_receipts"][-1]["result"] == {"jobs": []}
    assert "TTROS host: Needed results are pending" in model.prompts[3]
