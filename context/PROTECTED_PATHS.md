# PROTECTED_PATHS.md — what nothing in this OS touches uninvited
> Revisit: on a new protected area or a boundary incident. · Last touched: 2026-11-13.

## Source of truth
Authority: `rules/runtime_permission_policy.md` (credentials, protected evidence, client
isolation, Example Dealership). This file names the protected categories under that policy. The Brain
note `business_brain:operating_context/protected_paths.md` is reference detail (data), not a
separate authority. Original: archive/rules-consolidation-2026-11-13/context/PROTECTED_PATHS.md.

## Categories (see Business Brain for exact paths)
- **Runtime / connector state** — Telegram bridge `.env` and config, North
  Shore sales coach `.runtime/` and `data/`, all connector credentials, OAuth
  state, tokens, secrets, live runner state.
- **Archive / quarantine only** — `C:\AI-Vault`, old Ubuntu, old Hermes state,
  old AI Native Source of Truth vaults, old sessions, old skills, old ZPC
  material, any old runtime/connector/plugin/MCP state. Reference for mining
  only, on explicit approval — never a live dependency, never imported wholesale.
- **Example Dealership files** — a separate client system. Not this OS's to read,
  write, or reason about beyond what's explicitly scoped.

## Rule
No agent, workbench, or skill modifies anything in these categories without
Casey explicitly scoping that specific work in that turn. Dashboard code is ordinary scoped
work (patch in place; never a second dashboard, rules/never.md #5). Read access for
context is not write access for change. "I need to check X" is not "I may
edit X."

## Enforcement
Enforcement: tools/aos_codex_policy.py and the validated writers — checked before any write/edit,
blocks on a path match, surfaces the match in the receipt. Mirrored in
rules/never.md #2, #3, #8. A blocked attempt is reported, not silently
retried against a different path.

## What this is not
Not a blanket "don't touch the filesystem" rule — AgenticOSClean and the live
workspace outside these categories are the normal work surface. This file
exists so the narrow, high-cost mistakes (leaking a secret, corrupting a
client's live data, reviving legacy state) can't happen by drift.
