# Revisit: when a model launcher is added or renamed. · Last touched: 2026-11-12.
"""The test-wide guard refuses real Codex/Claude/Hermes launches. Mock-only: every blocked
case is refused before any process is created, and allowed cases are checked through the
classifier, never executed."""
import os
import pathlib
import subprocess
import sys

import pytest

import conftest as guard


@pytest.mark.parametrize("argv", [
    ["codex", "exec", "--json", "hi"],
    ["/home/operator/.local/npm/bin/claude", "-p", "hi"],
    ["aos-claude", "--prompt-file", "x"],
    ["aos-codex", "hello"],
    ["aos-hermes", "chat"],
    ["bash", "-lc", "cd /x; codex exec --model m < prompt"],
    ["bash", "-lc", "export A=1; /home/operator/.local/bin/aos-hermes-coordinator.sh --prompt-file p"],
    "claude -p hi",
])
def test_popen_of_paid_launcher_is_refused_before_spawn(argv):
    with pytest.raises(guard.PaidModelBlocked):
        subprocess.Popen(argv, shell=isinstance(argv, str))


def test_subprocess_run_and_os_system_are_refused():
    with pytest.raises(guard.PaidModelBlocked):
        subprocess.run(["codex", "exec", "x"], capture_output=True)
    with pytest.raises(guard.PaidModelBlocked):
        os.system("codex exec x")


@pytest.mark.parametrize("argv", [
    ["claude", "--version"],
    ["bash", "-lc", "command -v aos-hermes >/dev/null && command -v aos-claude >/dev/null"],
    ["bash", "-lc", "/home/operator/.local/npm/bin/claude --version"],
    [sys.executable, "-c", "print('codex')"],
    ["git", "status"],
    ["ls", "codex_notes"],
])
def test_free_probes_and_unrelated_commands_are_not_blocked(argv):
    assert guard._launcher_in(argv) is None


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_workbench_runners_are_blocked_without_mocks(provider):
    """Current source entrypoints are refused before even their shell is spawned."""
    launcher = pathlib.Path(__file__).resolve().parents[1] / 'scripts/workbench' / ('aos-' + provider)
    with pytest.raises(guard.PaidModelBlocked):
        subprocess.run(['bash', str(launcher), 'hello'])


@pytest.mark.paid_integration
def test_paid_marker_is_skipped_unless_explicitly_enabled():
    assert os.environ.get("TTROS_ALLOW_PAID_TESTS") == "1"


def test_launcher_stub_created_under_temp_dir_is_not_a_paid_cli(tmp_path):
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n")
    assert guard._launcher_in([str(stub), "-p", "x"]) is None
