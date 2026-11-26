"""Contract tests for ModelPort. No real model CLI is ever launched: every call
runs a local stand-in script, so the test-wide paid-model guard stays satisfied.

Revisit: when the ModelPort call contract or the Codex CLI flags change. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import aos_modelport as modelport  # noqa: E402
from aos_codex_policy import CODEX_TARGET  # noqa: E402

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["category", "summary"],
    "properties": {"category": {"type": "string"}, "summary": {"type": "string"}},
}

STAND_IN = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    mode = os.environ.get("STAND_IN_MODE", "ok")
    argv = sys.argv[1:]
    with open(os.environ["STAND_IN_ARGV"], "w") as handle:
        json.dump(argv, handle)
    answer = "not json" if mode == "bad_json" else json.dumps({"category": "sales", "summary": "ok"})
    if mode == "missing_key":
        answer = json.dumps({"category": "sales"})
    if "app-server" in argv:
        for raw in sys.stdin:
            message = json.loads(raw)
            method, ident = message.get("method"), message.get("id")
            def send(obj):
                sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
            if method == "initialize":
                send({"id": ident, "result": {"userAgent": "stand-in"}})
            elif method == "thread/start":
                with open(os.environ["STAND_IN_ARGV"] + ".thread", "w") as handle:
                    json.dump(message["params"], handle)
                send({"id": ident, "result": {"thread": {"id": "thr_1"}}})
            elif method == "turn/start":
                with open(os.environ["STAND_IN_ARGV"] + ".turn", "w") as handle:
                    json.dump(message["params"], handle)
                send({"id": ident, "result": {"turn": {"id": "turn_1", "status": "inProgress"}}})
                if mode == "hang":
                    time.sleep(30)
                send({"method": "item/completed", "params": {"threadId": "thr_1", "turnId": "turn_1",
                      "item": {"type": "agentMessage", "id": "m1", "text": answer}}})
                send({"method": "thread/tokenUsage/updated", "params": {"threadId": "thr_1", "turnId": "turn_1",
                      "tokenUsage": {"total": {"inputTokens": 900, "cachedInputTokens": 600, "outputTokens": 40,
                                               "reasoningOutputTokens": 10, "totalTokens": 940}}}})
                send({"method": "turn/completed", "params": {"threadId": "thr_1",
                      "turn": {"id": "turn_1", "status": "failed" if mode == "quota" else "completed",
                               "error": {"message": "usage limit reached"} if mode == "quota" else None}}})
        sys.exit(0)
    sys.stdin.read()
    if mode == "hang":
        time.sleep(30)
    if mode == "quota":
        sys.stderr.write("ERROR: You have hit your usage limit.\\n")
        sys.exit(1)
    print(json.dumps({"type": "thread.started", "thread_id": "thr_exec"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 800,
                      "output_tokens": 50, "reasoning_output_tokens": 0}}))
    open(argv[argv.index("--output-last-message") + 1], "w").write(answer)
''')


class ModelPortTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cli = base / "stand_in_model_cli"
        self.cli.write_text(STAND_IN, encoding="utf-8")
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        self.argv_file = base / "argv.json"
        self.root = base / "root"
        (self.root / "queue").mkdir(parents=True)
        self.target = replace(CODEX_TARGET, executable=self.cli)

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, mode="ok", **extra):
        return {"PATH": os.environ.get("PATH", ""), "STAND_IN_MODE": mode,
                "STAND_IN_ARGV": str(self.argv_file), **extra}

    def call(self, mode="ok", **kwargs):
        options = {"model": "gpt-6-luna", "effort": "low", "output_schema": SCHEMA, "root": self.root,
                   "target": self.target, "env": self.env(mode), "agent": "crm.fit_call_prep", "timeout": 20}
        options.update(kwargs)
        return modelport.call("Classify this note.", **options)

    def ledger_rows(self):
        path = self.root / "queue" / "token_ledger.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_unbounded_job_sequences_reach_the_existing_ledger_without_reattribution(self):
        for item_id in ("AOS-2026-9999", "AOS-2026-10000", "AOS-2026-" + "1" * 180):
            with self.subTest(item_id=item_id):
                result = self.call(work_item_id=item_id)
                self.assertTrue(result["ok"], result)
                row = [row for row in self.ledger_rows() if row.get("invocation_id") == result["invocation_id"]][0]
                self.assertEqual(row["item_id"], item_id)
                self.assertEqual(row["exact_usage"]["canonical_total"], 1250)

    def test_exec_call_returns_schema_json_and_records_exact_usage(self):
        result = self.call()
        self.assertTrue(result["ok"], result)
        self.assertEqual({"category": "sales", "summary": "ok"}, result["json"])
        self.assertEqual(1200, result["usage"]["input_tokens"])
        self.assertEqual({"recorded": True, "exact": True}, result["ledger"])
        rows = [row for row in self.ledger_rows() if row.get("event") == "model_invocation"]
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(1250, row["exact_usage"]["canonical_total"])
        self.assertEqual(800, row["exact_usage"]["cached_input"])
        self.assertEqual("gpt-6-luna", row["model_selection"]["model"])
        self.assertEqual("pinned", row["model_selection"]["tier"])
        self.assertEqual("modelport:codex:crm.fit_call_prep", row["surface"])
        self.assertEqual(result["invocation_id"], row["invocation_id"])

    def test_exec_command_is_read_only_tool_less_and_pinned(self):
        self.call()
        argv = json.loads(self.argv_file.read_text())
        self.assertEqual("read-only", argv[argv.index("--sandbox") + 1])
        self.assertEqual("never", argv[argv.index("--ask-for-approval") + 1])
        self.assertEqual("gpt-6-luna", argv[argv.index("--model") + 1])
        self.assertIn('forced_login_method="chatgpt"', argv)
        self.assertIn('model_provider="openai"', argv)
        self.assertIn("model_reasoning_effort=low", argv)
        self.assertIn("--ephemeral", argv)
        self.assertIn("--output-schema", argv)
        disabled = {argv[i + 1] for i, value in enumerate(argv) if value == "--disable"}
        self.assertLessEqual({"shell_tool", "unified_exec", "multi_agent", "apps", "plugins"}, disabled)
        self.assertTrue(any(value.startswith("model_instructions_file=") for value in argv))
        workdir = Path(argv[argv.index("-C") + 1])
        self.assertNotIn(str(ROOT), str(workdir))

    def test_both_backends_require_subscription_login_and_openai_provider(self):
        for profile in ('lean', 'default'):
            command = modelport.build_app_server_command(CODEX_TARGET, profile=profile)
            self.assertIn('forced_login_method="chatgpt"', command)
            self.assertIn('model_provider="openai"', command)

    def test_default_profile_sends_no_lean_overrides(self):
        self.call(profile="default")
        argv = json.loads(self.argv_file.read_text())
        self.assertNotIn("--disable", argv)
        self.assertFalse(any(value.startswith("model_instructions_file=") for value in argv))

    def test_refuses_when_a_billed_route_variable_is_set(self):
        result = self.call(env=self.env(OPENAI_API_KEY="sk-not-real"))
        self.assertEqual("refused", result["status"])
        self.assertIn("OPENAI_API_KEY", result["detail"])
        self.assertFalse(self.argv_file.exists(), "the CLI must not be launched")
        self.assertEqual([], self.ledger_rows())

    def test_refuses_a_model_outside_the_route_table(self):
        result = self.call(model="gpt-6-astra")
        self.assertEqual("refused", result["status"])
        self.assertFalse(self.argv_file.exists())

    def test_terra_is_pinnable_and_tiers_are_unchanged(self):
        self.assertIn("gpt-6.1-terra", modelport.allowed_models(ROOT))
        self.assertTrue(self.call(model="gpt-6.1-terra", effort="medium")["ok"])

    def test_refuses_bad_effort_backend_and_schema(self):
        self.assertEqual("refused", self.call(effort="ultra")["status"])
        self.assertEqual("refused", self.call(backend="api")["status"])
        self.assertEqual("refused", self.call(output_schema={"type": "string"})["status"])

    def test_non_json_and_missing_required_keys_are_invalid_output_with_usage_recorded(self):
        for mode in ("bad_json", "missing_key"):
            result = self.call(mode)
            self.assertEqual("invalid_output", result["status"], mode)
            self.assertFalse(result["ok"])
            self.assertTrue(result["ledger"]["exact"], "tokens were spent and must be accounted")

    def test_quota_failure_is_truthful_and_never_retried_elsewhere(self):
        result = self.call("quota")
        self.assertEqual("failed", result["status"])
        self.assertEqual("quota_unavailable", result["failure_class"])
        self.assertIsNone(result["json"])
        rows = [row for row in self.ledger_rows() if row.get("event") == "model_invocation"]
        self.assertEqual(1, len(rows), "one launch, one ledger row, no second attempt")
        self.assertIsNone(rows[0]["exact_usage"]["canonical_total"])

    def test_timeout_stops_the_process_and_reports_timeout(self):
        result = self.call("hang", timeout=1.5)
        self.assertEqual("timeout", result["status"])
        self.assertLess(result["latency_seconds"], 10)

    def test_app_server_call_uses_fresh_ephemeral_read_only_thread(self):
        result = self.call(backend="app-server")
        self.assertTrue(result["ok"], result)
        self.assertEqual(900, result["usage"]["input_tokens"])
        self.assertEqual(600, result["usage"]["cached_input_tokens"])
        thread = json.loads(Path(str(self.argv_file) + ".thread").read_text())
        self.assertEqual("read-only", thread["sandbox"])
        self.assertEqual("never", thread["approvalPolicy"])
        self.assertTrue(thread["ephemeral"])
        self.assertIn("precise function", thread["baseInstructions"])
        turn = json.loads(Path(str(self.argv_file) + ".turn").read_text())
        self.assertEqual(("gpt-6-luna", "low"), (turn["model"], turn["effort"]))
        self.assertEqual(SCHEMA, turn["outputSchema"])
        row = [row for row in self.ledger_rows() if row.get("event") == "model_invocation"][0]
        self.assertEqual(940, row["exact_usage"]["canonical_total"])

    def test_warm_app_server_session_serves_several_calls(self):
        with modelport.AppServerSession(target=self.target, env=self.env()) as session:
            first = self.call(backend="app-server", session=session)
            second = self.call(backend="app-server", session=session)
        self.assertTrue(first["ok"] and second["ok"])
        self.assertNotEqual(first["invocation_id"], second["invocation_id"])

    def test_app_server_failed_turn_and_timeout_are_reported(self):
        self.assertEqual("quota_unavailable", self.call("quota", backend="app-server")["failure_class"])
        self.assertEqual("timeout", self.call("hang", backend="app-server", timeout=1.5)["status"])


if __name__ == "__main__":
    unittest.main()
