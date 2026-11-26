"""One invocation, one authoritative row, one ledger, one lock.

These regressions pin the property the token accounting depends on: every
production execution route records exactly one row for one model invocation, in
``queue/token_ledger.jsonl``, behind ``queue/token_ledger.jsonl.lock``, and the
500,000-token fuse sees every invocation attributable to a named work item.

Revisit: on a new execution route or a change to the canonical ledger contract.
· Last touched: 2026-09-22.
"""

from __future__ import annotations

import importlib.util
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import step6_cost_control as step6  # noqa: E402
import aos_orchestration  # noqa: E402
from tools import source_intake_semantic  # noqa: E402

LEDGER = Path("queue/token_ledger.jsonl")


def _make_root(tmp: str) -> Path:
    root = Path(tmp) / "agentic-os-live"
    (root / "queue").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "queue" / "notifications.json", root / "queue" / "notifications.json")
    shutil.copy(ROOT / "scripts" / "model_prices.json", root / "scripts" / "model_prices.json")
    (root / LEDGER).write_text("", encoding="utf-8")
    return root


def _rows(root: Path) -> list[dict]:
    text = (root / LEDGER).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class _StubContext:
    """Stand in for the frozen AssembledContext the launcher boundary requires."""

    request = "do the work"
    session_id = "dashboard-session"
    surface = "queue:claude"


def _usage(model: str = "gpt-5-codex") -> dict:
    return {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 200, "provider": "openai-codex"}


class CanonicalLedgerLockTests(unittest.TestCase):
    """The canonical ledger has exactly one write boundary and loses no writer."""



    def test_queue_storage_docstring_no_longer_claims_a_package_operation_lock(self):
        text = (ROOT / "tools" / "aos_queue_storage.py").read_text(encoding="utf-8")
        header = text.split('"""')[1]
        self.assertNotIn("Lock order is package-operation lock first", header)
        self.assertIn("token_ledger.jsonl.lock", header)


class OneRowPerInvocationTests(unittest.TestCase):
    """Step 6 is authoritative; a second parse of the same call defers to it."""

    def test_second_parse_of_a_step6_recorded_invocation_is_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            step6.record_invocation(
                step6.Scope("work_item", "AOS-2026-0902"),
                invocation_id="codex-abc",
                provider="openai-codex",
                model="gpt-5-codex",
                usage=_usage(),
                root=root,
            )
            recorded, authoritative = step6.append_canonical_row(
                root,
                {"item_id": "AOS-2026-0902", "task_id": "second parse", "tokens": 1200},
                effect_id="backend_token_usage:codex-abc",
                defer_to_invocation_id="codex-abc",
            )
            self.assertFalse(recorded)
            self.assertEqual(authoritative["event"], "model_invocation")
            rows = _rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                step6.fuse_status(step6.Scope("work_item", "AOS-2026-0902"), root=root)["canonical_tokens"],
                1200,
            )

    def test_an_invocation_step6_never_saw_is_still_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            recorded, row = step6.append_canonical_row(
                root,
                {"item_id": "AOS-2026-0903", "task_id": "uncovered route", "tokens": 42},
                effect_id="backend_token_usage:orphan-1",
                defer_to_invocation_id="orphan-1",
            )
            self.assertTrue(recorded)
            self.assertEqual(row["effect_id"], "backend_token_usage:orphan-1")
            self.assertEqual(len(_rows(root)), 1)

    def test_the_same_effect_is_never_recorded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            first, _ = step6.append_canonical_row(root, {"item_id": "AOS-2026-0904"}, effect_id="once")
            second, _ = step6.append_canonical_row(root, {"item_id": "AOS-2026-0904"}, effect_id="once")
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(_rows(root)), 1)

    def test_memory_intake_sidecar_records_once_and_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_root(tmp)
            usage_file = root / "queue" / "context_assemblies" / "semantic.usage.json"
            usage_file.parent.mkdir(parents=True)
            usage_file.write_text(json.dumps({
                "provider": "openai-codex",
                "model": "gpt-5.5",
                "input_tokens": 15_868,
                "output_tokens": 2_098,
                "cache_read_tokens": 0,
                "reasoning_tokens": 187,
                "total_tokens": 17_966,
                "session_id": "20260922_173614_02857d",
            }), encoding="utf-8")

            first = source_intake_semantic._record_semantic_usage(
                usage_file,
                source_id="fixture-source",
                fallback_invocation_id="fallback-unused",
                model="gpt-5.5",
                root=root,
            )
            replay = source_intake_semantic._record_semantic_usage(
                usage_file,
                source_id="fixture-source",
                fallback_invocation_id="fallback-unused",
                model="gpt-5.5",
                root=root,
            )

            self.assertTrue(first["recorded"])
            self.assertTrue(replay["idempotent"])
            rows = _rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["invocation_id"], "20260922_173614_02857d")
            self.assertEqual(rows[0]["input_plus_output"], 17_966)
            self.assertEqual(rows[0]["surface"], "memory-intake:semantic")






if __name__ == "__main__":
    unittest.main()
