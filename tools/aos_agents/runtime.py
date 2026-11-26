"""run_agent: execute one TAP agent package and return a validated result.

The handler receives a context object and nothing else. Through it the handler
can read the references its manifest declares, make the pinned model call its
budget allows, write artifacts, and call the agents it lists. Capabilities are
checked here, in code, on every use; a handler that asks for something its
manifest does not grant gets a refusal, and the run fails closed.

Result shape (never raises for agent outcomes):
  status            ok | needs_input | refused | failed | blocked
  output            schema-valid object when status is ok
  proposed_effects  declared effects, each classified by the existing
                    action_authorization; the runtime performs none of them
  artifacts         files written through the context
  evidence_refs     references actually read
  usage             exact model usage for this run, including called agents

Revisit: when ModelPort, the scoped Brain reader or the permission policy
interface changes. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

from aos_job_ids import storage_key

from .package import AgentPackage, PackageError, load_handler, load_package, repo_root

RESULT_STATUSES = ("ok", "needs_input", "refused", "failed", "blocked")
MAX_CALL_DEPTH = 2
# Provider/account stops that end a run as truthful "blocked", never retried or rerouted.
# needs_authentication is the Codex account selector's "no signed-in eligible account".
BLOCKED_FAILURE_CLASSES = frozenset({"authentication_unavailable", "needs_authentication", "quota_unavailable",
                                     "quota_exhausted", "token_fuse_paused"})
ARTIFACTS_DIR = Path("results") / "agents"
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(md|json|txt)$")
INLINE_JOB = "inline"
# Effect name in a manifest -> the connector action prefix the policy classifies.
EFFECT_ACTION_PREFIXES = {
    "gmail.draft": ("GMAIL_",), "email.send": ("GMAIL_", "AGENTMAIL_"), "calendar.write": ("GOOGLECALENDAR_",),
    "drive.write": ("GOOGLEDRIVE_",), "docs.write": ("GOOGLEDOCS_",), "sheets.write": ("GOOGLESHEETS_",),
    "telegram.notify_operator": ("TELEGRAM_",),
}


class AgentStop(Exception):
    """Ends a run with a non-ok status. Raised by the context, caught by run_agent."""

    def __init__(self, status: str, failure_class: str, detail: str):
        super().__init__(detail)
        self.status, self.failure_class, self.detail = status, failure_class, detail


@dataclass
class RunEnv:
    """Where and for whom a run happens. One env is shared by a whole plan."""

    root: Path | None = None
    item: dict | None = None          # the queue item, when this is the durable path
    job: str = ""                     # queue item id; empty on the inline path
    modelport: Callable[..., dict] | None = None   # injected in tests; default is aos_modelport.call
    backend: str = "exec"
    session: Any = None               # a warm AppServerSession, when backend is app-server
    brain_read: Callable[[str, str], dict] | None = None  # (client_scope, pointer) -> {content, provenance}
    authorize: Callable[..., dict] | None = None   # default is aos_authority.action_authorization
    record: bool = True               # write model usage to the token ledger
    ledger_root: Path | None = None   # where the token ledger lives; defaults to root
    ledger_item: str | None = None    # ledger work-item id; defaults to job (a queue id, AOS-YYYY-NNNN)
    admit: Callable[[list[str]], str] | None = None  # release gate for a planner-emitted plan; "" admits
    codex_account: Any = None         # the job's aos_codex_policy.AccountPin: one Codex account per job
    connection_context: tuple | None = None  # (Connections, authenticated principal), server ingress only
    connection_permissions: frozenset = field(default_factory=frozenset)  # exact (connection id, operation) task scope

    def resolved_root(self) -> Path:
        return repo_root(self.root)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text or "") / 4)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def empty_usage() -> dict:
    return {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "total_tokens": 0, "exact": True, "latency_seconds": 0.0, "calls": []}


def add_usage(total: dict, part: dict) -> dict:
    for key in ("model_calls", "input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        total[key] += int(part.get(key) or 0)
    total["latency_seconds"] = round(total["latency_seconds"] + float(part.get("latency_seconds") or 0.0), 3)
    total["exact"] = bool(total["exact"] and part.get("exact", True))
    total["calls"].extend(part.get("calls") or [])
    return total


def _schema_error(value: Any, schema: dict) -> str:
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.absolute_path))
    if not errors:
        return ""
    first = errors[0]
    where = "/".join(str(part) for part in first.absolute_path) or "(root)"
    return f"{where}: {first.message[:240]}"


def _default_brain_read(client_scope: str, pointer: str) -> dict:
    """Open one Brain note through the existing scope registry and scoped loader."""
    from business_brain_context import ScopedBrainLoader

    result = ScopedBrainLoader().retrieve(
        work={"client_scope": client_scope, "business_brain_pointers": [pointer]}, pointers=[pointer])
    read = result.reads[0]
    return {"content": read.content, "provenance": result.brain_context_used[0]}


def _default_authorize(root: Path, item: dict | None, *, action: str, target: str) -> dict:
    import aos_authority

    return aos_authority.action_authorization(root, item, action=action, target=target)


def _callable_by(package: AgentPackage, caller: str) -> bool:
    """A direct call (no caller) is always allowed; a composed call needs a match."""
    if not caller:
        return True
    patterns = (package.manifest.get("compose") or {}).get("callable_by")
    if patterns is None:
        return caller.split(".", 1)[0] == package.department
    department = caller.split(".", 1)[0]
    return any(p == "*" or p == caller or (p.endswith(".*") and p[:-2] == department) for p in patterns)


class AgentContext:
    """The only door a handler has to the rest of TTROS."""

    def __init__(self, package: AgentPackage, env: RunEnv, *, step: str, parent: str, depth: int, started: float):
        self._package, self._env = package, env
        self.agent, self.version, self.kind = package.name, package.version, package.kind
        self.prompt = package.prompt
        self.schemas = package.schemas
        self.trace = {"job": env.job or INLINE_JOB, "step": step, "parent": parent}
        self._depth, self._started = depth, started
        self._usage = empty_usage()
        self._artifacts: list[dict] = []
        self._evidence: list[dict] = []
        self._own_calls = 0

    # -- budget ---------------------------------------------------------------
    def _seconds_left(self) -> float:
        return float(self._package.budget["seconds"]) - (time.monotonic() - self._started)

    # -- capabilities ---------------------------------------------------------
    def _require(self, capability: str) -> None:
        if capability not in self._package.grants:
            raise AgentStop("failed", "capability_denied", f"{self._package.ref} is not granted {capability}")

    def connector_read(self, service: str, operation: str, arguments: dict | None = None, *, connection: str | None = None) -> dict:
        return self._connector_operation('read', service, operation, arguments, connection)

    def connector_write(self, service: str, operation: str, arguments: dict | None = None, *, connection: str | None = None) -> dict:
        return self._connector_operation('write', service, operation, arguments, connection)

    def _connector_operation(self, mode, service, operation, arguments, connection):
        if self._env.connection_context is None:
            raise AgentStop('refused', 'connection_scope_missing', 'No authenticated Connections context for this run.')
        from ttros_connections import ConnectionError
        hub, who = self._env.connection_context
        try:
            receipt = hub.execute_agent(who, self._package, service, operation, arguments or {},
                                        permissions=self._env.connection_permissions, item=self._env.item, ident=connection, mode=mode)
        except ConnectionError as exc:
            raise AgentStop('refused', 'connection_denied', str(exc)) from None
        self._evidence.append({'ref': receipt['id'], 'connection': receipt['connection'], 'operation': operation})
        return receipt

    # -- context --------------------------------------------------------------
    def context(self, input: dict, *, query: str = "") -> list[dict]:
        """Resolve the manifest's declared context needs for this input.

        Returns blocks ``{id, ref, content, sha256, tokens, truncated}``. Only
        declared needs are resolved; the total stays inside context.max_tokens,
        and a block reduced to fit is marked ``truncated`` rather than cut silently.
        """
        declared = self._package.manifest.get("context") or {}
        refs: list[str] = []
        for need in declared.get("needs") or []:
            if need.startswith("input."):
                value = input.get(need[len("input."):])
                values = value if isinstance(value, list) else ([value] if value else [])
                refs.extend(str(v) for v in values if isinstance(v, str) and v)
            elif need.startswith("brain:"):
                refs.append("business_brain:" + need[len("brain:"):])
            elif need == "icm:human-language-profile":
                refs.append(need)
            else:
                raise AgentStop("failed", "context_need_unsupported", f"context need not supported by this runtime: {need}")
        blocks: list[dict] = []
        for index, ref in enumerate(dict.fromkeys(refs), start=1):
            content, provenance = self._read_ref(ref)
            blocks.append({"id": f"E{index}", "ref": ref, "content": content, "tokens": estimate_tokens(content),
                           "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "truncated": False,
                           "provenance": provenance})
        self._fit(blocks, int(declared.get("max_tokens") or 0), query)
        for block in blocks:
            row = {key: block[key] for key in ("id", "ref", "sha256", "tokens", "truncated")}
            if block.get("provenance"):
                row["provenance"] = dict(block["provenance"])   # the scoped loader's brain_context_used record
            self._evidence.append(row)
        return blocks

    def _read_ref(self, ref: str) -> tuple[str, dict]:
        if ref.startswith("business_brain:"):
            scopes = sorted(g.split(":", 1)[1] for g in self._package.grants if g.startswith("brain.read:"))
            if not scopes:
                raise AgentStop("failed", "capability_denied", f"{self._package.ref} is not granted brain.read")
            reader = self._env.brain_read or _default_brain_read
            last = ""
            for scope in scopes:
                try:
                    read = reader(scope, ref)
                    return str(read["content"]), dict(read.get("provenance") or {})
                except PermissionError as exc:   # ClientScopeError: outside this scope
                    last = str(exc)
                except Exception as exc:  # unreadable, missing, malformed: fail closed
                    raise AgentStop("failed", "context_unavailable", f"{ref}: {type(exc).__name__}: {str(exc)[:200]}") from exc
            raise AgentStop("failed", "scope_denied", f"{ref} is outside the granted scope(s) {scopes}: {last[:200]}")
        if ref.startswith("icm:"):
            self._require("icm.read")
            item = self._env.item or {}
            if (item.get("executor") or {}).get("david_capability") is not True:
                raise AgentStop("refused", "scope_denied", "Operator ICM requires a verified David Job.")
            import aos_icm

            try:
                source = aos_icm.read(ref)
            except (OSError, ValueError) as exc:
                raise AgentStop("failed", "context_unavailable", "The requested operator ICM is unavailable.") from exc
            return source["content"], {k: v for k, v in source.items() if k != "content"}
        if ref.startswith("artifact:"):
            self._require("artifact.read")
            relative = ref[len("artifact:"):]
            path = self._artifact_path(relative)
            item = self._env.item or {}
            if (item.get("executor") or {}).get("david_capability") is True:
                data = self._read_scoped_artifact(relative, path, item)
                return data.decode("utf-8"), {"path": path.relative_to(self._env.resolved_root()).as_posix()}
            if not path.is_file():
                raise AgentStop("failed", "context_unavailable", f"artifact not found: {ref}")
            return path.read_text(encoding="utf-8"), {"path": path.relative_to(self._env.resolved_root()).as_posix()}
        raise AgentStop("failed", "reference_unsupported", f"reference type not supported: {ref[:80]}")

    def _read_scoped_artifact(self, relative: str, path: Path, item: dict) -> bytes:
        import aos_jobs
        from aos_david_files import _read_regular

        root = self._env.resolved_root().resolve()
        source, _, _ = relative.rpartition("/")
        try:
            if path.resolve() != root / path.relative_to(self._env.resolved_root()):
                raise ValueError("unsafe artifact path")
            if not source or source == storage_key(self.trace["job"]):
                return _read_regular(path, 512 * 1024)
            if re.fullmatch(r"id-[a-f0-9]{64}", source):
                job = next(row for row in aos_jobs.list_jobs(root, conversation_id=item["conversation_id"], limit=None)
                           if storage_key(row["id"]) == source)
            else:
                job = aos_jobs.load(root, source)
            if (job["conversation_id"] == item["conversation_id"]
                    and (job.get("executor") or {}).get("capability_owner", "")
                    == (item.get("executor") or {}).get("capability_owner", "")):
                registered = next(row for row in job.get("artifacts") or () if row["ref"] == "artifact:" + relative)
                data = _read_regular(path, 512 * 1024)
                if registered["sha256"] == hashlib.sha256(data).hexdigest():
                    return data
        except (aos_jobs.JobError, OSError, StopIteration, KeyError, ValueError):
            pass
        raise AgentStop("failed", "scope_denied", "The artifact is unavailable in this conversation.")

    def _fit(self, blocks: list[dict], max_tokens: int, query: str) -> None:
        if not max_tokens or sum(block["tokens"] for block in blocks) <= max_tokens:
            return
        from brain_passage import _bounded_passage

        allowance = max(200, max_tokens // max(1, len(blocks)))
        for block in blocks:
            if block["tokens"] <= allowance:
                continue
            excerpt = _bounded_passage(block["content"], query, max_bytes=allowance * 4, keep_headings=True) if query else None
            block["content"] = excerpt if excerpt is not None else block["content"][: allowance * 4]
            block["tokens"], block["truncated"] = estimate_tokens(block["content"]), True
        if sum(block["tokens"] for block in blocks) > max_tokens:
            raise AgentStop("failed", "context_over_budget",
                            f"declared context exceeds context.max_tokens={max_tokens} after reduction")

    # -- model ----------------------------------------------------------------
    def model(self, prompt: str, *, schema: dict | None = None) -> dict:
        """One pinned, schema-constrained call, with at most one repair retry.

        Returns the parsed object. Any refusal, failure, timeout or exhausted
        budget ends the run; there is no fallback model or route.
        """
        if self.kind == "tool":
            raise AgentStop("failed", "capability_denied", "a tool agent has no model")
        output_schema = schema or self._package.output_schema
        budget, pinned = self._package.budget, self._package.model
        port = self._env.modelport
        if port is None:
            import aos_modelport

            port = aos_modelport.call
        attempt_prompt, problem = prompt, ""
        for _attempt in range(2):
            if self._own_calls >= int(budget["model_calls"]):
                raise AgentStop("failed", "budget_model_calls", problem or f"model call budget {budget['model_calls']} is spent")
            if self._usage["total_tokens"] >= int(budget["tokens"]):
                raise AgentStop("failed", "budget_tokens", f"token budget {budget['tokens']} is spent")
            seconds = self._seconds_left()
            if seconds <= 1:
                raise AgentStop("failed", "budget_seconds", f"time budget {budget['seconds']}s is spent")
            self._own_calls += 1
            # Every call of one job runs on the job's one Codex account (chosen at its first call).
            pin = {"account": self._env.codex_account} if self._env.codex_account is not None else {}
            attempts, attempt_key = None, {}
            if self._env.job:
                from .journal import StepJournal
                attempts = StepJournal(self._env.resolved_root(), self._env.job)
                attempts.path = attempts.path.with_suffix(".model-attempts.jsonl")
                attempt_key = dict(plan_ref="model-attempts", step=self.trace["step"], agent=self._package.ref,
                                   package_sha256=self._package.content_sha256,
                                   input_sha256=canonical_sha256([attempt_prompt, pinned["id"], pinned["effort"]]))
                attempts.append(**attempt_key, result={"status": "started"})
            result = port(
                attempt_prompt, model=pinned["id"], effort=pinned["effort"], output_schema=output_schema,
                backend=self._env.backend, session=self._env.session, timeout=min(seconds, 120.0),
                agent=self._package.ref, record=self._env.record,
                work_item_id=self._env.job if self._env.ledger_item is None else self._env.ledger_item,
                root=self._env.ledger_root or self._env.resolved_root(), **pin,
            )
            status = str(result.get("status") or "failed")
            if attempts is not None and status == "refused":
                # ModelPort refuses only before launch (policy, fuse or no eligible Codex account):
                # no provider call happened, so the journaled attempt is closed as never launched.
                attempts.append(**attempt_key, result={"status": "not_launched",
                                                       "failure_class": str(result.get("failure_class") or "")})
            self._record_call(result)
            parsed = result.get("json") if status == "ok" else None
            if status == "ok":
                problem = _schema_error(parsed, output_schema)
                if not problem:
                    return parsed
            elif status == "invalid_output":
                problem = str(result.get("detail") or "reply was not a valid JSON object")
            else:
                failure = str(result.get("failure_class") or f"model_{status}")
                blocked = failure in BLOCKED_FAILURE_CLASSES
                raise AgentStop("blocked" if blocked else "failed", failure, str(result.get("detail") or status)[:400])
            attempt_prompt = (f"{prompt}\n\nYour previous reply was rejected: {problem[:300]}\n"
                              "Reply again with exactly one JSON object that satisfies the schema.")
        raise AgentStop("failed", "schema_invalid_output", f"model output still invalid after one repair: {problem[:300]}")

    def _record_call(self, result: dict) -> None:
        usage = result.get("usage") or {}
        exact = bool(usage.get("available"))
        tokens_in, tokens_out = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        add_usage(self._usage, {
            "model_calls": 1, "input_tokens": tokens_in, "output_tokens": tokens_out,
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0), "total_tokens": tokens_in + tokens_out,
            "exact": exact, "latency_seconds": float(result.get("latency_seconds") or 0.0),
            "calls": [{"invocation_id": str(result.get("invocation_id") or ""), "agent": self._package.ref,
                       "step": self.trace["step"], "status": str(result.get("status") or ""),
                       "model": str(result.get("model") or ""), "effort": str(result.get("effort") or ""),
                       "tokens": tokens_in + tokens_out, "exact": exact,
                       "latency_seconds": float(result.get("latency_seconds") or 0.0),
                       "ledger_recorded": bool((result.get("ledger") or {}).get("recorded")),
                       "codex_account": str(((result.get("codex_account") or {}).get("slot")) or "")}],
        })

    # -- artifacts ------------------------------------------------------------
    def _artifact_path(self, relative: str) -> Path:
        job, _, name = relative.rpartition("/")
        job = job or self.trace["job"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", job) or not ARTIFACT_NAME_RE.match(name):
            raise AgentStop("failed", "artifact_name_invalid", f"not a valid artifact reference: {relative[:80]}")
        return self._env.resolved_root() / ARTIFACTS_DIR / storage_key(job) / name

    def write_artifact(self, name: str, text: str) -> str:
        """Write one artifact for this job and return its ``artifact:`` reference."""
        self._require("artifact.write")
        path = self._artifact_path(name)
        from aos_queue_storage import durable_replace_text

        path.parent.mkdir(parents=True, exist_ok=True)
        durable_replace_text(path, text)
        relative = path.relative_to(self._env.resolved_root()).as_posix()
        self._artifacts.append({"ref": f"artifact:{storage_key(self.trace['job'])}/{name}", "path": relative,
                                "job_id": self.trace["job"],
                                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        return self._artifacts[-1]["ref"]

    # -- composition ----------------------------------------------------------
    def call_agent(self, ref: str, input: dict) -> dict:
        """Call a listed agent as a tool. Its usage counts toward this run."""
        listed = (self._package.manifest.get("compose") or {}).get("calls") or []
        name = ref.split("@", 1)[0]
        if not any(entry == ref or entry.split("@", 1)[0] == name for entry in listed):
            raise AgentStop("failed", "compose_denied", f"{self._package.ref} does not list {ref} in compose.calls")
        result = run_agent(ref, input, self._env, caller=self._package.name, step=f"{self.trace['step']}>{name}",
                           depth=self._depth + 1)
        add_usage(self._usage, result["usage"])
        self._artifacts.extend(result.get("artifacts") or [])
        return result


def _result(package_ref: str, status: str, *, output: Any = None, detail: str = "", failure_class: str = "",
            usage: dict | None = None, **extra: Any) -> dict:
    result = {"status": status, "agent": package_ref, "output": output, "proposed_effects": [], "artifacts": [],
              "evidence_refs": [], "usage": usage or empty_usage(), "detail": detail, "failure_class": failure_class}
    result.update(extra)
    return result


def _classify_effects(package: AgentPackage, env: RunEnv, effects: Any) -> tuple[list[dict], str]:
    """Check every proposed effect against the manifest, then ask the one policy.

    Returns (classified effects, problem). The runtime performs no effect; the
    caller sees each one marked authorized or held for review.
    """
    if effects in (None, []):
        return [], ""
    if not isinstance(effects, list):
        return [], "proposed_effects is not a list"
    authorize = env.authorize or _default_authorize
    classified = []
    for effect in effects:
        if not isinstance(effect, dict) or not isinstance(effect.get("effect"), str):
            return [], "a proposed effect lacks an effect name"
        name, action, target = effect["effect"], str(effect.get("action") or ""), str(effect.get("target") or "")
        if name not in package.proposes:
            return [], f"undeclared effect {name!r} (manifest proposes {sorted(package.proposes)})"
        prefixes = EFFECT_ACTION_PREFIXES.get(name)
        if action and prefixes and not action.upper().startswith(prefixes):
            return [], f"action {action!r} does not belong to effect {name!r}"
        if ((env.item or {}).get("executor") or {}).get("david_capability") is True:
            decision = {"authorized": False, "basis": "capability_scope",
                        "detail": "This discovered operation can return local work and proposed effects."}
        else:
            decision = authorize(env.resolved_root(), env.item, action=action, target=target)
        classified.append({**effect, "authorization": {
            "authorized": bool(decision.get("authorized")), "basis": str(decision.get("basis") or ""),
            **({"detail": str(decision["detail"])} if decision.get("detail") else {}),   # why it is held
            "disposition": "authorized" if decision.get("authorized") else "review"}})
    return classified, ""


def run_agent(agent_ref: str, input: dict, env: RunEnv | None = None, *, caller: str = "", step: str = "",
              depth: int = 0) -> dict:
    """Run one agent package. ``caller`` is the plan or agent composing this call, if any."""
    env = env or RunEnv()
    started = time.monotonic()
    try:
        package = load_package(agent_ref, env.resolved_root())
    except PackageError as exc:
        return _result(str(agent_ref), "refused", failure_class="package_rejected", detail=str(exc))
    ref = package.ref
    if depth > MAX_CALL_DEPTH:
        return _result(ref, "refused", failure_class="compose_depth", detail=f"call depth exceeds {MAX_CALL_DEPTH}")
    if not _callable_by(package, caller):
        return _result(ref, "refused", failure_class="compose_denied", detail=f"{ref} is not callable by {caller}")
    if package.kind not in ("tool", "oneshot", "planner"):
        return _result(ref, "refused", failure_class="kind_unsupported",
                       detail=f"agent kind {package.kind} is not run by this runtime version")
    problem = _schema_error(input, package.input_schema)
    if problem:
        return _result(ref, "refused", failure_class="input_schema_violation", detail=problem)

    ctx = AgentContext(package, env, step=step or package.name, parent=caller, depth=depth, started=started)
    ctx._input_sha256 = canonical_sha256(input)
    try:
        raw = load_handler(package)(input, ctx)
    except AgentStop as stop:
        return _finish(ctx, _result(ref, stop.status, failure_class=stop.failure_class, detail=stop.detail), started)
    except PackageError as exc:
        return _finish(ctx, _result(ref, "refused", failure_class="package_rejected", detail=str(exc)), started)
    except Exception as exc:  # a handler bug is a failed run, never a crashed job
        return _finish(ctx, _result(ref, "failed", failure_class="handler_error",
                                    detail=f"{type(exc).__name__}: {str(exc)[:300]}"), started)

    if not isinstance(raw, dict) or raw.get("status") not in RESULT_STATUSES:
        return _finish(ctx, _result(ref, "failed", failure_class="result_shape", detail="handler returned no valid status"), started)
    status, output = raw["status"], raw.get("output")
    result = _result(ref, status, output=output, detail=str(raw.get("detail") or ""),
                     failure_class=str(raw.get("failure_class") or ""))
    if status == "ok":
        problem = _schema_error(output, package.output_schema)
        if problem:
            result.update(status="failed", failure_class="output_schema_violation", detail=problem, output=None)
        else:
            effects, problem = _classify_effects(package, env, raw.get("proposed_effects"))
            if problem:
                result.update(status="failed", failure_class="undeclared_effect", detail=problem, output=None)
            else:
                result["proposed_effects"] = effects
    return _finish(ctx, result, started)


def _finish(ctx: AgentContext, result: dict, started: float) -> dict:
    budget = ctx._package.budget
    usage = ctx._usage
    usage["elapsed_seconds"] = round(time.monotonic() - started, 3)
    usage["over_budget"] = [name for name, over in (
        ("tokens", usage["total_tokens"] > int(budget["tokens"])),
        ("seconds", usage["elapsed_seconds"] > float(budget["seconds"])),
    ) if over]
    result["usage"] = usage
    result["artifacts"] = list(ctx._artifacts)
    result["evidence_refs"] = [dict(row) for row in ctx._evidence]
    result["package_sha256"] = ctx._package.content_sha256
    from .evals import binding
    result["execution_identity"] = {"agent": ctx._package.ref, "package_sha256": ctx._package.content_sha256,
                                    "binding": binding(ctx._package), "model": ctx._package.model.get("id"),
                                    "effort": ctx._package.model.get("effort"),
                                    "input_sha256": ctx._input_sha256, "output_sha256": canonical_sha256(result.get("output"))}
    from .package import content_hash
    if content_hash(ctx._package.directory) != ctx._package.content_sha256:
        result.update(status="failed", failure_class="package_changed_during_execution", output=None)
    return result
