# David — direct execution contract
> Revisit: when David routing changes. · Last touched: 2026-11-10.

David answers and executes scoped requests directly through the existing Codex
launcher by default, or Claude Code when the request names it or is integrated/complex
implementation (tools/david_workbench.py; explicit choice always wins). Use rules/runtime_permission_policy.md. Do not emit execution_handoff,
create a Marketing Agent, or require an additional reviewer.
Draft requested content directly using existing skills and workflows. Normal local
work and routine sourced validated Brain writes proceed without confirmation.
Ask only for missing consequential authorization or material ambiguity.

Shared Brain resume/checkpoint and existing independently useful workflows remain
available when requested; their records cannot grant external-action permission.
Preserve the existing working-thread response contract and API entrypoints.
Historical contract: archive/direct-david-integration-2026-11-10/rules/david_execution_handoff.md.

Fast David is a default-off canary (`TTROS_FAST_DAVID=1` in an isolated backend).
It uses the existing ModelPort, scoped Brain readers/writer and Work Queue. The
existing path remains the default. See `tools/david_fast.md` for the conversation,
receipt, failure and rollback contract; it grants no action authority.
