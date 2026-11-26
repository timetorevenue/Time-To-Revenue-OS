"""Permanent IDs: offline boundary, cross-process allocation, crash and read contracts."""
from __future__ import annotations

import concurrent.futures
import ast
import json
import multiprocessing
import os
import re
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import aos_job_ids as ids
import aos_jobs as jobs
import shared_brain_queue as brain
from aos_agents.journal import StepJournal

EXECUTOR = {"kind": "agent", "agent": "shared.cite_verify@0.1.0", "input": {}}


def create(root, key=""):
    return jobs.create(Path(root), conversation_id="offline", topic="id-test", request="offline ID proof",
                       executor=EXECUTOR, dedupe_key=key)


def allocate_batch(root, count, output):
    try:
        output.put([create(root)[0]["id"] for _ in range(count)])
    except Exception as exc:
        output.put({"error": repr(exc)})


def die_after_reservation(root):
    def killed_save(*_args):
        os.kill(os.getpid(), signal.SIGKILL)
    jobs.save = killed_save
    create(root)


class PermanentIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "queue").mkdir()
        self.year = jobs.dt.datetime.now(jobs.dt.timezone.utc).strftime("%Y")

    def seed(self, number):
        path = self.root / "queue/work_items.jsonl"
        text = json.dumps({"id": f"AOS-{self.year}-{number}", "status": "done"}) + "\n"
        path.write_text(text)
        return path, text

    def test_9999_to_10000_and_restart_preserves_historical_records(self):
        path, before = self.seed("9999")
        first = create(self.root)[0]
        self.assertEqual(first["id"], f"AOS-{self.year}-10000")
        self.assertEqual(path.read_text(), before)
        context = multiprocessing.get_context("spawn")
        output = context.Queue()
        process = context.Process(target=allocate_batch, args=(str(self.root), 1, output))
        process.start()
        result = output.get(timeout=20)
        process.join(20)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(result, [f"AOS-{self.year}-10001"])
        self.assertEqual(jobs.load(self.root, first["id"]), first)

    def test_history_reserves_deleted_ids_from_receipts_journals_results_and_ledgers(self):
        self.seed("9999")
        for folder, number, suffix in (("queue/receipts", "10000", "-notification.md"),
                                       ("queue/threads", "10001", ".jsonl"),
                                       ("results/agents", "10002", "")):
            path = self.root / folder / f"AOS-{self.year}-{number}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("preserved")
        (self.root / "queue/token_ledger.jsonl").write_text(json.dumps({"item_id": f"AOS-{self.year}-10003"}) + "\n")
        (self.root / "queue/receipts/.task-deletion-fingerprint.pending").write_text(json.dumps({"item_id": f"AOS-{self.year}-10004"}))
        self.assertEqual(create(self.root)[0]["id"], f"AOS-{self.year}-10005")

    def test_sequence_has_no_numeric_digit_ceiling_or_filename_ceiling(self):
        self.seed("9" * 5000)
        job = create(self.root)[0]
        self.assertEqual(job["id"], f"AOS-{self.year}-1" + "0" * 5000)
        self.assertEqual(jobs.load(self.root, job["id"])["id"], job["id"])
        self.assertLess(len(jobs._path(self.root, job["id"]).name), 255)
        journal = StepJournal(self.root, job["id"])
        journal.append(plan_ref="offline", step="one", agent="tool", package_sha256="p", input_sha256="i",
                       result={"status": "ok"})
        self.assertIn("one", journal.completed("offline"))
        job["status"] = "done"
        jobs.write_receipt(self.root, job)
        jobs.save(self.root, job)
        with patch.object(brain, "ROOT", self.root):
            read = brain.queue_status(job["id"])
        self.assertTrue(read["found"])
        self.assertEqual(read["job_id"], job["id"])
        self.assertTrue(read["receipt"]["available"])

    def test_allocation_is_unique_across_threads_and_processes(self):
        self.seed("9999")
        context = multiprocessing.get_context("spawn")
        output = context.Queue()
        processes = [context.Process(target=allocate_batch, args=(str(self.root), 12, output)) for _ in range(4)]
        for process in processes:
            process.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            allocated = list(pool.map(lambda _: create(self.root)[0]["id"], range(24)))
        for _ in processes:
            result = output.get(timeout=30)
            self.assertIsInstance(result, list, result)
            allocated.extend(result)
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(set(allocated)), 72)
        self.assertEqual({int(value.rsplit("-", 1)[1]) for value in allocated}, set(range(10000, 10072)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            claimed = list(pool.map(lambda _: jobs.claim(self.root, allocated[0]), range(24)))
        self.assertEqual(sum(value is not None for value in claimed), 1)

    def test_duplicate_submission_and_crashes_do_not_duplicate_or_reuse_ids(self):
        self.seed("9999")
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            repeats = list(pool.map(lambda _: create(self.root, "same-delivery"), range(24)))
        self.assertEqual(len({job["id"] for job, _ in repeats}), 1)
        self.assertEqual(sum(created for _, created in repeats), 1)
        with patch.object(jobs, "save", side_effect=RuntimeError("crash after reservation")):
            with self.assertRaises(RuntimeError):
                create(self.root)
        self.assertEqual(create(self.root)[0]["id"], f"AOS-{self.year}-10002")
        real_replace = jobs.durable_replace_text

        def crash_before_key(path, text):
            if path.parent.name == "keys":
                raise RuntimeError("crash before dedupe publication")
            return real_replace(path, text)

        with patch.object(jobs, "durable_replace_text", side_effect=crash_before_key):
            with self.assertRaises(RuntimeError):
                create(self.root, "interrupted-delivery")
        recovered, created = create(self.root, "interrupted-delivery")
        self.assertFalse(created)
        self.assertEqual(recovered["id"], f"AOS-{self.year}-10003")
        self.assertEqual(len(jobs.list_jobs(self.root)), 3)

    def test_corrupt_counter_fails_closed_and_unsafe_ids_are_refused(self):
        create(self.root)
        state = self.root / "queue/jobs/.sequence.json"
        state.write_text('{"version":1,"years":{ "' + self.year + '": 9999 }}')
        with self.assertRaises(jobs.JobError):
            create(self.root)
        for value in ("AOS-2026-10000/../secret", "AOS-2026-１００００", "AOS-2026-10000\n", "J-../../secret"):
            with self.subTest(value=value), self.assertRaises(jobs.JobError):
                jobs.load(self.root, value)

    def test_sigkill_releases_allocation_lock_but_keeps_the_reserved_sequence(self):
        self.seed("9999")
        context = multiprocessing.get_context("spawn")
        process = context.Process(target=die_after_reservation, args=(str(self.root),))
        process.start()
        process.join(20)
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        self.assertEqual(create(self.root)[0]["id"], f"AOS-{self.year}-10001")

    def test_dedupe_key_cannot_return_another_conversations_job(self):
        create(self.root, "delivery")
        with self.assertRaises(jobs.JobError):
            jobs.create(self.root, conversation_id="someone-else", topic="id-test", request="offline ID proof",
                        executor=EXECUTOR, dedupe_key="delivery")

    def test_old_numeric_and_j_jobs_remain_readable_without_renaming(self):
        old_id = "J-20261114T190000Z-deadbeef"
        old = {"id": old_id, "status": "done", "request": "old job", "created_at": "2026-11-14", "artifacts": []}
        jobs.save(self.root, old)
        historical, before = self.seed("0001")
        with patch.object(brain, "ROOT", self.root), patch.object(brain, "QUEUE_FILE", historical):
            self.assertTrue(brain.queue_status(old_id)["found"])
            self.assertEqual(brain.queue_status(f"AOS-{self.year}-0001")["source"], "queue/work_items.jsonl")
        self.assertEqual(jobs.load(self.root, old_id)["id"], old_id)
        self.assertEqual(historical.read_text(), before)

    def test_connector_authorization_loads_the_new_record_and_keeps_the_job_id(self):
        import aos_authority
        import ttros_connections

        self.seed("9999")
        job = create(self.root)[0]
        allowed = aos_authority.action_authorization(self.root, job, action="GMAIL_CREATE_EMAIL_DRAFT", target="person@example.test")
        self.assertTrue(allowed["authorized"])
        denied = aos_authority.action_authorization(self.root, job, action="GMAIL_SEND_EMAIL", target="person@example.test")
        self.assertFalse(denied["authorized"])
        calls = []
        with patch.object(ttros_connections, "perform_operator",
                          lambda root, tool, arguments, **kw: calls.append(kw) or {"id": "cr_1", "result": {}}):
            jobs.composio_perform(self.root, "GMAIL_CREATE_EMAIL_DRAFT", "person@example.test", {}, job_id=job["id"], job=job)
        self.assertEqual(calls[0]["item"]["id"], job["id"])
        self.assertEqual(calls[0]["idempotency_key"], job["id"])

    def test_notification_and_brain_reference_parsers_keep_the_whole_numeric_sequence(self):
        # Execute only the pure reference parsers; never import bridge credentials or transports.
        tree = ast.parse((ROOT / "connectors/telegram_bridge/telegram_bridge.py").read_text())
        namespace = {"re": re}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_AOS_ID_RE" for target in node.targets):
                exec(compile(ast.Module(body=[node], type_ignores=[]), "bridge-reference-parser", "exec"), namespace)
            if isinstance(node, ast.FunctionDef) and node.name in {"_extract_work_item_id", "_extract_work_item_title"}:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "bridge-reference-parser", "exec"), namespace)
        from dashboard.backend.business_brain_graph import AOS_ACTIVITY_RE
        from morning_brief_detector import ITEM_RE
        for number in ("9999", "10000", "1" * 180):
            item_id = f"AOS-{self.year}-{number}"
            self.assertEqual(namespace["_extract_work_item_id"](f"[{item_id} — Offline title]"), item_id)
            self.assertEqual(namespace["_extract_work_item_title"](f"[{item_id} — Offline title]"), "Offline title")
            self.assertEqual(AOS_ACTIVITY_RE.search(item_id).group(), item_id)
            self.assertEqual(ITEM_RE.search(item_id).group(), item_id)

    def test_restart_receipt_reads_lifetime_accounting_without_rewriting_the_ledger(self):
        from step6_cost_control import Scope, record_invocation

        self.seed("9999")
        job = create(self.root)[0]
        running = jobs.claim(self.root, job["id"])
        record_invocation(Scope("session", "before-restart"), invocation_id="paid-stub-1", provider="openai-codex",
                          model="gpt-6-luna", usage={"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 0},
                          work_item_id=job["id"], root=self.root)
        ledger = self.root / "queue/token_ledger.jsonl"
        before = ledger.read_bytes()
        running["claim"] = {"pid": 999999999, "start_id": "gone"}
        jobs.save(self.root, running)
        self.assertEqual(jobs.recover(self.root)["requeued"], [job["id"]])
        replayed = jobs.claim(self.root, job["id"])
        replayed.update(status="done", claim=None, usage={"model_calls": 0, "total_tokens": 0, "exact": True})
        jobs.write_receipt(self.root, replayed)
        jobs.save(self.root, replayed)
        self.assertEqual(replayed["accounting"]["known_tokens"], 120)
        self.assertEqual(replayed["accounting"]["invocation_count"], 1)
        receipt = (self.root / replayed["receipts"][0]["path"]).read_text()
        self.assertIn("120 known", receipt)
        self.assertIn("120 known", jobs.notice_text(replayed))
        self.assertEqual(ledger.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
