# Completion and verification
> Revisit: when queue completion validation changes. · Last touched: 2026-11-10.

Use rules/runtime_permission_policy.md and rules/operational_ai.md. Define the
requested outcome, authorized scope and material stop conditions, then verify the
artifact or state before reporting completion. No routing layer, department
review or compulsory queue item is required for a scoped local task.

For independently useful queue work, preserve the existing completion_contract
fields (definition of done, allowed actions and stop conditions), contract
reference, verification note and token_usage receipt required by finalize_done().
Queue flags record authority; they do not create permission. Cross-client data,
credentials, unrelated conflicting work and missing consequential authorization
remain boundaries under the runtime policy.

A scoped receipt records actual result, source evidence, focused validation,
files/commit, remaining dirt, blockers and one next action. Missing token counts
are unavailable, never estimated. Codex commits validated intended work locally;
normal push closure belongs to Casey outside Codex.
