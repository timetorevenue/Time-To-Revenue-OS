"""SYNTHETIC M7 fixtures: a temporary repo root and runtime shaped like the real M5 evidence.

Every job, receipt and ledger row written here is invented for offline tests and the offline
demonstration. None of it is genuine canary evidence, and it is only ever written under a
temporary directory. The shapes copy the real AOS-2026-10002 job record, receipt and ledger rows.

Revisit: when the job record, receipt or ledger row shape changes. · Last touched: 2026-11-17.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CANARY_FOUR = ("shared.planner", "shared.brain_retrieve", "knowledge.source_classify", "knowledge.filing_proposal")
SOURCE_REF = "business_brain:sources/fixture/synthetic-source.md"
SYNTHETIC = "SYNTHETIC TEST CASE: not genuine canary evidence"


FIXTURE_COMMIT_DATE = "2026-11-16T12:00:00+00:00"   # before every synthetic job (2026-11-17)


def git(root: Path, *args: str, date: str = FIXTURE_COMMIT_DATE) -> str:
    """Run git in the fixture repository only, with a fixed identity and date and no hooks or signing."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "m7-fixture", "GIT_AUTHOR_EMAIL": "fixture@invalid",
           "GIT_COMMITTER_NAME": "m7-fixture", "GIT_COMMITTER_EMAIL": "fixture@invalid",
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    hooks = root.parent / "no-hooks"
    hooks.mkdir(exist_ok=True)
    done = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={hooks}", *args],
                          env=env, capture_output=True, text=True, timeout=60, check=True)
    return done.stdout.strip()


def commit(root: Path, message: str, *, date: str = FIXTURE_COMMIT_DATE, paths=("agents", "queue")) -> str:
    git(root, "add", *paths)
    git(root, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(root, "rev-parse", "HEAD")


def make_root(base: Path) -> tuple[Path, Path]:
    """Copy agents/ and the route table into a fresh git repo ``base/repo``; create an empty ``base/runtime``."""
    root, runtime = Path(base) / "repo", Path(base) / "runtime"
    shutil.copytree(REPO / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__", "dist"))
    (root / "queue").mkdir(parents=True)
    shutil.copy(REPO / "queue" / "model_routes.json", root / "queue" / "model_routes.json")
    for folder in ("jobs", "receipts"):
        (runtime / "queue" / folder).mkdir(parents=True)
    (runtime / "queue" / "token_ledger.jsonl").write_text("")
    write_acceptances(root, [])
    git(root, "init", "-q")
    commit(root, "SYNTHETIC fixture: production state the synthetic canaries ran on")
    return root, runtime


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


PRODUCTION_TRANSPORT = {"executable": "/home/operator/.local/npm/bin/codex", "production_binary": True}


def synthetic_output(package, label: str, run: int) -> dict:
    """A schema-valid SYNTHETIC output whose declared decision depends only on ``label``.

    Free text (summaries, reasons, ordering) varies with ``run``, so repeats of one label agree
    on the verdict while their whole-output hashes differ, as real model repeats do."""
    tag = hashlib.sha256(label.encode()).hexdigest()[:8]
    words = ("Synthetic fixture source, described one way.", "A synthetic fixture source, described another way.",
             "The synthetic fixture source, in a third wording.")[run % 3]
    if package.name == "knowledge.source_classify":
        kinds = package.output_schema["properties"]["kind"]["enum"]
        return {"kind": kinds[int(tag, 16) % len(kinds)], "summary": words, "entities": [], "date": "",
                "sensitivity": "normal", "scope_hint": f"client:fixture-{tag}", "confidence": ("high", "medium")[run % 2],
                "caveats": [f"synthetic caveat {run}"], "source": {"ref": SOURCE_REF, "tokens": 100 + run}}
    if package.name == "shared.planner":
        return {"decision": "plan", "summary": words,
                "tasks": [{"task": "file_source", "records": [f"business_brain:sources/fixture/{tag}.md"], "steps": ["classify", "file"]}],
                "plan": {"plan": "shared.planner", "version": "0.1.0", "steps": [{"id": "a"}, {"id": "b"}],
                         "input_schema": {}, "budget": {}, "output": "b"}, "plan_input": {}}
    if package.name == "shared.brain_retrieve":
        refs = [f"business_brain:sources/fixture/{tag}.md", "business_brain:HOME.md"]
        refs = refs if run % 2 == 0 else refs[::-1]
        return {"refs": refs, "sources": [{"id": f"S{i}", "ref": ref, "tokens": 10 + run, "sha256": "0" * 64}
                                          for i, ref in enumerate(refs)], "total_tokens": 20 + 2 * run}
    if package.name == "knowledge.filing_proposal":
        return {"action": "file", "area": "sources/fixture", "slug": f"fixture-{tag}",
                "target": f"business_brain:sources/fixture/fixture-{tag}.md",
                "frontmatter": {"type": "source", "source": SOURCE_REF, "date": "", "entities": [], "sensitivity": "normal"},
                "reasons": [f"synthetic reason, wording {run}"], "proposes_write": True}
    raise ValueError(f"no synthetic output for {package.name}")


def write_reliability(root: Path, name: str, *, repack: bool = True, **override) -> Path:
    """SYNTHETIC gate-7 evidence for the package as it is on disk (labelled in ``note``, never as a flag).

    ``repeats`` (case -> verdict labels) shapes the preserved runs; every other override is written over
    the summary recomputed from that provenance (so it can contradict it, as a tampering test needs)."""
    sys.path.insert(0, str(REPO / "tools"))
    from aos_agents import evals, reliability
    from aos_agents.package import load_package
    from aos_agents.runtime import canonical_sha256

    package = load_package(name, root)
    labels = override.pop("repeats", None) or [{"case": f"case-{index}", "verdicts": ["ok", "ok", "ok"]} for index in range(5)]
    path = package.directory / "evidence" / f"{evals.binding(package)}.reliability.json"
    model = package.model.get("id") if package.kind != "tool" else None
    runs = []
    for case in labels:
        case_runs = []
        for index, label in enumerate(case.get("verdicts") or []):
            jid = f"fixture-{case['case']}-{index}"
            output = synthetic_output(package, label, index)
            att = {"agent": package.ref, "package_sha256": package.content_sha256, "binding": evals.binding(package),
                   "input_sha256": f"same-input-{case['case']}", "output_sha256": canonical_sha256(output)}
            receipt = "Execution identity: " + json.dumps(att)
            sha = hashlib.sha256(receipt.encode()).hexdigest()
            job = {"id": jid, "status": "done", "usage": {"exact": True}, "receipts": [{"sha256": sha}],
                   "result": {"steps": [{"agent": package.ref, "status": "ok", "execution_identity": att}]}}
            case_runs.append({"job": job, "receipt": receipt, "receipt_sha256": sha, "output": output,
                              "ledger": [{"item_id": jid, "actual_model": model, "provider": "openai-codex",
                                          "surface": f"modelport:codex:{package.ref}",
                                          "model_selection": {"reasoning_effort": package.model.get("effort"),
                                                              "transport": dict(PRODUCTION_TRANSPORT)}}]})
        runs.append({"case": case['case'], "runs": case_runs})

    def probe_source(status, failure="", step_status="ok"):
        text = "SYNTHETIC probe receipt"
        sha = hashlib.sha256(text.encode()).hexdigest()
        return {"call_attempts": [{"agent": package.ref, "step": "fixture", "input_sha256": "fixture", "package_sha256": package.content_sha256, "result": {"status": "started"}}],
                "receipt": text, "receipt_sha256": sha,
                "ledger": [{"event": "model_invocation", "item_id": "fixture-probe", "surface": "modelport:codex:fixture",
                            "provider": "openai-codex", "model_selection": {"transport": dict(PRODUCTION_TRANSPORT)}}],
                "job": {"id": "fixture-probe", "status": status, "failure_class": failure,
                        "receipts": [{"sha256": sha}], "result": {"steps": [{"agent": package.ref, "status": step_status, "execution_identity": {"package_sha256": package.content_sha256, "binding": evals.binding(package)}}]}}}
    checkpoint = json.dumps({"step": "fixture", "result": {"status": "ok"}})
    bundle = {"schema": reliability.SCHEMA, "cases": runs,
              "kill": {"returncode": -9, "checkpoint_before_kill": checkpoint,
                       "checkpoint_sha256": hashlib.sha256(checkpoint.encode()).hexdigest(), "source": probe_source("done")},
              "forced": {"kind": "quota", "evidence_class": "genuine_provider",
                         "source": probe_source("blocked", "quota_exhausted", "blocked")}}
    data = {"tas": "0.1", "gate": 7, "agent": package.ref, "binding": evals.binding(package),
            "package_sha256": package.content_sha256, "prompt_sha256": package.model.get("prompt_sha256") or None,
            "mode": "live" if package.kind != "tool" else "deterministic", "note": SYNTHETIC,
            **reliability.summarize(bundle, package, root)}
    data.update(override)
    data = {key: value for key, value in data.items() if value is not None or key not in ("kill_resume", "forced_failure")}
    source = path.with_suffix('.provenance.json')
    source.write_text(json.dumps(bundle))
    data['provenance'] = {"path": source.name, "sha256": reliability.digest(bundle)}
    path.write_text(json.dumps(data, indent=1) + "\n")
    if repack:   # the archive bundles evidence; package identity (content hash) does not
        from aos_agents import registry

        registry.repack(name, root, note="SYNTHETIC gate-7 evidence added")
    return path


def registry_entry(root: Path, name: str) -> dict:
    return json.loads((root / "agents" / "registry.lock.json").read_text())["agents"][name]


def claim(root: Path, name: str, **override) -> dict:
    entry = registry_entry(root, name)
    return {"version": entry["version"], "package_sha256": entry["package_sha256"], "binding": entry["binding"], **override}


def write_acceptances(root: Path, records: list[dict]) -> None:
    (root / "agents" / "canary_acceptances.json").write_text(
        json.dumps({"about": SYNTHETIC, "version": 1, "records": records}, indent=1) + "\n")


def add_acceptance(root: Path, job: dict, names, *, decision="accepted", kind="real_canary", expected_held=("BRAIN_WRITE_PROPOSAL",),
                   packages: dict | None = None, record_id: str | None = None, receipt_sha256: str | None = None,
                   provenance: str | None = "HEAD") -> dict:
    """``provenance`` is a commit ('HEAD' = the fixture repo's head) or None for a record without one."""
    path = root / "agents" / "canary_acceptances.json"
    data = json.loads(path.read_text())
    record = {"id": record_id or f"FIX-{job['id']}", "job": job["id"], "kind": kind, "decision": decision,
              "accepted_by": "fixture" if decision == "accepted" else "", "decided_at": job["finished_at"],
              "source": SYNTHETIC, "receipt_sha256": receipt_sha256 or job["receipts"][0]["sha256"],
              "expected_held": list(expected_held), "packages": packages or {name: claim(root, name) for name in names}}
    if provenance:
        record["provenance"] = {"commit": head(root) if provenance == "HEAD" else provenance, "basis": SYNTHETIC}
    data["records"].append(record)
    path.write_text(json.dumps(data, indent=1) + "\n")
    return record


def add_job(root: Path, runtime: Path, number: int, names=CANARY_FOUR, *, minute: int | None = None, status="done",
            step_status: dict | None = None, replayed=False, effects=None, scopes=("global",), ledger=True,
            effort_override: str | None = None, content_sha256: str | None = None, named_ref: str = SOURCE_REF,
            round_number=0, mark: str = "", output_label: str | None = None) -> dict:
    """Write one synthetic job record, its receipt and its ledger rows. Returns the job.

    With ``output_label`` each step's output is a ``synthetic_output`` journaled in the job's step
    journal (as the real plan runner does), and the attested output_sha256 is its real hash."""
    job_id = f"AOS-2026-{90000 + number}"
    minute = number if minute is None else minute
    created = f"2026-11-17T{minute // 60:02d}:{minute % 60:02d}:00+00:00"
    steps, by_step, rows = [], [], []
    for index, name in enumerate(names):
        entry = registry_entry(root, name)
        ref = f"{name}@{entry['version']}"
        state = (step_status or {}).get(name, "ok")
        output_sha256 = "fixture-output"
        if output_label is not None:
            sys.path.insert(0, str(REPO / "tools"))
            from aos_agents.journal import StepJournal
            from aos_agents.package import load_package
            from aos_agents.runtime import canonical_sha256

            output = synthetic_output(load_package(name, root), output_label, number)
            output_sha256 = canonical_sha256(output)
        identity = {"agent": ref, "package_sha256": entry["package_sha256"], "binding": entry["binding"],
                    "input_sha256": "fixture-input", "output_sha256": output_sha256}
        if output_label is not None:
            StepJournal(runtime, job_id).append(plan_ref="fixture-plan", step=f"s{index}", agent=ref,
                                                package_sha256=entry["package_sha256"], input_sha256="fixture-input",
                                                result={"status": "ok", "output": output, "execution_identity": identity})
        steps.append({"agent": ref, "failure_class": "" if state == "ok" else "fixture_failure", "id": f"s{index}",
                      "replayed": replayed, "status": state, "execution_identity": identity})
        if entry["binding"] != "tool":
            model, effort = entry["binding"].split(".")[:2]
            tokens = 7000 + number * 10 + index
            invocation = f"modelport-fixture-{number:05d}-{index}"
            by_step.append({"agent": ref, "exact": True, "model": model, "status": state, "step": f"s{index}", "tokens": tokens})
            rows.append({"event": "model_invocation", "item_id": job_id, "invocation_id": invocation,
                         "scope_key": f"session:{invocation}", "surface": f"modelport:codex:{ref}", "actual_model": model,
                         "model_selection": {"actual_model": model, "reasoning_effort": effort_override or effort,
                                             "transport": dict(PRODUCTION_TRANSPORT)},
                         "fuse": {"invocation_tokens": tokens}, "timestamp": created.replace("+00:00", "Z"), "synthetic": True})
    total = sum(row["tokens"] for row in by_step)
    job = {
        "id": job_id, "status": status, "round": round_number, "created_at": created, "finished_at": created,
        "failure_class": "", "detail": SYNTHETIC, "request": "Synthetic canary request", "requested_by": "fixture",
        "executor": {"kind": "agent", "planner": "shared.planner@0.1.0",
                     "input": {"records": [{"ref": named_ref, "about": "synthetic"}] if named_ref else [], "request": "x"}},
        "brain_context_used": [{"client_scope": scope, "path": SOURCE_REF,
                                "content_sha256": content_sha256 or hashlib.sha256(scope.encode()).hexdigest()} for scope in scopes],
        "effects": effects if effects is not None else [{"action": "BRAIN_WRITE_PROPOSAL", "effect": "brain.write_proposal",
                                                          "status": "held", "target": "business_brain:fixture.md"}]
        if "knowledge.filing_proposal" in names or effects is not None else [],
        "result": {"steps": steps, "summary": SYNTHETIC},
        "usage": {"exact": True, "total_tokens": total, "model_calls": len(by_step), "by_step": by_step,
                  "invocation_ids": [row["invocation_id"] for row in rows]},
    }
    if mark:
        job[mark] = True
    receipt = (f"{'PASS' if status == 'done' else 'NEEDS ATTENTION'}\nJob: {job_id}\nStatus: {status}\nRound: {round_number}\n"
               f"Request: Synthetic canary request\n{SYNTHETIC}\n\nBrain: {SOURCE_REF} scope=global\n"
               f"Token usage: {total} known across {len(rows)} invocation(s) (exact)\n")
    receipt += "".join("Execution identity: " + json.dumps(step["execution_identity"], sort_keys=True) + "\n" for step in steps)
    relative = f"queue/receipts/{job_id}.md"
    (runtime / relative).write_text(receipt)
    job["receipts"] = [{"path": relative, "round": 0, "status": status, "created_at": created,
                        "sha256": hashlib.sha256(receipt.encode()).hexdigest()}]
    (runtime / "queue" / "jobs" / f"{job_id}.json").write_text(json.dumps(job, indent=1, sort_keys=True) + "\n")
    if ledger:
        with (runtime / "queue" / "token_ledger.jsonl").open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    return job


def clean_canaries(root: Path, runtime: Path, count: int, names=CANARY_FOUR, *, start: int = 1) -> list[dict]:
    """``count`` synthetic jobs, each recorded as an accepted canary for ``names``."""
    jobs = []
    for number in range(start, start + count):
        job = add_job(root, runtime, number, names)
        add_acceptance(root, job, names)
        jobs.append(job)
    return jobs
