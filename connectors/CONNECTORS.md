# TTROS Connectors

> Revisit: when connector authority or the Composio Platform integration changes. · Last touched: 2026-11-24.

Every app connection is a Composio Platform connection in the one Connections Hub
(`tools/ttros_connections.py`, Dashboard Settings → Connections). Do not build separate app
APIs, adapters or a second Composio integration.

- Connect: the Hub reads each app's auth schemes from Composio and opens hosted sign-in or the
  secure credential form. Secrets never enter chat, logs or Git.
- Use: David, Agent Forge and Jobs discover and run an app's tools through the Hub
  (`tools` / `tool` operations); services and Jobs effects use `perform_operator`.
- Authority: `rules/runtime_permission_policy.md` via `aos_authority.action_authorization`.
- Here: `gmail_draft_adapter.py` (draft-only revenue drafts, executed through the Hub),
  `gmail_draft_policy.py` (Gmail draft-only contract) and `telegram_bridge/`.

The old Composio For You CLI adapter (`composio_access_adapter.py`), its registry and the
application-key (`COMPOSIO_API_KEY`) provider path were removed on 2026-11-24 after every
caller moved to the Platform; see Git history.
