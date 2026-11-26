# Agentic OS Live — Runtime Status
> Revisit: after runtime or service topology changes. · Last touched: 2026-07-17.

Runtime:
- WSL distro: AgenticOSClean
- Canonical workspace: /opt/ttros-core
- Authoritative storage: Linux-native filesystem
- Frozen rollback: /var/lib/ttros/legacy-snapshot
- Codex execution root: fixed at `/opt/ttros-core` (not caller-overridable)

Services:
- FastAPI: existing `dashboard/backend/main.py`, Linux port 8010
- Frontend: existing `dashboard/frontend`, Linux port 3010
- Runner: existing `tools/aos-orchestration-runner.py --watch`
- Lifecycle: systemd user units - `aos-backend`, `aos-frontend`,
  `aos-runner`, `aos-bridge`, `aos-example-dealership` (linger enabled, start at boot)
- Manage with: `systemctl --user start|stop|status <unit>`
- Recurring Gmail capture: `aos-gmail-capture.timer` and
  `aos-gmail-capture.service` - read-only, every 15 minutes, no model invocation
- The legacy shell launcher under `tools/` was deleted 2026-08-15. systemd is the
  only runtime authority and there is no wrapper script to run.

Ready:
- aos-codex
- aos-claude

Codex policy:
- Executable: `/home/operator/.local/npm/bin/codex`
- Linux user: `operator`
- Working directory: `/opt/ttros-core`
- Sandbox: `danger-full-access`
- Approval policy: `never`

Rules:
- Old Ubuntu-24.04 is archive only.
- Do not import old Hermes state, old vault, old sessions, old skills, ZPC, MCP, gateway, OpenRouter/glm state, or old runtime files.
- Dashboard is Antigravity-built front end.
- Clean WSL is the live runtime.
- Windows is an optional launcher/browser client only; native Windows mutation is retired.
- The superseded Downloads native-Windows storage proof tests an unsupported architecture and must not be run as acceptance evidence.
