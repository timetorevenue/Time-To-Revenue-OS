"""Hybrid (keyword + local embedding) Business Brain search.

Revisit: when aos_embeddings scope/fusion or the connector/dashboard search contract changes. · Last touched: 2026-11-07.
A deterministic fake embedder stands in for the model, so no test loads bge-small or needs its files.
"""

import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import numpy as np  # noqa: E402

import aos_embeddings  # noqa: E402
import aos_indexer  # noqa: E402
from business_brain_scope import load_registry  # noqa: E402

FOLDER = "sources/historical_calls/2026-11-04-cara-harcourt-casey-first-meeting-491e7000"
THIRD_PARTY = "sources/historical_calls/undated-mira-valdivia-linkedin-content-strategy-2026-7ded0d7c"
RECORD = "sources/intake/records/491e7000399832dc31f7d3e507db9ab751e4155d997f51ca38af8c02ee121d29.md"

# (relative path, title, body, participants)
DOCS = [
    (f"{FOLDER}/roster-pilot-testing.md", "Cara Harcourt / Casey: Roster pilot testing arrangement",
     "Cara offered to trial the nightly roster early with volunteers.", "Casey; Cara Harcourt"),
    (f"{FOLDER}/INDEX.md", "Cara Harcourt / Casey: first meeting (index)",
     "Topics: signage, roster, volunteer groups.", "Casey; Cara Harcourt"),
    (f"{THIRD_PARTY}/three-checklist-handouts.md", "Mira Valdivia: Three checklist handouts",
     "Give away a free resource so readers join the newsletter list.", "none"),
    ("memory/offers.md", "Offers", "Paid assessment indicative $100-200; scoped build entry around $900.", ""),
    (RECORD, "Original: 1st meeting with Cara", "cara roster pilot nightly trial early volunteers " * 30, ""),
    ("people/cara-harcourt.md", "Cara Harcourt", "Person hub for Cara Harcourt.", ""),
    ("sources/historical_calls/cards/cara.card.md", "Card", "roster pilot card", ""),
]

# Words the fake embedder treats as synonyms, so a paraphrase with no shared keyword still matches.
SYNONYMS = {"trial": "pilot", "try": "pilot", "early": "pilot", "tool": "roster", "nightly": "roster",
            "giveaway": "free", "mailing": "newsletter", "charge": "price", "cost": "price", "paid": "price",
            "diagnosis": "price"}


class FakeEmbedder:
    loads = 0

    def __init__(self):
        type(self).loads += 1
        self.load_ms = 0.0
        self.texts_embedded = 0

    def embed(self, texts):
        out = []
        for text in texts:
            vector = np.zeros(64, dtype=np.float32)
            for word in re.findall(r"[a-z]+", text.lower()):
                word = SYNONYMS.get(word, word)
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
            norm = np.linalg.norm(vector) or 1.0
            out.append(vector / norm)
            self.texts_embedded += 1
        return np.vstack(out)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class Unavailable:
    def __init__(self):
        raise aos_embeddings.EmbeddingUnavailable("model file missing: test")


class HybridSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "search" / "os_index.db"
        conn = aos_indexer.connect(self.db)
        for relative, title, body, participants in DOCS:
            aos_indexer.upsert_document(conn, {
                "path": f"business_brain:{relative}", "title": title, "kind": "memory", "source": "business_brain",
                "source_root": "/vault", "client_scope": "global", "mtime": 1.0, "tags": "", "snippet": body[:80],
                "body": body, "indexed_at": "2026-11-07T00:00:00Z", "size_bytes": len(body), "participants": participants,
            })
        conn.commit()
        conn.close()
        self.registry = load_registry(aos_indexer.CLIENT_SCOPE_REGISTRY_PATH)
        FakeEmbedder.loads = 0
        patcher = mock.patch.object(aos_embeddings, "Embedder", FakeEmbedder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self):
        return aos_embeddings.refresh(self.db, create=True)

    def search(self, query, **kwargs):
        kwargs.setdefault("hybrid", True)
        return aos_indexer.search(query, source="business_brain", client_scope="global", db_path=self.db,
                                  registry=self.registry, **kwargs)

    def paths(self, query, **kwargs):
        result = self.search(query, **kwargs)
        return [row["path"].removeprefix("business_brain:") for group in result["groups"].values() for row in group]

    # ---------------------------------------------------------------- store scope and refresh

    def test_only_notes_indexes_and_canonical_files_are_embedded(self):
        result = self.build()
        self.assertEqual(result["status"], "refreshed")
        store = aos_embeddings._connect_store(aos_embeddings.store_path(self.db))
        embedded = {row[0].removeprefix("business_brain:") for row in store.execute("SELECT path FROM docs")}
        store.close()
        self.assertEqual(embedded, {f"{FOLDER}/roster-pilot-testing.md", f"{FOLDER}/INDEX.md",
                                    f"{THIRD_PARTY}/three-checklist-handouts.md", "memory/offers.md"})
        # Detector can say NO: the original, the card and the hub are present in the keyword index.
        self.assertIn(RECORD, self.paths("cara roster", hybrid=False))

    def test_refresh_is_incremental_and_never_loads_the_model_when_current(self):
        self.build()
        FakeEmbedder.loads = 0
        again = aos_embeddings.refresh(self.db)
        self.assertEqual((again["status"], again["model_loaded"], FakeEmbedder.loads), ("current", False, 0))
        conn = aos_indexer.connect(self.db)
        conn.execute("UPDATE documents SET body = body || ' changed' WHERE path = 'business_brain:memory/offers.md'")
        conn.commit()
        conn.close()
        changed = aos_embeddings.refresh(self.db)
        self.assertEqual((changed["status"], changed["embedded"], FakeEmbedder.loads), ("refreshed", 1, 1))

    def test_refresh_is_a_noop_without_a_store(self):
        self.assertEqual(aos_embeddings.refresh(self.db)["status"], "disabled")
        self.assertEqual(FakeEmbedder.loads, 0)
        self.assertFalse(aos_embeddings.store_path(self.db).exists())

    def test_removed_rows_leave_the_store(self):
        self.build()
        # aos_indexer.remove_one deletes the documents row; emulate that directly.
        conn = aos_indexer.connect(self.db)
        conn.execute("DELETE FROM documents WHERE path = 'business_brain:memory/offers.md'")
        conn.commit()
        conn.close()
        self.assertEqual(aos_embeddings.refresh(self.db)["removed"], 1)

    # ---------------------------------------------------------------- fusion

    def test_paraphrase_without_shared_keywords_is_found_only_with_hybrid(self):
        self.build()
        query = "who would try my tool"
        self.assertNotIn(f"{FOLDER}/roster-pilot-testing.md", self.paths(query, hybrid=False)[:3])
        # Canonical-first (2026-11-07): in this 7-document fixture every document is inside the
        # canonical window, so memory/offers.md leads; the note is the first non-canonical result.
        paths = [p for p in self.paths(query) if not p.startswith("memory/")]
        self.assertEqual(paths[0], f"{FOLDER}/roster-pilot-testing.md")

    def test_semantic_only_match_still_respects_filters(self):
        self.build()
        # source filter: hybrid never returns a row the keyword WHERE would refuse.
        result = aos_indexer.search("mailing giveaway", source="agentic_os_live", client_scope="global",
                                    db_path=self.db, registry=self.registry, hybrid=True)
        self.assertEqual(result["count"], 0)

    def test_hub_first_and_originals_below_notes_survive_fusion(self):
        self.build()
        paths = self.paths("Cara Harcourt roster pilot")
        self.assertEqual(paths[0], "people/cara-harcourt.md")
        self.assertIn(RECORD, paths)
        notes = [i for i, p in enumerate(paths) if p.startswith(FOLDER)]
        self.assertTrue(notes and max(notes) < paths.index(RECORD))

    def test_falls_back_to_keyword_when_the_model_is_unavailable(self):
        self.build()
        with mock.patch.object(aos_embeddings, "Embedder", Unavailable):
            result = self.search("Cara Harcourt roster pilot")
        self.assertEqual(result["hybrid"]["used"], False)
        self.assertIn("model file missing", result["hybrid"]["reason"])
        keyword = aos_indexer.search("Cara Harcourt roster pilot", source="business_brain", client_scope="global",
                                     db_path=self.db, registry=self.registry)
        flat = lambda r: [row["path"] for g in r["groups"].values() for row in g]  # noqa: E731
        self.assertEqual(flat(result), flat(keyword))

    def test_search_catches_up_a_changed_note(self):
        self.build()
        conn = aos_indexer.connect(self.db)
        conn.execute("UPDATE documents SET body = 'price list for the diagnosis' WHERE path = 'business_brain:memory/offers.md'")
        conn.commit()
        conn.close()
        result = self.search("what do we charge")
        self.assertEqual(result["hybrid"]["catch_up"], {"stale": 1, "embedded": 1})
        self.assertEqual(aos_embeddings.status(self.db)["stale"], 0)

    def test_exact_search_is_hybrid_with_exact_hits_first(self):
        # 2026-11-07: exact=True no longer disables hybrid; exact phrase hits lead
        # (tests/test_david_hybrid_search.py holds the prefix check).
        self.build()
        result = self.search("roster pilot", exact=True)
        self.assertTrue(result["hybrid"]["used"])
        self.assertEqual(result["search_mode"], "hybrid")

    def test_exact_title_and_identifier_lookup_survives_semantic_fusion(self):
        # 2026-11-11: a captured Drive doc's exact title / Drive ID ranked ~70th in hybrid because the
        # semantic half (weight 3) buried the one keyword hit, and intake records sort last.
        self.build()
        title = "Original: 1st meeting with Cara"
        self.assertEqual(self.paths(title, limit=10)[0], RECORD)
        with mock.patch.object(aos_indexer, "EXACT_PROMOTE", 0):  # detector: without promotion it is demoted
            self.assertNotEqual(self.paths(title, limit=10)[0], RECORD)
        # A phrase shared by many documents is ordinary ranking, not a lookup.
        with mock.patch.object(aos_indexer, "EXACT_PROMOTE", 1):
            self.assertNotEqual(self.paths("roster", limit=10)[0], RECORD)

    # ---------------------------------------------------------------- filter and paging

    def test_casey_only_keeps_his_conversations(self):
        self.build()
        paths = self.paths("free newsletter roster offers price cara", casey_only=True)
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith(FOLDER) for p in paths), paths)
        # Detector can say NO: without the filter the third-party note and canonical file appear.
        unfiltered = self.paths("free newsletter roster offers price cara")
        self.assertIn(f"{THIRD_PARTY}/three-checklist-handouts.md", unfiltered)

    def test_paging_returns_disjoint_pages(self):
        self.build()
        first = self.search("cara roster beta", limit=2)
        second = self.search("cara roster beta", limit=2, offset=2)
        a = [row["path"] for g in first["groups"].values() for row in g]
        b = [row["path"] for g in second["groups"].values() for row in g]
        self.assertEqual(len(a), 2)
        self.assertFalse(set(a) & set(b))
        self.assertEqual(first["next_offset"], 2)
        keyword_first = self.search("cara roster beta", limit=2, hybrid=False)
        keyword_second = self.search("cara roster beta", limit=2, offset=2, hybrid=False)
        ka = [row["path"] for g in keyword_first["groups"].values() for row in g]
        kb = [row["path"] for g in keyword_second["groups"].values() for row in g]
        self.assertFalse(set(ka) & set(kb))
        self.assertEqual(len(ka), 2)


class ChunkingTest(unittest.TestCase):
    def test_long_body_is_chunked_with_title_prefix(self):
        chunks = aos_embeddings.chunks_for("T", "word " * 1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.startswith("T\n") for chunk in chunks))
        self.assertEqual(aos_embeddings.chunks_for("T", ""), ["T"])

    def test_embeddable_scope(self):
        yes = ["business_brain:sources/historical_calls/f/note.md", "business_brain:sources/historical_calls/f/INDEX.md",
               "business_brain:memory/offers.md", "business_brain:decisions/DECISIONS.md",
               "business_brain:prospects/2026-07-flox-ron-halvorsen.md", "business_brain:operating_context/open_loops.md"]
        no = ["business_brain:sources/intake/records/abc.md", "business_brain:sources/historical_calls/cards/x.card.md",
              "business_brain:sources/historical_calls/f/nested/x.md", "business_brain:people/cara-harcourt.md",
              "business_brain:sources/historical_calls/INDEX.md", "agentic_os_live:memory/x.md"]
        self.assertTrue(all(aos_embeddings.is_embeddable(p) for p in yes))
        self.assertFalse(any(aos_embeddings.is_embeddable(p) for p in no))


if __name__ == "__main__":
    unittest.main()
