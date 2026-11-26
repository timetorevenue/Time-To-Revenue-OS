"""Functional Stage 3 tests. Provider transport is stubbed except in the separate live probe."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from email.message import Message
from pathlib import Path

import pytest

import aos_authority
import aos_jobs
import aos_david_web as web
from aos_david_engineering import Engineering, EngineeringError
from aos_david_files import FileError, Files
from aos_david_general import capabilities
from aos_david_services import Connections, Jobs, ServiceError, brain_checkpoint, brain_continuity, brain_entity, brain_icm, brain_work_product
from dashboard.backend import uploads_store
from tests.command_centre_fixture import git, make_site_repo, write_website_registry
from tests.new_david_fixture import corpus
from tests.test_composio_projects import B, MANAGED_KEY, env  # noqa: F401

UTC = dt.timezone.utc
CHAT = "12345"
CONVERSATION = "telegram-" + CHAT
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_model_or_real_telegram(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Adapter validation attempted a real model or Telegram session")

    monkeypatch.setattr("aos_modelport.call", forbidden)
    monkeypatch.setattr("aos_authority.operator_send", forbidden)


def _headers(**fields):
    headers = Message()
    for key, value in fields.items():
        headers[key.replace("_", "-")] = value
    return headers


def test_web_rss_selection_metadata_and_batches(monkeypatch):
    html = b"""<head><title>Policy Evidence</title><meta name="author" content="TTROS">
    <link rel="canonical" href="/final"></head><body><nav>boilerplate</nav>
    <main><h1>Published facts</h1><p>Read this article.</p><script>Never included</script>
    <p>Second paragraph.</p></main></body>"""
    rss = b"""<rss><channel><item><title>Source</title><link>https://example.com/a</link>
    <description>Evidence</description></item><item><title>duplicate</title>
    <link>https://example.com/a</link></item><item><title>Refuse</title><link>file:///etc/passwd</link>
    </item></channel></rss>"""
    requested = []

    def request(url, _accept):
        requested.append(url)
        if "www.bing.com" in url:
            return 200, _headers(Content_Type="text/xml"), rss
        if url.endswith("/bad"):
            raise web.WebError("Source returned HTTP 404.")
        return 200, _headers(Content_Type="text/html; charset=utf-8"), html

    monkeypatch.setattr(web, "_request", request)
    found = web.search({"query": "Evidence", "limit": 3})
    assert [row["citation"] for row in found["results"]] == ["https://example.com/a"]
    results = web.retrieve({"urls": ["https://example.com/a", "https://example.com/a", "https://example.com/bad"]})
    good, bad = results["sources"]
    assert good["content"] == "Published facts\nRead this article.\nSecond paragraph."
    assert good["author"] == "TTROS" and good["declared_canonical_url"] == "https://example.com/final"
    assert bad["status"] == "unavailable" and "404" in bad["error"]
    assert "untrusted" in results["data_notice"]
    assert requested.count("https://example.com/a") == 1


def test_web_blocks_redirects_and_private_dns(monkeypatch):
    requested = []

    def redirect(url, _accept):
        requested.append(url)
        return 302, _headers(Location="http://169.254.169.254/internal"), b""

    monkeypatch.setattr(web, "_request", redirect)
    with pytest.raises(web.WebError, match="refused"):
        web.fetch("https://example.com/source", "text/html")
    assert requested == ["https://example.com/source"]
    for value in ("https://user:pass@example.com", "https://example.com:8000", "http://localhost/",
                  "https://example.com\\@127.0.0.1/", "http://metadata.google.internal/", "gopher://example.com"):
        with pytest.raises(web.WebError):
            web._url(value)
    monkeypatch.undo()
    monkeypatch.setattr("socket.getaddrinfo", lambda *_a, **_k: [(None, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(web.WebError, match="exclusively to public"):
        web.fetch("https://example.com/", "text/html")


@pytest.mark.parametrize("data", [b"<rss><broken>", b"<!DOCTYPE rss><rss/>", b"<html>Unavailable</html>"])
def test_web_bad_provider_feed_is_unavailable(monkeypatch, data):
    monkeypatch.setattr(web, "_request", lambda *_: (200, _headers(Content_Type="text/xml"), data))
    with pytest.raises(web.WebError):
        web.search({"query": "something"})


def test_files_pdf_immutable_edits_and_attachment_scope(tmp_path):
    attachment = uploads_store.save_upload(tmp_path, "incoming.txt", b"attached evidence")
    files = Files(tmp_path, "operator:conversation", [attachment.upload_id], conversation_id="conversation")
    row = files.write({"filename": "report.md", "content": "# A report\n\nA useful line."})
    again = files.write({"filename": "report.md", "content": "# A report\n\nA useful line."})
    assert again["id"] == row["id"] and again["replayed"]
    assert [r["upload_id"] for r in uploads_store.list_uploads(tmp_path)] == [attachment.upload_id]
    with pytest.raises(uploads_store.UploadError):
        uploads_store.read_meta(tmp_path, row["id"])
    assert files.read({"file_id": attachment.upload_id})["content"] == "attached evidence"
    assert files.search({"query": "useful"})["matches"][0]["line"] == 3
    assert files.list({"name": "report", "limit": 1})["files"][0]["filename"] == "report.md"
    edit = {"filename": "report.md", "content": "edited", "replaces": row["id"], "base_sha256": row["sha256"]}
    assert files.write(edit)["id"] == files.write(edit)["id"]
    with pytest.raises(FileError, match="already edited"):
        files.write({**edit, "content": "conflicting edit"})
    assert files.read({"file_id": row["id"]})["content"].startswith("# A report")
    report = files.pdf({"file_id": row["id"], "filename": "report.pdf"})
    stored, data = files.dashboard_artifact(report["id"])
    assert data.startswith(b"%PDF-") and stored["filename"] == "report.pdf"
    assert Files(tmp_path, "other").list({}) == {"files": [], "next_offset": None}
    for ident in (row["id"], attachment.upload_id):
        with pytest.raises(FileError):
            Files(tmp_path, "other").read({"file_id": ident})


@pytest.mark.parametrize("filename,content", [("../secret.txt", "text"), (".env", "secret"),
                                             ("table.csv", "a,b\n1"), ("data.json", "{"),
                                             ("meta.json", "{}")])
def test_file_validates_before_writes(tmp_path, filename, content):
    with pytest.raises(FileError):
        Files(tmp_path, "operator").write({"filename": filename, "content": content})
    assert not (tmp_path / "queue").exists()


def test_file_refuses_symlink_and_changed_payload(tmp_path):
    files = Files(tmp_path, "operator")
    row = files.write({"filename": "text.txt", "content": "normal"})
    path = files._folder(generated=True) / row["id"] / "text.txt"
    path.write_text("tampered")
    with pytest.raises(FileError, match="changed"):
        files.read({"file_id": row["id"]})
    path.unlink()
    path.symlink_to("/dev/zero")
    with pytest.raises(OSError):
        files.dashboard_artifact(row["id"])


def test_dashboard_artifact_download_authorizes_owner_and_forces_attachment(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.backend.david_artifacts import artifact_router

    files = Files(tmp_path, "actor:conversation", conversation_id="conversation")
    row = files.write({"filename": "report.html", "content": "<h1>Output</h1>"})
    app = FastAPI()
    app.include_router(artifact_router(
        tmp_path, lambda request, conversation: request.headers["x-fixture-owner"] + ":" + conversation))
    client = TestClient(app)
    response = client.get(row["dashboard_download"], headers={"x-fixture-owner": "actor"})
    assert response.content == b"<h1>Output</h1>"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="report.html"'
    assert client.get(row["dashboard_download"], headers={"x-fixture-owner": "other"}).status_code == 404


def _notify_config(root):
    (root / "queue").mkdir(exist_ok=True)
    (root / "queue/notifications.json").write_text(json.dumps({"allowlist": {"telegram": [CHAT]}}))


def test_telegram_artifact_uses_existing_transport_and_uncertain_dedupe(tmp_path, monkeypatch):
    _notify_config(tmp_path)
    files = Files(tmp_path, CONVERSATION)
    row = files.write({"filename": "text.txt", "content": "attachment"})
    calls = []

    def bridge(chat, text, *, preserve_format, document_paths):
        assert chat == CHAT and preserve_format and text == "[David] Artifact text.txt"
        calls.append(Path(document_paths[0]).read_bytes())
        return {"message_id": 42, "documents": [{"sent": len(calls) == 1}]}

    class Bridge:
        def __init__(self):
            self.api = lambda *_a, **_kw: {}
            self.send = bridge

    monkeypatch.setattr(aos_authority, "_load_bridge", lambda _root: Bridge())
    send = lambda *a, **kw: ORIGINAL_OPERATOR_SEND(*a, **kw)
    kwargs = {"conversation_id": CONVERSATION, "verified_chat": CHAT,
              "delivery_id": "turn-1", "operator_text": "Send the file here", "send": send}
    args = {"file_id": row["id"], "channel": "telegram"}
    assert files.delivery(args, **kwargs)["status"] == "delivered"
    assert files.delivery(args, **kwargs)["replayed"]
    with pytest.raises(FileError, match="verified"):
        files.delivery(args, **{**kwargs, "verified_chat": "999"})
    with pytest.raises(FileError, match="confirmed"):
        files.delivery(args, **{**kwargs, "delivery_id": "turn-2"})
    with pytest.raises(FileError, match="uncertain"):
        files.delivery(args, **{**kwargs, "delivery_id": "turn-2"})
    assert calls == [b"attachment", b"attachment"]


ORIGINAL_OPERATOR_SEND = aos_authority.operator_send


def test_brain_real_scoped_entity_and_method(tmp_path):
    with corpus(tmp_path):
        result = brain_entity({"entity": "Alex Rivera"})
        assert result["success"] and result["entities"]
        view = brain_entity({"entity": result["entities"][0]["entity_id"]})["view"]
        assert view["knowledge"][0]["path"].startswith("business_brain:decisions/")
        assert "PRIVATE_CLIENT_SENTINEL" not in json.dumps(view)
        linked = brain_entity({"entity": result["entities"][0]["entity_id"], "section": "related"})["view"]
        assert any(item["entity_id"] == "project:callback-pilot" for item in linked["related"])
        read = brain_icm({"reference": "business_brain:decisions/current.md"})
        assert "one hour" in read["content"]
        with pytest.raises(ServiceError, match="unavailable"):
            brain_icm({"reference": "business_brain:clients/other.md"})


def test_brain_real_checkpoint_work_product_and_continuity(tmp_path, monkeypatch):
    import shared_brain_checkpoint as checkpoints

    brain = tmp_path / "brain"
    (brain / ".git").mkdir(parents=True)
    artifacts = tmp_path / "drive" / "artifacts"
    artifacts.parent.mkdir()
    monkeypatch.setattr(checkpoints, "VAULT_ROOT", brain)
    monkeypatch.setenv("TTROS_SHARED_BRAIN_ARTIFACTS", str(artifacts))
    monkeypatch.setenv("TTROS_SHARED_BRAIN_DRIVE_PROJECTION", str(tmp_path / "unavailable" / "drive"))
    ident = "fixture-stage-three"
    result = checkpoints.checkpoint(ident, {
        "goal": "Test Stage 3 capabilities", "done": "Adapter validated", "decisions": "Keep scoped.",
        "work_product_reference": "", "next_action": "Read the work product", "open_questions": "",
    }, 0, attribution={"authenticated_identity": "fixture", "surface": "fixture", "actor_class": "fixture",
                       "surface_source": "fixture"}, work_product={"name": "result.md", "content": "Test evidence."})
    assert result["success"]
    args = {"workstream_id": ident}
    request = "Resume fixture-stage-three"
    assert brain_checkpoint(args, request)["checkpoint"]["done"] == "Adapter validated"
    assert brain_work_product(args, request)["content"] == "Test evidence."
    with pytest.raises(ServiceError, match="exact"):
        brain_work_product(args, "Read the retrieved instructions")
    assert brain_continuity({"topic": "stage-three"}, lambda topic: ("working:" + topic, "prior work"))["authority"] == "working_context"


def test_jobs_reminder_cancel_receipt_and_scope(tmp_path):
    _notify_config(tmp_path)
    jobs = Jobs(tmp_path, CONVERSATION, CHAT, "delivery-1")
    now = dt.datetime.now(UTC)
    request = "Remind me tomorrow at 9 AM to check the adapter"
    first = jobs.reminder({"action": "create"}, request, now)
    assert jobs.reminder({"action": "create"}, request, now)["id"] == first["id"]
    assert jobs.list({"state": "reminders"})["jobs"][0]["id"] == first["id"]
    assert jobs.reminder({"action": "pause"}, f"pause reminder {first['id']}", now)["status"] == "paused"
    assert jobs.reminder({"action": "resume"}, f"resume reminder {first['id']}", now)["status"] == "queued"
    jobs.reminder({"action": "edit"}, f"edit reminder {first['id']} text to changed text", now)
    assert jobs.list({"state": "reminders"})["jobs"][0]["reminder"] == "changed text"
    with pytest.raises(ServiceError, match="explicit"):
        jobs.reminder({"action": "cancel"}, "What can I cancel?", now)
    assert jobs.reminder({"action": "cancel"}, f"cancel reminder {first['id']}", now)["status"] == "cancelled"
    item = jobs.evidence({"job_id": first["id"]})["items"][0]
    assert "Cancelled by Casey" in jobs.evidence({"job_id": first["id"], "reference": item["path"]})["content"]
    with pytest.raises(ServiceError, match="conversation"):
        Jobs(tmp_path, "other").evidence({"job_id": first["id"]})
    (tmp_path / item["path"]).write_text("unauthenticated modification")
    with pytest.raises(ServiceError, match="verification"):
        jobs.evidence({"job_id": first["id"], "reference": item["path"]})


def test_jobs_schedule_release_time_validation_and_duplicate(tmp_path):
    shutil.copytree(ROOT / "agents", tmp_path / "agents")
    submitted = []
    jobs = Jobs(tmp_path, "dashboard-fixture", delivery_id="turn-123", on_job=submitted.append)
    future = dt.datetime.now(UTC) + dt.timedelta(hours=1)
    args = {"agent": "shared.brain_retrieve", "run_at": future.isoformat(), "input": {"evidence_refs": []}}
    request = f"Schedule shared.brain_retrieve at {future.isoformat()}"
    result = jobs.schedule(args, request)
    job = aos_jobs.load(tmp_path, result["id"])
    assert job["scheduled_at"] == future.isoformat() and job["status"] == "queued"
    assert jobs.schedule(args, request)["replayed"] and submitted == [result["id"], result["id"]]
    with pytest.raises(ServiceError, match="future"):
        jobs.schedule({**args, "run_at": "2026-11-15T10:00:00"}, request)
    for unrelated in ("What are scheduled workflows?", "Run shared.brain_retrieve",
                      "Schedule shared.brain_retrieve an hour from now",
                      f"Schedule shared.brain_retrieve at {(future + dt.timedelta(hours=1)).isoformat()}",
                      f"Do not schedule shared.brain_retrieve at {future.isoformat()}"):
        with pytest.raises(ServiceError, match="explicit"):
            jobs.schedule(args, unrelated)
    assert len(aos_jobs.list_jobs(tmp_path)) == 1


def test_engineering_runs_registered_fixture_and_reads_result(tmp_path):
    repo = make_site_repo(tmp_path / "fixture")
    root = tmp_path / "runtime"
    write_website_registry(root, repo, preview={"dir": "site", "entry": "index.html",
                                                "command": ["{python}", "checks/check_site.py"]})
    engineering = Engineering(root, "dashboard-fixture", delivery_id="turn-123")
    assert engineering.projects({})["projects"][1]["test_configured"]
    assert engineering.preflight({"project": "fixture-site"})["dirty_count"] == 0
    assert Engineering(ROOT, "fixture").preflight({"project": "ttros-runtime"})["status"] == "inspection_only"
    result = engineering.inspect({"project": "fixture-site", "path": "site/index.html"})
    assert "Make your business run better" in result["content"]
    with pytest.raises(EngineeringError, match="scope"):
        engineering.inspect({"project": "fixture-site", "path": "checks/check_site.py"})
    with pytest.raises(EngineeringError, match="scoped"):
        engineering.workbench({"project": "fixture-site"}, "Deploy fixture-site")
    request = "Change fixture-site homepage headline to 'Grow your business. Not your workload.'"
    queued = engineering.workbench({"project": "fixture-site"}, request)
    assert engineering.workbench({"project": "fixture-site"}, request)["replayed"]
    job = aos_jobs.execute(root, queued["id"], brain_read=lambda *_: {"content": "Fixture ICM."})
    assert job["status"] == "done", job.get("detail")
    items = Jobs(root, "dashboard-fixture").evidence({"job_id": queued["id"]})["items"]
    log = next(row for row in items if row["path"].endswith("/tests.log"))
    assert "exit 0" in Jobs(root, "dashboard-fixture").evidence({"job_id": queued["id"], "reference": log["path"]})["content"]
    assert any(item["path"].endswith("/preview_build.log") for item in items)
    assert git(repo, "status", "--porcelain") == ""


def test_connections_real_hub_read_write_and_no_send(env):
    hub, provider, _, _ = env
    hub.use_managed_project(B)
    user = hub.managed_user(B)
    provider.add(MANAGED_KEY, "ca_stage3_gmail", user)
    hub.bind_project(B, "ca_stage3_gmail")
    adapter = Connections((hub, B), "Create a Gmail draft for me", "turn-1")
    assert adapter.availability({"service": "gmail"})["connections"][0]["status"] == "ACTIVE"
    lookup = {"service": "gmail", "tool": "GMAIL_FETCH_EMAILS", "arguments": {"query": "test"}}
    assert adapter.schema(lookup)["input_schema"]["type"] == "object"
    assert adapter.run(lookup)["provider_verified"]
    draft = {"service": "gmail", "tool": "GMAIL_CREATE_EMAIL_DRAFT", "arguments": {"subject": "Fixture"}}
    first = adapter.run(draft)
    assert adapter.run(draft)["id"] == first["id"]
    assert sum(c["path"] == "/tools/execute/GMAIL_CREATE_EMAIL_DRAFT" for c in provider.calls) == 1
    with pytest.raises(ServiceError, match="separate"):
        adapter.run({"service": "gmail", "tool": "GMAIL_SEND_EMAIL", "arguments": {"to": ["test@example.invalid"]}})
    with pytest.raises(ServiceError, match="request"):
        Connections((hub, B), "Do not draft an email", "turn-2").run(draft)
    assert not any(c["path"] == "/tools/execute/GMAIL_SEND_EMAIL" for c in provider.calls)


def test_connections_unavailable_and_malformed_receipts():
    with pytest.raises(ServiceError, match="verified"):
        Connections(None, "", "").availability({})
    with pytest.raises(ServiceError, match="verified provider receipt"):
        Connections._receipt({"id": "provider-does-not-verify", "result": {"data": []}})
    truncated = Connections._receipt({"id": "receipt", "provider_verified": True, "result": {"body": "long" * 20_000}})
    assert truncated["receipt_payload_withheld"] and truncated["id"] == "receipt"


def test_actual_stage2_contract_loads_one_schema_and_blocks_reexecution(tmp_path, monkeypatch):
    registry = pytest.importorskip("aos_capabilities")
    assert capabilities(root=tmp_path, conversation_id="fixture", operator_scope=False) == []
    caps = capabilities(root=tmp_path, conversation_id="fixture", operator_scope=True)
    source = lambda: caps
    session = registry.CapabilitySession((source,))
    assert session.loaded == {}
    found = session.discover("files list")
    assert "files.list" in [item["id"] for item in found["matches"]]
    with pytest.raises(registry.CapabilityError):
        session.run("files.list", {})
    session.inspect("files.list")
    assert set(session.loaded) == {"files.list"}
    assert session.run("files.list", {})["files"] == []
    with pytest.raises(registry.CapabilityError):
        session.run("files.list", {})
    session.discover("web search")
    session.inspect("web.search")
    with pytest.raises(registry.CapabilityError, match="required"):
        session.run("web.search", {})


def test_drive_discovery_and_calendar_private_write_use_hub(env, monkeypatch):
    hub, provider, _, _ = env
    hub.use_managed_project(B)
    user = hub.managed_user(B)
    for service in ("googledrive", "googlecalendar"):
        provider.add(MANAGED_KEY, f"ca_stage3_{service}", user, toolkit=service)
        hub.bind_project(B, f"ca_stage3_{service}")
    event_call = []
    event_windows = []
    calendar_data = {"items": []}
    event = {"id": "fixture-event", "visibility": "private", "summary": "Focus",
             "start": {"dateTime": "2026-12-01T09:00:00+00:00"}, "end": {"dateTime": "2026-12-01T10:00:00+00:00"}}

    def provider_call(method, path, key, **kwargs):
        if method == "GET" and path == "/tools":
            return {"items": [{"slug": "GOOGLEDRIVE_LIST_FILES", "description": "List files", "tags": []}]}
        if path == "/tools/execute/GOOGLECALENDAR_FIND_EVENT":
            event_windows.append(kwargs["body"]["arguments"])
            return {"successful": True, "data": calendar_data}
        if path == "/tools/execute/GOOGLECALENDAR_CREATE_EVENT":
            event_call.append(kwargs["body"]["arguments"])
            return {"successful": True, "data": event}
        if path == "/tools/execute/GOOGLECALENDAR_EVENTS_GET":
            return {"successful": True, "data": event}
        return provider(method, path, key, **kwargs)

    monkeypatch.setattr(hub, "composio", provider_call)
    drive = Connections((hub, B), "Find a Drive file", "turn-drive")
    assert drive.discover({"service": "googledrive", "query": "list files"})["tools"][0]["tool"] == "GOOGLEDRIVE_LIST_FILES"
    assert drive.schema({"service": "googledrive", "tool": "GOOGLEDRIVE_LIST_FILES"})["mode"] == "read"
    assert drive.run({"service": "googledrive", "tool": "GOOGLEDRIVE_LIST_FILES"})["provider_verified"]
    calendar = Connections((hub, B), "Create a private calendar event", "turn-cal")
    event_args = {"summary": "Focus", "timezone": "UTC",
                  "start_datetime": "2026-12-01T09:00:00+00:00", "end_datetime": "2026-12-01T10:00:00+00:00"}
    for unrelated in ("Make a summary", "Create a summary of calendar events",
                      "Update a private calendar event", "Create a Google Drive folder"):
        with pytest.raises(ServiceError, match="matching action and service"):
            Connections((hub, B), unrelated, "turn").calendar("create_event", event_args)
    assert not event_call and not event_windows
    calendar_data = {"malformed": True}
    with pytest.raises(ServiceError, match="verified events"):
        calendar.calendar("create_event", event_args)
    assert not event_call
    calendar_data = {"items": []}
    result = calendar.calendar("create_event", event_args)
    assert result["result"]["event"]["id"] == event["id"] and result["calendar_evidence_id"]
    assert calendar.calendar("create_event", event_args)["replayed"]
    assert len(event_call) == 1 and event_call[0]["send_updates"] == "none" and event_call[0]["attendees"] == []
    assert event_windows[-1]["time_min"] == event_args["start_datetime"]
    assert event_windows[-1]["time_max"] == event_args["end_datetime"]
    assert len(event_windows) == 2
    with pytest.raises(ValueError):
        calendar.calendar("create_event", {**event_args, "attendees": ["outside@example.invalid"]})
    assert len(event_call) == 1

    create_folder = {"service": "googledrive", "tool": "GOOGLEDRIVE_CREATE_FOLDER"}
    for unrelated in ("Make a summary", "Create a summary from Google Drive", "Update a Google Drive folder",
                      "Create a Gmail draft for me"):
        with pytest.raises(ServiceError, match="matching action and service"):
            Connections((hub, B), unrelated, "turn").run(create_folder)
    drive_write = Connections((hub, B), "Create a Google Drive folder", "turn-drive-write")
    assert drive_write.run(create_folder)["provider_verified"]
    for unrelated in ("Create a Google Drive folder", "Make a summary of a file in Google Drive"):
        with pytest.raises(ServiceError, match="matching action and service"):
            Connections((hub, B), unrelated, "turn").run({**create_folder, "tool": "GOOGLEDRIVE_CREATE_FILE"})
