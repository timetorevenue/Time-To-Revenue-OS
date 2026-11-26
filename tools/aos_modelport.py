"""ModelPort: one narrow, tool-less, schema-constrained, pinned-model call.

The only way an agent package reaches a model. A call names an exact model and
effort, supplies a prompt and a JSON Schema, and receives parsed JSON plus exact
usage. It runs through the logged-in Codex CLI (subscription access), in a
read-only sandbox inside an empty scratch directory, with the coding-agent
instruction set replaced and optional harness features switched off. There is no
fallback to another model, provider or billed API route: a refusal or failure is
returned as data.

Two backends behind the one function:
  exec        a fresh ``codex exec`` process per call
  app-server  a warm ``codex app-server`` process, one fresh thread per call

Exact usage is recorded in the existing token ledger through step6_cost_control.

Each call is a fresh session, so unless the caller supplies a job's AccountPin, an
explicit target or a warm session (bound to one account), the call chooses its
Codex account (slot A/B) through aos_codex_policy.select_codex_account. The chosen
CODEX_HOME is set in the launched process's environment and the slot is recorded
with the call's exact usage.

Revisit: when the Codex CLI flags, feature names, app-server protocol, the
model route table or the Codex account contract change. · Last touched: 2026-11-16.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from aos_codex_policy import (
    BILLED_ROUTE_VARIABLES,
    CODEX_TARGET,
    SUBSCRIPTION_ROUTE_CONFIG,
    AccountPin,
    AccountUnavailable,
    CodexPolicyError,
    CodexRuntimeTarget,
    build_environment,
    cumulative_usage_snapshot,
    load_codex_tiers,
    select_codex_account,
)
import step6_cost_control as cost_control

MODELPORT_VERSION = "0.1.0"
PROVIDER = "openai-codex"
BACKENDS = ("exec", "app-server")
PROFILES = ("lean", "default")
EFFORTS = ("low", "medium", "high")
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_INSTRUCTIONS = (
    "You are a precise function inside a larger program. Use only the material in the "
    "request. Do not call tools. Reply with exactly one JSON object that satisfies the "
    "supplied schema, and nothing else."
)
# BILLED_ROUTE_VARIABLES (from aos_codex_policy): a key in the environment could silently
# move a call from the subscription to a separately billed API route, so the port refuses
# to launch while one is set.

# Features that add tool definitions or instruction blocks a tool-less call never
# uses. Measured with ``codex debug prompt-input`` on codex-cli 0.167.0.
LEAN_DISABLED_FEATURES = (
    "multi_agent", "plugins", "apps", "skill_search", "skill_mcp_dependency_install",
    "shell_tool", "unified_exec", "shell_snapshot", "view_image", "image_generation",
    "browser_use", "computer_use", "in_app_browser", "goals", "hooks", "tool_suggest",
    "sleep_tool", "workspace_dependencies", "code_mode_host", "code_mode_interrupt",
)
LEAN_CONFIG = (
    "skills.enabled=false",
    "skills.bundled.enabled=false",
    "include_permissions_instructions=false",
    "include_environment_context=false",
    "include_collaboration_mode_instructions=false",
)


class ModelPortRefused(RuntimeError):
    """The call was refused before any model was contacted."""


def allowed_models(root: Path | None = None) -> list[str]:
    """Models an agent may pin: the supported list in queue/model_routes.json."""
    return list(load_codex_tiers(root)["supported_models"])


def lean_arguments(profile: str, instructions_file: Path | None) -> list[str]:
    if profile == "default":
        return []
    arguments: list[str] = []
    for feature in LEAN_DISABLED_FEATURES:
        arguments += ["--disable", feature]
    for override in LEAN_CONFIG:
        arguments += ["--config", override]
    if instructions_file is not None:
        arguments += ["--config", f"model_instructions_file={json.dumps(str(instructions_file))}"]
    return arguments


def build_exec_command(
    target: CodexRuntimeTarget, *, model: str, effort: str, workdir: Path,
    schema_file: Path, last_message_file: Path, profile: str, instructions_file: Path | None,
) -> list[str]:
    return [
        str(target.executable),
        *SUBSCRIPTION_ROUTE_CONFIG,
        "--sandbox", "read-only",
        "--ask-for-approval", "never",
        "-C", str(workdir),
        "--config", f"model_reasoning_effort={effort}",
        *lean_arguments(profile, instructions_file),
        "exec", "--model", model,
        "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
        "--output-schema", str(schema_file),
        "--output-last-message", str(last_message_file),
        "-",
    ]


def build_app_server_command(target: CodexRuntimeTarget, *, profile: str) -> list[str]:
    # Instructions are supplied per thread (baseInstructions), not by file.
    return [str(target.executable), "app-server", *SUBSCRIPTION_ROUTE_CONFIG, *lean_arguments(profile, None)]


def _preflight(model: str, effort: str, backend: str, profile: str, output_schema: Any,
               env: dict[str, str], root: Path | None) -> None:
    if backend not in BACKENDS:
        raise ModelPortRefused(f"unknown backend {backend!r}; expected one of {BACKENDS}")
    if profile not in PROFILES:
        raise ModelPortRefused(f"unknown profile {profile!r}; expected one of {PROFILES}")
    if effort not in EFFORTS:
        raise ModelPortRefused(f"unsupported effort {effort!r}; expected one of {EFFORTS}")
    if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
        raise ModelPortRefused("output_schema must be a JSON Schema whose top-level type is object")
    present = [name for name in BILLED_ROUTE_VARIABLES if env.get(name)]
    if present:
        raise ModelPortRefused(
            "billed-route variable set in the launch environment: " + ", ".join(present)
            + ". ModelPort uses subscription access only and will not start."
        )
    try:
        supported = allowed_models(root)
    except CodexPolicyError as exc:
        raise ModelPortRefused(str(exc)) from exc
    if model not in supported:
        raise ModelPortRefused(
            f"model {model!r} is not in queue/model_routes.json supported_models {supported}"
        )


def _kill_group(process: subprocess.Popen, grace: float = 2.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue


def _parse_json_object(text: str) -> tuple[dict | None, str]:
    stripped = str(text or "").strip()
    if not stripped:
        return None, "the model returned no final message"
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"final message is not JSON: {exc}"
    if not isinstance(value, dict):
        return None, "final message is JSON but not an object"
    return value, ""


def _missing_required(value: dict, schema: dict) -> list[str]:
    """Cheap top-level check; full validation belongs to the agent runtime."""
    return [key for key in schema.get("required") or [] if key not in value]


def _last_agent_message(stdout: str) -> str:
    text = ""
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            text = item["text"]
    return text


def _thread_id(stdout: str) -> str:
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            return str(event.get("thread_id") or "")
    return ""


def _failure_class(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("usage limit", "rate limit", "quota", "429", "too many requests")):
        return "quota_unavailable"
    if any(term in lowered for term in ("not logged in", "unauthorized", "401", "login", "authentication")):
        return "authentication_unavailable"
    if "model" in lowered and any(term in lowered for term in ("not found", "not supported", "unknown", "does not exist")):
        return "model_unavailable"
    return "provider_call_failed"


# Used only by cancellable exec calls. Fork the EOF guardian BEFORE exec, so an
# executor crash cannot leave an unguarded provider in the launch/registration gap.
# The guardian has no tools, queue or policy; it only kills this process group.
_GUARDED_EXEC = '''import os,signal,sys
fd=int(sys.argv[1]); group=os.getpgrp()
if os.fork()==0:
 os.setsid()
 for std in (0,1,2):
  if std!=fd: os.close(std)
 try:
  while os.read(fd,1): pass
 finally:
  try: os.killpg(group,signal.SIGKILL)
  except ProcessLookupError: pass
 os._exit(0)
os.close(fd)
os.execvpe(sys.argv[2],sys.argv[2:],os.environ)
'''


def _run_exec(prompt: str, *, model: str, effort: str, output_schema: dict, instructions: str,
              profile: str, timeout: float, target: CodexRuntimeTarget, env: dict[str, str],
              cancelled=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="aos-modelport-") as scratch:
        base = Path(scratch)
        workdir = base / "empty"
        workdir.mkdir()
        schema_file = base / "schema.json"
        schema_file.write_text(json.dumps(output_schema), encoding="utf-8")
        instructions_file = base / "instructions.md"
        instructions_file.write_text(instructions, encoding="utf-8")
        last_message_file = base / "last_message.txt"
        command = build_exec_command(
            target, model=model, effort=effort, workdir=workdir, schema_file=schema_file,
            last_message_file=last_message_file, profile=profile,
            instructions_file=instructions_file if profile == "lean" else None,
        )
        body = prompt if profile == "lean" else f"{instructions}\n\n{prompt}"
        started = time.monotonic()
        read_fd = write_fd = None
        try:
            if cancelled is not None:
                if not sys.platform.startswith('linux') or cancelled():
                    raise OSError('Cancellable call requires a live Linux execution scope')
                read_fd, write_fd = os.pipe()
                command = [sys.executable, '-c', _GUARDED_EXEC, str(read_fd), *command]
            process = subprocess.Popen(
                command, cwd=str(workdir), env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                pass_fds=(read_fd,) if read_fd is not None else (),
            )
        except OSError as exc:
            if write_fd is not None:
                os.close(write_fd)
            return {"status": "failed", "failure_class": "process_start_failed", "detail": str(exc),
                    "latency_seconds": 0.0, "usage": {"available": False}, "text": "", "session_id": ""}
        finally:
            if read_fd is not None:
                os.close(read_fd)
        timed_out = False
        was_cancelled = False
        try:
            if cancelled is None:
                out, err = process.communicate(body.encode("utf-8"), timeout=timeout)
            else:
                payload = body.encode('utf-8')
                while True:
                    was_cancelled = bool(cancelled())
                    if was_cancelled or time.monotonic() - started >= timeout:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        out, err = process.communicate(payload, timeout=min(.1, timeout - (time.monotonic()-started)))
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
        except subprocess.TimeoutExpired:
            timed_out = not was_cancelled
            _kill_group(process, grace=.2 if cancelled is not None else 2)
            out, err = process.communicate(**({'timeout': 2} if cancelled is not None else {}))
        except BaseException:
            _kill_group(process, grace=.2)
            raise
        finally:
            if write_fd is not None:
                # EOF kills descendants even if the provider leader already exited.
                os.close(write_fd)
        latency = round(time.monotonic() - started, 3)
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        try:
            text = last_message_file.read_text(encoding="utf-8")
        except OSError:
            text = ""
        text = text or _last_agent_message(stdout)
        usage = cumulative_usage_snapshot(stdout)
        result = {"latency_seconds": latency, "usage": usage, "text": text,
                  "session_id": _thread_id(stdout), "returncode": process.returncode}
        if was_cancelled:
            return {**result, "status": "cancelled", "failure_class": "execution_cancelled",
                    "detail": "execution scope disarmed or cancelled"}
        if timed_out:
            return {**result, "status": "timeout", "failure_class": "execution_timeout",
                    "detail": f"no completed turn within {timeout:.0f}s"}
        if process.returncode != 0:
            tail = (stderr or stdout)[-1200:]
            return {**result, "status": "failed", "failure_class": _failure_class(stderr + stdout), "detail": tail}
        return {**result, "status": "ok", "failure_class": "", "detail": ""}


class AppServerSession:
    """A warm ``codex app-server`` process. Each call uses a fresh ephemeral thread."""

    def __init__(self, *, profile: str = "lean", target: CodexRuntimeTarget = CODEX_TARGET,
                 env: dict[str, str] | None = None, startup_timeout: float = 30.0):
        self.profile = profile
        self.target = target
        self.env = dict(env) if env is not None else build_environment(target)
        # The process serves exactly one account: its own Codex home, never another's.
        self.env["CODEX_HOME"] = str(target.codex_home)
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()
        self._scratch: tempfile.TemporaryDirectory | None = None
        self.workdir: Path | None = None
        self.startup_seconds: float | None = None

    def __enter__(self) -> "AppServerSession":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return
        self._scratch = tempfile.TemporaryDirectory(prefix="aos-modelport-appserver-")
        self.workdir = Path(self._scratch.name) / "empty"
        self.workdir.mkdir()
        started = time.monotonic()
        self.process = subprocess.Popen(
            build_app_server_command(self.target, profile=self.profile), cwd=str(self.workdir), env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
        threading.Thread(target=self._pump, name="modelport-appserver-reader", daemon=True).start()
        self._request("initialize", {"clientInfo": {
            "name": "ttros_modelport", "title": "TTROS ModelPort", "version": MODELPORT_VERSION,
        }}, deadline=time.monotonic() + self.startup_timeout)
        self._send({"method": "initialized"})
        self.startup_seconds = round(time.monotonic() - started, 3)

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            _kill_group(process)
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None

    def _pump(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        for raw in process.stdout:
            try:
                self._lines.put(json.loads(raw.decode("utf-8", errors="replace")))
            except json.JSONDecodeError:
                continue
        self._lines.put(None)

    def _send(self, message: dict) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError("app-server is not running")
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        process.stdin.flush()

    def _read(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("app-server did not answer in time")
        try:
            message = self._lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("app-server did not answer in time") from exc
        if message is None:
            raise RuntimeError("app-server closed its output")
        if "method" in message and "id" in message:
            # A server-initiated request (approval, tool call). ModelPort grants nothing.
            self._send({"id": message["id"], "error": {"code": -32601, "message": "ModelPort grants no tools or approvals"}})
        return message

    def _request(self, method: str, params: dict, *, deadline: float, on_event=None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._read(deadline)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {json.dumps(message['error'])[:600]}")
                return message.get("result") or {}
            if on_event is not None:
                on_event(message)

    def rate_limits(self, timeout: float = 15.0) -> dict:
        """Read-only account rate-limit snapshot; makes no model call."""
        with self._lock:
            self.start()
            return self._request("account/rateLimits/read", {}, deadline=time.monotonic() + timeout)

    def run(self, prompt: str, *, model: str, effort: str, output_schema: dict,
            instructions: str, timeout: float, images: tuple[bytes, ...] = ()) -> dict:
        with self._lock, ExitStack() as resources:
            started = time.monotonic()
            deadline = started + timeout
            state: dict[str, Any] = {"text": "", "usage": None, "turn": None}

            def on_event(message: dict) -> None:
                method, params = message.get("method"), message.get("params") or {}
                if method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                        state["text"] = item["text"]
                elif method == "thread/tokenUsage/updated":
                    state["usage"] = (params.get("tokenUsage") or {}).get("total")
                elif method == "turn/completed":
                    state["turn"] = params.get("turn") or {}
                elif method == "error" and not state.get("error"):
                    state["error"] = json.dumps(params)[:600]

            thread_id = ""
            try:
                self.start()
                thread_params: dict[str, Any] = {
                    "model": model, "cwd": str(self.workdir), "sandbox": "read-only",
                    "approvalPolicy": "never", "ephemeral": True, "serviceName": "ttros_modelport",
                }
                if self.profile == "lean":
                    thread_params["baseInstructions"] = instructions
                thread = self._request("thread/start", thread_params, deadline=deadline, on_event=on_event)
                thread_id = str((thread.get("thread") or {}).get("id") or "")
                body = prompt if self.profile == "lean" else f"{instructions}\n\n{prompt}"
                inputs = [{"type": "text", "text": body}]
                if images:
                    image_dir = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix="aos-modelport-input-")))
                    for index, image in enumerate(images):
                        path = image_dir / f"{index}.jpg"
                        path.write_bytes(image)
                        inputs.append({"type": "localImage", "path": str(path)})
                self._request("turn/start", {
                    "threadId": thread_id, "input": inputs,
                    "model": model, "effort": effort, "outputSchema": output_schema,
                }, deadline=deadline, on_event=on_event)
                while state["turn"] is None:
                    on_event(self._read(deadline))
                # Usage can trail the completion notice by one message.
                settle = time.monotonic() + 1.0
                while state["usage"] is None and time.monotonic() < settle:
                    try:
                        on_event(self._read(settle))
                    except TimeoutError:
                        break
            except TimeoutError:
                self.close()  # an in-flight turn cannot be trusted; start clean next time
                return {"status": "timeout", "failure_class": "execution_timeout",
                        "detail": f"no completed turn within {timeout:.0f}s", "text": state["text"],
                        "usage": _app_server_usage(state["usage"]), "session_id": thread_id,
                        "latency_seconds": round(time.monotonic() - started, 3)}
            except (RuntimeError, OSError) as exc:
                self.close()
                return {"status": "failed", "failure_class": _failure_class(str(exc)), "detail": str(exc)[:1200],
                        "text": state["text"], "usage": _app_server_usage(state["usage"]), "session_id": thread_id,
                        "latency_seconds": round(time.monotonic() - started, 3)}
            latency = round(time.monotonic() - started, 3)
            turn = state["turn"] or {}
            result = {"text": state["text"], "usage": _app_server_usage(state["usage"]),
                      "session_id": thread_id, "latency_seconds": latency}
            if turn.get("status") != "completed":
                detail = json.dumps(turn.get("error") or state.get("error") or turn.get("status"))[:1200]
                return {**result, "status": "failed", "failure_class": _failure_class(detail), "detail": detail}
            return {**result, "status": "ok", "failure_class": "", "detail": ""}


def _app_server_usage(total: Any) -> dict:
    if not isinstance(total, dict):
        return {"available": False, "reason": "app-server reported no thread token usage"}
    try:
        input_tokens, output_tokens = int(total["inputTokens"]), int(total["outputTokens"])
    except (KeyError, TypeError, ValueError):
        return {"available": False, "reason": "app-server token usage lacks input/output"}
    usage: dict[str, Any] = {"available": True, "input_tokens": input_tokens, "output_tokens": output_tokens,
                             "cumulative_tokens": input_tokens + output_tokens}
    for source, name in (("cachedInputTokens", "cached_input_tokens"), ("reasoningOutputTokens", "reasoning_output_tokens")):
        if isinstance(total.get(source), int) and not isinstance(total.get(source), bool):
            usage[name] = total[source]
    return usage


def _ledger_usage(usage: dict) -> dict:
    exact = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
             "cache_semantics": "included_in_provider_input"}
    if isinstance(usage.get("cached_input_tokens"), int):
        exact["cached_input_tokens"] = usage["cached_input_tokens"]
    if isinstance(usage.get("reasoning_output_tokens"), int):
        exact["reasoning_tokens"] = usage["reasoning_output_tokens"]
    return exact


def _resolve_target(target: CodexRuntimeTarget | None, account: AccountPin | None,
                    session: "AppServerSession | None") -> CodexRuntimeTarget:
    """The one account this call runs on. Raises AccountUnavailable or ModelPortRefused."""
    if session is not None:
        bound = session.target
        chosen = target if target is not None else account.target() if account is not None else None
        if chosen is not None and chosen.codex_home != bound.codex_home:
            raise ModelPortRefused(f"warm session belongs to Codex account {bound.account}, not {chosen.account}; "
                                   "credentials are never mixed")
        return bound
    if target is not None:
        return target
    if account is not None:
        return account.target()
    return select_codex_account()


def call(
    prompt: str,
    *,
    model: str,
    effort: str,
    output_schema: dict,
    instructions: str = DEFAULT_INSTRUCTIONS,
    backend: str = "exec",
    profile: str = "lean",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    agent: str = "",
    work_item_id: str = "",
    session: AppServerSession | None = None,
    record: bool = True,
    root: Path | None = None,
    target: CodexRuntimeTarget | None = None,
    env: dict[str, str] | None = None,
    cancelled=None,
    account: AccountPin | None = None,
    images: tuple[bytes, ...] = (),
) -> dict:
    """Make one pinned, tool-less, schema-constrained model call.

    Returns a dict and never raises for provider or policy outcomes:
      status   ok | invalid_output | refused | failed | timeout
      json     the parsed object when status is ok
      usage    exact counters from the CLI, or {"available": False, ...}
      ledger   what was written to the token ledger
    ``session`` reuses a warm AppServerSession; without one the app-server
    backend starts and stops its own process for the single call.
    ``account`` is a job's AccountPin; ``target`` an already chosen account. With
    neither (and no session) the call selects its account itself. No eligible
    account -> status refused, failure_class quota_unavailable/needs_authentication.
    """
    invocation_id = f"modelport-{uuid.uuid4().hex}"
    ledger_root = Path(root) if root is not None else cost_control.ROOT
    result: dict[str, Any] = {
        "ok": False, "status": "refused", "json": None, "text": "", "model": model, "effort": effort,
        "backend": backend, "profile": profile, "agent": agent, "invocation_id": invocation_id,
        "session_id": "", "latency_seconds": 0.0, "usage": {"available": False},
        "failure_class": "", "detail": "", "ledger": {"recorded": False},
        "modelport_version": MODELPORT_VERSION, "codex_account": None,
    }
    try:
        if images and (backend != "app-server" or len(images) > 2 or any(
                not isinstance(image, bytes) or len(image) > 1024 * 1024 or not image.startswith(b"\xff\xd8\xff")
                for image in images)):
            raise ModelPortRefused("Visual input requires up to two normalized JPEGs via the subscription app server")
        if cancelled is not None and backend != 'exec':
            raise ModelPortRefused('Cancellation requires the guarded exec backend')
        # Policy checks first, so a call refused anyway never spends an account read.
        _preflight(model, effort, backend, profile, output_schema,
                   env if env is not None else session.env if session is not None else dict(os.environ), root)
        target = _resolve_target(target, account, session)
    except AccountUnavailable as exc:
        result.update(failure_class=exc.failure_class, detail=exc.detail, codex_account=exc.record())
        return result
    except ModelPortRefused as exc:
        result.update(failure_class="preflight_refused", detail=str(exc))
        return result
    result["codex_account"] = target.account_record()
    if env is not None:
        launch_env = dict(env)
        launch_env["CODEX_HOME"] = str(target.codex_home)
    else:
        launch_env = session.env if session is not None else build_environment(target)
    selection = {"tier": "pinned", "model": model, "reasoning_effort": effort, "actual_model": model,
                 "reason": f"ModelPort pinned call ({backend}/{profile})" + (f" for {agent}" if agent else ""),
                 "actual_model_source": "ModelPort --model argument", "codex_account": result["codex_account"],
                 # Which Codex binary ran: reliability evidence tells a real provider stop from a stand-in.
                 "transport": {"executable": str(target.executable),
                               "production_binary": Path(target.executable) == Path(CODEX_TARGET.executable)}}
    surface = "modelport:codex" + (f":{agent}" if agent else "")
    try:
        scope = None
        if record:
            scope, _ = cost_control.codex_session_preflight(invocation_id, work_item_id=work_item_id, root=ledger_root)
    except (ModelPortRefused, cost_control.CostControlError) as exc:
        result.update(failure_class="token_fuse_paused" if isinstance(exc, cost_control.FusePausedError) else "preflight_refused",
                      detail=str(exc))
        return result

    if backend == "exec":
        outcome = _run_exec(prompt, model=model, effort=effort, output_schema=output_schema,
                            instructions=instructions, profile=profile, timeout=timeout, target=target, env=launch_env,
                            **({'cancelled': cancelled} if cancelled is not None else {}))
    else:
        owned = session is None
        active = session or AppServerSession(profile=profile, target=target, env=launch_env)
        try:
            outcome = active.run(prompt, model=model, effort=effort, output_schema=output_schema,
                                 instructions=instructions, timeout=timeout, **({"images": images} if images else {}))
        finally:
            if owned:
                active.close()
    result.update({key: outcome[key] for key in ("status", "failure_class", "detail", "text", "usage", "session_id", "latency_seconds")})

    if result["status"] == "ok":
        parsed, problem = _parse_json_object(result["text"])
        missing = _missing_required(parsed, output_schema) if parsed is not None else []
        if parsed is None or missing:
            result.update(status="invalid_output", failure_class="schema_invalid_output",
                          detail=problem or f"missing required keys: {missing}")
        else:
            result.update(ok=True, json=parsed)

    if record and scope is not None:
        usage = result["usage"]
        try:
            if usage.get("available"):
                written = cost_control.record_invocation(
                    scope, invocation_id=invocation_id, provider=PROVIDER, model=model, usage=_ledger_usage(usage),
                    root=ledger_root, surface=surface, work_item_id=work_item_id, model_selection=selection,
                )
            else:
                # A launched call with unknown usage is recorded as unknown, never as zero.
                written = cost_control.record_unavailable_invocation(
                    scope, invocation_id=invocation_id, provider=PROVIDER, model=model,
                    reason=f"ModelPort {result['status']}: {usage.get('reason') or result['failure_class'] or 'usage unavailable'}",
                    root=ledger_root, surface=surface, work_item_id=work_item_id, model_selection=selection,
                )
            result["ledger"] = {"recorded": bool(written.get("recorded")), "exact": bool(usage.get("available"))}
        except cost_control.CostControlError as exc:
            result["ledger"] = {"recorded": False, "error": str(exc)}
    return result
