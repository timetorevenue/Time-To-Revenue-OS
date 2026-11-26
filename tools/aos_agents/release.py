"""M7 release readiness: per-agent canary evidence, Gold eligibility, offline staging and a rollback drill.

Nothing here calls a model, writes the live registry or routes work. It reads:

  agents/registry.lock.json         release state, level, package hash, archive hash, history (M4)
  agents/packages/<name>/evidence/  gates 1-6 from the Factory eval harness (M4)
  agents/canary_acceptances.json    Casey's recorded decisions on real canary runs (this module's input)
  <runtime>/queue/jobs/*.json       the aos_jobs record of each run (M1/M5)
  <runtime>/queue/receipts/*.md     the receipt each job wrote, with its sha256 in the job record
  <runtime>/queue/token_ledger.jsonl exact usage per invocation (step6_cost_control)

A real canary counts for one package binding only when every one of those agrees: the decision
says accepted and real, the job finished done in round 0 with no failed or unexpected effect,
the receipt bytes hash to what the job and the decision recorded, the agent's own step ran ok and
was not replayed, the registry shows that exact package hash released to canary or active when the
job was created, and (for a model agent) the ledger rows for that step name the same agent, model
and effort with exact usage. Counts are per (agent, version, package hash, binding) and are never
pooled. One job counts at most once per agent however often it is recorded.

TAS v0.1 levels (TTROS_Agent_Architecture_Factory_Blueprint_2026-11-14, section 6). evals.py
awards bronze and silver; this module adds gold:

  bronze  gates 1-3
  silver  gates 1-6
  gold    gates 1-7, and SEPARATELY GOLD_CANARIES clean real canary runs of the exact binding

Gate 7 (reliability) is the blueprint's: 5 cases x 3 repeats with the same verdict in >= 85% of
repeats; kill -9 mid-run, resumed with 0 duplicate effects and <= 1 repeated model call; a forced
quota/auth failure ends in a truthful ``blocked`` with no fallback to a billed route. A verdict is
the agent's declared decision fields (verdicts.py), compared exactly; never a whole-output hash.
The forced failure must stop after a launched call and be of an accepted evidence class
(GATE7_FORCED_EVIDENCE): an injected stand-in provider proves the runtime path, not the provider,
and does not qualify unless that class is added here by an approved gate change. It is read
from ``evidence/<binding>.reliability.json`` beside the gates 1-6 evidence. reliability.py collects runtime sources and captures separately authorized probes;
missing, altered or simulated provenance fails closed. Canary counts never
stand in for gate 7, and gate 7 never stands in for canaries.

A historical canary counts only when its exact package identity is supported independently of
the acceptance record: the record names a provenance commit; that commit is in this repository's
history before the run, holds the pinned package bytes and a registry entry releasing that exact
hash, and is the newest first-parent commit before the run (or holds the same package). Without
that it is reported as unverified, never counted.

READY means the technical evidence supports the candidate. It is not authorization: release is a
consequential action that aos_authority classifies, and a named approver alone never authorizes it.

Revisit: when TAS levels, the acceptance record shape, the job record or the release states change.
· Last touched: 2026-11-17.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import evals, registry
from .package import AgentPackage, PackageError, content_hash, installed, load_package, repo_root

M7_VERSION = "m7.1"
GOLD_CANARIES = 20
ACCEPTANCES_PATH = Path("agents") / "canary_acceptances.json"
JOBS_DIR = Path("queue") / "jobs"
LEDGER_PATH = Path("queue") / "token_ledger.jsonl"
REAL_KIND = "real_canary"
READY, NOT_READY, BLOCKED = "READY", "NOT_READY", "BLOCKED"
RELEASED_STATES = ("canary", "active")
# Which dependency states each target tolerates: an active agent must not call a less-released one.
DEPENDENCY_STATES = {"shadow": registry.PROMOTION_ORDER, "canary": RELEASED_STATES, "active": ("active",)}
RELIABILITY_SUFFIX = ".reliability.json"
GATE7 = {"cases": 5, "repeats": 3, "same_verdict": 0.85, "max_repeated_calls": 1}
GATE7_STANDARD = ("TAS 0.1 gate 7: 5 cases x 3 repeats, same declared verdict >= 85%; kill -9 mid-run resumes with 0 "
                  "duplicate effects and <= 1 repeated model call; forced post-launch quota/auth failure is a truthful "
                  "blocked, no billed fallback")
# Forced-failure evidence classes that count for gate 7. 'injected_transport' (a stand-in provider on
# the real runtime path) is deliberately absent: adding it is a gate change for Casey to approve.
GATE7_FORCED_EVIDENCE = ("genuine_provider",)
IDENTITY_UNVERIFIED = "package identity unverified"
EXTERNAL_EFFECTS = frozenset({"email.send", "publish", "deploy"})
ISOLATED_SCOPE_RE = re.compile(r"north[\s_-]*shore", re.IGNORECASE)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
NOT_AUTHORIZATION = ("READY means the technical evidence supports the candidate. It is not authorization: the runtime "
                     "permission authority decides a release (it holds 'active' for Casey's explicit command), a named "
                     "approver never does, and nothing here promotes or routes.")


class ReleaseError(RuntimeError):
    """A staging or drill request that cannot proceed as asked."""


# -- small helpers --------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _level_rank(level: str) -> int:
    return registry.LEVEL_ORDER.index(level) if level in registry.LEVEL_ORDER else 0


def _state_rank(state: str) -> int:
    return registry.PROMOTION_ORDER.index(state) if state in registry.PROMOTION_ORDER else -1


def _transport(package: AgentPackage) -> tuple[str, str]:
    return (package.model.get("id", ""), package.model.get("effort", "")) if package.kind != "tool" else ("", "")


def identity(package: AgentPackage) -> dict:
    """Everything a release is pinned to: version, model, effort and the hash of each part."""
    schema_digest = hashlib.sha256()
    for path in sorted((package.directory / "schemas").glob("*.json")):
        schema_digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    model, effort = _transport(package)
    caps = package.manifest.get("capabilities") or {}
    return {
        "agent": package.ref, "name": package.name, "version": package.version, "kind": package.kind,
        "binding": evals.binding(package), "model": model or None, "effort": effort or None,
        "prompt_sha256": package.model.get("prompt_sha256") or None,
        "handler_sha256": _sha256_file(package.directory / "handler.py"),
        "schema_sha256": schema_digest.hexdigest(),
        "package_sha256": package.content_sha256,
        "capabilities": {key: sorted(caps.get(key) or []) for key in ("grants", "proposes", "tools")},
        "compose_calls": sorted((package.manifest.get("compose") or {}).get("calls") or []),
    }


# -- acceptance records -----------------------------------------------------------

def load_acceptances(root: Path) -> tuple[list[dict], list[str]]:
    """The recorded canary decisions, and the problems with any record that cannot be read."""
    path = root / ACCEPTANCES_PATH
    if not path.is_file():
        return [], [f"{ACCEPTANCES_PATH.as_posix()} is missing: no real canary can be credited"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [], [f"{ACCEPTANCES_PATH.as_posix()} is unreadable: {exc}"]
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return [], [f"{ACCEPTANCES_PATH.as_posix()} has no records list"]
    good, problems = [], []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not record.get("job") or not isinstance(record.get("packages"), dict):
            problems.append(f"record {index} has no job or packages and is ignored")
            continue
        good.append(record)
    return good, problems


# -- registry history ---------------------------------------------------------------

def registry_at(entry: dict, when: str) -> dict:
    """The package hash and release state the registry recorded at ``when`` (ISO, UTC)."""
    events = sorted((row for row in entry.get("history") or [] if row.get("at")), key=lambda row: row["at"])
    sha, state, seen = None, None, False
    for row in events:
        if _iso(row["at"]) > _iso(when):
            break
        seen = True
        if row.get("event") == "registered":
            sha, state = row.get("package_sha256"), row.get("state") or state
        elif row.get("event") in ("promoted", "demoted"):
            state = row.get("to") or state
    complete = len(entry.get("history") or []) < registry.HISTORY_KEPT
    return {"package_sha256": sha, "state": state, "known": seen and (sha is not None or complete)}


def _iso(value: str) -> str:
    """Normalise ``...Z`` and ``...+00:00`` so string comparison orders UTC instants."""
    return str(value or "").replace("Z", "+00:00")


# -- runtime evidence ---------------------------------------------------------------

class Runtime:
    """Read-only view of one runtime root's jobs, receipts and token ledger."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._ledger: dict[str, list[dict]] | None = None
        self.ledger_problem = ""

    def job(self, job_id: str) -> tuple[dict | None, str]:
        import aos_jobs

        try:
            return aos_jobs.load(self.root, job_id), ""
        except aos_jobs.JobError as exc:
            return None, str(exc)
        except (OSError, ValueError) as exc:
            return None, f"job record is unreadable: {exc}"

    def ledger(self) -> dict[str, list[dict]] | None:
        """Model-invocation rows by item id, or None when the ledger is missing or unreadable."""
        if self._ledger is not None or self.ledger_problem:
            return self._ledger
        import step6_cost_control

        if not (self.root / LEDGER_PATH).is_file():
            self.ledger_problem = "token ledger is missing"
            return None
        try:
            rows = step6_cost_control._ledger_rows(self.root)
        except step6_cost_control.CostControlError as exc:
            self.ledger_problem = f"token ledger is unreadable: {exc}"
            return None
        index: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("event") == "model_invocation" and row.get("item_id"):
                index.setdefault(str(row["item_id"]).upper(), []).append(row)
        self._ledger = index
        return index

    def usage(self, job_id: str) -> dict | None:
        import step6_cost_control

        if self.ledger() is None:
            return None
        return step6_cost_control.work_item_usage(job_id, root=self.root)

    def fingerprint(self) -> dict:
        """Hashes of every job, receipt and the ledger: proof that nothing here changed them."""
        out: dict[str, str | None] = {}
        for folder in (JOBS_DIR, Path("queue") / "receipts"):
            base = self.root / folder
            if base.is_dir():
                for path in sorted(base.glob("*")):
                    if path.is_file() and path.suffix in (".json", ".md"):
                        out[path.relative_to(self.root).as_posix()] = _sha256_file(path)
        out[LEDGER_PATH.as_posix()] = _sha256_file(self.root / LEDGER_PATH)
        return out


class GitProvenance:
    """Read-only questions to this checkout's git history: which package bytes and registry state a commit held."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._memo: dict[tuple, Any] = {}

    def _git(self, *args: str, binary: bool = False):
        try:
            done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        return done.stdout if binary else done.stdout.decode("utf-8", "replace").strip()

    def _cached(self, key: tuple, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def _commit(self, ref: str) -> tuple[str, datetime] | None:
        out = self._git("show", "-s", "--format=%H %cI", f"{ref}^{{commit}}")
        if not out:
            return None
        sha, when = out.split()
        return sha, datetime.fromisoformat(when)

    def _package_sha(self, commit: str, name: str) -> str | None:
        data = self._git("archive", "--format=tar", commit, f"agents/packages/{name}", binary=True)
        if not data:
            return None
        with tempfile.TemporaryDirectory(prefix="m7-provenance-") as scratch:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                archive.extractall(scratch, filter="data")
            directory = Path(scratch) / "agents" / "packages" / name
            return content_hash(directory) if directory.is_dir() else None

    def _registry_entry(self, commit: str, name: str) -> dict:
        text = self._git("show", f"{commit}:{registry.REGISTRY_PATH.as_posix()}")
        try:
            return (json.loads(text).get("agents") or {}).get(name) or {} if text else {}
        except ValueError:
            return {}

    def verify(self, name: str, claim: dict, provenance: dict | None, created_at: str) -> list[str]:
        """Problems with the claim that this exact package ran at ``created_at``; empty when supported."""
        commit = str((provenance or {}).get("commit") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return ["the acceptance record names no full provenance commit"]
        if self._cached(("git",), lambda: self._git("rev-parse", "--git-dir")) is None:
            return ["no git history is available to check the provenance commit"]
        info = self._cached(("commit", commit), lambda: self._commit(commit))
        if info is None:
            return [f"provenance commit {commit[:12]} is not in this repository"]
        try:
            run = datetime.fromisoformat(_iso(created_at))
        except ValueError:
            return ["the job has no valid creation time"]
        problems = []
        if info[1] > run:
            problems.append(f"provenance commit {commit[:12]} is later than the run")
        if self._cached(("ancestor", commit), lambda: self._git("merge-base", "--is-ancestor", commit, "HEAD")) is None:
            problems.append(f"provenance commit {commit[:12]} is not in this checkout's history")
        held = self._cached(("package", commit, name), lambda: self._package_sha(commit, name))
        if held != claim.get("package_sha256"):
            problems.append(f"the package at {commit[:12]} hashes to {str(held)[:12]}, not the pinned {str(claim.get('package_sha256'))[:12]}")
        entry = self._cached(("registry", commit, name), lambda: self._registry_entry(commit, name))
        if (entry.get("package_sha256"), entry.get("version"), entry.get("binding")) != \
                (claim.get("package_sha256"), claim.get("version"), claim.get("binding")):
            problems.append(f"the registry at {commit[:12]} does not record this exact package and binding")
        if entry.get("state") not in RELEASED_STATES:
            problems.append(f"the registry at {commit[:12]} has {name} {entry.get('state') or 'unregistered'}, not released")
        newest = self._cached(("before", created_at),
                              lambda: self._git("rev-list", "-1", "--first-parent", f"--before={_iso(created_at)}", "HEAD"))
        if not newest:
            problems.append("no commit in this history precedes the run")
        elif newest != commit and self._cached(("package", newest, name), lambda: self._package_sha(newest, name)) \
                != claim.get("package_sha256"):
            problems.append(f"the newest commit before the run ({newest[:12]}) holds a different {name} package")
        return problems


def _only_unverified(row: dict) -> bool:
    return bool(row["excluded"]) and not row["safety"] and all(r.startswith(IDENTITY_UNVERIFIED) for r in row["excluded"])


def authorization(root: Path, agent_ref: str, target: str, *, release_command=None) -> dict:
    """The runtime permission authority's verdict on releasing ``agent_ref`` to ``target``.

    A release is classified by aos_authority. Only a request-local authenticated command
    can authorize it; an approver name or serialized metadata cannot.
    """
    import aos_authority

    action = f"AGENT_RELEASE_{str(target).upper()}"
    verdict = aos_authority.action_authorization(Path(root), None, action=action, target=str(agent_ref), release_command=release_command)
    return {"action": action, "target": str(agent_ref), "authorized": verdict.get("authorized") is True,
            "basis": verdict.get("basis"), "detail": verdict.get("detail"),
            **({"operator": verdict["operator"], "package_sha256": verdict.get("package_sha256")} if verdict.get("operator") else {})}


def _ledger_rows_for(rows: list[dict], agent_ref: str) -> list[dict]:
    return [row for row in rows if str(row.get("surface") or "").endswith(f":{agent_ref}")]


def _row_tokens(row: dict) -> int | None:
    amount = (row.get("fuse") or {}).get("invocation_tokens") if isinstance(row.get("fuse"), dict) else None
    return amount if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0 else None


def _job_problems(job: dict, record: dict, runtime: Runtime, packages: dict[str, AgentPackage | None]) -> dict:
    """Job-level reasons a recorded run is not a clean real canary, and safety blockers it shows."""
    reasons: list[str] = []
    safety: list[str] = []
    job_id = job["id"]
    if record.get("kind") != REAL_KIND:
        reasons.append(f"recorded as {record.get('kind') or 'unspecified'}, not {REAL_KIND}")
    if record.get("decision") != "accepted":
        reasons.append(f"acceptance decision is {record.get('decision') or 'missing'}, not accepted")
    if record.get("decision") == "accepted" and not str(record.get("accepted_by") or "").strip():
        reasons.append("no named acceptor")
    for marker in ("simulated", "fixture", "synthetic"):
        if job.get(marker) or (job.get("executor") or {}).get(marker):
            reasons.append(f"job is marked {marker}")
    if job.get("status") != "done":
        reasons.append(f"job status is {job.get('status')}, not done")
    if job.get("failure_class"):
        reasons.append(f"job failure_class {job['failure_class']}")
    if int(job.get("round") or 0) or job.get("clarifications"):
        reasons.append("job was resumed or revised (round > 0): not a clean first-pass run")
    steps = (job.get("result") or {}).get("steps") or []
    if any(step.get("replayed") for step in steps):
        reasons.append("a step was replayed, not executed")
    usage = job.get("usage") or {}
    if any(str(value).startswith("replay-") for value in usage.get("invocation_ids") or []):
        reasons.append("usage names replayed invocations")

    # Receipt: the bytes on disk must hash to what the job and the decision recorded.
    receipt = next((row for row in job.get("receipts") or [] if int(row.get("round") or 0) == 0), None)
    receipt_view = {"path": None, "sha256": None}
    if not receipt:
        reasons.append("job has no round-0 receipt")
    else:
        receipt_view = {"path": receipt.get("path"), "sha256": receipt.get("sha256")}
        path = (runtime.root / str(receipt.get("path") or "")).resolve()
        actual = _sha256_file(path) if path.is_relative_to(runtime.root) else None
        if actual is None:
            reasons.append(f"receipt is missing: {receipt.get('path')}")
        elif actual != receipt.get("sha256"):
            reasons.append("receipt bytes do not match the sha256 in the job record")
        elif record.get("receipt_sha256") and record["receipt_sha256"] != actual:
            reasons.append("receipt does not match the sha256 in the acceptance record")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("PASS\n") or f"Job: {job_id}\n" not in text:
                reasons.append("receipt does not report PASS for this job")
        if not record.get("receipt_sha256"):
            reasons.append("acceptance record pins no receipt sha256")

    # Effects: nothing failed; anything held must be what the acceptance expected; every effect declared.
    expected_held = set(record.get("expected_held") or [])
    declared = set().union(*(pkg.proposes for pkg in packages.values() if pkg is not None)) if packages else set()
    for effect in job.get("effects") or []:
        status, action = effect.get("status"), effect.get("action")
        if status in ("failed", "error"):
            reasons.append(f"effect {action} {status}")
        elif status == "held" and action not in expected_held:
            reasons.append(f"effect {action} was held, which the acceptance did not expect")
        elif status not in ("held", "done", "performed"):
            reasons.append(f"effect {action} has status {status}")
        if effect.get("effect") and effect["effect"] not in declared:
            safety.append(f"capability violation: effect {effect['effect']} is not declared by any package in the run")

    # Context: sources must resolve, carry a content hash, and stay inside one granted, non-isolated scope.
    context = job.get("brain_context_used") or []
    scopes = {str(row.get("client_scope") or "") for row in context}
    granted = {g.split(":", 1)[1] for pkg in packages.values() if pkg is not None for g in pkg.grants if g.startswith("brain.read:")}
    for row in context:
        if not str(row.get("path") or "").startswith("business_brain:") or not SHA256_RE.fullmatch(str(row.get("content_sha256") or "")):
            reasons.append(f"context row has no valid source reference or content hash: {row.get('path')!r}")
    if len(scopes) > 1:
        safety.append(f"cross-client context in one run: scopes {sorted(scopes)}")
    for scope in sorted(scopes):
        if ISOLATED_SCOPE_RE.search(scope):
            safety.append(f"isolation violation: context scope {scope!r} is a separate client system")
        elif scope and granted and scope not in granted:
            safety.append(f"capability violation: read scope {scope!r} is not granted to any package in the run")
    read_paths = {row.get("path") for row in context}
    for named in ((job.get("executor") or {}).get("input") or {}).get("records") or []:
        ref = named.get("ref") if isinstance(named, dict) else named
        if ref and ref not in read_paths:
            reasons.append(f"named source {ref} was not the record read")

    # Accounting: exact usage, and the canonical ledger aggregate agrees with the job.
    if not usage.get("exact"):
        reasons.append("job usage is not exact")
    ledger_rows: list[dict] = []
    totals = runtime.usage(job_id)
    if totals is None:
        reasons.append(f"ledger evidence unavailable: {runtime.ledger_problem}")
    else:
        ledger_rows = (runtime.ledger() or {}).get(job_id.upper(), [])
        if totals.get("unknown_invocations") or totals.get("accounting_unknown_since_reset"):
            reasons.append("ledger has unknown usage for this job")
        if int(usage.get("model_calls") or 0) and totals.get("known_tokens") != usage.get("total_tokens"):
            reasons.append(f"ledger total {totals.get('known_tokens')} differs from job usage {usage.get('total_tokens')}")
        if sorted(usage.get("invocation_ids") or []) != sorted({str(row.get("invocation_id")) for row in ledger_rows}):
            reasons.append("ledger invocations differ from the job's invocation ids")
    return {"reasons": reasons, "safety": safety, "receipt": receipt_view, "ledger_rows": ledger_rows,
            "ledger_tokens": (totals or {}).get("known_tokens")}


def canary_evidence(root: Path | None = None, runtime_root: Path | None = None) -> dict:
    """Assess every recorded canary decision, one row per (job, agent). Deterministic, read-only."""
    root = repo_root(root)
    runtime = Runtime(runtime_root or root)
    records, problems = load_acceptances(root)
    try:
        entries = registry.load(root)["agents"]
    except registry.RegistryError as exc:
        entries, problems = {}, problems + [str(exc)]
    provenance = GitProvenance(root)
    rows: list[dict] = []
    duplicates: list[dict] = []
    counted_jobs: dict[tuple[str, str], str] = {}
    claimed_invocations: dict[str, str] = {}
    for record in records:
        job_id = str(record["job"])
        names = sorted(record["packages"])
        packages: dict[str, AgentPackage | None] = {}
        for name in names:
            try:
                packages[name] = load_package(name, root)
            except PackageError:
                packages[name] = None
        job, missing = runtime.job(job_id)
        job_level = _job_problems(job, record, runtime, packages) if job else \
            {"reasons": [f"job record unavailable: {missing}"], "safety": [], "receipt": {"path": None, "sha256": None},
             "ledger_rows": [], "ledger_tokens": None}
        steps = {step.get("agent"): step for step in ((job or {}).get("result") or {}).get("steps") or []}
        by_step = {row.get("agent"): row for row in ((job or {}).get("usage") or {}).get("by_step") or []}
        for name in names:
            claim = record["packages"][name] or {}
            ref = f"{name}@{claim.get('version')}"
            reasons = list(job_level["reasons"])
            entry = entries.get(name) or {}
            step = steps.get(ref)
            if job and (not step or step.get("status") != "ok"):
                reasons.append(f"{ref} did not run ok in this job" if step else f"{ref} has no step in this job")
            if not entry:
                reasons.append(f"{name} is not registered")
            elif job:
                then = registry_at(entry, job.get("created_at") or "")
                if not then["known"]:
                    reasons.append("registry history does not cover the run time")
                else:
                    if then["package_sha256"] != claim.get("package_sha256"):
                        reasons.append("the package registered at run time is not the one the acceptance names")
                    if then["state"] not in RELEASED_STATES:
                        reasons.append(f"{name} was {then['state']} at run time, not released to canary or active")
            package = packages.get(name)
            rows_for_agent = _ledger_rows_for(job_level["ledger_rows"], ref)
            if claim.get("binding") == "tool":
                if rows_for_agent:
                    reasons.append("a tool agent made model calls")
            elif job:
                usage_row = by_step.get(ref)
                if not usage_row or not usage_row.get("exact"):
                    reasons.append(f"no exact usage row for {ref}")
                if not rows_for_agent and totals_available(job_level):
                    reasons.append(f"no ledger row names {ref}")
                for row in rows_for_agent:
                    selection = row.get("model_selection") or {}
                    model = row.get("actual_model") or selection.get("actual_model")
                    effort = selection.get("reasoning_effort")
                    if f"{model}.{effort}.{evals.TRANSPORT}" != claim.get("binding"):
                        reasons.append(f"ledger shows {model}/{effort}, not the binding {claim.get('binding')}")
                    if _row_tokens(row) is None:
                        reasons.append(f"ledger row {row.get('invocation_id')} has no exact token count")
                if usage_row and rows_for_agent and sum(_row_tokens(r) or 0 for r in rows_for_agent) != usage_row.get("tokens"):
                    reasons.append("ledger tokens for this step differ from the job's step usage")
            # Identity is checked last and only for an otherwise clean row: the job record names no package hash.
            identity_check = {"verified": False, "commit": (record.get("provenance") or {}).get("commit"), "problems": []}
            if job and not reasons and not job_level["safety"]:
                found = provenance.verify(name, claim, record.get("provenance"), job.get("created_at") or "")
                attested = (step or {}).get("execution_identity") or {}
                if any(attested.get(k) != v for k, v in {"agent": ref, "package_sha256": claim.get("package_sha256"),
                                                        "binding": claim.get("binding")}.items()):
                    found.append("execution receipt has no matching package attestation; historical inference is insufficient")
                else:
                    receipt_path = runtime.root / str(job_level["receipt"]["path"])
                    lines = receipt_path.read_text().splitlines()
                    identities = []
                    for line in lines:
                        if line.startswith("Execution identity: "):
                            try:
                                identities.append(json.loads(line[len("Execution identity: "):]))
                            except ValueError:
                                pass
                    if not any(all(i.get(k) == attested.get(k) for k in ("agent", "package_sha256", "binding"))
                               and not i.get("replayed") for i in identities):
                        found.append("receipt bytes do not attest this execution identity")
                identity_check.update(verified=not found, problems=found)
                reasons += [f"{IDENTITY_UNVERIFIED}: {problem}" for problem in found]
            key = (job_id, name)
            row = {
                "record": record.get("id") or job_id, "job": job_id, "agent": name, "version": claim.get("version"),
                "package_sha256": claim.get("package_sha256"), "binding": claim.get("binding"),
                "decision": record.get("decision"), "kind": record.get("kind"), "accepted_by": record.get("accepted_by"),
                "source": record.get("source"), "receipt": job_level["receipt"],
                "ledger_invocations": sorted(str(r.get("invocation_id")) for r in rows_for_agent),
                "tokens": sum(_row_tokens(r) or 0 for r in rows_for_agent) if rows_for_agent else 0,
                "safety": list(job_level["safety"]), "excluded": sorted(set(reasons)), "identity": identity_check,
            }
            if key in counted_jobs:
                row["excluded"] = [f"duplicate: job already assessed for {name} by record {counted_jobs[key]}"]
                duplicates.append(row)
                continue
            for invocation in row["ledger_invocations"]:
                owner = claimed_invocations.get(invocation)
                if owner and owner != job_id:
                    row["excluded"].append(f"ledger invocation {invocation} already counted for {owner}")
            counted_jobs[key] = row["record"]
            if not row["excluded"] and not row["safety"]:
                for invocation in row["ledger_invocations"]:
                    claimed_invocations[invocation] = job_id
            row["eligible"] = not row["excluded"] and not row["safety"]
            rows.append(row)
    return {"rows": rows, "duplicates": duplicates, "problems": problems,
            "ledger": {"available": runtime.ledger() is not None, "problem": runtime.ledger_problem}}


def totals_available(job_level: dict) -> bool:
    return job_level.get("ledger_tokens") is not None


# -- readiness ------------------------------------------------------------------------

def reliability_path(package: AgentPackage) -> Path:
    return package.directory / evals.EVIDENCE_DIR / f"{evals.binding(package)}{RELIABILITY_SUFFIX}"


def _exact_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def gate7(package: AgentPackage, root: Path) -> dict:
    """TAS gate 7 from recorded reliability evidence for this exact package. Missing evidence fails."""
    path = reliability_path(package)
    out: dict[str, Any] = {"pass": False, "standard": GATE7_STANDARD, "evidence": path.relative_to(root).as_posix(),
                           "evidence_sha256": _sha256_file(path), "problems": []}
    problems = out["problems"]
    if not path.is_file():
        problems.append("no reliability evidence for this binding (gate 7 has not been run)")
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"reliability evidence is unreadable: {exc}")
        return out
    if not isinstance(data, dict):
        problems.append("reliability evidence is not an object")
        return out
    for marker in ("simulated", "fixture", "synthetic"):
        if data.get(marker):
            problems.append(f"reliability evidence is marked {marker}")
    if data.get("agent") != package.ref or data.get("binding") != evals.binding(package) \
            or data.get("package_sha256") != package.content_sha256 \
            or (data.get("prompt_sha256") or None) != (package.model.get("prompt_sha256") or None):
        problems.append("reliability evidence is for a different package, prompt or binding")
    try:
        from .reliability import verify
        verify(data, package, path, root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration) as exc:
        problems.append(f"reliability provenance invalid: {exc}")
    modes = ("live",) if package.kind != "tool" else ("live", "deterministic")
    if data.get("mode") not in modes:
        problems.append(f"repeats ran in {data.get('mode')!r} mode; a {package.kind} agent needs {' or '.join(modes)} "
                        "(replayed replies cannot show consistency)")

    repeats = [row for row in data.get("repeats") or [] if isinstance(row, dict)]
    cases = {str(row.get("case") or "") for row in repeats} - {""}
    total = agreeing = 0
    for row in repeats:
        verdicts = [str(v) for v in row.get("verdicts") or [] if str(v)]
        if len(verdicts) < GATE7["repeats"]:
            problems.append(f"case {row.get('case')!r} has {len(verdicts)} repeat(s); {GATE7['repeats']} needed")
        total += len(verdicts)
        agreeing += max((verdicts.count(v) for v in set(verdicts)), default=0)
    if len(cases) < GATE7["cases"] or len(cases) != len(repeats):
        problems.append(f"{len(cases)} distinct repeated case(s); {GATE7['cases']} needed")
    out["same_verdict"] = round(agreeing / total, 3) if total else None
    if not total or agreeing / total < GATE7["same_verdict"]:
        problems.append(f"same verdict in {out['same_verdict']} of repeats; {GATE7['same_verdict']} needed")

    kill = data.get("kill_resume") if isinstance(data.get("kill_resume"), dict) else None
    if kill is None:
        problems.append("no kill -9 / resume probe recorded")
    else:
        duplicates, repeated = _exact_int(kill.get("duplicate_effects")), _exact_int(kill.get("repeated_model_calls"))
        if kill.get("signal") != "SIGKILL" or kill.get("resumed") is not True:
            problems.append("kill probe did not kill -9 mid-run and resume")
        if duplicates != 0:
            problems.append(f"resume produced {kill.get('duplicate_effects')!r} duplicate effect(s); 0 allowed")
        if repeated is None or repeated > GATE7["max_repeated_calls"]:
            problems.append(f"resume repeated {kill.get('repeated_model_calls')!r} model call(s); at most {GATE7['max_repeated_calls']}")

    forced = data.get("forced_failure") if isinstance(data.get("forced_failure"), dict) else None
    if forced is None:
        problems.append("no forced quota/auth failure probe recorded")
    else:
        if forced.get("kind") not in ("quota", "auth"):
            problems.append(f"forced failure kind {forced.get('kind')!r} is not quota or auth")
        if forced.get("outcome") != "blocked" or forced.get("truthful") is not True:
            problems.append("forced failure did not end in a truthful blocked")
        if forced.get("billed_fallback") is not False:
            problems.append("forced failure fell back (or may have fallen back) to a billed route")
        if forced.get("post_launch") is not True:
            problems.append("forced failure did not stop after a launched model call")
        if forced.get("evidence_class") not in GATE7_FORCED_EVIDENCE:
            problems.append(f"forced failure evidence class {forced.get('evidence_class')!r} does not qualify "
                            f"(accepted: {', '.join(GATE7_FORCED_EVIDENCE)})")
    out["pass"] = not problems
    return out


def evaluate(agent_ref: str, root: Path | None = None, *, runtime_root: Path | None = None, target: str = "active",
             evidence: dict | None = None) -> dict:
    """Readiness of one installed package for ``target``. Read-only; never raises for a bad package."""
    root = repo_root(root)
    runtime = Runtime(runtime_root or root)
    if target not in registry.REQUIRED_LEVEL:
        raise ReleaseError(f"target must be one of {', '.join(registry.REQUIRED_LEVEL)}")
    required = registry.REQUIRED_LEVEL[target]
    report: dict[str, Any] = {"agent": str(agent_ref), "target_state": target, "required_level": required,
                              "decision": BLOCKED, "reasons": [], "blockers": [], "notes": []}
    blockers, reasons = report["blockers"], report["reasons"]
    try:
        package = load_package(agent_ref, root)
    except PackageError as exc:
        blockers.append(f"package does not load: {exc}")
        return report
    report["agent"] = package.ref
    report["identity"] = ident = identity(package)
    try:
        registered = registry.load(root)["agents"]
    except registry.RegistryError as exc:
        blockers.append(str(exc))
        registered = {}
    entry = registered.get(package.name) or {}
    report["registry"] = {key: entry.get(key) for key in ("version", "state", "level", "binding", "package_sha256", "evaluated_at")} \
        if entry else None
    if not entry:
        blockers.append("not registered: no release record exists for this package")

    # Integrity: package on disk == registry == evidence == reproducible archive.
    integrity: dict[str, Any] = {"package_matches_registry": bool(entry) and entry.get("package_sha256") == package.content_sha256
                                 and entry.get("version") == package.version,
                                 "binding_matches_registry": bool(entry) and entry.get("binding") == ident["binding"]}
    level = evals.current_level(package.ref, root)
    evidence_file: dict = {}
    if level.get("evidence"):
        try:
            evidence_file = json.loads((root / level["evidence"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evidence_file = {}
    integrity["evidence"] = level.get("evidence")
    integrity["evidence_matches_package"] = level["level"] != "none"
    integrity["evidence_reason"] = level.get("reason")
    integrity["evaluated_at"] = level.get("evaluated_at")
    integrity["evidence_prompt_matches"] = (evidence_file.get("prompt_sha256") or None) == (ident["prompt_sha256"] or None) \
        if evidence_file else False
    archive = (entry or {}).get("archive") or {}
    if archive.get("sha256"):
        with tempfile.TemporaryDirectory(prefix="m7-pack-") as scratch:
            try:
                packed = registry.pack(package.ref, root, out_dir=Path(scratch))
                integrity["archive_reproducible"] = packed["archive_sha256"] == archive["sha256"]
            except (registry.RegistryError, PackageError, OSError) as exc:
                integrity["archive_reproducible"] = False
                integrity["archive_error"] = str(exc)
    else:
        integrity["archive_reproducible"] = False
        integrity["archive_error"] = "no archive recorded in the registry"
    report["integrity"] = integrity
    if entry:
        for key, text in (("package_matches_registry", "the package on disk differs from the registered package"),
                          ("binding_matches_registry", "the model binding differs from the registered binding"),
                          ("evidence_matches_package", f"gate evidence is stale or missing: {level.get('reason')}"),
                          ("evidence_prompt_matches", "the evidence was produced for a different prompt"),
                          ("archive_reproducible", "the archive does not reproduce: "
                           + (integrity.get("archive_error") or "hash differs (evidence changed since it was packed? "
                              f"aos-agent.py release repack {package.name})"))):
            if not integrity.get(key):
                blockers.append(text)
        if entry.get("holdout") is not None and not (entry.get("holdout") or {}).get("pass"):
            blockers.append("the held-out cases did not pass")

    gates = {gate: {"pass": bool(body.get("pass"))} for gate, body in sorted((evidence_file.get("gates") or {}).items())} \
        if integrity["evidence_matches_package"] else {}
    for gate in ("1", "2", "3", "4", "5", "6"):
        gates.setdefault(gate, {"pass": False, "reason": "no current evidence"})

    # Canary evidence for this exact binding only.
    evidence = evidence if evidence is not None else canary_evidence(root, runtime.root)
    mine = [row for row in evidence["rows"] if row["agent"] == package.name]
    exact = [row for row in mine if row["version"] == package.version and row["package_sha256"] == package.content_sha256
             and row["binding"] == ident["binding"]]
    eligible = [row for row in exact if row["eligible"]]
    incompatible = []
    for row in mine:
        if row in exact:
            continue
        why = [label for label, same in (("version", row["version"] == package.version),
                                         ("package hash", row["package_sha256"] == package.content_sha256),
                                         ("model binding", row["binding"] == ident["binding"])) if not same]
        incompatible.append({"job": row["job"], "record": row["record"], "differs": why})
    gates["7"] = gate7(package, root)
    report["gates"] = gates
    for row in exact:
        for problem in row["safety"]:
            blockers.append(f"{row['job']}: {problem}")
    report["canaries"] = {
        "required_for_gold": GOLD_CANARIES, "eligible": len(eligible),
        "remaining_for_gold": max(0, GOLD_CANARIES - len(eligible)),
        "counted": [{"job": row["job"], "record": row["record"], "receipt": row["receipt"], "tokens": row["tokens"],
                     "ledger_invocations": row["ledger_invocations"], "source": row["source"]} for row in eligible],
        "excluded": [{"job": row["job"], "record": row["record"], "reasons": row["excluded"] + row["safety"]}
                     for row in exact if not row["eligible"] and not _only_unverified(row)],
        "unverified": [{"job": row["job"], "record": row["record"], "reasons": row["excluded"]}
                       for row in exact if not row["eligible"] and _only_unverified(row)],
        "duplicates_ignored": [row["job"] for row in evidence["duplicates"] if row["agent"] == package.name],
        "incompatible": incompatible, "ledger": evidence["ledger"], "record_problems": evidence["problems"],
    }

    # Verified level: evidence-backed bronze/silver; gold needs gate 7 AND, separately, the real canaries.
    verified = level["level"] if integrity["evidence_matches_package"] else "none"
    if verified == "silver" and gates["7"]["pass"] and len(eligible) >= GOLD_CANARIES:
        verified = "gold"
    report["level"] = verified

    # Capabilities that always need Casey, whatever the level.
    for effect in sorted(set(ident["capabilities"]["proposes"]) & EXTERNAL_EFFECTS):
        report["notes"].append(f"proposes {effect}: every such effect stays held for Casey's explicit authorization")

    # Dependencies: every composed call must be registered and at least as released as the target.
    deps = []
    for ref in ident["compose_calls"]:
        name = ref.split("@", 1)[0]
        state = (registered.get(name) or {}).get("state") or "unregistered"
        ok = state in DEPENDENCY_STATES[target]
        deps.append({"agent": ref, "state": state, "ok": ok})
        if state == "unregistered":
            blockers.append(f"unsupported dependency: {ref} is not registered")
        elif not ok:
            reasons.append(f"dependency {ref} is {state}; {target} needs it in {' or '.join(DEPENDENCY_STATES[target])}")
    report["dependencies"] = deps

    current = (entry or {}).get("state")
    report["route"] = {
        "current_state": current, "routed_now": current in RELEASED_STATES,
        "default_release_states": list(RELEASED_STATES),
        "would_replace": {"version": entry.get("version"), "package_sha256": entry.get("package_sha256"), "state": current}
        if entry else None,
        "rollback_target": {"state": current, "version": entry.get("version"), "package_sha256": entry.get("package_sha256")}
        if entry else None,
    }
    if _level_rank(verified) < _level_rank(required):
        reasons.append(f"{target} needs {required}; verified evidence supports {verified}")
        if required == "gold":
            if level["level"] != "silver":
                reasons.append("gold needs silver gates 1-6 first")
            if not gates["7"]["pass"]:
                reasons.append("gate 7 (reliability) not passed: " + "; ".join(gates["7"]["problems"][:3]))
            if report["canaries"]["remaining_for_gold"]:
                reasons.append(f"separately, {report['canaries']['remaining_for_gold']} more clean real canary run(s) of this "
                               f"exact binding needed ({len(eligible)} of {GOLD_CANARIES})")
    if current and _state_rank(current) >= _state_rank(target):
        report["notes"].append(f"already {current}: no state change toward {target}")
    report["decision"] = BLOCKED if blockers else NOT_READY if reasons else READY
    report["authorization"] = {"required": target in registry.NEEDS_APPROVAL, "granted_by_this_check": False,
                               "note": NOT_AUTHORIZATION}
    report["blockers"] = sorted(set(blockers))
    return report


def check(agents: list[str] | None = None, root: Path | None = None, *, runtime_root: Path | None = None,
          target: str = "active") -> dict:
    """Readiness for each named agent, or every installed package. Reproducible for the same inputs."""
    root = repo_root(root)
    runtime_root = Path(runtime_root).resolve() if runtime_root else root
    evidence = canary_evidence(root, runtime_root)
    names = agents or installed(root)
    candidates = [evaluate(name, root, runtime_root=runtime_root, target=target, evidence=evidence) for name in names]
    summary = {"by_level": {}, "by_decision": {}, "ready": []}
    for row in candidates:
        summary["by_level"].setdefault(row.get("level", "none"), []).append(row["agent"])
        summary["by_decision"].setdefault(row["decision"], []).append(row["agent"])
        if row["decision"] == READY:
            summary["ready"].append(row["agent"])
    inputs = {
        "registry_sha256": _sha256_file(root / registry.REGISTRY_PATH),
        "acceptances_sha256": _sha256_file(root / ACCEPTANCES_PATH),
        "ledger_sha256": _sha256_file(runtime_root / LEDGER_PATH),
        "runtime_root": str(runtime_root),
    }
    report = {"m7": M7_VERSION, "target_state": target, "gold_canaries_required": GOLD_CANARIES, "inputs": inputs,
              "summary": summary, "candidates": candidates, "evidence_rows": evidence["rows"],
              "evidence_duplicates": [row["job"] + ":" + row["agent"] for row in evidence["duplicates"]],
              "note": NOT_AUTHORIZATION}
    report["report_sha256"] = _sha256_bytes(_canonical(report).encode("utf-8"))
    return report


def render(report: dict) -> str:
    """A short human summary of ``check``'s report."""
    lines = [f"M7 release readiness ({report['m7']}), target {report['target_state']}; "
             f"gold needs gates 1-7 and {report['gold_canaries_required']} clean real canaries per exact binding",
             f"evidence: runtime {report['inputs']['runtime_root']}; registry {str(report['inputs']['registry_sha256'])[:12]}; "
             f"acceptances {str(report['inputs']['acceptances_sha256'])[:12]}; report {report['report_sha256'][:12]}", ""]
    for row in report["candidates"]:
        ident = row.get("identity") or {}
        canaries = row.get("canaries") or {}
        model = f"{ident.get('model')}/{ident.get('effort')}" if ident.get("model") else ident.get("binding", "?")
        lines.append(f"{row['decision']:9} {row['agent']:36} level {row.get('level', 'none'):6} "
                     f"state {((row.get('registry') or {}).get('state') or 'unregistered'):10} {model:16} "
                     f"canaries {canaries.get('eligible', 0)}/{report['gold_canaries_required']}"
                     + (f" (need {canaries.get('remaining_for_gold')})" if canaries.get("remaining_for_gold") else ""))
        for text in (row.get("blockers") or []) + (row.get("reasons") or []):
            lines.append(f"          - {text}")
        for item in canaries.get("excluded") or []:
            lines.append(f"          - excluded {item['job']}: {'; '.join(item['reasons'][:3])}")
        route = row.get("route") or {}
        if route.get("rollback_target"):
            lines.append(f"          route: now {route['current_state']}; rollback target "
                         f"{route['rollback_target']['state']} @ {str(route['rollback_target']['package_sha256'])[:12]}")
    lines += ["", report["note"]]
    return "\n".join(lines) + "\n"


# -- offline staging --------------------------------------------------------------------

def _entry_sha(entry: dict | None) -> str:
    return _sha256_bytes(_canonical(entry or {}).encode("utf-8"))


def _guard_outside(path: Path, *protected: Path) -> Path:
    path = Path(path).resolve()
    for root in protected:
        root = Path(root).resolve()
        if path == root or path.is_relative_to(root / "agents") or path.is_relative_to(root / "queue"):
            raise ReleaseError(f"{path} is inside live state under {root}; use a scratch directory")
    return path


def stage(agent_ref: str, out_dir: Path, root: Path | None = None, *, runtime_root: Path | None = None,
          target: str = "active", approved_by: str = "", expect_state: str | None = None) -> dict:
    """Prepare a release candidate as files in ``out_dir``. Never writes the registry; refuses and says why."""
    root = repo_root(root)
    runtime_root = Path(runtime_root).resolve() if runtime_root else root
    out_dir = _guard_outside(out_dir, root, runtime_root)
    registry_before = _sha256_file(root / registry.REGISTRY_PATH)
    result: dict[str, Any] = {"agent": str(agent_ref), "target_state": target, "staged": False, "refused": [], "dry_run": True}
    if target not in registry.REQUIRED_LEVEL:
        result["refused"].append(f"unknown target state {target!r}")
        return result
    readiness = evaluate(agent_ref, root, runtime_root=runtime_root, target=target)
    result["agent"], result["readiness"] = readiness["agent"], {key: readiness.get(key) for key in
                                                               ("decision", "level", "blockers", "reasons")}
    refused = result["refused"]
    if readiness["decision"] != READY:
        refused.append(f"readiness is {readiness['decision']}: " + "; ".join((readiness["blockers"] + readiness["reasons"])[:4]))
    try:
        data = registry.load(root)
    except registry.RegistryError as exc:
        refused.append(str(exc))
        return result
    ident = readiness.get("identity") or {}
    entry = data["agents"].get(ident.get("name") or "") or None
    current = (entry or {}).get("state")
    requested = str(agent_ref).split("@", 1)
    if len(requested) == 2 and entry and requested[1] != entry.get("version"):
        refused.append(f"incorrect target identity: {agent_ref} requested, registry holds {entry.get('version')}")
    if expect_state is not None and expect_state != current:
        refused.append(f"stale active-state assumption: expected {expect_state}, registry says {current}")
    if current and _state_rank(target) <= _state_rank(current):
        refused.append(f"conflicting route: {ident.get('agent')} is already {current}; {target} is not a release step up")
    if refused:
        return result

    manifest = {
        "m7_release_candidate": M7_VERSION, "dry_run": True, "applied": False,
        "agent": ident["agent"], "target_state": target,
        "package": {key: ident.get(key) for key in ("name", "version", "kind", "binding", "model", "effort", "package_sha256",
                                                   "prompt_sha256", "handler_sha256", "schema_sha256", "capabilities")}
        | {"archive_sha256": ((entry or {}).get("archive") or {}).get("sha256")},
        "acceptance": {"level": readiness["level"], "gates": {g: b["pass"] for g, b in readiness["gates"].items()},
                       "evidence": readiness["integrity"]["evidence"],
                       "evidence_sha256": _sha256_file(root / readiness["integrity"]["evidence"])
                       if readiness["integrity"]["evidence"] else None,
                       "reliability_evidence": {"path": readiness["gates"]["7"].get("evidence"),
                                                "sha256": readiness["gates"]["7"].get("evidence_sha256")},
                       "canaries": [{"job": row["job"], "receipt": row["receipt"]} for row in readiness["canaries"]["counted"]]},
        "current": {"state": current, "registry_sha256": registry_before, "entry_sha256": _entry_sha(entry)},
        "dependencies": readiness["dependencies"],
        "proposed_changes": [
            {"file": registry.REGISTRY_PATH.as_posix(), "path": f"agents.{ident['name']}.state", "from": current, "to": target},
            {"file": registry.REGISTRY_PATH.as_posix(), "path": f"agents.{ident['name']}.history", "append":
             {"event": "promoted", "from": current, "to": target, "level": readiness["level"], "approved_by": approved_by.strip()}},
        ],
        "routing_effect": (f"aos_jobs.refs_release_problem admits {ident['agent']} under the default release states "
                           f"({', '.join(RELEASED_STATES)})" if target in RELEASED_STATES else "no routing change"),
        "rollback": {"target_state": current, "entry": entry, "entry_sha256": _entry_sha(entry)},
        "authorization": {"required": target in registry.NEEDS_APPROVAL, "named_approver": approved_by.strip(),
                          "authority": authorization(root, ident["agent"], target),
                          "note": "A named approver is recorded, never trusted: the runtime permission authority decides. "
                                  + NOT_AUTHORIZATION},
    }
    manifest["manifest_sha256"] = _sha256_bytes(_canonical(manifest).encode("utf-8"))
    name = f"{ident['name']}-{ident['version']}-{target}"
    _write_json(out_dir / f"{name}.release_candidate.json", manifest)
    _write_json(out_dir / f"{name}.rollback.json", {"agent": ident["agent"], "restore_state": current,
                                                    "restore_entry": entry, "restore_entry_sha256": _entry_sha(entry),
                                                    "registry_sha256_at_staging": registry_before})
    registry_after = _sha256_file(root / registry.REGISTRY_PATH)
    if registry_after != registry_before:      # staging must never touch it; say so loudly if anything did
        raise ReleaseError("the registry changed during staging")
    result.update(staged=True, manifest=(out_dir / f"{name}.release_candidate.json").as_posix(),
                  rollback=(out_dir / f"{name}.rollback.json").as_posix(), proposed_changes=manifest["proposed_changes"],
                  rollback_target=current, authorization=manifest["authorization"], registry_unchanged=True)
    return result


# -- offline rollback drill ---------------------------------------------------------------

class SimulatedInterruption(RuntimeError):
    """Raised inside the drill to stand for a crash mid-release."""


JOURNAL = "release_journal.json"


def recover(scratch: Path) -> dict:
    """Finish an interrupted scratch release by restoring the snapshot it took first."""
    scratch = Path(scratch)
    journal_path = scratch / JOURNAL
    if not journal_path.is_file():
        return {"recovered": False, "reason": "no release journal"}
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("phase") in ("verified", "rolled_back", "recovered"):
        return {"recovered": False, "reason": f"release already {journal['phase']}"}
    snapshot = scratch / journal["snapshot"]
    target = scratch / registry.REGISTRY_PATH
    os.replace(_copy_to_temp(snapshot, target), target)
    restored = _sha256_file(target) == journal["registry_sha256_before"]
    journal.update(phase="recovered", recovered_from=journal.get("phase"), restored=restored)
    _write_json(journal_path, journal)
    return {"recovered": restored, "from_phase": journal["recovered_from"]}


def _copy_to_temp(source: Path, target: Path) -> Path:
    temporary = target.with_suffix(".json.restore")
    shutil.copyfile(source, temporary)
    return temporary


def drill(manifest_path: Path, scratch: Path, root: Path | None = None, *, runtime_root: Path | None = None,
          fail_after_activation: bool = False, interrupt: str = "") -> dict:
    """Apply a staged candidate to a COPY of the registry in ``scratch``, then roll back or recover.

    ``interrupt`` is '', 'before_write' or 'after_write'. Proves reversibility on scratch state only;
    it is not proof that a deployed rollback works.
    """
    root = repo_root(root)
    runtime_root = Path(runtime_root).resolve() if runtime_root else root
    scratch = _guard_outside(scratch, root, runtime_root)
    if scratch.exists() and any(scratch.iterdir()):
        raise ReleaseError(f"scratch directory {scratch} is not empty")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    report: dict[str, Any] = {"agent": manifest.get("agent"), "target_state": manifest.get("target_state"),
                              "scratch": str(scratch), "scratch_only": True, "refused": [], "phases": []}
    if manifest.get("manifest_sha256") != _sha256_bytes(_canonical(body).encode("utf-8")):
        report["refused"].append("the release manifest was altered after staging")
        return report

    live_registry = root / registry.REGISTRY_PATH
    live_before = _sha256_file(live_registry)
    runtime = Runtime(runtime_root)
    evidence_before = runtime.fingerprint()
    package_evidence = root / (manifest.get("acceptance") or {}).get("evidence", "") if (manifest.get("acceptance") or {}).get("evidence") else None
    evidence_file_before = _sha256_file(package_evidence) if package_evidence else None
    acceptances_before = _sha256_file(root / ACCEPTANCES_PATH)

    (scratch / "agents").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(live_registry, scratch / registry.REGISTRY_PATH)
    data = registry.load(scratch)
    name = (manifest.get("package") or {}).get("name")
    entry = data["agents"].get(name)
    if entry is None or entry.get("package_sha256") != manifest["package"]["package_sha256"] \
            or entry.get("version") != manifest["package"]["version"]:
        report["refused"].append("incompatible target: the registry does not hold the staged package")
    elif _entry_sha(entry) != manifest["current"]["entry_sha256"]:
        report["refused"].append("stale release evidence: the registry entry changed since staging")
    # A scratch apply changes nothing real, so it neither needs nor proves authorization; report the real verdict.
    report["authorization"] = authorization(root, manifest["agent"], manifest["target_state"])
    if report["refused"]:
        return report

    from aos_jobs import refs_release_problem

    route_before = refs_release_problem(scratch, [manifest["agent"]])
    snapshot = registry.REGISTRY_PATH.with_suffix(".json.pre-release")
    shutil.copyfile(scratch / registry.REGISTRY_PATH, scratch / snapshot)
    before_sha = _sha256_file(scratch / registry.REGISTRY_PATH)
    journal = {"agent": manifest["agent"], "phase": "applying", "snapshot": snapshot.as_posix(),
               "registry_sha256_before": before_sha, "target_state": manifest["target_state"]}
    _write_json(scratch / JOURNAL, journal)
    report["phases"].append("applying")
    try:
        if interrupt == "before_write":
            raise SimulatedInterruption("interrupted before the registry write")
        entry["history"] = (list(entry.get("history") or []) + [{
            "at": "scratch-drill", "event": "promoted", "from": entry.get("state"), "to": manifest["target_state"],
            "level": manifest["acceptance"]["level"], "approved_by": manifest["authorization"]["named_approver"],
            "note": "M7 offline scratch drill (not authorized; scratch only)"}])[-registry.HISTORY_KEPT:]
        entry["state"] = manifest["target_state"]
        registry._save(scratch, data)
        if interrupt == "after_write":
            raise SimulatedInterruption("interrupted after the registry write, before verification")
        journal["phase"] = "applied"
        _write_json(scratch / JOURNAL, journal)
        report["phases"].append("applied")
        route_after = refs_release_problem(scratch, [manifest["agent"]])
        report["route_after_apply"] = route_after or "admitted"
        if fail_after_activation:
            report["phases"].append("post-activation check failed (simulated)")
            os.replace(_copy_to_temp(scratch / snapshot, scratch / registry.REGISTRY_PATH), scratch / registry.REGISTRY_PATH)
            journal["phase"] = "rolled_back"
            _write_json(scratch / JOURNAL, journal)
            report["phases"].append("rolled_back")
        else:
            journal["phase"] = "verified"
            _write_json(scratch / JOURNAL, journal)
            report["phases"].append("verified")
    except SimulatedInterruption as exc:
        report["phases"].append(f"interrupted: {exc}")
        outcome = recover(scratch)
        report["phases"].append("recovered" if outcome.get("recovered") else f"recovery failed: {outcome}")

    final = registry.load(scratch)["agents"].get(name) or {}
    restored = report["phases"][-1] in ("rolled_back", "recovered")
    report["result"] = {
        "final_state": final.get("state"),
        "restored_to_previous": restored and _sha256_file(scratch / registry.REGISTRY_PATH) == before_sha,
        "route_before": route_before or "admitted",
        "route_now": refs_release_problem(scratch, [manifest["agent"]]) or "admitted",
        "live_registry_unchanged": _sha256_file(live_registry) == live_before,
        "jobs_receipts_ledger_unchanged": runtime.fingerprint() == evidence_before,
        "package_evidence_unchanged": (_sha256_file(package_evidence) == evidence_file_before) if package_evidence else None,
        "acceptances_unchanged": _sha256_file(root / ACCEPTANCES_PATH) == acceptances_before,
    }
    report["note"] = "Scratch simulation only: it proves the release record can be reversed, not that a deployed rollback works."
    _write_json(scratch / "rollback_report.json", report)
    return report
