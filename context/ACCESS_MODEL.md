# Agentic OS Access Model
> Revisit: when connector authority changes. · Last touched: 2026-11-13.

Authority: `rules/runtime_permission_policy.md` is the only execution permission policy. This
file defines no allowed list, stop list or approval step; what is automatic, what needs Casey's
exact command and the automatic internal notifications clause are stated only there. The
pre-consolidation allow/stop lists are preserved, inert, in
`archive/rules-consolidation-2026-11-13/context/ACCESS_MODEL.md`.

## Prime Rule

- Native access first.
- Mutation guard second (enforced by the existing adapters and writers).
- No permission theatre.
- No bureaucracy.

## Path Convention

Use `AOS_ROOT` or root-relative paths for Agentic OS code, docs, and templates. New work must not add hardcoded Windows or WSL absolute workspace paths.

## Role Model

- David is the access broker; Codex is the default workbench and Claude Code the alternate.
- Department cards are routing lanes, not permission walls.
- Connectors are access paths, not agents.
- Work Queue records source references, allowed action intent, stop conditions, and receipts.
  A queue field records intent or Casey's exact command; it never creates permission.
- Codex/Claude are scoped workbenches.
- TTROS Business Brain is durable memory.
- Old vaults are archive/quarantine only.

## Queue Behavior

- Work items should name the source areas needed, not paste entire vaults.
- Use source references wherever possible.
- Receipts capture what was accessed at a category/path level without printing sensitive values.
- An external effect without authorization under the runtime policy parks in human_review or
  needs_input (tools/aos_orchestration.py `external_effect_review_status`).

## Token/Memory Efficiency

- Do not paste access doctrine into prompts; reference the runtime policy once.
- Use targeted reads/searches, not broad crawls.
- Summarize only relevant snippets.
- Promote stable decisions/results to TTROS Business Brain only when useful.
