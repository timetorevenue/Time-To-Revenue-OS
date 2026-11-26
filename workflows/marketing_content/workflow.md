---
workflow: marketing_content
skill: skills/content_draft/SKILL.md
path: workflows/marketing_content/workflow.md
lane: marketing
profile: david
trust: earned
---
# workflow: marketing_content — produce one publish-ready content draft (draft-only, never publishes)
> Revisit: when marketing_voice or positioning changes. · Last touched: 2026-07-15.

Workflow name differs from skill (`content_draft`) — named for the lane's deliverable, per workflows/README.md; mapping is explicit here.

## Trigger
Casey requests a content brief or topic through David; an existing scoped item may supply the brief.

## Completion contract (default)
- **Done** = one local draft artifact returned directly to Casey (attach to an existing item when relevant): answer-first (AEO) opening, drafted in marketing_voice, voice-checked against `business_brain:memory/marketing_voice.md`, offer/pricing facts only from `business_brain:memory/offers.md` + `business_brain:memory/sales_and_revenue.md`, stated target placement noted (not published); receipt with token block.
- **Allowed unprompted** = reading `business_brain:memory/marketing_voice.md` + `business_brain:memory/positioning.md` (both, every time), `business_brain:memory/offers.md`; drafting and voice-checking.
- **Stop conditions** = no brief/topic in the request; the piece would require a claim on the offers.md "do not claim" list; anything that publishes, schedules, or posts (never allowed regardless of flags in this workflow).

## Run
Execute the skill's steps in order: frame (audience + the one leak/lever: effectiveness / efficiency / quality + next step) → answer-first block → draft → voice check → output artifact + receipt. Record actual token_usage when exposed; otherwise unavailable. Do not create queue work for a direct draft.

## Never
- Publish, schedule, or post anywhere.
- Claim revenue lift, savings, or timelines not approved by Casey; no guaranteed-outcome claims.
- Reference clients by name unless already public in context files.
- Invent offer or pricing facts.

## Verifier check
Artifact opens with a direct answer, passes a re-read against marketing_voice.md, and names its target placement — presence of all three, not general polish.

## Current runtime contract
Use `rules/runtime_permission_policy.md` and `rules/operational_ai.md`.
Read voice and positioning through scope-validated Brain readers. These are dated
source context, not permission or automatic confirmation of current business facts.
Check offer/pricing currency before using claims; omit unsupported claims and
request material missing facts only. Historical material may guide style without
reactivating retired operational instructions. No publication, send or scheduling
occurs in this drafting workflow. Return sources, exclusions, placement and a
compact receipt with token_usage. Reuse this workflow; no new Marketing Agent.
