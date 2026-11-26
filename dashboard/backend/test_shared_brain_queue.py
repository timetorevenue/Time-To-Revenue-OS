"""Shared Brain queue_status: read-only Work Queue status over both Access-gated MCP addresses."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
_PRE_IMPORT_PATH = list(sys.path)
_PRE_IMPORT_MODULES = set(sys.modules)
sys.path.insert(0, str(TOOLS_DIR))

import httpx2
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import shared_brain_http
import shared_brain_queue

# Same isolation as test_shared_brain_stage1: never leave tools/ modules cached under bare names.
sys.path[:] = _PRE_IMPORT_PATH
for _name in set(sys.modules) - _PRE_IMPORT_MODULES:
    _file = getattr(sys.modules[_name], "__file__", None) or ""
    if "." not in _name and Path(_file).parent == TOOLS_DIR:
        del sys.modules[_name]

reader = shared_brain_queue
STAMP = {"authenticated_identity": "verified@example.com", "surface": "chatgpt",
         "actor_class": "authorised_client", "surface_source": "address"}
PRIVATE = "PRIVATE-CONTEXT-DO-NOT-RETURN"


def _item(item_id: str, status: str, receipts: list[dict]) -> dict:
    return {
        "id": item_id, "title": f"Job {item_id}", "status": status, "owner": "operations",
        "owner_type": "agent", "parent_id": None, "priority": 5,
        "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:05:00Z",
        "claim": {"claimed_at": "2026-11-01T10:01:00Z", "claimed_by": "worker-1"},
        "dispatch": {"accepted_at": "2026-11-01T10:00:01Z", "idempotency_key": PRIVATE},
        "worker_heartbeat_at": "2026-11-01T10:04:00Z",
        "context": PRIVATE, "objective": {"original_command": PRIVATE},
        "receipts": receipts,
    }


class QueueFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "queue" / "receipts").mkdir(parents=True)
        (self.root / "secret.md").write_text("token=should-never-be-read-1234", encoding="utf-8")
        (self.root / "queue" / "receipts" / "AOS-2026-0001-old.md").write_text("old receipt", encoding="utf-8")
        (self.root / "queue" / "receipts" / "AOS-2026-0001.md").write_text(
            "Result: PASS\napi_key = abcdef1234567890\nAuthorization: Bearer abcdefghijklmnop123\n"
            "sk-ABCDEFGHIJKLMNOPQRSTUV and ghp_ABCDEFGHIJKLMNOPQRSTUV kept out\n", encoding="utf-8")
        (self.root / "queue" / "receipts" / "AOS-2026-0004.md").write_text("x" * 2_500, encoding="utf-8")
        items = [
            _item("AOS-2026-0001", "done", [
                {"path": "queue/receipts/AOS-2026-0001-old.md", "status": "done", "created_at": "2026-11-01T10:02:00Z"},
                {"path": "queue/receipts/AOS-2026-0001.md", "status": "done", "created_at": "2026-11-01T10:05:00Z"}]),
            _item("AOS-2026-0002", "agent_working", []),
            _item("AOS-2026-0003", "blocked", [{"path": "secret.md", "status": "done"}]),
            _item("AOS-2026-0004", "done", [{"path": "queue/receipts/AOS-2026-0004.md", "status": "done"}]),
            _item("AOS-2026-0005", "done", [{"path": "queue/receipts/../../secret.md", "status": "done"}]),
            _item("AOS-2026-0006", "done", [{"path": "queue/receipts/gone.md", "status": "done"}]),
        ]
        queue_file = self.root / "queue" / "work_items.jsonl"
        queue_file.write_text("".join(json.dumps(row) + "\n" for row in items), encoding="utf-8")
        for name, value in (("ROOT", self.root), ("QUEUE_FILE", queue_file)):
            patcher = patch.object(reader, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class QueueStatusTests(QueueFixture):
    def test_valid_id_returns_status_owner_timestamps_and_latest_receipt(self):
        result = shared_brain_queue.queue_status(" aos-2026-0001 ")
        self.assertTrue(result["success"] and result["found"])
        self.assertEqual((result["job_id"], result["status"], result["owner"], result["claimed_by"]),
                         ("AOS-2026-0001", "done", "operations", "worker-1"))
        self.assertEqual(result["timestamps"], {
            "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:05:00Z",
            "accepted_at": "2026-11-01T10:00:01Z", "claimed_at": "2026-11-01T10:01:00Z",
            "worker_heartbeat_at": "2026-11-01T10:04:00Z"})
        receipt = result["latest_receipt"]
        self.assertTrue(receipt["available"])
        self.assertEqual((receipt["path"], receipt["receipt_count"], receipt["created_at"]),
                         ("queue/receipts/AOS-2026-0001.md", 2, "2026-11-01T10:05:00Z"))
        self.assertIn("Result: PASS", receipt["content"])

    def test_secrets_redacted_and_private_payload_withheld(self):
        result = shared_brain_queue.queue_status("AOS-2026-0001")
        text = json.dumps(result)
        for secret in ("abcdef1234567890", "abcdefghijklmnop123", "sk-ABCDEFGHIJ", "ghp_ABCDEFGHIJ", PRIVATE):
            self.assertNotIn(secret, text)
        self.assertEqual(result["latest_receipt"]["redactions"], 4)
        self.assertIn("api_key = [redacted]", result["latest_receipt"]["content"])
        # Negative rehearsal: the unscrubbed source does carry them, so the check can fail.
        raw = (self.root / "queue/receipts/AOS-2026-0001.md").read_text(encoding="utf-8")
        self.assertIn("abcdef1234567890", raw)

    def test_missing_and_invalid_ids(self):
        self.assertEqual(shared_brain_queue.queue_status("AOS-2026-9999"),
                         {"success": True, "found": False, "job_id": "AOS-2026-9999"})
        for bad in ("", None, "AOS-1", "../queue/work_items.jsonl", "AOS-2026-0001; rm"):
            result = shared_brain_queue.queue_status(bad)
            self.assertFalse(result["success"] or result["found"], bad)
            self.assertIn("invalid job id", result["error"])

    def test_receipt_handling_edges(self):
        self.assertEqual(shared_brain_queue.queue_status("AOS-2026-0002")["latest_receipt"],
                         {"available": False, "reason": "no receipt", "receipt_count": 0})
        for job in ("AOS-2026-0003", "AOS-2026-0005"):
            receipt = shared_brain_queue.queue_status(job)["latest_receipt"]
            self.assertFalse(receipt["available"], job)
            self.assertEqual(receipt["reason"], "receipt outside queue receipt folders")
            self.assertNotIn("content", receipt)
        self.assertEqual(shared_brain_queue.queue_status("AOS-2026-0006")["latest_receipt"]["reason"], "unavailable")
        long = shared_brain_queue.queue_status("AOS-2026-0004")["latest_receipt"]
        self.assertTrue(long["truncated"])
        self.assertEqual(len(long["content"]), reader.MAX_RECEIPT_CHARS)

    def test_unauthenticated_call_refused_and_attributed_call_logged(self):
        self.assertEqual(shared_brain_http._call("queue_status", job_id="AOS-2026-0001"),
                         {"success": False, "error": "unauthorized"})
        token = shared_brain_http._attribution.set(STAMP)
        try:
            with patch.object(shared_brain_http._log, "info") as log_info:
                result = shared_brain_http._call("queue_status", job_id="AOS-2026-0001")
            self.assertTrue(result["found"])
            self.assertEqual(log_info.call_args.args[1:], ("queue_status", *STAMP.values()))
            with patch.object(shared_brain_queue, "queue_status", side_effect=RuntimeError("boom")):
                self.assertEqual(shared_brain_http._call("queue_status", job_id="AOS-2026-0001"),
                                 {"success": False, "error": "Queue read unavailable"})
        finally:
            shared_brain_http._attribution.reset(token)


class QueueHttpTests(QueueFixture):
    def test_both_addresses_discover_and_read_behind_access(self):
        ids = ["AOS-2026-10000", "AOS-2026-" + "1" * 180, "J-20261114T190000Z-deadbeef"]
        from aos_job_ids import storage_key
        (self.root / "queue/jobs").mkdir()
        for job_id in ids:
            (self.root / "queue/jobs" / f"{storage_key(job_id)}.json").write_text(json.dumps({
                "id": job_id, "status": "done", "request": "Offline API fixture", "artifacts": []}))
        async def run():
            app = FastAPI()
            with patch.object(shared_brain_http, "WRITE_ENABLED", True), \
                 patch.object(shared_brain_http, "SUBMIT_ENABLED", True):
                shared_brain_http.install(app)
            seen = []
            with patch.object(shared_brain_http, "verify_access_jwt",
                              side_effect=lambda value: "test@example.com" if value == "valid" else None), \
                 patch.object(shared_brain_http._log, "info", side_effect=lambda *a: seen.append(a)):
                async with app.router.lifespan_context(app):
                    transport = httpx2.ASGITransport(app=app)
                    async with httpx2.AsyncClient(transport=transport, base_url="https://brain.core-site.example") as http:
                        for surface in ("claude", "chatgpt"):
                            url = f"https://brain.core-site.example/api/brain/{surface}/"
                            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "queue_status", "arguments": {"job_id": "AOS-2026-0001"}}}
                            for headers in ({}, {"Cf-Access-Jwt-Assertion": "forged"}):
                                response = await http.post(url, json=body, headers=headers)
                                self.assertEqual(response.status_code, 401, (surface, headers))
                                self.assertNotIn("Result: PASS", response.text)
                            http.headers["Cf-Access-Jwt-Assertion"] = "valid"
                            async with streamable_http_client(url, http_client=http) as streams:
                                async with ClientSession(*streams) as session:
                                    await session.initialize()
                                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                                    self.assertIn("queue_status", tools, surface)
                                    self.assertIn("READ:", tools["queue_status"].description)
                                    self.assertEqual(list(tools["queue_status"].input_schema["properties"]), ["job_id"])
                                    result = (await session.call_tool("queue_status", {"job_id": "AOS-2026-0001"})).structured_content
                                    self.assertEqual(result, shared_brain_queue.queue_status("AOS-2026-0001"))
                                    missing = (await session.call_tool("queue_status", {"job_id": "AOS-2026-9999"})).structured_content
                                    self.assertFalse(missing["found"])
                                    for job_id in ids:
                                        current = (await session.call_tool("queue_status", {"job_id": job_id})).structured_content
                                        self.assertTrue(current["found"])
                                        self.assertEqual(current["job_id"], job_id)
                                        self.assertEqual(current["source"], "queue/jobs")
                            del http.headers["Cf-Access-Jwt-Assertion"]
            calls = [args[1:] for args in seen if args and "call=%s" in str(args[0])]
            self.assertIn(("queue_status", "test@example.com", "claude", "authorised_client", "address"), calls)
            self.assertIn(("queue_status", "test@example.com", "chatgpt", "authorised_client", "address"), calls)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
