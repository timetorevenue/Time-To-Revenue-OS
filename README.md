# TTROS Core

> **Your business. Your intelligence. Your operating system.**

TTROS Core is the open-source foundation of TTROS, the Time to Revenue Operating System: a customer-owned AI business
operating system. Deterministic software does the reliable work (jobs, permissions, records, retrieval, validation);
AI models provide judgement, reasoning and drafting, behind explicit permission and evidence rules.

**Status: v0.1.0-alpha.** It runs, it is tested, and it is early. Read [Alpha limitations](#alpha-limitations) before relying on it.

## What is in Core

| Area | What you get |
|---|---|
| **David** | Operator conversation and background Jobs on a model-neutral port (`tools/aos_david.py`, `tools/aos_jobs.py`). |
| **Business Brain** | A Git-backed Markdown vault with scoped retrieval, source provenance and a validator. Starts empty. |
| **Universal knowledge (ICM)** | Reusable, company-neutral methods with a rights register (`universal/`). One seed method ships: ideal-customer-profile definition. |
| **Agent Forge** | Package, evaluate and register specialist agents (`tools/aos_agents`). Every shipped agent starts as `candidate`. |
| **Website Studio** | Structured website editing, SEO checks and release model. Ships with a synthetic example site. |
| **CRM and Jobs** | Pipeline, follow-ups, queue, automation and a Connections layer for third-party accounts. |
| **Dashboard** | FastAPI backend and React frontend. |

Three layers are kept apart (`context/LAYERS.md`): **software** (this repository), **universal** knowledge (reusable methods)
and **company** data (yours: never in Core, never shared by the tooling). Anything unclassified is treated as private.

## What is not in Core

No company data, Business Brain content, customer records, credentials, accounts, offers, prices or production agent
release states. The originating company's website copy and service-delivery playbooks are not included. Core has no
dependency on any private repository.

## Quickstart

See [QUICKSTART.md](QUICKSTART.md): install, create an empty company, start the dashboard, run the tests.

## Alpha limitations

- **Not production hardened.** Run it on your own machine or an isolated VM, bind it to localhost, and do not expose it to the internet.
- **No live-provider verification.** Agent evidence was replayed from recorded replies; live model calls were not part of release testing. Connections to Gmail, Drive, Stripe and similar services are code paths exercised with mocks only.
- **Tests:** the Python suite has known environment failures listed in `tests/core_known_failures.json` (they also fail in the originating deployment's tree); some tests are skipped with a stated reason in `tests/core_skips.json`. The required CI check is a focused smoke suite; the full suite is informational.
- **Some tests assume paths** such as an install root of `/opt/ttros-core` or a registered website checkout, and are skipped otherwise.
- **Defaults reflect a Linux/WSL operator machine.** Windows-host and Tailscale features are optional and unsupported in this alpha.
- **Interfaces will change** before 1.0. Agent names such as `crm.fit_call_prep` and their offer vocabulary reflect the originating deployment's sales process and are examples to adapt.
- **Single-operator model.** Multi-user roles, hosted deployment and a security audit are future work.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) first; contributions are untrusted by default and pass a deterministic gate.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in public issues.

## Licence

MIT. See [LICENSE](LICENSE). Third-party components and their licences: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Built by [Time to Revenue](https://timetorevenue.com), the first operational customer of TTROS.
