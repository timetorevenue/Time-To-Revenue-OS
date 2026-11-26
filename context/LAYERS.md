# TTROS layers: where new work goes
> Revisit: when a new top-level directory or export rule is added. · Last touched: 2026-11-26.

Every file belongs to exactly one layer. Decide at creation; unknown means private. `context/layers.json` is the map and
`python3 tools/ttros_layers.py classify PATH` / `check` apply it.

| Layer | What | Where | Shared? |
|---|---|---|---|
| 1 Software | code, dashboard, agents/packages, connectors, tests with synthetic fixtures, generic workflow definitions | existing paths (`tools/`, `dashboard/`, `agents/`, `workflows/*/workflow.md` ...) | yes, via reviewed export |
| 2 Universal intelligence | company-neutral methodology: *how* to define an ICP, write a post, qualify a lead | `universal/<domain>/<id>/METHOD.md` + `universal/manifest.json` | only when `cleared` and owner-reviewed |
| 3 Company intelligence | facts, voice, offers, prices, sites, campaigns, results, contacts, receipts | the Business Brain, `company/**`, `workspaces/**`, runtime state | never |

## Rules of thumb
1. **Build the capability once, generically; keep the company in data.** Code and methods take company facts from the Business Brain,
   `context/*.json` registries or arguments at run time. If you type the company's name, a price, an offer, a client or a URL into a
   software or universal file, stop: it belongs in layer 3.
2. **Applied versions are private.** A method is generic; its use for this company (our LinkedIn voice, our offer page, this campaign)
   lives under `company/<area>/` or in the Brain. Improve the generic method in `universal/` first, then apply it privately.
3. **Context assembly joins them at run time.** `business_brain_context.assemble_context()` returns universal methods (never counted as
   company context) next to scoped company context. An empty Brain gives `company_status="empty"`, not an empty system.
4. **Tests use synthetic data only**, inside the test or under `tests/fixtures/`. Never copy a real record, even renamed.
5. **Rights.** Add every universal file to `universal/manifest.json` (`authorship`, `licence`, `redistribution`, `owner_review`). Third-party
   research stays under `universal/_pending/` or `company/` until redistribution is established. Only `cleared` + a dated `owner_review` is exportable.
6. **Run `python3 tools/ttros_layers.py check`** before committing; it fails on unregistered universal files.

## Sharing (never automatic)
`~/ttros-core-work/tooling/ttros_core.py` exports from a committed SHA through an allowlist, scans, and produces a Core tree for review;
`import` brings reviewed Core changes back as a patch for a private branch. Nothing publishes or merges without the owner. Unclassified
material is excluded by default. See `tooling/README.md` (private).

## Fresh installation
`python3 tools/ttros_init.py init --brain DIR` creates an empty Brain, empty CRM/queue state and verifies the registries hold no company,
site or connection; `status` (also `/api/setup/status`) lists onboarding steps. Synthetic data exists only in tests or an explicit demo.
