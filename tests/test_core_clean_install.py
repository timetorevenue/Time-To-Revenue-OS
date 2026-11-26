"""A normal Core installation is empty: no company, site, client scope, connection or inherited agent authorisation."""
import json
import tempfile
import unittest
from pathlib import Path

import core_skips_plugin as harness
from tools import business_brain_context as bbc
from tools import ttros_init, universal_knowledge

ROOT = Path(__file__).resolve().parents[1]


class ShippedDefaultsAreEmpty(unittest.TestCase):
    def test_registries_hold_no_company_sites_or_extra_scopes(self):
        projects = json.loads(harness.pristine("website_projects.json"))
        sites = json.loads(harness.pristine("website_studio_sites.json"))
        scopes = json.loads(harness.pristine("client_scope_registry.json"))
        self.assertEqual((projects["projects"], sites["sites"]), ({}, {}))
        self.assertEqual(list(scopes["scopes"]), ["global"])
        self.assertEqual(scopes["denied_brain_pointers"], [])

    def test_no_connected_accounts_and_no_inherited_agent_authorisation(self):
        providers = json.loads((ROOT / "context/account_connections.json").read_text())["providers"]
        self.assertFalse([n for n, p in providers.items() if p.get("auth_config_id") or p.get("connected_account_id")])
        self.assertEqual(json.loads((ROOT / "agents/canary_acceptances.json").read_text())["records"], [])
        registry = json.loads(harness.pristine("registry.lock.json"))["agents"]
        self.assertEqual({a["state"] for a in registry.values()}, {"candidate"})
        self.assertTrue(all(len(a.get("history", [])) == 1 for a in registry.values()))

    def test_no_business_context_or_site_mirror_is_shipped(self):
        for rel in ("context/current_site", "queue/prospects.jsonl", "queue/crm", "proofs", "workspaces"):
            self.assertFalse((ROOT / rel).exists(), rel)


class FreshInstallWorks(unittest.TestCase):
    def test_empty_brain_gives_universal_methods_and_needs_company_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain, repo = Path(tmp) / "brain", Path(tmp) / "repo"
            repo.mkdir()
            state = ttros_init.init(brain, repo)
            self.assertFalse(state["company_configured"])
            self.assertTrue(state["onboarding_needed"])
            universal = Path(tmp) / "universal"
            folder = universal / "marketing" / "icp"
            folder.mkdir(parents=True)
            (folder / "METHOD.md").write_text("---\nid: universal.marketing.icp\ntitle: ICP\nversion: 1\ndomain: marketing\n"
                                              "summary: Find the audience.\nkeywords: [icp, audience]\n---\nSteps.\n")

            class Stub:
                BUSINESS_BRAIN_ROOT = brain

                @staticmethod
                def search(*_a, **_k):
                    return {"groups": {}, "search_mode": "keyword-only"}

                @staticmethod
                def parse_frontmatter(text):
                    return {}, text

            class Reg:
                def resolve_scope(self, _s):
                    return None
            got = bbc.assemble_context(bbc.ScopedBrainLoader(registry=Reg(), search_module=Stub),
                                       work={"client_scope": "global", "strategy_implication": True},
                                       query="our ideal audience", universal_root=universal)
            self.assertEqual(got.company_status, "empty")
            self.assertEqual([m.id for m in got.universal], ["universal.marketing.icp"])


if __name__ == "__main__":
    unittest.main()
