# Clean-break business foundations and bounded UX/memory review
> Revisit: when the capture/brief adapter, workbench, or continuity contracts change. · Last touched: 2026-11-15.

This describes the offline candidate. It grants no execution permission; the sole
global authority remains `rules/runtime_permission_policy.md`. No service,
specialist package or production release was activated in this integration.

## Gmail and morning brief

`tools/aos_capture_live.py poll --scheduled` retains the scheduled command,
four read-only Composio actions, INBOX metadata normalization, bounded history,
`gmail-live` cursor, provider/message-derived `cap-…` IDs, private capture
storage and `capture:<scope>:<record>` evidence identities. It reuses the
existing activation record and control/kill switch. It does not activate them.
Truncated bootstrap/page overflow, bad cursor, provider error or incomplete
publication does not intentionally advance the cursor. Restart replay repairs
raw-only writes without creating duplicate records or proposals. The existing
provider processing index prevents silent reattribution when scope mappings change.

This global foundation captures matched global metadata and quarantines
unresolved identities in the existing `_unresolved` capture namespace. Other
client matches are excluded before their scope storage is opened. Their
recoverable provider/message/thread/history provenance is retained in the
existing private provider audit journal, without subject/body and without
creating runnable work. Advancing the mailbox cursor does not erase those
source identities. A future authorized client-scoped consumer must resolve
its scope before retrieving content; this global foundation does not do so. It never
opens message bodies/attachments, sends, labels, drafts, creates jobs, or
promotes Brain knowledge. Claimed approval in a subject is only a classification.
Existing originals, scoped raw data, metadata and historical evidence stay in
place. No client capture data is migrated or relabelled.

Content-free dated evidence remains under `capture/gmail/<day>/<scope>/`;
metadata uses the existing `CaptureMetadataProjection`. Search/Graphify can
consume it through their retained ingestion/projection interfaces. A poll
explicitly reports `index_status=awaiting_existing_ingestion`; it does not
rebuild the whole Brain/search index or overwrite an existing graph to publish
only a partial global subset. Provider acceptance and end-to-end metadata
publication must be proved at controlled activation, not inferred here.

`tools/aos_morning_brief.py` retains `--deliver --format quiet`, the Example City
service date, `context/MORNING_BRIEF_FINDINGS.json`,
`context/DAVID_MORNING_BRIEF.md`, the existing delivery log and receipt naming,
the **email**/Telegram dedupe channel names, and the governed AgentMail/operator
notification send functions. A deterministic render selects at most three
findings and includes their source references, observation time and digest.
`--dry-run` reads fixtures/evidence only: no model, job, delivery or artifact write.

The detector augments historical evidence with current `aos_jobs` records,
including full permanent IDs and current states; it does not migrate history.
Scoped Brain paths are resolved before reading prospect/project/open-loop
content or hashing it. Completed jobs clear their active finding. The detector
does not treat a generated brief as a later source of truth.

The brief's exact per-day body is retained in the existing artifact hierarchy at
`workflows/queue_artifacts/david_morning_brief/<day>.md`. A receipt remains
delivery metadata; it is never sent as if it were the brief. Legacy same-day
delivery rows require the retained original artifact to match their hash.
Conflicting/missing evidence refuses retry. Intent is durable before transport;
timeout, uncertain, failed or interrupted attempts are not automatically resent.
A single process/file lock serializes this scheduled one-shot operation. It is
not a job scheduler or a second queue. Existing receipts are not overwritten.

Future Gmail and expandable brief specialists should be normal M1/M5 packages,
submitted through `aos_jobs.create` with its existing executor/release gates.
They should consume these bounded scoped evidence functions, not own another
poller, cursor, scheduler or memory store. Capture references are not fabricated
Brain references: source content must pass the existing scoped evidence/ICM
path before a package requiring Brain `records` consumes it. Morning findings
are data for interpretation, never permission to execute their suggested next
actions. M4 Factory improvement follows measured cases and a separate release
decision. No new specialist, package schema, M5 route or release-state bypass
is introduced here.

## Native workbenches and ingress

Current Dashboard copy prompts already use native Codex/Claude. Candidate code
has no caller requiring the removed `/api/wsl/codex` or `/api/wsl/claude` APIs.
`scripts/workbench/aos-codex` and `aos-claude` are reviewable direct native
entrypoints with the existing no-push environment. They preserve native CLI
arguments and sessions; they do not emulate the retired HTTP JSON response or
stdin-task wrapper. No installed wrapper was edited. Installation/retirement
and any caller migration belong to cutover. David does not recursively call
itself to launch a coding workbench; a coding-agent integration is not added.

Main WIP's necessary ingress behavior is adapted in `tools/david_ingress.py`:
reject historical canary markers/tombstones ahead of all handlers, and match
Telegram requests exactly against the existing bridge's authenticated delivery
receipt. The new jobs already persist their conversation/chat; the old queue
binding journal and child environment propagation are unnecessary. Completion
acknowledgements describe actual persisted job state and configured notification
availability. `aos_authority` still owns sends. This adds no credential or global
policy system and makes no change to the Telegram bridge.

## Existing Dashboard inventory and minimal recommendation

No UI files are changed in this pass. Source inventory:

| Surface | Current implementation | Minimal follow-up |
| --- | --- | --- |
| Conversational David | `App.jsx` cockpit/message-board -> `AskDavid.jsx`; local thread/draft state, uploads and Ask David polling | Keep one primary conversation; show topic continuity and link canonical job IDs to Jobs. Remove remaining Hermes/work-queue copy from visible labels when touched. |
| Jobs / Results | Both destinations use `views/DavidJobs.jsx` and `/api/david/jobs`; show status, artifacts, effects, notification status | Keep this one store/view; make receipt/artifact references clickable and show source/usage/detail for the selected job. Consolidate duplicate navigation only after saved routes are mapped. |
| Agents | No active agent-management page/API; packages and release state are in the existing registry/Factory CLI | Add a small read-only registry view under System: package, version, release state and capability. Keep release actions out of this pass. |
| Memory | `MemoryBrain.jsx`; scoped search/entity/document/browse and editable note APIs; `/memory?record=…` links | Keep a single Memory destination; clearly distinguish canonical notes, original evidence, working handoffs and unconfirmed submissions. Keep confirmation on its supported writer path. |
| Company Home | No dedicated routed Company Home; canonical `business_brain:HOME.md` is the navigation root | Add a small read-only landing section sourced from HOME/NOW links and current job/brief evidence. Do not create another company-profile file or hand-maintained status database. |
| Capture / Ingest | `CaptureBox.jsx`, `MemoryIntake.jsx`, uploads and existing ICM/ingest APIs | Keep explicit capture separate from scheduled Gmail metadata. Show the source's ingestion/publication state and provenance. |
| Graphify / GitHub / Remote PC / System | Retained direct views; `DavidStatus` reads health; old aliases route or explain retirement | Preserve them. Add small source/schedule health links under System rather than restoring removed cockpit/queue APIs. |

The shell still stores legacy route/workbench metadata and older view files;
that is compatibility/history, not proof that each old tool is an active surface.
This inventory is source inspection, not a browser or live UX acceptance.

## Hermes short-term memory: keep / adapt / reject

Read repository source only from the preserved Hermes checkout at
`/home/operator/.hermes/hermes-agent`, HEAD
`5f47c35d37a40fb651e4a00571d03e12b23d11f9`. Its four existing edited files were
left alone. No executable, state.db, user memory, authentication or configuration
was opened or run. Evidence: `website/docs/developer-guide/session-storage.md`,
`context-compression-and-caching.md`, `agent/context_compressor.py`,
`context_compressor_summary.py`, `conversation_compression.py`,
`memory_manager.py`, and `tools/memory_tool_store.py`.

- **Keep TTROS's equivalents:** conversation identity, bounded recent exchanges,
  per-topic Brain handoffs, explicit source references, versioned Shared Brain
  workstream checkpoints and job journals. David currently bounds recent turns
  and handoff fields before ModelPort; this already provides short-term context
  without importing Hermes.
- **Adapted in the offline retrieval/continuity candidate:** preserve the newest complete exchanges, settled
  decisions, unresolved requests and exact IDs when trimming context; use a
  measured byte/token budget; replace a handoff only after a complete validated
  result; leave the old handoff intact on failure/timeout. Expose saved/failed
  continuity status to the operator. Hermes's refusal/length-stop checks and
  failure rollback are useful patterns for this existing Brain-thread writer.
  No new summary call or compressor is enabled. The candidate retains complete
  exchanges, selects named topics before context assembly, records prompt bytes
  and estimated tokens, and refuses oversized or incomplete updates. Existing
  topic notes are locked during merge; read/write/validation failure retains
  the previous note. Corrupt conversation state fails visibly without reset.
  Decisions in these working notes remain unconfirmed conversation context.
- **Reject:** copying Hermes's SQLite transcript store, parallel MEMORY.md /
  USER.md authority, plugin memory providers, dual compressor/runtime layers,
  automatic memory extraction or another session scheduler. Full transcripts
  and inferred summaries must not silently become confirmed Brain knowledge.

The same candidate adds David's deterministic `jobs`, exact job ID/status/token
reads, `/memory <query>`, `/history <period> [query or entity=<id>]` and
`what happened <period> [with <name>]`. Periods support today, yesterday,
N weeks/months ago, ISO date and ISO date ranges. Event dates use Example City;
import dates never stand in for events. Ambiguous names require disambiguation.
The existing derived entity/search index carries review state, supersession and
original ICM provenance; filters apply before pagination. Search revalidates
current scope and source stat freshness before returning evidence. David uses
keyword lookup first and retains the existing hybrid fallback. These are bounded
evidence reads, not proof that an absent indexed event never happened.

Brain promotion and ordinary-writer/confirm semantics remain unchanged. Fixture
confirmation is proved without treating accepted intake as canonical promotion.
`proofs/retrieval_continuity_2026-11-15/receipt.md` records offline measurements,
three-conversation/six-job M1/M5 SIGKILL recovery, scope/provenance checks and the
remaining live/native-write-path limitations. No new memory authority is added.
