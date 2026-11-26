"""Tier 0 passages as ordinary hybrid-search rows, and David's compact evidence-backed search.

Temp vault and temp index only; no model, no Luna, no live Brain. Embeddings are absent here, so search runs
keyword-only, which is the documented fallback the same code paths take.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import aos_indexer  # noqa: E402
from tools import source_intake  # noqa: E402
from tools import source_passages as sp  # noqa: E402

RECORDS = source_intake.RECORDS_RELATIVE
SOURCE = "\n".join(
    ["# Assistant Brief", "", "Intro line about the project."] + [f"filler line {i} about nothing" for i in range(40)]
    + ["", "# Tracker", "", "The Google Sheet is the authoritative transaction tracker for version one.",
       "Vermilion ledger numbers are reconciled weekly.", "",
       "# Hosting", "", "Fallback hosting is a Toronto data centre."]
    + [f"more filler {i} unrelated text" for i in range(40)]
) + "\n"


class Vault:
    def __init__(self, test: unittest.TestCase, source: str = SOURCE):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.brain = self.root / "brain"
        (self.brain / RECORDS).mkdir(parents=True)
        raw = source.encode()
        self.sha = hashlib.sha256(raw).hexdigest()
        item = source_intake.SourceItem(PurePosixPath("brief.md"), raw, self.sha, "text/markdown", "Assistant Brief", None)
        self.record_path = self.brain / RECORDS / f"{self.sha}.md"
        self.record_path.write_text(source_intake.render_record(item, "2026-11-12T00:00:00Z"), encoding="utf-8")
        self.pointer = f"business_brain:{RECORDS}/{self.sha}.md"
        self.db = self.root / "index.db"
        self.lines = source.split("\n")[:-1]

    def tamper(self) -> None:
        """Change one character of the base64 exact-byte payload to another valid base64 character."""
        text = self.record_path.read_text(encoding="utf-8")
        start = text.index(source_intake.BYTES_BEGIN) + len(source_intake.BYTES_BEGIN) + 1 + 40
        swap = "B" if text[start] != "B" else "C"
        self.record_path.write_text(text[:start] + swap + text[start + 1:], encoding="utf-8")

    def record_doc(self) -> dict:
        return {"path": self.pointer, "title": "Assistant Brief", "kind": "memory", "source": "business_brain",
                "source_root": str(self.brain), "client_scope": "global", "mtime": 1.0, "tags": "", "snippet": "x",
                "body": self.record_path.read_text(encoding="utf-8"), "indexed_at": "t", "size_bytes": 1, "participants": ""}

    def index(self) -> int:
        with mock.patch.object(aos_indexer, "BUSINESS_BRAIN_ROOT", self.brain):
            conn = aos_indexer.connect(self.db)
            written = aos_indexer.upsert_with_passages(conn, self.record_doc())
            conn.commit()
            conn.close()
        return written

    def search(self, query: str, **kwargs) -> list[dict]:
        result = aos_indexer.search(query, source="business_brain", client_scope="global", db_path=self.db, hybrid=False,
                                    **kwargs)
        return [row for group in result["groups"].values() for row in group]


class WindowTests(unittest.TestCase):
    def test_windows_cover_every_non_blank_line_within_bounds_and_open_sections_at_headings(self):
        lines = SOURCE.split("\n")[:-1]
        windows = sp.index_windows(lines)
        covered = {n for a, b, _t in windows for n in range(a, b + 1)}
        self.assertTrue(all(n in covered for n, line in enumerate(lines, 1) if line.strip()))
        for a, b, text in windows:
            self.assertLessEqual(b - a + 1, sp.INDEX_MAX_LINES)
            self.assertEqual("\n".join(lines[a - 1:b]), text)
        tracker = next(n for n, line in enumerate(lines, 1) if line == "# Tracker")
        self.assertIn(tracker, [a for a, _b, _t in windows])

    def test_one_overlong_line_is_its_own_window(self):
        windows = sp.index_windows(["a" * 5000, "b"])
        self.assertEqual((1, 1), windows[0][:2])


class IndexedPassageTests(unittest.TestCase):
    def test_passages_are_hash_verified_rows_with_stable_refs_visible_only_when_requested(self):
        v = Vault(self)
        self.assertGreater(v.index(), 2)
        self.assertEqual([], [r for r in v.search("Vermilion") if aos_indexer.is_passage_pointer(r["path"])])
        rows = v.search("Vermilion ledger", passages=True)
        passage = next(r for r in rows if aos_indexer.is_passage_pointer(r["path"]))
        self.assertTrue(passage["source_ref"].startswith(f"source:{v.sha}#L"))
        verified = sp.read_passage(v.brain, passage["source_ref"])
        self.assertIn("Vermilion", verified["text"])
        self.assertEqual("source_passage", passage["tags"][0])

    def test_passage_outranks_its_whole_original_and_the_original_stays_last_tier(self):
        v = Vault(self)
        v.index()
        paths = [r["path"] for r in v.search("Vermilion ledger", passages=True)]
        self.assertTrue(aos_indexer.is_passage_pointer(paths[0]))
        self.assertEqual(v.pointer, paths[-1])

    def test_reindexing_replaces_passages_and_removal_drops_them(self):
        v = Vault(self)
        v.index()
        first = {r["path"] for r in v.search("Toronto", passages=True)}
        v.index()
        self.assertEqual(first, {r["path"] for r in v.search("Toronto", passages=True)})
        conn = aos_indexer.connect(v.db)
        aos_indexer.delete_passages(conn, v.pointer)
        conn.commit()
        conn.close()
        self.assertEqual([], [r for r in v.search("Toronto", passages=True) if aos_indexer.is_passage_pointer(r["path"])])

    def test_record_whose_bytes_do_not_verify_gets_no_passages(self):
        v = Vault(self)
        v.tamper()
        with mock.patch.object(aos_indexer, "BUSINESS_BRAIN_ROOT", v.brain):
            self.assertEqual([], aos_indexer.passage_documents(v.record_doc()))

    def test_query_excerpt_is_bounded_and_centred_on_the_rare_terms(self):
        text = "\n".join(["filler sentence about weather."] * 80 + ["The Vermilion ledger is reconciled weekly."]
                         + ["more filler about nothing."] * 80)
        excerpt = aos_indexer.query_excerpt(text, "how often is the vermilion ledger reconciled", 200)
        self.assertLessEqual(len(excerpt), 200)
        self.assertIn("Vermilion", excerpt)
        self.assertEqual("short", aos_indexer.query_excerpt("short", "anything", 200))

    def test_rerank_puts_the_candidate_holding_the_rare_term_ahead_of_a_semantic_favourite(self):
        db = Path(tempfile.mkdtemp()) / "r.db"
        self.addCleanup(lambda: db.parent.exists() and __import__("shutil").rmtree(db.parent, ignore_errors=True))
        conn = aos_indexer.connect(db)
        base = {"kind": "memory", "source": "business_brain", "source_root": "r", "client_scope": "global", "mtime": 1.0,
                "tags": "", "snippet": "", "indexed_at": "t", "size_bytes": 1, "participants": ""}
        for name, body in (("a", "general planning notes about quarterly budgets and staffing"),
                           ("b", "the zorblax calibration procedure is run every Monday")):
            aos_indexer.upsert_document(conn, {**base, "path": f"business_brain:memory/{name}.md", "title": name, "body": body})
        conn.commit()
        rows = [conn.execute("SELECT * FROM documents WHERE path = ?", (f"business_brain:memory/{n}.md",)).fetchone() for n in "ab"]
        fused = [(rows[0], "semantic"), (rows[1], "keyword")]
        out = aos_indexer.rerank_fused(conn, "how is zorblax calibration done", fused, [])
        self.assertEqual("business_brain:memory/b.md", out[0][0]["path"])
        conn.close()


class DavidEvidenceTests(unittest.TestCase):
    def setUp(self):
        try:
            import brain_memory_mcp as bmm
        except ModuleNotFoundError as exc:  # mcp is installed only in the backend venv
            self.skipTest(f"brain_memory_mcp unavailable: {exc}")
        self.bmm = bmm
        self.v = Vault(self)
        self.v.index()
        real = aos_indexer.search

        def search(*args, **kwargs):
            return real(*args, db_path=self.v.db, **kwargs)

        for patcher in (mock.patch.object(aos_indexer, "search", side_effect=search),
                        mock.patch.object(bmm, "VAULT_ROOT", self.v.brain)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_result_is_small_grounded_and_carries_verified_refs(self):
        result = self.bmm.search_history("Vermilion ledger reconciled")
        self.assertTrue(result["success"])
        self.assertLessEqual(result["approx_tokens"], self.bmm.RESULT_TOKENS_DEFAULT)
        self.assertLessEqual(len(result["matches"]), self.bmm.DEFAULT_RESULTS)
        top = result["matches"][0]
        self.assertEqual("source_passage", top["kind"])
        self.assertTrue(top["verified"])
        self.assertIn("Vermilion", top["excerpt"])
        self.assertEqual(sp.read_passage(self.v.brain, top["ref"])["ref"], top["ref"])

    def test_unverifiable_passage_is_never_returned(self):
        self.v.tamper()  # the index still holds the old passage rows; the preserved bytes no longer verify
        result = self.bmm.search_history("Vermilion ledger reconciled")
        self.assertTrue(all(m.get("kind") != "source_passage" for m in result["matches"]))

    def test_expand_raises_the_budget_and_sets_a_pointer_to_open_a_passage(self):
        small = self.bmm.search_history("filler")
        large = self.bmm.search_history("filler", expand=True)
        self.assertLessEqual(small["approx_tokens"], self.bmm.RESULT_TOKENS_DEFAULT)
        self.assertLessEqual(large["approx_tokens"], self.bmm.RESULT_TOKENS_EXPANDED)
        self.assertGreaterEqual(len(large["matches"]), len(small["matches"]))

    def test_open_note_reads_a_bounded_verified_passage_by_ref_or_search_pointer(self):
        ref = f"source:{self.v.sha}#L45-L47"
        with mock.patch.object(self.bmm, "read_vault_note", return_value=("x", "y")):
            by_ref = self.bmm.open_note(ref)
            by_pointer = self.bmm.open_note(f"{self.v.pointer}#L45-L47")
        self.assertTrue(by_ref["success"] and by_ref["verified"])
        self.assertEqual(by_ref["content"], by_pointer["content"])
        self.assertEqual("\n".join(self.v.lines[44:47]), by_ref["content"])

    def test_search_goes_through_the_local_indexer_only(self):
        with mock.patch.object(aos_indexer, "search", wraps=aos_indexer.search) as spy:
            self.bmm.search_history("Toronto")
        self.assertTrue(spy.called)


if __name__ == "__main__":
    unittest.main()
