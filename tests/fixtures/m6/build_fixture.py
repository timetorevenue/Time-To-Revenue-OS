"""Build the M6 fixture root: a scratch copy of agents/ plus synthetic job records.

FIXTURE, NOT LIVE EVIDENCE. The job records below are synthetic (IDs AOS-2099-*),
shaped exactly like aos_jobs records. The agent packages, golden cases and recorded
model replies are the repository's real ones. One real defect is reinstated: the
marketing.linkedin_post handler is rebuilt without the closing-question fix of
commit 544f65c (the pool patch reverse-applied), so the "active" fixture package
fails the way the live M5 run AOS-2026-9997 did. Nothing here calls a model.

Revisit: when the job record shape or the linkedin_post spec changes. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NORTH_SHORE = "client:example-dealership-sales-coach"
FIXTURE_MARKER = ".m6-fixture-root"   # = aos_agents.improve.FIXTURE_ROOT_MARKER
POST = "marketing.linkedin_post@0.1.0"
PLANNER = "shared.planner@0.1.0"
SYNTH = "research.synthesise@0.1.0"


def _job(n: int, minute: int, *, status: str, request: str, steps: list[tuple], failure_class: str = "",
         effects: list[dict] | None = None, scope: str = "") -> dict:
    job = {"id": f"AOS-2099-{n:04d}", "status": status, "created_at": f"2099-01-01T00:{minute:02d}:00+00:00",
           "requested_by": "casey", "conversation_id": "fixture", "topic": "fixture", "request": request,
           "executor": {"kind": "agent", "planner": PLANNER, "input": {"request": request}}, "failure_class": failure_class,
           "result": {"agent": f"{PLANNER} -> {len(steps)} step(s)",
                      "steps": [{"id": sid, "agent": agent, "status": st, "failure_class": fc} for sid, agent, st, fc, *_ in steps]},
           "usage": {"by_step": [{"agent": agent, "step": sid, "status": "ok", "model": "gpt-6-luna", "tokens": rest[0],
                                  "exact": rest[1] if len(rest) > 1 else True}
                                 for sid, agent, _st, _fc, *rest in steps if rest]},
           "effects": effects or [], "fixture": True}
    if scope:
        job["client_scope"] = scope
    return job


def jobs() -> list[dict]:
    plan = ("plan", PLANNER, "ok", "", 7900)
    retrieve = ("retrieve", "shared.brain_retrieve@0.1.0", "ok", "")
    post_fail = ("post", POST, "failed", "validation_failed", 8050)
    post_ok = ("post", POST, "ok", "", 8000)
    rows, n = [], 0

    def add(minute, **kw):
        nonlocal n
        n += 1
        rows.append(_job(n, minute, **kw))

    # Generation window (older). Held-out window = the newest 20% (minutes 50+).
    for minute in (1, 5, 9):
        add(minute, status="failed", failure_class="validation_failed", request="Draft a LinkedIn post from the pilot notes",
            steps=[plan, retrieve, post_fail])
    add(12, status="done", request="Draft a LinkedIn post about the gym case study", steps=[plan, retrieve, post_ok])
    for minute, text in ((14, "Reconcile this month's invoices against QuickBooks payments"),
                         (17, "Reconcile the open invoices with QuickBooks and flag mismatches"),
                         (21, "Match QuickBooks payments to invoices for October")):
        add(minute, status="refused", failure_class="request_unsupported", request=text,
            steps=[("plan", PLANNER, "refused", "request_unsupported", 7700)])
    for minute, tokens in ((24, 11900), (26, 12050), (28, 12200), (30, 11800), (32, 12100)):
        add(minute, status="done", request="Research brief: how fast must missed calls be returned",
            steps=[plan, retrieve, ("synth", SYNTH, "ok", "", tokens)])
    add(34, status="done", request="Research brief: missed-call follow-up", steps=[plan, retrieve, ("synth", SYNTH, "ok", "", 30000, False)])
    for minute in (36, 38, 40):
        add(minute, status="done", request="File the AI-SEO tutorial source",
            steps=[plan, retrieve, ("classify", "knowledge.source_classify@0.1.0", "ok", "", 8200), ("filing", "knowledge.filing_proposal@0.1.0", "ok", "")],
            effects=[{"key": f"E-fixture{minute}", "effect": "brain.write", "action": "BRAIN_WRITE_PROPOSAL", "status": "held",
                      "target": "operations/procedures/ai-seo.md", "detail": "Brain writes are always held for Casey"}])
    # Held-out window: never read by detection or generation; only confirms signals.
    add(50, status="failed", failure_class="validation_failed", request="Draft a LinkedIn post on onboarding lessons",
        steps=[plan, retrieve, post_fail])
    add(52, status="done", request="Research brief: callback speed", steps=[plan, retrieve, ("synth", SYNTH, "ok", "", 12150)])
    add(54, status="refused", failure_class="request_unsupported", request="Reconcile QuickBooks invoices for November",
        steps=[("plan", PLANNER, "refused", "request_unsupported", 7650)])
    add(56, status="done", request="Draft a LinkedIn post about trades hiring", steps=[plan, retrieve, post_ok])
    # Another client scope: must be excluded and never cited.
    for minute in (2, 6, 10, 15):
        add(minute, status="failed", failure_class="validation_failed", request="Example Dealership coaching post",
            steps=[("post", POST, "failed", "validation_failed", 8000)], scope=NORTH_SHORE)
    return rows


def build(dest: Path, repo: Path = REPO) -> dict:
    """Write the fixture root at ``dest`` (must be empty or absent). Returns what was built."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise RuntimeError(f"fixture destination is not empty: {dest}")
    if str(repo / "tools") not in sys.path:
        sys.path.insert(0, str(repo / "tools"))
    from aos_agents import evals, factory, registry

    shutil.copytree(repo / "agents", dest / "agents", ignore=shutil.ignore_patterns("__pycache__", "dist"))
    pool = dest / "agents/factory/improvements/patches/marketing.linkedin_post"
    pool.mkdir(parents=True)
    for name in ("closing-question-cta.diff", "closing-question-cta.json"):
        shutil.copy2(HERE / "patches/marketing.linkedin_post" / name, pool / name)
    # Reinstate the defect: reverse-apply the fix to the spec, rebuild the package, re-record its evidence by replay.
    done = subprocess.run(["git", "apply", "-R", "-p1", str(pool / "closing-question-cta.diff")], cwd=str(dest),
                          capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise RuntimeError(f"cannot reinstate the pre-fix handler: {done.stderr.strip()}")
    name = POST.split("@", 1)[0]
    spec = factory.load_spec(name, dest)
    pinned = registry.load(dest)["agents"][name]["built"]["candidate"]
    factory.materialise(spec, pinned, dest)
    evidence = evals.evaluate(name, dest, live=False, write=True)
    if evidence["totals"]["live_calls"]:
        raise RuntimeError("fixture evidence made a live call")
    folder = dest / "queue/jobs"
    folder.mkdir(parents=True)
    # Marks this root as a fixture: only here may the morning brief show a report built from fixture evidence.
    (dest / ".m6-fixture-root").write_text("M6 fixture root; synthetic AOS-2099-* jobs. Not live evidence.\n", encoding="utf-8")
    rows = jobs()
    for job in rows:
        (folder / f"{job['id']}.json").write_text(json.dumps(job, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"root": str(dest), "jobs": len(rows), "prefix_evidence": {"level": evidence["level"],
            "failed": [row["id"] for row in evidence["cases"] if not row["passed"]]}}


if __name__ == "__main__":
    print(json.dumps(build(Path(sys.argv[1])), indent=1))
