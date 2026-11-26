// Build one saved live-editor version into the real Next.js website, in a disposable fixture.
// Usage: node build-live.mjs <request.json>. Zero models, no provider calls, no publication.
// request: {repository, commit, snapshot, version, docs:{docId: tree}, code: code layer (code.js), assets:{sha: file}, purpose, output, npmCache}
// purpose 'release' keeps the website's own next.config.ts; 'preview' only relaxes framing so the
// Dashboard can embed the isolated preview (and blocks third-party frames, forms and connections).
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import {execFileSync} from 'node:child_process'
import {readCommit} from './git-source.mjs'
import {generate} from './live-source.mjs'
import {validateTree, walk} from '../../src/studio/live/model.js'
import {applyCodeLayer} from '../../src/studio/live/code.js'
import {archive, install, build, routes} from './fixture.mjs'
import {applySitePages} from './site-pages.mjs'
import {KIT_CODES} from '../../src/studio/live/pages.js'

const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const sha = b => crypto.createHash('sha256').update(b).digest('hex')
const pristine = JSON.parse(fs.readFileSync(path.join(request.snapshot, 'pristine.json'), 'utf8'))
const model = JSON.parse(fs.readFileSync(path.join(request.snapshot, 'model.json'), 'utf8'))
if (pristine.commit !== request.commit || model.commit !== request.commit) throw Error('Snapshot and requested website commit disagree.')
const source = readCommit(request.repository, request.commit)
const verify = () => { for (const [file, digest] of Object.entries(pristine.files)) if (sha(source.files.get(file) ?? '') !== digest) throw Error(`Website source drift: ${file}`) }
verify()
const pages = request.code?.pages || {}
for (const id of Object.keys(request.docs)) if (!model.docs[id] && !pages[id]) delete request.docs[id] // a removed page leaves no file
for (const [docId, tree] of Object.entries(request.docs)) validateTree(tree, {codes: Object.fromEntries(Object.entries({...pristine.codes, ...KIT_CODES}).map(([k, c]) => [k, c.pure ? {...c, doc: docId} : c])), doc: docId})
const extensions = {}
const assetPath = ref => {
 const id = ref.slice('asset:'.length)
 const file = request.assets[id]
 if (!file) throw Error(`Missing Studio asset ${id.slice(0, 12)}`)
 extensions[id] = path.extname(file)
 return `/studio-assets/${id}${path.extname(file)}`
}
// Element documents first (created pages as new files), then the version's code layer (owner CSS,
// header behaviour, source edits), then the created pages' records, registry and sitemap entries.
const changed = applySitePages(pages, source.files, applyCodeLayer(request.code || {}, source.files, generate(pristine, source.files, request.docs, {assetPath, pages}),
 {docFiles: new Set(Object.keys(model.docs)), assetPath}), request.docs, {assetPath})
const fixture = path.join(request.output, 'site')
archive(request.repository, request.commit, fixture)
for (const [file, text] of Object.entries(changed)) { fs.mkdirSync(path.dirname(path.join(fixture, file)), {recursive: true}); fs.writeFileSync(path.join(fixture, file), text) }
const copied = []
for (const id of Object.keys(extensions)) {
 const target = path.join(fixture, 'public/studio-assets', id + extensions[id])
 fs.mkdirSync(path.dirname(target), {recursive: true})
 fs.copyFileSync(request.assets[id], target)
 if (sha(fs.readFileSync(target)) !== id) throw Error('Studio asset changed after it was stored.')
 copied.push(`public/studio-assets/${id}${extensions[id]}`)
}
if (request.purpose !== 'release') {
 // Isolated preview only: embeddable by the Dashboard; no third-party frames, scripts, forms or connections.
 const file = path.join(fixture, 'next.config.ts')
 const config = fs.readFileSync(file, 'utf8')
 const anchor = 'headers: securityHeaders,'
 if (config.split(anchor).length !== 2) throw Error('Website next.config.ts drift.')
 fs.writeFileSync(file, config.replace(anchor, `headers: [...securityHeaders.filter(h=>!['X-Frame-Options','Content-Security-Policy-Report-Only'].includes(h.key)), {key:'Content-Security-Policy',value:"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; form-action 'none'; frame-ancestors ${(request.frameAncestors || ['http://127.0.0.1:3010', 'http://localhost:3010']).join(' ')}"}],`))
 changed['next.config.ts'] = fs.readFileSync(file, 'utf8')
}
const log = fs.openSync(path.join(request.output, 'next-build.log'), 'w')
const env = {PATH: process.env.PATH, HOME: path.join(request.output, 'home'), npm_config_cache: request.npmCache || path.join(request.output, 'npm-cache'),
 npm_config_userconfig: path.join(request.output, 'npm-user.config'), npm_config_globalconfig: path.join(request.output, 'npm-global.config'), npm_config_update_notifier: 'false',
 NEXT_TELEMETRY_DISABLED: '1', GUIDE_CAPTURE_ENABLED: 'false', NEWSLETTER_CAPTURE_ENABLED: 'false', NEWSLETTER_RELAYS_ENABLED: 'false', TTROS_SUBSCRIBERS_ENABLED: 'false'}
fs.writeFileSync(env.npm_config_userconfig, ''); fs.writeFileSync(env.npm_config_globalconfig, '')
install(fixture, env, log)
let nextBuildId
if (request.purpose === 'release' && request.vercel) {
 // Production-equivalent Vercel build: `vercel build --prod` (pinned CLI, the project's Node major),
 // offline from the project's settings. Only Vercel's non-secret system variables are present;
 // runtime secrets (BREVO_API_KEY) stay on the Vercel project and are never read here.
 const dot = path.join(fixture, '.vercel')
 fs.mkdirSync(dot, {recursive: true})
 fs.writeFileSync(path.join(dot, 'project.json'), JSON.stringify(request.vercel.project, null, 2) + '\n')
 fs.writeFileSync(path.join(dot, '.env.production.local'), Object.entries(request.vercel.env).map(([k, v]) => `${k}=${JSON.stringify(String(v))}`).join('\n') + '\n')
 const venv = {...env, PATH: `${path.dirname(request.vercel.node)}:${env.PATH}`, VERCEL_TELEMETRY_DISABLED: '1'}
 try {
  execFileSync(request.vercel.node, [request.vercel.cli, 'build', '--prod', '--yes', '--global-config', path.join(request.output, 'vercel-global')], {cwd: fixture, env: venv, stdio: ['ignore', log, log], timeout: 900000})
 } finally { fs.rmSync(path.join(dot, '.env.production.local'), {force: true}) }
 nextBuildId = fs.readFileSync(path.join(fixture, '.next/BUILD_ID'), 'utf8').trim()
} else nextBuildId = build(fixture, env, log)
fs.closeSync(log)
verify()
let diff = ''
const changes = []
for (const file of Object.keys(changed).sort()) {
 const before = source.files.get(file) ?? (() => { try { return execFileSync('git', ['-C', request.repository, 'show', `${request.commit}:${file}`], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}) } catch { return null } })()
 const after = fs.readFileSync(path.join(fixture, file), 'utf8')
 changes.push({path: file, before_sha256: before === null ? null : sha(before), after_sha256: sha(after)})
 const a = path.join(request.output, `before-${changes.length}.txt`)
 fs.writeFileSync(a, before ?? '')
 let text
 try { text = execFileSync('git', ['diff', '--no-index', '--', a, path.join(fixture, file)], {encoding: 'utf8', maxBuffer: 64 << 20}) } catch (e) { if (e.status !== 1) throw e; text = e.stdout }
 diff += `\nFile: ${file}\n${text}`
}
for (const file of copied) changes.push({path: file, before_sha256: null, after_sha256: sha(fs.readFileSync(path.join(fixture, file)))})
fs.writeFileSync(path.join(request.output, 'source.diff'), diff)
const docSha = Object.fromEntries(Object.entries(request.docs).map(([id, tree]) => [id, sha(JSON.stringify(tree))]))
if (request.code && Object.keys(request.code).length) docSha['code-layer'] = sha(JSON.stringify(request.code))
const built = routes(fixture)
const result = {fixture, base: request.commit, adapter: 'live', purpose: request.purpose === 'release' ? 'release' : 'preview', build: 'passed', nextBuildId,
 builder: request.purpose === 'release' && request.vercel ? 'vercel build --prod' : 'next build',
 routes: built.app, prerendered: built.prerendered, changes, sourceDiff: diff, sourceUntouched: true, version: request.version, pageDraftSha256: docSha,
 nodes: Object.values(request.docs).reduce((n, t) => n + [...walk(t)].length, 0), modelCalls: 0}
fs.writeFileSync(path.join(request.output, 'build.json'), JSON.stringify(result, null, 2))
