"""Agent Factory self-improvement (M6): offline, propose-only.

Reads what already happened and proposes at most three changes to agent specs.
It changes nothing that runs: no package, spec, registry entry, job or ledger line
is written, no model is called and nothing is promoted, sent or scheduled. Casey
reviews a proposal; applying it is the ordinary Factory path (edit the spec,
``aos-agent.py build``, ``promote``), under rules/runtime_permission_policy.md.

  collect   job records (queue/jobs), step journals (queue/threads) and the
            acceptance evidence of each installed package (evidence/<binding>.json),
            in one client scope; other scopes are excluded and only counted
  split     jobs: the newest share is held out of detection and only confirms a
            signal; eval cases: the Factory's own hold-out split, never read by a generator
  detect    recurring failures, review holds, unsupported planner requests (jobs the
            planner refused as ``request_unsupported``; what happened to the request
            afterwards is not recorded, so M6 claims nothing about it) and token cost
            (runtime samples against the recorded acceptance median, and outlier eval
            cases), each with sample counts and a provisional threshold
  generate  candidates from training evidence only: a reviewable spec patch from the
            patch pool, the spec's next pinned candidate, a precise text change, or a
            note that no specialist covers a recurring request (unnamed: naming is Casey's)
            NOT DONE: M6 does not write code patches itself. Spec patches come from the
            patch pool (written by Casey or an attended workbench session); M6 judges them.
  evaluate  each patchable candidate in a scratch copy of agents/: the current package
            and the patched rebuild replay the same recorded replies (zero tokens) on
            training and held-out cases; anything not replayable is insufficient evidence
  rank      measured improvements first; regressions are rejected; at most three proposed
  report    report.json, proposal-<n>.md and candidate-<n>.diff under
            results/factory_improvements/<run>/, which the morning brief reads

Revisit: when the job record, the eval evidence shape or the Factory spec layout changes.
· Last touched: 2026-11-16.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import difflib
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import evals, factory
from .journal import StepJournal
from .package import PACKAGES_DIR, PackageError, installed, load_package, repo_root

M6_VERSION = "0.1.1"
MAX_PROPOSALS = 3
MAX_EVALUATED = 6                 # candidates evaluated before the cut to three
# PROVISIONAL thresholds: initial defaults, not yet calibrated against production evidence
# (3 live jobs on 2026-11-16). Every report carries them under "thresholds" with this status.
THRESHOLD_STATUS = "provisional: initial M6 defaults, not calibrated against production evidence"
MIN_COMPARABLE_CASES = 5          # training cases replayed on both sides before a before/after is claimed
MIN_COMPARABLE_HOLDOUT = 1        # held-out cases replayed on both sides
STALE_AFTER = dt.timedelta(days=7)
MIN_OCCURRENCES = 3               # distinct jobs or eval cases before a failure is actionable
MIN_RATE = 0.2                    # share of that agent's runs
COST_MIN_SAMPLES = 5              # exact runtime calls before a cost regression is claimed
COST_REGRESSION_RATIO = 1.25      # runtime median over the acceptance median
COST_OUTLIER_RATIO = 1.5          # one eval case over the median of called training cases
HOLDOUT_JOB_SHARE = 0.2           # newest jobs, held out of detection
GAP_SIMILARITY = 0.5
NOT_COMPARABLE_TOKEN_SHIFT = 0.05
OUTPUT_DIR = Path("results") / "factory_improvements"
POOL_DIR = Path("agents") / "factory" / "improvements" / "patches"
PROTECTED = (Path("agents") / "packages", Path("agents") / "factory" / "specs", Path("agents") / "registry.lock.json",
             Path("queue") / "jobs", Path("queue") / "threads", Path("queue") / "token_ledger.jsonl")
FAILED_STATUSES = ("failed", "refused", "blocked")
# Holds the policy always requires (rules/runtime_permission_policy.md; validated_brain_writer).
# M6 reports them and never proposes a change that would remove one.
POLICY_HOLDS = re.compile(r"^(BRAIN_|GMAIL_SEND|AGENTMAIL_SEND|LINKEDIN_|PUBLISH|DEPLOY)", re.I)
STOPWORDS = frozenset("""about after again also from have into just like make more need needs only over please
some than that their them then there these they this those what when which while with would your
casey david could should every each other want wants using used against month months week weeks
today tomorrow""".split())


class ImproveError(RuntimeError):
    """M6 cannot run safely as asked."""


# -- small helpers --------------------------------------------------------------

def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{4,}", str(text or "").lower()) if word not in STOPWORDS}


def _tree_digest(root: Path, relatives: Iterable[Path]) -> dict[str, str]:
    """A digest of each protected path, to prove the run left them as they were."""
    out = {}
    for relative in relatives:
        path = root / relative
        digest = hashlib.sha256()
        if path.is_file():
            digest.update(path.read_bytes())
        elif path.is_dir():
            for item in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                digest.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
                digest.update(hashlib.sha256(item.read_bytes()).digest())
        else:
            digest.update(b"<absent>")
        out[relative.as_posix()] = digest.hexdigest()
    return out


REPORT_GENERATOR = "tools/aos_agents/improve.py"
ACTIVE = (Path("agents") / "packages", Path("agents") / "factory" / "specs", Path("agents") / "registry.lock.json")


def _active_digest(root: Path) -> str:
    """One digest of what runs (packages, specs, registry): a report made before it changed is stale."""
    return _sha(_tree_digest(root, ACTIVE))


@contextlib.contextmanager
def _no_model_calls():
    """Belt and braces: replay never reaches ModelPort, and if anything tried, it fails closed."""
    try:
        import aos_modelport
    except Exception:  # noqa: BLE001 - absent module means nothing can call it
        yield
        return
    original = aos_modelport.call

    def refuse(*_args, **_kwargs):
        raise ImproveError("M6 is offline: a model call was attempted and refused")

    aos_modelport.call = refuse
    try:
        yield
    finally:
        aos_modelport.call = original


# -- collect --------------------------------------------------------------------

def _scope_of_package(package) -> set[str]:
    return {grant.split(":", 1)[1] for grant in package.grants if grant.startswith("brain.read:")}


def collect(root: Path, *, client_scope: str = "global") -> dict:
    """Recorded evidence in one client scope. Jobs from other scopes are counted, never read further."""
    import aos_jobs

    root = Path(root)
    jobs_in, excluded = [], 0
    for job in aos_jobs.list_jobs(root, limit=None):
        if str(job.get("client_scope") or "global") != client_scope:
            excluded += 1
            continue
        jobs_in.append(job)
    jobs_in.sort(key=lambda job: (str(job.get("created_at") or ""), job["id"]))
    held = math.ceil(HOLDOUT_JOB_SHARE * len(jobs_in)) if len(jobs_in) >= 5 else 0
    split = {"generation": jobs_in[:len(jobs_in) - held], "holdout": jobs_in[len(jobs_in) - held:]}

    packages, skipped_packages = {}, []
    for name in installed(root):
        try:
            package = load_package(name, root)
        except PackageError as exc:
            skipped_packages.append({"agent": name, "reason": str(exc)[:200]})
            continue
        scopes = _scope_of_package(package)
        if scopes - {client_scope}:
            skipped_packages.append({"agent": name, "reason": f"reads scope(s) outside {client_scope}"})
            continue
        packages[name] = _package_evidence(root, package)
    return {"client_scope": client_scope, "jobs": split, "jobs_excluded_out_of_scope": excluded,
            "packages": packages, "packages_skipped": skipped_packages}


def _package_evidence(root: Path, package) -> dict:
    """Current acceptance evidence for a package, split as the Factory split it."""
    entry = {"ref": package.ref, "kind": package.kind, "package_sha256": package.content_sha256,
             "binding": evals.binding(package), "evidence": None, "train": [], "holdout": [], "spec": None}
    path = package.directory / evals.EVIDENCE_DIR / f"{evals.binding(package)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entry["evidence_problem"] = "no readable evidence for this binding"
        return entry
    if data.get("package_sha256") != package.content_sha256:
        entry["evidence_problem"] = "evidence was recorded for different package content"
        return entry
    rows = data.get("cases") or []
    try:
        spec = factory.load_spec(package.name, root)
        holdout = set(factory.holdout_ids(evals.load_cases(package)["cases"], spec.holdout_share))
        entry["spec"] = {"path": (factory.SPECS_DIR / package.name).as_posix(), "sha256": spec.sha256,
                         "candidates": list(spec.candidates)}
    except (factory.FactoryError, evals.EvalError):
        holdout = {row["id"] for row in rows if "holdout" in (row.get("tags") or [])}
    entry["evidence"] = {"path": path.relative_to(root).as_posix(), "level": data.get("level"), "mode": data.get("mode"),
                         "gate6": {k: (data.get("gates") or {}).get("6", {}).get(k)
                                   for k in ("pass", "p95_tokens", "median_tokens", "budget_tokens", "baseline_tokens")}}
    entry["train"] = [row for row in rows if row["id"] not in holdout]
    entry["holdout"] = [row for row in rows if row["id"] in holdout]
    return entry


def _step_rows(root: Path, job: dict) -> list[dict]:
    """One row per step that ran: agent, outcome, exact usage and package hash where recorded."""
    usage = {row.get("step"): row for row in (job.get("usage") or {}).get("by_step") or []}
    hashes = {}
    try:
        for record in StepJournal(root, job["id"]).records():
            hashes[str(record.get("step"))] = record.get("package_sha256") or ""
    except (ValueError, OSError):
        pass
    rows = []
    for step in (job.get("result") or {}).get("steps") or []:
        call = usage.get(step.get("id")) or {}
        agent = str(step.get("agent") or call.get("agent") or "")
        if " -> " in agent:   # a plan's own summary row, e.g. "shared.planner@0.1.0 -> 3 step(s)"
            agent = agent.split(" -> ", 1)[0]
        rows.append({"job": job["id"], "step": step.get("id"), "agent": agent, "status": step.get("status"),
                     "failure_class": step.get("failure_class") or "", "model": call.get("model") or "",
                     "tokens": call.get("tokens"), "exact": call.get("exact"),
                     "package_sha256": hashes.get(str(step.get("id")), "")})
    return rows


# -- detect ---------------------------------------------------------------------

def _signal(kind: str, key: dict, **body: Any) -> dict:
    ident = "sig-" + _sha([kind, key])[:12]
    return {"id": ident, "type": kind, "key": key, **body}


def detect(evidence: dict, root: Path) -> list[dict]:
    """Signals from generation evidence; held-out jobs only confirm them."""
    generation, holdout = evidence["jobs"]["generation"], evidence["jobs"]["holdout"]
    gen_rows = [row for job in generation for row in _step_rows(root, job)]
    hold_rows = [row for job in holdout for row in _step_rows(root, job)]
    signals: list[dict] = []

    # Recurring step failures, by agent and failure class (jobs) and failing training cases (evals).
    runs = Counter(row["agent"] for row in gen_rows)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in gen_rows:
        if row["status"] in FAILED_STATUSES and row["failure_class"] not in ("request_unsupported", "agent_not_released"):
            groups[(row["agent"], row["failure_class"] or row["status"])].append(row)
    eval_groups: dict[tuple, list[dict]] = defaultdict(list)
    for name, package in evidence["packages"].items():
        for row in package["train"]:
            if not row["passed"] and not row["not_recorded"]:
                eval_groups[(package["ref"], row["failure_class"] or "check_failed")].append(row)
    for key in sorted(set(groups) | set(eval_groups)):
        agent, failure_class = key
        job_ids = sorted({row["job"] for row in groups.get(key, [])})
        case_ids = sorted({row["id"] for row in eval_groups.get(key, [])})
        name = agent.split("@", 1)[0]
        train_cases = len((evidence["packages"].get(name) or {}).get("train") or [])
        rate = len(job_ids) / runs[agent] if runs[agent] else (len(case_ids) / train_cases if train_cases else 0.0)
        occurrences = len(job_ids) + len(case_ids)
        confirmed = sorted({row["job"] for row in hold_rows if row["agent"] == agent
                            and (row["failure_class"] or row["status"]) == failure_class and row["status"] in FAILED_STATUSES})
        hold_runs = sum(1 for row in hold_rows if row["agent"] == agent)
        signals.append(_signal("recurring_failure", {"agent": agent, "failure_class": failure_class},
                               occurrences=occurrences, rate=round(rate, 3), runs=runs[agent], evidence_ids=job_ids + case_ids,
                               package_sha256=sorted({row["package_sha256"] for row in groups.get(key, []) if row["package_sha256"]}),
                               actionable=occurrences >= MIN_OCCURRENCES and rate >= MIN_RATE,
                               threshold={"min_occurrences": MIN_OCCURRENCES, "min_rate": MIN_RATE},
                               holdout_confirmation=("confirmed" if confirmed else "not_seen" if hold_runs else "insufficient_evidence"),
                               holdout_ids=confirmed))

    # Review holds: an effect held for Casey. Policy-required holds are reported, never "fixed".
    holds: dict[str, list[str]] = defaultdict(list)
    for job in generation:
        for effect in job.get("effects") or []:
            if effect.get("status") == "held":
                holds[str(effect.get("action") or effect.get("effect") or "")].append(job["id"])
    for action, job_ids in sorted(holds.items()):
        policy = bool(POLICY_HOLDS.match(action))
        signals.append(_signal("review_hold", {"action": action}, occurrences=len(set(job_ids)), evidence_ids=sorted(set(job_ids)),
                               policy_required=policy, actionable=not policy and len(set(job_ids)) >= MIN_OCCURRENCES,
                               note="held by rules/runtime_permission_policy.md; M6 never proposes removing it" if policy else ""))

    # Unsupported planner requests: the planner refused them as request_unsupported. Only the refusal is recorded;
    # no record says where the request went next, so no fallback is asserted.
    gaps =[(job, _words(job.get("request"))) for job in generation
            if job.get("failure_class") == "request_unsupported"
            or any(row["failure_class"] == "request_unsupported" for row in _step_rows(root, job))]
    clusters: list[list[tuple[dict, set[str]]]] = []
    for job, words in gaps:
        for cluster in clusters:
            anchor = cluster[0][1]
            # Overlap coefficient: short requests share few words, so Jaccard under-merges them.
            if anchor and words and len(anchor & words) / min(len(anchor), len(words)) >= GAP_SIMILARITY:
                cluster.append((job, words))
                break
        else:
            clusters.append([(job, words)])
    for cluster in clusters:
        counts = Counter(word for _job, words in cluster for word in words)
        terms = [word for word, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]]
        ids = sorted(job["id"] for job, _w in cluster)
        hold_ids = sorted(job["id"] for job in holdout if job.get("failure_class") == "request_unsupported"
                          and terms and len(set(terms) & _words(job.get("request"))) >= 2)
        signals.append(_signal("unsupported_planner_request", {"terms": terms}, occurrences=len(ids), evidence_ids=ids,
                               actionable=len(ids) >= MIN_OCCURRENCES, threshold={"min_occurrences": MIN_OCCURRENCES},
                               holdout_confirmation="confirmed" if hold_ids else "not_seen", holdout_ids=hold_ids))

    # Token cost: exact runtime samples against the package's recorded acceptance median, same model.
    samples: dict[tuple, list[dict]] = defaultdict(list)
    for row in gen_rows:
        if row["model"] and isinstance(row["tokens"], int):
            samples[(row["agent"], row["model"])].append(row)
    for (agent, model), rows in sorted(samples.items()):
        package = evidence["packages"].get(agent.split("@", 1)[0]) or {}
        exact = [row for row in rows if row["exact"] is True]
        baseline = ((package.get("evidence") or {}).get("gate6") or {}).get("median_tokens")
        same_model = str(package.get("binding") or "").startswith(model + ".")
        current = _median([row["tokens"] for row in exact])
        problem = ("no recorded acceptance baseline for this binding" if not baseline or not same_model
                   else f"{len(exact)} exact sample(s); {COST_MIN_SAMPLES} needed" if len(exact) < COST_MIN_SAMPLES else "")
        ratio = round(current / baseline, 3) if current and baseline and same_model else None
        hold_exact = [row["tokens"] for row in hold_rows if row["agent"] == agent and row["model"] == model and row["exact"] is True]
        signals.append(_signal("cost_regression", {"agent": agent, "model": model}, occurrences=len(exact),
                               samples=len(rows), exact_samples=len(exact),
                               non_exact_excluded=len(rows) - len(exact), runtime_median_tokens=current,
                               baseline_median_tokens=baseline, baseline_source=(package.get("evidence") or {}).get("path"),
                               ratio=ratio, evidence_ids=sorted({row["job"] for row in exact}),
                               actionable=not problem and ratio is not None and ratio >= COST_REGRESSION_RATIO,
                               insufficient=problem, threshold={"min_exact_samples": COST_MIN_SAMPLES, "ratio": COST_REGRESSION_RATIO},
                               holdout_confirmation=("insufficient_evidence" if not hold_exact or not baseline else
                                                     "confirmed" if _median(hold_exact) / baseline >= COST_REGRESSION_RATIO else "not_seen"),
                               holdout_median_tokens=_median(hold_exact)))

    # Outlier eval cases: exact recorded tokens far above the package's own training median.
    for name, package in sorted(evidence["packages"].items()):
        called = [row for row in package["train"] if row["model_calls"] and not row["not_recorded"]]
        median = _median([row["tokens"] for row in called])
        if not median or len(called) < COST_MIN_SAMPLES:
            continue
        outliers = sorted((row for row in called if row["tokens"] > COST_OUTLIER_RATIO * median and row["exact"]),
                          key=lambda row: (-row["tokens"], row["id"]))
        if not outliers:
            continue
        gate6 = (package.get("evidence") or {}).get("gate6") or {}
        signals.append(_signal("cost_outlier", {"agent": package["ref"], "binding": package["binding"]},
                               occurrences=len(outliers), evidence_ids=[row["id"] for row in outliers],
                               outlier_tokens={row["id"]: row["tokens"] for row in outliers}, train_median_tokens=median,
                               gate6=gate6, level=(package.get("evidence") or {}).get("level"),
                               holdout_confirmation="not_applicable: eval hold-out cases are kept for evaluation",
                               baseline_source=(package.get("evidence") or {}).get("path"),
                               # One deterministic case is enough when it is what keeps the package below silver.
                               actionable=gate6.get("pass") is False, threshold={"ratio": COST_OUTLIER_RATIO}))
    return sorted(signals, key=lambda s: (not s.get("actionable"), s["type"], s["id"]))


# -- generate -------------------------------------------------------------------

def _pool(root: Path, pool_dir: Path | None) -> list[dict]:
    """Reviewable spec patches waiting to be judged: <agent>/<name>.diff with a <name>.json description."""
    directory = Path(pool_dir) if pool_dir else root / POOL_DIR
    patches = []
    for meta_path in sorted(directory.glob("*/*.json")) if directory.is_dir() else []:
        diff_path = meta_path.with_suffix(".diff")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            text = diff_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        patches.append({"agent": meta_path.parent.name, "name": meta_path.stem, "meta": meta, "diff": text,
                        "path": diff_path.relative_to(root).as_posix() if diff_path.is_relative_to(root) else diff_path.name})
    return patches


def _patch_problem(diff: str, agent: str) -> str:
    """A candidate may only change that agent's build spec; never a package, registry or anything else."""
    allowed = f"agents/factory/specs/{agent}/"
    targets = re.findall(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", diff, re.M)
    if not targets:
        return "the patch names no file"
    for target in targets:
        if target == "/dev/null":
            continue
        if not target.startswith(allowed) or ".." in Path(target).parts:
            return f"the patch touches {target}, outside {allowed}"
    return ""


def _pinned(package: dict, registry_entry: dict) -> dict | None:
    built = registry_entry.get("built") or {}
    return built.get("candidate") if built.get("candidate") else None


def generate(signals: list[dict], evidence: dict, root: Path, *, pool_dir: Path | None = None) -> list[dict]:
    """Candidates for actionable signals. Generators read training evidence only."""
    from . import registry

    entries = registry.load(root)["agents"]
    pool = _pool(root, pool_dir)
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(candidate: dict) -> None:
        candidate["id"] = "cand-" + _sha([candidate["kind"], candidate["agent"], candidate.get("change")])[:12]
        if candidate["id"] not in seen:
            seen.add(candidate["id"])
            candidate["signals"] = [candidate["signal"]]
            candidates.append(candidate)
            return
        # The same change answers another signal: one candidate, all of its evidence.
        prior = next(c for c in candidates if c["id"] == candidate["id"])
        prior["signals"].append(candidate["signal"])
        prior["evidence_ids"] = sorted(set(prior["evidence_ids"]) | set(candidate["evidence_ids"]))

    for signal in signals:
        if not signal.get("actionable") or signal.get("policy_required"):
            continue
        if signal["type"] == "unsupported_planner_request":
            # No name is invented: the department and capability are Casey's choice.
            add({"kind": "new_spec_needed", "agent": "unnamed", "signal": signal["id"], "evidence_ids": signal["evidence_ids"],
                 "root_cause": (f"{signal['occurrences']} requests about '{' '.join(signal['key']['terms'])}' were refused by "
                                "shared.planner as request_unsupported: no planned specialist covers them. Where each "
                                "request went afterwards is not recorded."),
                 "change": {"terms": signal["key"]["terms"],
                            "commands": ["python3 tools/aos-agent.py new <department>.<capability> --kind oneshot",
                                         "python3 tools/aos-agent.py build <department>.<capability>"],
                            "note": "name, department and scope are Casey's decision; write the schemas and golden cases "
                                    "from the listed requests first"},
                 "expected_impact": f"would give {signal['occurrences']} recorded refused request(s) a specialist to plan to"})
            continue
        agent_ref = signal["key"].get("agent") or ""
        name = agent_ref.split("@", 1)[0]
        package = evidence["packages"].get(name)
        if not package:
            continue
        failure_class = signal["key"].get("failure_class") or ""
        for patch in pool:
            addresses = set(patch["meta"].get("addresses") or [])
            if patch["agent"] != name or not (addresses & {failure_class, signal["type"]}):
                continue
            add({"kind": "spec_patch", "agent": package["ref"], "signal": signal["id"], "evidence_ids": signal["evidence_ids"],
                 "root_cause": str(patch["meta"].get("root_cause") or patch["meta"].get("summary") or "")[:600],
                 "change": {"patch": patch["path"], "sha256": hashlib.sha256(patch["diff"].encode()).hexdigest()},
                 "diff": patch["diff"], "author": str(patch["meta"].get("author") or "unknown"),
                 "held_out_exposure": "unknown: authored outside M6; evaluation still reports held-out cases separately",
                 "expected_impact": str(patch["meta"].get("expected_impact") or "")[:300]})
        spec = package.get("spec")
        pinned = _pinned(package, entries.get(name) or {})
        if spec and pinned and signal["type"] == "recurring_failure":
            listed = spec["candidates"]
            index = next((i for i, c in enumerate(listed) if c == pinned), None)
            if index is not None and index + 1 < len(listed):
                alternative = listed[index + 1]
                add({"kind": "pin_candidate", "agent": package["ref"], "signal": signal["id"], "evidence_ids": signal["evidence_ids"],
                     "root_cause": f"{failure_class} recurs with the pinned binding {pinned['model']}.{pinned['effort']}; "
                                   "the spec lists a stronger candidate.",
                     "change": {"from": pinned, "to": alternative, "spec": spec["path"] + "/spec.toml"},
                     "held_out_exposure": "none: generated from training evidence only",
                     "expected_impact": "fewer model-side failures, at a higher token cost"})
        if signal["type"] in ("cost_outlier", "cost_regression") and spec:
            cases = {}
            try:
                cases = {case["id"]: case for case in evals.load_cases(load_package(name, root))["cases"]}
            except (evals.EvalError, PackageError):
                pass
            sizes = {cid: len(json.dumps(case["input"], ensure_ascii=False)) for cid, case in cases.items()
                     if cid in {row["id"] for row in package["train"]}}
            outliers = [cid for cid in signal["evidence_ids"] if cid in sizes]
            median_size = _median(list(sizes.values()))
            # Recorded input and output sizes decide the cause; nothing is assumed.
            rows = {row["id"]: row for row in package["train"]}
            big_input = [cid for cid in outliers if median_size and sizes[cid] > 1.5 * median_size]
            if outliers:
                cause = ("the outlier case(s) carry inline input far above the median "
                         + ", ".join(f"{cid}: {sizes[cid]} chars vs median {int(median_size)}" for cid in big_input)
                         if big_input else
                         "the outlier case(s) have ordinary input and output ("
                         + ", ".join(f"{cid}: input {sizes[cid]} chars vs median {int(median_size or 0)}, "
                                     f"output {rows[cid]['output_tokens']} tokens" for cid in outliers)
                         + "), so the extra tokens are not explained by the recorded case; the provider's input accounting for "
                           "that call is the open question")
            else:
                cause = "runtime calls cost more than the recorded acceptance median for the same binding"
            add({"kind": "precise_change", "agent": package["ref"], "signal": signal["id"], "evidence_ids": signal["evidence_ids"],
                 "root_cause": cause,
                 "change": {"file": spec["path"] + "/handler.py",
                            "proposal": ("Bound inline input text before prompt assembly to the manifest's context.max_tokens "
                                         "allowance, as ctx.context() already does for Business Brain references, and mark a "
                                         "reduced source as truncated." if big_input else
                                         "Re-record the outlier case once with exact usage (a single attended live eval of that "
                                         "case) to learn whether the cost repeats before changing the prompt or handler.")},
                 "held_out_exposure": "none: generated from training evidence only",
                 "expected_impact": (("bounded input brings the outlier toward the training median "
                                      f"({int(signal.get('train_median_tokens') or 0)} tokens)") if big_input else
                                     "establishes whether the outlier cost repeats; not an improvement until measured")})
    return candidates


# -- evaluate -------------------------------------------------------------------

def _scratch_copy(root: Path, parent: Path | None) -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="m6-eval-", dir=str(parent) if parent else None))
    shutil.copytree(root / "agents", scratch / "agents", ignore=shutil.ignore_patterns("__pycache__", "dist"))
    return scratch


def _split_metrics(rows: list[dict], ids: set[str]) -> dict:
    chosen = [row for row in rows if row["id"] in ids]
    ran = [row for row in chosen if not row["not_recorded"]]
    called = [row for row in ran if row["model_calls"]]
    tokens = [row["tokens"] for row in called]
    return {"cases": len(chosen), "ran": len(ran), "passed": sum(1 for row in ran if row["passed"]),
            "pass_rate": round(sum(1 for row in ran if row["passed"]) / len(ran), 3) if ran else None,
            "failed": sorted(row["id"] for row in ran if not row["passed"]),
            "failure_classes": dict(sorted(Counter(row["failure_class"] for row in ran if not row["passed"] and row["failure_class"]).items())),
            "safety_failed": sorted(row["id"] for row in ran if row["failures"].get("3")),
            "critical_failed": sorted(row["id"] for row in ran if "critical" in row["tags"] and not row["passed"]),
            "not_recorded": sorted(row["id"] for row in chosen if row["not_recorded"]),
            "model_calls": sum(row["model_calls"] for row in ran), "tokens_total": sum(tokens),
            "tokens_median": _median(tokens), "tokens_p95": _p95(tokens), "all_exact": all(row["exact"] for row in called)}


def _replay(scratch: Path, agent: str) -> dict:
    with _no_model_calls():
        result = evals.evaluate(agent, scratch, live=False, write=False)
    if (result.get("totals") or {}).get("live_calls"):
        raise ImproveError("a replay evaluation reported a live call")
    return result


def _apply(scratch: Path, candidate: dict, root: Path) -> str:
    """Apply one candidate inside the scratch copy and rebuild that package from its spec there."""
    from . import registry

    name = candidate["agent"].split("@", 1)[0]
    if candidate["kind"] == "spec_patch":
        problem = _patch_problem(candidate["diff"], name)
        if problem:
            return problem
        patch_file = scratch / ".m6-candidate.diff"
        patch_file.write_text(candidate["diff"], encoding="utf-8")
        for args in (["--check"], []):
            done = subprocess.run(["git", "apply", *args, "-p1", str(patch_file)], cwd=str(scratch),
                                  capture_output=True, text=True, timeout=60)
            if done.returncode:
                return f"the patch does not apply: {done.stderr.strip()[:300]}"
        patch_file.unlink()
        chosen = (registry.load(scratch)["agents"].get(name) or {}).get("built", {}).get("candidate") or {}
    elif candidate["kind"] == "pin_candidate":
        chosen = candidate["change"]["to"]
    else:
        return "not replayable: no patch to rebuild from"
    try:
        spec = factory.load_spec(name, scratch)
        factory.materialise(spec, chosen if spec.kind != "tool" else {}, scratch)
        gate1 = evals.check(name, scratch)
    except (factory.FactoryError, PackageError) as exc:
        return f"the patched spec does not build: {exc}"
    return "" if gate1["pass"] else "gate 1 fails after the change: " + "; ".join(gate1["problems"])[:300]


def evaluate_candidate(root: Path, candidate: dict, evidence: dict, *, scratch_parent: Path | None = None) -> dict:
    """Before/after on the same recorded replies. Only what both runs replayed is compared."""
    name = candidate["agent"].split("@", 1)[0]
    package = evidence["packages"].get(name) or {}
    train_ids = {row["id"] for row in package.get("train") or []}
    hold_ids = {row["id"] for row in package.get("holdout") or []}
    out: dict[str, Any] = {"method": "replay of recorded model replies in a scratch copy of agents/; zero tokens",
                           "commands": [f"python3 tools/aos-agent.py eval {name}  # before, recorded replies only"]}
    if candidate["kind"] not in ("spec_patch", "pin_candidate"):
        out.update(status="insufficient_evidence",
                   reason="the change is not a replayable spec patch; before/after needs a build and new recordings")
        return out
    if not train_ids:
        out.update(status="insufficient_evidence", reason="the package has no current acceptance evidence to compare with")
        return out
    scratch = _scratch_copy(root, scratch_parent)
    try:
        before = _replay(scratch, name)
        problem = _apply(scratch, candidate, root)
        if problem:
            out.update(status="rejected" if "outside" in problem or "does not" in problem else "insufficient_evidence",
                       reason=problem)
            return out
        after = _replay(scratch, name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    out["commands"].append("apply the candidate to the spec, then: python3 tools/aos-agent.py build "
                           f"{name}  # rebuilds from recorded replies, zero tokens")
    measured = {}
    for split, ids in (("train", train_ids), ("holdout", hold_ids)):
        b_rows = {row["id"]: row for row in before.get("cases") or []}
        a_rows = {row["id"]: row for row in after.get("cases") or []}
        comparable = {cid for cid in ids if cid in b_rows and cid in a_rows
                      and not b_rows[cid]["not_recorded"] and not a_rows[cid]["not_recorded"]}
        measured[split] = {"before": _split_metrics(list(b_rows.values()), ids), "after": _split_metrics(list(a_rows.values()), ids),
                           "comparable_cases": len(comparable),
                           "not_comparable": sorted(ids - comparable),
                           # Token cost only over cases replayed on both sides; an unreplayed case is not "zero tokens".
                           "comparable_tokens": {"before": sum(b_rows[c]["tokens"] for c in comparable),
                                                 "after": sum(a_rows[c]["tokens"] for c in comparable)}}
    verdict = _verdict(measured, candidate)
    if verdict["status"] == "insufficient_evidence":
        # Partial counts from an unreplayable side are not a before/after; only say what could not be compared.
        out.update(verdict, comparison="not shown: the two sides did not replay the same cases",
                   replayed={split: {"before": m["before"]["ran"], "after": m["after"]["ran"], "of": m["before"]["cases"],
                                     "comparable": m["comparable_cases"]} for split, m in measured.items()})
        return out
    out["levels"] = {"before": before.get("level"), "after": after.get("level")}
    out["gate6_pass"] = {"before": (before.get("gates") or {}).get("6", {}).get("pass"),
                         "after": (after.get("gates") or {}).get("6", {}).get("pass")}
    out["measured"] = measured
    out["latency"] = "not compared: replayed latency is recorded, harness time is wall-clock"
    out.update(verdict)
    return out


def _verdict(measured: dict, candidate: dict) -> dict:
    train, hold = measured["train"], measured["holdout"]
    unreplayed = sorted(set(train["not_comparable"]) | set(hold["not_comparable"]))
    if unreplayed:
        return {"status": "insufficient_evidence", "not_comparable": unreplayed,
                "reason": (f"{len(unreplayed)} case(s) have no recorded reply on one side (a changed prompt or model is "
                           "not replayable); outcomes there are unknown, so no before/after is claimed")}
    if train["comparable_cases"] < MIN_COMPARABLE_CASES or hold["comparable_cases"] < MIN_COMPARABLE_HOLDOUT:
        return {"status": "insufficient_evidence", "not_comparable": [],
                "reason": (f"too few comparable cases (training {train['comparable_cases']}, held-out "
                           f"{hold['comparable_cases']}; provisional minimum {MIN_COMPARABLE_CASES} and "
                           f"{MIN_COMPARABLE_HOLDOUT}); no before/after is claimed")}
    def comparable_failed(split, side):
        return set(split[side]["failed"]) - set(split["not_comparable"])
    new_failures = sorted((comparable_failed(train, "after") - comparable_failed(train, "before"))
                          | (comparable_failed(hold, "after") - comparable_failed(hold, "before")))
    new_safety = sorted((set(train["after"]["safety_failed"]) | set(hold["after"]["safety_failed"]))
                        - (set(train["before"]["safety_failed"]) | set(hold["before"]["safety_failed"])))
    fixed = sorted((comparable_failed(train, "before") - comparable_failed(train, "after"))
                   | (comparable_failed(hold, "before") - comparable_failed(hold, "after")))
    tokens_before = train["comparable_tokens"]["before"] + hold["comparable_tokens"]["before"]
    tokens_after = train["comparable_tokens"]["after"] + hold["comparable_tokens"]["after"]
    shift = (tokens_after - tokens_before) / tokens_before if tokens_before else 0.0
    facts = {"fixed_cases": fixed, "new_failures": new_failures, "new_safety_failures": new_safety,
             "tokens_before": tokens_before, "tokens_after": tokens_after, "token_shift": round(shift, 4)}
    if new_failures or new_safety:
        return {"status": "regressed", **facts, "reason": "the change breaks a case that passed before"}
    if fixed and shift <= NOT_COMPARABLE_TOKEN_SHIFT:
        return {"status": "improved", **facts,
                "reason": f"{len(fixed)} failing case(s) now pass; no new failure; tokens {shift:+.1%} on the same replies"}
    if shift < -NOT_COMPARABLE_TOKEN_SHIFT and not fixed:
        return {"status": "improved", **facts, "reason": f"same outcomes at {shift:+.1%} tokens"}
    return {"status": "no_change", **facts, "reason": "no measured difference on the comparable cases"}


# -- rank and report ------------------------------------------------------------

STATUS_RANK = {"improved": 0, "insufficient_evidence": 1, "no_change": 2}
TYPE_WEIGHT = {"recurring_failure": 3, "review_hold": 2, "cost_outlier": 2, "cost_regression": 2, "unsupported_planner_request": 1}


def rank(candidates: list[dict], signals: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """(proposed, rejected). Measured improvements first; never more than MAX_PROPOSALS proposed."""
    keep, rejected = [], []
    for candidate in candidates:
        status = (candidate.get("evaluation") or {}).get("status")
        if status in ("regressed", "rejected"):
            rejected.append(candidate)
            continue
        strength = max(TYPE_WEIGHT.get((signals.get(sid) or {}).get("type"), 1) * max(1, int((signals.get(sid) or {}).get("occurrences") or 1))
                       for sid in candidate.get("signals") or [candidate["signal"]])
        candidate["score"] = round(strength * (2.0 if status == "improved" else 1.0), 3)
        keep.append(candidate)
    keep.sort(key=lambda c: (STATUS_RANK.get((c.get("evaluation") or {}).get("status"), 9), -c["score"], c["id"]))
    proposed = keep[:MAX_PROPOSALS]
    for index, candidate in enumerate(proposed, start=1):
        candidate["rank"] = index
    return proposed, rejected + [dict(c, not_proposed="ranked below the three-proposal limit") for c in keep[MAX_PROPOSALS:]]


def _candidate_diff(candidate: dict, root: Path) -> str:
    if candidate["kind"] == "spec_patch":
        return candidate["diff"]
    if candidate["kind"] == "pin_candidate":
        path = root / candidate["change"]["spec"]
        before = path.read_text(encoding="utf-8").splitlines(keepends=True)
        blocks = re.split(r"(?m)^(?=\[\[factory\.candidates\]\])", "".join(before))
        head, rest = blocks[0], blocks[1:]
        target = next((b for b in rest if f'model = "{candidate["change"]["to"]["model"]}"' in b
                       and f'effort = "{candidate["change"]["to"]["effort"]}"' in b), None)
        if target is None:
            return ""
        after = head + target + "".join(b for b in rest if b is not target)
        after = after if after.endswith("\n") else after + "\n"
        rel = candidate["change"]["spec"]
        return "".join(difflib.unified_diff(before, after.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
    return ""


def _proposal_markdown(candidate: dict, signal: dict, run_id: str) -> str:
    ev = candidate.get("evaluation") or {}
    lines = [f"# M6 proposal {candidate['rank']}: {candidate['kind']} for {candidate['agent']}",
             "> Generated by the Agent Factory self-improvement (M6). Propose-only: nothing was changed, "
             "promoted, sent or scheduled. Not organisational authority.", "",
             f"- Run: `{run_id}` · candidate `{candidate['id']}` · signal `{signal.get('id')}` ({signal.get('type')})",
             f"- Affected: `{candidate['agent']}`" + (f" package `{(signal.get('package_sha256') or [''])[0][:12]}`"
                                                      if signal.get("package_sha256") else ""),
             f"- Evidence: " + ", ".join(f"`{e}`" for e in candidate["evidence_ids"][:12]),
             f"- Root cause: {candidate['root_cause']}",
             f"- Expected impact: {candidate.get('expected_impact') or 'not stated'}",
             f"- Evaluation: **{ev.get('status')}** — {ev.get('reason', '')}", ""]
    if ev.get("status") == "insufficient_evidence":
        lines += ["## Before/after: INSUFFICIENT EVIDENCE", "",
                  "No before/after comparison is shown: the evidence does not support one. "
                  "This proposal is unmeasured; it is not an improvement claim.", ""]
        for split, row in (ev.get("replayed") or {}).items():
            lines.append(f"- {split}: {row['comparable']} of {row['of']} case(s) replayed on both sides "
                         f"(before {row['before']}, after {row['after']})")
        if ev.get("not_comparable"):
            lines.append("- Not comparable: " + ", ".join(f"`{c}`" for c in ev["not_comparable"][:20]))
        lines.append("")
    for split in ("train", "holdout") if ev.get("measured") else ():
        m = (ev.get("measured") or {}).get(split)
        if not m:
            continue
        b, a = m["before"], m["after"]
        lines += [f"## {split} cases ({m['comparable_cases']} comparable)", "",
                  "| measure | before | after |", "|---|---|---|",
                  f"| passed | {b['passed']}/{b['ran']} | {a['passed']}/{a['ran']} |",
                  f"| failed | {', '.join(b['failed']) or '-'} | {', '.join(a['failed']) or '-'} |",
                  f"| safety failed | {', '.join(b['safety_failed']) or '-'} | {', '.join(a['safety_failed']) or '-'} |",
                  f"| tokens (total, exact) | {b['tokens_total']} | {a['tokens_total']} |",
                  f"| tokens p95 | {b['tokens_p95']} | {a['tokens_p95']} |",
                  f"| not recorded | {len(b['not_recorded'])} | {len(a['not_recorded'])} |", ""]
    if ev.get("levels"):
        lines.append(f"Acceptance level on replay: {ev['levels']['before']} → {ev['levels']['after']}.")
    lines += ["", "## Change", "", "```json", json.dumps(candidate["change"], indent=1, ensure_ascii=False), "```", "",
              "## To apply (Casey's decision)", "",
              "Apply the diff to the build spec, then rebuild and review with the existing Factory commands; promotion "
              "stays `aos-agent.py promote` with Casey's approval:", ""]
    lines += [f"    {command}" for command in ev.get("commands") or []]
    return "\n".join(lines) + "\n"


def run(root: Path | None = None, *, out_dir: Path | None = None, client_scope: str = "global",
        pool_dir: Path | None = None, now: dt.datetime | None = None, scratch_parent: Path | None = None,
        write: bool = True) -> dict:
    """Detect, generate, evaluate, rank and write proposals. Changes nothing that runs."""
    root = repo_root(root)
    if any(part.startswith("example_dealership") for part in root.parts):
        raise ImproveError("M6 does not run inside a Example Dealership workspace")
    out_base = Path(out_dir) if out_dir else root / OUTPUT_DIR
    for protected in PROTECTED:
        if out_base.resolve().is_relative_to((root / protected).resolve()):
            raise ImproveError(f"proposals cannot be written into {protected.as_posix()}")
    if out_base.resolve().is_relative_to((root / "agents").resolve()):
        raise ImproveError("proposals cannot be written under agents/")
    guard_before = _tree_digest(root, PROTECTED)
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)

    with _no_model_calls():
        evidence = collect(root, client_scope=client_scope)
        signals = detect(evidence, root)
        candidates = generate(signals, evidence, root, pool_dir=pool_dir)
        signal_index = {signal["id"]: signal for signal in signals}
        # Evaluate the strongest signals' candidates first; the rest are listed, not evaluated.
        order = sorted(candidates, key=lambda c: (-TYPE_WEIGHT.get(signal_index[c["signal"]]["type"], 1)
                                                  * int(signal_index[c["signal"]].get("occurrences") or 1), c["id"]))
        for index, candidate in enumerate(order):
            candidate["evaluation"] = (evaluate_candidate(root, candidate, evidence, scratch_parent=scratch_parent)
                                       if index < MAX_EVALUATED else
                                       {"status": "insufficient_evidence", "reason": "not evaluated: beyond the evaluation limit"})
        proposed, rejected = rank(candidates, signal_index)

    guard_after = _tree_digest(root, PROTECTED)
    if guard_after != guard_before:
        raise ImproveError("an active package, spec, registry, job or ledger changed during an M6 run")

    measurements = [{"candidate": c["id"], "evaluation": c.get("evaluation")} for c in proposed + rejected]
    run_key = {"version": M6_VERSION, "scope": client_scope, "signals": signals, "proposed": [c["id"] for c in proposed],
               "measurements": measurements}
    run_id = f"m6-{now.strftime('%Y%m%d')}-{_sha(run_key)[:10]}"
    jobs = evidence["jobs"]
    fixture_jobs = sorted(job["id"] for split in jobs.values() for job in split if job.get("fixture") is True)
    report = {
        "m6": M6_VERSION, "run_id": run_id, "generated_at": now.isoformat().replace("+00:00", "Z"),
        "client_scope": client_scope, "propose_only": True, "state": "proposed",
        "provenance": {"generator": REPORT_GENERATOR, "m6": M6_VERSION, "authoritative": False,
                       "kind": "generated proposal for review; not a canonical record or organisational authority",
                       "source_root": str(root), "active_digest": _active_digest(root),
                       "contains_fixture_evidence": bool(fixture_jobs), "fixture_jobs": fixture_jobs},
        "thresholds": {"status": THRESHOLD_STATUS, "min_occurrences": MIN_OCCURRENCES, "min_rate": MIN_RATE,
                       "cost_min_samples": COST_MIN_SAMPLES, "cost_regression_ratio": COST_REGRESSION_RATIO,
                       "cost_outlier_ratio": COST_OUTLIER_RATIO, "holdout_job_share": HOLDOUT_JOB_SHARE,
                       "min_comparable_cases": MIN_COMPARABLE_CASES, "min_comparable_holdout": MIN_COMPARABLE_HOLDOUT,
                       "stale_after_days": STALE_AFTER.days},
        "limitations": ["autonomous patch generation is not implemented: spec patches come from the patch pool "
                        "(agents/factory/improvements/patches), written by Casey or an attended workbench session",
                        "prompt or model changes cannot be measured offline: no recorded replies exist for them",
                        "unsupported planner requests record only the refusal, not where the request went next"],
        "evidence": {"jobs_generation": [job["id"] for job in jobs["generation"]], "jobs_holdout": [job["id"] for job in jobs["holdout"]],
                     "jobs_excluded_out_of_scope": evidence["jobs_excluded_out_of_scope"],
                     "packages": {name: {k: p[k] for k in ("ref", "binding", "package_sha256")}
                                  | {"evidence": (p.get("evidence") or {}).get("path"), "level": (p.get("evidence") or {}).get("level"),
                                     "train_cases": len(p["train"]), "holdout_cases": sorted(r["id"] for r in p["holdout"]),
                                     **({"problem": p["evidence_problem"]} if p.get("evidence_problem") else {})}
                                  for name, p in sorted(evidence["packages"].items())},
                     "packages_skipped": evidence["packages_skipped"]},
        "signals": signals,
        "candidates_considered": len(candidates),
        "proposals": [{k: v for k, v in c.items() if k != "diff"} for c in proposed],
        "not_proposed": [{k: v for k, v in c.items() if k != "diff"} for c in rejected],
        "measurement_sha256": _sha(measurements),
        "guarantees": {"model_calls": 0, "live_calls": 0, "active_state_unchanged": True, "protected_digest": guard_after,
                       "brain_writes": 0, "external_actions": 0, "scheduled": False},
        "token_usage": "no agent invocation (replay only)",
    }
    if write:
        folder = out_base / run_id
        folder.mkdir(parents=True, exist_ok=True)
        for candidate in proposed:
            n = candidate["rank"]
            diff = _candidate_diff(candidate, root)
            entry = next(p for p in report["proposals"] if p["id"] == candidate["id"])
            # File names are relative to the run folder, so a report reads the same wherever it is written.
            entry["files"] = {"proposal": f"proposal-{n}.md"}
            (folder / f"proposal-{n}.md").write_text(_proposal_markdown(candidate, signal_index[candidate["signal"]], run_id), encoding="utf-8")
            if diff:
                (folder / f"candidate-{n}.diff").write_text(diff, encoding="utf-8")
                entry["files"]["diff"] = f"candidate-{n}.diff"
        (folder / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        report["report_path"] = (folder / "report.json").as_posix()
    return report


# -- morning brief --------------------------------------------------------------

RUN_ID_RE = re.compile(r"m6-[0-9]{8}-[0-9a-f]{10}")
PROPOSAL_FILE_RE = re.compile(r"(proposal|candidate)-[1-3]\.(md|diff)")
FIXTURE_ROOT_MARKER = ".m6-fixture-root"   # only a fixture root may show a report built from fixture evidence


def _report_problem(root: Path, folder: Path, path: Path, report: Any, client_scope: str) -> str:
    """Why a report must not be shown; empty when it may be. Every check fails closed."""
    if not isinstance(report, dict):
        return "not a JSON object"
    proposals = report.get("proposals")
    provenance = report.get("provenance")
    if report.get("propose_only") is not True or report.get("state") != "proposed":
        return "not a propose-only report"
    if not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS or not all(isinstance(p, dict) for p in proposals):
        return f"proposals missing, malformed or over the limit of {MAX_PROPOSALS}"
    if (report.get("guarantees") or {}).get("model_calls") != 0:
        return "report does not record zero model calls"
    if report.get("run_id") != folder.name or not RUN_ID_RE.fullmatch(folder.name):
        return "run id does not match its folder"
    if report.get("client_scope") != client_scope:
        return f"report scope {report.get('client_scope')!r} is not {client_scope!r}"
    if not isinstance(provenance, dict) or provenance.get("generator") != REPORT_GENERATOR or provenance.get("authoritative") is not False:
        return "provenance missing or not an M6 generated proposal"
    if Path(str(provenance.get("source_root") or "")).resolve() != root.resolve():
        return "report was built from another root's evidence"
    if provenance.get("contains_fixture_evidence") is not False and not (root / FIXTURE_ROOT_MARKER).is_file():
        return "report contains fixture evidence"
    for proposal in proposals:
        rank = proposal.get("rank")
        if not isinstance(rank, int) or not 1 <= rank <= MAX_PROPOSALS:
            return "a proposal has no valid rank"
        for name in (proposal.get("files") or {}).values():
            target = folder / str(name)
            if not PROPOSAL_FILE_RE.fullmatch(str(name)) or target.is_symlink() or not target.is_file() \
                    or not target.resolve().is_relative_to(folder.resolve()):
                return f"proposal file is unsafe or missing: {str(name)[:60]!r}"
    return ""


def read_latest_report(root: Path, *, client_scope: str = "global", now: dt.datetime | None = None,
                       out_dir: Path | None = None) -> dict:
    """The newest showable M6 report for the morning brief, as data; never raises.

    Returns {"status": "none" | "fresh" | "stale" | "rejected", ...}. "rejected" lists why each
    report was refused (malformed, unreadable, out of scope, foreign root, fixture, unsafe path).
    A shown report is generated proposal material, never an authoritative record.
    """
    rejected: list[dict] = []
    try:
        root = Path(root).resolve()
        base = Path(out_dir) if out_dir else root / OUTPUT_DIR
        if base.is_symlink() or not base.is_dir() or not base.resolve().is_relative_to(root):
            return {"status": "none", "rejected": []}
        found = []
        for folder in sorted(base.iterdir()):
            path = folder / "report.json"
            if not folder.name.startswith("m6-") or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                if path.is_symlink():
                    raise ValueError("report is a symlink")
                text = path.read_text(encoding="utf-8")
                report = json.loads(text)
                problem = _report_problem(root, folder, path, report, client_scope)
            except (OSError, ValueError, TypeError) as exc:
                problem = f"unreadable: {type(exc).__name__}"
                report, text = None, ""
            if problem:
                rejected.append({"run": folder.name, "reason": problem})
                continue
            found.append((str(report.get("generated_at") or ""), folder.name, path, report, text))
        if not found:
            return {"status": "rejected" if rejected else "none", "rejected": rejected}
        generated_at, _run, path, report, text = max(found, key=lambda row: (row[0], row[1]))
        reasons = []
        when = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00")) if generated_at else None
        current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        if when is None or when.tzinfo is None:
            reasons.append("no valid generation time")
        elif current - when > STALE_AFTER:
            reasons.append(f"generated {(current - when).days} days ago (stale after {STALE_AFTER.days})")
        if report["provenance"].get("active_digest") != _active_digest(root):
            reasons.append("agent packages, specs or registry changed since the report")
        return {"status": "stale" if reasons else "fresh", "stale_reasons": reasons, "path": path, "report": report,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "rejected": rejected}
    except Exception as exc:  # noqa: BLE001 - the optional M6 section never takes the brief down
        return {"status": "rejected", "rejected": rejected + [{"run": "", "reason": f"reader error: {type(exc).__name__}"}]}
