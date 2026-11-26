# Local Drive source watcher

Revisit when Drive, source intake, scope or runtime contracts change.

`tools/drive_watcher.py` reads Drive root `1PdJ5qci_nH3KCbbAWx_xalsq8X7tNktF`
and its eligible descendants. It uses read-only HTTPS GETs and Application Default
Credentials through google-auth. The connected ChatGPT Drive plugin cannot supply
an unattended local Changes API session. Authentication must be supplied by the
operator through Google's normal SDK process; never copy ChatGPT tokens, print
credentials, or commit credential material. No credential/config file is inspected
by the workbench. A missing ADC session produces `DefaultCredentialsError` in the
receipt and no capture.

The systemd user units are installed under `~/.config/systemd/user/` but initially
disabled. After authentication and a reviewed bounded canary, the operator may
activate `aos-drive-watcher.timer`. Do not activate a competing watcher. The unit
runs in AgenticOSClean every five minutes, with a four-minute timeout, 1 GiB memory
limit and 50% CPU quota. The watcher commits locally under process-local Git overrides
that disable hooks and push transport. Validated captures carry the Brain sync trailer.
The separate Brain Git sweep verifies and syncs those commits to the authorized private
origin. Capture and sync use deterministic code.

Run a bounded pass with:

```bash
cd /opt/ttros-core
.venv/bin/python tools/drive_watcher.py --pages 1 --batch 1
cat logs/runtime/drive_watcher/receipt.json
systemctl --user status aos-drive-watcher.timer
```

The initial inventory takes a start cursor **before** walking folders. The SQLite
BFS queue persists each folder/page and the cursor advances only after metadata is
durable. One in-flight Changes page and its row offset are persisted, so
deep ancestry lookups resume within a page after an API-budget interruption. Failed content remains dirty independently of cursor progress. Changes
are replay safe. An expired cursor starts another bounded inventory, retaining all
original evidence. Unknown parent chains use metadata only, at most 100 ancestors,
and content is never read until ancestry and scope pass. File and ancestor metadata
are checked again immediately before each new content capture; ancestor checks are
shared within one tick to keep deep hierarchies efficient. Shared-drive flags are
included. Drive shortcuts and multi-parent ambiguity are metadata only. Folder
moves into scope schedule inventory; folder moves/deletions out of scope invalidate
descendant eligibility without physically deleting records or originals. A source
provenance record is marked inactive; prior evidence remains historical and searchable.

Default per-run limits: two metadata pages, 100 rows per page, 128 API calls total,
five pending items per tick, 750,000 bytes per source/normalized result and 25 MiB
expanded DOCX archive size. There is **no daily batch or byte ceiling**: the former
24 batches / 10 MiB per UTC day caps starved capture (the backlog sat pending behind
them) and were removed. Throughput is bounded only by the per-run limits and the
five-minute timer. API errors do not advance their page token. Failing sources rotate
behind other dirty items. A permanently unreadable or oversized source stays pending
with a failed count. No timer deletes data or resets state. Retain the SQLite
database/WAL when recovering; losing them re-inventories and deduplicates against
existing exact source records. Single-instance locking reuses Memory Exchange's lock
helper. systemd timeout bounds lock waits.

Usage record (local, not a quota subsystem): per UTC day the state DB keeps API calls,
estimated quota units (1 per GET, a conservative estimate), downloaded egress bytes,
captures, failures and throttle events. The receipt's `usage_today` reports these plus
`pending`, `inventory_folders_pending` and `backlog_runs_at_current_batch`. They count
**this watcher only**, never project-wide Google usage. As protective headroom, not a
target, the watcher defers new work if its own estimate reaches 85% of Google's
published standard daily thresholds (340 million units, 850 GB egress).

Throttling: HTTP 429, or 403 with a rate/quota reason (`rateLimitExceeded`,
`userRateLimitExceeded`, `dailyLimitExceeded`, `quotaExceeded`,
`sharingRateLimitExceeded`), stops the pass without losing durable state. The watcher
backs off for the larger of `Retry-After` and an exponential 60 s…1 h delay, makes no
API calls during backoff, and resets after a clean pass. Other 403s (permissions) stay
ordinary per-file failures. Response bodies are only inspected for the reason code.

Supported content: native Google Docs exported to UTF-8 text, TXT, Markdown, DOCX
and text-bearing PDF. Normalization reuses `drive_memory_intake.normalize`.
Scanned PDFs require a later OCR workflow. Capture reuses `source_intake`'s exact
byte record and navigation renderers and the validated `brain_memory` transaction.
Only `sources/intake/records`, its INDEX and `sources/intake/drive/<id>.md` are
written. One batch creates at most one Brain commit. Source records are keyed by
normalized content SHA-256; binary originals are preserved under the runtime state
by raw SHA-256, outside Git. Identical content from different IDs shares a source
record, with one stable provenance association per Drive ID. Updates preserve
historical evidence; metadata-only renames/moves do not redownload unchanged versions.
Export/download is followed by a metadata check; a racing version is retried.

Provenance keeps Drive ID, path, URL, modified timestamp, raw hash, source pointer,
blob head revision where available and Drive version. Google Docs use a labelled
Drive version, because `headRevisionId` is unavailable for native Docs; this is not
a claim to possess a native Docs revision ID. Source bytes are the exported or
normalized content, with original raw bytes separately preserved locally.

Exclusions apply **before content reads**: existing indexer scope/secret/legacy
rules, Example Dealership variants, client/protected/confidential folders, generated
mirrors, Memory Exchange READY/IMPORTED/REJECTED and source/session/queue outputs.
Ambiguous ancestry fails closed. Folder names do not prove undisclosed client
ownership: operators must keep protected client material in the established
isolated namespaces. No content-derived category is guessed. All sources remain
`category: unknown`, `interpretation: required`; no trusted classification map is
configured. Capture is separate from semantic ICM. ICP-A/B and canonical business
decisions are never targets.

Retrieval reuses `memory_exchange_import._refresh`: incremental lexical indexing,
entity index, existing local ONNX embeddings where their scope supports them, and
Graphify. Raw intake sources remain keyword-only under the existing embedding
contract. No paid extraction or semantic calls are made. Derived-refresh failure
leaves evidence committed and dirty for retry; successful retries do not create
another evidence commit. Receipts overwrite one bounded JSON file with last check,
discovered/imported/metadata-only/duplicate/failed/pending counts and zero model
invocations. Counts describe each pass; pending work is not reported imported
until the writer and retrieval refresh return successfully.

Validation:

```bash
.venv/bin/python -m unittest tests.test_drive_watcher tests.test_drive_memory_intake tests.test_source_intake.SourceIntakeTest.test_capture_exact_retrieval_duplicate_zero_model_and_boundaries -v
systemd-analyze --user verify systemd/aos-drive-watcher.service systemd/aos-drive-watcher.timer
```

The synthetic local canary uses a temporary Git vault and real validated writes;
it verifies exact bytes, deduplication, provenance, rename/deletion markers and
the Brain sync trailer. Its derived-refresh boundary is mocked; the focused existing
source-intake test independently exercises real lexical retrieval and Graphify.
Live Drive and live Brain capture require operator authentication verification.

The sweep's latest sync result and verified upstream SHA are recorded in
`logs/runtime/brain_git_closure.json`.
