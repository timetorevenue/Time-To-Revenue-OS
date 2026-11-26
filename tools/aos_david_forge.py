"""Project Forge's existing release and execution contracts into David's capability session."""
from __future__ import annotations

import json
import re
import tomllib

import aos_jobs
from aos_agents import registry
from aos_agents.package import PackageError, load_package
from aos_agents.plans import PLANS_DIR, PlanError, load_plan
from aos_capabilities import Capability, CapabilityError, _terms

OUTPUT = {"type": "object", "required": ["job_id", "status"]}
LIVE_STATES = {"canary", "active"}
SCOPED_GRANTS = {"brain.read:global", "icm.read", "artifact.read", "artifact.write"}


def read_registry(root):
    try:
        return registry.load(root)["agents"]
    except (registry.RegistryError, KeyError, OSError):
        return {}


def eligibility(ref, row, manifest, caller, *, planner=False):
    agent = manifest["agent"]
    name, _, version = ref.partition("@")
    if name != agent["name"] or row.get("version") != agent["version"] or version != agent["version"]:
        return "unavailable", "The installed version does not match the registered release."
    state = row.get("state")
    if state not in LIVE_STATES:
        return ("experimental" if state in {"candidate", "shadow"} else "unavailable",
                f"{ref} is {state or 'unregistered'} in Agent Forge.")
    compose = manifest.get("compose") or {}
    grants = set((manifest.get("capabilities") or {}).get("grants") or ())
    kinds = {"planner"} if planner else {"tool", "oneshot"}
    if agent["kind"] not in kinds or compose.get("calls") or grants - SCOPED_GRANTS:
        return "unavailable", "This package needs a specialized runtime or a narrower execution grant."
    if caller == "david" and not set(compose.get("callable_by") or ()) & {"*", caller}:
        return "unavailable", "Its composition contract permits use only through a compatible workflow or specialist."
    return ("conditional" if state == "canary" else "available"), ""


def package_problem(root, ref, caller, *, rows=None, planner=False):
    rows = read_registry(root) if rows is None else rows
    try:
        package = load_package(ref, root)
        row = rows.get(package.name) or {}
        _, problem = eligibility(package.ref, row, package.manifest, caller, planner=planner)
        if problem:
            return problem
        if package.content_sha256 != row.get("package_sha256"):
            return f"{package.ref} does not match the package hash registered at release."
    except (PackageError, KeyError, TypeError, OSError):
        return "The installed specialist contract is unavailable."
    return ""


def admission(root, kind, ref, *, rows=None):
    """Recheck package identity and all step contracts at admission and dispatch."""
    rows = read_registry(root) if rows is None else rows
    try:
        plan = load_plan(ref, root) if kind == "plan" else None
        refs = [step["agent"] for step in plan["steps"]] if plan else [ref]
        for target in refs:
            if problem := package_problem(root, target, plan["plan"] if plan else "david", rows=rows,
                                          planner=kind == "planner"):
                return problem
        return aos_jobs.refs_release_problem(root, refs)
    except (PlanError, PackageError, KeyError, TypeError, OSError):
        return f"The installed contract for {ref} is unavailable."


def request_matches(text, cap):
    """Bind admission to the current, original request rather than tool or attachment text."""
    name = cap.id.split(".", 2)[2].split("@", 1)[0]
    exact = re.match(r"^/capability\s+" + re.escape(cap.id) + r"(?:\s|$)", text)
    if exact:
        return True
    if re.search(r"\b(?:do not|don't|never|without)\b", text, re.I):
        return False
    explicit = re.match(r"^(?:please\s+)?(?:run|start|invoke|queue)\b", text, re.I)
    if explicit and re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", text, re.I):
        return True
    action = re.match(r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?"
                      r"(?:classify|triage|retrieve|verify|check|draft|summarize|analyse|analyze|"
                      r"prepare|research|create|review|propose|open|find|run|use)\b", text, re.I)
    generic = {"draft", "create", "one", "named", "use", "run", "check", "task", "agent", "workflow"}
    terms = (_terms(text.split("\n", 1)[0]) & _terms(cap.description + " " + name)) - generic
    return bool(action and len(terms) >= 2)


def capabilities(root, submit):
    rows = read_registry(root)
    entries = []
    packages = {}
    for path in sorted((root / "agents/packages").glob("*/agent.toml")):
        try:
            manifest = tomllib.loads(path.read_text(encoding="utf-8"))
            agent = manifest["agent"]
            name, version = agent["name"], agent["version"]
            if name != path.parent.name:
                continue
            row = rows.get(name) or {}
            ref = f"{name}@{version}"
            state, problem = eligibility(ref, row, manifest, "david")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        packages[ref] = (manifest, row)
        entries.append(_entry(root, submit, "agent", ref, agent["summary"], state, problem, {
            "release": row.get("state", "unregistered"), "reliability": row.get("level"),
            "runtime": agent["kind"],
        }))
    for path in sorted((root / PLANS_DIR).glob("*.json")):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            ref = f"{plan['plan']}@{plan['version']}"
            refs = [step["agent"] for step in plan["steps"]]
            states = [(rows.get(target.split("@")[0]) or {}).get("state", "unregistered") for target in refs]
            missing = [f"{target} ({state})" for target, state in zip(refs, states) if state not in LIVE_STATES]
            problem = "Requires release or registration of " + ", ".join(missing) if missing else ""
            if not refs:
                problem = "The workflow has no steps."
            if not problem:
                for target in refs:
                    package = packages.get(target)
                    if package is None:
                        problem = f"{target} is not installed at the specified version."
                        break
                    manifest, row = package
                    _, problem = eligibility(target, row, manifest, plan["plan"])
                    if problem:
                        break
            if missing:
                state = "experimental" if "unregistered" not in states else "unavailable"
            else:
                state = "unavailable" if problem else "conditional" if "canary" in states else "available"
            entries.append(_entry(root, submit, "plan", ref, plan.get("summary", ""), state, problem, {
                "release": "derived from workflow steps", "steps": len(refs),
            }))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


def _entry(root, submit, kind, ref, summary, state, problem, metadata):
    def schemas():
        if problem := admission(root, kind, ref):
            raise CapabilityError(problem)
        try:
            contract = load_plan(ref, root)["input_schema"] if kind == "plan" else load_package(ref, root).input_schema
            return contract, OUTPUT
        except (PackageError, PlanError):
            raise CapabilityError("The Job input contract could not be loaded.") from None

    def details():
        if kind == "agent":
            package = load_package(ref, root)
            return {"result_schema": package.output_schema, "context": package.manifest.get("context", {}),
                    "resources": package.budget, "grants": sorted(package.grants)}
        plan = load_plan(ref, root)
        return {"result_mapping": plan.get("output", {}), "resources": plan["budget"],
                "steps": [{**{key: step[key] for key in ("id", "agent")},
                           "context": load_package(step["agent"], root).manifest.get("context", {})}
                          for step in plan["steps"]]}

    return Capability(f"jobs.{kind}.{ref}", summary, "operator", "Jobs with scoped context and effects held",
                      schemas, lambda args: submit(kind, ref, args), availability=state,
                      metadata={**metadata, **({"reason": problem} if problem else {})},
                      details=details, auto_inspect=True, direct=True)
