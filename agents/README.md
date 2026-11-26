# Department Agent Cards

These are lightweight routing and boundary cards for David.

They are not autonomous agents, runtimes, dashboards, skills, connector configs, schedulers, or activation profiles.

Department cards are routing lanes, not access restrictions; access behavior is defined only in `rules/runtime_permission_policy.md`.

Work still moves through the Agentic OS Work Queue. Codex and Claude remain workbenches, not department owners.

The cards exist to keep routing, stop conditions, allowed outputs, and receipts consistent.

Connector reads use the existing Connections registry. A TAP grant such as
`connector.read:github:profile` declares one required read; the existing
`connector.read:github` form remains supported. Factory discovery records
available bindings and missing requirements as advisory evidence. It never
embeds credentials or portable authorization. At execution, the authenticated
Connections ingress stores exact task reads with the existing Jobs scope grant;
`ctx.connector_read` intersects them with the current manifest, membership,
account ownership, access mode and `aos_authority` policy decision. Restricted
access names exact agent versions and read operations; Disabled takes effect
before the next provider request, including queued tasks. Provider sends,
publishing, deployments and payments retain their existing protected adapters.
