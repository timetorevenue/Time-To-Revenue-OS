# Dashboard connections: secure local setup

The implementation is in `feature/ttros-account-connections-20261119`, based on
`46e45ac8848997ade22d9e9d4cda500a295bbc5e`. Nothing here authorizes production
activation, account changes, publishing, a website build/deployment, or a push.
`rules/runtime_permission_policy.md` remains the execution authority.

## Local preview

From `/home/operator/ttros-worktrees/account-connections-20261119`, run
`python3 scripts/connections_preview.py`. It starts/reuses only feature-owned
loopback ports 4390 and 4391. It refuses ports belonging to another checkout and
never stops a process. The server is offline for models and uses isolated state
in `.studio-preview/state`. Dashboard dependencies and the Python environment
must already be installed; this session prepared them without changing production.

- Studio: http://127.0.0.1:4390/websites?tab=studio&site=default-site
- Connections: http://127.0.0.1:4390/settings/connections
- Existing launch workflow: http://127.0.0.1:4390/websites
- Jobs / CRM / David: `/queue`, `/crm`, `/david` on that same origin.

Use the exact `127.0.0.1` origin. `localhost` is a different browser origin and
is deliberately refused for mutations when the configured origin is 127.0.0.1.

The local-preview identity cannot initiate provider connections. To complete real
consent, configure the isolated server using the existing verified operator path,
or configure Google OIDC and sign in. Do not expose the backend publicly: the
existing Access/Tailscale ingress remains required for legacy operator APIs.
New Google member sessions are refused on legacy global CRM/Jobs/David/Studio
surfaces unless explicitly linked to an existing operator. New member drafts,
images, provider bindings and audit receipts are scoped to their membership.
Non-cookie service clients keep their current authentication contracts.

## Google identity

1. Create a Google OAuth **Web application** client. Register the exact callback
   `http://127.0.0.1:4390/api/auth/google/callback` for local verification. Add the
   test operator on the consent screen if the OAuth application is in Testing.
2. Through the runtime's secret injection mechanism, supply
   `TTROS_GOOGLE_CLIENT_ID`, `TTROS_GOOGLE_CLIENT_SECRET`, and the non-secret
   `TTROS_DASHBOARD_ORIGIN=http://127.0.0.1:4390` to the isolated backend.
   Do not paste credentials in chat, Git, screenshots, command arguments or receipts.
   Do not read or modify production credential files or restart production.
3. For any non-local origin use HTTPS and register its exact callback. Sessions
   use Secure cookies on HTTPS, HttpOnly, SameSite=Lax, an eight-hour expiry,
   random tokens hashed at rest, and server-side logout/revocation. OIDC uses a
   single-use browser-bound state, nonce, PKCE S256, pinned issuer/audience and RS256.
4. An existing operator must enter via the existing verified Access/Tailscale
   identity and choose **Link Google to this existing user**. The Google verified
   email must match that existing user. Ordinary sign-in never links by email;
   a new subject receives a separate user/workspace.

Only `openid email profile` are requested. No offline access or provider refresh
token is requested. Google ID/access tokens are discarded after verification.
Gmail and Drive need separate consent through their provider connections.

## Composio provider connections

Updated 2026-11-24. The provider panel (Vercel, GitHub, Google Drive, Gmail) uses the one
Connections Hub on the member's linked or TTROS-managed Composio Platform project. There is
no `COMPOSIO_API_KEY` application key and no tool-router session path any more.

- Connect calls the Hub's metadata-driven Connect (hosted sign-in or secure credential form)
  and reuses an existing ACTIVE Platform account without new authorization.
- Status verifies the exact connected account (ID, owner, toolkit, ACTIVE) server-side.
- Reads use `POST tools/execute/proxy` with a fixed reviewed **GET** endpoint on the
  verified Platform account; no browser- or David-supplied URL, action, account or owner.
- `read_only_reviewed=true` in `context/account_connections.json` is still required per
  provider after reviewing its consent scopes.
## Vercel and the deployed website source

Sign in at https://vercel.com/login using **Continue with Google**. A Vercel
password is neither required nor requested. Browser sign-in alone does not grant
TTROS server API access; complete the reviewed Vercel Composio connection as above.
The Dashboard exposes only a metadata GET and compares all of:

- Project: `prj_tDmcXcdOvbfW7mw5VMYt6maN4wY7`
- Deployment: `dpl_De6tgWAJDw34wk7pkFXLwxo1qanW`
- Source: `2e521be1ce7469538b0eff234860e7f97bc6c739`
- Operator-supplied source branch: `codex/enlarge-site-header`

The deployment marker was independently verified in Chromium. The supplied SHA
is recorded as operator-verified Vercel evidence, **not** falsely claimed as a
locally verified Git object. The exact commit is absent from both existing source
object stores. The website remote's Windows credential helper fails on Linux;
the runtime SSH key cannot access that repository; the existing Composio GitHub
connection gets HTTP 403 because the organization restricts OAuth apps.

An organization owner must approve read access for the existing Composio OAuth app,
or provide an authorized read-only export of that exact commit. Preserve the
current source checkout, Windows redesign and all release-candidate worktrees.
Reconcile and hash the source in an isolated checkout only after this access is
available, then implement/verify the native source adapter. No website repository
push or new Vercel deployment is part of that step.

Until then, Studio provides a **published-page capture** mode for 17 actual pages,
with the original markup and production stylesheet. Captures carry URLs,
timestamps, deployment markers and hashes. Text/image/section/design edits are
local versioned drafts, rendered against those published bytes. Scripts,
embedded frames, form submissions and publishing are disabled. This is not a
source-backed Next build. The preserved unpublished Puck release candidate and
product-launch landing page remain reachable through their explicit separate UI.
Drafts cannot currently be exported as a verified source patch for production.

## Stripe TEST connection

Keep the existing `aos_product_launch` and `aos_stripe_test` workflow. No Stripe
Connect account or approval is assumed. Future customer-owned accounts require a
server-provisioned workspace/member/account/secret-reference binding; the operator
runtime key cannot be used by another workspace. No customer secret is accepted
through the browser. Live keys are refused before any Stripe request.

1. In Stripe TEST mode, obtain a restricted test key and the exact `acct_…` ID.
   Required test permissions: account read, Products write, Prices write and
   Payment Links write. Provision only the test permissions needed by this workflow.
2. Inject `TTROS_STRIPE_TEST_KEY` (`rk_test_…` or `sk_test_…`),
   `TTROS_STRIPE_TEST_ACCOUNT`, and `TTROS_STRIPE_TEST_WEBHOOK_SECRET` through the
   runtime secret manager into the isolated backend. Do not print their values.
3. **Verify Stripe TEST connection (read only)** performs a real account GET and
   checks the account ID. This alone is not product/checkout/webhook proof.
4. Use **Prepare test launch**, then **Reconcile Stripe test checkout** in the
   existing workflow. Its existing idempotency journal creates a real test Product,
   Price and hosted test Payment Link. Retain real returned object IDs as evidence.
5. For a signed event, run Stripe CLI in test mode:
   `stripe listen --forward-to http://127.0.0.1:4391/api/website/studio/launch/webhook`.
   Inject that listener's signing secret before starting the isolated backend.
   Complete the actual hosted test checkout with Stripe's documented test payment
   details. Verify the signed, matching-account event maps to that offer's payment
   link, amount and currency, and creates the existing fulfillment job/CRM receipt.
   An unrelated synthetic `stripe trigger` event is not proof of that mapping.

The session has no injected test credentials. Therefore no real Stripe objects,
checkout or signed provider event were verified. Offline transport/signature tests
are labeled fixture tests and are not represented as provider proof.
# Current Connections hub

The operator catalogue, existing-account reuse, custom connectors, encrypted OS
credential references and David/Jobs reads are documented in
[CONNECTIONS_HUB.md](CONNECTIONS_HUB.md). The earlier sections describe the
retained Google OIDC and application-key member configuration path. Local preview
remains unprivileged by default; the new explicit `--operator-connections` mode
can reuse the matching existing Composio human account on loopback.
