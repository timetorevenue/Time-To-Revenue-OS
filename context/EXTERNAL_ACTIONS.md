# EXTERNAL_ACTIONS.md — what's free, what's gated
> Revisit: on a new connector, a boundary incident, or a compliance change. · Last touched: 2026-11-14.

Use rules/runtime_permission_policy.md as the sole action authority. These are
procedural references; they add no standing approval or separate permission gate.

## The line
Native access first, mutation guard second, no permission theatre. Free
because they touch nothing outside this system's own read path:

```
read / search / list / fetch / status / get / info
draft / prepare / summarize / recommend (local artifacts only)
create/update local work items, receipts, packets, results, scoped files
ordinary writes: CRM, Calendar, Drive / Docs / Sheets, email drafts
Telegram to Casey and declared internal notifications
```

Gated — requires an explicit command naming the specific action, every time:

```
send (email or message to other people, DM, outreach)
write / publish / post (LinkedIn, website, any public surface)
push (GitHub)
share files or grant access to other people
delete / trash / archive / move broad data
connect a new account or grant a new scope
spend money or make a financial commitment
touch a production or client-owned system
```

"Explicit command" means Casey names the action and target in the current task.
An explicit normal push request authorizes non-Codex closure to the verified remote
without a second approval. Codex never pushes, directly or through a service.
That command is approval; never ask Casey to approve the same action again. A
general go-ahead on the task ("build the proposal") is not authorization to
send it. Approval is per-action, not per-project. Agent-initiated third-party
actions remain gated.

An operator recipient allowlist validates destinations; it is not send
authorization. Messages, files and notifications require the action authority
in the runtime policy. An exact current Casey-authorized action needs no second
approval; a historical allowlist or standing-approval note cannot supply one.

Calendar booking and changes are routine and automatic; ask once only when date,
time, participants or timezone is materially ambiguous. Deleting events is gated.
A task's own tighter restriction stays binding.

## Connector spine
Composio is the shared access path (Gmail, Calendar, Drive, Docs, Sheets,
GitHub, LinkedIn, YouTube, Instagram, Maps, Agent Mail, Reddit — CRM/GHL
later). Connectors are access paths, not agents; the free/gated split above
applies identically regardless of which connector carries the call.

## CASL / outreach-specific gate
No lead is contacted without a passing email-safe/CASL check and a stated
outreach basis on the draft (rules/never.md #10). Failing that check removes
the lead from outreach entirely, not just downgrades it — never.md #12
applies (report, don't quietly drop and forget).

## Verification, not assumption
Sandbox/CLI network checks never stand in for live connector status. Live
checks use existing native readers. If a live check can't run, the
receipt says so — it does not report success from an unverified assumption.

## Enforcement
Enforcement: tools/aos_orchestration.action_authorization and the shared Composio adapter (the guard on any gated verb before
execution); the adapter never prints credentials (even to
confirm an action is safe). Mirrored in rules/always.md #11, #13 and
rules/never.md #1, #6, #10, #11.
