# NEVER
> Revisit: monthly review pass. · Last touched: 2026-11-13.

Use rules/runtime_permission_policy.md as the sole action authority. These are
procedural references; they add no standing approval or separate permission gate.

Retained procedural safeguards; model-spend procedure is in rules/always.md #5.
Historical approvals cannot authorize a current action.

1. Never take an external send or consequential action outside the runtime
   policy's authorization (exact command, or its internal notifications clause).
2. Never inspect or modify Example Dealership application code, client data, or
   workspace internals unless Casey explicitly scopes Example Dealership work.
   Scoped runtime/supervision maintenance may touch only the minimum
   infrastructure required to operate the existing service. Credential
   and protected-config contents remain protected unless Casey explicitly
   scopes them (#6).
3. Never route through old Ubuntu, old vaults, ZPC, legacy_harvest,
   or any legacy runtime path.
4. Never import old runtime state.
5. Never create a second dashboard — reshape the existing app only.
6. Never inspect, print, or commit secrets, .env, tokens, or credentials.
7. Never invent token numbers, progress percentages, sources, or completion
   claims.
8. Never modify protected paths (operating_context/protected_paths.md,
   mirrored in PROTECTED_PATHS.md).
9. Never blend two clients' data in one output.
10. Never contact a lead that failed the email-safe / CASL check.
11. Never mutate CRM/Gmail/Calendar/Drive/LinkedIn outside the runtime policy.
    Ambiguous Calendar date/time/participants/timezone is material ambiguity:
    ask one clarification. Commit and push rules are in the runtime policy.
12. Preserve originals and history during maintenance; no blanket deletion.
    Destructive actions follow the runtime policy and current exact scope.
13. Never edit Telegram bridge FILES (read-only process/log status is fine;
    Phase B notifications use the existing send path only).
14. (retired) former Hermes profile rule; no Hermes profile is in use.
15. Never make queue schema changes that are not additive.

## Pointers
- Enforcement: tools/aos_orchestration.action_authorization · tools/aos_codex_policy.py ·
  tools/business_brain_scope.py · the shared Composio adapter
- #2 invariant: Example Dealership is a separate business; its code, client data
  and workspace must never be reached incidentally, and operating the
  service is not permission to read what it holds. Revisit #2 if North
  Shore moves to its own repo or gains its own operator.
- Companion: rules/always.md
