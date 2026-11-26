"""Layer separation, universal knowledge, context assembly on an empty Brain, and clean installation."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import business_brain_context as bbc
from tools import ttros_init, ttros_layers, universal_knowledge, validate_business_brain

REPO = Path(__file__).resolve().parents[1]


def write_method(root: Path, domain: str, mid: str, body: str = "Steps for the thing.", keywords="[icp, audience]"):
    folder = root / domain / mid.split(".")[-1]
    folder.mkdir(parents=True)
    (folder / "METHOD.md").write_text(
        f"---\nid: {mid}\ntitle: {mid} title\nversion: 1\ndomain: {domain}\nsummary: Summary of {mid}.\nkeywords: {keywords}\n---\n{body}\n",
        encoding="utf-8")


class NoHits:
    BUSINESS_BRAIN_ROOT = Path("/nonexistent")

    @staticmethod
    def search(*_a, **_k):
        return {"groups": {}, "search_mode": "keyword-only"}

    @staticmethod
    def parse_frontmatter(text):
        return {}, text


class StubRegistry:
    def resolve_scope(self, _scope):
        return None


class LayerTests(unittest.TestCase):
    def test_unknown_and_company_paths_are_private(self):
        for path in ("company/website/home.md", "brand_new_dir/x.py", "../etc/passwd", "/abs/path", "universal/_pending/x/METHOD.md",
                     "context/current_site/home.json", "workspaces/a/b.py"):
            self.assertEqual(ttros_layers.classify(path), "private", path)

    def test_software_and_universal_paths(self):
        self.assertEqual(ttros_layers.classify("tools/aos_crm.py"), "software")
        self.assertEqual(ttros_layers.classify("universal/marketing/x/METHOD.md"), "universal")

    def test_repository_has_no_layering_violation(self):
        self.assertEqual(ttros_layers.check(ttros_layers.tracked(REPO), REPO), [])

    def test_only_cleared_and_owner_reviewed_universal_is_shareable(self):
        manifest = {"entries": {
            "universal/a/a/METHOD.md": {"redistribution": "cleared", "authorship": "original", "licence": "MIT", "owner_review": "2026-11-30"},
            "universal/a/b/METHOD.md": {"redistribution": "cleared", "authorship": "original", "licence": "MIT", "owner_review": "pending"},
            "universal/a/c/METHOD.md": {"redistribution": "pending", "owner_review": "2026-11-30"}}}
        self.assertEqual(ttros_layers.cleared_universal(manifest), {"universal/a/a/METHOD.md"})

    def test_manifest_flags_unregistered_and_unlicensed_cleared_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "context").mkdir()
            (root / "context/layers.json").write_text((REPO / "context/layers.json").read_text())
            write_method(root / "universal", "marketing", "universal.marketing.x")
            (root / "universal/manifest.json").write_text(json.dumps({"entries": {}}))
            problems = ttros_layers.validate_manifest(root)
            self.assertTrue(any("not registered" in p for p in problems))
            (root / "universal/manifest.json").write_text(json.dumps({"entries": {
                "universal/marketing/x/METHOD.md": {"redistribution": "cleared", "owner_review": "pending"}}}))
            self.assertTrue(any("requires authorship=original" in p for p in ttros_layers.validate_manifest(root)))


class UniversalKnowledgeTests(unittest.TestCase):
    def test_find_is_deterministic_and_carries_universal_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "universal"
            write_method(root, "marketing", "universal.marketing.icp")
            write_method(root, "sales", "universal.sales.discovery", keywords="[discovery, qualification]")
            hits = universal_knowledge.find("define our ideal icp audience", root=root)
            self.assertEqual([m.id for m in hits], ["universal.marketing.icp"])
            prov = hits[0].provenance()
            self.assertEqual(prov["layer"], "universal")
            self.assertIsNone(prov["client_scope"])
            self.assertNotIn("content", prov)
            self.assertEqual(universal_knowledge.find("audience", domain="sales", root=root), [])

    def test_pending_folder_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "universal"
            write_method(root, "_pending", "universal.x.hidden")
            self.assertEqual(universal_knowledge.list_methods(root), [])
            write_method(root, "a", "universal.a.same")
            write_method(root / "b", "a", "universal.a.same")
            with self.assertRaises(universal_knowledge.UniversalKnowledgeError):
                universal_knowledge.list_methods(root)

    def test_shipped_seed_method_loads(self):
        shipped = list((REPO / "universal").rglob("METHOD.md"))
        self.assertEqual(len(universal_knowledge.list_methods()), len(shipped))


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "universal"
        write_method(self.root, "marketing", "universal.marketing.icp")
        self.loader = bbc.ScopedBrainLoader(registry=StubRegistry(), search_module=NoHits)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_company_brain_still_yields_universal_method(self):
        got = bbc.assemble_context(self.loader, work={"client_scope": "global", "strategy_implication": True},
                                   query="icp audience", universal_root=self.root)
        self.assertEqual(got.company_status, "empty")
        self.assertTrue(got.needs_company_context)
        self.assertEqual([m.id for m in got.universal], ["universal.marketing.icp"])
        self.assertEqual(got.company.brain_context_used, [])

    def test_universal_never_satisfies_company_completion_gate(self):
        work = {"client_scope": "global", "strategy_implication": True}
        with self.assertRaises(bbc.BrainContextError):
            bbc.validate_completion_context(work, brain_context_used=[])

    def test_technical_only_work_is_not_applicable(self):
        got = bbc.assemble_context(self.loader, work={"technical_only": True, "client_scope": "global"},
                                   query="icp", universal_root=self.root)
        self.assertEqual(got.company_status, "not_applicable")


class CleanInstallTests(unittest.TestCase):
    def test_init_creates_empty_valid_unconfigured_brain_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain, repo = Path(tmp) / "brain", Path(tmp) / "repo"
            repo.mkdir()
            first = ttros_init.init(brain, repo, env_file=Path(tmp) / "ttros.env")
            self.assertFalse(first["company_configured"])
            self.assertEqual((first["studio_sites"], first["website_projects"], first["connected_accounts"]), (0, 0, 0))
            self.assertTrue(first["onboarding_needed"])
            self.assertEqual(validate_business_brain.analyze_vault(brain)["status"], "PASS")
            self.assertEqual(subprocess.run(["git", "remote"], cwd=brain, capture_output=True, text=True).stdout, "")
            self.assertEqual((repo / "queue/crm/events.jsonl").read_text(), "")
            self.assertIn(f"TTROS_BRAIN_ROOT={brain}", (Path(tmp) / "ttros.env").read_text())
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=brain, capture_output=True, text=True).stdout
            second = ttros_init.init(brain, repo)
            self.assertEqual(second["created"], [])
            self.assertEqual(head, subprocess.run(["git", "rev-parse", "HEAD"], cwd=brain, capture_output=True, text=True).stdout)

    def test_company_note_without_unconfigured_marker_counts_as_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain, repo = Path(tmp) / "brain", Path(tmp) / "repo"
            repo.mkdir()
            ttros_init.init(brain, repo)
            (brain / "memory/company.md").write_text("---\nid: ttros-brain-company\ntype: knowledge\n---\n# Company\nReal.\n")
            self.assertTrue(ttros_init.status(brain, repo)["company_configured"])

    def test_skeleton_contains_no_company_facts(self):
        text = "\n".join(ttros_init.skeleton().values()).lower()
        for banned in ("@", "http", "$", "pricing:", "offer:"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
