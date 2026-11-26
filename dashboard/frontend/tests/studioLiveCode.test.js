// Website Studio code layer and shared operations (code.js): validators (shared fixture with the
// Python authority), operations, and the code layer applied over the real pinned website source.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {execFileSync} from 'node:child_process'
import {parse} from '@babel/parser'
import {readCommit, LIVE_SOURCE} from '../scripts/studio-live/git-source.mjs'
import {importSource, generate} from '../scripts/studio-live/live-source.mjs'
import {applyOps, applyCodeLayer, cssProblem, linkProblem, fileProblem, codeProblem, headerProblem, headerState, behaviorSource, normalizeCode, HEADER_DEFAULT, BEHAVIOR_FILE} from '../src/studio/live/code.js'
import {findNode, walk, styleProblem} from '../src/studio/live/model.js'
import {compose, split, canonical} from '../src/studio/live/compose.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const cases = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/studio-code-cases.json'), 'utf8'))
const repo = '/home/operator/ttros-projects/default-site'
const available = (() => { try { execFileSync('git', ['-C', repo, 'cat-file', '-e', `${LIVE_SOURCE}^{commit}`]); return true } catch { return false } })()
const skip = available ? false : `live source ${LIVE_SOURCE.slice(0, 8)} is not in ${repo}`
const tsx = text => parse(text, {sourceType: 'module', plugins: ['typescript', 'jsx']})
let cached
const load = () => cached ||= (() => { const source = readCommit(repo, LIVE_SOURCE); return {source, ...importSource(source.files, {commit: source.commit})} })()
const docsOf = model => Object.fromEntries(Object.entries(model.docs).map(([id, d]) => [id, structuredClone(d.tree)]))

test('validators agree with the shared fixture (same table as the Python authority)', () => {
 for (const [css, ok] of cases.css) assert.equal(cssProblem(css) === null, ok, `css ${css} → ${cssProblem(css)}`)
 const ctx = {routes: new Set(cases.routes), files: new Set(cases.files_public)}
 for (const [href, ok] of cases.links) assert.equal(linkProblem(href, ctx) === null, ok, `link ${JSON.stringify(href)} → ${linkProblem(href, ctx)}`)
 for (const [file, ok] of cases.files) assert.equal(fileProblem(file, 'x') === null, ok, `file ${file} → ${fileProblem(file, 'x')}`)
})

test('header settings, code layer shape and gradients are validated', () => {
 assert.equal(headerProblem(HEADER_DEFAULT), null)
 assert.match(headerProblem({...HEADER_DEFAULT, mobile: 'float'}), /mobile mode/)
 assert.match(headerProblem({...HEADER_DEFAULT, threshold: 999}), /threshold/)
 assert.match(codeProblem({css: 'a{}', script: 'x'}), /Unknown code layer field/)
 assert.equal(styleProblem('backgroundImage', 'linear-gradient(180deg, #F7F3EA, #ECE9E2)'), null)
 assert.ok(styleProblem('backgroundImage', 'linear-gradient(180deg, url(x), red)'))
 assert.deepEqual(normalizeCode({css: '  ', files: {}}), {})
})

test('header state: desktop sticky transparent→solid, mobile hides scrolling down and reveals solid scrolling up, menu keeps it visible', () => {
 const h = HEADER_DEFAULT
 assert.deepEqual(headerState(h, 'desktop', {y: 0, lastY: 0, height: 112}), {mode: 'sticky-transparent', hidden: false, solid: false})
 assert.deepEqual(headerState(h, 'desktop', {y: 600, lastY: 300, height: 112}), {mode: 'sticky-transparent', hidden: false, solid: true})
 assert.deepEqual(headerState(h, 'mobile', {y: 600, lastY: 300, height: 112}), {mode: 'auto-hide', hidden: true, solid: true})
 assert.deepEqual(headerState(h, 'mobile', {y: 500, lastY: 600, height: 112}), {mode: 'auto-hide', hidden: false, solid: true})
 assert.equal(headerState(h, 'tablet', {y: 600, lastY: 300, height: 112, menuOpen: true}).hidden, false)
})

test('operations: link, style, text, attributes, CSS, header, files; refusals name the problem', () => {
 const tree = [{type: 'El', props: {id: 'p_1', tag: 'a', a: {href: '/pricing'}, kids: [{type: 'Txt', props: {id: 'p_2', text: 'Go'}}]}},
  {type: 'El', props: {id: 'p_3', tag: 'a', x: {href: 'p~1'}, kids: []}}, {type: 'El', props: {id: 'p_4', tag: 'img', a: {src: '/x.png'}}}]
 const ctx = {routes: new Set(['/', '/pricing']), files: new Set()}
 const r = applyOps({page: tree}, {}, [{op: 'set_link', id: 'p_1', href: 'https://cal.example/booking', newTab: true}, {op: 'set_style', id: 'p_1', bp: 'm', values: {fontSize: '18px'}},
  {op: 'set_text', id: 'p_2', text: 'Book'}, {op: 'set_attr', id: 'p_4', name: 'alt', value: 'Logo'}, {op: 'set_css', css: '.x{color:red}'},
  {op: 'set_header', header: HEADER_DEFAULT}, {op: 'set_file', path: 'lib/config.ts', text: 'export const X = 1\n'}], ctx)
 const a = findNode(r.lists.page, 'p_1').item.props
 assert.deepEqual(a.a, {href: 'https://cal.example/booking', target: '_blank', rel: 'noopener noreferrer'})
 assert.deepEqual(a.st, {m: {fontSize: '18px'}})
 assert.equal(findNode(r.lists.page, 'p_2').item.props.text, 'Book')
 assert.equal(findNode(r.lists.page, 'p_4').item.props.a.alt, 'Logo')
 assert.deepEqual(Object.keys(r.code).sort(), ['css', 'files', 'header'])
 assert.equal(findNode(tree, 'p_1').item.props.a.href, '/pricing', 'inputs are not mutated')
 const refuse = (ops, re) => assert.throws(() => applyOps({page: tree}, {}, ops, ctx), re)
 refuse([{op: 'set_link', id: 'p_1', href: '/nope'}], /no page or file/)
 refuse([{op: 'set_link', id: 'p_1', href: 'javascript:alert(1)'}], /Unsafe/)
 refuse([{op: 'set_link', id: 'p_3', href: '/pricing'}], /computed by the site/)
 refuse([{op: 'set_link', id: 'zz_1', href: '/pricing'}], /not on this page/)
 refuse([{op: 'set_style', id: 'p_2', bp: 'd', values: {color: 'red'}}], /element around this text/)
 refuse([{op: 'set_style', id: 'p_1', bp: 'd', values: {color: 'red;background:url(x)'}}], /unsupported value/)
 refuse([{op: 'set_css', css: '@import url(x)'}], /import/)
 refuse([{op: 'set_file', path: 'package.json', text: '{}'}], /not an editable/)
 refuse([{op: 'eval'}], /Unknown operation/)
})

test('code layer over the real source: owner CSS, header behaviour mounted in the layout, source edits, valid TSX', {skip}, () => {
 const {source, model, pristine} = load()
 const layer = {css: '.btn-primary{letter-spacing:.02em}', header: HEADER_DEFAULT, files: {'lib/config.ts': source.files.get('lib/config.ts').replace('export const', '// edited in Studio Code mode\nexport const')}}
 const out = applyCodeLayer(layer, source.files, generate(pristine, source.files, docsOf(model)), {docFiles: new Set(Object.keys(model.docs))})
 assert.deepEqual(Object.keys(out).sort(), ['app/globals.css', 'app/layout.tsx', BEHAVIOR_FILE, 'lib/config.ts'].sort())
 assert.ok(out['app/globals.css'].startsWith(source.files.get('app/globals.css').trimEnd()))
 assert.match(out['app/globals.css'], /ttros-studio: owner CSS \*\/\n\.btn-primary\{letter-spacing:\.02em\}/)
 assert.match(out['app/globals.css'], /data-studio-mode="auto-hide"\]\[data-studio-hidden="true"\]\{transform:translateY\(-100%\)/)
 assert.match(out['app/layout.tsx'], /import StudioSiteBehavior from "@\/components\/StudioSiteBehavior";/)
 assert.match(out['app/layout.tsx'], /<StudioSiteBehavior \/>\n\s*<\/body>/)
 tsx(out['app/layout.tsx']); tsx(out[BEHAVIOR_FILE])
 assert.match(out[BEHAVIOR_FILE], /"desktop":"sticky-transparent","tablet":"auto-hide","mobile":"auto-hide","threshold":8/)
 // A visual document with element edits cannot also carry a source edit.
 const docs = docsOf(model)
 const h = [...walk(docs['components/Header.tsx'])].find(e => e.item.type === 'Txt').item
 h.props.text = 'Edited'
 assert.throws(() => applyCodeLayer({files: {'components/Header.tsx': '// x'}}, source.files, generate(pristine, source.files, docs), {docFiles: new Set(Object.keys(model.docs))}), /visual edits and a source edit/)
 // An untouched visual document can be source-edited (code-only): its file is replaced verbatim.
 const faq = source.files.get('components/FaqAccordion.tsx') + '\n// Studio code edit\n'
 assert.equal(applyCodeLayer({files: {'components/FaqAccordion.tsx': faq}}, source.files, generate(pristine, source.files, docsOf(model)), {docFiles: new Set(Object.keys(model.docs))})['components/FaqAccordion.tsx'], faq)
})

test('manual edit and the equivalent operation on the composed page yield identical documents and source', {skip}, () => {
 const {source, model, pristine} = load()
 const page = model.pages.find(p => p.id === 'home')
 const docs = docsOf(model)
 const content = compose(page, docs)
 const link = [...walk(content)].find(e => e.item.type === 'El' && ['a', 'Link'].includes(e.item.props.tag) && e.item.props.a?.href === '/free-assessment').item
 // "Manual": the inspector's own path (ops on the composed Puck content, then split).
 const manual = split(page, applyOps({content}, {}, [{op: 'set_link', id: link.props.id, href: 'https://cal.com/timetorevenue/fit-call'}, {op: 'set_style', id: link.props.id, bp: 'm', values: {fontSize: '18px'}}], {routes: new Set(['/'])}).lists.content)
 // "Agent": the same ops applied to the per-document trees (what the server does for a proposal).
 const agent = applyOps(split(page, content), {}, [{op: 'set_link', id: link.props.id, href: 'https://cal.com/timetorevenue/fit-call'}, {op: 'set_style', id: link.props.id, bp: 'm', values: {fontSize: '18px'}}], {routes: new Set(['/'])}).lists
 assert.equal(canonical(manual), canonical(agent))
 const a = generate(pristine, source.files, {...docs, ...manual}), b = generate(pristine, source.files, {...docs, ...agent})
 assert.deepEqual(a, b)
 // The first /free-assessment link on the composed homepage is the shared Header's CTA.
 assert.deepEqual(Object.keys(a).sort(), ['app/layout.tsx', 'app/ttros-studio.css', 'components/Header.tsx'])
 assert.match(a['components/Header.tsx'], /href="https:\/\/cal\.com\/timetorevenue\/fit-call"/)
})

test('the generated header component type-checks under strict TypeScript for every mode combination', {skip: fs.existsSync(`${repo}/node_modules/typescript/bin/tsc`) ? false : 'website TypeScript not installed'}, () => {
 const dir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'studio-tsc-'))
 try {
  const files = []
  for (const desktop of ['sticky-transparent', 'sticky-solid', 'static']) for (const mobile of ['auto-hide', 'static']) {
   const name = `B_${desktop}_${mobile}.tsx`.replace(/-/g, '_')
   fs.writeFileSync(path.join(dir, name), behaviorSource({desktop, tablet: mobile, mobile, threshold: 8}))
   files.push(name)
  }
  fs.writeFileSync(path.join(dir, 'tsconfig.json'), JSON.stringify({compilerOptions: {strict: true, noEmit: true, jsx: 'preserve', target: 'ES2020', module: 'esnext', moduleResolution: 'node', lib: ['dom', 'es2020'],
   skipLibCheck: true, baseUrl: '.', paths: {react: [`${repo}/node_modules/@types/react/index.d.ts`]}, typeRoots: [`${repo}/node_modules/@types`], types: []}, files}))
  execFileSync(process.execPath, [`${repo}/node_modules/typescript/bin/tsc`, '-p', dir], {encoding: 'utf8'})
 } finally { fs.rmSync(dir, {recursive: true, force: true}) }
})
