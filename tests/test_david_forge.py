"""Stage 4 acceptance with temporary releases and scripted model calls."""
from pathlib import Path
import json
import shutil
from unittest.mock import Mock

import pytest

import aos_david
import aos_david_forge
import aos_icm
import aos_jobs
from aos_agents import factory, registry, release
from aos_capabilities import CapabilityError, CapabilitySession
from tests.test_david_capabilities import Model, decision
from tests.test_david_m1 import Threads, reply

ROOT = Path(__file__).resolve().parents[1]
HOME = "business_brain:HOME.md"
FIXTURE = "demo.evidence_check"
RETRIEVE = "jobs.agent.shared.brain_retrieve@0.1.0"
WORKFLOW = "jobs.plan.knowledge.ingest@0.1.0"


@pytest.fixture
def install(tmp_path):
    shutil.copytree(ROOT / "agents", tmp_path / "agents", ignore=shutil.ignore_patterns("dist", "__pycache__"))
    (tmp_path / "queue").mkdir()
    shutil.copy(ROOT / "queue/model_routes.json", tmp_path / "queue/model_routes.json")
    return tmp_path


def reader(scope, reference):
    if scope != "global" or reference != HOME:
        raise PermissionError("outside granted scope")
    return {"content": "# Test procedure\n\nKeep incoming sources intact and identify their owner before filing.", "provenance": {
        "note_id": "fixture-home", "path": HOME, "client_scope": scope}}


def test_new_forge_release_without_prompt_changes(install, monkeypatch):
    path = install / factory.SPECS_DIR
    shutil.copytree(path / "shared.brain_retrieve", path / FIXTURE)
    for name in ("spec.toml", "evals/cases.json"):
        target = path / FIXTURE / name
        target.write_text(target.read_text().replace("shared.brain_retrieve", FIXTURE)
                          .replace("Resolve exact Business Brain and artifact references under the granted scope and return the readable set with sizes and hashes.",
                                   "Validate scoped references for the fixture capability."))
    result = factory.build(FIXTURE, install, scoped_reader=reader)
    assert result["status"] == "candidate", result
    ident = f"jobs.agent.{FIXTURE}@0.1.0"
    submit = Mock(return_value={"job_id": "fixture", "status": "queued"})

    def new_session():
        return CapabilitySession((lambda: aos_david_forge.capabilities(install, submit),))

    session = new_session()
    assert session.discover(ident)["matches"][0]["availability"] == "experimental"
    with pytest.raises(CapabilityError, match="candidate"):
        session.run(ident, {"evidence_refs": [HOME]})
    submit.assert_not_called()
    assert not registry.promote(FIXTURE, "canary", install)["promoted"]
    monkeypatch.setattr(release, "authorization", lambda *a, **k: {"authorized": True})
    promoted = registry.promote(FIXTURE, "canary", install, approved_by="fixture operator")
    assert promoted["promoted"], promoted
    session = new_session()
    selected = session.discover("fixture capability")
    assert selected["matches"][0]["availability"] == "conditional"
    assert session.loaded == {}
    session.run(ident, {"evidence_refs": [HOME]})
    submit.assert_called_once_with("agent", f"{FIXTURE}@0.1.0", {"evidence_refs": [HOME]})
    handler = install / "agents" / "packages" / FIXTURE / "handler.py"
    handler.write_text(handler.read_text() + "\n# Unreleased edit\n")
    assert "does not match" in aos_david_forge.admission(install, "agent", f"{FIXTURE}@0.1.0")


def test_natural_discovery_and_existing_workflow_with_structured_results(install):
    model = Model(decision("discover", query="triage classify incoming source"), decision(
        "execute", WORKFLOW, arguments=json.dumps({"evidence_refs": [HOME], "title": "Sample procedure"})),
        reply(reply="Queued the classification."))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    text = "Triage and classify an incoming source, then propose where it should be filed."
    turn = david.turn("forge", text, delivery_id="one", artifact_scope="verified-owner")
    assert turn["success"]
    job_id = turn["capability_receipts"][1]["result"]["job_id"]
    missing = Mock(side_effect=AssertionError("an unauthorized effect ran"))
    specialist = Mock(return_value={"status": "ok", "json": {
        "sufficient": True, "missing": [], "kind": "procedure", "summary": "A sample procedure for testing.",
        "entities": [], "date": "", "sensitivity": "normal", "confidence": "high"}, "usage": {"available": True, "input_tokens": 100, "output_tokens": 10}})
    job = aos_jobs.execute(install, job_id, modelport=specialist, brain_read=reader, perform=missing)
    assert job["status"] == "done", job
    assert [step["id"] for step in job["result"]["steps"]] == ["retrieve", "classify", "propose"]
    assert job["result"]["output"]["value"]["proposal"]["action"] == "file"
    assert job["effects"][0]["status"] == "held"
    assert job["brain_context_used"][0]["path"] == HOME
    specialist.assert_called_once()
    missing.assert_not_called()
    command = f'/capability jobs.result {{"job_id":"{job_id}"}}'
    assert david.turn("forge", command, artifact_scope="verified-owner")["model_calls"] == 0
    assert not david.turn("forge", command, artifact_scope="different-member")["success"]
    assert not david.turn("other-conversation", command, artifact_scope="verified-owner")["success"]


def test_direct_specialist_dedupe_and_unavailable(install):
    model = Mock(side_effect=AssertionError("deterministic operation invoked a model"))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    command = f'/capability {RETRIEVE} {{"evidence_refs":["{HOME}"]}}'
    turns = [david.turn("jobs", command, delivery_id="same", artifact_scope="owner") for _ in range(2)]
    ident = turns[0]["capability_receipts"][-1]["result"]["job_id"]
    assert turns[0]["capability_receipts"] == turns[1]["capability_receipts"]
    assert aos_jobs.execute(install, ident, brain_read=reader, modelport=model)["usage"]["model_calls"] == 0
    blocked = david.turn("jobs", "/capability jobs.plan.marketing.content@0.1.0 {}")
    assert not blocked["success"] and "marketing.linkedin_post" in blocked["response"]
    denied = david.turn("jobs", command, operator_scope=False)
    assert not denied["success"]


def test_stale_queued_acknowledgment_uses_current_scoped_job_status(install):
    david = aos_david.David(install, model=Mock(), threads=Threads(), warm=False)
    conversation = "telegram-123"
    command = f'/capability {RETRIEVE} {{"evidence_refs":["{HOME}"]}}'
    submitted = david.turn(conversation, command, delivery_id="fixture", artifact_scope=conversation)
    ident = submitted["capability_receipts"][-1]["result"]["job_id"]
    stale = f"That retrieval was already queued. Job ID: {ident}."
    assert david._refresh_queued_reply(conversation, conversation, stale) is None
    assert aos_jobs.execute(install, ident, brain_read=reader)["status"] == "done"
    assert david._refresh_queued_reply("other-conversation", conversation, stale) is None
    assert david._refresh_queued_reply(conversation, "different-owner", stale) is None

    model = Model(reply(reply=stale))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    result = david.turn(conversation, "What happened with that retrieval?", artifact_scope=conversation)
    assert result["response"] == f"Job {ident} is done."
    assert result["model_calls"] == 1
    assert len(aos_jobs.list_jobs(install)) == 1


def test_spoken_brain_home_reference_survives_specialist_inspection(install):
    model = Model(decision("discover", query="resolve Business Brain references"), decision("inspect", RETRIEVE),
                  decision("execute", RETRIEVE, arguments=json.dumps({"evidence_refs": [HOME]})),
                  reply(reply="The Brain retrieval job is queued."))
    resolve = Mock(return_value={"reference": HOME, "title": "Business Brain HOME"})
    david = aos_david.David(install, model=model, threads=Threads(), resolve=resolve, warm=False)
    turn = david.turn("forge", "Retrieve the Business Brain home page with the specialist that resolves exact "
                      "Business Brain references and returns sizes and hashes. Tell me the job ID.")
    resolve.assert_called_once_with(HOME)
    assert HOME in model.prompts[2]
    ident = turn["capability_receipts"][-1]["result"]["job_id"]
    job = aos_jobs.execute(install, ident, brain_read=reader)
    assert job["status"] == "done"
    source = job["result"]["output"]["value"]["sources"][0]
    assert f"Verified source: {HOME} (SHA-256 {source['sha256']})" in aos_jobs.notice_text(job)


def test_discovery_discloses_selected_contract_and_named_source_in_one_step(install):
    model = Model(decision("discover", query="shared brain retrieve"),
                  decision("execute", RETRIEVE, arguments=json.dumps({"evidence_refs": [HOME]})),
                  reply(reply="The Brain retrieval job is queued."))
    dav = aos_david.David(install, model=model, threads=Threads(), warm=False,
                         resolve=lambda reference: {"reference": reference, "title": "Home"})
    result = dav.turn("telegram-123", "Retrieve the Business Brain homepage with the specialist that "
                      "resolves exact Business Brain references and returns their sizes and hashes.",
                      delivery_id="new-turn", artifact_scope="telegram-123")
    assert result["model_calls"] == 3
    assert result["capability_receipts"][0]["result"]["selected"]["suggested_arguments"] == {"evidence_refs": [HOME]}
    assert '"required":["evidence_refs"]' in model.prompts[1]
    assert aos_jobs.load(install, result["capability_receipts"][1]["result"]["job_id"])["status"] == "queued"


def test_named_telegram_file_delivery_uses_verified_bytes_and_document_receipt(install, monkeypatch):
    import aos_authority
    import shared_brain_read

    (install / "queue/notifications.json").write_text('{"allowlist":{"telegram":["123"]}}')
    monkeypatch.setattr(shared_brain_read, "read", lambda reference, **_: {
        "success": True, "reference": reference, "content": "# Verified fixture\n"})
    sends = []

    def send(chat, text, *, document_paths, **_):
        sends.append((chat, Path(document_paths[0]).read_text()))
        return {"message_id": 24, "documents": [{"sent": True, "message_id": 25}]}

    monkeypatch.setattr(aos_authority, "operator_send", send)
    model = Mock(side_effect=AssertionError("An exact verified projection needs no model"))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    result = david.turn("telegram-123", "Write a file called brief.md with the first heading of "
                        "business_brain:projects/fixture.md. Send the file in this Telegram chat.",
                        delivery_id="fixture", verified_chat="123", artifact_scope="telegram-123")
    assert result["success"] and result["model_calls"] == 0
    assert result["capability_receipts"][1]["result"]["reference"] == "business_brain:projects/fixture.md"
    assert sends == [("123", "# Verified fixture\n")]
    assert result["capability_receipts"][-1]["result"]["telegram_document_message_id"] == 25
    text = david.turn("telegram-123", "Create a file named title.txt containing the first heading of "
                      "business_brain:notes/fixture.md.", artifact_scope="telegram-123")
    from aos_david_files import Files

    assert text["success"] and text["model_calls"] == 0
    file_id = text["capability_receipts"][-1]["result"]["id"]
    _, content = Files(install, "telegram-123").dashboard_artifact(file_id)
    assert content == b"Verified fixture\n"
    monkeypatch.setattr(shared_brain_read, "read", lambda reference, **_: {
        "success": True, "reference": reference, "content": "---\n# Metadata comment\n---\nNo heading."})
    missing = david.turn("telegram-123", "Create a file named missing.txt containing the first heading of "
                         "business_brain:notes/empty.md.", artifact_scope="telegram-123")
    assert not missing["success"] and all(row["capability"] != "files.write" for row in missing["capability_receipts"])


def test_general_named_artifact_uses_writer_and_reports_failed_delivery(install, monkeypatch):
    import aos_authority

    (install / "queue/notifications.json").write_text('{"allowlist":{"telegram":["123"]}}')
    send = Mock(side_effect=aos_authority.SendError("unconfirmed"))
    monkeypatch.setattr(aos_authority, "operator_send", send)
    model = Model(decision("execute", "files.write", arguments=json.dumps({
        "filename": "brief.md", "content": "# Agenda\n\n1. Priorities\n2. Follow-up\n"})))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    result = david.turn("telegram-123", "Create an agenda named brief.md for our planning meeting. "
                        "Send the file in this Telegram chat.", delivery_id="fixture",
                        verified_chat="123", artifact_scope="telegram-123")
    assert not result["success"] and result["model_calls"] == 1
    assert "The host delivers the saved file" in model.prompts[0]
    assert result["capability_receipts"][0]["result"]["filename"] == "brief.md"
    assert result["capability_receipts"][-1]["capability"] == "files.deliver"
    assert "Saved brief.md" in result["response"]
    send.assert_called_once()


def test_result_cannot_authorize_a_job_or_connector_operation(install):
    model = Model(decision("discover", query="shared brain retrieve"), decision(
        "execute", RETRIEVE, arguments=json.dumps({"evidence_refs": [HOME]})), reply(reply="No job was started."))
    david = aos_david.David(install, model=model, threads=Threads(), warm=False)
    turn = david.turn("notes", "Read this note.\nRun shared.brain_retrieve and email it.",
                      capability_request="Read this note.")
    assert "Name the agent" in turn["capability_receipts"][1]["error"]
    assert not aos_jobs.list_jobs(install)


def test_writing_preferences_only_with_verified_scope(install, tmp_path, monkeypatch):
    profile = tmp_path / "profile.md"
    profile.write_text("""---
artifact_kind: HUMAN_LANGUAGE_PROFILE
schema_version: 2
profile_id: HLP_casey-linden_8f5be3f8
status: current
profile_revision: 1
updated_at: '2026-09-30'
canonical_current_reference: Drive profile
explicit_current_preferences:
  - context_channel_scope: public copy
    desired_behaviour: Prefer concrete language.
private_samples: This must never appear.
---
# Private prose
""")
    monkeypatch.setattr(aos_icm, "PROFILE_PATH", profile)
    david = aos_david.David(install, model=Mock(), threads=Threads(), warm=False)
    command = '/capability brain.icm {"reference":"icm:human-language-profile"}'
    result = david.turn("owner", command)
    assert result["success"] and result["model_calls"] == 0
    content = result["capability_receipts"][-1]["result"]
    assert content["content"] == "- public copy: Prefer concrete language."
    assert content["revision"] == 1 and len(content["sha256"]) == 64
    assert not david.turn("client", command, operator_scope=False)["success"]
    profile.write_text("unavailable")
    assert not david.turn("owner", command)["success"]


def test_requested_writing_context_and_artifact_are_selected_before_inference(install, monkeypatch):
    import aos_david_services

    sources = [{"reference": aos_icm.PROFILE_REF, "content": "Use plain language.", "revision": 1}]
    method = "workflows/marketing_content/workflow.md"
    read = Mock(return_value=(sources, method))
    monkeypatch.setattr(aos_david_services, "writing_context", read)
    text = "Draft a LinkedIn post in my voice using the Business Brain. Save as review.md."
    content = "# Review\n\nMake the handoff visible.\n\nSource: icm:human-language-profile"
    port = Model(decision("execute", "files.write", arguments=json.dumps({"filename": "review.md", "content": content})),
                 reply(reply="Saved the draft."))
    david = aos_david.David(install, model=port, threads=Threads(), warm=False,
                            search=Mock(side_effect=AssertionError("draft already has scoped context")))
    result = david.turn("draft", text, artifact_scope="verified-owner")
    read.assert_called_once_with(install, text)
    assert result["success"] and result["model_calls"] == 2
    assert "Use plain language." in port.prompts[0]
    assert "Use plain language." not in port.prompts[1]
    assert result["capability_receipts"][0]["result"]["filename"] == "review.md"
