# Free guide capture: local integration

This feature connects the existing Example Company website guide to TTROS CRM and
`aos_jobs`. The public site route uses a server-side token to call the private
TTROS API. The API pins the visitor to the global scope, creates a first-touch
contact and opportunity, gives back the existing guide URL, and queues a
deterministic follow-up draft job only when the visitor explicitly opts in.
The job uses `GmailDraftAdapter.create_draft`; there is no send path or model call.

Set the same non-public `TTROS_LEAD_MAGNET_CAPTURE_TOKEN` on the TTROS backend
and the website server. Set `TTROS_LEAD_MAGNET_BACKEND_URL` on the website server
to the backend's private URL. Neither setting belongs in `NEXT_PUBLIC_*`.
Keep the backend capture route behind the website server; it rejects missing or
incorrect tokens. The guide asset itself remains publicly readable.

For a local no-connector demonstration, start
`tests/lead_magnet_http_demo.py` with an isolated temporary root and port. It
serves the real API with its production lifespan disabled, so David and the
connector runner do not start. Start the built site with the local backend URL
and the same fixture token. POST to `/api/guide-capture`, then inspect
`/api/crm/home` and `/api/crm/overview` on the fixture API. Execute the queued
job with an injected fake `GmailDraftAdapter` as in `tests/test_lead_magnet.py`.
This proves routing and persistence without a provider call or external send.

If a CRM step fails, repeating the capture repairs the remaining steps using
stable keys. The adapter records intent before a provider call; an uncertain
provider result fails the job for manual review and is never blindly retried.
Later form submissions can revoke follow-up consent. They cannot silently
reactivate consent that was previously declined or revoked.
