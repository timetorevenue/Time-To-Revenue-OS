"""Load the preserved Telegram bridge for tests without leaking production imports.

The bridge hard-codes its production workspace and inserts ``/opt/ttros-core/tools``
at the front of ``sys.path`` when it is executed. Its own tool imports, and every later
test import, would then resolve to the production checkout. This loader binds the bridge's
tool modules to this worktree first, restores ``sys.path`` after execution and proves
where those modules came from.

Revisit: when the bridge's module-level imports or workspace constant change. · 2026-11-15.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
BRIDGE = ROOT / "connectors" / "telegram_bridge" / "telegram_bridge.py"
BRIDGE_TOOL_IMPORTS = ("business_brain", "business_brain_inbox", "business_brain_scope", "aos_authority")


def _from_worktree(module) -> bool:
    return Path(getattr(module, "__file__", "") or "/").resolve().is_relative_to(ROOT)


def load_bridge(module_name: str = "telegram_bridge_under_test"):
    """Execute the bridge with a fake token; its imports resolve to this worktree only."""
    saved_path = list(sys.path)
    for entry in (str(TOOLS), str(ROOT)):
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)
    try:
        for name in BRIDGE_TOOL_IMPORTS:
            module = sys.modules.get(name)
            if module is not None and not _from_worktree(module):
                del sys.modules[name]   # a stale production copy must not satisfy the bridge
            importlib.import_module(name)
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, BRIDGE)
        bridge = importlib.util.module_from_spec(spec)
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="TELEGRAM_BOT_TOKEN=fake-token\n"), \
             patch.object(Path, "mkdir"):
            spec.loader.exec_module(bridge)
    finally:
        sys.path[:] = saved_path        # drops the bridge's production tools entry
    leaked = [name for name in BRIDGE_TOOL_IMPORTS if not _from_worktree(getattr(bridge, name))]
    if leaked:
        raise AssertionError(f"bridge bound production modules: {leaked}")
    return bridge
