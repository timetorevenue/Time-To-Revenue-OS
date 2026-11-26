// Read-only materialisation of one committed website revision for the snapshot scripts.
// `git archive <commit>` is extracted into a fresh directory outside every Git checkout: the
// registered website checkout, its index, refs and working tree are never touched.
// Revisit when the snapshot scripts need files outside SOURCE_ENTRIES. Last touched: 2026-11-21.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
const SOURCE_ENTRIES = ['app', 'components', 'content', 'lib', 'public']
// Parses `[source] [--commit <sha>]`. Without --commit the snapshot reads the clean working tree.
export function snapshotSource(argv, fallback = '/home/operator/ttros-projects/default-site') {
  const args = [...argv], at = args.indexOf('--commit')
  const commit = at >= 0 ? args.splice(at, 2)[1] : null
  const repository = args[0] || fallback
  if (!commit) {
    if (execFileSync('git', ['-C', repository, 'status', '--porcelain']).toString().trim()) throw Error('Website checkout has uncommitted changes; snapshot only a committed revision.')
    return { repository, root: repository, commit: execFileSync('git', ['-C', repository, 'rev-parse', 'HEAD']).toString().trim() }
  }
  const full = execFileSync('git', ['-C', repository, 'rev-parse', '--verify', `${commit}^{commit}`]).toString().trim()
  const root = fs.mkdtempSync(path.join(process.env.TTROS_STUDIO_SNAPSHOT_ROOT || os.tmpdir(), 'ttros-studio-snapshot-'))
  const archive = path.join(root, 'source.tar')
  execFileSync('git', ['-C', repository, 'archive', '--format=tar', '--output', archive, full, ...SOURCE_ENTRIES])
  execFileSync('tar', ['-x', '-C', root, '-f', archive])
  fs.rmSync(archive)
  return { repository, root, commit: full }
}
