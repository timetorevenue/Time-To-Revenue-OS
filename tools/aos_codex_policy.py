"""Mandatory Agentic OS policy for every Codex process launch.

Also the one Codex account selector: every launch path picks its Codex home
(subscription account slot A or B) here, once per new session or job.

Revisit: when the canonical Agentic OS root, Linux user, Codex installation,
fresh-session/compaction CLI contract, model catalog, ``codex exec --json``
usage-event contract, the app-server account/rate-limit protocol or the
configured Codex accounts change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import codecs
import datetime as dt
import fcntl
import os
import pwd
import json
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable


class CodexPolicyError(RuntimeError):
    """The mandatory Agentic OS Codex runtime contract is not effective."""


SANDBOX_MODE = "danger-full-access"
APPROVAL_POLICY = "never"
AUTO_COMPACT_TOKEN_LIMIT = 75_000
REASONING_EFFORT_BY_DIAL = {"light": "low", "standard": "medium", "heavy": "high"}
CODEX_MODEL = "gpt-6-sol"
SESSION_FUSE_LIMIT = 500_000
SUPPORTED_EFFORTS = ("low", "medium", "high")
COMPLEXITY_TIERS = ("simple", "medium", "complex")
TIER_BY_DIAL = {"light": "simple", "standard": "medium", "heavy": "complex"}
MODEL_ROUTES_PATH = Path("queue/model_routes.json")
# A key or base URL in the launch environment could move a call from the subscription
# to a separately billed API route, so every Codex launcher refuses to start while one is set.
BILLED_ROUTE_VARIABLES = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL")
SUBSCRIPTION_ROUTE_CONFIG = ("--config", 'forced_login_method="chatgpt"', "--config", 'model_provider="openai"')
NO_PUSH_GIT_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "url.codex-push-forbidden://.pushInsteadOf",
    "GIT_CONFIG_VALUE_0": "",
}


def _positive_environment_integer(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise CodexPolicyError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise CodexPolicyError(f"{name} must be a positive integer")
    return value


PERMISSION_HEADER = "PERMISSION MODE — SCOPED LOCAL TASK APPROVED\n\n" + (
    Path(__file__).resolve().parents[1] / "rules/runtime_permission_policy.md"
).read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class AccountSelection:
    """Why one Codex account was chosen for one new session or job. Contains no credential."""

    slot: str
    reason: str
    basis: str
    checked_at: str
    windows: tuple = ()          # ((name, remaining_percent, resets_at_iso), ...)
    threshold_percent: int = 0

    def record(self) -> dict:
        return {"slot": self.slot, "reason": self.reason, "basis": self.basis, "checked_at": self.checked_at,
                "threshold_remaining_percent": self.threshold_percent, "threshold_window": "five_hour",
                "windows": [{"window": name, "remaining_percent": remaining, "resets_at": resets}
                            for name, remaining, resets in self.windows]}


@dataclass(frozen=True)
class CodexRuntimeTarget:
    """Filesystem/process identity only; permission policy cannot be overridden.

    ``account`` is the opaque slot label of ``codex_home``; ``selection`` is the evidence
    for choosing it (absent on the static default, which no launcher uses unselected).
    """

    root: Path
    executable: Path
    linux_user: str
    codex_home: Path
    account: str = "A"
    selection: AccountSelection | None = field(default=None, compare=False)

    def account_record(self) -> dict:
        if self.selection is not None:
            return self.selection.record()
        return {"slot": self.account, "reason": "explicit target (no selection)", "basis": "caller_supplied",
                "checked_at": "", "threshold_remaining_percent": None, "windows": []}


CODEX_TARGET = CodexRuntimeTarget(
    root=Path("/opt/ttros-core"),
    executable=Path("/home/operator/.local/npm/bin/codex"),
    linux_user=__import__("getpass").getuser(),
    codex_home=Path("/home/operator/.codex"),
)


def effective_linux_user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:  # pragma: no cover - a broken host account database
        raise CodexPolicyError(f"effective uid {os.geteuid()} has no Linux user") from exc


def invocation_metadata(target: CodexRuntimeTarget = CODEX_TARGET) -> dict:
    return {
        "executable": str(target.executable),
        "cwd": str(target.root),
        "linux_user": effective_linux_user(),
        "effective_uid": os.geteuid(),
        "sandbox": SANDBOX_MODE,
        "sandbox_mode": SANDBOX_MODE,
        "approval_policy": APPROVAL_POLICY,
        "ask_for_approval": APPROVAL_POLICY,
        "session_mode": "fresh_ephemeral",
        "resume_allowed": False,
        "model_auto_compact_token_limit": AUTO_COMPACT_TOKEN_LIMIT,
        "token_fuse_limit": 500_000,
        "model_requested": CODEX_MODEL,
        "actual_model": CODEX_MODEL,
    }


def validate_runtime(
    requested_root: Path | str,
    target: CodexRuntimeTarget = CODEX_TARGET,
) -> dict:
    requested = Path(requested_root).resolve()
    expected = target.root.resolve()
    user = effective_linux_user()
    defects = []
    if requested != expected:
        defects.append(f"cwd must be {expected}, got {requested}")
    if user != target.linux_user:
        defects.append(f"Linux user must be {target.linux_user}, got {user}")
    if not target.executable.is_absolute() or not target.executable.is_file() or not os.access(target.executable, os.X_OK):
        defects.append(f"Codex executable is missing or not executable: {target.executable}")
    if str(expected).startswith("/mnt/"):
        defects.append(f"Windows-mounted Agentic OS roots are forbidden: {expected}")
    if defects:
        raise CodexPolicyError("Agentic OS Codex policy defect: " + "; ".join(defects))
    return invocation_metadata(target)


def build_exec_command(
    target: CodexRuntimeTarget = CODEX_TARGET,
    *,
    cost_dial: str = "standard",
    selection: dict | None = None,
) -> list[str]:
    """Return the immutable headless CLI contract.

    ``selection`` (from :func:`select_model_and_effort`) sets model and effort;
    without it the canonical dial effort and pinned model apply unchanged.
    """
    if cost_dial not in REASONING_EFFORT_BY_DIAL:
        raise CodexPolicyError(
            f"invalid cost dial {cost_dial!r}; expected light, standard, or heavy"
        )
    model, effort = CODEX_MODEL, REASONING_EFFORT_BY_DIAL[cost_dial]
    if selection is not None:
        model, effort = str(selection.get("model") or ""), str(selection.get("reasoning_effort") or "")
        if effort not in SUPPORTED_EFFORTS or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,63}", model):
            raise CodexPolicyError(f"invalid Codex model selection {model!r}/{effort!r}")
    return [
        str(target.executable),
        # The authenticated ChatGPT subscription route only; never an API-key provider.
        *SUBSCRIPTION_ROUTE_CONFIG,
        "--sandbox", SANDBOX_MODE,
        "--ask-for-approval", APPROVAL_POLICY,
        "-C", str(target.root),
        "--config", f"model_auto_compact_token_limit={AUTO_COMPACT_TOKEN_LIMIT}",
        "--config", f"model_reasoning_effort={effort}",
        "exec", "--model", model,
        "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never", "-",
    ]


# --- Automatic model and effort selection --------------------------------
# One deterministic, zero-token classifier feeds the existing cost dial. The
# configured tier table lives in queue/model_routes.json (codex route); only
# models listed there may be launched, and that list is checked against the
# Codex CLI catalog (``codex debug models``) when it is edited, not per call.

_RISK_TERMS = re.compile(
    r"\b(?:security|credential|auth(?:entication|orization)?|secret|migrat\w*|concurren\w*|race|"
    r"deadlock|data loss|architecture|schema|ledger|fuse|refactor|multi-file|cross-cutting|"
    r"protected|rollback|lock(?:ing)?|encryption|permission)\b",
    re.IGNORECASE,
)
_SIMPLE_TERMS = re.compile(
    r"\b(?:typo|rename|one-line|single-line|read-only|status check|list|count|inspect|"
    r"report only|docs? only|comment|wording|smoke)\b",
    re.IGNORECASE,
)
_LOCAL_ACTIONS = {"local_read", "local_edit", "local_test"}


def classify_step_complexity(item: dict | None, *, attempt: int = 1) -> dict:
    """Score one bounded step as simple / medium / complex with visible reasons."""
    item = item or {}
    text = " ".join(str(item.get(key) or "") for key in ("title", "context", "definition_of_done"))
    actions = {str(value) for value in (item.get("allowed_actions") or [])}
    score = 0
    reasons: list[str] = []
    size = str(item.get("size") or "").strip().casefold()
    if size == "small":
        score -= 2
        reasons.append("size=small (-2)")
    elif size == "large":
        score += 2
        reasons.append("size=large (+2)")
    if actions and actions <= {"local_read"}:
        score -= 1
        reasons.append("read-only actions (-1)")
    elif actions - _LOCAL_ACTIONS:
        score += 2
        reasons.append(f"non-local actions {sorted(actions - _LOCAL_ACTIONS)} (+2)")
    if len(text) < 500:
        score -= 1
        reasons.append(f"short brief {len(text)} chars (-1)")
    elif len(text) > 2500:
        score += 1
        reasons.append(f"long brief {len(text)} chars (+1)")
    risk = sorted({match.lower() for match in _RISK_TERMS.findall(text)})
    if risk:
        score += 2
        reasons.append(f"risk terms {risk[:4]} (+2)")
        if len(risk) >= 3:
            score += 1
            reasons.append("several distinct risk signals (+1)")
    simple = sorted({match.lower() for match in _SIMPLE_TERMS.findall(text)})
    if simple and not risk:
        score -= 1
        reasons.append(f"simple terms {simple[:4]} (-1)")
    if attempt > 1:
        score += 1
        reasons.append(f"retry attempt {attempt} (+1)")
    if str(item.get("client_scope") or "global") != "global" and score < 0:
        score = 0
        reasons.append("client-scoped work floors at medium")
    tier = "simple" if score <= -2 else "complex" if score >= 2 else "medium"
    return {"tier": tier, "score": score, "reasons": reasons}


def load_codex_tiers(root: Path | None = None) -> dict:
    """Return the configured tier table from the existing queue route config.

    Fixture roots without their own route file use the repository's contract.
    """
    path = (root or CODEX_TARGET.root) / MODEL_ROUTES_PATH
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / MODEL_ROUTES_PATH
    try:
        route = json.loads(path.read_text(encoding="utf-8"))["routes"]["codex"]
        table = route["codex_selection"]
        supported = set(table["supported_models"])
        tiers = {name: dict(table["tiers"][name]) for name in COMPLEXITY_TIERS}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CodexPolicyError(f"Codex model tiers unavailable in {MODEL_ROUTES_PATH}: {exc}") from exc
    for name, tier in tiers.items():
        if tier.get("model") not in supported or tier.get("reasoning_effort") not in SUPPORTED_EFFORTS:
            raise CodexPolicyError(f"Codex tier {name} names an unconfigured model or effort: {tier}")
    return {"tiers": tiers, "supported_models": sorted(supported), "catalog_check": table.get("catalog_check", "")}


def select_model_and_effort(
    item: dict | None,
    *,
    cost_dial: dict,
    attempt: int = 1,
    root: Path | None = None,
) -> dict:
    """Pick model + effort for one step from complexity and the cost dial.

    An explicit scoped ``budget:<dial>`` override pins the tier outright. The
    global dial shifts the classified tier: light one step down, heavy one up.
    """
    table = load_codex_tiers(root)
    value, source = str(cost_dial.get("value") or "standard"), str(cost_dial.get("source") or "")
    if value not in TIER_BY_DIAL:
        raise CodexPolicyError(f"invalid cost dial {value!r}")
    classified = classify_step_complexity(item, attempt=attempt)
    if source == "scope_override":
        tier = TIER_BY_DIAL[value]
        reason = f"operator override budget:{value} pins tier {tier}"
    else:
        index = COMPLEXITY_TIERS.index(classified["tier"]) + {"light": -1, "standard": 0, "heavy": 1}[value]
        tier = COMPLEXITY_TIERS[max(0, min(2, index))]
        shift = "" if tier == classified["tier"] else f"; {value} dial shifted {classified['tier']}->{tier}"
        reason = f"classified {classified['tier']} (score {classified['score']}: {', '.join(classified['reasons']) or 'no signals'}){shift}"
    chosen = table["tiers"][tier]
    return {
        "tier": tier,
        "model": chosen["model"],
        "reasoning_effort": chosen["reasoning_effort"],
        "reason": reason,
        "classification": classified,
        "cost_dial": {"value": value, "source": source},
        "catalog_check": table["catalog_check"],
    }


# --- Streaming per-session fuse --------------------------------------------
# codex-cli 0.164.0 ``exec --json`` emits exact usage only in turn.completed,
# once per turn, and an exec run is normally one turn. The fuse stops ONLY on
# that exact counter: once a turn.completed reports the limit reached, the
# next turn.started is refused. A turn that has already completed is never
# discarded, and a single-turn session that ends over the limit is recorded
# (limit_reached_at_turn_end), not failed -- Step 6 accounts its exact usage.
# The in-flight estimate (inferred model requests x (prompt + fixed overhead
# floor)) is telemetry only and never stops a session: it is neither usage
# nor a bound. It ranged 0.24x-1.27x of exact over the recorded streams and,
# as a stop rule, killed AOS-2026-0947 after 35s and AOS-2026-9954 after 111s
# with no usage known (scripts/codex_fuse_calibration.txt). Wall-clock limits,
# auto-compaction and per-step model/effort selection remain the in-turn
# cost controls.
REQUEST_OVERHEAD_FLOOR_TOKENS = 12_000  # measured ~20K via `codex debug prompt-input` 2026-10-25
CHARS_PER_TOKEN_ESTIMATE = 4
ESTIMATE_BASIS = "in_flight_estimate_unverified"
EXACT_BASIS = "exact_turn_usage"
TOOL_ITEM_TYPES = frozenset({"command_execution", "file_change"})


class StreamingSessionFuse:
    """Watch one Codex JSONL stream; stop a further turn once exact usage reaches the fuse."""

    def __init__(self, prompt: str, *, limit: int = SESSION_FUSE_LIMIT) -> None:
        self.limit = limit
        self._prompt_tokens = len(prompt) // CHARS_PER_TOKEN_ESTIMATE
        self._buffer = ""
        self._in_response = False
        self.requests_estimated = 0
        self.in_flight_estimate = 0
        self.exact_cumulative: int | None = None
        self.turn_usage_events = 0
        self.limit_reached_at_turn_end = False
        self.breach: dict | None = None

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if kind == "turn.started":
            self._in_response = False
            if self.limit_reached_at_turn_end and self.breach is None:
                self.breach = {"basis": EXACT_BASIS, "tokens": self.exact_cumulative, "limit": self.limit}
        elif kind == "item.started" or (kind == "item.completed" and item.get("type") == "agent_message"):
            # One model response may carry a message and several tool calls;
            # only a tool result in between proves a further request.
            if not self._in_response:
                self.requests_estimated += 1
                self.in_flight_estimate += self._prompt_tokens + REQUEST_OVERHEAD_FLOOR_TOKENS
                self._in_response = True
        if kind == "item.completed" and item.get("type") in TOOL_ITEM_TYPES:
            self._in_response = False
        elif kind == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            value_in, value_out = usage.get("input_tokens"), usage.get("output_tokens")
            if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (value_in, value_out)):
                self.exact_cumulative = value_in + value_out
                self.turn_usage_events += 1
                self.limit_reached_at_turn_end = self.exact_cumulative >= self.limit

    def feed(self, text: str) -> dict | None:
        """Consume streamed stdout text; return the breach once a turn starts past the fuse."""
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        for raw in lines:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self._event(event)
        return self.breach

    def summary(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "scope": "this Codex session only",
            "exact_cumulative_streamed": self.exact_cumulative,
            "turn_usage_events": self.turn_usage_events,
            "limit_reached_at_turn_end": self.limit_reached_at_turn_end,
            "in_flight_estimate": self.in_flight_estimate,
            "in_flight_estimate_status": "unverified telemetry; never stops a session; not usage and not a bound",
            "requests_estimated": self.requests_estimated,
            "stopped": self.breach,
            "latency_note": (
                "codex exec --json reports exact usage only at turn.completed; the fuse stops only a "
                "further turn once exact usage reaches the limit, never a turn in progress"
            ),
        }


def describe_fuse_stop(breach: dict) -> str:
    """Operator wording for a fuse stop; only exact provider usage can stop a session."""
    return f"exact provider usage {breach['tokens']:,} of {breach['limit']:,}; further turn refused"


def prepare_fresh_prompt(prompt: str) -> str:
    """Apply the mandatory permission header once, without a hidden byte cap.

    Session isolation, the fuse and no-push are enforced by the launch itself
    (``--ephemeral``, ``require_clean_session_id``, ``StreamingSessionFuse``,
    ``build_environment``), so the prompt does not restate them.
    """
    task = str(prompt or "").strip()
    if not task:
        raise CodexPolicyError("Codex clean-session prompt is empty")
    # The canonical header goes first exactly once; copies embedded by a
    # template (e.g. the manual codex-run prompt) are dropped, not repeated.
    task = re.sub(r"\n{3,}", "\n\n", task.replace(PERMISSION_HEADER, "")).strip()
    return f"{PERMISSION_HEADER}\n\n{task}\n"


def require_clean_session_id(output: str) -> str:
    """Require one real ``thread.started`` identity; never invent a fallback ID."""
    identities: list[str] = []
    started_events = 0
    for raw in str(output or "").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "thread.started":
            continue
        started_events += 1
        value = event.get("thread_id") or event.get("session_id") or event.get("id")
        if isinstance(value, str) and value.strip():
            identities.append(value.strip())
    if started_events != 1 or len(identities) != 1:
        if started_events == 0:
            detail = "missing"
        elif not identities:
            detail = "invalid (thread.started has no real identity)"
        else:
            detail = f"ambiguous ({started_events} thread.started events)"
        raise CodexPolicyError(
            f"Codex clean-session creation failed: thread.started identity is {detail}; "
            "refusing previous-session inheritance or synthetic fallback"
        )
    return identities[0]


def cumulative_usage_snapshot(output: str) -> dict:
    """Return the last cumulative JSONL usage snapshot without summing events.

    Codex ``turn.completed`` usage is cumulative. Repeated snapshots replace the
    preceding snapshot; an incomplete final event never falls back to an older
    complete event because that would silently under-report the closing usage.
    """
    event_count = 0
    last_usage: object = None
    for raw in str(output or "").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        event_count += 1
        last_usage = event.get("usage")
    if event_count == 0:
        return {"available": False, "event_count": 0, "reason": "missing terminal turn.completed usage"}
    if not isinstance(last_usage, dict):
        return {"available": False, "event_count": event_count, "reason": "terminal turn.completed usage is malformed"}

    values = {
        "input_tokens": last_usage.get("input_tokens"),
        "cached_input_tokens": last_usage.get("cached_input_tokens"),
        "output_tokens": last_usage.get("output_tokens"),
        "reasoning_output_tokens": last_usage.get("reasoning_output_tokens"),
    }
    required = ("input_tokens", "output_tokens")
    if any(not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] < 0 for key in required):
        return {"available": False, "event_count": event_count, "reason": "terminal turn.completed usage lacks non-negative input/output"}
    for key in ("cached_input_tokens", "reasoning_output_tokens"):
        value = values[key]
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            return {"available": False, "event_count": event_count, "reason": f"terminal {key} is malformed"}
    cached = values["cached_input_tokens"]
    reasoning = values["reasoning_output_tokens"]
    if cached is not None and cached > values["input_tokens"]:
        return {"available": False, "event_count": event_count, "reason": "cached input exceeds provider-total input"}
    if reasoning is not None and reasoning > values["output_tokens"]:
        return {"available": False, "event_count": event_count, "reason": "reasoning output exceeds provider output"}
    return {
        "available": True,
        "event_count": event_count,
        **values,
        "cumulative_tokens": values["input_tokens"] + values["output_tokens"],
    }


def build_environment(target: CodexRuntimeTarget = CODEX_TARGET) -> dict[str, str]:
    """Retain the authenticated process environment while pinning Casey's Codex home."""
    try:
        account = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise CodexPolicyError(f"Linux user does not exist: {target.linux_user}") from exc
    env = os.environ.copy()
    env["HOME"] = account.pw_dir
    env["USER"] = target.linux_user
    env["LOGNAME"] = target.linux_user
    env["CODEX_HOME"] = str(target.codex_home)
    # Codex never pushes; the approved closeout pushes outside Codex. An empty
    # pushInsteadOf rewrites every push URL to a transport git cannot open,
    # while fetch, status and diff keep working.
    env.update(NO_PUSH_GIT_ENV)
    env["TTROS_BRAIN_AUTOPUSH"] = "0"
    env["TTROS_CODEX_NO_PUSH"] = "1"
    env["PATH"] = os.pathsep.join((
        str(target.executable.parent),
        str(Path(account.pw_dir) / ".local" / "bin"),
        env.get("PATH", ""),
    ))
    return env


def readiness(target: CodexRuntimeTarget = CODEX_TARGET, *, timeout: float = 5) -> dict:
    """Inspect the actual production command, identity, executable, and authentication."""
    metadata = invocation_metadata(target)
    command = build_exec_command(target)
    try:
        validate_runtime(target.root, target)
    except CodexPolicyError as exc:
        return {"available": False, "state": "policy_defect", "policy_error": str(exc), **metadata, "command": command}
    env = build_environment(target)
    try:
        version = subprocess.run(
            [str(target.executable), "--version"],
            cwd=str(target.root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "state": type(exc).__name__, **metadata, "command": command}
    result = {
        "available": version.returncode == 0,
        "state": "ready" if version.returncode == 0 else "version_probe_failed",
        "version": ((version.stdout or "") + (version.stderr or "")).strip().splitlines()[-1]
        if (version.stdout or version.stderr) else "unavailable",
        **metadata,
        "command": command,
    }
    if not result["available"]:
        return result
    try:
        auth = subprocess.run(
            [str(target.executable), "login", "status"],
            cwd=str(target.root), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result.update(available=False, authenticated=False, state="authentication_probe_failed")
        return result
    result["authenticated"] = auth.returncode == 0 and "logged in" in ((auth.stdout or "") + (auth.stderr or "")).lower()
    if not result["authenticated"]:
        result.update(available=False, state="authentication_unavailable")
    return result


def run_streaming(
    command: list[str],
    *,
    cwd: Path | str,
    env: dict[str, str],
    prompt: str,
    timeout_seconds: float,
    fuse: StreamingSessionFuse,
    terminate_grace_seconds: float = 1.0,
    kill_grace_seconds: float = 1.0,
) -> dict[str, Any]:
    """Run one Codex process, feeding stdout to the session fuse as it streams.

    On timeout or a fuse breach the whole process group gets SIGTERM, then
    SIGKILL after the grace period; output that arrives meanwhile is kept.
    """
    process = subprocess.Popen(
        command, cwd=str(cwd), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, close_fds=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()
    except BrokenPipeError:
        pass
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    stop_stage = ""  # "", "terminated", "killed"
    stop_at = 0.0

    def signal_group(sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        while selector.get_map():
            now = time.monotonic()
            if not stop_stage and now >= deadline:
                timed_out = True
            if not stop_stage and (timed_out or fuse.breach is not None):
                signal_group(signal.SIGTERM)
                stop_stage, stop_at = "terminated", now
            elif stop_stage == "terminated" and now - stop_at >= terminate_grace_seconds:
                signal_group(signal.SIGKILL)
                stop_stage, stop_at = "killed", now
            elif stop_stage == "killed" and now - stop_at >= kill_grace_seconds:
                break
            for key, _ in selector.select(timeout=0.1):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                chunks[key.data].append(chunk)
                if key.data == "stdout" and fuse.breach is None:
                    fuse.feed(decoder.decode(chunk))
        if stop_stage:
            signal_group(signal.SIGKILL)
        try:
            process.wait(timeout=kill_grace_seconds + 1)
        except subprocess.TimeoutExpired:
            pass
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
    return {
        "stdout": b"".join(chunks["stdout"]).decode("utf-8", errors="replace"),
        "stderr": b"".join(chunks["stderr"]).decode("utf-8", errors="replace"),
        "returncode": process.returncode,
        "timed_out": timed_out,
        "fuse_breach": fuse.breach,
        "pid": process.pid,
    }


# --- Subscription environment and provider failure classes -----------------

def billed_route_variables(env: dict[str, str]) -> list[str]:
    return [name for name in BILLED_ROUTE_VARIABLES if env.get(name)]


def require_subscription_environment(env: dict[str, str]) -> None:
    """Refuse a launch whose environment could route to a separately billed API."""
    present = billed_route_variables(env)
    if present:
        raise CodexPolicyError(
            "billed-route variable set in the launch environment: " + ", ".join(present)
            + ". Codex runs on the ChatGPT subscription login only and will not start."
        )


_QUOTA_TERMS = ("usage limit", "rate limit", "rate_limit", "quota", "429", "too many requests")
_AUTH_TERMS = ("not logged in", "unauthorized", "401", "please log in", "login required", "authentication",
               "refresh token", "token expired", "sign in again", "log in again")


def classify_provider_text(text: str) -> str:
    """quota_unavailable | needs_authentication | "" for provider error text."""
    lowered = str(text or "").lower()
    if any(term in lowered for term in _QUOTA_TERMS):
        return QUOTA_UNAVAILABLE
    if any(term in lowered for term in _AUTH_TERMS):
        return NEEDS_AUTHENTICATION
    return ""


def provider_failure_class(stdout: str, stderr: str) -> str:
    """Classify a failed ``codex exec --json`` run from provider errors only.

    Only stderr and the stream's ``error``/``turn.failed`` events are read, never agent or
    tool output, so a coding session that merely mentions "429" is not called a quota stop.
    """
    texts = [str(stderr or "")[-4000:]]
    for raw in str(stdout or "").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") in ("error", "turn.failed"):
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            texts.append(str(event.get("message") or error.get("message") or ""))
    return classify_provider_text("\n".join(texts))


# --- Codex account selection -------------------------------------------------
# Two independently paid ChatGPT subscription accounts, each with its own Codex home
# (queue/model_routes.json routes.codex.codex_accounts). Before each NEW model session
# or job one account is chosen; a running session or job never changes account.
#
#   eligible   the provider's own account and usage-window read, through that home's
#              authenticated session, is fresh, the account is a ChatGPT login (never
#              an API key), no provider restriction flag is set, the FIVE-HOUR window
#              has MORE than the threshold remaining, and no other window (weekly) is
#              exhausted. The weekly window has no reserve: it blocks only at 0% or
#              through a provider restriction flag. No five-hour window = not eligible.
#   order      sticky: the active account (persisted in ACTIVE_ACCOUNT_FILE) is kept
#              while eligible; only when it is not does selection move to the next
#              eligible account, which becomes active. A -> B -> A, never back early.
#              With no (valid) active record the first configured account leads.
#   none       the launch is refused as quota_unavailable or needs_authentication;
#              the active account is left unchanged.
#
# Remaining quota is never inferred from token totals: the provider's read already
# counts usage from every client of that ChatGPT account (T3, Codex CLI, other sessions). A probe starts ``codex app-server``
# for that home only and makes no model call. Snapshots are cached per account for
# ``recheck_seconds`` under a file lock, so concurrent launches probe once and a failing
# probe is not retried more than once per interval. Nothing here reads, copies or edits
# authentication files; credentials stay in their own CODEX_HOME.

QUOTA_UNAVAILABLE = "quota_unavailable"
NEEDS_AUTHENTICATION = "needs_authentication"
ACCOUNT_SLOT_RE = re.compile(r"^[A-Z]$")
FIVE_HOUR_WINDOW_MINUTES = 300
WEEKLY_WINDOW_MINUTES = 10_080
WINDOW_NAMES = {FIVE_HOUR_WINDOW_MINUTES: "five_hour", WEEKLY_WINDOW_MINUTES: "weekly"}
DEFAULT_SWITCH_REMAINING_PERCENT = 20
DEFAULT_RECHECK_SECONDS = 60
ACCOUNT_PROBE_TIMEOUT_SECONDS = 20.0
# A refusal names when it is worth asking again: a quota refusal at its earliest reset,
# an authentication refusal after this, never sooner than the recheck interval.
AUTH_RECHECK_SECONDS = 1800
ACCOUNT_STATE_DIR = Path("queue/codex_accounts")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_SNAPSHOT_VERSION = 1
ACTIVE_ACCOUNT_FILE = "active.json"
ACTIVE_ACCOUNT_VERSION = 1
_AUTH_STATES = frozenset({"needs_authentication", "api_key_account"})


class AccountUnavailable(CodexPolicyError):
    """No configured Codex account may start a new session now. Nothing was launched."""

    def __init__(self, failure_class: str, detail: str, *, accounts: list[dict], recheck_after: float):
        super().__init__(f"{failure_class}: {detail}")
        self.failure_class = failure_class
        self.detail = detail
        self.accounts = accounts
        self.recheck_after = recheck_after

    def record(self) -> dict:
        return {"failure_class": self.failure_class, "detail": self.detail, "accounts": self.accounts,
                "recheck_after": _iso(self.recheck_after)}


@dataclass(frozen=True)
class CodexAccount:
    slot: str
    codex_home: Path


@dataclass(frozen=True)
class AccountConfig:
    accounts: tuple
    threshold_percent: int = DEFAULT_SWITCH_REMAINING_PERCENT
    recheck_seconds: int = DEFAULT_RECHECK_SECONDS


def _iso(epoch: float | None) -> str:
    if epoch is None:
        return ""
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_codex_accounts(root: Path | None = None) -> AccountConfig:
    """The explicitly configured, authorized Codex homes, in preference order.

    Read from this code's own checkout (code and account list ship together), or ``root``'s."""
    path = (root / MODEL_ROUTES_PATH) if root is not None else REPOSITORY_ROOT / MODEL_ROUTES_PATH
    if not path.exists():
        path = REPOSITORY_ROOT / MODEL_ROUTES_PATH
    try:
        section = json.loads(path.read_text(encoding="utf-8"))["routes"]["codex"]["codex_accounts"]
        rows = section["accounts"]
        threshold = section.get("switch_remaining_percent", DEFAULT_SWITCH_REMAINING_PERCENT)
        recheck = section.get("recheck_seconds", DEFAULT_RECHECK_SECONDS)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CodexPolicyError(f"Codex accounts unavailable in {MODEL_ROUTES_PATH}: {exc}") from exc
    accounts, slots, homes = [], set(), set()
    for row in rows if isinstance(rows, list) else []:
        slot, home = str((row or {}).get("slot") or ""), Path(str((row or {}).get("codex_home") or ""))
        if not ACCOUNT_SLOT_RE.fullmatch(slot) or slot in slots:
            raise CodexPolicyError(f"Codex account slot {slot!r} is not a unique single capital letter")
        if not home.is_absolute() or str(home).startswith("/mnt/") or home in homes:
            raise CodexPolicyError(f"Codex account {slot} home {str(home)!r} must be a distinct absolute Linux path")
        slots.add(slot)
        homes.add(home)
        accounts.append(CodexAccount(slot, home))
    if not 1 <= len(accounts) <= 2:
        raise CodexPolicyError("between one and two Codex accounts must be configured")
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (threshold, recheck)) \
            or not 1 <= threshold <= 99 or not 10 <= recheck <= 900:
        raise CodexPolicyError("switch_remaining_percent must be 1-99 and recheck_seconds 10-900")
    return AccountConfig(tuple(accounts), threshold, recheck)


def account_target(account: CodexAccount, base: CodexRuntimeTarget = CODEX_TARGET,
                   selection: AccountSelection | None = None) -> CodexRuntimeTarget:
    return replace(base, codex_home=account.codex_home, account=account.slot, selection=selection)


# -- provider read (no model call) ---------------------------------------------

class _ProbeError(RuntimeError):
    pass


def probe_account(target: CodexRuntimeTarget, *, timeout: float = ACCOUNT_PROBE_TIMEOUT_SECONDS) -> dict:
    """Ask this home's own ``codex app-server`` for its account and usage windows.

    Read-only: ``account/read`` without a token refresh request and ``account/rateLimits/read``.
    Returns ``{"account": ..., "rate_limits": ...}`` raw; raises _ProbeError on any failure.
    """
    env = build_environment(target)
    for name in BILLED_ROUTE_VARIABLES:
        env.pop(name, None)
    command = [str(target.executable), "app-server", *SUBSCRIPTION_ROUTE_CONFIG]
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="aos-codex-account-") as scratch:
        try:
            process = subprocess.Popen(command, cwd=scratch, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        except OSError as exc:
            raise _ProbeError(f"app-server could not start: {exc}") from exc
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b""

        def send(message: dict) -> None:
            try:
                process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                process.stdin.flush()
            except OSError as exc:
                raise _ProbeError(f"app-server closed its input: {exc}") from exc

        def next_message() -> dict:
            nonlocal buffer
            while b"\n" not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _ProbeError("app-server did not answer the account read in time")
                if not selector.select(timeout=min(remaining, 0.5)):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise _ProbeError("app-server closed its output")
                buffer += chunk
            line, buffer = buffer.split(b"\n", 1)
            try:
                value = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}

        def request(ident: int, method: str, params: dict) -> dict:
            send({"id": ident, "method": method, "params": params})
            while True:
                message = next_message()
                if "method" in message and "id" in message:   # a server request; grant nothing
                    send({"id": message["id"], "error": {"code": -32601, "message": "account probe grants nothing"}})
                    continue
                if message.get("id") == ident:
                    if "error" in message:
                        raise _ProbeError(f"{method} failed: {json.dumps(message['error'])[:400]}")
                    result = message.get("result")
                    return result if isinstance(result, dict) else {}

        try:
            request(1, "initialize", {"clientInfo": {"name": "ttros_account_probe", "title": "TTROS account probe",
                                                     "version": "1.0.0"}})
            send({"method": "initialized"})
            account = request(2, "account/read", {"refreshToken": False})
            if not isinstance(account.get("account"), dict):
                return {"account": account, "rate_limits": None}
            return {"account": account, "rate_limits": request(3, "account/rateLimits/read", {})}
        finally:
            selector.close()
            try:
                process.stdin.close()
            except OSError:
                pass
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if process.poll() is not None:
                    break
                try:
                    os.killpg(process.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    continue
            process.stdout.close()


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def sanitize_probe(raw: dict) -> dict:
    """Keep only what eligibility needs: account type/plan and the usage windows. No email or IDs."""
    account = (raw.get("account") or {}).get("account") if isinstance(raw.get("account"), dict) else None
    out: dict[str, Any] = {
        "account_type": str(account.get("type") or "") if isinstance(account, dict) else "",
        "plan_type": str(account.get("planType") or "") if isinstance(account, dict) else "",
        "requires_openai_auth": bool((raw.get("account") or {}).get("requiresOpenaiAuth")) if isinstance(raw.get("account"), dict) else None,
        "rate_limits": None,
    }
    snapshot = raw.get("rate_limits")
    if isinstance(snapshot, dict):
        by_id = snapshot.get("rateLimitsByLimitId")
        limits = by_id.get("codex") if isinstance(by_id, dict) and isinstance(by_id.get("codex"), dict) else snapshot.get("rateLimits")
        limits = limits if isinstance(limits, dict) else {}
        windows = []
        for key in ("primary", "secondary"):
            window = limits.get(key)
            if window is None:
                continue
            window = window if isinstance(window, dict) else {}
            windows.append({"source": key, "used_percent": window.get("usedPercent"),
                            "window_minutes": window.get("windowDurationMins"), "resets_at": window.get("resetsAt")})
        out["rate_limits"] = {
            "ordinary_usage_allowed": snapshot.get("ordinaryUsageAllowed"),
            "rate_limit_reached_type": limits.get("rateLimitReachedType"),
            "spend_control_reached": limits.get("spendControlReached"),
            "windows": windows,
        }
    return out


def evaluate_account(slot: str, entry: dict | None, *, threshold: int, now: float, max_age: float) -> dict:
    """Eligibility of one account from its cached snapshot. Never eligible on doubt."""
    result: dict[str, Any] = {"slot": slot, "eligible": False, "state": "quota_unknown", "detail": "",
                              "checked_at": "", "windows": [], "recheck_after": now + max_age}
    if not isinstance(entry, dict) or entry.get("version") != ACCOUNT_SNAPSHOT_VERSION \
            or not isinstance(entry.get("checked_at"), (int, float)):
        result["detail"] = "no account snapshot"
        return result
    checked = float(entry["checked_at"])
    result["checked_at"] = _iso(checked)
    result["recheck_after"] = checked + max_age
    if not 0 <= now - checked <= max_age:
        result.update(state="stale", detail=f"snapshot from {_iso(checked)} is older than {int(max_age)}s")
        return result
    error = entry.get("error")
    if isinstance(error, dict):
        state = NEEDS_AUTHENTICATION if error.get("class") == NEEDS_AUTHENTICATION else "quota_unknown"
        result.update(state=state, detail=str(error.get("detail") or "account read failed")[:300])
        if state == NEEDS_AUTHENTICATION:
            result["recheck_after"] = checked + max(max_age, AUTH_RECHECK_SECONDS)
        return result
    snap = entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {}
    kind = snap.get("account_type")
    if kind == "apiKey":
        result.update(state="api_key_account", detail="home is signed in with an API key (separately billed); ChatGPT login required",
                      recheck_after=checked + max(max_age, AUTH_RECHECK_SECONDS))
        return result
    if kind != "chatgpt":
        result.update(state=NEEDS_AUTHENTICATION, detail="no ChatGPT login in this Codex home",
                      recheck_after=checked + max(max_age, AUTH_RECHECK_SECONDS))
        return result
    limits = snap.get("rate_limits") if isinstance(snap.get("rate_limits"), dict) else None
    windows_raw = (limits or {}).get("windows") or []
    if not windows_raw:
        result["detail"] = "the provider reported no usage window"
        return result
    windows, blocking = [], []
    has_five_hour = False
    for window in windows_raw:
        used, minutes, resets = (_int_or_none(window.get(key)) for key in ("used_percent", "window_minutes", "resets_at"))
        if used is None or not 0 <= used <= 100:
            result["detail"] = f"usage window {window.get('source')} is malformed"
            return result
        if resets is not None and resets <= now:
            result.update(state="stale", detail=f"usage window reset at {_iso(resets)} after this snapshot")
            return result
        name = WINDOW_NAMES.get(minutes, f"{minutes}m" if minutes else str(window.get("source")))
        remaining = 100 - used
        windows.append((name, remaining, _iso(resets) if resets else ""))
        if minutes == FIVE_HOUR_WINDOW_MINUTES:
            has_five_hour = True
            if remaining <= threshold:                  # the switching reserve: five-hour only
                blocking.append((name, remaining, resets))
        elif remaining == 0:                            # weekly and others: exhaustion only
            blocking.append((name, remaining, resets))
    result["windows"] = windows
    restricted = (limits.get("ordinary_usage_allowed") is False or bool(limits.get("rate_limit_reached_type"))
                  or limits.get("spend_control_reached") is True)
    if restricted:
        result.update(state="restricted", detail=(
            f"provider restriction: ordinaryUsageAllowed={limits.get('ordinary_usage_allowed')}, "
            f"rateLimitReachedType={limits.get('rate_limit_reached_type')}, spendControlReached={limits.get('spend_control_reached')}"))
        resets = [r for r in (_int_or_none(w.get("resets_at")) for w in windows_raw) if r]
        result["recheck_after"] = max(checked + max_age, min(resets)) if resets else checked + max(max_age, AUTH_RECHECK_SECONDS)
        return result
    summary = ", ".join(f"{name} {remaining}% remaining" for name, remaining, _r in windows)
    if not has_five_hour:
        result["detail"] = f"{summary}; the provider reported no five-hour window"
        return result
    if blocking:
        exhausted = any(remaining == 0 for _n, remaining, _r in blocking)
        result.update(state="exhausted" if exhausted else "below_threshold",
                      detail=f"{summary}; " + ", ".join(
                          f"{n} at {rem}% <= {threshold}%" if n == "five_hour" else f"{n} exhausted at {rem}%"
                          for n, rem, _r in blocking))
        reset_times = [r for _n, _rem, r in blocking if r]
        # Capacity returns only when every blocking window has reset.
        result["recheck_after"] = max(reset_times) if reset_times else checked + max_age
        return result
    result.update(eligible=True, state="eligible", detail=f"{summary} (five_hour > {threshold}%)")
    return result


def _read_snapshot(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_snapshot(path: Path, entry: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _usable(entry: dict | None, *, now: float, max_age: float) -> bool:
    if not isinstance(entry, dict) or entry.get("version") != ACCOUNT_SNAPSHOT_VERSION:
        return False
    checked = entry.get("checked_at")
    if not isinstance(checked, (int, float)) or not 0 <= now - float(checked) < max_age:
        return False
    windows = (((entry.get("snapshot") or {}).get("rate_limits") or {}).get("windows") or []) \
        if isinstance(entry.get("snapshot"), dict) else []
    # A window that has reset since the read makes the read stale.
    return not any(_int_or_none(w.get("resets_at")) is not None and w["resets_at"] <= now for w in windows if isinstance(w, dict))


def account_snapshot(account: CodexAccount, *, config: AccountConfig, reader: Callable[[CodexRuntimeTarget], dict] | None = None,
                     state_dir: Path | None = None, base: CodexRuntimeTarget = CODEX_TARGET,
                     clock: Callable[[], float] = time.time) -> dict:
    """The account's snapshot, re-read at most once per recheck interval across processes."""
    directory = _state_directory(state_dir)
    path = directory / f"{account.slot}.json"
    entry = _read_snapshot(path)
    if entry is not None and entry.get("codex_home") == str(account.codex_home) \
            and _usable(entry, now=clock(), max_age=config.recheck_seconds):
        return entry
    with (directory / f"{account.slot}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            entry = _read_snapshot(path)   # another launch may have read it while we waited
            if entry is not None and entry.get("codex_home") == str(account.codex_home) \
                    and _usable(entry, now=clock(), max_age=config.recheck_seconds):
                return entry
            target = account_target(account, base)
            entry = {"version": ACCOUNT_SNAPSHOT_VERSION, "slot": account.slot, "codex_home": str(account.codex_home),
                     "basis": "codex app-server account/read + account/rateLimits/read"}
            try:
                entry["snapshot"] = sanitize_probe((reader or probe_account)(target))
                entry["error"] = None
            except Exception as exc:  # a failed read makes the account ineligible, never "probably fine"
                text = f"{type(exc).__name__}: {str(exc)[:300]}"
                entry["snapshot"] = None
                entry["error"] = {"class": NEEDS_AUTHENTICATION if classify_provider_text(text) == NEEDS_AUTHENTICATION
                                  else "probe_failed", "detail": text}
            entry["checked_at"] = clock()
            _write_snapshot(path, entry)
            return entry
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _state_directory(state_dir: Path | None) -> Path:
    directory = Path(state_dir) if state_dir is not None else REPOSITORY_ROOT / ACCOUNT_STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_active_account(config: AccountConfig, state_dir: Path | None = None) -> str | None:
    """The persisted active slot, or None when absent, unreadable or no longer configured."""
    entry = _read_snapshot(_state_directory(state_dir) / ACTIVE_ACCOUNT_FILE)
    if not entry or entry.get("version") != ACTIVE_ACCOUNT_VERSION:
        return None
    slot = entry.get("slot")
    return slot if slot in {account.slot for account in config.accounts} else None


def select_codex_account(*, root: Path | None = None, config: AccountConfig | None = None,
                         reader: Callable[[CodexRuntimeTarget], dict] | None = None, state_dir: Path | None = None,
                         base: CodexRuntimeTarget = CODEX_TARGET, clock: Callable[[], float] = time.time) -> CodexRuntimeTarget:
    """Choose the Codex account for ONE new session or job; raise AccountUnavailable if none qualifies.

    Sticky: the active account is kept while eligible; a switch is persisted atomically under
    an exclusive lock, so concurrent launches (threads or processes) agree and a restart resumes it.
    """
    config = config or load_codex_accounts(root)
    directory = _state_directory(state_dir)
    with (directory / "active.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            active = read_active_account(config, directory)
            ordered = sorted(config.accounts, key=lambda a: a.slot != active) if active else list(config.accounts)
            evaluations = []
            for account in ordered:
                entry = account_snapshot(account, config=config, reader=reader, state_dir=directory, base=base, clock=clock)
                verdict = evaluate_account(account.slot, entry, threshold=config.threshold_percent, now=clock(),
                                           max_age=config.recheck_seconds)
                evaluations.append(verdict)
                if not verdict["eligible"]:
                    continue
                earlier = "; ".join(f"{row['slot']} not eligible ({row['state']}: {row['detail']})" for row in evaluations[:-1])
                if account.slot == active:
                    lead = f"{account.slot} active and eligible"
                elif active:
                    lead = f"switched {active} -> {account.slot}; {account.slot} eligible"
                else:
                    lead = f"{account.slot} eligible"
                reason = (f"{earlier}; " if earlier else "") + f"{lead}: {verdict['detail']}"
                if account.slot != active:
                    _write_snapshot(directory / ACTIVE_ACCOUNT_FILE, {
                        "version": ACTIVE_ACCOUNT_VERSION, "slot": account.slot, "previous": active,
                        "since": _iso(clock()), "reason": reason[:600]})
                selection = AccountSelection(
                    slot=account.slot, reason=reason, basis=str(entry.get("basis") or ""), checked_at=verdict["checked_at"],
                    windows=tuple(verdict["windows"]), threshold_percent=config.threshold_percent)
                return account_target(account, base, selection)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    every_auth = all(row["state"] in _AUTH_STATES for row in evaluations)
    failure = NEEDS_AUTHENTICATION if every_auth else QUOTA_UNAVAILABLE
    detail = "no Codex account is eligible: " + "; ".join(f"{row['slot']} {row['state']} ({row['detail']})" for row in evaluations)
    floor = clock() + config.recheck_seconds
    recheck = max(floor, min(float(row["recheck_after"]) for row in evaluations))
    raise AccountUnavailable(failure, detail, accounts=[{k: row[k] for k in ("slot", "state", "detail", "checked_at")}
                                                       for row in evaluations], recheck_after=recheck)


class AccountPin:
    """One account for one job or session: chosen at its first model launch, then never changed.

    A refusal is kept too, so a job that could not start a model does not keep re-asking
    mid-run; the job layer re-queues it for a bounded recheck instead.
    """

    def __init__(self, select: Callable[[], CodexRuntimeTarget] | None = None) -> None:
        self._select = select or select_codex_account
        self._lock = threading.Lock()
        self.granted: CodexRuntimeTarget | None = None
        self.refusal: AccountUnavailable | None = None

    def target(self) -> CodexRuntimeTarget:
        with self._lock:
            if self.granted is not None:
                return self.granted
            if self.refusal is not None:
                raise self.refusal
            try:
                self.granted = self._select()
            except AccountUnavailable as exc:
                self.refusal = exc
                raise
            return self.granted

    @property
    def refused_before_launch(self) -> bool:
        return self.granted is None and self.refusal is not None


def account_report(root: Path | None = None) -> list[dict]:
    """Read-only status of every configured account (operator check; no model call)."""
    config = load_codex_accounts(root)
    active = read_active_account(config)
    rows = []
    for account in config.accounts:
        entry = account_snapshot(account, config=config)
        row = evaluate_account(account.slot, entry, threshold=config.threshold_percent, now=time.time(),
                               max_age=config.recheck_seconds)
        row["active"] = account.slot == (active or config.accounts[0].slot)
        rows.append(row)
    return rows


if __name__ == "__main__":  # pragma: no cover - operator check
    import sys

    if sys.argv[1:] != ["accounts"]:
        raise SystemExit("usage: aos_codex_policy.py accounts")
    for row in account_report():
        row["recheck_after"] = _iso(row["recheck_after"])
        print(json.dumps(row, sort_keys=True))
