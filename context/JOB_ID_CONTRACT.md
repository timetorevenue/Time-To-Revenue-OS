# TTROS permanent job ID contract
> Revisit: when job allocation, storage or API identity changes. Last touched: 2026-11-15.

This describes the existing clean-break runtime. It grants no execution permission.

- New business jobs are `AOS-YYYY-<decimal sequence>`, padded to at least four
  ASCII digits, with no maximum width. IDs remain strings in JSON, HTTP, MCP,
  receipts, notifications and ledger attribution. Never convert them to JS numbers.
- `tools/aos_jobs.py` owns allocation, persistence, claims, recovery, effects and
  completion notices. M1/M5 execute its agent/plan/planner specifications; M4
  executes its Factory build specifications through the existing executor adapter.
  There is no second executing queue. Factory work-order template IDs describe
  the source definition; `aos-agent.py work-order` allocates a canonical job ID.
- One POSIX `flock` on `queue/jobs/allocation.lock` serializes allocation and
  dedupe publication. `queue/jobs/.sequence.json` stores per-year decimal strings.
  The reservation is fsynced before publishing the job. Crashes may leave gaps;
  reserved numbers are never deliberately reused. A published job stores its
  dedupe digest so a crash before key publication cannot duplicate the request.
- Initial annual high-water marks reserve historical IDs in work items, deletion
  tombstones (including pending ones), orchestration/run/token ledgers, receipts,
  M1 journals, results and existing job records. Historical state is only read.
  Corrupt sequence or deletion evidence refuses allocation. Freeze the old
  allocator before initialization; never run both writers during cutover.
- Short IDs retain `queue/jobs/<id>.json`, `queue/threads/<id>.jsonl`,
  `queue/receipts/<id>.md` and `results/agents/<id>/`. For long safe identifiers,
  `aos_job_ids.storage_key` uses `id-<full SHA-256>` as the path component; records,
  artifact metadata and receipt bodies retain the complete ID. Long-ID artifact
  references use that same compact key so existing agent input schemas remain
  valid without rebuilding packages. Both full and compact references resolve.
  This avoids a filename or artifact-reference limit becoming a sequence limit.
- Existing `AOS` and `J-YYYYMMDDTHHMMSSZ-xxxxxxxx` identifiers, files and ledger
  rows are preserved. Shared Brain checks the new job store first, then reads the
  historical Work Queue. No old item is automatically migrated or executed.
- The existing token ledger, cost dial and per-session fuse are unchanged.
  Every new M1/M5 or Factory model invocation carries the canonical job ID;
  unknown accounting on that ID blocks a later model session as before. Receipts
  and notices read lifetime totals from that ledger after a restart; the runtime
  usage field describes the current execution attempt. No ledger rows are rewritten.
- `DJ-...` identifiers name conversational turns and their polling/delivery
  contracts, not business jobs. Remote PC and eval fixture identifiers likewise
  keep their existing independent meanings.
- Backup/rollback must preserve `.sequence.json`, jobs, dedupe keys, journals,
  receipts, artifacts and the append-only ledger together. Never roll back the
  counter or restart the retired allocator after new IDs have been issued.

Current task restrictions remain binding: no paid calls, promotion, canary,
service changes, deployment or push without the required authorization.
