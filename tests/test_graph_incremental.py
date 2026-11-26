"""Incremental Graphify: change-driven updates must equal a from-scratch build, byte for byte.

Every scenario applies a change with apply_changes() (or the stat-only detect_changes()) and then
builds the same vault from scratch into a second Graphify root: the three published artifacts must
hash identically. Real installed Graphify extractor, fixture vault, no live data.
"""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard.backend.business_brain_graph import BusinessBrainGraphService
from tests.business_brain_test_support import make_registry, registry_data

ARTIFACTS = ("graph.json", "source_manifest.json", "projection_manifest.json")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def note(note_id: str, title: str, body: str, extra: str = "", kind: str = "knowledge") -> str:
    return f"---\nid: {note_id}\ntype: {kind}\n{extra}---\n# {title}\n\n{body}\n"


def prefix_registry():
    data = registry_data()
    data["scopes"]["global"]["graphify_targets"] = [{
        "namespace": "ttros-business-brain",
        "paths": ["business_brain:README.md"],
        "path_prefixes": ["business_brain:memory/", "business_brain:prospects/"],
    }]
    data["scopes"]["global"]["brain_pointer_prefixes"] = ["business_brain:memory/", "business_brain:prospects/"]
    return make_registry(data)


def artifact_hashes(directory: Path) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in ARTIFACTS}


class IncrementalGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.registry = prefix_registry()
        write(self.vault / "README.md", note("root", "Root", "[[memory/company|Company]] · [[memory/offers]]"))
        write(self.vault / "memory/company.md", note("company", "Company", "See [[memory/offers|Offers]] and [[memory/later]].\n\n## Team\n\nCasey."))
        write(self.vault / "memory/offers.md", note("offers", "Offers", "Scoped workflow builds.\n\n## Pricing\n\nFixed."))
        write(self.vault / "memory/sub/a.md", note("sub-a", "Sub A", "Sibling link [[b]]."))
        write(self.vault / "memory/sub/b.md", note("sub-b", "Sub B", "Back to [[memory/company]]."))
        write(self.vault / "prospects/acme.md", note("acme", "Acme", "Prospect.", "queue_ids:\n  - AOS-2026-0001\n", kind="prospect"))
        self.service = self.make_service(self.root / "graphify", self.root / "state.db")
        built = self.service.build()
        self.assertEqual(built["incremental_state"]["status"], "synced", built["incremental_state"])

    def tearDown(self):
        self.temp.cleanup()

    def make_service(self, graphify: Path, state_db: Path | None):
        return BusinessBrainGraphService(graphify_root=graphify, vault_root=self.vault, registry=self.registry, state_db=state_db)

    def assert_matches_full_build(self, label: str):
        oracle_root = self.root / f"oracle-{label}"
        oracle = self.make_service(oracle_root, None)
        oracle.build()
        self.assertEqual(artifact_hashes(self.service.published), artifact_hashes(oracle.published), label)

    def apply(self, *paths: str) -> dict:
        result = self.service.apply_changes(list(paths))
        self.assertIn(result["status"], {"success", "unchanged"}, result)
        return result

    def test_edit_add_delete_rename_and_id_change_equal_a_full_build(self):
        write(self.vault / "memory/offers.md", note("offers", "Offers", "Scoped builds.\n\n## Pricing\n\nFixed.\n\n## Terms\n\nNet 30."))
        self.assertEqual(self.apply("memory/offers.md")["changed"], ["memory/offers.md"])
        self.assert_matches_full_build("edit")

        # A new note that an existing note already links to: the linking note is re-projected.
        write(self.vault / "memory/later.md", note("later", "Later", "Points at [[memory/offers]]."))
        result = self.apply("memory/later.md")
        self.assertEqual(result["relinked"], ["memory/company.md"])
        self.assert_matches_full_build("add")

        # Sibling resolution: memory/sub/a.md links [[b]] -> memory/sub/b.md.
        (self.vault / "memory/sub/b.md").unlink()
        result = self.apply("memory/sub/b.md")
        self.assertEqual(result["removed"], ["memory/sub/b.md"])
        self.assertEqual(result["relinked"], ["memory/sub/a.md"])
        self.assert_matches_full_build("delete")

        # Rename: old path gone, new path present, same note id.
        (self.vault / "memory/offers.md").rename(self.vault / "memory/offer-book.md")
        self.apply("memory/offers.md", "memory/offer-book.md")
        self.assert_matches_full_build("rename")

        # Frontmatter id change: every note linking to it must be re-projected.
        write(self.vault / "memory/later.md", note("later-v2", "Later", "Points at [[memory/offer-book]]."))
        result = self.apply("memory/later.md")
        self.assertIn("memory/company.md", result["relinked"])
        self.assert_matches_full_build("id-change")

        # Activity nodes from prospect queue_ids appear and disappear with the note.
        write(self.vault / "prospects/acme.md", note("acme", "Acme", "Prospect.", "queue_ids:\n  - AOS-2026-0002\n", kind="prospect"))
        self.apply("prospects/acme.md")
        self.assert_matches_full_build("activity")
        graph = json.loads((self.service.published / "graph.json").read_text())
        self.assertEqual({n["id"] for n in graph["nodes"] if n["kind"] == "activity"}, {"activity:AOS-2026-0002"})

    def test_unchanged_content_is_a_no_op_and_revision_moves_only_on_change(self):
        before = artifact_hashes(self.service.published)
        os.utime(self.vault / "memory/offers.md", None)
        self.assertEqual(self.apply("memory/offers.md")["status"], "unchanged")
        self.assertEqual(artifact_hashes(self.service.published), before)
        write(self.vault / "memory/offers.md", note("offers", "Offers", "Changed."))
        self.assertEqual(self.apply("memory/offers.md")["revision"], 2)

    def test_stat_sweep_applies_out_of_band_edits_and_detects_nothing_when_idle(self):
        self.assertEqual(self.service.detect_changes()["status"], "unchanged")
        write(self.vault / "memory/new-from-windows.md", note("windows", "Windows Edit", "Made outside a transaction."))
        (self.vault / "memory/sub/a.md").unlink()
        result = self.service.detect_changes()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["stat_candidates"], 2)
        self.assert_matches_full_build("sweep")
        self.assertEqual(self.service.detect_changes()["status"], "unchanged")

    def test_indexed_status_reads_no_note_and_falls_back_to_the_full_scan(self):
        flags = {"TTROS_GRAPH_INCREMENTAL": "1", "TTROS_GRAPH_LEXICAL_SCOPE": "1"}
        with mock.patch.dict(os.environ, flags), mock.patch.object(BusinessBrainGraphService, "_notes", side_effect=AssertionError("vault scanned")):
            state = self.service.status()
        self.assertEqual((state["state"], state["status_mode"], state["revision"]), ("fresh", "indexed", 1))
        # Flag off (or lexical off): the original full scan.
        for env in ({"TTROS_GRAPH_LEXICAL_SCOPE": "1"}, {"TTROS_GRAPH_INCREMENTAL": "1"}):
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("TTROS_GRAPH_INCREMENTAL" if "TTROS_GRAPH_LEXICAL_SCOPE" in env else "TTROS_GRAPH_LEXICAL_SCOPE", None)
                self.assertNotIn("status_mode", self.service.status())
        # Published manifest replaced behind the state's back -> state not trusted -> full scan.
        manifest = self.service.published / "source_manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        with mock.patch.dict(os.environ, flags):
            self.assertNotIn("status_mode", self.service.status())

    def test_out_of_band_edit_before_the_sweep_is_dropped_at_return(self):
        flags = {"TTROS_GRAPH_INCREMENTAL": "1", "TTROS_GRAPH_LEXICAL_SCOPE": "1"}
        write(self.vault / "memory/offers.md", note("offers", "Offers", "Tampered outside any transaction."))
        with mock.patch.dict(os.environ, flags):
            result = self.service.query_targets("offers", client_scope="global", registry=self.registry)
            self.assertEqual(result["graph_state"], "fresh")
            self.assertNotIn("business_brain:memory/offers.md", [row["path"] for row in result["targets"]])
            self.assertIn("business_brain:memory/offers.md", result["scope_check"]["hash_dropped"])
            self.service.detect_changes()
            result = self.service.query_targets("offers", client_scope="global", registry=self.registry)
            self.assertIn("business_brain:memory/offers.md", [row["path"] for row in result["targets"]])

    def test_duplicate_id_fails_closed_and_leaves_the_published_graph(self):
        before = artifact_hashes(self.service.published)
        write(self.vault / "memory/dupe.md", note("offers", "Dupe", "Same id as offers."))
        result = self.service.apply_changes(["memory/dupe.md"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(artifact_hashes(self.service.published), before)
        with mock.patch.dict(os.environ, {"TTROS_GRAPH_INCREMENTAL": "1", "TTROS_GRAPH_LEXICAL_SCOPE": "1"}):
            self.assertEqual(self.service.status()["state"], "unavailable")  # the full scan's own verdict
        self.assertEqual(self.service.apply_changes(["memory/offers.md"])["status"], "unsynced")

    def test_symlink_escape_and_path_traversal_are_never_read(self):
        outside = self.root / "outside.md"
        write(outside, note("outside", "Outside", "OUTSIDE-SENTINEL"))
        (self.vault / "memory/escape.md").symlink_to(outside)
        self.assertEqual(self.service.apply_changes(["memory/escape.md", "../outside.md", "/etc/passwd"])["status"], "unchanged")
        self.assertNotIn("OUTSIDE-SENTINEL", (self.service.published / "graph.json").read_text())
        self.assertNotIn("escape.md", (self.service.published / "source_manifest.json").read_text())

    def test_nightly_build_records_parity_with_incremental_updates(self):
        write(self.vault / "memory/offers.md", note("offers", "Offers", "Edited."))
        self.apply("memory/offers.md")
        receipt = self.service.build()
        self.assertEqual(receipt["operation"], "unchanged")
        self.assertEqual(receipt["incremental_parity"], {"checked_revision": 2, "equal": True, "differing_artifacts": []})

    def test_non_live_graph_without_a_state_db_stays_on_the_full_path(self):
        plain = self.make_service(self.root / "graphify-plain", None)
        plain.build()
        self.assertIsNone(plain.state_db)
        self.assertEqual(plain.apply_changes(["memory/offers.md"])["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
