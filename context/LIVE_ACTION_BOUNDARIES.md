# Live action boundaries
> Revisit: when the dashboard backend, queue runner, connector adapters, notification wiring, or Business Brain writers change. · Last touched: 2026-11-13.

Operator map of rules reachable from the current TTROS runtime. The single execution permission authority is `rules/runtime_permission_policy.md`; this table describes where boundaries are enforced, it grants nothing. This excludes archive/history unless a live caller still reaches it.

## Live entrypoints confirmed

- `aos-backend.service` is active and runs `dashboard/backend/main.py` through Uvicorn from `/opt/ttros-core/dashboard`.
- `aos-runner.service` is enabled and runs the recurring `--watch` loop (5 s tick). Every tick it dispatches the next ready `async_dispatch` agent item to a detached `--execute-item` executor (at most 2 concurrently). Its `KillMode=process` drop-in keeps in-flight executors running when the runner restarts. `--dispatch-item ID` remains the one-shot path.
- Dashboard (`POST /api/queue/items`) and `tools/aos-queue.py create --status agent_todo` tag ready agent work `async_dispatch`; `auto_dispatch=false` / `--manual` opt out. `POST /api/queue/items/{id}/run` (Run/Retry) only requeues the item for the runner; it never executes in the backend.
- The executor calls `dashboard/backend/main.py::run_queue_item()` directly. One worker invocation per run; the result is verified against its claimed artifacts. No reviewer, retry or correction run. Claims are accepted only from `inbox`/`agent_todo`. The executor registers its exact process identity, and its workers carry `TTROS_QUEUE_CLAIM`. An abandoned claim (no heartbeat and no live process of that claim) is requeued once, then blocked, with a `-recovery-N` receipt. A closeout whose claim was recovered meanwhile changes nothing.
- Ask David (`POST /api/dashboard/ask-david`) runs a direct Codex turn (Claude Code when selected) under `rules/runtime_permission_policy.md` as a pollable job (`GET /api/dashboard/ask-david/jobs/{job_id}`). No Hermes route exists; `/api/wsl/hermes` (protected Telegram bridge) and `/api/wsl/codex` (`aos-codex` launcher) are compatibility entrypoints for the same Ask David call.
- `aos-bridge.service` is active for inbound Telegram. Its files/content were not inspected; live outbound callers use the existing bridge function only.

## Reachable rules

| Action | Current behavior | Exact file/function/config | What triggers the block | How Casey changes it |
|---|---|---|---|---|
| Normal queue-launched Codex local reads/edits/tests/install/dev server/browser/screenshot | **AUTO** | `tools/aos_codex_policy.py`: `SANDBOX_MODE`, `APPROVAL_POLICY`, `PERMISSION_HEADER`, `build_exec_command()` | No approval prompt. A task can still stop on its queue `stop_conditions`, a hard boundary below, or execution failure. | Change the queue item's scope/fields or the defaults in `dashboard/backend/main.py::QueueItemCreate`; do not change the Codex runtime policy merely to widen one task. |
| Queue `allowed_actions` / `stop_conditions` | **ALLOWLISTED** | `dashboard/backend/main.py::_queue_actual_run_prompt()`, `_queue_actual_department_run_prompt()`, `_queue_render_prompt()`; stored by `tools/aos-queue.py::create_item()` | These are instructions supplied to the worker, not a mechanical filesystem permission layer. A worker stopping on them can ultimately create `needs_input`/`blocked`. | Edit them when creating the task, or edit the specific live work item fields. Defaults are on `QueueItemCreate`. |
| Start a queue item | **AUTO** | `tools/aos-orchestration-runner.py::next_async_item()`, `dispatch_via_executor()`; `dashboard/backend/main.py::run_queue_item()` | Refused if dependencies lack `done` receipts, the item is already actively claimed/working, no runner capacity exists, or the workbench cannot start. | Satisfy dependencies/release stale claims; capacity and lease/timeouts are `AOS_MAX_CONCURRENT_EXECUTORS`, `AOS_AGENT_LEASE_SECONDS`, and runner timeout env vars. Lane/model routing is `queue/model_routes.json`. |
| Further model invocation in the same named scope | **BLOCKED/PROTECTED** | `tools/step6_cost_control.py::preflight()`; called before every Codex session and every queue-worker Claude Code session | The 500,000 canonical-token scope fuse is reached, or usage accounting is missing/corrupt and fails closed. | Safest: use the scoped `/api/cost-control/fuse-override`, `/fuse override`, or scoped reset with a visible reason. The global limit is `FUSE_LIMIT` in `tools/step6_cost_control.py`. |
| Generic Composio read action | **AUTO** | `tools/ttros_connections.py` (Connections Hub `tool` operation, `perform_operator`) | Gmail is additionally limited by `connectors/gmail_draft_policy.py`; invalid/unregistered actions fail. | Change the connector registry/policy or Gmail policy. |
| Generic Composio external mutation from CLI | **AUTO for routine Drive/Docs/Sheets/Calendar writes; EXACT AUTHORITY otherwise** | `tools/aos_authority.py::action_authorization()` via the Connections Hub (`tools/ttros_connections.py`) | Operator/internal delivery is automatic when the exact recipient is in `queue/notifications.json`. A third-party mutation requires an exact Casey-authored action, target, and command on the queue item. Complete agreed Calendar context is automatic. Agent-initiated third-party actions and ambiguous Calendar details stop. Publish/upload/send keep their existing review and CASL checks; secret-shaped payloads always fail. | Supply the exact action/target command once, complete the Calendar agreement fields, or change the internal recipient allowlist. Do not add a second approval after an exact Casey command. |
| Dashboard generic Composio action `/api/connectors/composio/action` | **VERIFIED OPERATOR + READ-ONLY** | `dashboard/backend/main.py::composio_action()` → `remote_pc.require_operator()` → `classify_composio_action()` → `_run_composio_adapter()` | The HTTP route needs the Remote PC operator identity (Cloudflare Access JWT or Tailscale Serve identity in `TTROS_REMOTE_PC_IDENTITIES`, plus the `X-TTROS-Remote-PC` header); denials are logged to `queue/receipts/remote_pc/denied.jsonl`. It runs a slug only if it names an explicit read verb and no mutation verb; mutations and unclassifiable slugs are refused before the adapter, and the adapter still gets no `--confirmed`. | Change `_COMPOSIO_READ_VERBS` / `_COMPOSIO_MUTATION_VERBS` in `main.py`. Mutations stay on their governed routes; do not open them here. |
| LinkedIn read-only MCP `/api/brain/linkedin/` (ChatGPT) | **ENABLED 2026-11-06 / ALLOWLISTED** | `tools/linkedin_profile_mcp.py`; mounted by `main.py` because `TTROS_LINKEDIN_MCP_HTTP=1` in `~/.config/systemd/user/aos-backend.service.d/linkedin-mcp.conf`; edge-protected by the brain.core-site.example Access application (tunnel forwards only `^/api/brain`) | One parameterless tool `linkedin_get_my_profile` runs only `LINKEDIN_GET_MY_INFO` with `{}` through the shared adapter. Requires a Brain Access JWT (`shared_brain_http.verify_access_jwt`) whose identity is in `TTROS_LINKEDIN_MCP_IDENTITIES` (live: `owner.personal@example.invalid,account-owner@example.invalid`, the same pair as Remote PC; code default `owner.personal@example.invalid`). ChatGPT authenticates through Access as `account-owner@example.invalid`; without it discovery fails with 403 before `initialize`. Secret-shaped keys are redacted; output is size-bounded. Separate from the six-operation Shared Brain server. | Remove `linkedin-mcp.conf` (or set the flag to 0), `daemon-reload`, restart `aos-backend` to disable. No slug or argument input exists to widen. |
| Gmail draft creation through the dedicated adapter | **ALLOWLISTED** | `connectors/gmail_draft_policy.py`; `connectors/gmail_draft_adapter.py::GmailDraftAdapter.create_draft()` | Only `GMAIL_CREATE_EMAIL_DRAFT` is allowed; malformed inputs, duplicate/indeterminate recovery state, provider failures, or prospect validation/CASL conditions stop it. Send/reply/forward/schedule/update/delete/label actions remain forbidden on generic Gmail routing; internal email uses AgentMail. | Change the exact authority contract in `gmail_draft_policy.py` and validation in `gmail_draft_adapter.py`. Draft authority does not grant send authority. |
| Third-party send stub in dashboard | **EXPLICIT APPROVAL** | `dashboard/backend/main.py::_write_external_dry_run_receipt()` | Typed confirmation must equal `SEND <recipient>`. Even when accepted it is dry-run only and transmits nothing. | This path cannot be made live through config; it requires a code/wiring decision. |
| Casey/internal email or AgentMail delivery | **AUTO for declared notification kinds** | Policy clause "Automatic internal notifications"; `tools/aos_orchestration.py::action_authorization()` (`STANDING_NOTIFICATION_KINDS`, `TTROS_NOTIFICATION_KIND`); `dashboard/backend/main.py::_agentmail_digest_attempt()`; `queue/notifications.json`; morning brief and ChatGPT wake via `perform_operator("AGENT_MAIL_SEND_EMAIL")` on the AgentMail Platform connection | Recipient not in `agentmail_internal`, invalid provider contract, duplicate idempotency key, provider failure, or secret-shaped content blocks delivery. An allowlisted destination without a declared kind (`operational_notification`, `queue_status`, `chatgpt_wake`) is not authorized. | Edit `queue/notifications.json::allowlist.agentmail_internal`. |
| Operator Telegram message/file delivery | **AUTO for system notices** | Policy clause "Automatic internal notifications"; `tools/aos_orchestration.py::action_authorization()` (declared kind required), `prepare_telegram_send()`; `queue/notifications.json::allowlist.telegram` | Recipient is not allowlisted; a prior send exists; or a durable intent exists without a result, which refuses automatic retry pending reconciliation. Message and file delivery use the existing bridge path and do not enter `human_review`. | Edit `queue/notifications.json`. Immediate Telegram-origin run/completion replies remain active. The recurring 10-minute escalation is currently disabled by `--skip-telegram-escalation` in `aos-runner.service`; change that unit flag and restart the service to enable it. |
| Calendar booking/change | **AUTO (routine write)** | `tools/aos_orchestration.py::action_authorization()` (`routine_business_write()`); governed Composio adapter | Automatic unless the task's own `stop_conditions` (`calendar_action`, `connector_action`) or external-effect tags restrict it. Event delete/remove actions need Casey's exact action+target command. Materially ambiguous date, time, participants or timezone gets one clarification. | Issue an exact action+target command for a delete; otherwise none needed. |
| David decides conversation vs execution | **AUTO** | `rules/david_execution_handoff.md`; `dashboard/backend/main.py::dashboard_ask_david()` | Material ambiguity makes David ask one question. External/protected intent is not bypassed by direct execution. | Change `rules/david_execution_handoff.md`. |
| Ordinary durable Business Brain learning through MCP | **AUTO** | `tools/brain_memory_mcp.py::remember_brain_knowledge()` | Refuses unsafe pointer/section/body, missing source/session provenance, and commitment/consequential language matched by `COMMITMENT_RE`. | Change ordinary placement in `tools/brain_memory.py` and the commitment regex/validation in `brain_memory_mcp.py`. Keep commitments on an explicit Casey-confirmation path. |
| Dashboard quick capture to Brain inbox | **AUTO** | `dashboard/backend/main.py::dashboard_capture()` → `tools/business_brain_inbox.py::capture_text()` | Unsafe/unavailable canonical inbox, invalid text/capture identity, or boundary failure. It writes inbox only and does not promote. | Change the dashboard endpoint or inbox validation. |
| Memory Intake / explicit David attachment ingest | **AUTO** | `dashboard/backend/main.py::memory_intake_ingest()`, `_try_ask_david_explicit_ingest()`, `_run_memory_intake_capture()` | Requires an explicit Ingest action or an explicit ingest phrase plus attachment. Scope/path, byte-preservation, semantic-card, index-integrity, or downstream validation failures return failed/needs-attention (not queue `needs_input`). | Change the explicit-phrase matcher or intake validation in `dashboard/backend/main.py` / `tools/source_intake.py`. |
| Direct dashboard edit of an existing permitted Brain note | **AUTO** | `dashboard/backend/main.py::dashboard_save_memory()` | Refuses denied/out-of-scope pointers, stale `expected_revision`, invalid content, or failed persistence verification. No second approval gate exists after Casey clicks Save. | Change permitted pointers in `context/client_scope_registry.json`; concurrency/content checks are in `_dashboard_memory_note_path()`, `_validate_dashboard_memory_content()`, and `dashboard_save_memory()`. |
| Business Brain machine promotion | **ALLOWLISTED** | `tools/business_brain_promotion.py::evaluate_promotion()` / `PromotionWriter.apply()` | Automatic write is allowed only for `generated_marker_section` at `business_brain:index/MEMORY_INDEX.md` with marker `block-2-outcome-index`. Target/marker mismatch falls to review. Never classes and scope violations refuse outright. | Safest widening point: add an exact target+marker under `ENABLED_AUTOMATIC_RULES`; never/review class sets are beside it. |
| Business Brain review-tier promotion | **EXPLICIT APPROVAL** | `tools/promotion_review_queue.py::PromotionReviewQueue.create_or_get()`; `tools/business_brain_promotion.py::PromotionWriter.apply()` | Proposal sits in queue `human_review`; writer refuses without an accepted `human_review` approval reference. | Approve the exact proposal, then call the writer with that reference. Change `REVIEW_CHANGE_CLASSES` only if Casey deliberately reclassifies a whole consequence class. |
| Business Brain never-tier promotion | **BLOCKED/PROTECTED** | `tools/business_brain_promotion.py::NEVER_CHANGE_CLASSES`, `evaluate_promotion()` | Secrets/credentials/auth, raw queue/runtime/log trees, speculation-as-fact, source/cloned/raw communications, or protected/out-of-scope material. | Change `NEVER_CHANGE_CLASSES`. These are unsafe candidates for blocker removal. |

## Queue status creation

### What creates `blocked`

- `run_queue_item()` sets it on worker timeout, non-zero worker exit, reported worker failure, a claimed artifact that is not on disk, or a pre-completion exception; stale/dead `agent_working` recovery also sets it.
- Casey can set it through dashboard review **Block/Reject** (note required), Telegram rejection of one bound pending item, the generic status endpoint, or receipt attachment with status `blocked`.
- A notification result named `blocked` (for example, recipient not allowlisted) does **not** change the queue item to `blocked`; it records a blocked notification while preserving the item's status.

### What creates `needs_input`

- A successful run whose item explicitly has `on_complete: needs_input`.
- A non-routine (for example delete) Calendar action without Casey's exact command asks one
  clarification.
- Casey selecting **Needs changes** on a `human_review` item, or the generic status/receipt endpoints setting it directly.

### What creates `human_review`

- A passing run with `on_complete: human_review`, or an agent-initiated/unapproved
  third-party effect tagged `external`, `outbound`, `send`, `client_facing`, or
  `payment`. A `calendar` tag alone no longer gates (Calendar writes are routine).
- Generic effect tags do not override an exact Casey-authorized action. A queue item's recorded action to an allowlisted
  destination is not authorization (only declared system notification kinds are).
- Filing a Business Brain review-tier promotion proposal.
- The generic status/receipt endpoints can set it directly.

`human_review` closes only through explicit **Approve**, **Needs changes**, **Block**, or **Reject** actions. Generic Telegram approval refuses external/destructive items and requires an exact action+target route instead.

## Old rules still reachable

No old/obsolete permission code can block **normal** local work.

- `TTROS_BRAIN_AUTOPUSH` (installed backend/runner/importer drop-ins still set `=1`) is retired and inert:
  `tools/brain_git_closure.py::enabled()` always returns False, so no service pushes the Brain.
  Brain commits stay local for Casey's normal push.

## Safest ways to reduce blockers

1. Change per-item `allowed_actions`, `stop_conditions`, `on_complete`, `review`, tags, and dependencies instead of weakening shared enforcement.
2. Use a named-scope token-fuse override/reset rather than raising/removing the global fuse.
3. Adjust notification recipients in `queue/notifications.json`; this affects delivery, not task execution.
4. Do not relax secret, client-isolation, external-send, or never-tier Brain rules globally.
5. Add only exact target+marker pairs to `ENABLED_AUTOMATIC_RULES` for proven deterministic Brain writes; leave consequential classes review-tier.

## Primary edit points

- Queue scope/gates/status behavior: `dashboard/backend/main.py`, `tools/aos-orchestration-runner.py`, `tools/aos_orchestration.py`, `tools/aos-queue.py`
- Queue lane/model routing: `queue/model_routes.json`
- Codex local permission posture: `tools/aos_codex_policy.py`
- Execution permission authority: `rules/runtime_permission_policy.md` (the only policy; `tools/aos_codex_policy.py` injects it into every workbench prompt)
- Connector approval: `tools/ttros_connections.py`, `connectors/gmail_draft_policy.py`, `connectors/gmail_draft_adapter.py`
- Recipients/escalation window: `queue/notifications.json`; recurring escalation enablement: `/home/operator/.config/systemd/user/aos-runner.service`
- Business Brain write classes: `tools/brain_memory_mcp.py`, `tools/business_brain_promotion.py`, `tools/promotion_review_queue.py`, and `context/client_scope_registry.json`
- Token fuse: `tools/step6_cost_control.py` or the scoped dashboard/Telegram override interfaces

Token usage: unavailable from current CLI output.
