"""Release gate for the stale-backend restart unit (2026-11-18).

Development HEAD moves in the live checkout must never restart production; runtime-branch
releases and explicit operator releases still do. No test touches the real backend or systemd.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import runtime_code_staleness as rcs  # noqa: E402

STALE = {"code": {"stale": True, "changed": ["dashboard/backend/website_studio.py"], "changed_count": 1}}


class Systemctl:
    def __init__(self, returncode=0):
        self.calls, self.returncode = [], returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, "", "boom" if self.returncode else "")


@pytest.fixture
def health(monkeypatch):
    state = {"responses": [STALE, {"code": {"stale": False}}]}

    def fake(timeout=5.0):
        value = state["responses"][0]
        if len(state["responses"]) > 1:
            state["responses"].pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(rcs, "_health", fake)
    monkeypatch.setattr(rcs.time, "sleep", lambda s: None)
    return state


def run(branch, health, **kw):
    systemctl = Systemctl()
    result = rcs.restart_if_stale(systemctl=systemctl, branch=lambda: branch, env={}, wait_seconds=5, **kw)
    return result, systemctl.calls


@pytest.mark.parametrize("branch", ["feature/ttros-website-studio-20261118", "fix/anything", None])
def test_development_or_detached_head_defers_and_never_restarts(health, branch):
    result, calls = run(branch, health)
    assert calls == []
    assert result["action"] == "deferred" and result["branch"] == branch
    assert result["changed"] == ["dashboard/backend/website_studio.py"]
    assert result["runtime_branches"] == ["main"]


def test_runtime_branch_release_still_restarts_and_verifies(health):
    result, calls = run("main", health)
    assert calls == [["systemctl", "--user", "restart", "aos-backend"]]
    assert result["action"] == "restarted" and result["stale_after_restart"] is False and result["branch"] == "main"


def test_explicit_operator_release_restarts_from_any_branch(health):
    result, calls = run("feature/x", health, release=True)
    assert len(calls) == 1 and result["action"] == "restarted" and result["release"] is True


def test_dry_run_never_restarts(health):
    result, calls = run("main", health, dry_run=True)
    assert calls == [] and result["action"] == "would_restart"
    health["responses"] = [STALE]
    result, calls = run("feature/x", health, dry_run=True)
    assert calls == [] and result["action"] == "deferred"


def test_current_backend_or_unreachable_health_is_unchanged(health):
    health["responses"] = [{"code": {"stale": False}}]
    assert run("feature/x", health)[0]["reason"] == "backend code is current"
    health["responses"] = [OSError("down")]
    result, calls = run("main", health)
    assert calls == [] and result["reason"].startswith("backend health unreachable")


def test_failed_restart_is_reported(health):
    systemctl = Systemctl(returncode=1)
    result = rcs.restart_if_stale(systemctl=systemctl, branch=lambda: "main", env={}, wait_seconds=5)
    assert result["action"] == "restart_failed" and result["returncode"] == 1


def test_runtime_branches_override():
    assert rcs.runtime_branches({}) == ("main",)
    assert rcs.runtime_branches({"TTROS_RUNTIME_BRANCHES": "main, release/2026 ,"}) == ("main", "release/2026")


def test_current_branch_reads_real_git_state(tmp_path):
    repo = tmp_path / "repo"
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    repo.mkdir()
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    assert rcs.current_branch(repo) == "main"
    git("checkout", "-q", "-b", "feature/studio")
    assert rcs.current_branch(repo) == "feature/studio"
    git("checkout", "-q", "--detach")
    assert rcs.current_branch(repo) is None
    assert rcs.current_branch(tmp_path / "missing") is None


def test_cli_exit_codes(monkeypatch, capsys):
    for action, code in (("deferred", 0), ("would_restart", 0), ("none", 0), ("restart_failed", 1)):
        monkeypatch.setattr(rcs, "restart_if_stale", lambda **kw: {"action": action})
        assert rcs.main(["--restart-if-stale"]) == code
    seen = {}
    monkeypatch.setattr(rcs, "restart_if_stale", lambda **kw: seen.update(kw) or {"action": "none"})
    rcs.main(["--restart-if-stale", "--dry-run", "--release"])
    assert seen == {"release": True, "dry_run": True}
