# queue/memory_promotion_prompt.md
> Template only — does not write to the Business Brain. A promotion is a
> review-tier proposal until an accepted human_review reference exists.
> Authority: rules/runtime_permission_policy.md; writer contracts:
> context/MEMORY_PROMOTION_POLICY.md. Routine sourced learning uses validated
> writers without this proposal template.
> Revisit: when promotion authority or logical pointer conventions change. · Last touched: 2026-11-10.

## Purpose
Turn a completed item's discovery into an explicit, reviewable proposal to
update durable memory — never a direct write.

---

**Memory promotion proposal — {item_id}**

Lane: {lane} · Source item: {item_id}

### What's being proposed
{proposed_change}

### Target file
{target_memory_file} (e.g. `business_brain:memory/positioning.md`,
`business_brain:memory/offers.md`, `business_brain:decisions/DECISIONS.md`,
or an exact canonical client pointer supplied by the reviewed work item)

### Why this qualifies
Check against MEMORY_PROMOTION_POLICY.md — must be one of:
- [ ] Stable decision
- [ ] Approved positioning/offer change
- [ ] Recurring workflow improvement (skill graduated — see skill_trust.jsonl)
- [ ] Durable source-of-truth change (new client fact, new ICP evidence)
- [ ] Receipt worth keeping as precedent

### Explicitly NOT this
Confirm none of these apply — if any do, do not propose:
- Draft, raw transcript, or failed run
- Old-vault or legacy content
- Secret, credential, or connector account metadata
- Speculative or unsourced claim
- Another client's data

### Source
{source_pointer} — every claim in the proposal must trace back here.

### Required executable fields
- Client scope: {client_scope}
- Target preimage SHA-256: {target_preimage_sha256}
- Candidate diff: {candidate_diff}
- Provenance references: {provenance_references}
- Authority tier and reason: {authority_tier_reason}

### Gate
The existing classified promotion API requires an accepted human_review
reference for review-tier candidates before PromotionWriter may run. No Hermes
orchestrator or mandatory workbench review is involved. Deterministic automatic
work needs no queue item and uses only the rule's named writer. Routine sourced
learning/import uses brain_memory validated transactions under the runtime policy;
not all Brain writes are classified promotion proposals.
