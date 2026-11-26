"""Exercise durable Telegram ingress and voice fallback with the provider boundaries stubbed."""

import sys

import pytest

from tests.bridge_loader import load_bridge
from tools import aos_reminders, david_ingress, david_speech


CHAT = 123
MESSAGE = {"chat": {"id": CHAT, "type": "private"}, "from": {"id": CHAT}, "message_id": 15,
           "voice": {"file_id": "fixture", "mime_type": "audio/ogg", "duration": 2}}
DELIVERY = "telegram-message-123-15"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    bridge = load_bridge()
    bridge.WORKSPACE = tmp_path
    bridge.UPDATE_STATE_FILE = tmp_path / david_ingress.STATE
    config = tmp_path / "queue/notifications.json"
    config.parent.mkdir()
    config.write_text('{"allowlist":{"telegram":["123"]}}')
    monkeypatch.setitem(sys.modules, "david_speech", david_speech)
    monkeypatch.setattr(bridge, "load_allowed", lambda: {"operator_chat_ids": [CHAT]})
    monkeypatch.setattr(bridge, "log", lambda *args: None)
    monkeypatch.setattr(bridge, "send", lambda *args, **kwargs: {"message_sent": True, "message_id": 37})
    return bridge


def test_audio_is_claimed_atomically_and_resumes_through_the_same_verified_david_turn(bridge, monkeypatch):
    queued = []
    monkeypatch.setattr(bridge, "dispatch_david_request", lambda *args, **kwargs: queued.append(kwargs["delivery_id"]))
    bridge.handle_message(MESSAGE, delivery_id="telegram-update-40", update_id=40)
    bridge.handle_message(MESSAGE, delivery_id="telegram-update-40", update_id=40)
    assert queued == [DELIVERY]
    assert bridge._david_delivery(CHAT, DELIVERY)["status"] == "audio_pending"
    queued.clear()
    bridge.resume_david_requests()
    assert queued == [DELIVERY]

    transcript = "List my reminders."
    downloads, submissions, sends = [], [], []
    monkeypatch.setattr(bridge, "_download_telegram_file", lambda *a, **k: downloads.append(a) or b"audio")
    monkeypatch.setattr(david_speech, "transcribe", lambda *a: (transcript, {"seconds": 1.1}))

    def post(route, payload, timeout):
        assert route == "/api/dashboard/ask-david"
        assert david_ingress.verify_telegram(bridge.WORKSPACE, transcript, "telegram-123", DELIVERY) == str(CHAT)
        assert aos_reminders.command(payload["text"])[0] == "list"
        submissions.append(payload)
        return {"kind": "david_reply", "response": "No active reminders.", "success": True, "model_calls": 0}

    def synthesize(text, output):
        assert text == "No active reminders."
        output.write_bytes(b"voice")
        return {"seconds": 0.4}

    def multipart(method, fields, files, timeout):
        assert method == "sendVoice" and fields["chat_id"] == str(CHAT)
        assert files["voice"].read_bytes() == b"voice"
        sends.append(files["voice"])
        return {"ok": True, "result": {"message_id": 38}}

    monkeypatch.setattr(bridge, "post_backend_json", post)
    monkeypatch.setattr(david_speech, "synthesize", synthesize)
    monkeypatch.setattr(bridge, "_multipart_api", multipart)
    bridge._run_david_request(CHAT, "", DELIVERY)
    bridge.handle_message(MESSAGE, delivery_id="telegram-update-41", update_id=41)
    bridge._run_david_request(CHAT, "", DELIVERY)
    bridge.handle_message({**MESSAGE, "caption": "edited"}, update_id=42, edited=True)
    assert len(downloads) == len(submissions) == len(sends) == 1
    assert not sends[0].parent.exists()
    row = bridge._david_delivery(CHAT, DELIVERY)
    assert (row["status"], row["voice_message_id"], row["message_id"]) == ("sent", 38, 37)
    assert row["audio"] is None and row["source_update"] == "telegram-update-40"


@pytest.mark.parametrize("failure", ["unsupported", "download", "transcription", "revoked"])
def test_failed_or_unauthorized_audio_never_reaches_david(bridge, monkeypatch, failure):
    attempted = []
    monkeypatch.setattr(bridge, "dispatch_david_request", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "post_backend_json", lambda *a, **k: pytest.fail("David was called"))
    monkeypatch.setattr(bridge, "send", lambda chat, text, **k: attempted.append(text) or {"message_sent": True})
    msg = MESSAGE
    if failure == "unsupported":
        msg = {**MESSAGE, "voice": None, "audio": {"file_id": "fixture", "mime_type": "audio/midi"}}

    def download(*args, **kw):
        if failure == "download":
            raise bridge.business_brain_inbox.InboxCaptureError("private details")
        assert failure == "transcription"
        return b"invalid audio"

    def transcribe(*args):
        raise david_speech.SpeechError("The audio could not be decoded.")

    monkeypatch.setattr(bridge, "_download_telegram_file", download)
    monkeypatch.setattr(david_speech, "transcribe", transcribe)
    bridge.handle_message(msg, update_id=40)
    if failure == "revoked":
        monkeypatch.setattr(bridge, "load_allowed", lambda: {})
    bridge._run_david_request(CHAT, "", DELIVERY)
    row = bridge._david_delivery(CHAT, DELIVERY)
    assert row["audio"] is None
    if failure == "revoked":
        assert row["status"] == "refused" and not attempted
    else:
        assert len(attempted) == 1
        assert "resend" in attempted[0] and "private details" not in attempted[0]


@pytest.mark.parametrize("prior", [None, "sending"])
def test_voice_failure_falls_back_to_text_without_resending(bridge, monkeypatch, prior):
    response = {"success": True, "response": "The answer", "kind": "david_reply"}
    messages = []
    monkeypatch.setattr(bridge, "send", lambda chat, text, **k: messages.append(text) or {"message_sent": True})

    def synthesize(*args):
        assert prior is None
        raise david_speech.SpeechError("Local speech synthesis is unavailable.")

    monkeypatch.setattr(david_speech, "synthesize", synthesize)
    monkeypatch.setattr(bridge, "_multipart_api", lambda *a, **k: pytest.fail("voice retransmitted"))
    bridge._david_delivery(CHAT, DELIVERY, status="ready", reply_mode="voice", voice_status=prior, result=response)
    bridge._run_david_request(CHAT, "", DELIVERY)
    bridge._run_david_request(CHAT, "", DELIVERY)
    assert len(messages) == 1 and "The answer" in messages[0]
    assert bridge._david_delivery(CHAT, DELIVERY)["status"] == "sent"


def test_revocation_during_the_david_turn_blocks_both_reply_formats(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "dispatch_david_request", lambda *a, **k: None)
    message = {**MESSAGE, "voice": None, "text": "Tell me the result."}
    bridge.handle_message(message, update_id=40)
    monkeypatch.setattr(bridge, "send", lambda *a, **k: pytest.fail("revoked chat received a reply"))
    monkeypatch.setattr(bridge, "_multipart_api", lambda *a, **k: pytest.fail("revoked chat received speech"))

    def post(*args, **kwargs):
        monkeypatch.setattr(bridge, "load_allowed", lambda: {})
        return {"success": True, "response": "Private result", "kind": "david_reply"}

    monkeypatch.setattr(bridge, "post_backend_json", post)
    bridge._run_david_request(CHAT, "", DELIVERY)
    row = bridge._david_delivery(CHAT, DELIVERY)
    assert row["status"] == "refused" and row["result"] is None


def test_audio_attachments_and_speech_rendering():
    audio = {"document": {"mime_type": "application/octet-stream", "file_name": "Note.WAV", "file_id": "fixture"}}
    assert david_speech.telegram_audio(audio) == {"file_id": "fixture", "suffix": ".wav"}
    assert david_speech.telegram_audio({"document": {"file_name": "brief.docx"}}) is None
    assert david_speech.telegram_audio({"voice": {"file_id": "fixture", "duration": 121}})["error"]
    spoken, keep = david_speech.spoken_text("Read [the source](https://example.com) and `business_brain:HOME.md`.")
    assert keep and "the source" in spoken and "example.com" not in spoken and "HOME.md" not in spoken
    spoken, keep = david_speech.spoken_text("A full sentence. " * 200)
    assert keep and len(spoken) < 950 and spoken.endswith("See the written reply for details.")
