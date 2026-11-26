import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Load schema resources before the bridge's fake-token Path.read_text patch.
import jsonschema

from tests.bridge_loader import load_bridge as _isolated_load_bridge


BRIDGE = Path(__file__).parents[1] / "connectors" / "telegram_bridge" / "telegram_bridge.py"
AUTO_LAUNCHER = BRIDGE.with_name("Start-Telegram-Bridge-Auto.ps1")


def load_bridge():
    # Shared isolating loader: the bridge's production sys.path insert never outlives the load.
    return _isolated_load_bridge("telegram_bridge_under_test")


class TelegramBridgeFormattingTests(unittest.TestCase):
    # Revisit: when the David citation delivery contract changes. Last touched: 2026-11-09.
    def test_david_citations_reach_form_encoded_send_as_code_and_web_links_as_urls(self):
        bridge = load_bridge()
        pointer = "business_brain:sources/historical_calls/example-call/INDEX.md"
        for index, citation in enumerate((pointer, f"`{pointer}`", f"[INDEX.md]({pointer})")):
            with self.subTest(citation=citation), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(bridge, "UPDATE_STATE_FILE", Path(tmp)/"updates.json"), \
                 patch.object(bridge, "post_backend_json", return_value={
                     "kind": "david_reply", "response": f"🧠 Source: {citation}\nSee https://example.com/guide.md"
                 }), patch.object(bridge, "log"), \
                 patch.object(bridge.urllib.request, "urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
                bridge._run_david_request(123, "Find that record", "citation-fixture")
            request = urlopen.call_args.args[0]
            payload = bridge.urllib.parse.parse_qs(request.data.decode())
            body = payload["text"][0]
            self.assertEqual(body, f"[David] 🧠 Source: {pointer}\nSee https://example.com/guide.md")
            self.assertNotIn("parse_mode", payload)
            self.assertNotIn("https://INDEX.md", body)
            entities = json.loads(payload["entities"][0])
            encoded = body.encode("utf-16-le")
            spans = [(entity["type"], encoded[entity["offset"] * 2:(entity["offset"] + entity["length"]) * 2].decode("utf-16-le")) for entity in entities]
            self.assertEqual(spans, [("code", pointer), ("url", "https://example.com/guide.md")])

    def test_david_bare_filename_is_code_without_inventing_a_path(self):
        bridge = load_bridge()
        with patch.object(bridge, "api") as api:
            bridge.send(123, "[David] Source: INDEX.md", preserve_format=True)
        payload = api.call_args.args[1]
        self.assertEqual(payload["text"], "[David] Source: INDEX.md")
        self.assertEqual(json.loads(payload["entities"]), [{"type": "code", "offset": 16, "length": 8}])

    def test_david_web_links_keep_query_strings_and_exclude_surrounding_punctuation(self):
        bridge = load_bridge()
        url = "https://example.com/guide.md?q=brain&view=all"
        for text in (f"[David] See {url}.", f"[David] [Guide]({url})"):
            with self.subTest(text=text):
                body, entities = bridge.david_citation_payload(text)
                self.assertEqual(body, text)
                self.assertEqual(entities, [{"type": "url", "offset": text.index(url), "length": len(url)}])

    def test_full_brain_paths_with_spaces_and_multiple_records_stay_exact(self):
        bridge = load_bridge()
        pointers = ["business_brain:sources/example.md/Meeting Notes/INDEX.md",
                    "business_brain:sources/another-call/transcript.txt"]
        body, entities = bridge.david_citation_payload("[David] " + "\n".join(pointers))
        encoded = body.encode("utf-16-le")
        self.assertEqual([encoded[e["offset"] * 2:(e["offset"] + e["length"]) * 2].decode("utf-16-le") for e in entities], pointers)
        self.assertTrue(all(e["type"] == "code" for e in entities))

    def test_david_trim_drops_partial_citation_and_keeps_entities_in_bounds(self):
        bridge = load_bridge()
        pointer = "business_brain:sources/" + "x" * 200 + "/INDEX.md"
        with patch.object(bridge, "api") as api:
            bridge.send(123, "[David] INDEX.md " + "x" * 3320 + " " + pointer, preserve_format=True)
        payload = api.call_args.args[1]
        self.assertNotIn("business_brain:", payload["text"])
        self.assertTrue(payload["text"].endswith("[trimmed]"))
        self.assertEqual(len(json.loads(payload["entities"])), 1)

    def test_other_modes_keep_existing_plain_text_payload(self):
        bridge = load_bridge()
        with patch.object(bridge, "api") as api:
            bridge.send(123, "[Orchestrator] INDEX.md https://example.com", preserve_format=True)
        self.assertEqual(api.call_args.args[1], {"chat_id": "123", "text": "[Orchestrator] INDEX.md https://example.com"})

    @staticmethod
    def make_vault(root):
        (root / "inbox/source_notes").mkdir(parents=True)
        (root / "inbox/README.md").write_text(
            "---\nid: ttros-brain-inbox\ntype: intake\n---\n# Inbox\n",
            encoding="utf-8",
        )
        return root


    def test_windows_auto_launcher_is_powershell_51_safe_and_linux_canonical(self):
        source = AUTO_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("System.Diagnostics.ProcessStartInfo", source)
        self.assertIn("$startInfo.Arguments", source)
        self.assertNotIn("$startInfo.ArgumentList", source)
        self.assertNotIn("-ArgumentList", source)
        self.assertIn("/opt/ttros-core", source)
        self.assertIn("AgenticOSClean", source)
        self.assertNotIn("py.exe", source)
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn(".env", source)


    def test_status_formats_composite_backend_state_and_stays_inline(self):
        bridge = load_bridge()
        backend_status = {
            "success": True,
            "state": "healthy",
            "bridge": {"state": "running"},
            "queue": {"state": "healthy", "items": 12, "actionable": 0},
            "runner": {"state": "on_demand_idle"},
            "codex": {"state": "ready"},
            "hermes": {"state": "ready"},
            "local_agent_route": {"state": "ready"},
            "last_route_failure": None,
        }
        message = {"chat": {"id": 123}, "text": "/status"}
        with patch.object(bridge, "load_allowed", return_value={"operator_chat_ids": [123], "pilots": {}}), \
             patch.object(bridge, "get_backend_status", return_value=backend_status) as backend, \
             patch.object(bridge, "dispatch_david_request") as post_agent, \
             patch.object(bridge, "send") as send:
            bridge.handle_message(message)

        backend.assert_called_once_with()
        post_agent.assert_not_called()
        body = send.call_args.args[1]
        self.assertIn("Overall: healthy", body)
        self.assertIn("Bridge: live handler; backend_process=running; mode=operator", body)
        self.assertIn("Backend: ready", body)
        self.assertIn("Queue: healthy; items=12; actionable=0", body)
        self.assertIn("Runner: on_demand_idle", body)
        self.assertIn("Codex: ready", body)
        self.assertNotIn("Hermes:", body)
        self.assertIn("Local-agent readiness: ready", body)
        self.assertIn("Last route failure: none recorded", body)
        self.assertTrue(send.call_args.kwargs["preserve_format"])
        self.assertEqual(send.call_args.kwargs["api_timeout"], bridge.STATUS_SEND_TIMEOUT_SECONDS)
        self.assertLess(
            bridge.STATUS_BACKEND_TIMEOUT_SECONDS + bridge.STATUS_SEND_TIMEOUT_SECONDS,
            5,
        )

    def test_status_backend_unavailable_is_bounded_and_degraded(self):
        bridge = load_bridge()
        message = {"chat": {"id": 123}, "text": "/status"}
        with patch.object(bridge, "load_allowed", return_value={"operator_chat_ids": [123], "pilots": {}}), \
             patch.object(bridge, "get_backend_status", side_effect=TimeoutError), \
             patch.object(bridge, "send") as send:
            bridge.handle_message(message)

        body = send.call_args.args[1]
        self.assertIn("Overall: degraded", body)
        self.assertIn("Backend: unavailable", body)
        self.assertIn("Queue: unknown", body)
        self.assertIn("Last route failure: backend_status_TimeoutError", body)
        self.assertNotIn("123", body)


    def test_update_id_claim_is_durable_and_bounded(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.UPDATE_STATE_FILE = Path(tmp) / "runtime" / "telegram_bridge_updates.json"
            self.assertTrue(bridge.claim_update(900))
            self.assertFalse(bridge.claim_update(900))
            self.assertTrue(bridge.claim_update(901))
            payload = json.loads(bridge.UPDATE_STATE_FILE.read_text(encoding="utf-8"))

        self.assertEqual(payload["processed_update_ids"], [900, 901])

    def test_operator_inbox_text_and_forward_capture_without_queue_or_external_send(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as temp:
            vault = self.make_vault(Path(temp))
            messages = (
                {"message_id": 7, "date": 1784491200, "chat": {"id": 123}, "text": "/inbox Héllo\nworld"},
                {"message_id": 8, "date": 1784491201, "chat": {"id": 123}, "text": "Forwarded content", "forward_origin": {"type": "hidden_user"}},
            )
            with patch.object(bridge.business_brain, "BUSINESS_BRAIN_ROOT", vault), \
                 patch.object(bridge, "load_allowed", return_value={"operator_chat_ids": [123], "pilots": {}}), \
                 patch.object(bridge, "dispatch_david_request") as post_agent, \
                 patch.object(bridge, "api") as api, \
                 patch.object(bridge, "send") as send:
                for message in messages:
                    bridge.handle_message(message)
                    bridge.handle_message(message)

            post_agent.assert_not_called()
            api.assert_not_called()
            self.assertEqual(send.call_count, 4)
            self.assertEqual(send.call_args_list[0].args[1], "Captured ✓")
            self.assertEqual(send.call_args_list[1].args[1], "Already captured ✓")
            notes = sorted((vault / "inbox/source_notes").glob("tg_*.md"))
            self.assertEqual(len(notes), 2)
            bodies = "\n".join(path.read_text(encoding="utf-8") for path in notes)
            self.assertIn("Héllo\nworld", bodies)
            self.assertIn("Forwarded content", bodies)
            self.assertNotIn("chat_id", bodies)
            self.assertNotIn("123", bodies)
            self.assertNotIn("fake-token", bodies)

    def test_operator_document_capture_reuses_attachment_download_and_never_queues(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as temp:
            vault = self.make_vault(Path(temp))
            message = {
                "message_id": 9,
                "date": 1784491202,
                "chat": {"id": 123},
                "caption": "/capture supporting file",
                "document": {"file_id": "safe-file", "file_name": "../../brief?.txt", "mime_type": "text/plain"},
            }
            with patch.object(bridge.business_brain, "BUSINESS_BRAIN_ROOT", vault), \
                 patch.object(bridge, "_download_telegram_file", return_value=b"attachment body") as download, \
                 patch.object(bridge, "dispatch_david_request") as post_agent:
                result = bridge.capture_operator_message(message)

            download.assert_called_once_with("safe-file")
            post_agent.assert_not_called()
            self.assertEqual(result.attachment_path.read_bytes(), b"attachment body")
            self.assertTrue(result.attachment_path.resolve().is_relative_to((vault / "inbox/source_notes").resolve()))
            self.assertIn("supporting file", result.path.read_text(encoding="utf-8"))

    def test_voice_audio_is_retained_with_honest_local_transcription_fallback(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as temp:
            vault = self.make_vault(Path(temp))
            message = {
                "message_id": 10,
                "date": int(datetime(2026, 7, 19, tzinfo=timezone.utc).timestamp()),
                "chat": {"id": 123},
                "caption": "/inbox",
                "voice": {"file_id": "voice-file", "mime_type": "audio/ogg"},
            }
            with patch.object(bridge.business_brain, "BUSINESS_BRAIN_ROOT", vault), \
                 patch.object(bridge, "_download_telegram_file", return_value=b"OggSfixture"), \
                 patch.dict(bridge.os.environ, {}, clear=True), \
                 patch.object(bridge, "dispatch_david_request") as post_agent:
                result = bridge.capture_operator_message(message)

            post_agent.assert_not_called()
            self.assertEqual(result.attachment_path.read_bytes(), b"OggSfixture")
            note = result.path.read_text(encoding="utf-8")
            self.assertIn("content_type: voice", note)
            self.assertIn("transcription_status:", note)
            self.assertIn("unavailable", note)
            self.assertIn("transcription is unavailable", note)


    def test_compact_completion_and_receipt_caption_are_title_first(self):
        bridge = load_bridge()
        output = "\n".join([
            "PASS",
            "Work item title: Gmail draft capability",
            "Work item ID: AOS-2026-0131",
            "Final status: done",
            "Files touched: workflows/queue_artifacts/gmail.md",
            "Validation: Gmail draft fixture passed.",
            "Token usage: unavailable from current CLI output",
        ])

        summary = bridge.compact_telegram_closeout(output, success=True)

        self.assertTrue(summary.startswith("[AOS-2026-0131 — Gmail draft capability]\nWork item: AOS-2026-0131"))
        self.assertIn("Status: done", summary)
        self.assertEqual(
            bridge._receipt_caption(summary),
            "AOS-2026-0131 — Gmail draft capability done receipt",
        )

    def test_receipt_caption_uses_work_item_title_when_body_has_no_bracket_header(self):
        bridge = load_bridge()
        body = "PASS\nWork item title: Telegram approval-routing test\nWork item ID: AOS-2026-0136"

        self.assertEqual(
            bridge._receipt_caption(body),
            "AOS-2026-0136 — Telegram approval-routing test done receipt",
        )

    def test_send_attaches_completion_documents_after_message_body(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            document = Path(tmp) / "receipt.md"
            document.write_text("PASS\n", encoding="utf-8")
            with patch.object(bridge, "api") as api, patch.object(bridge, "_multipart_api") as multipart:
                multipart.return_value = {"ok": True, "result": {"message_id": 26}}
                result = bridge.send(123, "[Receipt delivery improvement]\nWork item: AOS-2026-0139", preserve_format=True, document_paths=[str(document)])

        api.assert_called_once()
        multipart.assert_called_once()
        self.assertEqual(api.call_args.args[0], "sendMessage")
        self.assertEqual(multipart.call_args.args[0], "sendDocument")
        self.assertEqual(multipart.call_args.args[1]["caption"], "AOS-2026-0139 — Receipt delivery improvement done receipt")
        self.assertTrue(result["message_sent"])
        self.assertEqual(result["documents"], [{"path": str(document), "sent": True, "message_id": 26}])


if __name__ == "__main__":
    unittest.main()
