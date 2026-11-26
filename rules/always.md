# ALWAYS
> Revisit: monthly review pass. · Last touched: 2026-11-13.

Use rules/runtime_permission_policy.md as the sole action authority. These are
procedural references; they add no standing approval or separate permission gate.

Operator-approved rule set (pruned 2026-07-08). Absolutes an LLM invented and
the operator never approved were removed; see rules/never.md for the hard
prohibitions.

1. Every meaningful queue item leaves a receipt: lane, profile, model requested,
   model confirmed, tokens (exact / ~estimate / "unavailable" — never invented),
   artifact paths.
2. Every external-facing draft states its outreach basis (CASL block for leads).
3. Every Business Brain write uses canonical `business_brain:<relative-path>`
   references and introduces no structural failure. Which records need
   navigation and which are raw evidence is defined once, in
   tools/validate_business_brain.py.
4. Every new file in the substrate carries a Revisit: or Expires: line.
5. Deterministic script first where it can complete the work. Model calls use
   the one visible `light|standard|heavy` cost dial and record the actual model;
   no hidden rule may silently downgrade it. Model spend happens on explicit
   operator action through the current runtime — never silently from typing,
   viewing, or searching.
6. State sources for every factual claim in client-facing output.
7. Keep each client in its own context. One client, one thread, one folder
   (rules/client_data_boundaries.md).
8. Verify work against its completion contract before reporting done
   (rules/completion_contract.md).
9. Use existing native readers for live checks; never claim unverified connector status.
10. Resolve routine choices within scope; ask only for material ambiguity or missing consequential authorization.
11. Messages, files and notifications follow the runtime policy, including its
    automatic internal notifications clause; an allowlist alone authorizes nothing.
12. Surface blockers in the current response and existing local status views.
    Old escalation timeouts are not standing Telegram-send authorization.
13. Consequential-action authorization is defined only in the runtime policy;
    do not request duplicate confirmation.

## Efficient-operation doctrine

The unit of work is the requested practical outcome, not maximum inspection or proof. For routine operations:

1. Use deterministic/local mechanisms whenever they can correctly produce the answer or perform the operation.
2. Call a model only for genuine interpretation, judgment, synthesis, or language reasoning.
3. Do not repeatedly prove established platform invariants during normal use.
4. Distinguish FIRST BUILD / REPAIR (focused tests plus representative end-to-end proof) from ROUTINE USE (narrow integrity/result verification only).
5. Reuse existing evidence and live contracts instead of rediscovering repository history.
6. Avoid broad repository reads, full suites, multi-agent investigation, and extensive proof artifacts unless failure evidence or change scope requires them.
7. Optimize for useful outcome per token, not maximum context.
8. Routine source/data operations use existing deterministic capabilities instead of invoking Codex to rediscover the system.
9. Load only the smallest relevant knowledge into model context.
10. Commit validated scoped work; push and unpushed-commit rules are in the runtime policy.

Preserve protected boundaries and the classified promotion API validation;
routine sourced learning uses validated writers under the runtime policy.

## Pointers
- Enforcement: hooks/receipt_completeness_check.md · hooks/token_budget_check.md
- Companion: rules/never.md
