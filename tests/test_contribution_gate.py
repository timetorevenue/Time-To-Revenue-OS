import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import contribution_gate as gate  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin"})


def contribution(tmp_path, files):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("base\n")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "pr")
    for rel, data in files.items():
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "pr")
    return gate.check("main", "pr", str(tmp_path))


def reasons(result):
    return {(r["path"], r["reason"]) for r in result["blocked"]}


def test_ordinary_change_passes(tmp_path):
    r = contribution(tmp_path, {"tools/helper.py": "def f():\n    return 1\n", "tests/test_helper.py": "def test_f():\n    assert True\n"})
    assert r["ok"] and not r["blocked"] and not r["review"]


def test_secrets_and_private_identifiers_are_blocked(tmp_path):
    r = contribution(tmp_path, {"tools/x.py": 'KEY = "sk-' + "a" * 30 + '"\nP = "/home/alice/work"\nM = "alice@' + "corp" + '.com"\n'})
    assert not r["ok"]
    assert {x[1] for x in reasons(r)} >= {"api-key", "host-path", "real-email"}


def test_example_domains_and_placeholder_paths_are_allowed(tmp_path):
    r = contribution(tmp_path, {"docs/a.md": "mail owner@example.org and see /home/runner/work\n"})
    assert r["ok"]


def test_release_state_ci_and_credential_files_are_blocked(tmp_path):
    r = contribution(tmp_path, {"agents/registry.lock.json": "{}", ".github/workflows/x.yml": "on: push\n", ".env": "A=1\n", "tools/contribution_gate.py": "# weakened\n"})
    assert {p for p, _ in reasons(r)} == {"agents/registry.lock.json", ".github/workflows/x.yml", ".env", "tools/contribution_gate.py"}


def test_binary_files_are_blocked_and_permission_code_is_flagged(tmp_path):
    r = contribution(tmp_path, {"assets/x.bin": b"\x00\x01\x02", "rules/policy.md": "x\n", "connectors/a.py": "x = 1\n"})
    assert ("assets/x.bin", "binary-file") in reasons(r)
    assert {x["path"] for x in r["review"]} == {"rules/policy.md", "connectors/a.py"}
