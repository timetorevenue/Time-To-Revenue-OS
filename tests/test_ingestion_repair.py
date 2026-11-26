"""Focused tests for the November 2026 ingestion repair.

Revisit: when semantic intake, Brain transactions or the Drive intake watcher change. · Created 2026-11-11.
Covers: Hermes retired from ingestion (no executable launch, no model call on the normal path),
semantic enrichment only from an explicitly supplied extraction, refusal on a blocked Git index
before anything is written, extraction reuse, rollback that unstages only its own paths, 429
accounting, and deterministic-by-default capture.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_source_intake import (
    HISTORICAL_INDEX_FIXTURE, NOW, ROOT, SourceIntakeTest, historical_source_record, write,
)
from tools import brain_memory, drive_memory_intake as drive
from tools import source_intake_semantic as sis
from tools.source_intake import run_intake

PAYLOAD = {
    "claims": [{
        "claim": "Fixture Person proposed starting the pilot next month.",
        "type": "advice_received", "attribution": "Fixture Person", "status": "historical",
        "evidence": {"quote": "We should try the pilot next month.", "locator": "first turn"},
    }],
    "card": {
        "one_line_description": "Fixture call", "summary": "Short summary.", "key_topics": ["pilot"],
        "who_said_what": ["Fixture Person"], "decisions": [], "advice_and_opinions": [],
        "casey_positions": [], "commitments_and_actions": [], "open_questions": [],
        "conflicts_and_caveats": [], "promotion_candidates": [],
    },
}
RAW = "Fixture Person\nWe should try the pilot next month.\n\nYou\nAgreed.\n"


def git(brain: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(brain), *args], check=True, capture_output=True, text=True).stdout


class Scenario:
    """A fixture Brain + repo holding one historical source, ready for semantic mode."""

    def __init__(self, case: unittest.TestCase, root: Path):
        self.repo, self.brain, self.graph, _ = SourceIntakeTest("fixture").fixture(root)
        self.registry = self.repo / "context/client_scope_registry.json"
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        g = data["scopes"]["global"]
        g["brain_pointer_prefixes"].append("business_brain:sources/historical_calls/cards/")
        g["search_source_identities"].append({"source": "business_brain", "path_prefix": "business_brain:sources/historical_calls/cards/"})
        g["graphify_targets"][0]["paths"].extend([
            "business_brain:sources/historical_calls/cards/fixture-call.card.md",
            "business_brain:sources/historical_calls/INDEX.md",
        ])
        write(self.registry, json.dumps(data, indent=2) + "\n")
        record, _ = historical_source_record("fixture-call", RAW)
        write(self.brain / "sources/historical_calls/fixture-call.md", record)
        write(self.brain / "sources/historical_calls/INDEX.md", HISTORICAL_INDEX_FIXTURE)
        self.source = self.brain / "sources/historical_calls/fixture-call.md"
        self.card = self.brain / "sources/historical_calls/cards/fixture-call.card.md"

    def run(self, commit: bool = False, payload=None):
        return run_intake(
            self.source, repo_root=self.repo, brain_root=self.brain, registry_path=self.registry,
            schema_path=ROOT / "context/client_scope_registry.schema.json",
            search_db=self.repo / "search/os_index.db", graphify_root=self.graph,
            mode="semantic", commit=commit, now=lambda: NOW, semantic_payload=payload,
        )


def spawned_commands(run_mock) -> list[str]:
    return [str(c.args[0][0]) if c.args and isinstance(c.args[0], (list, tuple)) and c.args[0] else "" for c in run_mock.call_args_list]


class BlockedGitRefusesBeforeAnyWork(unittest.TestCase):
    def test_staged_index_refuses_and_spawns_nothing_but_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Scenario(self, Path(tmp))
            write(s.brain / "staged.md", "---\nid: staged\n---\nStaged by someone.\n")
            git(s.brain, "add", "staged.md")
            with mock.patch.object(sis.subprocess, "run", wraps=subprocess.run) as run:
                with self.assertRaisesRegex(sis.SemanticBlocked, r"1 staged path\(s\).*nothing written"):
                    run_intake(s.source, repo_root=s.repo, brain_root=s.brain, registry_path=s.registry,
                               schema_path=ROOT / "context/client_scope_registry.schema.json",
                               search_db=s.repo / "search/os_index.db", graphify_root=s.graph,
                               mode="semantic", commit=True, now=lambda: NOW, semantic_payload=PAYLOAD)
            self.assertEqual(set(spawned_commands(run)), {"git"})
            self.assertEqual(git(s.brain, "diff", "--cached", "--name-only").split(), ["staged.md"])
            self.assertFalse(s.card.exists())


class SemanticIsExplicitAndModelFree(unittest.TestCase):
    def test_hermes_launcher_is_gone(self):
        self.assertFalse(hasattr(sis, "call_hermes_semantic"))
        self.assertFalse(hasattr(sis, "HERMES_PROFILE"))

    def test_no_executable_hermes_or_model_launch_in_ingestion_modules(self):
        for name in ("source_intake", "source_intake_semantic", "drive_memory_intake", "drive_watcher",
                     "memory_exchange_import", "memory_filing"):
            text = (ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
            for forbidden in ("call_hermes", "HERMES_PROFILE", '"hermes"', "'hermes'", '"codex"', "'codex'", '"claude"', "'claude'"):
                self.assertNotIn(forbidden, text, f"{name}.py contains {forbidden}")
        ps = (ROOT / "tools/Watch-DriveMemoryIntake.ps1").read_text(encoding="utf-8").lower()
        self.assertNotIn("hermes", ps)

    def test_semantic_mode_without_supplied_extraction_writes_nothing_and_calls_no_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Scenario(self, Path(tmp))
            with mock.patch.object(sis.subprocess, "run") as run:
                with self.assertRaisesRegex(sis.SemanticNotAuthorized, "no model is called"):
                    s.run()
            run.assert_not_called()
            self.assertFalse(s.card.exists())
            self.assertFalse((s.repo / sis.STATE_RELATIVE).exists())

    def test_supplied_extraction_writes_the_card_with_zero_model_invocations(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Scenario(self, Path(tmp))
            result = s.run(payload=PAYLOAD)
            self.assertTrue(s.card.is_file())
            self.assertEqual(result.model_invocations, 0)
            self.assertEqual(result.token_usage_text, "Token usage: no agent invocation")

    def test_supplied_extraction_survives_a_failed_git_transaction_and_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Scenario(self, Path(tmp))
            with mock.patch.object(brain_memory, "write_transaction", side_effect=brain_memory.BrainMemoryError("git refused")):
                with self.assertRaises(brain_memory.BrainMemoryError):
                    s.run(payload=PAYLOAD)
            self.assertEqual(len(list((s.repo / sis.STATE_RELATIVE).glob("*.payload.json"))), 1)
            result = s.run()  # nothing supplied this time: the saved extraction is reused
            self.assertTrue(s.card.is_file())
            self.assertEqual(result.model_invocations, 0)
            self.assertEqual(list((s.repo / sis.STATE_RELATIVE).glob("*.payload.json")), [])

    def test_default_capture_spawns_no_model_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, brain, graph, _ = SourceIntakeTest("fixture").fixture(root)
            source = root / "in" / "note.txt"
            write(source, "Plain note.\n")
            real = subprocess.run
            with mock.patch("subprocess.run", wraps=real) as run, mock.patch("subprocess.Popen", wraps=subprocess.Popen) as popen:
                result = run_intake(source, repo_root=repo, brain_root=brain,
                                    registry_path=repo / "context/client_scope_registry.json",
                                    schema_path=ROOT / "context/client_scope_registry.schema.json",
                                    search_db=repo / "search/os_index.db", graphify_root=graph, commit=False, now=lambda: NOW)
            self.assertEqual(result.model_invocations, 0)
            for launched in spawned_commands(run) + [str(c.args[0][0]) for c in popen.call_args_list if c.args and isinstance(c.args[0], (list, tuple))]:
                self.assertNotRegex(Path(launched).name.lower(), r"hermes|codex|claude|openai")


class RateLimitAccounting(unittest.TestCase):
    QUOTA = {"failed": True, "failure": "Codex provider quota exhausted (429); retry after 2827s. Credentials are still valid.",
             "input_tokens": None, "output_tokens": None, "total_tokens": None}

    def test_429_is_not_recorded_as_zero_or_as_lost_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "u.json"
            usage.write_text(json.dumps(self.QUOTA))
            with mock.patch.object(sis, "record_invocation") as measured, \
                 mock.patch.object(sis, "record_unavailable_invocation") as lost:
                out = sis._record_semantic_usage(usage, source_id="d", fallback_invocation_id="f", model="m", root=Path(tmp))
            measured.assert_not_called()
            lost.assert_not_called()
            self.assertTrue(out["provider_rejected"])
            self.assertEqual(out["retry_after"], 2827)

    def test_missing_report_is_still_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sis, "record_unavailable_invocation", return_value={"row": {}}) as lost:
                sis._record_semantic_usage(Path(tmp) / "none.json", source_id="d", fallback_invocation_id="f", model="m", root=Path(tmp))
            lost.assert_called_once()


class TransactionRollback(unittest.TestCase):
    NOTE = "---\nid: note-{n}\n---\n# Note\n\nbody{tail}\n"

    def brain(self, tmp: str) -> Path:
        brain = Path(tmp) / "brain"
        brain.mkdir()
        subprocess.run(["git", "init", "-q", str(brain)], check=True)
        git(brain, "config", "user.email", "t@example.invalid")
        git(brain, "config", "user.name", "T")
        write(brain / "base.md", self.NOTE.format(n="base", tail=""))
        git(brain, "add", "base.md")
        git(brain, "commit", "-qm", "base")
        return brain

    def test_failed_transaction_unstages_only_its_own_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = self.brain(tmp)
            real_git = brain_memory._git

            def failing_commit(*args, **kwargs):
                if "commit" in args:  # the add has already happened when the commit fails
                    raise brain_memory.BrainMemoryError("injected commit failure")
                return real_git(*args, **kwargs)

            with mock.patch.object(brain_memory, "VAULT_ROOT", brain), \
                 mock.patch.object(brain_memory, "_git", side_effect=failing_commit):
                with self.assertRaisesRegex(brain_memory.BrainMemoryError, "injected commit failure"):
                    brain_memory.write_transaction({"new.md": self.NOTE.format(n="new", tail="")},
                                                   source="t", session_id="s", commit=True)
            self.assertEqual(git(brain, "diff", "--cached", "--name-only"), "")
            self.assertFalse((brain / "new.md").exists())
            self.assertEqual(git(brain, "status", "--porcelain"), "")

    def test_failed_uncommitted_transaction_leaves_unrelated_staged_work_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = self.brain(tmp)
            write(brain / "mine.md", self.NOTE.format(n="mine", tail=""))
            git(brain, "add", "mine.md")
            with mock.patch.object(brain_memory, "VAULT_ROOT", brain):
                with self.assertRaises(brain_memory.BrainMemoryError):
                    brain_memory.write_transaction({"new.md": self.NOTE.format(n="new", tail="")}, source="t",
                                                   session_id="s", commit=False, failure_injection="after_first_replace")
            self.assertEqual(git(brain, "diff", "--cached", "--name-only").split(), ["mine.md"])
            self.assertFalse((brain / "new.md").exists())

    def test_existing_staged_work_is_refused_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = self.brain(tmp)
            write(brain / "mine.md", self.NOTE.format(n="mine", tail=""))
            git(brain, "add", "mine.md")
            with mock.patch.object(brain_memory, "VAULT_ROOT", brain):
                with self.assertRaisesRegex(brain_memory.BrainMemoryError, r"1 staged path\(s\).*staged work left untouched"):
                    brain_memory.write_transaction({"new.md": self.NOTE.format(n="new", tail="")},
                                                   source="t", session_id="s", commit=True)
            self.assertEqual(git(brain, "diff", "--cached", "--name-only").split(), ["mine.md"])
            self.assertFalse((brain / "new.md").exists())


class DeterministicByDefault(unittest.TestCase):
    def outcome(self, **kwargs):
        from dashboard.backend import main as backend
        capture = mock.Mock(imported_pointers=("business_brain:sources/intake/records/a.md",), duplicate_pointers=(),
                            search_status="ready", graphify_status="build", brain_commit="abc",
                            model_invocations=0, token_usage_text="Token usage: no agent invocation")
        with mock.patch.object(backend.source_intake, "run_intake", return_value=capture) as intake, \
             mock.patch.object(backend, "_verify_memory_intake_original", return_value=("a", Path("a"))), \
             mock.patch.dict("os.environ", kwargs.pop("env", {}), clear=False):
            result = backend._run_memory_intake_capture(Path("x.md"), **kwargs)
        return result, intake

    def test_default_ingest_makes_no_semantic_call(self):
        result, intake = self.outcome()
        self.assertEqual([c.kwargs["mode"] for c in intake.call_args_list], ["capture"])
        self.assertTrue(result["success"])
        self.assertEqual((result["status"], result["semantic"]), ("captured", "not_requested"))
        self.assertEqual(result["token_usage_text"], "Token usage: no agent invocation")

    def test_captured_receipt_reconciles_without_a_card(self):
        import hashlib
        from tools.source_intake import BYTES_BEGIN, BYTES_END  # noqa: F401
        with tempfile.TemporaryDirectory() as tmp:
            root, brain = Path(tmp), Path(tmp) / "brain"
            raw = b"# Source\n"
            digest = hashlib.sha256(raw).hexdigest()
            import base64
            write(brain / f"sources/intake/records/{digest}.md",
                  f"---\ntype: source\nsource_sha256: {digest}\n---\n{BYTES_BEGIN}\n{base64.b64encode(raw).decode()}\n{BYTES_END}\n")
            receipts = root / "queue/receipts/memory_intake"
            receipts.mkdir(parents=True)
            (receipts / "drive.json").write_text(json.dumps({
                "success": True, "status": "captured", "source_sha256": digest,
                "source_record": f"business_brain:sources/intake/records/{digest}.md"}))
            self.assertTrue(drive.reconcile(digest, root=root, brain_root=brain))
            (receipts / "drive.json").write_text(json.dumps({
                "success": False, "status": "needs_attention", "source_sha256": digest,
                "source_record": f"business_brain:sources/intake/records/{digest}.md"}))
            self.assertFalse(drive.reconcile(digest, root=root, brain_root=brain))


class WatcherScriptIsBounded(unittest.TestCase):
    def test_script_has_bounded_persisted_backoff_and_no_retry_on_refusal(self):
        text = (ROOT / "tools/Watch-DriveMemoryIntake.ps1").read_text(encoding="utf-8")
        for needle in ("$MaxAttempts", "$BackoffCapSeconds", "drive_intake_retry_state.json",
                       "NonRetryable", "needs_attention", "PARKED", "Save-RetryState"):
            self.assertIn(needle, text)
        self.assertIn("if ($state.Parked) { return }", text)
        self.assertNotIn("retry pending for", text)


if __name__ == "__main__":
    unittest.main()
