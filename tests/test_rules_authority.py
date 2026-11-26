"""One effective authorization authority: rules/runtime_permission_policy.md.

Regression proof that historical grants (allowlists alone, queue tags, service push flags, retired
instruction files) cannot authorize an action, that ordinary authorized work still proceeds, that
scoped technical gates apply only to their own scope, and that unchanged renders never rewrite a
Brain file. Revisit: when the runtime permission policy or a governed send/push path changes.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tools import aos_orchestration as orchestration
from tools import brain_git_closure, brain_memory
from tools import validate_business_brain as validator

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rules/runtime_permission_policy.md"
OPERATOR_CHAT = "100200300"
INTERNAL_INBOX = "wake@internal.example"


@pytest.fixture
def notify_root(tmp_path, monkeypatch):
    monkeypatch.delenv(orchestration.NOTIFICATION_KIND_ENV, raising=False)
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue/notifications.json").write_text(json.dumps({
        "allowlist": {"telegram": [OPERATOR_CHAT], "agentmail_internal": [INTERNAL_INBOX]}}))
    return tmp_path


# ------------------------------------------------------------------ one authority, no competing text

def test_policy_is_the_only_authority_loaded_by_the_launchers():
    from tools import aos_codex_policy
    policy = POLICY.read_text(encoding="utf-8").strip()
    assert aos_codex_policy.PERMISSION_HEADER.endswith(policy)
    assert "Automatic internal notifications" in policy and "On conflict, this file wins." in policy


ACTIVE_SURFACES = [
    "AGENTS.md", "CODEX.md", "CLAUDE.md", "queue/README.md", "agents/README.md",
    *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "rules").glob("*.md"))],
    *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "hooks").glob("*.md"))],
    *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "queue/templates").glob("*.md"))],
    "context/ACCESS_MODEL.md", "context/LOOP_POLICY.md", "context/OPERATOR_CONTRACT.md",
    "context/PROTECTED_PATHS.md", "context/EXTERNAL_ACTIONS.md", "context/MEMORY_PROMOTION_POLICY.md",
]
# Statements that once granted, denied or gated actions independently of the policy.
RETIRED_AUTHORITY = [
    r"Hermes is the operator", r"sole implementation\s+workbench", r"Frozen this phase",
    r"Not Allowed in v0\.1", r"Stop for human review or explicit instruction", r"## Explicit-Stop Actions",
    r"## Default Allowed Access", r"Internal sends unrestricted", r"typed confirmation",
    r"Escalation on exactly two triggers", r"Until Block 2 promotion authority is accepted",
    r"internal recipient allowlist constrains the destination; it does not\s+authorize",
]


@pytest.mark.parametrize("relative", ACTIVE_SURFACES)
def test_active_instruction_surfaces_carry_no_retired_authority(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    for pattern in RETIRED_AUTHORITY:
        assert not re.search(pattern, text), f"{relative} still states retired authority: {pattern}"


def test_only_the_policy_defines_the_standing_notification_authorization():
    holders = [p for p in ACTIVE_SURFACES if "predefined operational" in (ROOT / p).read_text(encoding="utf-8")]
    assert holders == ["rules/runtime_permission_policy.md"]


def test_retired_rule_files_are_archived_not_active():
    for name in ("OPERATOR_RULES_MINIMAL.md", "ROUTING_RULES_MINIMAL.md"):
        assert not (ROOT / "context" / name).exists()
        assert (ROOT / "archive/rules-consolidation-2026-11-13/context" / name).is_file()


def test_notification_config_grants_nothing():
    config = json.loads((ROOT / "queue/notifications.json").read_text(encoding="utf-8"))
    assert "third_party" not in config
    assert "grants nothing" in config["$comment"] and "runtime_permission_policy.md" in config["$comment"]


# ------------------------------------------------------------------ automatic internal notifications

@pytest.mark.parametrize("kind", sorted(orchestration.STANDING_NOTIFICATION_KINDS))
def test_declared_notification_kind_to_allowlisted_inbox_is_authorized(notify_root, kind):
    decision = orchestration.action_authorization(notify_root, None, action="AGENT_MAIL_SEND_EMAIL",
                                                  target=INTERNAL_INBOX, notification_kind=kind)
    assert decision["authorized"] and decision["basis"] == "internal_mail_allowlist"
    assert decision["notification_kind"] == kind


def test_declared_operational_telegram_notice_is_authorized(notify_root):
    decision = orchestration.action_authorization(notify_root, None, action="TELEGRAM_SEND_MESSAGE",
                                                  target=OPERATOR_CHAT, notification_kind="queue_status")
    assert decision["authorized"] and decision["basis"] == "operator_telegram_allowlist"


@pytest.mark.parametrize("kind", [None, "", "outreach", "customer_reply", "file_transfer", "arbitrary"])
def test_allowlisted_destination_alone_never_authorizes(notify_root, kind):
    for action, target in (("AGENT_MAIL_SEND_EMAIL", INTERNAL_INBOX), ("TELEGRAM_SEND_MESSAGE", OPERATOR_CHAT)):
        decision = orchestration.action_authorization(notify_root, None, action=action, target=target,
                                                      notification_kind=kind)
        assert decision["authorized"] is False and decision["basis"] == "approval_required"


def test_declared_kind_never_reaches_an_unlisted_destination(notify_root):
    decision = orchestration.action_authorization(notify_root, None, action="AGENT_MAIL_SEND_EMAIL",
                                                  target="client@example.com", notification_kind="chatgpt_wake")
    assert decision["authorized"] is False


def test_subprocess_declaration_uses_the_environment(notify_root, monkeypatch):
    monkeypatch.setenv(orchestration.NOTIFICATION_KIND_ENV, "operational_notification")
    assert orchestration.action_authorization(notify_root, None, action="AGENT_MAIL_SEND_EMAIL",
                                              target=INTERNAL_INBOX)["authorized"]
    monkeypatch.setenv(orchestration.NOTIFICATION_KIND_ENV, "outreach")
    assert not orchestration.action_authorization(notify_root, None, action="AGENT_MAIL_SEND_EMAIL",
                                                  target=INTERNAL_INBOX)["authorized"]


def test_queue_tags_and_recorded_actions_do_not_authorize_an_allowlisted_send(notify_root):
    # Old shape: an item whose recorded action is Telegram to the allowlisted chat, with no exact command.
    item = {"tags": ["send"], "approved_external_action": ["TELEGRAM_SEND_MESSAGE"],
            "approved_external_target": OPERATOR_CHAT, "requested_by": "Casey"}
    assert orchestration.action_authorization(notify_root, item)["authorized"] is False


def test_retired_queue_approval_fields_no_longer_authorize(notify_root):
    # The old Work Queue's approved_external_* fields have no writer since the clean break; a record that
    # still carries them is held like any other job, with the reason Casey is shown.
    item = {"tags": ["send"], "requested_by": "Casey", "approved_external_action": ["GMAIL_SEND_EMAIL"],
            "approved_external_target": "client@example.com",
            "approved_external_command": "Send the proposal to client@example.com"}
    decision = orchestration.action_authorization(notify_root, item, action="GMAIL_SEND_EMAIL", target="client@example.com")
    assert decision["authorized"] is False and decision["basis"] == "approval_required"
    assert "explicit command" in decision["detail"]
    assert orchestration.action_authorization(notify_root, item)["authorized"] is False
    source = (ROOT / "tools/aos_authority.py").read_text(encoding="utf-8")
    assert "approved_external_command" not in source.split('"""', 2)[2]   # documented, never read
    for name in ("stop_conditions", "EXTERNAL_EFFECT_TAGS", '.get("tags")'):
        assert name not in source.split('"""', 2)[2], name


# ------------------------------------------------------------------ routine business operations

ROUTINE_WRITES = ["GOOGLEDRIVE_UPLOAD_FILE", "GOOGLEDOCS_CREATE_DOCUMENT", "GOOGLESHEETS_BATCH_UPDATE",
                  "GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_PATCH_EVENT"]
PROTECTED_ACTIONS = ["GMAIL_SEND_EMAIL", "GMAIL_REPLY_TO_THREAD", "AGENT_MAIL_SEND_EMAIL",
                     "GOOGLEDRIVE_DELETE_FILE", "GOOGLEDRIVE_ADD_FILE_SHARING_PREFERENCE",
                     "GOOGLEDRIVE_CREATE_PERMISSION", "GOOGLECALENDAR_DELETE_EVENT",
                     "LINKEDIN_CREATE_LINKED_IN_POST", "GITHUB_CREATE_AN_ISSUE"]


@pytest.mark.parametrize("action", ROUTINE_WRITES)
def test_routine_business_write_is_automatic_for_any_worker(notify_root, action):
    for item in (None, {"requested_by": "david"}, {"requested_by": "codex", "tags": ["docs"]}):
        decision = orchestration.action_authorization(notify_root, item, action=action, target="any")
        assert decision["authorized"] and decision["basis"] == "routine_business_operation"


@pytest.mark.parametrize("action", PROTECTED_ACTIONS)
def test_sends_sharing_destruction_and_publishing_stay_explicit(notify_root, action):
    for kind in (None, "operational_notification"):
        decision = orchestration.action_authorization(notify_root, None, action=action,
                                                      target="person@example.com", notification_kind=kind)
        assert decision["authorized"] is False


def test_task_restrictions_stay_binding_over_routine_default(notify_root):
    # A job's own restrictions (aos_jobs restrictions, set from Casey's request) narrow the routine default.
    for job in ({"restrictions": ["no_connector_writes"]}, {"restrictions": ["no_calendar_writes"]},
                {"restrictions": ["no_calendar_writes", "no_connector_writes", "unknown_value"]}):
        decision = orchestration.action_authorization(notify_root, job, action="GOOGLECALENDAR_CREATE_EVENT")
        assert decision["authorized"] is False and decision["basis"] == "task_restriction", job
        assert decision["clarification_required"] is True   # the adapter's calendar message still applies
    assert not orchestration.action_authorization(notify_root, {"restrictions": ["no_connector_writes"]},
                                                  action="GMAIL_CREATE_EMAIL_DRAFT")["authorized"]
    calendar_only = {"restrictions": ["no_calendar_writes"]}
    assert orchestration.action_authorization(notify_root, calendar_only, action="GOOGLEDOCS_CREATE_DOCUMENT")["authorized"]
    assert orchestration.action_authorization(notify_root, {"restrictions": ["unknown_value"]},
                                              action="GOOGLEDOCS_CREATE_DOCUMENT")["authorized"]


@pytest.mark.parametrize("job", [None, {"requested_by": "casey"}, {"requested_by": "casey", "restrictions": ["no_brain_writes"]}])
def test_a_job_never_writes_the_brain_itself(notify_root, job):
    # Brain changes go only through the validated writers; a job's BRAIN_WRITE_PROPOSAL is always held.
    for kind in (None, "operational_notification"):
        decision = orchestration.action_authorization(notify_root, job, action="BRAIN_WRITE_PROPOSAL",
                                                      target="business_brain:sources/x.md", notification_kind=kind)
        assert decision["authorized"] is False and decision["basis"] == "validated_brain_writer"
        assert ("Casey asked for nothing to be written" in decision["detail"]) == bool(job and job.get("restrictions"))


def test_a_job_send_is_held_even_when_casey_requested_the_job(notify_root):
    # Delegated routine work carries Casey's authorization; a send still needs his explicit command naming
    # the action and target, which no job record carries. It is held, never performed.
    job = {"id": "AOS-2026-10001", "requested_by": "casey", "restrictions": []}
    decision = orchestration.action_authorization(notify_root, job, action="GMAIL_SEND_EMAIL", target="client@example.com")
    assert decision == {"authorized": False, "basis": "approval_required", "clarification_required": False,
                        "detail": decision["detail"]}
    assert "explicit command naming this action and target" in decision["detail"]


def test_policy_states_the_confirmed_defaults():
    text = " ".join(POLICY.read_text(encoding="utf-8").split())
    for clause in ("Explicit task-specific restrictions stay binding", "external email is draft-first",
                   "carry his authorization", "Selective automatic email replies stay disabled",
                   "Composio reads and ordinary writes"):
        assert clause in text


def test_six_internal_agentmail_destinations_are_preserved():
    config = json.loads((ROOT / "queue/notifications.json").read_text(encoding="utf-8"))
    assert len(config["allowlist"]["agentmail_internal"]) == 6
    assert config["allowlist"]["telegram"] == ["1320777128"]


def test_chatgpt_wake_declares_its_kind():
    from tools import shared_brain_chatgpt_wake as wake
    assert wake.NOTIFICATION_KIND in orchestration.STANDING_NOTIFICATION_KINDS


# ------------------------------------------------------------------ Brain sweep push grant

def test_autopush_flag_does_not_override_workbench_context(monkeypatch, tmp_path):
    monkeypatch.setenv("TTROS_BRAIN_AUTOPUSH", "1")
    monkeypatch.setenv("TTROS_CODEX_NO_PUSH", "1")
    assert brain_git_closure.enabled() is False
    calls = []
    monkeypatch.setattr(brain_git_closure, "git", lambda *args: calls.append(args))
    assert brain_git_closure.push_commit(tmp_path, "sha") == "pending"
    assert calls == []


# ------------------------------------------------------------------ scoped navigation gate

def test_navigation_scope_is_defined_once():
    assert not validator.navigation_required("sources/intake/drive/1AbC.md")
    assert not validator.navigation_required("sessions/2026-11-01_david_abc.md")
    assert validator.navigation_required("prospects/2026-07-flox.md")
    assert validator.navigation_required("people/someone.md")


def _vault(tmp_path: Path, extra: dict[str, str]) -> Path:
    vault = tmp_path / "vault"
    for name in validator.OBSIDIAN_JSON:
        path = vault / ".obsidian" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"search": "-path:_backups"} if name == "graph.json" else {}))
    notes = {
        "HOME.md": "[[index/KNOWLEDGE|Knowledge]]",
        "README.md": "[[index/MEMORY_INDEX|Index]]",
        "index/MEMORY_INDEX.md": "[[README|Root]]",
        "index/KNOWLEDGE.md": "[[prospects/index|Prospects]]",
        "prospects/index.md": "[[prospects/flox|Flox]]",
        "prospects/flox.md": "# Flox",
        **extra,
    }
    for relative, body in notes.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nid: {relative.replace('/', '-')}\ntype: note\n---\n{body}\n")
    return vault


def test_home_folder_index_navigation_passes_and_evidence_needs_no_links(tmp_path):
    vault = _vault(tmp_path, {"sources/intake/drive/1AbC.md": "# Drive provenance"})
    result = validator.analyze_vault(vault)
    assert result["status"] == "PASS", result["unreachable"]
    assert result["root_distances"]["prospects/flox.md"] == 3
    assert result["evidence_unlinked_count"] == 1


def test_unrelated_orphan_is_reported_but_never_blocks_a_valid_write(tmp_path):
    vault = _vault(tmp_path, {"people/repair-fixture.md": "# Synthetic fixture"})
    before = validator.analyze_vault(vault)
    assert before["unreachable"] == ["people/repair-fixture.md"]      # visible, not hidden
    (vault / "prospects/index.md").write_text("---\nid: p\ntype: note\n---\n[[prospects/flox|Flox]] [[prospects/new|New]]\n")
    (vault / "prospects/new.md").write_text("---\nid: new\ntype: note\n---\n# New\n")
    gate = validator.introduced_failures(before, validator.analyze_vault(vault))
    assert gate["passed"] and gate["preexisting"] == 1 and gate["introduced"] == []


def test_a_write_that_introduces_a_failure_is_rejected(tmp_path):
    vault = _vault(tmp_path, {})
    before = validator.analyze_vault(vault)
    (vault / "prospects/orphan.md").write_text("---\nid: orphan\ntype: note\n---\n[[missing/target]]\n")
    gate = validator.introduced_failures(before, validator.analyze_vault(vault))
    assert not gate["passed"]
    assert any(key.startswith("unreachable:") for key in gate["introduced"])
    assert any(key.startswith("broken_links:") for key in gate["introduced"])


# ------------------------------------------------------------------ no provenance-only rewrites

def test_provenance_neutral_ignores_only_write_provenance():
    body = "---\nid: x\n---\n# X\n"
    a = brain_memory.managed_section(body, section_id="generated-navigation", title="Linked", content="- a",
                                     source="memory-filing", session_id="s1")
    b = brain_memory.managed_section(a.replace("author: ttros", "author: hermes"), section_id="generated-navigation",
                                     title="Linked", content="- a", source="other", session_id="s2")
    assert a != b and brain_memory.provenance_neutral(a) == brain_memory.provenance_neutral(b)
    c = brain_memory.managed_section(body, section_id="generated-navigation", title="Linked", content="- b",
                                     source="memory-filing", session_id="s1")
    assert brain_memory.provenance_neutral(a) != brain_memory.provenance_neutral(c)


def test_unchanged_render_makes_no_commit(tmp_path, monkeypatch):
    from tools import aos_embeddings, aos_entity_index, aos_indexer
    vault = tmp_path / "brain"
    vault.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-b", "main")
    (vault / "README.md").write_text("---\nid: root\ntype: index\n---\n# Brain\n")
    git("add", "README.md")
    git("-c", "user.name=Test", "-c", "user.email=test@local", "commit", "-m", "baseline")
    monkeypatch.setattr(brain_memory, "VAULT_ROOT", vault)
    monkeypatch.setattr(aos_indexer, "index_one", lambda _path: {"status": "success"})
    monkeypatch.setattr(aos_entity_index, "ensure_current", lambda: None)
    monkeypatch.setattr(aos_embeddings, "safe_refresh", lambda *a, **k: None)
    monkeypatch.setenv("TTROS_BRAIN_CLOSURE_STATE", str(tmp_path / "closure.json"))
    note = brain_memory.managed_section("---\nid: n\ntype: note\n---\n# N\n", section_id="generated-navigation",
                                        title="Linked", content="- a", source="memory-filing", session_id="s1")
    first = brain_memory.write_transaction({"people/n.md": note}, source="memory-filing", session_id="s1")
    assert first.commit and first.sync_status != "synced"   # committed locally; nothing pushes
    head = git("rev-parse", "HEAD")
    current = (vault / "people/n.md").read_text()
    # A re-render that differs only in provenance (new session, old author spelling) is not a change.
    again = brain_memory.write_transaction({"people/n.md": current.replace("author: ttros", "author: hermes")},
                                           source="memory-filing", session_id="s2")
    assert again.commit is None and again.changed_paths == ()
    assert git("rev-parse", "HEAD") == head and (vault / "people/n.md").read_text() == current
