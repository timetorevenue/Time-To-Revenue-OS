"""Regression contract for mandatory Agentic OS Codex full-access execution.

Revisit: when an Agentic OS route gains a new Codex subprocess constructor. · Last touched: 2026-10-25.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tools import aos_codex_policy as policy


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path("/home/operator/.local/bin/aos-codex")
HERMES_LAUNCHER = Path("/home/operator/.local/bin/aos-hermes")


class AosCodexPolicyTest(unittest.TestCase):
    def test_authoritative_command_is_explicit_and_not_caller_downgradable(self):
        command = policy.build_exec_command()
        self.assertEqual("gpt-6-sol", policy.CODEX_MODEL)
        self.assertEqual("/home/operator/.local/npm/bin/codex", command[0])
        self.assertEqual("danger-full-access", command[command.index("--sandbox") + 1])
        self.assertEqual("never", command[command.index("--ask-for-approval") + 1])
        self.assertEqual("/opt/ttros-core", command[command.index("-C") + 1])
        self.assertIn("exec", command)
        self.assertEqual(command[command.index("--model") + 1], policy.CODEX_MODEL)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("resume", command)
        self.assertNotIn("--last", command)
        self.assertNotIn("workspace-write", command)
        self.assertNotIn("/mnt/c", " ".join(command))
        parameters = inspect.signature(policy.build_exec_command).parameters
        self.assertNotIn("sandbox", parameters)
        self.assertNotIn("approval_policy", parameters)
        self.assertNotIn("root", parameters)
        prompt = policy.prepare_fresh_prompt("bounded task sentinel")
        self.assertEqual(f"{policy.PERMISSION_HEADER}\n\nbounded task sentinel\n", prompt)
        # Isolation is the --ephemeral launch plus require_clean_session_id, not prose.
        self.assertNotIn("ephemeral", prompt)

    def test_codex_environment_blocks_push_but_not_fetch(self):
        env = policy.build_environment()
        for key, value in policy.NO_PUSH_GIT_ENV.items():
            self.assertEqual(value, env[key])
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "remote", "add", "origin", "https://example.invalid/r.git"], check=True)

            def url(*args: str, environ: dict) -> str:
                return subprocess.run(
                    ["git", "-C", tmp, "remote", "get-url", *args, "origin"],
                    env=environ, check=True, capture_output=True, text=True,
                ).stdout.strip()

            plain = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
            self.assertEqual("https://example.invalid/r.git", url("--push", environ=plain))  # control
            self.assertEqual("codex-push-forbidden://https://example.invalid/r.git", url("--push", environ=env))
            self.assertEqual("https://example.invalid/r.git", url(environ=env))

    def test_clean_session_identity_is_required_and_never_synthesized(self):
        self.assertEqual(
            "fresh-session-id",
            policy.require_clean_session_id('{"type":"thread.started","thread_id":"fresh-session-id"}\n'),
        )
        with self.assertRaisesRegex(policy.CodexPolicyError, "clean-session creation failed"):
            policy.require_clean_session_id('{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n')
        with self.assertRaisesRegex(policy.CodexPolicyError, "ambiguous"):
            policy.require_clean_session_id(
                '{"type":"thread.started","thread_id":"one"}\n'
                '{"type":"thread.started","thread_id":"two"}\n'
            )
        with self.assertRaisesRegex(policy.CodexPolicyError, "ambiguous"):
            policy.require_clean_session_id(
                '{"type":"thread.started","thread_id":"same"}\n'
                '{"type":"thread.started","thread_id":"same"}\n'
            )

    def test_runtime_validation_rejects_alternate_root_user_and_missing_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "codex"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            fixture = replace(
                policy.CODEX_TARGET,
                root=root,
                executable=binary,
                codex_home=root / ".codex",
                linux_user=policy.effective_linux_user(),
            )
            metadata = policy.validate_runtime(root, fixture)
            self.assertEqual("danger-full-access", metadata["sandbox"])
            self.assertEqual("never", metadata["approval_policy"])
            with self.assertRaisesRegex(policy.CodexPolicyError, "cwd must be"):
                policy.validate_runtime(root / "downgraded-route", fixture)
            with self.assertRaisesRegex(policy.CodexPolicyError, "Linux user must be"):
                policy.validate_runtime(root, replace(fixture, linux_user="not-casey"))
            with self.assertRaisesRegex(policy.CodexPolicyError, "missing or not executable"):
                policy.validate_runtime(root, replace(fixture, executable=root / "missing-codex"))






if __name__ == "__main__":
    unittest.main()
