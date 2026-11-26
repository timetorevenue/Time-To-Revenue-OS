# Agentic OS Path Convention
> Revisit: when the canonical runtime root, Brain vault or mount policy changes. · Last touched: 2026-11-15.

- `/opt/ttros-core` is the canonical live installation.
- `AOS_ROOT` is the portable runtime root contract; code defaults from its repository location.
- Code should resolve runtime files from `AOS_ROOT` or root-relative paths.
- Authoritative mutation is Linux/POSIX-only on a Linux-native filesystem.
- `/mnt/c`, other Windows drive mounts, NTFS/DrvFS/9p/fuseblk roots, native Windows Python, and Windows APIs are unsupported for queue, package, receipt, artifact, runner, dashboard, and orchestration mutation.
- The old `/var/lib/ttros/legacy-snapshot` tree is a frozen rollback snapshot.
- The one canonical Business Brain is the separate Git vault at
  `/var/lib/ttros/brain` (9p,
  `origin` ttros-business-brain). It is outside the AOS-runtime rule above:
  `business_brain.BUSINESS_BRAIN_ROOT` and the default `brain_memory.VAULT_ROOT`
  name it, and production's ordinary writers (`brain_memory`, Shared Brain submit,
  memory filing, Drive Watcher, source intake) commit there. Do not create a
  Linux-native copy, move records, or point `TTROS_BRAIN_ROOT` at another vault
  outside an isolated fixture. Writer locks (`flock` on `.git/brain-memory.lock`)
  are coherent only among Linux processes in this WSL instance; Windows-side
  edits are caught by the writer's content-hash checks, not by the lock.
- Windows may only invoke the Linux launcher and open the Linux-hosted dashboard.
- Secrets stay outside git and are re-provisioned during migration.
- Retired Hermes executable entrypoints were removed in the bounded 2026-11-14 cleanup.
  Original launcher bytes are preserved without executable permissions under
  `archive/legacy-executable-removal-2026-11-14/`. The modified historical Hermes
  checkout and its Git history remain preserved; do not reactivate it.
  Telegram uses the existing Ask David API; explicit Codex/Claude selection stays
  governed by `rules/runtime_permission_policy.md`.
