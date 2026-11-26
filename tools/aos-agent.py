#!/usr/bin/env python3
"""aos-agent: build, check, evaluate, pack and promote TAP agent packages.

Acceptance gate
  aos-agent.py check [<agent>]          gate 1 for one package, or all installed; zero tokens
  aos-agent.py eval <agent>             gates 1-6 from recorded replies; zero tokens
  aos-agent.py eval <agent> --live      the same with real ModelPort calls, recorded for replay
  aos-agent.py level <agent>            the level current evidence supports

Release readiness (M7, offline; reads jobs, receipts and ledger; changes nothing live)
  aos-agent.py release check [<agent>...] [--target active] [--runtime-root DIR] [--out DIR] [--text]
  aos-agent.py release stage <agent> --out DIR [--target active] [--approved-by WHO] [--expect-state S]
  aos-agent.py release drill <manifest.json> --scratch DIR [--fail-after-activation] [--interrupt before_write|after_write]
  aos-agent.py release repack <agent> [--note TEXT]   after new evidence: re-pack, record only the archive

Agent Factory
  aos-agent.py assess <name> --requirement TEXT    inspect architecture before authoring
  aos-agent.py new <name> --kind oneshot|tool    scaffold a build spec under agents/factory/specs/
  aos-agent.py build <name> [--live]             spec -> package -> optimise -> eval -> pack -> candidate
  aos-agent.py optimise <name> [--live]          the candidate search only; registers nothing
  aos-agent.py work-order <item.json>            run a factory.build_agent queue item
  aos-agent.py verify <agent>                    package == spec output == evidence == registry
  aos-agent.py pack <agent>                      write the reproducible archive
  aos-agent.py register <agent>                  record an evaluated hand-built package as a candidate
  aos-agent.py promote <agent> --to shadow|canary|active|candidate
  aos-agent.py registry                          registered agents, their state and current level

Self-improvement (M6, offline, propose-only)
  aos-agent.py improve [--out DIR] [--now ISO]   detect, generate, replay-evaluate, rank <=3 proposals

Without --live nothing calls a model: recorded replies are replayed. A live run
makes real subscription calls only for replies not yet recorded, inside the
spec's token budget, after checking the subscription's usage window, and
records exact usage in the token ledger. Nothing here routes work to an agent.

Revisit: when the acceptance gate, Factory steps or registry layout change. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from aos_agents import engineering, evals, factory, registry  # noqa: E402
from aos_agents.package import parse_ref  # noqa: E402
import aos_jobs  # noqa: E402


def _show(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _ledger_root(args) -> Path | None:
    if args.live and args.ledger_root is None:
        from aos_codex_policy import CODEX_TARGET

        return CODEX_TARGET.root
    return args.ledger_root


def _add_live(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--live", action="store_true", help="allow real model calls for replies not yet recorded")
    parser.add_argument("--backend", choices=("exec", "app-server"), default="exec")
    parser.add_argument("--ledger-root", type=Path, default=None, help="root holding the token ledger (live runs)")


def _build_summary(report: dict) -> dict:
    keep = ("factory", "agent", "status", "failed_step", "level", "supports", "binding", "chosen", "gates", "holdout",
            "tokens", "totals", "package_sha256", "evidence", "archive", "registry", "engineering", "elapsed_seconds")
    summary = {key: report[key] for key in keep if key in report}
    summary["steps"] = [{"step": row["step"], "ok": row["ok"]} for row in report["steps"]]
    summary["candidates"] = [{key: trial.get(key) for key in ("prompt", "model", "effort", "outcome", "tokens_spent")}
                             for trial in (report.get("optimise") or {}).get("candidates") or []]
    failed = next((row for row in report["steps"] if not row["ok"]), None)
    if failed:
        summary["failure"] = failed
    return summary


def _add_release(commands) -> None:
    release_cmd = commands.add_parser("release", help="M7 release readiness, offline staging and rollback drill")
    actions = release_cmd.add_subparsers(dest="action", required=True)
    targets = tuple(registry.REQUIRED_LEVEL)
    check_cmd = actions.add_parser("check", help="per-agent readiness report (no model call, no write)")
    check_cmd.add_argument("agents", nargs="*")
    check_cmd.add_argument("--target", choices=targets, default="active")
    check_cmd.add_argument("--runtime-root", type=Path, default=None, help="root holding queue/jobs, receipts and ledger")
    check_cmd.add_argument("--out", type=Path, default=None, help="also write readiness.json and readiness.txt here")
    check_cmd.add_argument("--text", action="store_true", help="print the human summary instead of JSON")
    stage_cmd = actions.add_parser("stage", help="prepare a release candidate as files; never writes the registry")
    stage_cmd.add_argument("agent")
    stage_cmd.add_argument("--out", type=Path, required=True)
    stage_cmd.add_argument("--target", choices=targets, default="active")
    stage_cmd.add_argument("--runtime-root", type=Path, default=None)
    stage_cmd.add_argument("--approved-by", default="", help="who approved; recorded, not verified")
    stage_cmd.add_argument("--expect-state", default=None, help="refuse unless the registry state is this")
    drill_cmd = actions.add_parser("drill", help="apply a staged candidate to a scratch registry copy, then reverse it")
    drill_cmd.add_argument("manifest", type=Path)
    drill_cmd.add_argument("--scratch", type=Path, required=True)
    drill_cmd.add_argument("--runtime-root", type=Path, default=None)
    drill_cmd.add_argument("--fail-after-activation", action="store_true")
    drill_cmd.add_argument("--interrupt", choices=("before_write", "after_write"), default="")
    collect = actions.add_parser("reliability", help="collect Gate 7 sources from existing jobs; no execution or promotion")
    collect.add_argument("agent")
    collect.add_argument("--observations", type=Path, required=True, help="JSON: cases map (case -> 3 job IDs), kill and forced captured sources; "
                         "forced carries evidence_class (genuine_provider or injected_transport)")
    collect.add_argument("--runtime-root", type=Path, required=True)
    collect.add_argument("--simulated", action="store_true", help="mark offline evidence; cannot pass Gate 7")
    repack_cmd = actions.add_parser("repack", help="re-pack an unchanged registered package after its evidence changed")
    repack_cmd.add_argument("agent")
    repack_cmd.add_argument("--note", default="")


def _release(args) -> int:
    from aos_agents import release

    try:
        if args.action == "reliability":
            from aos_agents import reliability
            from aos_agents.package import repo_root
            source = json.loads(args.observations.read_text())
            path = reliability.produce(args.agent, source["cases"], source["kill"], source["forced"],
                                       repo_root(args.root), args.runtime_root, simulated=args.simulated)
            _show({"evidence": str(path), "simulated": args.simulated, "executed_jobs": 0})
            return 0
        if args.action == "check":
            report = release.check(args.agents or None, args.root, runtime_root=args.runtime_root, target=args.target)
            text = release.render(report)
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "readiness.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
                (args.out / "readiness.txt").write_text(text)
            print(text, end="") if args.text else _show(report)
            return 0
        if args.action == "stage":
            result = release.stage(args.agent, args.out, args.root, runtime_root=args.runtime_root, target=args.target,
                                   approved_by=args.approved_by, expect_state=args.expect_state)
            _show(result)
            return 0 if result["staged"] else 1
        if args.action == "repack":
            _show(registry.repack(args.agent, args.root, note=args.note))
            return 0
        result = release.drill(args.manifest, args.scratch, args.root, runtime_root=args.runtime_root,
                               fail_after_activation=args.fail_after_activation, interrupt=args.interrupt)
        _show(result)
        return 0 if not result["refused"] else 1
    except (release.ReleaseError, registry.RegistryError, OSError, ValueError) as exc:
        _show({"error": str(exc)})
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aos-agent", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="repository root (default: this checkout)")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="gate 1, zero tokens")
    check.add_argument("agent", nargs="?")
    run = commands.add_parser("eval", help="gates 1-6")
    run.add_argument("agent")
    _add_live(run)
    run.add_argument("--cases", default="", help="comma-separated case ids; a partial run writes no evidence")
    run.add_argument("--reuse-recorded", action="store_true", help="on a live run, call only for replies not yet recorded")
    run.add_argument("--full", action="store_true", help="print every case row")
    level = commands.add_parser("level", help="level supported by current evidence")
    level.add_argument("agent")
    _add_release(commands)

    new = commands.add_parser("new", help="scaffold a build spec")
    new.add_argument("name")
    new.add_argument("--kind", choices=("oneshot", "tool"), required=True)
    new.add_argument("--summary", default="")
    new.add_argument("--requirement", default="")
    assess = commands.add_parser("assess", help="inspect existing capabilities and integration paths before authoring")
    assess.add_argument("name")
    assess.add_argument("--requirement", required=True)
    assess.add_argument("--dependency", action="append", default=[])
    for name, text in (("build", "run the build loop for a spec"), ("optimise", "candidate search only")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("name")
        _add_live(sub)
        sub.add_argument("--token-budget", type=int, default=None, help="lower the spec's token budget for this run")
        sub.add_argument("--skip-quota-check", action="store_true", help="do not read the subscription usage window first")
        sub.add_argument("--full", action="store_true", help="print the whole build report")
    order = commands.add_parser("work-order", help="run a factory.build_agent queue item from a file")
    order.add_argument("item", type=Path)
    order.add_argument("--backend", choices=("exec", "app-server"), default="exec")
    order.add_argument("--ledger-root", type=Path, default=None)
    order.add_argument("--full", action="store_true")
    for name, text in (("verify", "package, spec, evidence and registry agree"), ("pack", "write the archive"),
                       ("register", "record an evaluated package as a candidate")):
        commands.add_parser(name, help=text).add_argument("agent")
    promote = commands.add_parser("promote", help="move a registered package to a release state")
    promote.add_argument("agent")
    promote.add_argument("--to", required=True, choices=registry.PROMOTION_ORDER)
    promote.add_argument("--approved-by", default="", help="who approved a canary or active promotion")
    promote.add_argument("--note", default="")
    commands.add_parser("registry", help="list registered agents")
    improve_cmd = commands.add_parser("improve", help="M6: offline, propose-only improvement proposals (no model call)")
    improve_cmd.add_argument("--out", type=Path, default=None, help="where proposals go (default results/factory_improvements)")
    improve_cmd.add_argument("--pool", type=Path, default=None, help="candidate patch pool (default agents/factory/improvements/patches)")
    improve_cmd.add_argument("--scope", default="global", help="client scope of the evidence read (default global)")
    improve_cmd.add_argument("--now", default="", help="ISO timestamp for a reproducible run")
    improve_cmd.add_argument("--full", action="store_true", help="print the whole report")
    args = parser.parse_args(argv)

    if args.command == "improve":
        import datetime as dt

        from aos_agents import improve

        now = dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
        try:
            report = improve.run(args.root, out_dir=args.out, pool_dir=args.pool, client_scope=args.scope, now=now)
        except improve.ImproveError as exc:
            _show({"error": str(exc)})
            return 1
        _show(report if args.full else {
            "run_id": report["run_id"], "report": report.get("report_path"), "candidates_considered": report["candidates_considered"],
            "signals": [{k: s.get(k) for k in ("type", "key", "occurrences", "actionable", "holdout_confirmation")} for s in report["signals"]],
            "proposals": [{"rank": p["rank"], "kind": p["kind"], "agent": p["agent"], "evaluation": p["evaluation"].get("status"),
                           "reason": p["evaluation"].get("reason"), "files": p.get("files")} for p in report["proposals"]],
            "guarantees": report["guarantees"]})
        return 0

    if args.command == "check":
        reports = [evals.check(args.agent, args.root)] if args.agent else evals.check_all(args.root)
        _show(reports if not args.agent else reports[0])
        return 0 if all(report["pass"] for report in reports) else 1
    if args.command == "level":
        _show(evals.current_level(args.agent, args.root))
        return 0
    if args.command == "release":
        return _release(args)
    if args.command == "eval":
        only = [part.strip() for part in args.cases.split(",") if part.strip()] or None
        evidence = evals.evaluate(args.agent, args.root, live=args.live, backend=args.backend, ledger_root=_ledger_root(args),
                                  only=only, reuse_recorded=args.reuse_recorded)
        summary = {key: evidence.get(key) for key in ("agent", "binding", "mode", "complete", "level", "totals", "not_recorded", "evidence_path")}
        summary["gates"] = {gate: {k: v for k, v in body.items() if k not in ("composition", "eval_set")}
                            for gate, body in (evidence.get("gates") or {}).items()}
        if args.full:
            summary["cases"] = evidence.get("cases")
        else:
            summary["failed_cases"] = [row["id"] for row in evidence.get("cases") or [] if not row["passed"] and not row["not_recorded"]]
        _show(summary)
        return 0 if evidence.get("complete") and evidence.get("level") != "none" else 1

    try:
        if args.command == "assess":
            root = evals.repo_root(args.root)
            name, _ = parse_ref(args.name)
            _show(engineering.assess(root, name, args.requirement, dependencies=args.dependency, base=engineering.head(root)))
            return 0
        if args.command == "new":
            _show(factory.new_spec(args.name, args.kind, args.root, summary=args.summary, requirement=args.requirement))
            return 0
        if args.command in ("build", "optimise"):
            if args.live and not args.skip_quota_check:
                quota = factory.quota_preflight()
                if quota.get("advisory"):
                    print(f"quota advisory: {quota['advisory']}", file=sys.stderr)
                if not quota["ok"]:
                    _show({"status": "failed", "failed_step": "quota", "quota": quota})
                    return 1
            report = factory.build(args.name, args.root, live=args.live, backend=args.backend, ledger_root=_ledger_root(args),
                                   token_budget=args.token_budget, until="optimise" if args.command == "optimise" else "register")
            _show(report if args.full else _build_summary(report))
            return 0 if report["status"] in ("candidate", "stopped", "reused") else 1
        if args.command == "work-order":
            item = json.loads(args.item.read_text(encoding="utf-8"))
            ledger_root = args.ledger_root
            if (factory.work_order_spec(item) or {}).get("live") is True and ledger_root is None:
                from aos_codex_policy import CODEX_TARGET

                ledger_root = CODEX_TARGET.root
            root = evals.repo_root(args.root)
            # Build and ledger use the same data root, ID, claim and receipt as every other job.
            if ledger_root is not None and Path(ledger_root).resolve() != root.resolve():
                raise factory.FactoryError("work-order job and ledger must use the same root")
            job, _ = aos_jobs.create(root, conversation_id="factory-cli", topic="factory", request=f"Factory work order {item.get('id') or args.item.name}",
                                     executor=item["executor"])
            result = aos_jobs.execute(root, job["id"], backend=args.backend)
            print((root / result["receipts"][-1]["path"]).read_text(), end="")
            if args.full:
                _show(result.get("factory_report") or {})
            return 0 if result["status"] == "done" else 1
        if args.command == "verify":
            result = factory.verify(args.agent, args.root)
            _show(result)
            return 0 if result["pass"] else 1
        if args.command == "pack":
            _show(registry.pack(args.agent, args.root))
            return 0
        if args.command == "register":
            entry = registry.register(args.agent, args.root)
            _show({key: entry[key] for key in ("version", "state", "level", "binding", "package_sha256", "evidence")})
            return 0
        if args.command == "promote":
            result = registry.promote(args.agent, args.to, args.root, approved_by=args.approved_by, note=args.note)
            _show(result)
            return 0 if result["promoted"] else 1
        _show(registry.listing(args.root))
        return 0
    except (factory.FactoryError, engineering.EngineeringError, registry.RegistryError, evals.PackageError, aos_jobs.JobError) as exc:
        _show({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
