export const SCOPED_PERMISSION_HEADER = `SCOPED LOCAL TASK
Read AGENTS.md, CODEX.md and rules/runtime_permission_policy.md. Follow the current task's scope and restrictions.
Complete authorized local work, validate, and return a compact closeout. Preserve unrelated changes. Never push.`

export const CODEX_LINUX_LAUNCH = `cd "${'${AOS_ROOT:-/opt/ttros-core}'}"
export PATH="$HOME/.local/bin:$HOME/.local/npm/bin:$HOME/.composio:$PATH"
command -v codex
codex --version
codex '<TASK>'`

export const CLAUDE_LINUX_LAUNCH = `cd "${'${AOS_ROOT:-/opt/ttros-core}'}"
export PATH="$HOME/.local/bin:$HOME/.local/npm/bin:$HOME/.composio:$PATH"
command -v claude
claude --version
claude --permission-mode acceptEdits`

export const launcherPrompt = target => `${SCOPED_PERMISSION_HEADER}\n\n${target === 'codex' ? CODEX_LINUX_LAUNCH : CLAUDE_LINUX_LAUNCH}`
