# Connections hub runtime contract

Settings → Connections is served at `http://localhost:4390/settings/connections`
in the isolated account-connections worktree. The production checkout and service
are not activated by this change. `rules/runtime_permission_policy.md` remains the
sole action authority. This hub exposes named reads; existing specific adapters
retain email sends, publication, deployment, financial changes and other writes.

## Composio access

Updated 2026-11-24: TTROS uses only the Composio Platform REST API (v3.1) with the linked or
TTROS-managed project key, decrypted in memory from the existing encrypted OS credential.
The old For You CLI path and its adapter were removed. The installed `composio` CLI is used
only by operators for read-only developer inventory, never by TTROS runtime code.
## Custom connections and secret custody

Use the catalogue, a reviewed Composio auth configuration or a named authenticated
proxy operation first. The minimal custom adapter supports fixed HTTPS origins on
443, named GET endpoints, scalar query schemas and JSON response schemas. Import
accepts OpenAPI 3 JSON; remote references, templates, writes and parameterized
operations are not silently imported. Review parameterized operations manually.

DNS must resolve entirely to public unicast addresses on every read. The actual
TLS socket connects to a validated IP while verifying the original hostname.
Redirects are refused, so credentials cannot follow a redirect to another host.
Responses are bounded to 64 KiB and schema-validated before returning. Auth fields
and exact key echoes are redacted before receipts are persisted. An edit resets
verification; enabling a disabled connection requires another test for evidence.

API-key authentication accepts **references only**, never key values in the form,
connection JSON or plaintext environment-variable slots. References must match
`TTROS_CONNECTOR_SECRET_[A-Z0-9_]+`. Provision them using the existing OS credential
facility, outside this repository and outside chat:

1. Use `systemd-creds encrypt --name=TTROS_CONNECTOR_SECRET_SERVICE --with-key=host`
   with a secure input file and a private output `.cred` file. The operator owns
   this provisioning; do not print the key or put it in shell history/Git.
2. For the ordinary unprivileged backend, deliver it through the existing system
   service's `LoadCredentialEncrypted=TTROS_CONNECTOR_SECRET_SERVICE:/secure/path`
   configuration. systemd supplies a private volatile `CREDENTIALS_DIRECTORY`.
   No production service changes or restart are authorized by this document.
3. Alternatively, an already privileged credential-capable backend can use
   `TTROS_CONNECTOR_ENCRYPTED_DIRECTORY` containing private operator-owned
   `TTROS_CONNECTOR_SECRET_SERVICE.cred` files. The adapter decrypts via stdin/stdout
   in memory using systemd-creds, never to a plaintext file. Missing decryption
   authority, invalid names, unsafe permissions and expiry fail closed.
4. Assign the reference to the exact principal and host with the non-secret,
   server-provisioned `TTROS_CONNECTOR_SECRET_BINDINGS` JSON mapping, for example:
   `{"local-preview:local-operator":{"TTROS_CONNECTOR_SECRET_SERVICE":"https://api.example.com"}}`.
   Customer memberships have their own workspace/user IDs. A key assigned to one
   member/host cannot be borrowed by another, even if they know the reference.

The real custom proof is public GitHub Status, requiring no credential. API-key
redaction and encrypted-custody failure cases use isolated fixture values only;
no new real API key or provider account was provisioned for that test.

## David and Jobs

The same registry/executor (`tools/ttros_connections.py`) handles both kinds.
`/connections` lists this principal's bindings. `/connection ID OPERATION {JSON}`
executes an enabled named read with a trusted ingress context. The page's Ask David
action uses the same David implementation through a scoped endpoint, allowing
customer reads without granting access to legacy global operator conversations.
No model invocation is required for these explicit commands.

The page can submit a read through the existing `aos_jobs` allocator and executor.
The server stores an expiring scope grant tied to the exact job ID in the scoped
registry. Model arguments contain no owner authority. The worker rechecks the
grant, current membership, enabled binding, provider account and named operation.
A copied executor spec on another job, expired grant or disconnected binding fails.
There is no second queue, ID allocator, worker pool or orchestrator.

Named Composio reads can be discovered and enabled with their current provider
schema. Cross-toolkit slugs, mutation verbs, credential tools and arbitrary
code/query/proxy operations are refused. Schema/version changes require enabling
the read again. Tools needing specific write authorization keep their existing
TTROS execution adapters and policy gates. Provider reads are recorded with durable
member/workspace-scoped receipts; normal Jobs receipts reference those receipts.

## Safe local activation

Keep canonical/live services unchanged. From this feature worktree, after stopping
only a verified older **feature** backend on 4391 if necessary, run:

```sh
python3 scripts/connections_preview.py --operator-connections
```

The launcher reuses owned local services; it does not silently replace a process
or reload its environment. The exact browser origin must equal
`TTROS_DASHBOARD_ORIGIN` (default `http://localhost:4390`). API 4391 and Dashboard
4390 are loopback-only. Model calls remain disabled in the preview. Existing
provider credentials and all state remain outside Git in the original credential
facility and the ignored `.studio-preview` root. Activating on production, pushing,
publishing or restarting production remains a separate operator action.
