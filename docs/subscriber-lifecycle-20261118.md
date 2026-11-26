# TTROS subscriber lifecycle — local integration contract
> Revisit: before consent-copy approval, transport wiring or runtime activation.

Implemented locally on `feature/crm-subscriber-lifecycle-20261118`, from `3936f9ddce309669f686326ddbbc0c2a95eae91a`. No provider, website or live runtime was modified. This is an implementation receipt and integration contract, not activation authorization.

Validated implementation commit: `249a34303754672c0aff83cdd00f5012ee4323b9`. Exact test commands and outputs: `proofs/crm-subscriber-lifecycle-20261118/`.

## Context and authority

Read the locally mirrored Shared Brain `example-business-crm-20260101` v521 and its exact linked artifact, `artifact:example-business-crm-20260101/example-growth-plan-2026-01-01.md`, from the existing Memory Exchange. The Shared Brain connector is not exposed in this session; no checkpoint write is claimed. Read current AGENTS/CODEX, paths and runtime permission policy. Current code and runtime take precedence over dated contextual claims.

The canonical live checkout remains on `main` at the baseline, with its unrelated tracked receipt and untracked files preserved. Implementation used the **existing clean TTROS integration worktree** at the same baseline, switching it to a new feature branch; the prior integration branch is retained. No new clone, CRM, queue, Dashboard, Brain, orchestrator or model execution. No inspection of protected client records, credentials, environment files or provider authentication state.

## Implemented behavior

`tools/aos_subscribers.py` extends the existing CRM event writer and projection. There is no separate subscription database. Events are appended through `aos_crm.record_event`, retaining scope, actor, time, evidence references, request digest and stable idempotency identity. Newsletter requests record the exact notice text/version and explicit boolean consent; guide follow-up permission is never converted into newsletter permission.

Newsletter states are pending, confirmed, expired and suppressed. Email ownership, requested consent, effective marketing permission and delivery status are separate fields. A verified guide identity can still have **no newsletter consent**. Fifteen-minute random verification tokens are stored only as SHA-256 digests. Concurrent confirmations create one verification event; subsequent uses return the same harmless confirmation status without another state transition. Expired and rotated verification tokens cannot grant permission. Scope and consent notice are validated before mutation.

Unsubscribe is durable and idempotent. Old unsubscribe tokens remain usable after pending-token rotation. Unsubscribe, hard bounce, soft bounce, complaint, invalid address and provider blocking prevent marketing. All suppression reasons remain in history; no subsequent delivery or form submission clears them. Soft bounces are conservatively held until an explicitly reviewed recovery policy exists. There is deliberately no automatic resubscription or suppression-reset endpoint.

Authenticated callbacks are correlated with recipient and recorded Brevo message ID. Unknown recipients and uncorrelated delivered events are refused. A suppression callback arriving before its send receipt is durably quarantined and sets a delivery-review hold, rather than being lost. Later receipts/delivery do not automatically clear that hold. Stale delivered callbacks are audited without overwriting a newer pending request. Supported native callback evidence (provider ID, event time/date, recipient, message ID and event name) is retained without unrelated provider metadata.

The CRM audit reader now fails closed on malformed event lines instead of silently dropping possible consent/suppression evidence. Recovery must preserve and repair the original audit; never delete a damaged line merely to resume sends.

## Subscribers and opportunities

New guide captures reuse the existing scoped contact identity, record trusted website guide-verification evidence and link their existing draft jobs to the **contact**. They no longer create an outreach opportunity or sales next-action cadence. Return field `opportunity_id` is null; the existing website only consumes guide URL and follow-up choice, and its unchanged integration tests pass.

Historical `opp:guide-*` records with the original guide offer, outreach/identified status and no interactions remain addressable with their history. They are labelled `guide_interest` and excluded from sales counts, attention and pipeline summaries. Historical records with substantive sales progress are retained in pipeline. No historical events or prospect-ledger rows are rewritten. Creating a genuine sales opportunity still uses the existing explicit CRM workflow and evidence; subscriber confirmation never qualifies a sale.

Existing guide draft jobs recheck verified ownership, current follow-up consent, suppression, delivery holds and scope at execution. They remain review-only through the existing Gmail draft adapter. No newsletter send job, model or runner is created by a subscription request or confirmation. David can read the same CRM summary; no separate assistant route or memory authority was added.

The existing Dashboard shows confirmed/pending/suppressed/review-held counts, a Subscribers directory, ownership/consent/delivery state, and audit events on contact records. Guide-only contacts remain in Contacts. Subscriber directory URLs survive reload. No send or activation controls were added.

## Private HTTP contract — disabled by default

All new endpoints require exact `TTROS_SUBSCRIBERS_ENABLED=true` in the **serving process**, plus authentication. No such production configuration was changed. There are no GET confirmation/unsubscribe effects. Public callers cannot select a client scope: HTTP handlers pin global; internal module calls continue using the existing registry for isolated client scopes.

Website relay uses the existing server-only `x-ttros-capture-token` / `TTROS_LEAD_MAGNET_CAPTURE_TOKEN`. The provider route instead requires `x-ttros-brevo-token` / `TTROS_BREVO_WEBHOOK_TOKEN`; a website token cannot authorize provider callbacks. Generic `/api/crm/events` cannot forge subscription events.

| POST route | Input / result |
|---|---|
| `/api/subscribers/request` | `name`, `email`, strict boolean `consent: true`, `notice_version: newsletter-2026-11-18`, unique stable `request_id`. Returns contact, pending state and one-time transport envelope; response is `Cache-Control: no-store`. Extra fields including client scope refused. |
| `/api/subscribers/confirm` | `token`. Confirms the pending newsletter request, not a sales opportunity. |
| `/api/subscribers/unsubscribe` | `token`. Suppresses permission; safe repeated POST. Must be reached through the protected website relay without exposing its server credential. |
| `/api/subscribers/unsubscribe-link` | `contact_id`, stable `request_id`. Prepares a one-time unsubscribe URL for an eligible subscriber’s future approved draft, never sends. Older links remain valid. Response is no-store; repeat returns no new link. |
| `/api/subscribers/delivery` | `contact_id`, original `request_id`, `status` (`accepted`, `delivered`, `failed`, `unknown`), `message_id`, stable `receipt_id`. Accepted/delivered require a provider ID. Record only real observed outcomes. |
| `/api/subscribers/brevo-events` | Native transactional fields `event`, `message-id`, `email`, `id`, optional `ts_event`/`date`. Receipt fingerprint deduplicates retries. Supported: delivered, hard/soft bounce, spam, unsubscribed, blocked and invalid. Both native snake-case and hardBounce/softBounce aliases are handled. |

Tokens in an envelope are confidential bearer material: never log, commit, expose them in query strings or add them to analytics. Only digests persist in CRM. The request retry returns status with **no new envelope**, preventing automatic duplicate delivery. If the original response was lost or a provider outcome is unknown, do not automatically resend. Inspect the transport receipt; a new explicit request ID rotates pending verification. Confirmation, unsubscribe and provider retries are idempotent; request-ID content conflicts fail closed through the CRM writer.

## Existing Brevo transport integration — still held

This implementation makes **no HTTP call to Brevo** and contains no send API. `brevo_verification_payload(envelope, sender=approved_sender)` is a pure payload builder for the existing website Brevo transactional adapter (`POST /v3/smtp/email`), not another transport. Its notice is newsletter-specific; the existing guide verification wording is insufficient for this purpose.

Before any activation, the website owner must separately wire:

1. An optional, unselected newsletter checkbox displaying the exact approved notice; post only an affirmative explicit choice to `/api/subscribers/request`. Keep immediate guide access independent.
2. The one-time envelope into the **existing** Brevo sender, after approval. Use the returned token for a newsletter-specific confirmation POST, not the old guide verification assertion. The payload builder currently names `/get-guide/verify#newsletter=TOKEN` and `/get-guide/unsubscribe#TOKEN`. **These newsletter handlers are not implemented in the website and the URLs must not be published until they are.** No website file was edited here.
3. Record actual Brevo acceptance `messageId` as the matching TTROS delivery receipt; separately record failed/unknown outcomes. Delivery alone never confirms ownership. Do not add a second provider/list as a CRM authority.
4. Relay confirmation and unsubscribe POSTs with fixed destinations, existing machine authentication, no redirect following, body/rate limits, no raw-token logging, and generic public errors. Preserve existing website abuse protections. The current capture-only Cloudflare path policy will need separately authorized narrow routes for these endpoints; no broad Dashboard access or bypass.
5. Configure the existing Brevo transactional webhook with its dedicated approved authentication header. Correlate message IDs and test rejection of missing/wrong credentials, unknown recipients and stale callbacks. No webhook or provider-account change was performed here.

The initial envelope includes the unsubscribe token so verification mail can offer cancellation immediately. Future approved recurring-message preparation can use `issue_unsubscribe` / `/api/subscribers/unsubscribe-link` to mint a fresh link, retaining every older link’s revocation capability. Repeated preparation IDs never mint duplicate material. This patch is **not** a bulk newsletter sender or campaign implementation. CRM stores only token hashes and never recovers raw tokens for later mail.

Official contracts consulted read-only: [Brevo transactional webhook events](https://developers.brevo.com/docs/transactional-webhooks), [secured webhook calls](https://developers.brevo.com/docs/secured-webhooks), and [transactional email](https://developers.brevo.com/docs/send-a-transactional-email). These describe provider behavior, not verified account configuration or successful delivery.

## Validation and release boundary

Evidence lives in `proofs/crm-subscriber-lifecycle-20261118/`. Focused backend suite: 48 passed plus 40 subtests. Mounted React/real fixture API acceptance: two tests passed, including pending → confirmed → unsubscribed and unchanged sales counts. Dashboard production build passed. Existing **unchanged website** capture-security suite: eight passed against this worktree, isolated Redis and invented CRM root. Provider fetches are mocked; no lifespan, David, real queue execution, real mailbox or production dataset was started. Python compilation and `git diff --check` passed. Model calls: zero; harness token_usage unavailable.

Fixtures cover explicit/false/coerced consent, wrong notice, auth/disabled endpoints, scope isolation, pending/confirmed/expiry/rotation, twelve concurrent confirmations, duplicate contacts, malformed tokens/audit, unsubscribe replay, suppression before existing draft execution, all supported bounce/complaint variants, provider correlation, stale delivery, receipt-before/after-callback ordering, partial durable writes, lost response and uncertain delivery. Existing sales/follow-up tests remain green.

No data migration is required. Keep capture and sending disabled before an eventual rollback: older code does not understand the new suppression events. Integration/deployment, sender verification, real webhook delivery, approved consent wording, processor/retention disclosure, and a specifically authorized owned-mailbox acceptance test remain release requirements. No live email, push, deployment, provider change, paid fallback or live activation was performed. Existing website branches, previews and unrelated TTROS dirt remain intact.
