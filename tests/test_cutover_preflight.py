"""Cutover preflight on a disposable Git repository: read-only proofs, then the documented commands."""
import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("cutover_preflight", Path(__file__).resolve().parents[1] / "scripts/cutover/preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, message, files):
    for name, text in files.items():
        path = repo / name
        if text is None:
            git(repo, "rm", "-q", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(repo, "add", name)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "live"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.org")
    git(repo, "config", "user.name", "Fixture")
    base = commit(repo, "base", {"backend.py": "a\nb\nc\nd\ne\n", "canary.py": "old\n", "policy.py": "p\n",
                                 "receipt.md": "r\n", "north/bot.py": "bot\n"})
    git(repo, "checkout", "-qb", "wip")
    wip = commit(repo, "wip", {"backend.py": "a\nb\nc\nd\ne2\n", "canary.py": "wip\n", "policy.py": "p-wip\n"})
    git(repo, "checkout", "-q", "main")
    main_head = commit(repo, "main moved", {"backend.py": "a0\nb\nc\nd\ne\n"})       # WIP delta must 3-way onto this
    git(repo, "checkout", "-qb", "candidate")
    commit(repo, "candidate", {"backend.py": "new backend\n", "canary.py": None, "added.py": "x\n"})
    git(repo, "checkout", "-q", "main")
    (repo / "backend.py").write_text("a0\nb\nc\nd\ne2\n")      # main HEAD + WIP delta, as production runs it
    (repo / "canary.py").write_text("wip\n")
    (repo / "policy.py").write_text("p-wip\n")
    (repo / "receipt.md").write_text("r\nindependent addendum\n")
    (repo / "notes.txt").write_text("untracked business evidence\n")
    for name, value in (("EXPECTED_MAIN", main_head), ("WIP_COMMIT", wip), ("OWNED_ADDENDUM", "receipt.md"),
                        ("THREE_WAY", "backend.py")):
        monkeypatch.setattr(preflight, name, value)
    return repo


def test_pre_proves_exact_wip_then_documented_commands_reach_a_clean_post(repo):
    index_before = (repo / ".git/index").read_bytes()
    report = preflight.pre(repo, "candidate", check_units=False)
    assert report["ok"], report["problems"]
    assert report["restore_exactly"] == ["backend.py", "canary.py", "policy.py"]
    assert report["kept_dirty"] == ["receipt.md"]
    assert (repo / ".git/index").read_bytes() == index_before and git(repo, "rev-parse", "HEAD") == preflight.EXPECTED_MAIN
    addendum = hashlib.sha256((repo / "receipt.md").read_bytes()).hexdigest()
    for command in report["next_commands"]:
        subprocess.run(command.split(), check=True, capture_output=True)
    after = preflight.post(repo, "candidate", addendum_sha256=addendum, min_untracked=report["untracked_count"])
    assert after["ok"], after["problems"]
    assert (repo / "receipt.md").read_text().endswith("independent addendum\n")
    assert (repo / "notes.txt").exists() and (repo / "north/bot.py").read_text() == "bot\n"


@pytest.mark.parametrize("damage, expected", [
    (lambda r: (r / "canary.py").write_text("someone else's edit\n"), "work tree differs"),
    (lambda r: (r / "added.py").write_text("untracked\n"), "untracked path would be overwritten: added.py"),
    (lambda r: commit(r, "unexpected", {"other.py": "z\n"}), "main HEAD is"),
])
def test_pre_refuses_unknown_owner_edits_collisions_and_moved_head(repo, damage, expected):
    damage(repo)
    report = preflight.pre(repo, "candidate", check_units=False)
    assert not report["ok"] and report["next_commands"] == []
    assert any(expected in problem for problem in report["problems"]), report["problems"]


def test_post_detects_a_changed_addendum(repo):
    report = preflight.pre(repo, "candidate", check_units=False)
    for command in report["next_commands"]:
        subprocess.run(command.split(), check=True, capture_output=True)
    after = preflight.post(repo, "candidate", addendum_sha256="0" * 64, min_untracked=0)
    assert not after["ok"] and "addendum changed" in after["problems"][0]
