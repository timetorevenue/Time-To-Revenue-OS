"""Queue seam: run a Work Queue item whose executor is an agent or a plan.

An item opts in with an ``executor`` field:

    "executor": {"kind": "agent", "plan": "crm.fit_call_prep@0.1.0", "input": {...}}
    "executor": {"kind": "agent", "agent": "shared.cite_verify@0.1.0", "input": {...}}
    "executor": {"kind": "agent", "planner": "shared.planner@0.1.0", "input": {"request": "..."}}

With ``planner``, the named planner agent makes its one call and returns a plan
object; the plan is checked like a plan file and run in the same job, so a
request that crosses departments is still one queue item, one journal and one
receipt. The planner's reply is journaled, so a recovered item does not plan twice.

``dispatch`` is the single entry point for the runner seam: it runs agent items,
Factory work orders and website change requests (tools/aos_website.py) and returns
None for every other item.

``run_queue_item`` returns the same result shape the existing worker launchers
hand to the dashboard's closeout step (success, returncode, output with the
standard closeout fields, exact token usage), so the runner writes its normal
receipt, attaches it and notifies as it does for a workbench session. Claims,
leases, heartbeats, recovery, receipts and notifications stay where they are.

David's background jobs (tools/aos_jobs.py) call ``run_queue_item`` with the job as
the item; the job store owns claims, recovery, effects and notices.

Revisit: when the worker result shape or closeout fields change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .journal import StepJournal
from .package import PackageError, load_package
from .plans import run_plan
from .runtime import RunEnv, add_usage, canonical_sha256, empty_usage, run_agent

EXECUTOR_KIND = "agent"
FAILURE_CLASSES = {"needs_input": "agent_needs_input", "refused": "agent_refused", "failed": "agent_failed"}


def executor_spec(item: dict | None) -> dict | None:
    spec = (item or {}).get("executor")
    if not isinstance(spec, dict) or spec.get("kind") != EXECUTOR_KIND:
        return None
    return spec


def is_agent_item(item: dict | None) -> bool:
    return executor_spec(item) is not None


def dispatch(root: Path, item: dict, **kwargs: Any) -> dict | None:
    """Run the item if its executor is an agent, a plan, a planner or a Factory work order; else None.

    Kept for callers holding a whole item; David's jobs call ``run_queue_item`` directly.
    Items without an ``executor`` field are never affected.
    """
    if is_agent_item(item):
        return run_queue_item(root, item, **kwargs)
    import aos_website

    if aos_website.is_website_item(item):
        # A website change runs the coding workbench in an isolated worktree, not an agent package.
        return aos_website.run_work_order(root, item, brain_read=kwargs.get("brain_read"),
                                          codex_account=kwargs.get("codex_account"))
    from . import factory

    if factory.is_factory_item(item):
        allowed = {key: value for key, value in kwargs.items() if key in ("modelport", "ledger_root", "backend", "codex_account")}
        if kwargs.get("brain_read") is not None:
            allowed["scoped_reader"] = kwargs["brain_read"]
        return factory.run_work_order(root, item, **allowed)
    return None


PLANNER_STEP = "plan"


def run_planned(planner_ref: str, payload: dict, env: RunEnv) -> dict:
    """One planner call, then the plan it emitted, as one job.

    The planner agent returns ``{"plan": {...}, "plan_input": {...}}`` when it can plan the
    request; any other outcome (needs_input, refused, failed) is the job's outcome and
    nothing else runs. The emitted plan gets the same static check as a plan file.
    """
    result: dict[str, Any] = {"status": "failed", "plan": str(planner_ref), "output": None, "proposed_effects": [],
                              "artifacts": [], "evidence_refs": [], "usage": empty_usage(), "detail": "",
                              "failure_class": "", "steps": []}
    try:
        package = load_package(planner_ref, env.resolved_root())
    except PackageError as exc:
        result.update(status="refused", failure_class="package_rejected", detail=str(exc))
        return result
    if package.kind != "planner":
        result.update(status="refused", failure_class="not_a_planner", detail=f"{package.ref} is a {package.kind} agent, not a planner")
        return result
    journal = StepJournal(env.resolved_root(), env.job) if env.job else None
    journal_ref, input_hash = f"planner:{package.ref}", canonical_sha256(payload)
    prior = (journal.completed(journal_ref) if journal else {}).get(PLANNER_STEP)
    replayed = bool(prior and prior.get("input_sha256") == input_hash and prior.get("package_sha256") == package.content_sha256)
    if replayed:
        planned = prior["result"]
    else:
        planned = run_agent(package.ref, payload, env, step=PLANNER_STEP)
        if journal and planned["status"] == "ok":
            journal.append(plan_ref=journal_ref, step=PLANNER_STEP, agent=package.ref,
                           package_sha256=package.content_sha256, input_sha256=input_hash, result=planned)
        add_usage(result["usage"], planned["usage"])
    planner_step = {"id": PLANNER_STEP, "agent": package.ref, "status": planned["status"], "replayed": replayed,
                    "execution_identity": planned.get("execution_identity"), "failure_class": planned.get("failure_class") or "",
                    "usage": {key: planned["usage"].get(key) for key in ("model_calls", "total_tokens", "exact", "elapsed_seconds")}}
    output = planned.get("output") if isinstance(planned.get("output"), dict) else {}
    if planned["status"] != "ok" or not isinstance(output.get("plan"), dict):
        result.update(status=planned["status"] if planned["status"] != "ok" else "failed",
                      failure_class=planned.get("failure_class") or "planner_no_plan",
                      detail=planned.get("detail") or "the planner returned no plan",
                      output=output if planned["status"] == "needs_input" else None, steps=[planner_step])
        return result
    if env.admit is not None:
        # A planner-emitted plan names its specialists only now; they pass the same release
        # gate as a plan file before any step runs. The planner's own call is still metered.
        steps = output["plan"].get("steps")
        refs = [str(step.get("agent") or "") for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
        problem = env.admit(refs)
        if problem:
            result.update(status="refused", failure_class="agent_not_released", detail=problem, steps=[planner_step],
                          planned_tasks=output.get("tasks") or [])
            return result
    ran = run_plan(output["plan"], output.get("plan_input") or {}, env)
    add_usage(result["usage"], ran["usage"])
    for key in ("elapsed_seconds", "over_budget"):
        result["usage"][key] = ran["usage"].get(key)
    ran.update(usage=result["usage"], steps=[planner_step] + ran["steps"], planner=package.ref,
               planned_tasks=output.get("tasks") or [])
    ran["plan"] = f"{package.ref} -> {len(ran['steps']) - 1} step(s)"
    return ran


def _token_usage(usage: dict) -> tuple[dict, str]:
    if not usage.get("model_calls"):
        return {"available": False, "no_agent_invocation": True}, "Token usage: no agent invocation"
    if not usage.get("exact"):
        return {"available": False}, "Token usage: unavailable from current CLI output"
    fields = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
              "cached_input_tokens": usage["cached_input_tokens"], "total_tokens": usage["total_tokens"]}
    text = (f"Token usage: input {fields['input_tokens']}, output {fields['output_tokens']}, "
            f"cached input {fields['cached_input_tokens']}, total {fields['total_tokens']}")
    return {"available": True, **{key: str(value) for key, value in fields.items()}}, text


def brain_context_used(result: dict) -> list[dict]:
    """The scoped Brain reads a job actually made, one per record, as the queue's completion check expects."""
    seen, records = set(), []
    for row in result.get("evidence_refs") or []:
        record = row.get("provenance") if isinstance(row, dict) else None
        if not isinstance(record, dict) or record.get("fixture") or not record.get("note_id") or not record.get("path"):
            continue
        key = (str(record.get("client_scope") or ""), str(record["path"]))
        if key not in seen:
            seen.add(key)
            records.append(dict(record))
    return records


def _one_line(text: Any, limit: int = 600) -> str:
    return " ".join(str(text or "").split())[:limit]


def run_queue_item(root: Path, item: dict, *, modelport: Callable[..., dict] | None = None,
                   brain_read: Callable[[str, str], dict] | None = None, backend: str = "exec",
                   session: Any = None, ledger_root: Path | None = None,
                   ledger_item: str | None = None, admit: Callable[[list[str]], str] | None = None,
                   codex_account: Any = None) -> dict:
    """Run the item's plan or agent and return a worker-result dict.

    ``codex_account`` is the job's aos_codex_policy.AccountPin (one Codex account per job)."""
    started = time.monotonic()
    spec = executor_spec(item) or {}
    item_id = str(item.get("id") or "")
    target = str(spec.get("plan") or spec.get("agent") or spec.get("planner") or "")
    payload = spec.get("input") if isinstance(spec.get("input"), dict) else {}
    env = RunEnv(root=root, item=item, job=item_id, modelport=modelport, brain_read=brain_read,
                 backend=backend, session=session, ledger_root=ledger_root, ledger_item=ledger_item, admit=admit,
                 codex_account=codex_account)

    if not item_id or not target or sum(key in spec for key in ("plan", "agent", "planner")) != 1:
        result: dict[str, Any] = {"status": "refused", "failure_class": "executor_spec_invalid", "usage": {},
                                  "detail": "executor needs an item id and exactly one of plan, agent or planner",
                                  "artifacts": [], "steps": []}
    elif "plan" in spec:
        result = run_plan(target, payload, env)
    elif "planner" in spec:
        result = run_planned(target, payload, env)
    else:
        result = run_agent(target, payload, env)
        result["steps"] = [{"id": "run", "agent": result.get("agent"), "status": result["status"], "replayed": False, "execution_identity": result.get("execution_identity")}]
        identity = result.get("execution_identity") or {}
        if result["status"] == "ok" and identity.get("agent"):
            # Journal the completed direct run like a plan step, so its full output stays auditable
            # (gate 7 reliability reads it). A direct run is never replayed from this record.
            StepJournal(env.resolved_root(), item_id).append(
                plan_ref=f"agent:{identity['agent']}", step="run", agent=identity["agent"],
                package_sha256=str(identity.get("package_sha256") or ""), input_sha256=str(identity.get("input_sha256") or ""),
                result=result)

    status = result["status"]
    passed = status == "ok"
    context_used = brain_context_used(result)
    usage = result.get("usage") or {}
    token_usage, token_text = _token_usage(usage)
    resolved = str(result.get("plan") or result.get("agent") or target)
    steps = ", ".join(f"{step['id']} {step['status']}" + (" (replayed)" if step.get("replayed") else "")
                      for step in result.get("steps") or []) or "no step ran"
    artifacts = [str(row.get("path")) for row in result.get("artifacts") or [] if row.get("path")]
    held = [effect for effect in result.get("proposed_effects") or []
            if not (effect.get("authorization") or {}).get("authorized")]
    if status == "needs_input":
        missing = (result.get("output") or {}).get("missing") if isinstance(result.get("output"), dict) else None
        blocker = "needs input: " + "; ".join(str(m) for m in missing) if missing else _one_line(result.get("detail"))
    elif passed:
        blocker = "None"
    else:
        blocker = f"{result.get('failure_class') or status}: {_one_line(result.get('detail'))}"
    lines = [
        "PASS" if passed else "NEEDS ATTENTION",
        f"Summary for operator: {resolved} {'completed' if passed else 'stopped (' + status + ')'}; steps: {steps}.",
        f"Files touched: {', '.join(artifacts) if artifacts else 'None'}",
        f"Validation: {steps}; outputs schema-checked by the agent runtime",
        f"Brain context: {len(context_used)} scoped read(s)" + (": " + ", ".join(r["path"] for r in context_used[:6]) if context_used else ""),
        "Connector access: No connector action"
        + (f"; {len(result.get('proposed_effects') or [])} effect(s) proposed, {len(held)} held for review"
           if result.get("proposed_effects") else ""),
        token_text,
        f"Blockers: {blocker}",
        f"Next action: {'None' if passed else ('Reply with the missing input; the same job continues' if status == 'needs_input' else 'Review the agent failure')}",
    ]
    return {
        "success": passed, "returncode": 0 if passed else 1, "output": "\n".join(lines), "stdout": "", "stderr": "",
        "timed_out": False, "command_stage": "completion" if passed else "agent_runtime",
        "failure_class": "" if passed else FAILURE_CLASSES.get(status, "agent_failed"),
        "failure_detail": "" if passed else blocker,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "token_usage": token_usage, "token_usage_text": token_text,
        # ModelPort already wrote each call to the token ledger; the closeout must not log it again.
        "token_usage_logged": True,
        "file_changes": artifacts, "session_id": "", "agent_ref": resolved, "agent_result": result,
        "brain_context_used": context_used,
    }
