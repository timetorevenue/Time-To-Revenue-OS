"""Derived Memory entity index: deterministic, metadata-only, no silent merges."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import aos_entity_index as E
import aos_indexer


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


DOCS = {
    "sources/historical_calls/theodore-june-30.md": '---\nid: hs-1\ntype: historical_source\nsource_document_kind: call_transcript\nparticipants_text: "Casey Linden; Theodore (surname absent from metadata)"\nsource_date_text: "Jun 30"\nimported_at: "2026-08-17"\n---\n# Historical source — Call with Theodore\n',
    "sources/historical_calls/cards/theodore-june-30.card.md": '---\nid: card-1\ntype: source_card\nsource_path: "sources/historical_calls/theodore-june-30.md"\nentities: "Harbor opportunity, budget constraints"\n---\n# June 30 call summary\n',
    "sources/historical_calls/pryce.md": '---\nid: hs-2\ntype: historical_source\nparticipants_text: "Casey Linden; Dr Theodore Pryce"\nimported_at: "2026-08-17"\n---\n# Historical source — Call with Dr Theodore Pryce\n',
    "sources/historical_calls/dunmore.md": '---\nid: hs-3\ntype: historical_source\nparticipants_text: "Casey Linden; Theo Dunmore"\nsource_date_text: "Jul 7"\nimported_at: "2026-08-17"\n---\n# Historical source — Quinn founder\n',
    "sources/historical_calls/theodore-brackenridge-targeting-2026-09-25.md": "# Theodore / Brackenridge Targeting — Historical Evidence (2026-09-25)\n\n**Temporal posture:** historical\n",
    "sources/historical_calls/harbor-co-opportunity-2026-09-25.md": "# Harbor Co / Example Opportunity — Historical Evidence (2026-09-25)\n\n**Temporal posture:** historical\nKenneth said...\n",
    "memory/clients.md": "---\nid: clients\ntype: knowledge\n---\n# Clients\n\n- Contacts: Theodore Pryce (referral).\n",
    "sources/historical_calls/INDEX.md": "---\nid: idx\ntype: index\n---\n# Index\nKenneth Pryce, Theo Dunmore, Theodore\n",
}


class MemoryEntityIndexTest(unittest.TestCase):
    def build(self, temp: Path) -> Path:
        vault, db = temp / "vault", temp / "search" / "os_index.db"
        conn = aos_indexer.connect(db)
        for relative, text in DOCS.items():
            write(vault / relative, text)
            aos_indexer.upsert_document(conn, {
                "path": f"business_brain:{relative}", "title": relative, "kind": "memory", "source": "business_brain",
                "source_root": str(vault), "client_scope": "global", "mtime": 1.0, "tags": "", "snippet": "",
                "body": text, "indexed_at": "2026-10-02T00:00:00Z", "size_bytes": len(text),
            })
        conn.commit()
        conn.close()
        receipts = temp / "receipts.jsonl"
        receipts.write_text(json.dumps({"outcome": "imported", "package_id": "MI-test", "outputs": [
            {"pointer": "business_brain:sources/historical_calls/theodore-brackenridge-targeting-2026-09-25.md"},
            {"pointer": "business_brain:sources/historical_calls/harbor-co-opportunity-2026-09-25.md"},
        ]}) + "\n", encoding="utf-8")
        self.patch = patch.object(E, "MEMORY_EXCHANGE_RECEIPTS", receipts)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        result = E.rebuild(db, vault=vault)
        self.assertEqual(result["token_usage_text"], "Token usage: no agent invocation")
        return db

    def test_theodore_variants_never_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            found = E.search_entities("Theodore", db_path=db)
            self.assertEqual(found["resolution"], "ambiguous")
            names = [row["display_name"] for row in found["entities"]]
            self.assertEqual(names[:2], ["Theodore", "Theodore Pryce"])
            self.assertNotIn("Theo Dunmore", names)
            self.assertEqual(E.search_entities("Theo Dunmore", db_path=db)["resolution"], "single")
            self.assertEqual(len(E.search_entities("Theo", db_path=db)["entities"]), 3)

    def test_entity_view_links_only_declared_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            view = E.entity_view("person:theodore", db_path=db)
            basis = {item["path"].split("/")[-1]: item["basis"] for item in view["knowledge"] + view["sources"]}
            self.assertEqual(basis["theodore-june-30.md"], "participants metadata")
            self.assertEqual(basis["theodore-june-30.card.md"], "card of linked source")
            self.assertEqual(basis["theodore-brackenridge-targeting-2026-09-25.md"], "title mention")
            self.assertEqual(basis["harbor-co-opportunity-2026-09-25.md"], "same Memory Ingest package")
            self.assertNotIn("pryce.md", basis)
            self.assertNotIn("INDEX.md", basis)
            self.assertEqual([item["path"].split("/")[-1] for item in view["sources"]], ["theodore-june-30.md"])
            self.assertEqual(view["entity"]["latest_date"], "2026-09-25")
            self.assertEqual([row["display_name"] for row in view["similar_not_merged"]], ["Theodore Pryce"])
            self.assertNotIn("Theo Dunmore", [row["display_name"] for row in view["similar_not_merged"] + view["related"]])
            pryce = E.entity_view("person:theodore-pryce", db_path=db)
            # import-only dates stay out of the chronology and never count as activity
            self.assertEqual(pryce["entity"]["latest_date"], "")
            self.assertEqual([item["path"].split("/")[-1] for item in pryce["import_dated"]], ["pryce.md"])
            self.assertTrue(all(item["date_basis"] != "imported_at" for item in pryce["timeline"]))
            self.assertIn("2026-09-25", [item["date"] for item in view["timeline"]])
            self.assertEqual({item["path"].split("/")[-1] for item in pryce["knowledge"] + pryce["sources"]}, {"pryce.md", "clients.md"})

    def test_declared_entity_key_resolves_surnameless_participant_only(self):
        declared = "sources/historical_calls/theodore-june-30.md"
        DOCS_DECLARED = dict(DOCS)
        DOCS_DECLARED[declared] = DOCS[declared].replace("imported_at:", "entity_key: person:theodore-pryce\nimported_at:")
        with patch.dict(DOCS, DOCS_DECLARED), tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            pryce = E.entity_view("person:theodore-pryce", db_path=db)
            basis = {item["path"].split("/")[-1]: item["basis"] for item in pryce["knowledge"] + pryce["sources"]}
            self.assertEqual(basis["theodore-june-30.md"], "entity_key metadata")
            self.assertEqual(basis["theodore-june-30.card.md"], "card of linked source")
            # the only record that created bare "Theodore" now declares its identity, so no bare entity is invented
            self.assertIsNone(E.entity_view("person:theodore", db_path=db))
            self.assertNotIn("theodore-brackenridge-targeting-2026-09-25.md", basis)  # undeclared title mention is not silently merged
            self.assertEqual(E.search_entities("Theo Dunmore", db_path=db)["entities"][0]["entity_id"], "person:theo-dunmore")
            dunmore = E.entity_view("person:theo-dunmore", db_path=db)
            self.assertEqual([item["path"].split("/")[-1] for item in dunmore["sources"]], ["dunmore.md"])

    def test_classification_and_dates_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            meta = E.document_meta([
                "business_brain:sources/historical_calls/theodore-june-30.md",
                "business_brain:sources/historical_calls/theodore-brackenridge-targeting-2026-09-25.md",
                "business_brain:memory/clients.md",
            ], db_path=db)
            source = meta["business_brain:sources/historical_calls/theodore-june-30.md"]
            self.assertEqual((source["role"], source["type_label"], source["date"]), ("source", "Call transcript", "2026-06-30"))
            ingest = meta["business_brain:sources/historical_calls/theodore-brackenridge-targeting-2026-09-25.md"]
            self.assertEqual((ingest["role"], ingest["knowledge_type"], ingest["canonical"], ingest["package_id"]), ("knowledge", "historical_evidence", False, "MI-test"))
            self.assertTrue(meta["business_brain:memory/clients.md"]["canonical"])

    def test_sources_are_historical_evidence_and_never_canonical(self):
        """Fix 7: an undeclared ingest note under sources/ is historical evidence, not canonical knowledge."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        def classify(relative, fields, body):
            return E.classify(relative, fields, body, vault_root=Path(directory.name))
        knowledge = {"id": "n", "type": "knowledge"}
        self.assertEqual(("knowledge", "historical_evidence", "Historical evidence", False),
                         classify("sources/historical_calls/cara-beta-testing-7c476039148b.md", knowledge, "# Cara\n"))
        # A declared label is kept, but sources/ evidence never becomes canonical.
        self.assertEqual(("knowledge", "verified_fact", "Verified / current knowledge", False),
                         classify("sources/historical_calls/x.md", {**knowledge, "knowledge_type": "verified_fact"}, ""))
        self.assertFalse(classify("sources/historical_calls/x.md", knowledge, "**Temporal posture:** current\n")[3])
        # Labels outside sources/ are unchanged.
        self.assertEqual(("knowledge", "canonical", "Canonical knowledge", True), classify("memory/company.md", knowledge, ""))
        self.assertEqual(("knowledge", "client_project_context", "Project / client context", True),
                         classify("memory/clients.md", knowledge, ""))
        self.assertEqual("explicit_decision", classify("decisions/DECISIONS.md", knowledge, "")[1])
        self.assertEqual("inbox", classify("inbox/distilled_packets/s.md", knowledge, "")[1])
        self.assertEqual(("knowledge", "verified_fact", "Verified / current knowledge", True),
                         classify("memory/company.md", {**knowledge, "knowledge_type": "verified_fact"}, ""))

    def test_person_hubs_are_navigation_not_records(self):
        """v4: a people/ hub (generated link list) never becomes one more record of its person."""
        hub = {"id": "person-theodore-pryce", "type": "person", "entity_key": "person:theodore-pryce"}
        self.assertEqual(("index", "index", "Person hub · navigation", False), E.classify("people/theodore-pryce.md", hub, "# Theodore Pryce\n"))
        self.assertEqual("knowledge", E.classify("memory/theodore.md", hub, "")[0], "only hubs under people/ are navigation")
        docs = {**DOCS, "people/theodore-pryce.md": "---\nid: person-theodore-pryce\ntype: person\nentity_key: person:theodore-pryce\n"
                "title: \"Theodore Pryce\"\n---\n# Theodore Pryce\n\n- [[sources/historical_calls/pryce|Call with Dr Theodore Pryce]]\n"}
        with patch.dict(DOCS, docs), tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            view = E.entity_view("person:theodore-pryce", db_path=db)
            paths = {item["path"] for item in view["knowledge"] + view["sources"]}
            self.assertNotIn("business_brain:people/theodore-pryce.md", paths)
            self.assertEqual("index", E.document_meta(["business_brain:people/theodore-pryce.md"], db_path=db)
                             ["business_brain:people/theodore-pryce.md"]["role"])
            self.assertEqual("single", E.search_entities("Theodore Pryce", db_path=db)["resolution"])

    def test_ensure_current_rebuilds_only_on_change(self):
        with tempfile.TemporaryDirectory() as temp:
            db = self.build(Path(temp))
            with patch.object(E, "BUSINESS_BRAIN_ROOT", Path(temp) / "vault"):
                self.assertEqual(E.ensure_current(db)["status"], "current")


if __name__ == "__main__":
    unittest.main()
