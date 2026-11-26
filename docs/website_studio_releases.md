# Website Studio — exact-version releases, publication and rollback
> Revisit: when the Vercel project, its credential, the deployment API or the permission policy changes. · Last touched: 2026-11-22.

Studio publishes only the exact build it saved, built and validated. Nothing is rebuilt at
publication time. Every release operation is deterministic: no model is ever called.

## Flow

1. **Saved drafts.** Owners save versioned server drafts (append-only, `website_studio_edits.Drafts`).
2. **Release build.** In the editor, *Build release candidate* builds the saved homepage, Shared site
   and saved service pages at the pinned website commit, using the website's own `next.config.ts`.
   *Build saved real-site preview* remains the embeddable preview build; it cannot become a release.
   `website_studio_builds` then health-checks the running build on loopback, packages it once as a
   byte-reproducible artifact (`website_studio_artifacts`: source as built plus `.next`, without
   dependencies, caches or traces) and writes `build-record.json`.
3. **Immutable release.** *Releases → Create release from this build* re-verifies the artifact checksum,
   each saved draft record's sha256 and the source pin, then writes `releases/<rel_id>/` once, read-only:
   `release.json` (with `record_sha256`), `artifact.tar` and `evidence/`. The id is derived from the
   content, so the same build always gives the same release.
4. **Publish.** Choose a target, inspect the release and its comparison with what that target serves,
   then *Request publication*. The server re-verifies everything and returns the exact command. Type it
   and confirm. The operation stages one immutable deployment, health-checks it on its own URL, moves the
   production alias atomically, re-checks production identity (deployment, build id, artifact and file
   manifest) and health, and otherwise restores the previous alias.
5. **Rollback.** Any release the target has served successfully before can be selected. Rollback
   re-aliases that release's existing deployment after re-verifying its files. Nothing is rebuilt.

State lives in the Studio state folder (`results/website/studio/<site>/releases/`): releases, `operations/`
(status by step), write-once `receipts/` and a hash-chained `ledger.jsonl`.

## Refusals

Publication is refused when the artifact or record changed or is missing, build validation did not
pass, a saved draft changed, the source pin is stale, the target is blocked, or the operator compared
against a state that is no longer published. Rollback is also refused for a release that the target
never served, and a deployed copy whose files changed is never re-promoted. The exact same request
again returns the first operation and deploys nothing. Publishing the release already served records a
no-op. One operation runs per target at a time. An operation stopped by a crash is closed as
`interrupted` on the next request.

## Authorization

The runtime permission policy requires Casey's explicit command naming the action and target. Publish
and rollback therefore need all of these:

- the verified Dashboard operator identity (Cloudflare Access JWT or Tailscale Serve identity in the
  Remote PC allowlist; `remote_pc.require_operator`). Local preview and workspace members are refused;
- a same-origin Dashboard request (CSRF);
- a short-lived, single-use confirmation from *Request publication*, bound to the exact digest:
  action, release, artifact, manifest, record, target and the published state compared against;
- the typed command, e.g. `PUBLISH rel_… TO <target>` or `ROLLBACK <target> TO rel_…`, which
  `aos_authority.action_authorization` checks for `WEBSITE_PUBLISH` / `WEBSITE_ROLLBACK`.

## Targets

- **`vercel-production`** (registered in `context/website_studio_sites.json`: project, team, production
  domains): core-site.example on Vercel, implemented by `website_studio_vercel` (REST client) and
  `VercelTarget`. Ready when the pinned tooling exists, the private token file
  (`TTROS_VERCEL_TOKEN_FILE`, default `~/.config/ttros-vercel-deploy.token`, mode 600) can read the
  project, and the production domains are verified domains of that project.
- **Isolated test targets** are configured only by an explicit file named in
  `TTROS_STUDIO_RELEASE_TARGETS` (provider `isolated`, outside the production checkout). They follow
  Vercel's model: immutable deployments in content-addressed folders, an atomic production pointer and a
  stable loopback production endpoint. `server: next` runs `next start` with dependencies installed
  once per exact lockfile (`npm ci --ignore-scripts`; `next start` needs TypeScript to load `next.config.ts`). `faults.json` injects test failures.

## Vercel production pipeline (2026-11-22)

1. **Build.** A live-editor release build runs `vercel build --prod` (pinned Vercel CLI 53.3.2 under
   Node 24.22.2, matching the project's `nodejs24.x` runtime) offline from the project's settings read
   through the API. Only Vercel's non-secret system variables are present; `BREVO_API_KEY` stays a
   sensitive runtime variable on the project (Vercel attaches it to the functions). The build records
   `studio-vercel-output.json`: every file `vercel deploy --prebuilt` uploads (`.vercel/output/**` and
   the dependency files the functions map), with sha1/sha256/size and the artifact's normalized modes.
   The artifact keeps the Build Output's internal function links and refuses any other link or an
   environment file.
2. **Stage.** Publication extracts and re-verifies the artifact, reinstalls dependencies from its exact
   lockfile, requires the upload set to equal the recorded one file for file, uploads it
   (content-addressed) and creates one production-target deployment with `autoAssignCustomDomains:
   false`, so only its immutable URL serves it. Publishing the same release again reuses it.
3. **Check the staged URL.** Build-identity health on every page; functional checks (security headers,
   the booking webhook rejecting an empty POST before any Brevo call, robots, sitemap, the static guide,
   404, SEO parity with the validated build on every route); and a browser check
   (`scripts/studio-live/deploy-check.mjs`: navigation, FAQ accordion, Tally booking embed, blog,
   guide, mobile menu, no page errors). Nothing reaches production unless all pass.
4. **Promote** that same deployment (`/v10/projects/{id}/promote/{deployment}`), wait until every
   production domain serves its build (an apex that redirects to www counts through www), then repeat
   health and functional checks on production and verify identity (deployment, artifact and upload
   digest in its meta, and the uploaded file list). Any failure once promotion began restores the
   previous deployment.
5. **Roll back** by promoting an earlier deployment again: a Studio release, or the pre-Studio Git
   deployment a Studio publication replaced (`ROLLBACK vercel-production TO dpl_…`). Nothing is rebuilt.

The Vercel CLI is only used for the offline build: the provisioned token is project-scoped and cannot
read the user/team endpoints its deploy and promote commands require, so TTROS calls the REST API.

## First production publication and rollback (2026-11-22)

Under Casey's explicit instruction, through the Dashboard (evidence `proofs/website_studio_production_20261122/`):
release `rel_e6ce122e6a17fecf72b9` (build `a78lrnd13NdkLUr9kGX7G`) staged as
`dpl_FjH7H4u1hLyAjyJdcvxqVnFFyAvQ`, published by `op_cf1b2bc9939194b4_3` (staged and production checks
15/15, production health 46/46, identity matched), then rolled back to the original
`dpl_De6tgWAJDw34wk7pkFXLwxo1qanW` by `op_070baeaf584729ee_1` (public site identical to the pristine
source on 32/32 routes). Earlier attempts and the defects they exposed are listed in the receipt.
