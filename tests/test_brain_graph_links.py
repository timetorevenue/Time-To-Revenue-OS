"""Corroborated suggested links: similarity proposes, Entity Index / ICM / source metadata decide.

Fixture databases and hand-made unit vectors (no model load, no live data). The retrieval cases use
the real Graphify fixture graph from test_graph_incremental's registry shape.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from dashboard.backend.business_brain_graph import BusinessBrainGraphService
from tests.test_graph_incremental import note, prefix_registry, write
from tools import aos_indexer, brain_graph_links as links

A, B, C, D, E = (f"business_brain:memory/{name}.md" for name in ("a", "b", "c", "d", "e"))


def unit(*values):
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class LinkFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.db = self.root / "index.db"
        self.embed = self.root / "embed.db"
        self.graphify = self.root / "graphify"
        for name in ("a", "b", "c", "d", "e"):
            write(self.vault / f"memory/{name}.md", note(f"note-{name}", f"Note {name.upper()} alpha", f"Body of {name}."))
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE documents (path TEXT, client_scope TEXT, source TEXT);
            CREATE TABLE entities (entity_id TEXT, display_name TEXT, entity_type TEXT);
            CREATE TABLE entity_links (entity_id TEXT, path TEXT, basis TEXT);
            CREATE TABLE brain_documents (path TEXT, role TEXT, knowledge_type TEXT, canonical INTEGER, package_id TEXT);
        """)
        scopes = {A: "global", B: "global", C: "global", D: "client-a", E: "global"}
        conn.executemany("INSERT INTO documents VALUES (?, ?, 'business_brain')", scopes.items())
        conn.executemany("INSERT INTO entities VALUES (?, ?, ?)", [
            ("person:pat-example", "Pat Example", "person"), ("topic:ai", "AI agents", "topic")])
        conn.executemany("INSERT INTO brain_documents VALUES (?, 'knowledge', ?, ?, '')", [
            (A, "canonical", 1), (B, "historical_evidence", 0), (C, "canonical", 1), (D, "canonical", 1), (E, "canonical", 1)])
        conn.commit()
        conn.close()
        store = sqlite3.connect(self.embed)
        store.executescript("CREATE TABLE vectors (path TEXT, idx INTEGER, vector BLOB);")
        # A~B 0.95, A~C 0.95, A~D 0.95 (other client), A~E 0.5 (below threshold)
        vectors = {A: unit(1, 0.0, 0, 0), B: unit(1, 0.33, 0, 0), C: unit(1, 0, 0.33, 0), D: unit(1, 0, 0, 0.33), E: unit(1, 1.7, 0, 0)}
        store.executemany("INSERT INTO vectors VALUES (?, 0, ?)", [(p, v.tobytes()) for p, v in vectors.items()])
        store.commit()
        store.close()

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, statement, *params):
        conn = sqlite3.connect(self.db)
        conn.execute(statement, params)
        conn.commit()
        conn.close()

    def entity(self, path, entity_id="person:pat-example", basis="participants metadata"):
        self.sql("INSERT INTO entity_links VALUES (?, ?, ?)", entity_id, path, basis)

    def update(self, *pointers):
        return links.update_links(pointers, db_path=self.db, embed_db=self.embed, vault=self.vault, graphify_root=self.graphify)

    def stored(self):
        conn = links.connect(self.db)
        try:
            return links.rows(conn)
        finally:
            conn.close()


class SuggestedLinkTest(LinkFixture):
    def test_similarity_alone_never_links_and_shared_entity_makes_a_typed_suggestion(self):
        result = self.update(A)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["uncorroborated_skipped"], 2)  # B and C: similar, nothing corroborates

        for path in (A, B):
            self.entity(path)
        result = self.update(A)
        self.assertEqual(result["created"], 1)
        (link,) = self.stored()
        self.assertEqual((link["source_path"], link["target_path"], link["relation"], link["status"]), (A, B, "related_to", "suggested"))
        self.assertEqual(link["confidence"], "medium")
        self.assertGreaterEqual(link["similarity"], links.LINK_MIN_SIMILARITY)
        self.assertIn("shared person entity: Pat Example (Entity Index)", link["reasons"])
        self.assertEqual(link["evidence"]["entity_links"][0]["source_basis"], "participants metadata")
        self.assertEqual(link["evidence"]["authority"], {"source": "canonical", "target": "historical_evidence"})
        self.assertEqual(len(link["source_sha256"]), 64)
        exported = json.loads((self.graphify / "document_graphs/ttros-business-brain/suggested/suggested_links.json").read_text())
        self.assertEqual([row["target_path"] for row in exported["links"]], [B])
        self.assertFalse(exported["authoritative"])

    def test_never_crosses_a_client_scope_or_the_threshold(self):
        for path in (A, D, E):
            self.entity(path)
        self.assertEqual(self.update(A)["created"], 0)  # D is client-a; E is below LINK_MIN_SIMILARITY
        self.assertEqual(self.stored(), [])

    def test_specific_types_need_their_own_evidence(self):
        self.sql("UPDATE brain_documents SET package_id = 'MI-call-1' WHERE path IN (?, ?)", A, C)
        self.entity(A, "topic:ai", "card entities metadata")
        self.entity(B, "topic:ai", "card entities metadata")
        self.update(A)
        relations = {(row["target_path"], row["relation"], row["confidence"]) for row in self.stored()}
        self.assertEqual(relations, {(B, "related_to", "low"), (C, "same_source", "medium")})
        # A decision note sharing a person in metadata -> decision_about, decision as source.
        self.sql("UPDATE brain_documents SET knowledge_type = 'explicit_decision' WHERE path = ?", B)
        self.entity(A)
        self.entity(B)
        self.update(A)
        decision = [row for row in self.stored() if row["relation"] == "decision_about"]
        self.assertEqual([(row["source_path"], row["target_path"]) for row in decision], [(B, A)])
        # ...but a person found only by a text mention is not evidence of what the decision is about.
        self.sql("UPDATE entity_links SET basis = 'named in text' WHERE path = ? AND entity_id = 'person:pat-example'", B)
        self.update(A)
        self.assertEqual([row["relation"] for row in self.stored() if B in (row["source_path"], row["target_path"])], ["related_to"])

    def test_withdrawn_corroboration_and_deletion_remove_the_link(self):
        for path in (A, B):
            self.entity(path)
        self.update(A)
        self.assertEqual(len(self.stored()), 1)
        self.sql("DELETE FROM entity_links WHERE path = ?", B)
        self.update(B)
        self.assertEqual(self.stored(), [])
        self.entity(B)
        self.update(A)
        self.sql("DELETE FROM documents WHERE path = ?", B)
        result = self.update(B)
        self.assertEqual((result["removed"], self.stored()), (1, []))

    def test_vocabulary_is_the_agreed_one(self):
        self.assertEqual(set(links.RELATION_VOCABULARY), {
            "about_person", "about_company", "about_project", "decision_about", "advice_on", "evidence_for", "same_source", "related_to"})
        self.assertTrue(set(links.PRODUCED_RELATIONS) <= set(links.RELATION_VOCABULARY))


class SuggestedLinkRetrievalTest(LinkFixture):
    def setUp(self):
        super().setUp()
        self.registry = prefix_registry()
        self.service = BusinessBrainGraphService(graphify_root=self.graphify, vault_root=self.vault, registry=self.registry)
        self.service.build()
        write(self.vault / "memory/a.md", note("note-a", "Alpha plan", "Alpha."))
        write(self.vault / "memory/b.md", note("note-b", "Bravo record", "Bravo."))
        self.service.build()
        for path in (A, B):
            self.entity(path)
        self.update(A)

    def query(self, scope="global"):
        with mock.patch.dict(os.environ, {"TTROS_GRAPH_LEXICAL_SCOPE": "1"}):
            return self.service.query_targets("alpha plan", client_scope=scope, registry=self.registry)

    def test_graph_query_follows_a_suggested_link_and_labels_it(self):
        targets = {row["path"]: row for row in self.query()["targets"]}
        self.assertIn(B, targets)
        self.assertTrue(targets[B]["suggested_only"])
        self.assertTrue(targets[B]["relationship_reasons"][0].startswith("suggested related_to (status=suggested"))
        self.assertIn("[derived, not canonical]", targets[B]["relationship_reasons"][0])
        self.assertLess(targets[B]["score"], targets[A]["score"])

    def test_stale_endpoint_or_other_scope_never_uses_the_link(self):
        write(self.vault / "memory/b.md", note("note-b", "Bravo record", "Edited after the link was made."))
        self.service.apply_changes(["memory/b.md"])  # no state db -> disabled; rebuild instead
        self.service.build()
        self.assertNotIn(B, [row["path"] for row in self.query()["targets"]])
        self.assertEqual(self.service._suggested_links("client-a"), [])

    def test_tampered_target_is_dropped_at_return(self):
        write(self.vault / "memory/b.md", note("note-b", "Bravo record", "Tampered, graph not rebuilt."))
        result = self.query()
        # Without incremental state the full status scan sees the edit and returns nothing at all;
        # with it, the per-note hash check drops B (test_graph_incremental covers that case).
        self.assertEqual((result["graph_state"], result["targets"]), ("stale", []))

    def test_search_rows_carry_links_only_within_their_scope(self):
        conn = links.connect(self.db)
        try:
            rows = [{"path": A, "client_scope": "global"}, {"path": B, "client_scope": "client-a"}]
            aos_indexer.attach_suggested_links(conn, rows)
        finally:
            conn.close()
        self.assertEqual(rows[0]["suggested_links"][0]["path"], B)
        self.assertEqual(rows[0]["suggested_links"][0]["status"], "suggested")
        self.assertFalse(rows[0]["suggested_links"][0]["authoritative"])
        self.assertNotIn("suggested_links", rows[1])


if __name__ == "__main__":
    unittest.main()
