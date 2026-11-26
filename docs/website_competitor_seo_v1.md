# Competitor SEO planning in Websites & Digital

Implemented from `ttros-website-competitor-seo-v1.zip` (SHA-256 `fde05cc6c5a091bfdb654cf54404918c887606b16741da9857e12df0b3bd63d9`) against TTROS baseline `e20376c41660c4f6e4834d6f348c1e131ad6133e` on isolated `feature/website-competitor-seo-20261117`. Package contents and integration transformations were reviewed before application. The uploaded installer was used only for the initial scoped transformation; it is not a runtime component.

> Final targeted review: see [website_seo_integration_review_20261117.md](website_seo_integration_review_20261117.md). The follow-up tightens literal source/CTA checks and malformed-input handling; the original proof files remain historical evidence for `d74f287`.

## Working path

Websites & Digital → select registered website → Competitor SEO strategy → enter research and a real CTA → Generate SEO page briefs → choose one page → existing Prepare a website change form → existing readiness checks → existing `aos_jobs` allocation/execution → verified artifacts/preview/receipt → publication held.

Planning is deterministic and makes no model, search or connector call. The new `POST /api/website/preparations/seo-plan` route uses the existing preparation router and its existing backend access boundary; no separate authentication mechanism or public service was created. This feature does not discover evidence, prove claims or verify a source's truth. It checks supplied evidence structure and dates and flags research gaps. Keyword metrics remain unknown unless supplied with a dated source. Planned internal links are suggestions, not proof that those pages exist.

The five brief types are alternatives, competitor review, our-business versus competitor, three-way comparison and best-for-use-case. Alternatives come first. Plans are bounded to ten competitors and research to 15KB UTF-8/25 claims. Selecting a brief fills the existing request, branch, affected file, references and exact heading acceptance. It never automatically saves or queues work. Drafts can retain incomplete evidence; they cannot queue until readiness passes.

Optional `seo_strategy` travels in the existing preparation snapshot and worker brief. Existing repository identity, client scope, base commit, allowed paths, source hashes, test/build and local-only action checks still apply. Acceptance inspects the actual produced source for heading, CTA label, byline, disclosure and a dated claim's source URL. These are source-content checks, not a substitute for editorial review of rendered semantics, factual accuracy or competitor fairness. Publication remains held.

## Corrections made during integration

- Reject malformed research arrays/scalars and oversized payloads as validation errors instead of server failures. Handle malformed evidence URLs, invalid metric rows and non-finite metrics safely.
- Reject protocol-relative CTA destinations, unknown strategy versions and cross-client research attachments.
- Reset SEO research/selection on registered-project changes. Invalidate briefs when research changes; disable edits while planning; provide an explicit clear-selection control. Keep the chosen evidence when revising an order.
- Remove the assumed `/contact` destination so a verified destination must be entered.
- Repair a malformed table in the test demonstration, observed during real browser inspection; verify two cells per row.
- Initial frontend tests exposed an incomplete shared dependency cache (missing jsdom). Used the already-installed complete Company Ops dependency cache; no dependency download or production cache modification. The temporary worktree symlink is excluded from the commit and removed after validation.

## Validation and demonstration

Run from the TTROS feature worktree with the installed interpreter and the root `conftest.py` guards active:

```bash
AOS_ROOT="$PWD" TTROS_ALLOW_PAID_TESTS=0 /opt/ttros-core/.venv/bin/python -m pytest -q -s tests/test_aos_website_seo.py tests/test_website_seo_integration.py tests/test_website_readiness.py tests/test_command_centre_website.py
```

Result: **60 tests and 24 subtests passed**. Coverage includes legacy non-SEO behavior, malformed API inputs, evidence-poor draft rejection, client/scope/version guards, post-worker missing evidence, source isolation, job reuse, real site tests, artifacts, preview and receipt.

Frontend: from `dashboard/frontend`, `npm test` passes **154 tests** with `AOS_ROOT` set to this worktree, `TTROS_ALLOW_PAID_TESTS=0` and `TTROS_TEST_PYTHON=/opt/ttros-core/.venv/bin/python`. `npm run build` passes (Vite 6.4.3). A mounted React test covers planning, serialization, project-change clearing and the absence of automatic queue submission.

`tests/website_seo_demo.py` creates only a unique `/tmp/ttros-seo-zero-model-*` fixture. It is executed through guarded pytest, not through a production backend lifespan. The API plans briefs, saves an evidence-poor draft (queue returns 422), prepares one evidence-ready order, queues idempotently, executes the registered inert fixture worker through `aos_jobs`, and checks the hashed preview over the real preview API. It asserts zero model calls, unchanged source checkout, no remote and publication held. `AOS-2026-0001` in this proof belongs to the fixture's private runtime, not the production queue.

Proofs and exact logs: `proofs/website_seo_20261117/`. Includes final demo report, workflow output, receipt, backend/frontend logs, built-preview files and screenshot. The copied preview files are checked against the job artifact hashes. Browser inspection confirms the fixture label, title/H1, author, source citation and intact table. Invented content is labelled throughout; it is not a published client comparison or real competitor research.

Open `http://localhost:18117/` while the review server is running. Restart from this worktree with:

```bash
python3 -m http.server 18117 --bind 127.0.0.1 --directory proofs/website_seo_20261117/preview
```

This serves only fixture artifacts. The real production preview API was separately exercised through TestClient with sandbox CSP and artifact verification; the simple review server does not reproduce that API's security headers.

## Preserved boundaries and remaining work

No production services, queue records, registries, secrets, runtime models, deployment or external communications changed. No push or merge. The live TTROS checkout had pre-existing unrelated runtime dirt; website-related feature worktrees and the separate EXCO redesign were inspected and preserved. The EXCO redesign remains at `70dd9f4ea2fda1c0b460405f4eaeaf663589d049` with its clean feature worktree. No test uses that checkout as a fixture.

The registered EXCO website still permits its existing guide-only path. New comparison-page paths remain intentionally blocked. A real EXCO comparison needs separately approved bounded paths, researched real competitors, a named author, verified CTA and dated evidence. No blind registry/base-ref changes were made. This integration is validated locally but not active in the live Dashboard until permitted integration/deployment.

Next action: review the feature diff and fixture receipt for permitted integration. Agent token_usage is unavailable from this harness; fixture model usage is exactly zero, as recorded in its receipt.

## Changed source and test files

- `tools/aos_website_seo.py`: deterministic planner, validation, source acceptance and proposed-order builder.
- `tools/aos_website_prepare.py`: optional SEO snapshot and readiness/acceptance integration.
- `dashboard/backend/website_readiness.py`: optional request field and pure planning endpoint.
- `dashboard/frontend/src/views/WebsiteSeoPlanner.jsx`: research/brief selection panel.
- `dashboard/frontend/src/views/WebsitePreparation.jsx`: existing form integration, scope reset and revision handling.
- `dashboard/frontend/src/websitePreparationApi.js`, `websitePreparationState.js`: existing API/serializer extension.
- `tests/test_aos_website_seo.py`, `test_website_seo_integration.py`, `website_seo_demo.py`: planner and guarded real-path fixture coverage.
- `dashboard/frontend/tests/websitePreparation.test.js`, `websiteSeoPlanner.test.js`: serialization and mounted UI regression checks.
- This documentation and `proofs/website_seo_20261117/`: provenance, logs, fixture receipt, preview and screenshot.
