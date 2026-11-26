// Read a pinned commit's text sources from Git objects only (never the working tree).
import {execFileSync} from 'node:child_process'
export const LIVE_SOURCE = '2e521be1ce7469538b0eff234860e7f97bc6c739'
const TEXT = /\.(tsx?|jsx?|mjs|cjs|css|json|html)$/
// Static HTML guides (public/guides/<name>/*.html) are documents too.
export function readCommit(repo, commit, {prefixes = ['app/', 'components/', 'content/', 'lib/', 'public/guides/']} = {}) {
 const sha = execFileSync('git', ['-C', repo, 'rev-parse', '--verify', `${commit}^{commit}`], {encoding: 'utf8'}).trim()
 if (sha !== commit) throw Error(`Pinned website commit ${commit} is not in ${repo}.`)
 const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', commit], {encoding: 'utf8', maxBuffer: 64 << 20}).trim().split('\n')
 const files = new Map()
 const wanted = listing.map(l => { const [meta, path] = l.split('\t'); return {object: meta.split(' ')[2], path} })
  .filter(e => (prefixes.some(p => e.path.startsWith(p)) || !e.path.includes('/')) && TEXT.test(e.path) && (!e.path.endsWith('.html') || e.path.startsWith('public/guides/')))
 const batch = execFileSync('git', ['-C', repo, 'cat-file', '--batch'], {input: wanted.map(e => e.object).join('\n') + '\n', maxBuffer: 256 << 20})
 let offset = 0
 for (const entry of wanted) {
  const nl = batch.indexOf(10, offset)
  const size = Number(batch.subarray(offset, nl).toString().split(' ')[2])
  files.set(entry.path, batch.subarray(nl + 1, nl + 1 + size).toString('utf8'))
  offset = nl + 1 + size + 1
 }
 return {commit: sha, files, tree: listing.map(l => l.split('\t')[1])}
}
