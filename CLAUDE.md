# Claude entry — shared TTROS instructions
> Revisit: when instruction loading changes. · Last touched: 2026-11-11.

@AGENTS.md

This import supplies the authoritative repository entry without copying its
permission contract. Codex is the default implementation workbench; Claude Code is the alternate David
may select, under the same runtime permission policy. Claude browser Shared Brain
access and workstream handoffs remain supported; this file grants no authority
beyond that policy and no mandatory review role to Claude.
The pre-reset original remains in archive/clean-brain-reset-2026-11-10/CLAUDE.md.

## Shell habits
- Never run filesystem-wide `find /` or unrestricted recursive `grep -r`, especially across
  Windows mounts (`/mnt/c`, `/mnt/*`), without Casey's explicit authorisation for that scan.
- Keep searches scoped to known relevant directories, with depth limits and include/exclude
  filters; prefer the dedicated search tools and the existing indexes over raw scans.
- Run bounded commands in the foreground. Do not background a search to get around a timeout.
- A timeout must stop the launched process and its children; never leave it running detached.
- Before reporting completion, stop every unnecessary shell job this session started and
  confirm none remain.
