"""Plans: a department coordinator as a versioned file, run as code.

A plan in ``agents/plans/<name>.json`` is a small DAG of agent calls. The plan
runner resolves each step's input from the plan input and earlier step outputs,
runs independent steps in parallel inside the one job, journals completed steps
and stops at the first step that does not return ``ok``. It makes no model call
of its own.

Step input values may be whole-value references:
  "$input.<field>"                      a field of the plan input
  "$steps.<id>.output[.<field>...]"     an earlier step's output
  "$steps.<id>.artifacts"               an earlier step's artifact list
A field name applied to a list plucks that field from every row, so
"$steps.extract.output.sources.ref" is the list of the sources' refs.
Anything else is passed through as a literal.

A plan may also be handed over as an object instead of a file reference: that
is how a plan emitted by the planner agent runs. It passes the same static
checks as a file and is run the same way.

Revisit: when the plan file shape or the journal contract changes. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .journal import StepJournal
from .package import PackageError, load_package, repo_root
from .runtime import RunEnv, _schema_error, add_usage, canonical_sha256, empty_usage, run_agent

PLANS_DIR = Path("agents") / "plans"
PLAN_REF_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?:@(?P<version>\d+\.\d+\.\d+))?$")
STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_STEPS = 12
MAX_PARALLEL = 3


class PlanError(RuntimeError):
    """The plan file is missing or not runnable as written."""


def load_plan(ref: str, root: Path | None = None) -> dict:
    """Load a plan file and statically check it."""
    root = repo_root(root)
    match = PLAN_REF_RE.match(str(ref or "").strip())
    if not match:
        raise PlanError(f"not a plan reference: {ref!r}")
    name, version = match.group("name"), match.group("version")
    path = root / PLANS_DIR / f"{name}.json"
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanError(f"plan is not installed: {name}") from exc
    except ValueError as exc:
        raise PlanError(f"plan {name} is not valid JSON: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("plan") != name:
        raise PlanError(f"plan {name}: file does not declare plan {name!r}")
    if version is not None and version != plan.get("version"):
        raise PlanError(f"plan {name}: version {version} requested, {plan.get('version')} installed")
    return check_plan(plan, root)


def check_plan(plan: Any, root: Path | None = None) -> dict:
    """Statically check a plan object: ids, dependencies, agents, call rights, budget.

    Used for plan files and for a plan the planner agent emitted; neither runs unchecked.
    """
    root = repo_root(root)
    if not isinstance(plan, dict) or not PLAN_REF_RE.match(str(plan.get("plan") or "")) or "@" in str(plan.get("plan")):
        raise PlanError("a plan needs a name of the form department.name")
    plan = dict(plan)
    name = plan["plan"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(plan.get("version") or "")):
        raise PlanError(f"plan {name}: version is not semantic")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise PlanError(f"plan {name}: needs 1 to {MAX_STEPS} steps")
    seen: dict[str, dict] = {}
    for step in steps:
        step_id = str((step or {}).get("id") or "")
        if not STEP_ID_RE.match(step_id) or step_id in seen:
            raise PlanError(f"plan {name}: step id missing, malformed or repeated: {step_id!r}")
        needs = step.get("needs") or []
        if any(need not in seen for need in needs):
            # Steps are listed in dependency order, which also rules out cycles.
            raise PlanError(f"plan {name}: step {step_id} needs a step not defined before it")
        if not isinstance(step.get("input"), dict):
            raise PlanError(f"plan {name}: step {step_id} has no input mapping")
        for value in _references(step["input"]):
            target = value.split(".")[1] if value.startswith("$steps.") else ""
            if target and target not in needs:
                raise PlanError(f"plan {name}: step {step_id} reads {target} without needing it")
        try:
            package = load_package(str(step.get("agent") or ""), root)
        except PackageError as exc:
            raise PlanError(f"plan {name}: step {step_id}: {exc}") from exc
        from .runtime import _callable_by

        if not _callable_by(package, name):
            raise PlanError(f"plan {name}: step {step_id}: {package.ref} is not callable by this plan")
        seen[step_id] = step
    if not isinstance(plan.get("input_schema"), dict):
        raise PlanError(f"plan {name}: input_schema is required")
    budget = plan.get("budget") or {}
    if not all(isinstance(budget.get(key), int) and budget[key] >= 0 for key in ("tokens", "model_calls", "seconds")):
        raise PlanError(f"plan {name}: budget needs integer tokens, model_calls and seconds")
    plan["ref"] = f"{name}@{plan['version']}"
    return plan


def _references(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("$input.", "$steps.")) else []
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _references(item)]
    return []


def _resolve(value: Any, plan_input: dict, results: dict[str, dict]) -> Any:
    if isinstance(value, dict):
        resolved = {key: _resolve(item, plan_input, results) for key, item in value.items()}
        # An optional plan input that was not supplied is left out, not passed as null.
        return {key: item for key, item in resolved.items()
                if not (item is None and isinstance(value[key], str) and value[key].startswith("$input."))}
    if isinstance(value, list):
        return [_resolve(item, plan_input, results) for item in value]
    if not isinstance(value, str) or not value.startswith(("$input.", "$steps.")):
        return value
    parts = value[1:].split(".")
    current: Any = plan_input if parts[0] == "input" else results.get(parts[1], {})
    for part in parts[1 if parts[0] == "input" else 2:]:
        if isinstance(current, list):
            # A field name applied to a list plucks that field from every row that has it.
            current = [row[part] for row in current if isinstance(row, dict) and part in row]
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def run_plan(plan_ref: str | dict, plan_input: dict, env: RunEnv | None = None) -> dict:
    """Run a plan file (by reference) or a plan object. The result has the agent result shape plus a ``steps`` list."""
    env = env or RunEnv()
    root = env.resolved_root()
    started = time.monotonic()
    usage = empty_usage()
    label = str(plan_ref.get("plan") or "(unnamed plan)") if isinstance(plan_ref, dict) else str(plan_ref)
    result: dict[str, Any] = {"status": "failed", "plan": label, "output": None, "proposed_effects": [],
                              "artifacts": [], "evidence_refs": [], "usage": usage, "detail": "", "failure_class": "",
                              "steps": []}
    try:
        plan = check_plan(plan_ref, root) if isinstance(plan_ref, dict) else load_plan(plan_ref, root)
    except PlanError as exc:
        result.update(status="refused", failure_class="plan_rejected", detail=str(exc))
        return result
    result["plan"] = plan["ref"]
    problem = _schema_error(plan_input, plan["input_schema"])
    if problem:
        result.update(status="refused", failure_class="input_schema_violation", detail=problem)
        return result

    journal = StepJournal(root, env.job) if env.job else None
    replayable = journal.completed(plan["ref"]) if journal else {}
    budget = plan["budget"]
    results: dict[str, dict] = {}
    pending = list(plan["steps"])
    stopped: dict | None = None

    def execute(step: dict) -> tuple[dict, dict, bool]:
        step_input = _resolve(step["input"], plan_input, results)
        package = load_package(step["agent"], root)
        input_hash = canonical_sha256(step_input)
        prior = replayable.get(step["id"])
        if (prior and prior.get("input_sha256") == input_hash and prior.get("agent") == package.ref
                and prior.get("package_sha256") == package.content_sha256):
            return step, prior["result"], True
        outcome = run_agent(package.ref, step_input, env, caller=plan["plan"], step=step["id"])
        if journal and outcome["status"] == "ok":
            journal.append(plan_ref=plan["ref"], step=step["id"], agent=package.ref,
                           package_sha256=package.content_sha256, input_sha256=input_hash, result=outcome)
        return step, outcome, False

    while pending and stopped is None:
        wave = [step for step in pending if all(need in results for need in step.get("needs") or [])]
        if usage["total_tokens"] >= budget["tokens"] > 0 and any(
                load_package(step["agent"], root).kind != "tool" for step in wave):
            stopped = {"status": "failed", "failure_class": "plan_budget_tokens",
                       "detail": f"plan token budget {budget['tokens']} is spent"}
            break
        if time.monotonic() - started > budget["seconds"]:
            stopped = {"status": "failed", "failure_class": "plan_budget_seconds",
                       "detail": f"plan time budget {budget['seconds']}s is spent"}
            break
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(wave))) as pool:
            outcomes = list(pool.map(execute, wave))
        for step, outcome, replayed in outcomes:
            pending.remove(step)
            results[step["id"]] = outcome
            if not replayed:
                add_usage(usage, outcome["usage"])
            result["steps"].append({
                "id": step["id"], "agent": outcome.get("agent") or step["agent"], "status": outcome["status"],
                "replayed": replayed, "execution_identity": outcome.get("execution_identity"), "failure_class": outcome.get("failure_class") or "",
                "usage": {key: outcome["usage"].get(key) for key in ("model_calls", "total_tokens", "exact", "elapsed_seconds")},
            })
            result["artifacts"].extend(outcome.get("artifacts") or [])
            result["evidence_refs"].extend(outcome.get("evidence_refs") or [])
            result["proposed_effects"].extend(outcome.get("proposed_effects") or [])
            if outcome["status"] != "ok" and stopped is None:
                stopped = {"status": outcome["status"], "failure_class": outcome.get("failure_class") or "step_not_ok",
                           "detail": f"step {step['id']}: {outcome.get('detail') or outcome['status']}",
                           "output": outcome.get("output") if outcome["status"] == "needs_input" else None}

    usage["elapsed_seconds"] = round(time.monotonic() - started, 3)
    usage["over_budget"] = [name for name, over in (
        ("tokens", usage["total_tokens"] > budget["tokens"]),
        ("model_calls", usage["model_calls"] > budget["model_calls"]),
        ("seconds", usage["elapsed_seconds"] > budget["seconds"]),
    ) if over]
    if stopped is not None:
        result.update(stopped)
        result["not_run"] = [step["id"] for step in pending]
        return result
    result.update(status="ok", output=_resolve(plan.get("output") or {}, plan_input, results))
    return result
