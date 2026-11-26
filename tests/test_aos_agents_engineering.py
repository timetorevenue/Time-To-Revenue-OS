"""Prove the Factory engineering steps using real Git and a scripted ModelPort."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from aos_agents import engineering, factory, registry  # noqa: E402

NAME = "marketing.landing_brief"
SPEC = factory.SPECS_DIR / NAME
PACKAGE = Path("agents/packages") / NAME


@pytest.fixture
def repo(tmp_path):
    def copy(relative):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, dest)

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    for relative in ("agents/tap.schema.json", "agents/marketing.card.md", "tools/aos_product_launch.py",
                     "tools/aos_website_manager.py", engineering.RUNTIME):
        copy(relative)
    shutil.copytree(ROOT / "agents/packages/marketing.post_check", tmp_path / "agents/packages/marketing.post_check",
                    ignore=shutil.ignore_patterns("__pycache__"))
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Forge Test", "-c", "user.email=forge@example.invalid", "commit", "-qm", "baseline")
    shutil.copytree(ROOT / SPEC, tmp_path / SPEC, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


class Port:
    def __init__(self, decision="new", blocker=False, edit=None):
        self.decision, self.blocker, self.edit = decision, blocker, edit
        self.calls = []

    def __call__(self, prompt, **kwargs):
        phase = kwargs["agent"].rsplit(".", 1)[1]
        assert kwargs["backend"] == "exec"
        assert "session" not in kwargs
        self.calls.append((phase, json.loads(prompt)))
        if phase == "assessment":
            body = {"decision": self.decision,
                    "reuse_ref": "marketing.post_check@0.1.0" if self.decision == "reuse" else "",
                    "rationale": "Referenced component analysis", "implementation": "Use TAP", "verify": "Run cases"}
        else:
            if self.edit:
                self.edit()
            body = {"checks": dict.fromkeys(engineering.CHECKS, "checked"),
                    "findings": ([{"severity": "blocking", "path": (SPEC / "handler.py").as_posix(),
                                   "issue": "A reproducible functional defect"}] if self.blocker else [])}
        return {"status": "ok", "json": body, "session_id": phase,
                "usage": {"available": True, "input_tokens": 100, "output_tokens": 20}}


def test_build_admits_a_candidate_after_separate_review_and_replays(repo):
    dep = repo / "tools/aos_product_launch.py"
    with dep.open("a") as stream:
        stream.write("\n# Related working-tree change\n")
    port = Port()
    report = factory.build(NAME, repo, live=True, modelport=port)
    assert report["status"] == "candidate", report["steps"]
    assert [phase for phase, _ in port.calls] == ["assessment", "review"]
    design, review = [payload for _, payload in port.calls]
    assert design["components"][0]["ref"] == "marketing.post_check@0.1.0"
    assert review["requirement"] == design["requirement"]
    assert "Referenced component analysis" not in json.dumps(review)
    assert "diff --git a/" in review["git_diff"]
    assert "Related working-tree change" in review["git_diff"]
    assert "new file" in review["generated_manifest_diff"]
    assert review["evaluation"]["failed_cases"] == {}
    assert report["tokens"]["spent"] == 240
    assert factory.verify(NAME, repo)["pass"]
    replay = factory.build(NAME, repo)
    assert replay["status"] == "candidate", replay["steps"]
    assert replay["tokens"]["live_calls"] == 0
    assert replay["engineering"]["review"] == report["engineering"]["review"]


def test_reuse_stops_before_scaffold(repo):
    report = factory.build(NAME, repo, live=True, modelport=Port(decision="reuse"))
    assert report["status"] == "reused"
    assert not (repo / PACKAGE).exists()
    assert NAME not in registry.load(repo)["agents"]


def test_blocker_stops_registration_and_offline_cannot_bypass(repo):
    report = factory.build(NAME, repo, live=True, modelport=Port(blocker=True))
    assert report["failed_step"] == "review"
    assert NAME not in registry.load(repo)["agents"]
    assert factory.build(NAME, repo)["failed_step"] == "assessment"


def test_review_detects_an_edit_during_the_model_call(repo):
    def edit():
        with (repo / SPEC / "handler.py").open("a") as stream:
            stream.write("\n# Changed while the reviewer ran\n")

    report = factory.build(NAME, repo, live=True, modelport=Port(edit=edit))
    assert report["failed_step"] == "review"
    assert "changed during the build" in str(report["steps"][-1])
    assert NAME not in registry.load(repo)["agents"]


def test_protected_dependency_and_budget_refused_before_model(repo):
    port = Port()
    assert factory.build(NAME, repo, live=True, modelport=port, token_budget=10)["failed_step"] == "assessment"
    path = repo / SPEC / "spec.toml"
    path.write_text(path.read_text().replace("tools/aos_product_launch.py", ".env"))
    report = factory.build(NAME, repo, live=True, modelport=port)
    assert report["failed_step"] == "assessment"
    assert "scoped TTROS source paths" in str(report["steps"][-1])
    assert port.calls == []
