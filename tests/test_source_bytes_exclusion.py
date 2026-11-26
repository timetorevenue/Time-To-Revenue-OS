"""TTROS:SOURCE-BYTES base64 stays preserved as evidence but never enters derived search text.

Temp vault and temp index only; no model, no Luna, no live Brain.
Revisit: when the source-bytes marker or the indexer document builder changes. · Last touched: 2026-11-13.
"""
from __future__ import annotations

import hashlib
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

RAW = ("# Brief\n\nThe Vermilion ledger is reconciled weekly.\n" + "unrelated filler sentence.\n" * 30).encode()


class SourceBytesExclusionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.brain = Path(tmp.name) / "brain"
        (self.brain / source_intake.RECORDS_RELATIVE).mkdir(parents=True)
        self.sha = hashlib.sha256(RAW).hexdigest()
        item = source_intake.SourceItem(PurePosixPath("brief.md"), RAW, self.sha, "text/markdown", "Brief", None)
        self.path = self.brain / source_intake.RECORDS_RELATIVE / f"{self.sha}.md"
        self.path.write_text(source_intake.render_record(item, "2026-11-13T00:00:00Z"), encoding="utf-8")
        self.payload = source_intake.extract_exact_bytes(self.path.read_text(encoding="utf-8"))
        import base64
        self.b64 = base64.b64encode(RAW).decode()
        patch = mock.patch.object(aos_indexer, "BUSINESS_BRAIN_ROOT", self.brain)
        patch.start()
        self.addCleanup(patch.stop)

    def doc(self):
        with mock.patch.object(aos_indexer, "source_for_path", return_value=("business_brain", self.brain)):
            return aos_indexer.document_from_path(self.path)

    def test_the_file_keeps_the_exact_bytes_but_the_search_text_does_not(self):
        self.assertIn(self.b64, self.path.read_text(encoding="utf-8"))
        self.assertEqual(RAW, self.payload)
        doc = self.doc()
        self.assertIsNotNone(doc)
        self.assertNotIn(self.b64[:60], doc["body"])
        self.assertNotIn(self.b64[:60], doc["snippet"])
        self.assertNotIn("TTROS:SOURCE-BYTES", doc["body"])
        self.assertIn(aos_indexer.SOURCE_BYTES_PLACEHOLDER, doc["body"])
        self.assertIn("Vermilion ledger", doc["body"])          # the readable searchable section stays

    def test_search_finds_the_readable_text_and_never_the_base64(self):
        db = self.brain.parent / "index.db"
        conn = aos_indexer.connect(db)
        aos_indexer.upsert_with_passages(conn, self.doc())
        conn.commit()
        conn.close()
        hit = aos_indexer.search("Vermilion ledger", source="business_brain", client_scope="global", db_path=db, hybrid=False)
        self.assertTrue(any(self.sha in row["path"] for g in hit["groups"].values() for row in g))
        miss = aos_indexer.search(self.b64[100:140], source="business_brain", client_scope="global", db_path=db, hybrid=False)
        self.assertEqual(0, miss["count"])

    def test_passages_still_verify_against_the_preserved_bytes(self):
        db = self.brain.parent / "index.db"
        conn = aos_indexer.connect(db)
        self.assertGreater(aos_indexer.upsert_with_passages(conn, self.doc()), 1)
        conn.close()
        self.assertEqual(RAW.decode(), sp.load_text(self.brain, self.sha)[1])

    def test_text_without_a_block_is_untouched_and_a_malformed_block_is_left_alone(self):
        plain = "# Note\n\nNothing preserved here.\n"
        self.assertEqual(plain, aos_indexer.strip_source_bytes(plain))
        broken = "a\n<!-- TTROS:SOURCE-BYTES:BEGIN -->\nAAAA\n"
        self.assertEqual(broken, aos_indexer.strip_source_bytes(broken))


if __name__ == "__main__":
    unittest.main()
