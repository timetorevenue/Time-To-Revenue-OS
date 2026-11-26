# TTROS Website Studio — local launch and proof

Stage 1 extends the existing Puck editor inside Websites & Digital → **Website Studio**
(a tab beside SEO Strategy, Website Changes and History; deep link
`/websites?tab=studio&site=default-site`). Puck core 0.24.1 is MIT licensed; its npm LICENSE remains installed. No
Doop source, new website repository, orchestrator or paid model integration was used.

## Launch the editor without model calls

From `/opt/ttros-core`:

```bash
TTROS_STUDIO_OFFLINE=1 PYTHONPATH=tools:dashboard/backend .venv/bin/python -m uvicorn studio_local:app --host 127.0.0.1 --port 4319
```

In a second terminal:

```bash
cd /opt/ttros-core/dashboard/frontend
TTROS_STUDIO_API_TARGET=http://127.0.0.1:4319 npm run dev -- --port 4318
```

Visit `http://127.0.0.1:4318/websites?tab=studio&site=default-site` (or Websites & Digital →
Website Studio). Vite caches are
isolated per server (`dashboard/frontend/vite.cache.js`): only the production 3010
`--strictPort` server uses `node_modules/.vite`; this dev server, `npm test` and any worktree
(including one with a symlinked `node_modules`) use `node_modules/.vite-dev-<port>-<checkout>`,
so they can no longer rewrite the dependencies the running Dashboard serves. These processes
avoid the production dashboard lifespan, job recovery and systemd changes. The
local launcher now constructs David lazily. Offline mode refuses live proposals;
browser acceptance replaces only the proposal request with a deterministic mock.
Outside offline mode the existing David routing, account selection and cost policy
remain unchanged. Validation for this increment made zero real model calls.

## Editing

- **Global Design:** typed heading/body font families (existing local Geist/Inter,
  Georgia and system fallbacks); individual H1/H2/H3/body/button typography, weights,
  responsive heading sizes, line heights and tracking; primary, secondary surface,
  accent, background, text and muted colours; Example Editorial/Forest/Ocean palettes;
  button padding/radius; borders; section spacing, content width, gap and alignment.
- **Selected styles:** select an existing section or semantic element in the canvas,
  or select a section in the list and choose its element. Supported targets are the
  whole section, H1/H2/H3, paragraphs, links/buttons, figures and FAQ summaries.
  Each appropriate property has Use Global Style / Override Locally. Reset to
  Global removes the selected target's explicit overrides. Missing properties use
  global tokens and the website's original component defaults. Local overrides
  remain independent of later global token changes.
- All nine original sections, drag ordering, plain-text inline editing and paste,
  hero emphasis, 1200px/375px viewports and Puck undo/redo are retained. Manual
  edits, saved drafts, palettes and export require zero model calls.
- **Save draft** persists a version-2 document in the original browser storage key.
  Existing version-1 drafts migrate on load without losing text or order. Invalid
  drafts fail visibly and are not overwritten automatically. Browser drafts are
  local to that browser/origin; there is no authenticated shared storage yet.
- David's bounded operations now support typed global design changes, target style
  patches/resets, text and ordering. The selected target is supplied as context.
  The same acceptance reducer validates manual results and proposals. Preview,
  text/style diff, reject, atomic accept/undo and whole-draft stale protection remain.
  No executable CSS/HTML, file paths, jobs or publication operations are accepted.

`design-schema.json` is the shared field/type/range contract for browser and backend.
The Puck root stores global design; each existing section stores target overrides.
Semantic tags are preserved. SVG path editing, arbitrary element insertion and
header/footer content editing remain outside this increment.

## Actual website renderer integration

The original canvas importer remains read-only and retains the original source
snapshot at `c58e64139af19feab78aca7cbcfbd725d1828e7d`. Stage 1 adds the missing
reverse path instead of making the website render the canvas's HTML snapshot:

1. Export site draft downloads the validated, versioned document with source HEAD
   and SHA-256 hashes of relevant source files.
2. `scripts/studio-site-fixture.mjs` validates that document and checks actual source
   revision/bytes. It refuses drift. It archives the existing website into a new
   disposable `/tmp/ttros-studio-site-*` fixture, excluding Git and secrets, and copies
   installed dependencies. It never writes into the website checkout or its caches.
3. The fixture adds a narrow `studioHome` opt-in to the original homepage's
   `EditorialPage`. A server React-tree adapter expands only the explicitly supported
   synchronous website components, verifies all original text/element destinations,
   applies escaped text/order/inline styles, and retains the original Next Link,
   Next Image, header/footer, illustration, metadata and URL behavior. FAQ JSON-LD
   is synchronized with visible FAQ edits. Unknown or drifted destinations fail.
4. The same deterministic `designCss` compiler supplies editor CSS and the fixture's
   real global stylesheet. Existing secondary editorial pages inherit those styles.
5. The actual website's `npm run build` produces the edited homepage before client
   JavaScript runs. This is not a screenshot or a browser-side DOM replacement.

Reproduce with a draft exported from the editor:

```bash
cd /opt/ttros-core
node dashboard/frontend/scripts/studio-site-fixture.mjs /path/to/site-studio-draft.json > /tmp/ttros-studio-adapter.json
STUDIO_SITE_FIXTURE=$(node -p "JSON.parse(require('fs').readFileSync('/tmp/ttros-studio-adapter.json','utf8')).fixture")
cd "$STUDIO_SITE_FIXTURE"
NEXT_TELEMETRY_DISABLED=1 npm run build
```

Then verify the actual build and run the local site:

```bash
cd /opt/ttros-core
node dashboard/frontend/scripts/studio-build-proof.mjs "$STUDIO_SITE_FIXTURE" /path/to/site-studio-draft.json
cd "$STUDIO_SITE_FIXTURE"
NEXT_TELEMETRY_DISABLED=1 npm run start -- --hostname 127.0.0.1 --port 4320
```

The adapter records the six changed/generated website paths and hashes in
`studio-adapter-receipt.json`. These changes exist only in that disposable fixture.
The source release candidate, live website registry, Jobs allocation/worker, model
routing and permission policy remain unchanged. The existing live EXCO registry
still permits only the guide page. Export does not queue, apply to the protected
checkout, publish, or grant additional permissions. Connecting this export to the
existing scoped review/Jobs flow is later work.

## Stage 1 validation (2026-11-18)

Evidence is in `proofs/website_studio_stage1_20261118/`.

- Frontend: **163 pass**, including v1 migration, invalid token/destination rejection,
  typed proposal/reset semantics, source drift and export guards.
- Backend: **47 pass + 16 subtests**, including selected-target context, typed style
  validation, out-of-scope rejection and validation before model access.
- Dashboard production build: **pass**; inherited large-editor-chunk warning remains.
- Actual Next website build: **pass**, including lint, type checks and all 58 static
  pages. The inherited Browserslist-age notice remains.
- Original browser acceptance: **8 pass** (inline formatting/persistence, section
  drag, viewports, David replay accept/reject/undo and stale protection).
- Stage 1 browser acceptance: **9 pass**, plus reload verification and desktop/mobile
  editor-versus-real-website measurements. Fonts/palettes; semantic H1/H2 styles;
  section/button overrides; reset; history; mocked David global/local changes;
  style-driven stale protection; saved order; no mobile horizontal overflow.
- `saved-browser-draft.json` was taken from the actual browser after visual edits,
  then fed unchanged to the website adapter and real Next build.
- `site-renderer-proof.json` verifies all nine rendered sections and every text
  destination, ordering, local styles, headings, original illustration/Next Image,
  canonical/CTA URLs, FAQ JSON-LD, shared CSS and unchanged source manifest.
- `renderer-browser-parity.json` records matching computed desktop (1200px) and
  mobile (375px) heading sizes/weights, body font, accent, section background,
  individual button radius, section order and document width in editor and built
  website. Reload retains version-2 text, global design and local overrides.
- Screenshots: editor desktop/mobile/button inspector and real website desktop/mobile.
- **Real model calls: 0.** Browser proposals were mocked. Harness token_usage unavailable.

The original proof at `e1533a0` and its evidence under
`proofs/website_studio_20261118/` remain preserved. Its single historical live call
is not part of Stage 1's validation or usage.

## Remaining limits

The adapter is intentionally pinned to the existing nine-section homepage and
known component structure; source edits need a reviewed adapter refresh. The editor
is still homepage-focused. Shared editorial CSS is integrated; legacy page-specific
Tailwind styles are not comprehensively normalized into Studio controls. The website
release candidate and deployed website have not adopted the adapter. There is no
one-click authenticated shared draft/review/Jobs publishing integration.

Section insertion/duplication and per-section link editing arrived in Stage 2 (below). Free
element composition, multi-page/navigation editing, Blog Studio, asset uploads, Doop imports, advanced shapes and video are not
implemented. Client permissions, durable shared history/concurrency, auditing and
commercial publishing hardening remain later increments. The prior dependency
report of 9 advisories was not reassessed here; no dependencies or licenses changed.

## Stage 1.5 — Dashboard integration, brand and connected sources (2026-11-18)

**Deterministic first.** Clicking, dragging, selecting, typing, styling, navigation, saving,
previewing, validation and source checks run in application code with zero model calls.
`POST /api/website/studio/propose` is the only Studio route that can construct David, and the
browser calls it only from the explicit **Propose changes** button. Drive, Brain and brand-kit
changes never trigger inference; there is no timer, watcher or autosave model path.

- **Dashboard:** Websites & Digital shows **Website Studio** as a first-class tab. Studio opens
  with the registered Example Company site preselected; the choice persists per browser.
- **Registry:** `context/website_studio_sites.json` names the only sources Studio may read:
  the website project from `context/website_projects.json`, the Windows reference checkout,
  verified Drive file/folder IDs, Brain pointers, site guidance files and local brand assets.
  Assets are served by registered ID only; no path parameter reaches the filesystem.
- **Sources & Branding:** Local website, Windows reference, Google Drive, Business Brain and
  Brand Guide each show Connected / Disconnected / Stale / Error, last successful sync and
  references. **Check sources** is a bounded, operator-initiated freshness check (≤24 Drive
  API calls, provenance hash comparison of the Windows tree, index-validated Brain reads).
  Drive status turns Stale after 24 hours. Missing credentials report the exact capability.
- **Drive:** the existing TTROS Drive reader (`tools/drive_watcher.DriveHTTP`, google.auth ADC,
  drive.readonly) is reused; Composio and account connections are untouched. The redesign
  folder is registered as project context, not as the asset folder; an asset folder is chosen
  by link/ID and verified (must be an active folder, not excluded). The brand kit's parent
  folder is excluded because it holds unrelated client material.
- **Brain:** `business_brain:memory/website_and_content.md`, `memory/positioning.md` and
  `decisions/DECISIONS.md` via the existing scope-gated `shared_brain_read.read`.
- **Brand kit:** Drive `1pAR0Hi83yb1jjyaHBjqniVJqROIAtwz_` (sha256 `83cb57cf…`, identical to the
  website's `brand/time-to-revenue-brand-kit.pdf`) is parsed with fixed patterns
  (`website_brand.py`, identical output under pypdf 6.14 and 6.21). Unreadable sections are
  reported for review; no model fallback exists. The proposal lists palette, fonts, type scale,
  component rules, logos, voice/messaging, nearest-compliant Studio tokens, one WCAG exception
  (brand Slate Taupe is 4.18:1 on ivory; the site's #57564f is kept) and conflicts with the
  actual CSS. Brand-kit messaging is marked superseded by the 14 Nov 2026 addendum and Brain.
- **Approval:** the proposal is not authoritative until approved (fingerprint-checked).
  Approval never restyles the draft or website. **Apply approved brand to draft** is an
  explicit, undoable edit. Owner overrides remain possible and are listed in the Brand panel.
- **One edit API:** `website_studio_edits.py` provides the validated reducer (Python twin of
  `state.js`, parity-tested), brand checks and append-only versioned server drafts with
  base-version stale protection and source-revision checks. David's proposals, manual saves and
  any worker (`POST …/operations`, operations only) use it. Off-brand David/worker operations
  are rejected server-side and blocked in review. Herbie is not a verified active actor in
  this repository; no other editing path exists.

Evidence: `proofs/website_studio_stage15_20261118/`. Receipt:
`workflows/queue_artifacts/2026-11-18_website_studio_stage15_receipt.md`.

The production backend (8010) serves the new Studio routes only after an authorized restart;
until then the Dashboard (3010) Studio editor works and Sources & Branding reports that the
Stage 1.5 backend is required. Run the proof API from this checkout as above for the full view.

## Stage 2 — Add, duplicate and manage sections (2026-11-18)

Sections are now **instances** of the real website components in `homepage.json`.

- **Stable instance IDs.** `props.id` is independent of type and position. The nine original
  instances keep their saved IDs (`Section0`…`Section8`), so Stage 1/1.5 browser drafts, server
  history, exports and accepted proposals keep resolving without rewriting. Added and duplicated
  instances get fresh IDs (`<type slug>-<6 random characters>`, e.g. `how-we-work-4evydx`),
  never reused within the page. IDs survive save, reload, versions, undo/redo and export.
- **Registry and limits** (`src/studio/section-rules.json`, shared with Python): all nine
  components can be added; Hero is exactly one (only H1, SVG IDs) and FAQ at most one (its
  JSON-LD must match one visible FAQ); at most 40 sections and 20 section operations per change.
- **Operations** (`sections` in the one proposal schema, run before edits/order/design/styles):
  `add` (type, new ID, before/after a section or at the end), `duplicate` (copies text, links and
  local styles; placed after the source by default) and `remove`. Manual editing, David and
  workers share `applyProposal` / `website_studio_edits.apply_proposal` (parity-tested). David
  may add and duplicate but never remove (refused in the browser, the proposal route and on save).
- **Links and anchors.** Each `<a href>` in a component is an editable `linkN` field. Only site
  pages (routes from the website at `c58e641`), `#anchors` on this page, `https://` (no
  credentials) and `mailto:` are accepted. The original instance keeps its component anchors
  (`#how-it-works`); copies get instance-scoped IDs (`#how-it-works-<instance id>`) and their ARIA
  references follow, so the page never has duplicate IDs. Removing a section whose anchor another
  link targets is refused with the reason.
- **Editor.** A **Sections** tab holds a visual palette (live thumbnails of every registered
  component in the current design), Add above / below the selected section or at the end,
  Duplicate, Remove, Undo and Redo, with each limit explained. New sections are selected for
  direct inline editing and inherit global design; Selected styles adds link fields and independent
  local overrides. The existing drag list and arrows move any instance. Puck's own insert,
  duplicate and delete stay disabled so every change goes through validated operations.
- **Migration.** Version-2 envelopes (browser and server) load unchanged and save as version 3;
  missing link fields take the component's original destinations. History files are never rewritten.
- **Real renderer.** The site adapter verifies each original section once as a template, then
  renders every instance from its type's template with its own text, links, styles and scoped
  anchors. FAQ JSON-LD follows the FAQ instance and is dropped if the FAQ is removed.

**Validation (worktree `~/ttros-worktrees/website-studio-sections-20261118`):** frontend 183/183
(9 new); backend Studio 86 (49 new), related suites 204 + 24 subtests; Dashboard build passes.
Browser acceptance 11/11 (`tests/websiteStudioStage2BrowserProof.mjs`); Stage 1.5 regression
13/13 and Stage 1 regressions (original, Stage 1 and reload) pass. The exported draft (13
sections: three *How we work*, two *Our work*, two *Contact*) built with the actual Next site (lint,
types, 58/58 pages) and `scripts/studio-build-proof.mjs` verified every instance's text, links,
styles, anchors and unique DOM IDs. Real model calls: 0 (proposals mocked).
Evidence: `proofs/website_studio_stage2_20261118/`.

Remaining Stage 2 limits (contrast and the Blocks rail are resolved in Stage 2.1 below): Studio
remains homepage-only; header/footer and navigation are not editable.

## Stage 2.1 — WCAG AA contrast and one section-insertion interface (2026-11-18)

- **Colour model.** `src/studio/contrast-rules.json` mirrors every colour declaration that reaches
  homepage text (homepage.css, then designCss, in cascade order). `scripts/studio-contrast-model.mjs`
  builds `contrast-model.json` from it and the real section HTML: for each text field, the chain of
  elements up to the section with their style targets and declared colours (`--check` fails if stale).
- **Checks** (`contrast.js`, Python twin `website_studio_contrast.py`, parity-tested): every visible
  text field of every instance resolves its colour and background through global tokens, local
  overrides and inheritance (a section `text` override reaches `var(--ink)`/`var(--muted)`
  descendants, not fixed site colours; backgrounds come from the nearest element that paints one;
  button appearance included). WCAG 2.x AA: 4.5:1, or 3:1 for large text (24px+, or 18.66px+ at
  700+) at the smallest rendered size. Ratios are shown floored to two decimals. Deterministic.
- **Editor.** Header warning in the Studio panel, an Accessibility tab (count badge) listing each
  failure with sample, ratio, requirement, origin and **verified alternatives** (each applied and
  re-checked: fixes the failure, adds none; brand-rule compliant ones first; global-token fixes for
  token-driven colours). Selected styles shows the selected element's contrast. Failing elements get
  a dashed outline on the canvas (editor only).
- **No silent approval.** Draft editing and saving stay allowed; every server version records its
  `contrast_failures`. Export for the site adapter requires acknowledging the exact failure set
  (reset when it changes) and records it in the export (`accessibility`). David proposals and worker
  operations that introduce failures are blocked in review and refused server-side (propose,
  operations and operation saves). Failures the page already has are listed, not hidden.
- **Sections only.** Puck's Blocks rail entry (`blocks` plugin) is replaced by a *Sections* entry
  that opens the registered Sections palette; no Puck component drawer exists.
- **Fixed on the way.** Puck inline-edit wrappers (`span[data-puck-overlay-portal]`) no longer pick up
  site `span` colours in the canvas (they showed three Outcomes items and a figure caption in gold the
  site never uses). The Selected styles field is keyed per instance and design: Puck memoizes custom
  field renders by `field.key`, so selecting another section kept the first section's element list.
- **Known finding.** The default page has one AA failure: the Hero accent line renders the global
  Accent (`#b89b63`) on ivory at 2.39:1 (needs 3:1). The protected site uses `#675638` there; Stage 1's
  designCss maps it to Accent in both the editor and the adapter. It is reported, not silently
  changed; the panel offers on-brand global Accent alternatives (e.g. Slate Taupe 4.18:1).
- Not modelled: hover/focus states, text over images, non-text contrast (icons, borders).

Evidence: `proofs/website_studio_stage21_20261118/`.

## Stage 3 — Real multi-page editing (2026-11-18)

**Hero contrast corrected.** Stage 1's designCss mapped the Hero's second headline line to the global Accent
(`#b89b63`, 2.39:1 on ivory). That mapping is removed: the line keeps the website's own `#675638` (6.39:1) in the
editor, the contrast model and the site adapter. The Accent token, every saved token and every local override are
unchanged and still checked; the brand configuration is untouched (still pending unless the owner approves it). The
default homepage has 0 contrast failures and no accessibility warnings.

**Pages.** `src/studio/pages.json` registers four permanent page IDs: `home` (`/`), `service-process`
(`/services/business-process-automation`), `service-integration` (`/services/systems-integration`) and `service-ai`
(`/services/ai-implementation`). `scripts/snapshot-services.mjs` renders each route's real `page.tsx` →
`components/ServicePage.tsx` with its `content/services.ts` entry, read-only, into `services.json` (five section
types per page, original metadata and JSON-LD) and `service-manifest.json` (source hashes, checked with the homepage
manifest before every proposal, save and fixture). The related-link lists keep their 3, 2 and 4 entries.

- A page's content may only hold its own section types (browser, Python, site adapter). Each page has its own
  browser draft key (the homepage keeps its Stage 1 key), server version sequence (homepage keeps
  `<state>/<site>/drafts`; others `pages/<page>/drafts`), Puck undo history, pending David proposal and contrast
  acknowledgement. Homepage envelopes are byte-compatible with Stage 2; service envelopes add `page`.
- **Page picker** above the editor; unsaved pages are marked. Switching keeps unsaved work and undo/redo history in
  memory and never carries text, styles or design tokens to another page. The last page reopens after a refresh.
- **Links tab**: every link classified (Studio page / website page / anchor / external), incoming links from other
  Studio pages, and **Open … in Studio** (also on a selected link). Anchors are validated on the page they point
  into (`/#solutions` from a service page is checked against the homepage).
- **SEO tab** (`seo.js`, Python twin `website_studio_seo.py`): route, document title, meta description, canonical,
  Open Graph/Twitter and JSON-LD exactly as the site renders them. On a service page the Intro label is the title
  and the Intro lead the description; WebPage, Breadcrumb and Service names/descriptions follow them; URLs,
  canonical and `@id`s never change. Empty/oversized values are errors that block export; length advice is a warning.
  Cross-page mismatches (homepage link text, homepage OfferCatalog and `/services` still using `content/services.ts`
  names) are reported, never rewritten. Each saved service version records its SEO summary.
- **David** receives only the selected page: its sections, ID, route, SEO, website commit, base draft version
  (stale versions are refused with 409 before any model call), approved brand context and Brain references.
  Proposals addressing another page's sections are refused. Preview, accept/reject, undo and stale protection as before.
- **Site adapter.** One fixture takes any mix of page drafts. Service routes render ServicePage through the same
  verified-template adapter; their `metadata` and JSON-LD come from `pageSeo`. The homepage design stays site-wide;
  each service page's design is appended to `globals.css` scoped to `main[data-studio-page=…]` with `:where()` (no
  specificity change, no leakage across client navigation).

```bash
node dashboard/frontend/scripts/studio-site-fixture.mjs export-home.json export-service-process.json export-service-integration.json export-service-ai.json > adapter.json
(cd "$FIXTURE" && NEXT_TELEMETRY_DISABLED=1 npm run build)
node dashboard/frontend/scripts/studio-build-proof.mjs "$FIXTURE" export-*.json
```

**Fidelity fixes (identical to the original site at default tokens):** service lead paragraphs keep the site's fluid
size; section labels keep 0.875em/630/1.35 (the service name was rendered as body text); the contact band keeps its
own padding. Remaining default differences vs the untouched site are Stage 1 token mappings: contact-button fill
(Secondary `#ece7dc` vs site `#e0c899`), button tracking (0 vs −0.005em) and padding (12px vs 0.78rem), `text-align:
left` vs `start`.

**Validation:** frontend 205/205; backend Studio 107; related suites 118 + 24 subtests; Dashboard build passes. Browser
11/11 (`tests/websiteStudioStage3BrowserProof.mjs`), model = rendered canvas on all four pages. Actual Next build of the
four browser exports: lint/types clean, 58/58; build proof and `tests/websiteStudioStage3SiteProof.mjs` (navigation via
the built site's own links, axe-core WCAG 2.x A/AA, editor = built page for 378 computed properties). The one axe
finding (`target-size` on the footer email link) is present on the untouched site. Regressions: Stage 1 (8, 9, reload),
1.5 13/13, 2 11/11, 2.1 8/8 (expectations updated for the corrected Hero). Real model calls: 0.
Evidence: `proofs/website_studio_stage3_20261118/`.

**Limits.** Header, footer and navigation are not editable; a page's design tokens also reach its header/footer on the
built site (e.g. the wordmark takes the page heading font). Pending proposals and unsaved work live in the tab's
memory (saved drafts persist). `/services`, other service-like pages and blog/resources are not Studio pages. The
website release candidate has not adopted the adapter; publication remains held.

## Stage 4 — Shared site (2026-11-18)

**Shared site** is a fifth editing scope, stored once alongside the four page drafts. Its two
required elements expose the actual header/footer fields, registered logo choices, main navigation,
Learning and Legal rows, global design and selected element styles. Main navigation is one array
with stable row IDs: desktop header, mobile header and footer Explore all render it. Every ordinary
edit, validation, save, history and export remains deterministic. Only explicit David proposals
invoke a model; tests mock that boundary.

Shared site has its own browser storage key and append-only server history under `shared/drafts`.
It uses the existing proposal preview/reject/accept/undo and version guards. Invalid destinations,
missing anchors, unregistered logos, malformed email addresses and duplicate row IDs/destinations
are refused. Main navigation accepts registered routes and anchors, rather than executable or
arbitrary asset URLs. Logo uploads and a Drive asset-folder choice are outside this increment.

Shared design supplies global defaults. Page designs compile into their own `main[data-studio-page]`,
with original selector specificity preserved; no service-page token or selector reaches `:root`,
`body`, header or footer. Legacy homepage global design seeds the first shared draft without
rewriting old records. Legacy per-page differences and explicit `pageStyles` stay as owner
overrides; **Use global page styles** clears only that page's token overrides. Selected component
styles stay independent. Approved-brand application uses the same reducer.

The Links panel shows incoming dependencies and optional destination-matching label updates;
Shared elements offers the same review for shared rows. Preview/reject/accept is explicit and
undoable, and stale reviews cannot overwrite newer labels. URLs, prose, source-owned `/services`
names and homepage OfferCatalog entries are never renamed silently. Those source dependencies
remain visible as warnings.

Page exports include the shared envelope. The fixture adapter checks that shared envelopes agree,
then binds the values into the **original** Header and Footer source components in the disposable
fixture. Header React state, Next links, active links and Escape/focus logic stay in their original
component. Page content still renders through the real Editorial/ServicePage adapters. Exporting
Shared site separately is also supported; multiple page exports must come from the same shared
revision. Existing exports without a shared envelope retain their legacy homepage-global behavior.

Default button spacing, tracking, contact colour and transparent borders match website CSS;
explicit global/local overrides remain supported. The footer email link has a 24px minimum target
height. Shared text contrast is checked too; David proposals cannot introduce failures in either
shared elements or inheriting Studio pages. Export checks shared acknowledgements as well as the
page's own acknowledgement. The contrast model excludes hover/focus, images and non-text contrast;
the final built pages are additionally audited with axe.

Evidence and integration readiness: `proofs/website_studio_stage4_20261118/README.md`.
All validation fixtures/dependencies/caches are local to the development worktree. No publication,
production restart, push, merge or integration is part of this procedure. Brand-guide approval and
Drive asset-folder selection remain pending.

## Stage 4 usability repair — local preview and Hero graphic

The isolated preview is now left running at `http://127.0.0.1:4350/websites?tab=studio&site=default-site`
and the actual built Next site at `http://127.0.0.1:4361`. From this worktree, run
`python3 scripts/website_studio_preview.py` to start/reuse its local API, Vite and Next processes.
The helper refuses ports owned by another checkout, checks the API's offline mode and never calls
systemd. Logs and saved drafts live in ignored `.studio-preview/`. David is disabled in this preview.
Production ports 3010/8010 and the unrelated Next site on 3038 are not controlled by this helper.

Click the dark workflow graphic (including its padding or workflow steps) to select the native
Puck `WorkflowFigure` child. Clicking the surrounding Hero selects `Section0`. The native Puck
Outline exposes **Hero → Workflow graphic** as keyboard-accessible buttons; Enter selects the
child. **Select parent Hero** returns to the parent. There is no graphic pointer interceptor or
separate nested selection state. The inspector uses `Puck.Fields`, including custom typed graphic
and style fields that update Puck's own component data/history.

`nested-schema.json` registers the Hero `workflow` slot, allowed component types, cardinality and
legacy destinations. Generic browser/backend walkers validate nested IDs, fields, budgets and
exactly-one graphic. The Puck child is inline and supplies `puck.dragRef` to its real figure; drag,
insert, duplicate and delete remain restricted. Other registered nested source components can use
the same slot, migration, validation, inspector, proposal and source-renderer registry contracts.

Controls: responsive width (25–100%), horizontal/vertical alignment in the original grid, original
SVG signal colour, caption/eyebrow/step text, local raster replacement, image description and fit.
Existing figure/element style controls remain available. Local PNG/JPEG/WebP imports are bounded
to 256 KiB and 4096 × 4096 pixels in the picker, validated by type/signature/size and embedded in the
draft. Descriptions are required for replacements. No Drive selection, upload service or AI call
is needed. Restoring the original graphic preserves caption edits and is undoable. An in-flight
file read cannot overwrite an intervening edit or a different selection.

Save persists a version-4 native tree in browser and independent server history. Legacy v2/v3
flat graphic properties, caption fields and figure overrides migrate on read into one child with
a deterministic ID; native trees retain their IDs. No historical file or browser draft is rewritten
on load. Saving explicitly creates the next server version. **Saved draft version → Load selected
version** loads historical drafts with undo; **Load latest version** opens the newest saved draft.
Legacy proposal text/style destinations remain aliases to the child, without duplicate state.
David may address the child's ID and supported typed properties; normal edits make no AI calls.

Export contains the canonical slot tree, image bytes, properties, shared site data and existing
source manifest. The website adapter identifies the actual imported `WorkflowFigure` function,
verifies the original source tree and binds the native child's properties to that source output.
The canvas and original Next component use the same visual renderer. The protected website
checkout is never edited.

To rebuild from UI exports, set `TTROS_STUDIO_FIXTURE_ROOT="$PWD/.studio-preview"` and run the
existing `dashboard/frontend/scripts/studio-site-fixture.mjs` with the exported page files. Build
the returned fixture using `NEXT_TELEMETRY_DISABLED=1 npm run build`, put its absolute path in
`.studio-preview/fixture-path`, then run `python3 scripts/website_studio_preview.py --refresh-site`.
That explicit flag switches only the helper's recorded development Next listener to the selected
built fixture; it does not restart TTROS services. This synchronization is deterministic and
operator-triggered. Editing does not silently rebuild or publish the site.

Evidence/readiness: `proofs/website_studio_native_20261119/README.md`. Free SVG path/node editing,
arbitrary composition and asset-library management remain outside this focused repair. Brand
approval and the Drive asset-folder selection remain pending.
