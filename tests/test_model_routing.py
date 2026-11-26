# Revisit: when queue/model_routes.json, the workbench routes or the permission policy change. · Last touched: 2026-11-12.
"""Model routing and the single permission authority. Mock-only: no model is ever launched."""
import importlib
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ModelRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = importlib.import_module("dashboard.backend.main")
        cls.routes = json.loads((ROOT / "queue/model_routes.json").read_text(encoding="utf-8"))

    def test_routes_cover_workbenches_and_lanes_and_no_orchestrator_lane(self):
        self.assertEqual({"revenue", "marketing", "delivery", "operations", "ops", "codex", "claude"}, set(self.routes["routes"]))
        self.assertEqual("default", self.routes["fallback"]["profile_requested"])
        self.assertNotIn("operations_review_waiver", self.routes)

    def test_codex_selection_tiers_use_the_catalogued_models(self):
        selection = self.routes["routes"]["codex"]["codex_selection"]
        for tier in selection["tiers"].values():
            self.assertIn(tier["model"], selection["supported_models"])






class SingleAuthorityTests(unittest.TestCase):
    POLICY = ROOT / "rules/runtime_permission_policy.md"

    def test_prompt_header_is_the_policy_file_and_nothing_else(self):
        import tools.aos_codex_policy as policy  # noqa: PLC0415
        self.assertTrue(policy.PERMISSION_HEADER.endswith(self.POLICY.read_text(encoding="utf-8").strip()))

    def test_templates_defer_to_the_policy_and_add_no_second_permission_text(self):
        for template in (ROOT / "queue/templates").glob("*.prompt.md"):
            text = template.read_text(encoding="utf-8")
            self.assertNotIn("Do not ask for permission", text, template.name)
            self.assertNotIn("Assume approval", text, template.name)
            self.assertNotIn("Hermes", text, template.name)

    def test_active_routing_config_names_no_hermes_runtime(self):
        for name in ("queue/model_routes.json", "queue/command_routes.json", "queue/agent_registry.json"):
            self.assertNotRegex((ROOT / name).read_text(encoding="utf-8"), re.compile("hermes", re.I), name)

    def test_no_hermes_launcher_or_pre_tool_hook_remains(self):
        for retired in ("tools/aos-hermes-coordinator.sh", "tools/aos-hermes-operator-lean.sh",
                        "tools/aos-hermes-dashboard.sh", "hooks/runtime_guard.py",
                        "hooks/context_assembler_hook.py", "queue/lane_profiles.json"):
            self.assertFalse((ROOT / retired).exists(), retired)

    def test_active_instruction_files_name_one_policy(self):
        for name in ("AGENTS.md", "CODEX.md"):
            self.assertIn("runtime_permission_policy.md", (ROOT / name).read_text(encoding="utf-8"), name)
        self.assertIn("@AGENTS.md", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
