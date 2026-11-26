# Direct work and retained maintenance
> Revisit: when active callers or schedules change. · Last touched: 2026-11-13.

rules/runtime_permission_policy.md is the authority. David identifies the domain,
reads business_brain:HOME.md and scoped evidence, uses Codex or an existing tool,
verifies the result and records a receipt. Codex is the default implementation
workbench and Claude Code the alternate, as the runtime policy states. No profile resolution, compulsory queue item, department fan-out,
orchestrator review, model escalation chain or automatic Work Mode applies.

Use the existing durable queue only where the requested work needs it. Keep live
job state there; memory holds source-backed durable knowledge and pointers.
Use existing scoped readers and validated writers, preserving client isolation,
preimage checks, receipts and evidence. Diagnose and repair scoped failures under
rules/operational_ai.md; do not repeat failed calls blindly.

Retained independent procedures:
- skills/morning_brief/SKILL.md: deterministic attention query and direct David
  interpretation through the existing Ask David API. Any external delivery needs
  the authorization required by the runtime policy; old standing-approval text
  is not permission for this task.
- tools/nightly_knowledge_hygiene.py: deterministic reconciliation, validated
  transaction and projection refresh; historical targets remain retired.
- skills/maintain_os/SKILL.md: scoped maintenance on request or failure evidence.

Inspect installed schedules before asserting their live state. Do not revive the
retired orchestration runner or headless Claude watcher. Preserve independent
workflow reporting, ChatGPT wake and Drive transport that share the old service.
