# Work order: factory.build_agent
> Revisit: when the Factory steps or the spec layout change. · Last touched: 2026-11-24.

Build one specialist agent through the Agent Factory. The item names the agent and what it is for;
the deliverable is a packed candidate and its build report. Permissions come only from
`rules/runtime_permission_policy.md`. Work in an isolated worktree. Do not merge, push, promote or route.

Agent: {{agent_name}} (`department.capability`) · Kind: {{kind}} (`tool` unless a step needs judgment, then `oneshot`)
Requirement: {{requirement}}
Source workflow or skill, if one exists: {{source}}

Do, in this order:
1. `python3 tools/aos-agent.py assess {{agent_name}} --requirement "{{requirement}}"`. Follow the
   relevant entry points and callers. Record whether the work reuses, extends or adds a capability.
   If an installed agent already covers the requirement, return its reference.
2. `python3 tools/aos-agent.py new {{agent_name}} --kind {{kind}} --summary "..." --requirement "..."`.
   This scaffolds `agents/factory/specs/{{agent_name}}/`. Everything you write goes there.
3. Schemas and golden cases first: `schemas/*.json`, then `evals/cases.json`. At least 20 synthetic cases:
   5 `edge`, 3 `adversarial`, 2 `needs_input`, and one each of `safety:injection`, `safety:canary`,
   `safety:undeclared_effect`, `safety:isolation`, `safety:missing_context`. Tag must-pass cases `critical`.
   No real client data.
4. `handler.py`: code gathers the evidence, makes at most the one pinned call, and checks what comes back.
   Reach files, the Brain and the model only through `ctx`. Take existing wording from the source workflow; do not rewrite it.
5. Name the affected tracked source paths in `factory.dependencies`. Set `factory.token_budget`
   to cover engineering calls and evaluation. For model-backed tasks, list prompt/model/effort
   candidates cheapest first.
6. Run the offline build to surface deterministic spec and case checks.
7. The queue item's executor runs the build with real subscription calls. The Factory assesses
   the design, runs the evaluations and separately reviews the scoped Git diff before registration.
   `"executor": {"kind": "factory", "action": "build_agent", "spec": "{{agent_name}}", "live": true}`
   or by hand: `python3 tools/aos-agent.py work-order <item.json>`.
8. If it stops, fix the spec and run it again. Recorded replies and matching engineering evidence can be replayed.
   Never edit `agents/packages/{{agent_name}}/`: it is generated, and `aos-agent.py verify` will show the edit.

Stop when: the build reports `candidate` at bronze or better and `verify` passes; or two full attempts have failed;
or the token budget is spent. Report the level, the pinned model, tokens spent, and any case that failed and why.

Not in this work order: promotion (`aos-agent.py promote`, Casey's call above shadow), plans, routing, the runner seam.
