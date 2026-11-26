# Revisit: when a model launcher is added or renamed. · Last touched: 2026-11-12.
"""Test-wide paid-model guard.

No automated test may start a real Codex, Claude or Hermes process. Every
subprocess in the test run (subprocess.run/check_output/Popen, os.system and
the exec/spawn family) passes through the check below. A launch of a paid
model CLI raises PaidModelBlocked, so a stale test that forgets to mock a
workbench fails fast and free instead of spending tokens.

The only exception is a test explicitly marked ``@pytest.mark.paid_integration``
while ``TTROS_ALLOW_PAID_TESTS=1`` is set; unmarked runs skip those tests.
Version/help probes (``claude --version``) and ``command -v`` checks are free
and stay allowed.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile

import pytest

PAID_LAUNCHERS = {"codex", "claude", "aos-codex", "aos-claude", "hermes", "aos-hermes"}
_LAUNCHER_RE = re.compile(
    r"(?:^|[\s;&|(`'\"/])(codex|claude|aos-codex|aos-claude|hermes|aos-hermes(?:-[a-z-]+)?(?:\.sh)?)(?=$|[\s;&|)`'\"])"
)
_FREE_FLAGS = {"--version", "-v", "-V", "--help", "-h"}
_ALLOW = {"active": False}


class PaidModelBlocked(RuntimeError):
    """A test tried to launch a real paid model CLI."""


def _is_free_probe(tokens: list[str]) -> bool:
    return bool(tokens) and all(t in _FREE_FLAGS for t in tokens[1:]) and len(tokens) <= 2


def _is_test_stub(executable: str) -> bool:
    """A launcher the test itself created under the system temp dir is a stub, not a paid CLI."""
    resolved = executable if os.path.isabs(executable) else (shutil.which(executable) or "")
    if not resolved:
        return False
    if not os.path.exists(resolved):
        return True  # nothing there to launch
    tmp = os.path.realpath(tempfile.gettempdir())
    return os.path.realpath(resolved).startswith(tmp + os.sep)


def _launcher_in(argv) -> str | None:
    if isinstance(argv, (str, bytes, os.PathLike)):
        argv = [os.fsdecode(argv)]
    else:
        argv = [os.fsdecode(a) for a in argv]
    if not argv:
        return None
    head = os.path.basename(argv[0])
    if head in PAID_LAUNCHERS or head.startswith("aos-hermes"):
        if _is_test_stub(argv[0]) or _is_free_probe(argv):
            return None
        return head
    # Shell wrappers: only a shell's command string can hide a launcher invocation. Other
    # programs (python aos-queue.py --owner codex) merely mention the name in an argument.
    is_string_cmd = len(argv) == 1 and " " in argv[0]
    if head not in {"bash", "sh", "zsh", "dash"} and not is_string_cmd:
        return None
    rest = argv if is_string_cmd else argv[1:]
    while rest and re.fullmatch(r"-[a-z]*c[a-z]*|-l|--login", rest[0]):
        rest = rest[1:]
    text = " ".join(rest)
    for segment in re.split(r"[;&|\n]+", text):
        segment = segment.strip()
        if re.match(r"^(?:command\s+-v|which|type)\b", segment):
            continue
        match = _LAUNCHER_RE.search(segment)
        if not match:
            continue
        try:
            tokens = shlex.split(segment[match.start(1):])
        except ValueError:
            tokens = segment[match.start(1):].split()
        if not _is_free_probe(tokens):
            return match.group(1)
    return None


def _check(argv) -> None:
    if _ALLOW["active"]:
        return
    launcher = _launcher_in(argv)
    if launcher:
        raise PaidModelBlocked(
            f"test attempted to launch real model CLI {launcher!r} (argv: {str(argv)[:160]}); mock the workbench "
            "or mark the test @pytest.mark.paid_integration and set TTROS_ALLOW_PAID_TESTS=1"
        )


_orig_popen_init = subprocess.Popen.__init__
_orig_system = os.system


def _guarded_popen_init(self, args, *a, **kw):
    _check(args)
    _orig_popen_init(self, args, *a, **kw)


def _guarded_system(command):
    _check(command)
    return _orig_system(command)


def pytest_configure(config):
    config.addinivalue_line("markers", "paid_integration: may launch a real paid model; needs TTROS_ALLOW_PAID_TESTS=1")
    subprocess.Popen.__init__ = _guarded_popen_init
    os.system = _guarded_system


def pytest_unconfigure(config):
    subprocess.Popen.__init__ = _orig_popen_init
    os.system = _orig_system


@pytest.fixture(autouse=True)
def _paid_model_guard(request):
    marked = request.node.get_closest_marker("paid_integration") is not None
    if marked and os.environ.get("TTROS_ALLOW_PAID_TESTS") != "1":
        pytest.skip("paid_integration test; set TTROS_ALLOW_PAID_TESTS=1 to run")
    _ALLOW["active"] = marked
    try:
        yield
    finally:
        _ALLOW["active"] = False


# -- production import isolation ---------------------------------------------------------
# A worktree's tests must import the worktree's code. Some preserved runtime modules (the
# Telegram bridge) hard-code the production checkout and put its tools/ on sys.path; a test
# that leaves that behind makes every later import silently resolve to production. Each test
# is checked at teardown; a leak fails that test and is removed so it cannot spread.
# Inside the production checkout itself the guard is a no-op.
_PRODUCTION_ROOT = "/opt/ttros-core/"
_THIS_CHECKOUT = os.path.dirname(os.path.abspath(__file__)) + "/"


def _production_leaks() -> tuple[list[str], list[str]]:
    import sys

    def production(path) -> bool:
        text = str(path or "")
        return text.startswith(_PRODUCTION_ROOT) and "/.venv/" not in text

    paths = [entry for entry in sys.path if production(entry)]
    modules = sorted(name for name, module in list(sys.modules.items())
                     if production(getattr(module, "__file__", None)))
    return paths, modules


@pytest.fixture(autouse=True)
def _production_import_guard():
    yield
    if _THIS_CHECKOUT == _PRODUCTION_ROOT:
        return
    import sys

    paths, modules = _production_leaks()
    if paths or modules:
        sys.path[:] = [entry for entry in sys.path if entry not in paths]
        for name in modules:
            sys.modules.pop(name, None)
        pytest.fail(f"test leaked production imports into this worktree run: sys.path={paths} modules={modules[:8]}")


# --- ttros-core: documented skips (tests/core_skips.json) -------------------------------------
pytest_plugins = ["core_skips_plugin"]
