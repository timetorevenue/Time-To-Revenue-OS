"""Isolated fixtures for the Company Command Centre (CRM & Sales, Websites & Digital).

Everything here is invented test data, labelled as such, in a temporary root: a prospect
ledger, a Business Brain vault with prospect pages, a client scope registry, and a
disposable static website repository with its own tests. No production file is read
or written, and no model, connector or network is used.

Revisit: when the prospect ledger schema, the scope registry or aos_website's registry shape changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tools", ROOT / "dashboard" / "backend", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import business_brain  # noqa: E402
import business_brain_scope  # noqa: E402
from tests.business_brain_test_support import registry_data, write_registry  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 17, 0, tzinfo=dt.timezone.utc)
FIXTURE_NOTE = "FIXTURE — invented test data, not a real person or company."

PROSPECTS = [
    # (prospect_id, person, company, tier, score, signal_date, status_date)
    ("2026-11-fixture-alpha-ada-example", "Ada Example", "Fixture Alpha Ltd", "B", 70, "2026-10-20", "2026-10-25"),
    ("2026-11-fixture-beta-ben-example", "Ben Example", "Fixture Beta Inc", "A", 85, "2026-07-01", "2026-07-10"),
    ("2026-11-fixture-gamma-gil-example", "Gil Example", "Fixture Gamma Co", "B", 66, "2026-11-01", "2026-11-02"),
]


def ledger_row(prospect_id, person, company, tier, score, signal_date, status_date) -> dict:
    return {
        "prospect_id": prospect_id, "name": person, "company": company, "lane": "professional_services",
        "icp_variant": "A", "signal_class": "growth_event", "signal_date": signal_date,
        "signal_source_url": f"https://example.org/fixture/{prospect_id}", "source_query": "fixture query",
        "score": score, "tier": tier, "readiness": "now", "wedge": f"{FIXTURE_NOTE} Growth signal for {company}.",
        "angle_type": "signal_reference", "first_touch_style": "email", "outreach_basis": "fixture basis",
        "status": "drafted", "status_date": status_date, "touch_count": 0, "next_touch_due": None,
        "outcome_notes": FIXTURE_NOTE, "entity_page_path": f"business_brain:prospects/{prospect_id}.md",
        "do_not_contact": False,
    }


def write_crm_fixture(root: Path) -> dict:
    """Ledger + vault + registry under ``root``. Returns the paths."""
    vault = root / "brain"
    for row in PROSPECTS:
        page = vault / "prospects" / f"{row[0]}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"---\nid: {row[0]}\ntype: prospect\nstatus: drafted\n---\n# {row[1]} — {row[2]}\n\n{FIXTURE_NOTE}\n",
                        encoding="utf-8")
    (vault / "memory").mkdir(parents=True, exist_ok=True)
    (vault / "memory" / "website_and_content.md").write_text(
        f"---\nid: website-fixture\ntype: knowledge\n---\n# Website voice (fixture)\n\n{FIXTURE_NOTE} Plain language; no invented prices.\n",
        encoding="utf-8")
    (root / "queue").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "queue" / "prospects_schema.json", root / "queue" / "prospects_schema.json")
    (root / "queue" / "prospects.jsonl").write_text(
        "".join(json.dumps(ledger_row(*row), sort_keys=True) + "\n" for row in PROSPECTS), encoding="utf-8")
    data = registry_data()
    data["scopes"]["global"]["brain_pointer_prefixes"] = ["business_brain:prospects/"]
    data["scopes"]["global"]["brain_pointers"].append("business_brain:memory/website_and_content.md")
    data["denied_brain_pointers"].append("business_brain:memory/example_dealership_sales_coach.md")
    registry_path = root / "context" / "client_scope_registry.json"
    write_registry(registry_path, data)
    return {"vault": vault, "registry_path": registry_path}


@contextlib.contextmanager
def crm_environment(root: Path):
    """Point the Brain root and the default scope registry at the fixture."""
    paths = write_crm_fixture(root)
    with patch.object(business_brain, "BUSINESS_BRAIN_ROOT", paths["vault"]), \
            patch.object(business_brain_scope, "DEFAULT_REGISTRY_PATH", paths["registry_path"]):
        yield paths


# -- website fixture -------------------------------------------------------------------

SITE_FILES = {
    "AGENTS.md": ("# Fixture site instructions\n\n" + FIXTURE_NOTE + "\n\n- Edit only files under site/.\n"
                  "- Keep the contact link in the navigation.\n- Never invent prices or client results.\n"),
    "site/index.html": ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Fixture Co</title>"
                        "<link rel=\"stylesheet\" href=\"styles.css\"></head>\n<body>\n"
                        "<nav><a href=\"index.html\">Home</a> <a href=\"contact.html\">Contact</a></nav>\n"
                        "<h1>Make your business run better.</h1>\n<p>" + FIXTURE_NOTE + "</p>\n</body></html>\n"),
    "site/contact.html": ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Contact</title></head>"
                          "<body><h1>Contact</h1><p>" + FIXTURE_NOTE + "</p></body></html>\n"),
    "site/styles.css": "body { font-family: sans-serif; max-width: 40rem; margin: 2rem auto; }\n",
    "checks/check_site.py": (
        "import pathlib, re, sys\n"
        "site = pathlib.Path(__file__).resolve().parents[1] / 'site'\n"
        "index = (site / 'index.html').read_text()\n"
        "errors = []\n"
        "if not re.search(r'<h1>[^<]{5,120}</h1>', index): errors.append('homepage needs one h1 headline')\n"
        "if 'href=\"contact.html\"' not in index: errors.append('navigation must link to contact.html')\n"
        "for href in re.findall(r'href=\"([^\"#:]+)\"', index):\n"
        "    if not (site / href).exists(): errors.append(f'broken link {href}')\n"
        "print('\\n'.join(errors) or 'fixture site checks passed')\n"
        "sys.exit(1 if errors else 0)\n"),
}

FIXTURE_EDITS = [
    {"name": "homepage-headline", "when": r"headline to [\"'“](?P<text>[^\"'”]{3,120})[\"'”]",
     "path": "site/index.html", "replace_between": ["<h1>", "</h1>"], "text": "{text}"},
    {"name": "remove-contact-link", "when": r"remove the contact link",
     "path": "site/index.html", "replace_between": ["<nav>", "</nav>"], "text": "<a href=\"index.html\">Home</a>"},
    {"name": "deploy-workflow", "when": r"deploy(ment)? workflow",
     "path": ".github/workflows/deploy.yml", "text": "on: push\njobs: {}\n"},
]


def git(repo: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "PATH": "/usr/bin:/bin", "HOME": str(repo)}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def make_site_repo(path: Path) -> Path:
    """A disposable Git repository holding the fixture site; no remote is configured."""
    path.mkdir(parents=True)
    for relative, text in SITE_FILES.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "Fixture site")
    return path


def write_website_registry(root: Path, repo: Path, **overrides) -> Path:
    project = {
        "name": "Fixture Co website (TTROS test fixture)", "aliases": ["fixture site", "fixture co website"],
        "fixture": True, "client_scope": "global", "repository": str(repo), "base_ref": "main", "worker": "fixture",
        "icm_files": ["AGENTS.md"], "icm_brain": ["business_brain:memory/website_and_content.md"],
        "allowed_paths": ["site/*"], "forbidden_paths": ["checks/*"],
        "test_command": ["{python}", "checks/check_site.py"], "preview": {"dir": "site", "entry": "index.html"},
        "publish": {"target": "fixture-co.example (never deployed)"}, "fixture_edits": FIXTURE_EDITS,
    }
    project.update(overrides)
    data = {"version": 1, "projects": {
        "fixture-site": project,
        "unchecked-site": {"name": "A site with no checkout", "aliases": [], "repository": "", "worker": "codex"},
    }}
    path = root / "context" / "website_projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path
