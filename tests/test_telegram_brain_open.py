"""Tap-to-open Brain citations; all Telegram traffic is mocked.

Revisit: when the callback or scope contract changes. Last touched: 2026-11-09.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from tests.test_telegram_bridge_formatting import load_bridge


class BrainOpenTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load_bridge()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pointers = ["business_brain:memory/company.md", "business_brain:memory/offers.md"]
        for pointer in self.pointers:
            path = self.root / pointer.removeprefix("business_brain:")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# " + path.stem + "\n\nActual Markdown record 🧠\n")
        self.addCleanup(patch.stopall)
        patch.object(self.bridge.business_brain, "BUSINESS_BRAIN_ROOT", self.root).start()
        patch.object(self.bridge, "BOT_ID", 999).start()
        patch.object(self.bridge, "load_allowed", return_value={"operator_chat_ids": [123]}).start()
        patch.object(self.bridge, "log").start()
        self.api = patch.object(self.bridge, "api", return_value={"ok": True}).start()
        self.multipart = patch.object(self.bridge, "_multipart_api", return_value={"ok": True}).start()

    def message(self, pointers=None):
        pointers = pointers or self.pointers
        self.bridge.send(123, "[David] 🧠\n" + "\n".join(pointers) + "\nhttps://example.com", preserve_format=True)
        payload = self.api.call_args.args[1]
        return {"message_id": 7, "date": 1, "chat": {"id": 123, "type": "private"},
                "from": {"id": 999, "is_bot": True}, "text": payload["text"],
                "entities": json.loads(payload["entities"]),
                "reply_markup": json.loads(payload.get("reply_markup", "{}"))}

    def query(self):
        msg = self.message()
        return {"id": "tap-1", "from": {"id": 123, "is_bot": False}, "message": msg,
                "data": msg["reply_markup"]["inline_keyboard"][1][0]["callback_data"]}

    def test_buttons_deduplicate_and_preserve_references_urls_and_emoji_offsets(self):
        msg = self.message(self.pointers + self.pointers[:1])
        rows = msg["reply_markup"]["inline_keyboard"]
        self.assertEqual([row[0]["text"] for row in rows], ["📄 Open Brain record 1", "📄 Open Brain record 2"])
        self.assertEqual([row[1]["text"] for row in rows], ["View in Dashboard 1", "View in Dashboard 2"])
        for row, pointer in zip(rows, self.pointers):
            self.assert_dashboard_link(row[1], pointer)
        self.assertTrue(all(len(row[0]["callback_data"].encode()) <= 64 for row in rows))
        self.assertIn(self.pointers[0], msg["text"])
        self.assertEqual(msg["entities"][-1]["type"], "url")
        self.assertEqual(self.bridge.brain_message_references(msg["text"], msg["entities"]), self.pointers)
        self.multipart.assert_not_called()

    def test_single_button_exact_label(self):
        msg = self.message(self.pointers[:1])
        self.assertEqual(msg["reply_markup"]["inline_keyboard"][0][0]["text"], "📄 Open Brain record")
        button = msg["reply_markup"]["inline_keyboard"][0][1]
        self.assertEqual(button["text"], "View in Dashboard")
        self.assertEqual(button["url"], "https://ttros.core-site.example/memory?record=business_brain%3Amemory%2Fcompany.md")

    def assert_dashboard_link(self, button, pointer):
        url = urlsplit(button["url"])
        self.assertEqual((url.scheme, url.netloc, url.path, url.fragment),
                         ("https", "ttros.core-site.example", "/memory", ""))
        self.assertEqual(parse_qs(url.query), {"record": [pointer]})
        self.assertNotIn("callback_data", button)

    def test_long_paths_with_spaces_and_same_basename_select_exact_record(self):
        self.pointers = ["business_brain:sources/historical_calls/" + name + "/Meeting Notes/INDEX.md"
                         for name in ("🧠 " + "a" * 100, "b" * 100)]
        for index, pointer in enumerate(self.pointers):
            path = self.root / pointer.removeprefix("business_brain:")
            path.parent.mkdir(parents=True)
            path.write_text(f"# Record {index}\n")
        query = self.query()
        rows = query["message"]["reply_markup"]["inline_keyboard"]
        self.assertNotEqual(rows[0][0]["callback_data"], rows[1][0]["callback_data"])
        for row, pointer in zip(rows, self.pointers):
            self.assert_dashboard_link(row[1], pointer)
        self.assertTrue(all(len(row[0]["callback_data"].encode()) <= 64 for row in rows))
        with patch.object(self.bridge, "send_document", return_value=True) as document:
            self.bridge.handle_brain_callback(query)
        document.assert_called_once()
        self.assertEqual(document.call_args.kwargs["caption"], self.pointers[1])

    def test_dashboard_url_encodes_query_and_fragment_characters(self):
        pointer = "business_brain:memory/Plan 🧠 & pricing #1 + 50%.md"
        (self.root / pointer.removeprefix("business_brain:")).write_text("# Plan\n")
        msg = self.message([pointer])
        self.assert_dashboard_link(msg["reply_markup"]["inline_keyboard"][0][1], pointer)

    def test_authorized_tap_sends_only_selected_record_via_existing_document_path(self):
        query = self.query()
        uploaded = []
        def upload(method, fields, files, timeout):
            uploaded.append((method, fields, Path(files["document"]).read_bytes(), Path(files["document"])))
            return {"ok": True}
        self.multipart.side_effect = upload
        self.bridge.handle_brain_callback(query)
        self.assertEqual(len(uploaded), 1)
        method, fields, content, temporary = uploaded[0]
        self.assertEqual(method, "sendDocument")
        self.assertEqual(fields, {"chat_id": "123", "caption": self.pointers[1]})
        self.assertEqual(content, (self.root / "memory/offers.md").read_bytes())
        self.assertFalse(temporary.exists())
        self.assertEqual(self.api.call_args.args[0], "answerCallbackQuery")
        self.assertEqual(self.api.call_args.args[1]["text"], "Opening Brain record…")

    def test_unauthorized_and_unbound_callbacks_never_send_documents(self):
        original = self.query()
        mutations = [
            lambda q: q["from"].update(id=456),
            lambda q: q["from"].update(is_bot=True),
            lambda q: q["message"]["chat"].update(id=456),
            lambda q: q["message"]["chat"].update(type="group"),
            lambda q: q["message"]["from"].update(id=998),
            lambda q: q["message"].update(forward_origin={"type": "user"}),
            lambda q: q["message"].update(text="[Orchestrator] " + q["message"]["text"]),
            lambda q: q["message"].pop("entities"),
            lambda q: q["message"].pop("reply_markup"),
            lambda q: q.update(data="business_brain:memory/company.md"),
            lambda q: q.update(data=self.bridge.brain_callback_data("business_brain:memory/positioning.md")),
            lambda q: q.pop("message"),
        ]
        for mutate in mutations:
            query = copy.deepcopy(original)
            mutate(query)
            with self.subTest(query=query):
                self.bridge.handle_brain_callback(query)
                self.multipart.assert_not_called()
                self.assertIn("Cannot open", self.api.call_args.args[1]["text"])

    def test_unsafe_missing_non_markdown_client_and_oversize_records_have_no_button(self):
        bad = ["business_brain:../outside.md", "business_brain:/etc/passwd.md",
               "business_brain:memory/absent.md", "business_brain:memory/company.txt",
               "business_brain:memory/example_dealership_sales_coach.md",
               "business_brain:clients/other/private.md", "business_brain:memory//company.md"]
        with patch.object(self.bridge, "_MAX_DOCUMENT_BYTES", 4):
            self.assertFalse(self.message(self.pointers)["reply_markup"])
        for pointer in bad:
            with self.subTest(pointer=pointer):
                self.assertFalse(self.message([pointer])["reply_markup"])
        outside = self.root / "outside.md"
        outside.write_text("private")
        path = self.root / "memory/company.md"
        path.unlink()
        path.symlink_to(outside)
        self.assertFalse(self.message(self.pointers[:1])["reply_markup"])

    def test_click_revalidates_missing_size_scope_and_symlink_changes(self):
        query = self.query()
        path = self.root / "memory/offers.md"
        path.unlink()
        self.bridge.handle_brain_callback(query)
        self.multipart.assert_not_called()
        path.write_text("too big")
        with patch.object(self.bridge, "_MAX_DOCUMENT_BYTES", 2):
            self.bridge.handle_brain_callback(query)
        self.multipart.assert_not_called()
        with patch.object(self.bridge.business_brain_scope, "load_registry", side_effect=PermissionError):
            self.bridge.handle_brain_callback(query)
        self.multipart.assert_not_called()
        path.unlink()
        path.symlink_to(self.root / "memory/company.md")
        self.bridge.handle_brain_callback(query)
        self.multipart.assert_not_called()

    def test_parent_symlink_swap_after_resolution_is_rejected(self):
        query = self.query()
        original = self.bridge._brain_record_bytes
        def swapped(resolved):
            (self.root / "memory").rename(self.root / "moved")
            (self.root / "memory").symlink_to(self.root / "moved", target_is_directory=True)
            return original(resolved)
        with patch.object(self.bridge, "_brain_record_bytes", side_effect=swapped):
            self.bridge.handle_brain_callback(query)
        self.multipart.assert_not_called()

    def test_telegram_rejection_and_transport_error_report_retry_and_cleanup(self):
        query = self.query()
        for result in ({"ok": False}, OSError("network")):
            self.multipart.side_effect = result if isinstance(result, Exception) else None
            self.multipart.return_value = result
            self.bridge.handle_brain_callback(query)
            self.assertIn("delivery failed", self.api.call_args.args[1]["text"])
            self.assertEqual(self.api.call_args.args[0], "sendMessage")
            temporary = Path(self.multipart.call_args.args[2]["document"])
            self.assertFalse(temporary.exists())

    def test_trimmed_reference_never_gets_a_button(self):
        self.bridge.send(123, "[David] " + "x" * 3380 + self.pointers[0] + " " + "x" * 150, preserve_format=True)
        self.assertNotIn("reply_markup", self.api.call_args.args[1])

    def test_main_routes_callback_updates_and_requests_callback_subscription(self):
        query = self.query()
        def api(method, data=None, timeout=None):
            if method == "getMe":
                return {"result": {"id": 999}}
            if method == "getUpdates":
                self.assertIn("callback_query", json.loads(data["allowed_updates"]))
                return {"result": [{"update_id": 42, "callback_query": query}]}
            raise AssertionError(method)
        with patch.object(self.bridge, "api", side_effect=api), \
             patch.object(self.bridge, "acquire_bridge_singleton", return_value=True), \
             patch.object(self.bridge, "_load_recorded_update_ids", return_value=[]), \
             patch.object(self.bridge, "claim_update", return_value=True), \
             patch.object(self.bridge, "handle_brain_callback", side_effect=KeyboardInterrupt) as callback:
            with self.assertRaises(KeyboardInterrupt):
                self.bridge.main()
        callback.assert_called_once_with(query)
