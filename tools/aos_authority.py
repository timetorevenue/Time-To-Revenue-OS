"""Authorization at real side-effect boundaries, and the one operator Telegram send.

This is the code form of rules/runtime_permission_policy.md for exact consequential
actions. It is called where an effect is about to happen (a job's proposed effect, a
connector write, a Telegram notice), never as a gate on ordinary work. It reads only
what a job actually carries (aos_jobs: ``requested_by`` and ``restrictions``).

Authorized:
  operator notification   a declared system notification kind to the configured
                          operator Telegram chat or internal AgentMail inbox
  routine business write  an ordinary Drive/Docs/Sheets/Calendar write or an email
                          draft, unless the job's own restrictions forbid it
  test launch draft       an unambiguous affirmative TEST ONLY request; test checkout
                          also requires no_connector_writes to be absent

Held, with the reason:
  Brain change            BRAIN_* actions; the Business Brain changes only through its
                          validated writers, so a job proposes and never writes
  task restriction        a routine write the job's restrictions forbid
  explicit command        everything else (sending, sharing, publishing, destruction,
                          money, new scopes). The policy needs Casey's own command naming
                          the exact action and target; no job carries one, so it is held.

D-2026-11-15-01 remains open for general job effects. Agent releases now have a
request-local command from the existing authenticated Dashboard boundary; it is bound
to root, exact agent/version, package bytes and target state, expires and is single-use.
No serialized job field, historical approval or approver name grants this capability.
See proofs/m5_acceptance_2026-11-15/receipt.md.
Website Studio publication/rollback (WEBSITE_PUBLISH / WEBSITE_ROLLBACK) uses the same shape: a
request-local command from the verified Dashboard operator, bound to the exact release digest and
target, carrying the operator's typed command that names the action, release and target; it
expires and is single-use (dashboard/backend/website_studio_releases.py).

Allowlists constrain destinations; they never authorize by themselves. Sources,
tags, model output and historical approvals never authorize. The old Work Queue
approval fields (approved_external_*, stop_conditions, tags) are no longer read:
nothing in the clean-break runtime writes them.

Revisit: when rules/runtime_permission_policy.md, queue/notifications.json or the job
record's authorization fields change. · Last touched: 2026-11-15.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from aos_paths import aos_root
except ModuleNotFoundError:  # imported as tools.aos_authority
    from tools.aos_paths import aos_root

NOTIFICATIONS_PATH = Path("queue") / "notifications.json"
BRIDGE_PATH = Path("connectors") / "telegram_bridge" / "telegram_bridge.py"

# Ordinary Workspace writes run automatically; sending/sharing, destruction and money stay explicit.
ROUTINE_WRITE_TOOLKITS = ("GOOGLEDRIVE", "GOOGLEDOCS", "GOOGLESHEETS", "GOOGLECALENDAR")
PROTECTED_ACTION_TOKENS = frozenset({
    "SEND", "REPLY", "FORWARD", "SHARE", "SHARING", "PERMISSION", "PERMISSIONS", "INVITE", "PUBLISH", "POST",
    "TRANSFER", "DELETE", "TRASH", "EMPTY", "REMOVE", "CLEAR", "PURGE", "DESTROY",
    "PAY", "PAYMENT", "CHARGE", "PURCHASE", "BUY", "REFUND",
})
STANDING_NOTIFICATION_KINDS = frozenset({"operational_notification", "queue_status", "chatgpt_wake"})
NOTIFICATION_KIND_ENV = "TTROS_NOTIFICATION_KIND"
# Task-specific restrictions a job can carry (aos_jobs.create/resume). They only ever narrow the policy.
RESTRICTIONS = {
    "no_connector_reads": "Casey asked for no connector reads on this task",
    "no_brain_writes": "Casey asked for nothing to be written to the Business Brain on this task",
    "no_connector_writes": "Casey asked for no connector or provider changes on this task",
    "no_calendar_writes": "Casey asked for no Calendar changes on this task",
}
BRAIN_ACTION_PREFIX = "BRAIN_"
EXPLICIT_COMMAND_DETAIL = ("needs Casey's explicit command naming this action and target; a job does not carry "
                           "one, so it is held for him")


class SendError(RuntimeError):
    """The Telegram bridge did not confirm delivery."""


def operator_identities() -> set[str]:
    raw = os.environ.get("TTROS_REMOTE_PC_IDENTITIES", "owner.personal@example.invalid")
    return {value.strip().lower() for value in raw.split(",") if value.strip()}


def external_job_operators() -> set[str]:
    raw = os.environ.get("TTROS_SHARED_BRAIN_JOBS_IDENTITIES", "")
    granted = {value.strip().lower() for value in raw.split(",") if value.strip()}
    return granted & operator_identities()


def external_job_operator(identity: str) -> bool:
    return identity.lower() in external_job_operators()


def load_notifications(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / NOTIFICATIONS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    allowlist = data.get("allowlist") if isinstance(data.get("allowlist"), dict) else {}
    return {
        "telegram": [str(value) for value in allowlist.get("telegram") or []],
        "agentmail_internal": [str(value) for value in allowlist.get("agentmail_internal") or []],
    }


def _tokens(slug: str, toolkit: str) -> set[str]:
    return {token for token in slug[len(toolkit) + 1:].split("_") if token}


def routine_business_write(action: str) -> bool:
    """True for an ordinary Workspace write or an email draft with no protected verb."""
    slug = str(action or "").strip().upper()
    if slug.startswith("GMAIL_"):
        tokens = _tokens(slug, "GMAIL")
        return "DRAFT" in tokens and not tokens & PROTECTED_ACTION_TOKENS
    toolkit = next((name for name in ROUTINE_WRITE_TOOLKITS if slug.startswith(name + "_")), None)
    if toolkit is None:
        return False
    tokens = _tokens(slug, toolkit)
    return bool(tokens) and not tokens & PROTECTED_ACTION_TOKENS


def restrictions_of(job: dict | None) -> list[str]:
    """The known restrictions a job carries, in a stable order; unknown values are ignored."""
    raw = (job or {}).get("restrictions") if isinstance(job, dict) else None
    values = {str(value).strip() for value in raw or [] if str(value).strip()}
    return [name for name in RESTRICTIONS if name in values]


def _held(basis: str, detail: str, *, action: str = "") -> dict[str, Any]:
    return {"authorized": False, "basis": basis, "detail": detail, "clarification_required": "CALENDAR" in action}


def launch_request_problem(request: str) -> str:
    verbs = r'(?:create|build|make|prepare|start|launch|submit|execute|run)'
    negative_verbs = r'(?:creat\w*|build\w*|built|mak\w*|made|prepar\w*|start\w*|launch\w*|submit\w*|execut\w*|run\w*)'
    negative = rf"\b(?:do\s+not|don['’]t|never|must\s+not|cannot|can['’]t|should\s+not|shouldn['’]t|avoid|without)\b[^.;!?\n]*\b{negative_verbs}\b"
    information_only = r'\b(?:explain|describe|discuss|review)\b[^.;!?\n]*\bonly\b|\bonly\s+(?:explain|describe|discuss|review)\b|\bno\s+(?:launch|jobs?)\b|\bnot\s+authorized\b'
    if re.search(negative, request, re.I) or re.search(information_only, request, re.I):
        return 'Explicit negative or explanation-only task constraint; no launch is authorized'
    if '?' in request or not re.match(rf'^\s*(?:please\s+)?{verbs}\b', request, re.I):
        return 'An affirmative launch instruction is required; mentioning a test offer is not authorization'
    if not re.search(r'\btest\s+only\b', request, re.I) or not re.search(r'\bworkflow\s+audit\b', request, re.I):
        return 'An explicit TEST ONLY workflow audit request is required'
    return ''


class ReleaseCommand:
    """Request-local capability. Never deserialize this from jobs, CLI flags or model output."""
    def __init__(self, root, agent, state, digest, operator):
        import time
        self.root, self.agent, self.state = Path(root).resolve(), agent, state
        self.digest, self.operator = digest, operator
        self.deadline, self.used = time.monotonic() + 60, False


def authenticated_release_command(root: Path, headers, agent: str, state: str, digest: str):
    """Authenticate at the existing Dashboard boundary before constructing a capability.

    Headers must come directly from the HTTP request through the trusted Access/Serve proxy.
    This grants only the explicit release in that request, for the exact package bytes.
    """
    from remote_pc import authenticate
    operator = authenticate(headers)
    return ReleaseCommand(root, agent, state, digest, operator)


class WebsiteReleaseCommand:
    """Request-local publish/rollback capability. Never deserialize this from jobs, files or model output."""
    def __init__(self, action, target, digest, phrase, typed, operator):
        import time
        self.action, self.target, self.digest = action, target, digest
        self.phrase, self.typed, self.operator = phrase, typed, operator
        self.deadline, self.used = time.monotonic() + 60, False


def authenticated_website_release_command(headers, action: str, target: str, digest: str, phrase: str, typed: str):
    """Authenticate the operator at the existing Dashboard boundary, then bind the typed command."""
    from remote_pc import authenticate
    operator = authenticate(headers)
    return WebsiteReleaseCommand(action, target, digest, phrase, typed, operator)


def _website_release_decision(action, target, website_release):
    import time
    command, digest = website_release if isinstance(website_release, tuple) and len(website_release) == 2 else (None, None)
    if type(command) is not WebsiteReleaseCommand or command.used or time.monotonic() > command.deadline:
        return None
    if action not in ("WEBSITE_PUBLISH", "WEBSITE_ROLLBACK") or command.action != action or command.target != target:
        return None
    # The operator's own words must name this exact action, release and target.
    if not digest or command.digest != digest or command.typed != command.phrase:
        return None
    command.used = True
    return {"authorized": True, "basis": "authenticated_explicit_publication_command", "detail": "",
            "clarification_required": False, "operator": command.operator, "release_digest": digest,
            "command": command.typed}


def _release_decision(root, action, target, command):
    import time
    if type(command) is not ReleaseCommand or command.used or time.monotonic() > command.deadline:
        return None
    if command.root != Path(root).resolve() or command.agent != target or action != f"AGENT_RELEASE_{command.state.upper()}":
        return None
    from aos_agents.package import load_package
    if load_package(target, root).content_sha256 != command.digest:
        return None
    command.used = True
    return {"authorized": True, "basis": "authenticated_explicit_release_command", "detail": "",
            "clarification_required": False, "operator": command.operator, "package_sha256": command.digest}


def action_authorization(root: Path, job: dict | None = None, *, action: str = "", target: str = "",
                         notification_kind: str | None = None, release_command=None,
                         website_release=None) -> dict[str, Any]:
    """Classify one exact consequential action for a job (or None outside a job).

    Returns {authorized, basis, detail, clarification_required}.
    """
    kind = str(notification_kind or os.environ.get(NOTIFICATION_KIND_ENV) or "").strip()
    standing = kind if kind in STANDING_NOTIFICATION_KINDS else None
    configured = load_notifications(root)
    exact_action = str(action or "").strip().upper()
    exact_target = str(target or "").strip()
    restrictions = restrictions_of(job)

    if exact_action in ('PRODUCT_LAUNCH_DRAFT', 'STRIPE_TEST_CHECKOUT'):
        if exact_action == 'STRIPE_TEST_CHECKOUT' and 'no_connector_writes' in restrictions:
            return _held('task_restriction', RESTRICTIONS['no_connector_writes'])
        problem = launch_request_problem(str((job or {}).get('request') or ''))
        if problem or exact_target != 'default-site':
            return _held('task_constraint', problem or 'Wrong test launch site')
        return {'authorized': True, 'basis': 'routine_test_draft', 'detail': '', 'clarification_required': False}

    if exact_action == 'CONNECTION_READ':
        if 'no_connector_reads' in restrictions:
            return _held('task_restriction', RESTRICTIONS['no_connector_reads'])
        return {'authorized': True, 'basis': 'routine_business_operation', 'detail': '', 'clarification_required': False}

    if standing and "TELEGRAM" in exact_action and exact_target in set(configured["telegram"]):
        return {"authorized": True, "basis": "operator_telegram_allowlist", "detail": "",
                "clarification_required": False, "notification_kind": standing}
    if standing and "SEND" in exact_action and exact_target.casefold() in {
            value.strip().casefold() for value in configured["agentmail_internal"]}:
        return {"authorized": True, "basis": "internal_mail_allowlist", "detail": "",
                "clarification_required": False, "notification_kind": standing}
    if exact_action.startswith(BRAIN_ACTION_PREFIX):
        detail = "the Business Brain changes only through its validated writers; this job proposes, it does not write"
        if "no_brain_writes" in restrictions:
            detail += f" ({RESTRICTIONS['no_brain_writes']})"
        return _held("validated_brain_writer", detail)
    if routine_business_write(exact_action):
        forbidden = "no_connector_writes" if "no_connector_writes" in restrictions else (
            "no_calendar_writes" if "no_calendar_writes" in restrictions and exact_action.startswith("GOOGLECALENDAR_")
            else "")
        if not forbidden:
            return {"authorized": True, "basis": "routine_business_operation", "detail": "",
                    "clarification_required": False}
        return _held("task_restriction", RESTRICTIONS[forbidden], action=exact_action)
    release = _release_decision(root, exact_action, exact_target, release_command)
    if release:
        return release
    publication = _website_release_decision(exact_action, exact_target, website_release)
    if publication:
        return publication
    return _held("approval_required", EXPLICIT_COMMAND_DETAIL, action=exact_action)


def _load_bridge(root: Path):
    path = Path(root) / BRIDGE_PATH
    spec = importlib.util.spec_from_file_location("aos_telegram_bridge_send", path)
    if spec is None or spec.loader is None:
        raise SendError("telegram bridge send path unavailable")
    bridge = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(bridge)
    except RuntimeError as exc:
        if ".env file not found" not in str(exc):
            raise
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"^WORKSPACE = .*$", f"WORKSPACE = Path({str(root)!r})", source, count=1, flags=re.MULTILINE)
        exec(compile(source, str(path), "exec"), bridge.__dict__)
    return bridge


def reminder_authorized(root: Path, conversation_id: str, chat_id: str) -> bool:
    """A reminder stays in the operator's existing private Telegram conversation."""
    return bool(re.fullmatch(r"[1-9][0-9]{0,19}", chat_id)
                and conversation_id == f"telegram-{chat_id}"
                and action_authorization(root, None, action="TELEGRAM_SEND_MESSAGE", target=chat_id,
                                         notification_kind="operational_notification")["authorized"])


def operator_send(chat_id: str, text: str, *, kind: str = "queue_status", document_paths: list[str] | None = None,
                  root: Path | None = None) -> dict:
    """Send one system notification to an allowlisted operator chat through the existing bridge.

    Refused unless the policy authorizes this exact notification kind and destination.
    Raises SendError when the bridge does not confirm delivery.
    """
    root = Path(root) if root is not None else aos_root()
    decision = action_authorization(root, None, action="TELEGRAM_SEND_MESSAGE", target=str(chat_id),
                                    notification_kind=kind)
    if not decision["authorized"]:
        raise SendError(f"notification to {chat_id} is not authorized ({decision['basis']})")
    bridge = _load_bridge(root)
    failure: dict[str, str] = {}
    message_date = None
    bridge_api = bridge.api

    def tracking_api(method, data=None, timeout=60):
        nonlocal message_date
        try:
            response = bridge_api(method, data, timeout)
            if method == "sendMessage" and isinstance(response, dict) and response.get("ok") is True:
                value = (response.get("result") or {}).get("date")
                if type(value) is int and value > 0:
                    message_date = value
            return response
        except Exception as exc:
            failure["error"] = type(exc).__name__
            raise

    bridge.api = tracking_api
    delivery = bridge.send(str(chat_id), text, preserve_format=True, document_paths=document_paths or [])
    if failure:
        raise SendError(failure["error"])
    documents = delivery.get("documents") if isinstance(delivery, dict) else []
    if document_paths and (len(documents or []) != len(document_paths)
                           or not all(isinstance(row, dict) and row.get("sent") is True for row in documents)):
        raise SendError("telegram_document_delivery_failed")
    if not isinstance(delivery, dict):
        return {}
    if message_date is not None:
        delivery["message_date"] = message_date
    return delivery
