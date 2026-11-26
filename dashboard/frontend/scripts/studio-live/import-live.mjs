// Import the live website source into a Studio snapshot. Explicit operator action; zero models.
// Usage: node import-live.mjs <repository> <commit> <output-dir> [--npm-cache <dir>]
// Writes (atomically, into a new directory): model.json (the editor model), pristine.json (source
// spans and code, server/build only), capture.json (each route's rendered attributes of code-bound
// elements and the HTML of opaque functional parts), canvas/site.css + canvas/media (the built
// site's stylesheet and fonts for the editor canvas), assets.json (public/ files), parity.json
// (pristine build vs fully regenerated build, route by route) and receipt.json.
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import {execFileSync} from 'node:child_process'
import {JSDOM} from 'jsdom'
import {readCommit} from './git-source.mjs'
import {importSource, generate, dataPages} from './live-source.mjs'
import {walk} from '../../src/studio/live/model.js'
import {archive, install, build, serve, cleanEnv, routes} from './fixture.mjs'

const args = process.argv.slice(2)
const [repo, commit, output] = args
const cacheIndex = args.indexOf('--npm-cache')
const npmCache = cacheIndex >= 0 ? args[cacheIndex + 1] : null
if (!repo || !commit || !output) throw Error('Usage: import-live.mjs <repository> <commit> <output-dir>')
const started = Date.now()
const work = output + '.work'
fs.rmSync(work, {recursive: true, force: true})
fs.mkdirSync(work, {recursive: true})
const log = fs.openSync(path.join(work, 'import.log'), 'w')
const say = m => fs.writeSync(log, `[${new Date().toISOString()}] ${m}\n`)
const sha = b => crypto.createHash('sha256').update(b).digest('hex')

const source = readCommit(repo, commit)
const {model, pristine} = importSource(source.files, {commit: source.commit})
say(`imported ${Object.keys(model.docs).length} documents, ${model.pages.length} pages`)
const docs = Object.fromEntries(Object.entries(model.docs).map(([id, d]) => [id, d.tree]))
if (Object.keys(generate(pristine, source.files, docs)).length) throw Error('Untouched model did not reproduce the source exactly.')
const instrumented = generate(pristine, source.files, docs, {mode: 'instrument'})

// Pristine and fully regenerated (instrumented) fixtures share one dependency install.
const base = path.join(work, 'pristine'), inst = path.join(work, 'instrumented')
archive(repo, commit, base)
const env = cleanEnv(work, npmCache)
env.HOME = path.join(work, '.config')
say('npm ci'); install(base, env, log)
archive(repo, commit, inst)
fs.symlinkSync(path.join(base, 'node_modules'), path.join(inst, 'node_modules'))
for (const [file, text] of Object.entries(instrumented)) fs.writeFileSync(path.join(inst, file), text)
// Capture-only marker element around opaque parts (never in drafts, previews or releases).
fs.writeFileSync(path.join(inst, 'ttros-studio-mark.d.ts'), "import 'react';\ndeclare module 'react' { namespace JSX { interface IntrinsicElements { 'studio-mark': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & { 'data-sid'?: string } } } }\n")
say('next build (pristine)'); const baseBuild = build(base, env, log)
say('next build (regenerated, instrumented)'); const instBuild = build(inst, env, log)
const a = await serve(base, env, log), b = await serve(inst, env, log)
const built = routes(base)
// Every prerendered page is a Studio page; dynamic routes without their own body document
// (placeholder posts, resources) are edited through their route template.
for (const route of built.prerendered) {
 if (model.pages.some(p => p.route === route)) continue
 const template = model.pages.find(p => /\[[a-z]+\]/.test(p.doc) && route.startsWith(p.route.split('/').slice(0, -1).join('/') + '/')) ||
  Object.values(model.docs).filter(d => d.kind === 'page' && /\[[a-z]+\]/.test(d.file)).map(d => ({doc: d.file, key: d.key, route: '/' + d.file.replace(/^app\//, '').replace(/\/page\.tsx$/, '')}))
   .find(t => new RegExp('^' + t.route.replace(/\[[a-z]+\]/, '[^/]+') + '$').test(route))
 if (!template) continue
 const doc = model.docs[template.doc]
 const slug = route.split('/').at(-1)
 model.pages.push({id: `${doc.key}:${slug}`.slice(0, 90), route, doc: template.doc, bind: {}, label: `${route.split('/')[1]}: ${slug} (template)`})
}
model.pages.sort((x, y) => (x.route === '/' ? -1 : y.route === '/' ? 1 : x.route.localeCompare(y.route)))
dataPages(model.pages, model.docs, pristine.codes)
// Coverage: every built route and static file, with how Studio edits it.
const editable = new Set(model.pages.map(p => p.route))
const coverage = [...new Set([...built.prerendered, ...built.app.map(r => r.replace(/\/(page|route)$/, '') || '/')])].sort().map(route => {
 if (editable.has(route) || (route === '/' && editable.has('/'))) return {route, status: 'visual'}
 if (/\/api\//.test(route)) return {route, status: 'functional', detail: 'API route (server handler); preserved unchanged'}
 if (/\.(txt|xml|ico|png|webmanifest)$|robots|sitemap|icon|manifest/.test(route)) return {route, status: 'generated', detail: 'Generated metadata file; preserved unchanged'}
 if (/\[[a-z]+\]/.test(route)) return {route, status: 'template', detail: 'Dynamic route pattern; each prerendered path is a Studio page'}
 return {route, status: 'not_visual', detail: 'No Studio document'}
})
for (const f of source.tree.filter(f => /^public\/guides\/.+\.html$/.test(f))) {
 const route = '/' + f.replace(/^public\//, '').replace(/\/index\.html$/, '')
 const mirrorOf = Object.values(model.docs).find(d => d.html?.mirrors?.includes(f))
 coverage.push(editable.has(route) ? {route, status: 'visual', detail: 'Static HTML guide, edited element by element (its own stylesheet)'}
  : mirrorOf ? {route, status: 'visual', detail: `Identical copy of ${mirrorOf.file}; written with it`} : {route, status: 'static_html', detail: 'Static HTML file; preserved unchanged'})
}

// Elements whose rendered attributes come from code, and opaque parts whose HTML the canvas shows.
const needsAttrs = new Set(), needsHtml = new Set(), maps = new Map() // Map id → its template root id
const outerOf = new Map() // nested template → enclosing template
for (const d of Object.values(model.docs)) {
 const visit = (list, outer) => { for (const item of list) { if (item.type === 'Map' && outer) outerOf.set(item.props.id, outer); for (const s of ['kids', 'alt']) if (Array.isArray(item.props[s])) visit(item.props[s], item.type === 'Map' ? item.props.id : outer) } }
 visit(d.tree, null)
}
for (const d of Object.values(model.docs)) for (const {item} of walk(d.tree)) {
 if (item.type === 'El' && item.props.x) needsAttrs.add(item.props.id)
 if (item.type === 'Code' || (item.type === 'Use' && !item.props.doc)) needsHtml.add(item.props.id)
 if (item.type === 'Map') maps.set(item.props.id, item.props.kids[0]?.props.id)
}
const normalise = (html, strip) => {
 const dom = new JSDOM(html)
 const doc = dom.window.document
 const seo = {title: doc.title, meta: [...doc.querySelectorAll('meta[name],meta[property]')].map(m => `${m.getAttribute('name') || m.getAttribute('property')}=${m.getAttribute('content')}`).sort(),
  canonical: doc.querySelector('link[rel=canonical]')?.getAttribute('href') || null,
  jsonld: [...doc.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent)}
 for (const s of [...doc.querySelectorAll('script:not([type="application/ld+json"]), link[rel=preload], link[as], template, next-route-announcer')]) s.remove()
 if (strip) {
  for (const m of [...doc.querySelectorAll('studio-mark')]) m.replaceWith(...m.childNodes)
  for (const e of [...doc.querySelectorAll('[data-sid]')]) e.removeAttribute('data-sid')
 }
 const walker = doc.createTreeWalker(doc.body, 128)
 const comments = []
 while (walker.nextNode()) comments.push(walker.currentNode)
 comments.forEach(c => c.remove())
 doc.body.normalize()
 return {body: doc.body.innerHTML, seo, htmlClass: doc.documentElement.className, bodyClass: doc.body.className}
}
const capture = {}, parity = {}
const pageRoutes = [...new Set(model.pages.map(p => p.route))]
for (const route of pageRoutes) {
 const url = route === '/_not-found' ? '/__ttros-studio-missing-page' : route
 const [ra, rb] = await Promise.all([fetch(a.url + url), fetch(b.url + url)])
 const [ha, hb] = [await ra.text(), await rb.text()]
 const left = normalise(ha, false), right = normalise(hb, true)
 let firstDiff = null
 if (left.body !== right.body) { let i = 0; while (left.body[i] === right.body[i]) i++; firstDiff = {at: i, pristine: left.body.slice(Math.max(0, i - 80), i + 120), regenerated: right.body.slice(Math.max(0, i - 80), i + 120)} }
 parity[route] = {status: [ra.status, rb.status], body: left.body === right.body, seo: JSON.stringify(left.seo) === JSON.stringify(right.seo), bodySha256: sha(left.body), ...(firstDiff ? {firstDiff} : {})}
 const dom = new JSDOM(hb).window.document
 // Elements inside repeated templates occur once per entry: the first occurrence is the editable
 // entry; every occurrence is kept (htmlAll/attrsAll) so the canvas can show each entry as rendered.
 const attrs = {}, html = {}, present = [], counts = {}, htmlAll = {}, attrsAll = {}, countsByEntry = {}
 const occurrence = new Map()
 const clean = h => h.replace(/ data-sid="[^"]*"/g, '').replace(/<\/?studio-mark>/g, '')
 for (const e of dom.querySelectorAll('[data-sid]')) {
  const id = e.getAttribute('data-sid')
  present.push(id)
  counts[id] = (counts[id] || 0) + 1
  occurrence.set(e, counts[id] - 1)
  if (e.tagName === 'STUDIO-MARK') {
   if (needsHtml.has(id)) { (htmlAll[id] ||= []).push(clean(e.innerHTML)); if (counts[id] === 1) html[id] = htmlAll[id][0] }
   continue
  }
  if (needsAttrs.has(id)) { (attrsAll[id] ||= []).push(Object.fromEntries([...e.attributes].filter(x => x.name !== 'data-sid').map(x => [x.name, x.value]))); if (counts[id] === 1) attrs[id] = attrsAll[id][0] }
 }
 // A template's entries = occurrences of its root element.
 for (const [id, root] of maps) if (root && counts[root]) counts[id] = counts[root]
 // Nested templates: entries per entry of the enclosing template.
 for (const [id, outer] of outerOf) {
  const root = maps.get(id), outerRoot = maps.get(outer)
  if (!root || !outerRoot) continue
  const per = []
  for (const e of dom.querySelectorAll(`[data-sid="${root}"]`)) { const a = e.parentElement?.closest(`[data-sid="${outerRoot}"]`); const k = a ? occurrence.get(a) : 0; per[k] = (per[k] || 0) + 1 }
  if (per.length) countsByEntry[id] = Array.from(per, v => v || 0)
 }
 for (const id of Object.keys(htmlAll)) if (htmlAll[id].length < 2) delete htmlAll[id]
 for (const id of Object.keys(attrsAll)) if (attrsAll[id].length < 2) delete attrsAll[id]
 for (const id of Object.keys(counts)) if (counts[id] < 2 && !maps.has(id)) delete counts[id]
 capture[route] = {htmlClass: left.htmlClass, bodyClass: left.bodyClass, attrs, html, htmlAll, attrsAll, counts, countsByEntry, seo: left.seo, present}
}
a.stop(); b.stop()
// State-dependent conditions show in the editor the branch the site renders on load.
const rendered = new Set(Object.values(capture).flatMap(c => c.present))
for (const d of Object.values(model.docs)) for (const {item} of walk(d.tree)) {
 if (item.type !== 'Cond' || item.props.show) continue
 const any = list => [...walk(list)].some(e => rendered.has(e.item.props.id))
 item.props.show = any(item.props.kids) ? 'kids' : any(item.props.alt) ? 'alt' : 'none'
}
for (const c of Object.values(capture)) delete c.present

// The built stylesheet and fonts for the canvas (URLs rewritten to the snapshot's own media).
fs.mkdirSync(path.join(work, 'canvas/media'), {recursive: true})
const cssDir = path.join(base, '.next/static/css')
let css = ''
for (const f of fs.readdirSync(cssDir).sort()) css += fs.readFileSync(path.join(cssDir, f), 'utf8') + '\n'
css = css.replace(/\/_next\/static\/media\//g, 'media/')
fs.writeFileSync(path.join(work, 'canvas/site.css'), css)
// Static HTML guides render on the canvas with their own stylesheet.
for (const page of model.pages.filter(p => p.css)) fs.writeFileSync(path.join(work, 'canvas', page.css), pristine.docs[page.doc].html.css)
const mediaDir = path.join(base, '.next/static/media')
if (fs.existsSync(mediaDir)) for (const f of fs.readdirSync(mediaDir)) fs.copyFileSync(path.join(mediaDir, f), path.join(work, 'canvas/media', f))

// public/ files from Git objects (served by the backend by object id; never from a working tree).
const listing = execFileSync('git', ['-C', repo, 'ls-tree', '-r', '-l', commit, 'public'], {encoding: 'utf8'}).trim().split('\n')
const assets = listing.map(l => { const [meta, file] = l.split('\t'); const [, , object, size] = meta.split(/\s+/); return {path: file.replace(/^public/, ''), object, bytes: Number(size)} })
 .filter(x => /\.(png|jpe?g|gif|webp|avif|svg|ico|pdf|mp4|webm)$/i.test(x.path))

for (const [name, value] of Object.entries({'coverage.json': coverage, 'model.json': model, 'pristine.json': pristine, 'capture.json': capture, 'assets.json': {commit, assets}, 'parity.json': parity})) fs.writeFileSync(path.join(work, name), JSON.stringify(value))
const receipt = {schema: 'ttros.studio.live.import/1', commit, repository: 'registered website repository (Git objects only)', started_at: new Date(started).toISOString(), finished_at: new Date().toISOString(),
 seconds: Math.round((Date.now() - started) / 1000), documents: Object.keys(model.docs).length, pages: model.pages.length,
 nodes: Object.values(model.docs).reduce((n, d) => n + [...walk(d.tree)].length, 0), codes: Object.keys(model.codes).length, unsupported: pristine.unsupported,
 builds: {pristine: baseBuild, regenerated: instBuild}, routes: built, parity: {routes: Object.keys(parity).length, identical: Object.values(parity).filter(p => p.body && p.seo).length},
 files: Object.fromEntries(['model.json', 'pristine.json', 'capture.json', 'assets.json', 'parity.json', 'canvas/site.css'].map(f => [f, sha(fs.readFileSync(path.join(work, f)))])), model_calls: 0}
fs.writeFileSync(path.join(work, 'receipt.json'), JSON.stringify(receipt, null, 2))
// Keep only the snapshot: fixtures (≈1 GB) are removed.
fs.rmSync(base, {recursive: true, force: true}); fs.rmSync(inst, {recursive: true, force: true})
fs.closeSync(log)
for (const f of ['.config', '.npm-user.config', '.npm-global.config', '.npm-cache']) fs.rmSync(path.join(work, f), {recursive: true, force: true})
fs.rmSync(output, {recursive: true, force: true})
fs.renameSync(work, output)
console.log(JSON.stringify({ok: true, ...receipt.parity, seconds: receipt.seconds}))
