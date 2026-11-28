# Agent Factory
> Revisit: when the Factory steps, the spec layout or the registry change. · Last touched: 2026-11-27.

The Factory builds a TAP agent package from a build spec and gates it. It is a CLI
(`tools/aos-agent.py`) and one work-order template (`queue/templates/factory_build_agent.prompt.md`),
not a service. It grants no permission; `rules/runtime_permission_policy.md` is the only authority.

| Path | What it is |
|---|---|
| `specs/<name>/` | The source of an agent: `spec.toml`, schemas, golden cases, handler, prompt variants. Edit here. |
| `work_orders/<name>.json` | A `factory.build_agent` item in queue-item shape. |
| `../packages/<name>/` | Generated from the spec. Never edited by hand; `verify` regenerates and compares. |
| `../registry.lock.json` | Which package content was evaluated, its level, its archive hash and its release state. |
| `../dist/` | Archives (`<name>-<version>.tar.gz`). Build output, git-ignored, re-created byte-for-byte by `pack`. |
| `improvements/` | M6: offline, propose-only improvement proposals (`aos-agent.py improve`); see its README. |
| `app_forge/` | App Forge recipes: pinned external software (source commit, package integrity, file digests, licence obligations) and acceptance fixtures. `tools/app_forge.py discover\|assess\|install\|verify` runs each step as a deterministic Job (kind `app_forge`); evidence states are derived, never approvals. |

```text
aos-agent.py assess <name> --requirement TEXT [--dependency PATH]
aos-agent.py new <name> --kind oneshot|tool    scaffold a spec
aos-agent.py build <name> [--live]             assess, scaffold, evaluate, review, pack and register a candidate
aos-agent.py optimise <name> [--live]          candidate search only
aos-agent.py work-order <item.json>            run a factory.build_agent item
aos-agent.py verify <agent>                    package == spec output == evidence == registry
aos-agent.py pack | register | promote | registry
```

### Engineering quality

TTROS uses Interpretable Context Methodology (ICM) to keep reference material and working evidence
small, source-linked and inspectable. See the [adopted ICM practices](../../docs/ttros/00_TTROS_CURRENT_STATE_v2026-09-08_rev7.md#icm--what-the-acronym-means-and-the-hybrid-boundary).

- **Inputs.** Start with the original requirement and the current Git base. Run `assess` to find
  related TAPs, registry states and interfaces. Follow their actual callers and add relevant
  tracked source paths to `factory.dependencies`. Use scoped Brain pointers for business evidence.
- **Process.** Establish the owner of each responsibility. Reuse an existing capability if it
  covers the requirement, extend its contract if it owns the behavior, or add one focused capability.
  Write schemas and golden cases first. Keep deterministic checks and source-linked context small.
  On a substantive Git change, Forge asks ModelPort for an assessment before generating files.
- **Outputs.** The existing TAP spec and generated package carry the implementation. The Factory
  report records the design, Git base, evidence digests, review and model usage.
- **Verify.** After evaluation, a fresh tool-less ModelPort call receives the original requirement,
  scoped Git diff, selected manifest, affected source excerpts and test results. It checks fit,
  efficiency, duplication, maintainability and correctness. Resolve blocking findings before
  candidate registration. This is build evidence. Existing release and invocation paths remain separate.

Without `--live`, Forge uses recorded evidence. Substantive builds without matching evidence report
the missing assessment or review. Live builds use the existing subscription and share
`factory.token_budget` across assessment, candidate evaluation and review. Budget at least 36,000
tokens for the two engineering calls in addition to evaluation. A quota window at 50% is advisory.
Candidates are tried cheapest first on the training cases. Only the selected candidate sees the holdout.

The `marketing.landing_brief` spec is a worked example. It reads the website positioning note and
one approved ICP hypothesis through TAP context, then returns a structured landing-page brief
with one CTA, a proof check and a measurement checklist for manual handoff to Website Studio.

Release states are a record. `candidate` needs evidence; `shadow` needs bronze; `canary` needs silver;
`active` needs gold. Production work goes only to `canary` and `active` packages
(`aos_jobs.refs_release_problem`), so a step up into either is a release: `promote` asks the runtime
permission authority (`aos_authority`, action `AGENT_RELEASE_CANARY` / `AGENT_RELEASE_ACTIVE`), which
holds it for Casey's explicit command. `--approved-by` is recorded with the event but never authorizes.
The Dashboard's `POST /api/agents/release` accepts an authenticated explicit command bound to
agent/version, package digest and state. Its request-local authorization expires after 60 seconds
and is single-use; CLI metadata and serialized job fields cannot recreate it.
Stepping down is always allowed.

Package identity (`package_sha256`) excludes `evidence/` and `evals/recordings/`, and every evidence file
pins that hash, so adding evidence never changes identity. The archive bundles the evidence, so after new
evidence (for example gate 7) run `aos-agent.py release repack <agent>`: it re-packs the unchanged package
and records only the new archive, keeping state, level, built and holdout.

Gold is awarded only by M7 (`tools/aos_agents/release.py`, `aos-agent.py release check|stage|drill`):
gates 1-6 from current evidence; gate 7 (TAS reliability: 5 cases x 3 repeats >= 85% same verdict, kill -9
resume with 0 duplicate effects and <= 1 repeated call, forced quota/auth failure is a truthful blocked
with no billed fallback) from `evidence/<binding>.reliability.json`. `release reliability` collects
existing jobs plus captured probe sources; `aos_agents.reliability` preserves a separately hash-pinned
provenance bundle and recomputes all summary checks. Its probe helpers operate only when explicitly
invoked on an owned qualification subprocess/existing job. Simulations never qualify. Repeat
consistency currently requires identical output digests (stricter than semantic verdict equivalence);
model-call duplication uses durable pre-launch attempt keys, including interrupted calls. Missing provenance fails closed; and, separately, 20 clean real canary runs of the exact package binding. A run counts only
when it is recorded as accepted in `../canary_acceptances.json`, its job record, receipt, registry history
and token ledger agree, its receipt attests the executed digest and binding, and its provenance
commit in git holds that exact package released. Historical inference alone cannot count. `release
stage` writes a candidate and rollback manifest without touching the registry; `release drill` applies it
to a scratch copy and reverses it. READY is technical evidence, not authorization.
