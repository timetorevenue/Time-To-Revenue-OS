"""Worktree tests import worktree code: the bridge loader and the conftest leak guard."""
import sys
from pathlib import Path

import conftest
from tests import bridge_loader

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_load_restores_sys_path_and_binds_worktree_modules():
    before = list(sys.path)
    bridge = bridge_loader.load_bridge("telegram_bridge_isolation_probe")
    assert sys.path == before
    assert not [entry for entry in sys.path if entry.startswith(conftest._PRODUCTION_ROOT) and "/.venv/" not in entry]
    for name in bridge_loader.BRIDGE_TOOL_IMPORTS:
        assert Path(getattr(bridge, name).__file__).resolve().is_relative_to(ROOT), name
    # The bridge's own constant still names production; only the test import path is isolated.
    assert str(bridge.WORKSPACE) == "/opt/ttros-core"
    sys.modules.pop("telegram_bridge_isolation_probe", None)


def test_guard_detects_a_production_sys_path_entry_but_not_the_shared_venvs():
    probe = conftest._PRODUCTION_ROOT + "tools"
    sys.path.insert(0, probe)
    try:
        paths, _modules = conftest._production_leaks()
        assert paths == [probe]
    finally:
        sys.path.remove(probe)
    assert conftest._production_leaks() == ([], [])
    assert conftest._THIS_CHECKOUT != conftest._PRODUCTION_ROOT
