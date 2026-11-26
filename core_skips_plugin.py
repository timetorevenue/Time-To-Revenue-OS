"""Core test harness: documented skips (tests/core_skips.json) and the synthetic-fixture overlay.

A fresh installation ships EMPTY runtime registries (no company, site, connection or client scope). Tests that
need example sites or client scopes get synthetic fixtures from tests/fixtures/core_context/ staged over
context/ for the duration of the run only; the pristine files are saved in .core-overlay/ and restored on exit
(and at the next start if a run was killed). Nothing synthetic is ever left in the working tree.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
_SKIPS = json.loads((ROOT / "tests" / "core_skips.json").read_text(encoding="utf-8"))["skips"]
FIXTURES = ROOT / "tests" / "fixtures" / "core_context"
BACKUP = ROOT / ".core-overlay"
OVERLAY = {"client_scope_registry.json": "context", "website_projects.json": "context", "website_studio_sites.json": "context",
           "registry.lock.json": "agents"}


def pristine(name):
    """The shipped (empty) registry text, even while the overlay is staged."""
    return ((BACKUP / name) if (BACKUP / name).exists() else (ROOT / OVERLAY[name] / name)).read_text(encoding="utf-8")


def _restore():
    if BACKUP.is_dir():
        for name, folder in OVERLAY.items():
            if (BACKUP / name).exists():
                shutil.copyfile(BACKUP / name, ROOT / folder / name)
        shutil.rmtree(BACKUP, ignore_errors=True)


def pytest_configure(config):
    if hasattr(config, "workerinput"):          # xdist workers: the controller owns staging
        return
    _restore()
    if not FIXTURES.is_dir():
        return
    BACKUP.mkdir()
    for name, folder in OVERLAY.items():
        if not (FIXTURES / name).exists():
            continue
        shutil.copyfile(ROOT / folder / name, BACKUP / name)
        shutil.copyfile(FIXTURES / name, ROOT / folder / name)


def pytest_unconfigure(config):
    if not hasattr(config, "workerinput"):
        _restore()


def pytest_collection_modifyitems(config, items):
    for item in items:
        for rule in _SKIPS:
            if rule.get("unless_exists") and os.path.exists(os.path.expanduser(rule["unless_exists"])):
                continue
            if item.nodeid.startswith(rule["match"]):
                item.add_marker(pytest.mark.skip(reason="core: " + rule["reason"]))
                break
