# Supervised Fast David production acceptance

Revisit when admission, routing or queue contracts change. 2026-11-14.
`rules/runtime_permission_policy.md` is the execution authority. This procedure
adds no permission. Reload, activation and live testing require the current
operator's authorization; none occurred during implementation.

## Disabled default and scoped window

`TTROS_FAST_DAVID` remains unset/0. No canary configuration is shipped or created
by importing code. An authorized supervisor can atomically install this file in
the canonical runtime root, without a service reload once this code is loaded:

`queue/fast_david_canary.json`

```json
{"window_id":"unique-acceptance-id","conversation_id":"telegram-1320777128","expires_at":1794654000}
```

Replace the example ID with a fresh unique ID and expiry with a near-future Unix
UTC timestamp (e.g. now + 120 seconds). The example timestamp is not an activation
value. Use the existing atomic durable file writer. Confirm Casey's configured
operator destination and David chat mode through the existing bridge interfaces;
do not inspect credential files. Conversation IDs are routing identities, not
authentication: the existing bridge operator allowlist is still authoritative.

While the file exists, only that exact Telegram conversation can use Fast David;
all other Ask David Telegram conversations and legacy Telegram conversation
requests receive a deterministic hold. Telegram `/delegate` queue creation is held,
including for the test chat; `/work` and `/chain` cannot bypass through the legacy
conversation URL. Dashboard conversations and unrelated jobs continue normally.
Malformed or expired configuration keeps Telegram model intake held. A rejected
request never silently selects the legacy path.

## Admission and accounting

The fixed limit is two admissions and 24,000 tokens of previous actual usage plus
current reservation, across the whole window, not per process or message.
Each reservation is max(12,000, full UTF-8 payload bytes + 1,024 headroom), including
instructions/schema. This is a conservative input estimate and an admission
reservation, **not a hard in-flight output-token limit**. Actual provider usage can
exceed a reservation; a total overrun seals the window. Never call this a guaranteed
24,000-token actual-usage cap. If that hard cap is required, do not activate.

Admissions/settlements/stops append with the existing canonical token-ledger lock
and fsync. ModelPort still records each invocation's actual usage separately;
bookkeeping rows do not add those tokens a second time. Calls pin subscription
ChatGPT login, OpenAI provider, gpt-6-luna/low, exec/lean and 20-second timeout.
Billed-route environment variables are refused by ModelPort. No coding workbench,
API route, alternate model or automatic retry is allowed in this window.

Short referent follow-ups with an existing thread use conversation continuity and
boundaries only, plus the unchanged full permission policy in model instructions.
HOME/WORK navigation is omitted in that path. Explicit source/live-state questions
and unrecognized requests retain scope-validated navigation and source retrieval.
Admissions and rejections record payload byte estimate, output headroom, floor,
prior actual usage and remaining allowance. These estimates are not provider tokens.

Known terminal supervised failures return a normal `david_reply` with success=false
and HTTP 200, including duplicate POST recovery of a failed job. The existing bridge
can deliver that reply immediately. Genuine uncertain transport outcomes still use
read-only recovery, and ordinary non-canary error semantics are unchanged.

Unknown usage, ledger recording failure, invalid output, provider failure, queue
handoff uncertainty or an unfinished admission after restart seals further calls.
The stop marker is durable; restarting does not grant more calls. Reusing a window
ID does not reset admissions. An interrupted durable job is reported as interrupted,
never rerun; delivery receipts recover with GET and idempotent terminal edits.

## Next acceptance, after separately authorized controlled reload

1. Verify health, loaded sources, disabled global flag, no pending test deliveries,
   Git state and Fable isolation. Keep the existing runner running.
2. Install one fresh short-lived scoped window as above. Record its exact ID/expiry
   and service state. The supervisor must use a `finally` cleanup for the window.
3. Through Casey's real Telegram David conversation send: "In the background, create
   canary.txt containing exactly Amber in the local test workspace. No other action."
   Verify a single terminal acknowledgement/edit and a manual queue item tagged
   `supervised_zero_model_canary`, with **no** `async_dispatch` tag.
4. Claim that item through the existing queue tool as the supervised deterministic
   operator. Do not use Dashboard Run/Retry or launch any model worker. Immediately
   send: "While that task is claimed, what is our test project called? Answer briefly."
5. Check stable conversation identity, grounded Amber response, two admissions at
   most, exact ModelPort ledger counters, per-turn/model and Telegram latencies,
   message/edit IDs and duplicate receipts. Perform only the fixed local write/readback
   and close the same queue item with the normal completion API, local receipt and
   explicit zero-worker-token accounting. Use the live queue run-ledger review value
   `pending` for deterministic closeout; `none` is not in that schema. This is not
   autonomous worker acceptance.
6. In `finally`, wait for each admitted job to terminate (timeout/interrupt also counts
   as terminal), preserve the window ID, ledger and delivery evidence, and disarm by
   moving only the canary configuration into the scoped proof directory. Do not clear
   the ledger or reuse the ID. Verify configuration absent, global flag still disabled,
   service health and no active test work. If a handoff is uncertain, inspect the
   existing queue before any cleanup; retain manual isolation and never retry it.

Do not run the isolated script as evidence of production acceptance. There is no
need for a new queue, bridge edit, runner interruption, merge or push.
