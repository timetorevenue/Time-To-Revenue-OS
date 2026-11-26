# Agentic OS Work Queue v0
> Revisit: when queue storage, deletion receipts, receipt promotion, or rollup ownership changes. · Last touched: 2026-11-13.

Minimal local work movement layer for Agentic OS.

Items with `owner_type: agent` are executable work. Items with
`owner_type: workflow` are human-visible aggregates: `next` and `claim`
exclude them for every registered starter agent regardless of display owner
or status. Their child items remain ordinary executable work.

This queue is intentionally boring:

- JSONL/local files only
- no database
- no server
- no scheduler
- no dashboard changes
- no connector calls
- no external writes

Access behavior is `rules/runtime_permission_policy.md`; queue items reference it instead of repeating rules, and their fields never create permission.

## Files

- `queue/work_items.jsonl` stores one JSON work item per line.
- `queue/agent_registry.json` stores local agent names.
- `queue/model_routes.json` stores lane/profile/provider/model route metadata for queue runs, receipts, and token ledgers. Codex model and effort come from `codex_selection`; placeholders such as `configured externally` mean no explicit override.
  profile identity. Its live profile has no skills, memory, delegation, or
  built-in toolsets and exposes only the six bounded local queue tools in
  `tools/operator_lean_mcp.py`.
- `queue/run_ledger.jsonl` is the master per-run record (`run_ledger_schema.json`); one line appended per item at done-transition.
- `queue/token_ledger.jsonl` is the append-only token-usage ledger (`token_ledger_schema.json`); one line per completed receipt's `token_usage` block. Numbers come from harness/API usage only — unreportable components are listed under `unavailable`, never estimated.
- `queue/rollups/` holds intentionally versioned weekly snapshots produced only
  by an explicit `scripts/token_rollup.py` run (dashboard-ready JSON, no front
  end). Ordinary capture polling writes its live metrics under ignored
  `capture/runtime/rollups/` and must not rewrite these snapshots.
- `queue/receipts/` stores optional receipt artifacts; the coordinator writes a `<id>.token_usage.json` sidecar and, when a markdown receipt exists, a fenced `token_usage` block.
- `queue/receipts/task-deletion-<request-id>.json` is the one minimal tombstone for a physically removed task. It contains no task context, prompt, source, output, raw receipt, artifact, or ledger content.
- `queue/locks/` is reserved for future local lock files.
- `queue/schemas/` documents the JSON shapes.

## Prompt Templates

- `queue/templates/` contains copy/paste Codex, Claude, and receipt templates for scoped queue work.
- `rules/runtime_permission_policy.md` is the one permission authority those templates reference.

## Receipt artifact policy

- `queue/receipts/*.md` and `queue/receipts/*.json` files are local runtime
  proof trail by default.
- Smoke-test receipts should not be committed.
- Real receipts may be committed only when intentionally promoted as durable project proof or handoff.
- `queue/receipts/.gitkeep` remains tracked so the folder exists.
- Do not include secrets, tokens, customer private data, raw OAuth values, Telegram IDs, or connector credentials in receipts.

`queue/run_prompts/` and routine `workflows/queue_artifacts/AOS-2026-*.md`
worker output are likewise ignored local runtime state. A reviewed artifact is
promoted deliberately with a narrow `.gitignore` negation (or a path-limited
forced add); tracked historical artifacts remain tracked. Browser proof
directories and the repository-level `proofs/` tree remain available for
durable, bounded evidence.

## CLI

Run from the repository root:

```bash
python3 -m pip install -r requirements.txt
```

```bash
python3 tools/aos-queue.py create --title "Review packet" --requested-by casey --owner codex
# Ready agent work is picked up by aos-runner automatically (tagged async_dispatch; --manual opts out):
python3 tools/aos-queue.py create --status agent_todo --owner codex --title "Summarise notes" --context "..."
python3 tools/aos-queue.py list
python3 tools/aos-queue.py show AOS-2026-0001
python3 tools/aos-queue.py claim AOS-2026-0001 codex
python3 tools/aos-queue.py release AOS-2026-0001 --status agent_todo
# Status update syntax: positional status, no --status flag.
python3 tools/aos-queue.py status AOS-2026-0001 human_review
# Receipt attach syntax: positional receipt path; optional --status updates the item at the same time.
python3 tools/aos-queue.py receipt AOS-2026-0001 queue/receipts/AOS-2026-0001.md --status done
python3 tools/aos-queue.py next codex
# Permanent deletion is compare-and-swap protected and writes one minimal tombstone.
python3 tools/aos-queue.py delete AOS-2026-0001 --expected-record-hash SHA256 --reason "Duplicate fixture" --request-id delete-local-0001
# Coordinator close: resolve lane->profile, write run+token ledgers, meter tokens.
# Token numbers come from a harness usage source (never estimated); omit them to record "unavailable".
python3 tools/aos-queue.py done AOS-2026-0001 --receipt queue/receipts/AOS-2026-0001.md --usage-file /tmp/usage.json
# Direct Codex: explicit item ID + prompt on stdin; waits for exit and reconciles
# usage independently of whether the item honestly ends in human_review, done,
# needs_input, blocked, or another supported status.
python3 tools/aos-queue.py codex-run AOS-2026-0001 --prompt-file - < /tmp/AOS-2026-0001.prompt.md
# Historical pasted evidence uses the same path and an explicit session identity.
python3 tools/aos-queue.py codex-reconcile AOS-2026-0001 --session-id SESSION_ID --summary 'Token usage: total=3 input=2 output=1' --cli-version 'codex-cli 0.144.1'
python3 scripts/token_rollup.py            # weekly rollups -> queue/rollups/
```

Any transition into `done` (via `status`, `receipt --status done`, or `done`)
triggers the coordinator: it appends one line to both `queue/run_ledger.jsonl`
and `queue/token_ledger.jsonl` and writes the `token_usage` block into the
receipt. The `status`/`receipt` paths keep queue liveness on a metering hiccup
(surfaced as `NEEDS ATTENTION (metering)` on stderr); the explicit `done`
command is the strict path.

`codex-run` uses the installed CLI's noninteractive `codex exec --json` command,
captures stdout and stderr together, waits for process exit, and prefers the
final structured `turn.completed` usage event over a terminal-summary
cross-check. It reconciles by work-item ID + Codex session ID, regardless of
queue status. Repeating identical evidence is a no-op; unavailable can upgrade
to exact; exact cannot downgrade; conflicting exact evidence fails closed;
different sessions remain separate ledger invocations. The canonical item
sidecar and one receipt block expose the selected invocation to dashboard
consumers. Cached input remains separate from input+output total, and reasoning
output remains a labelled subset of output.

Approved statuses:

- `inbox`
- `agent_todo`
- `agent_working`
- `needs_input`
- `human_review`
- `done`
- `blocked`
- `cancelled`
