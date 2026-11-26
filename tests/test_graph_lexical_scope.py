"""Stage 1: lexical scope ranking + full check / sha256 only on the returned notes.

Every detector has a case that must return the other answer (a symlink the lexical gate accepts but
the full check rejects, a stale note that is returned when the check is off, a sabotaged new path
that the shadow comparison must flag).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard.backend import business_brain_graph as graph_module
from dashboard.backend.business_brain_graph import BusinessBrainGraphService
from tests.business_brain_test_support import registry_data, make_registry, write_note

GLOBAL_PREFIX = "business_brain:topics/"
QUERY = "atlas planning topic"


def registry(denied=()):
    data = registry_data()
    data["denied_brain_pointers"] = list(data["denied_brain_pointers"]) + list(denied)
    scope = data["scopes"]["global"]
    scope["brain_pointer_prefixes"] = [GLOBAL_PREFIX]
    scope["graphify_targets"][0]["path_prefixes"] = [GLOBAL_PREFIX]
    return make_registry(data)


class LexicalScopeTest(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("TTROS_GRAPH_LEXICAL_SCOPE", None)
        self.addCleanup(self._env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.vault, self.graphify, self.outside = root / "vault", root / "graphify", root / "outside"
        write_note(self.vault / "README.md", "root", "Root", "root")
        write_note(self.vault / "index/MEMORY_INDEX.md", "index", "Index", "index")
        write_note(self.vault / "memory/global.md", "global", "Global", "global")
        write_note(self.vault / "memory/client-a.md", "note-a", "Atlas planning topic client", "CLIENT-A-SENTINEL")
        write_note(self.vault / "memory/client-b.md", "note-b", "Atlas planning topic client", "CLIENT-B-SENTINEL")
        for number in range(1, 11):
            write_note(self.vault / f"topics/t{number:02d}.md", f"topic-{number:02d}", f"Atlas planning topic {number:02d}", f"body {number}")
        write_note(self.vault / "topics/protected.md", "topic-protected", "Atlas planning topic protected", "PROTECTED-SENTINEL")
        write_note(self.outside / "secret.md", "secret", "Atlas planning topic secret", "OUTSIDE-SENTINEL")
        (self.vault / "topics" / "escape.md").symlink_to(self.outside / "secret.md")
        self.build_registry = registry()
        # The full check resolves pointers against the canonical vault root; point it at the fixture
        # so the symlink-escape check is real (otherwise the escape is invisible to it).
        scope_module = __import__(type(self.build_registry).__module__, fromlist=["business_brain"])
        patcher = mock.patch.object(scope_module.business_brain, "BUSINESS_BRAIN_ROOT", self.vault)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = BusinessBrainGraphService(graphify_root=self.graphify, vault_root=self.vault, registry=self.build_registry)
        self.service.build()

    def query(self, mode, *, client_scope="global", reg=None, limit=5):
        reg = reg or self.build_registry
        if mode is None:
            os.environ.pop("TTROS_GRAPH_LEXICAL_SCOPE", None)
        else:
            os.environ["TTROS_GRAPH_LEXICAL_SCOPE"] = mode
        return self.service.query_targets(QUERY, limit=limit, client_scope=client_scope, registry=reg)

    @staticmethod
    def rows(result):
        return [(row["path"], row["score"]) for row in result["targets"]]

    def counting(self, reg):
        return mock.patch.object(reg, "validate_graph_target", wraps=reg.validate_graph_target)

    def test_same_targets_same_order_and_far_fewer_full_checks(self):
        reg = self.build_registry
        with self.counting(reg) as old_calls:
            old = self.query(None)
        with self.counting(reg) as new_calls:
            new = self.query("1")
        self.assertEqual(self.rows(old), self.rows(new))
        self.assertEqual(5, len(old["targets"]))
        # Detector can return both answers: the original path really did check every candidate.
        self.assertGreaterEqual(old_calls.call_count, 12)
        self.assertLessEqual(new_calls.call_count, 5 + graph_module.SCOPE_OVERSCAN)
        self.assertLess(new_calls.call_count, old_calls.call_count)
        self.assertEqual("lexical_topk", new["scope_check"]["strategy"])
        self.assertNotIn("scope_check", old)

    def test_symlink_escape_accepted_lexically_rejected_by_full_check_and_refilled(self):
        gate = self.build_registry.lexical_graph_gate("global", self.service.namespace)
        self.assertEqual("business_brain:topics/escape.md", gate("business_brain:topics/escape.md"))  # lexical alone accepts it
        with self.assertRaises(Exception):
            self.build_registry.validate_graph_target("global", self.service.namespace, "business_brain:topics/escape.md")
        for mode in (None, "1"):
            result = self.query(mode)
            paths = [row["path"] for row in result["targets"]]
            self.assertNotIn("business_brain:topics/escape.md", paths)
            self.assertEqual(5, len(paths))  # refilled with the next ranked note
            self.assertNotIn("OUTSIDE-SENTINEL", json.dumps(result))

    def test_client_scope_isolation(self):
        reg = self.build_registry
        for mode in (None, "1"):
            a = self.query(mode, client_scope="client-a")
            b = self.query(mode, client_scope="client-b")
            self.assertEqual(["business_brain:memory/client-a.md"], [row["path"] for row in a["targets"]])
            self.assertEqual(["business_brain:memory/client-b.md"], [row["path"] for row in b["targets"]])
            self.assertNotIn("client-b", json.dumps(a))
            self.assertNotIn("client-a", json.dumps(b))
        with self.counting(reg) as calls:
            self.query("1", client_scope="client-a")
        checked = [call.args[2] for call in calls.call_args_list]
        self.assertTrue(all("client-b" not in pointer for pointer in checked))
        # Negative: an unknown scope is still refused up front.
        with self.assertRaises(Exception):
            self.query("1", client_scope="client-disabled")

    def test_denied_pointer_stays_denied(self):
        denying = registry(denied=["business_brain:topics/protected.md"])
        allowing_paths = [row["path"] for row in self.query("1", limit=20)["targets"]]
        self.assertIn("business_brain:topics/protected.md", allowing_paths)  # detector can say yes
        for mode in (None, "1", "shadow"):
            result = self.query(mode, reg=denying, limit=20)
            self.assertNotIn("business_brain:topics/protected.md", [row["path"] for row in result["targets"]])
            self.assertNotIn("PROTECTED-SENTINEL", json.dumps(result))

    def test_stale_hash_is_caught_on_returned_notes_only(self):
        fresh = {"state": "fresh", "trusted": True, "fallback": None}
        with mock.patch.object(self.service, "status", return_value=fresh):
            before = self.query("1")
            self.assertEqual([], before["scope_check"]["hash_dropped"])
            note = self.vault / "topics" / "t01.md"
            note.write_text(note.read_text() + "\nedited after the graph was published\n", encoding="utf-8")
            original_path_result = self.query(None)  # original path never checks hashes: still returns it
            self.assertIn("business_brain:topics/t01.md", [row["path"] for row in original_path_result["targets"]])
            result = self.query("1")
        self.assertNotIn("business_brain:topics/t01.md", [row["path"] for row in result["targets"]])
        self.assertEqual(["business_brain:topics/t01.md"], result["scope_check"]["hash_dropped"])
        self.assertEqual(5, len(result["targets"]))

    def test_failures_beyond_overscan_fall_back_to_the_original_path(self):
        fresh = {"state": "fresh", "trusted": True, "fallback": None}
        for number in range(1, 8):
            note = self.vault / "topics" / f"t{number:02d}.md"
            note.write_text(note.read_text() + "\nchanged\n", encoding="utf-8")
        strategies = []
        real = BusinessBrainGraphService._targets_from_graph

        def spy(service, query, limit, gate, identity, graph, strategy):
            strategies.append(strategy)
            return real(service, query, limit, gate, identity, graph, strategy)

        with mock.patch.object(self.service, "status", return_value=fresh), \
                mock.patch.object(BusinessBrainGraphService, "_targets_from_graph", spy):
            result = self.query("1")
            original = self.query(None)
        self.assertEqual(["lexical", "full", "full"], strategies)
        self.assertEqual(self.rows(original), self.rows(result))

    def test_shadow_mode_returns_original_and_logs_equal(self):
        log = Path(self.tmp.name) / "shadow.jsonl"
        with mock.patch.dict(os.environ, {"TTROS_GRAPH_SHADOW_LOG": str(log)}):
            shadow = self.query("shadow")
        original = self.query(None)
        self.assertEqual(self.rows(original), self.rows(shadow))
        self.assertNotIn("scope_check", shadow)
        row = json.loads(log.read_text().splitlines()[-1])
        self.assertTrue(row["equal"])
        self.assertGreater(row["old_validate_calls"], row["new_validate_calls"])

    def test_shadow_comparison_flags_a_sabotaged_new_path(self):
        log = Path(self.tmp.name) / "shadow_bad.jsonl"
        real = BusinessBrainGraphService._targets_from_graph

        def sabotaged(service, query, limit, gate, identity, graph, strategy):
            result, stats = real(service, query, limit, gate, identity, graph, strategy)
            if strategy == "lexical" and result:
                result = {**result, "targets": list(reversed(result["targets"]))}
            return result, stats

        with mock.patch.dict(os.environ, {"TTROS_GRAPH_SHADOW_LOG": str(log)}), \
                mock.patch.object(BusinessBrainGraphService, "_targets_from_graph", sabotaged):
            self.query("shadow")
        self.assertFalse(json.loads(log.read_text().splitlines()[-1])["equal"])

    def test_flag_default_is_off(self):
        os.environ.pop("TTROS_GRAPH_LEXICAL_SCOPE", None)
        self.assertEqual("off", graph_module.lexical_scope_mode())
        for value, expected in (("0", "off"), ("", "off"), ("garbage", "off"), ("1", "on"), ("shadow", "shadow")):
            with mock.patch.dict(os.environ, {"TTROS_GRAPH_LEXICAL_SCOPE": value}):
                self.assertEqual(expected, graph_module.lexical_scope_mode())


if __name__ == "__main__":
    unittest.main()
