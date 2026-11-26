"""One disposable CRM + website runtime, real HTTP routes, no lifespan or listener.

The JSON-lines adapter is test-only. RUN_FIXTURE executes only its synthetic website
job via the canonical executor. It never launches a real coding provider/connector.
"""
import contextlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.command_centre_fixture import NOW, crm_environment, git, make_site_repo, write_website_registry
import aos_crm
import aos_jobs
import aos_website as web
import main as backend
from fastapi.testclient import TestClient

HEADLINE = "Synthetic Company Command Centre website change."
CONTACT = "contact:2026-11-fixture-alpha-ada-example"


@contextlib.contextmanager
def company_fixture():
    with tempfile.TemporaryDirectory(prefix="ttros-company-ops-") as folder:
        root = Path(folder) / "runtime"
        root.mkdir()
        repo = make_site_repo(Path(folder) / "synthetic-site")
        (repo / ".gitignore").write_text("build/\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-qm", "Ignore synthetic build output")
        original_head = git(repo, "rev-parse", "HEAD")
        with crm_environment(root) as paths, patch.object(backend, "BASE_DIR", root), \
                patch.object(backend, "RUNNER", None), patch.object(backend, "DAVID", None), \
                patch.object(aos_crm, "_now", return_value=NOW), \
                patch.object(backend, "start_david", side_effect=AssertionError("runtime startup forbidden")), \
                patch.object(web, "codex_worker", side_effect=AssertionError("real model forbidden")):
            write_website_registry(root, repo, required_assets=["site/styles.css"],
                preview={"dir": "build", "entry": "index.html", "command": ["{python}", "-c",
                    "import shutil; shutil.copytree('site', 'build', dirs_exist_ok=True)"]})
            client = TestClient(backend.app)  # no lifespan: no production workers or models
            inputs = {"project": "fixture-site", "repository": str(repo), "client_scope": "global",
                      "request": f"Change the homepage headline to '{HEADLINE}'", "branch": "feature/company-ops-site",
                      "affected_paths": ["site/index.html"], "source_references": ["AGENTS.md", "site/index.html"],
                      "acceptance_checks": [{"path": "site/index.html", "contains": HEADLINE}]}
            def brain_read(scope, pointer):
                # Use exactly the fixture scope contract; never open canonical Brain content.
                import business_brain_scope
                checked = business_brain_scope.load_registry().validate_brain_pointer(scope, pointer)
                return {"content": (paths["vault"] / checked.removeprefix("business_brain:")).read_text()}
            def execute(job_id):
                job = aos_jobs.load(root, job_id)
                assert job["executor"]["kind"] == "website"
                assert job["executor"]["project"] == "fixture-site"
                return aos_jobs.execute(root, job_id, brain_read=brain_read)
            yield SimpleNamespace(root=root, repo=repo, client=client, inputs=inputs, execute=execute, original_head=original_head)
            assert git(repo, "rev-parse", "HEAD") == original_head
            assert not git(repo, "status", "--porcelain")
            assert not git(repo, "remote")


def main():
    with company_fixture() as fixture:
        for line in sys.stdin:
            request = json.loads(line)
            if request["method"] == "META":
                reply = {"status": 200, "body": {"website": fixture.inputs, "contact": CONTACT}}
            elif request["method"] == "RUN_FIXTURE":
                job = fixture.execute(request["body"]["job_id"])
                reply = {"status": 200, "body": {"id": job["id"], "status": job["status"]}}
            elif request["method"] == "TAMPER_FIXTURE_PREVIEW":
                job = aos_jobs.load(fixture.root, request["body"]["job_id"])
                web.preview_file(fixture.root, job, "index.html").write_text("synthetic tamper for integrity test")
                reply = {"status": 200, "body": {"tampered": True}}
            else:
                assert request["path"].startswith(("/api/crm/", "/api/website/", "/api/david/jobs", "/api/health"))
                response = fixture.client.request(request["method"], request["path"], json=request.get("body"))
                is_json = response.headers.get("content-type", "").startswith("application/json")
                reply = {"status": response.status_code, "body": response.json() if is_json else response.text,
                         "headers": dict(response.headers)}
            print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
