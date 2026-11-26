// Live website editor: adapter round-trip, minimal regeneration, structural edits, responsive CSS,
// composition and protected negative cases. Uses the pinned live commit from Git objects; skips if
// the registered website repository does not have it.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {execFileSync} from 'node:child_process'
import {parse} from '@babel/parser'
import {readCommit, LIVE_SOURCE} from '../scripts/studio-live/git-source.mjs'
import {importSource, generate} from '../scripts/studio-live/live-source.mjs'
import {validateTree, moveNode, insertNode, removeNode, setStyle, updateProps, duplicateSubtree, wrapNode, unwrapNode, findNode, walk, treeCss, instantiate, PALETTE, urlProblem, styleProblem} from '../src/studio/live/model.js'
import {compose, split, canonical} from '../src/studio/live/compose.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const registry = JSON.parse(fs.readFileSync(path.join(root, 'context/website_projects.json'), 'utf8'))
const repo = Object.values(registry.projects || registry).find(p => p && p.id === 'default-site' || p?.repository?.includes?.('default-site'))?.repository || '/home/operator/ttros-projects/default-site'
const available = (() => { try { execFileSync('git', ['-C', repo, 'cat-file', '-e', `${LIVE_SOURCE}^{commit}`]); return true } catch { return false } })()
const skip = available ? false : `live source ${LIVE_SOURCE.slice(0, 8)} is not in ${repo}`

let cached
function load() {
 if (!cached) {
  const source = readCommit(repo, LIVE_SOURCE)
  cached = {source, ...importSource(source.files, {commit: source.commit})}
 }
 return cached
}
const docsOf = model => Object.fromEntries(Object.entries(model.docs).map(([id, d]) => [id, structuredClone(d.tree)]))
const codesFor = (model, doc) => Object.fromEntries(Object.entries(model.codes).map(([k, c]) => [k, c.pure ? {...c, doc} : c]))
const tsx = text => parse(text, {sourceType: 'module', plugins: ['typescript', 'jsx']})

test('imports every page, shared component and post body with no unsupported documents', {skip}, () => {
 const {model, pristine} = load()
 assert.equal(pristine.unsupported.length, 0)
 assert.ok(Object.keys(model.docs).length >= 38)
 for (const f of ['app/page.tsx', 'components/Header.tsx', 'components/Footer.tsx', 'app/free-assessment/page.tsx', 'app/pricing/page.tsx', 'app/blog/[slug]/page.tsx']) assert.ok(model.docs[f], f)
 let n = 0, opaque = 0
 for (const d of Object.values(model.docs)) for (const {item} of walk(d.tree)) { n++; if (item.type === 'Code') opaque++ }
 assert.ok(n > 9000)
 assert.ok(opaque / n < 0.01, `opaque share ${opaque}/${n}`)
 for (const [id, d] of Object.entries(model.docs)) validateTree(d.tree, {codes: codesFor(model, id), doc: id})
})

test('an untouched model regenerates nothing (byte-identical source)', {skip}, () => {
 const {model, pristine, source} = load()
 assert.deepEqual(generate(pristine, source.files, docsOf(model)), {})
})

test('a text edit rewrites only its file, keeps the rest of the file verbatim, and is valid TSX', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const h1 = [...walk(docs['app/page.tsx'])].find(e => e.item.props.tag === 'h1').item
 const text = h1.props.kids.find(k => k.type === 'Txt')
 docs['app/page.tsx'] = updateProps(docs['app/page.tsx'], text.props.id, {text: 'Edited "headline" & <more>'})
 const out = generate(pristine, source.files, docs)
 assert.deepEqual(Object.keys(out), ['app/page.tsx'])
 const next = out['app/page.tsx']
 tsx(next)
 assert.ok(next.includes(JSON.stringify('Edited "headline" & <more>')))
 const original = source.files.get('app/page.tsx')
 assert.ok(next.startsWith(original.slice(0, original.indexOf('export default'))), 'metadata, imports and data before the component are unchanged')
 assert.ok(next.includes('export const metadata'))
})

test('moving an element between containers and styling it per breakpoint generates real source + CSS', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 let home = docs['app/page.tsx']
 home = moveNode(home, 'home_c', 'home_o', 'kids', 0)
 home = setStyle(home, 'home_p', 'd', {display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))'})
 home = setStyle(home, 'home_p', 'm', {gridTemplateColumns: '1fr'})
 home = setStyle(home, 'home_8', 'd', {position: 'relative', left: '160px', top: '40px'})
 docs['app/page.tsx'] = home
 const out = generate(pristine, source.files, docs)
 assert.deepEqual(Object.keys(out).sort(), ['app/layout.tsx', 'app/page.tsx', 'app/ttros-studio.css'])
 tsx(out['app/page.tsx']); tsx(out['app/layout.tsx'])
 const page = out['app/page.tsx']
 assert.ok(page.indexOf('s-home_p') > 0 && page.indexOf('s-home_8') > 0)
 const css = out['app/ttros-studio.css']
 assert.match(css, /\.s-home_p\{display:grid !important;grid-template-columns:repeat\(3, minmax\(0, 1fr\)\) !important\}/)
 assert.match(css, /@media \(max-width: 767\.98px\)\{\.s-home_p\{grid-template-columns:1fr !important\}\}/)
 assert.ok(css.indexOf('.s-home_p{display') < css.indexOf('@media (max-width: 767.98px)'), 'desktop before mobile overrides')
 assert.ok(out['app/layout.tsx'].includes('import "./ttros-studio.css";'))
 // The heading now sits in the stats container: its <h1> appears before the stats grid.
 const band = page.indexOf('className="container-site py-5')
 assert.ok(band > 0 && page.indexOf('<h1', band) > band && page.indexOf('<h1', band) < page.indexOf('s-home_p'))
})

test('shared Header: unrolled navigation edits keep the state handlers as source code', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const header = docs['components/Header.tsx']
 const link = [...walk(header)].find(e => e.item.props.tag === 'Link' && e.item.props.x?.onClick).item
 const label = link.props.kids.find(k => k.type === 'Txt')
 docs['components/Header.tsx'] = updateProps(header, label.props.id, {text: 'Speed to Lead'})
 const out = generate(pristine, source.files, docs)
 assert.deepEqual(Object.keys(out), ['components/Header.tsx'])
 const next = out['components/Header.tsx']
 tsx(next)
 assert.ok(next.startsWith('"use client"'))
 assert.ok(next.includes('"Speed to Lead"'))
 assert.ok(next.includes('onClick={() => setSolutionsOpen(false)}'))
 assert.ok(next.includes('useState(false)'))
})

test('functional booking form: moving its section keeps the Tally iframe source binding and Script', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const fa = docs['app/free-assessment/page.tsx']
 const main = fa.find(i => i.props.tag === 'main')
 docs['app/free-assessment/page.tsx'] = moveNode(fa, 'free-assessment_m', main.props.id, 'kids', 0)
 const out = generate(pristine, source.files, docs)
 const next = out['app/free-assessment/page.tsx']
 tsx(next)
 assert.ok(next.includes('src={`https://tally.so/embed/${TALLY_FORM_ID}'))
 assert.ok(next.includes('<Script src="https://tally.so/widgets/embed.js" strategy="afterInteractive" />'))
 assert.ok(next.indexOf('aria-label="Assessment application form"') < next.indexOf('aria-labelledby="apply-heading"'))
})

test('palette blocks, duplicate, wrap and unwrap produce valid documents and source', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 let home = docs['app/page.tsx']
 const taken = new Set([...walk(home)].map(e => e.item.props.id))
 for (const p of PALETTE) home = insertNode(home, instantiate(p.make(), 'home', taken), 'home_7', 'kids')
 const {copy} = duplicateSubtree(findNode(home, 'home_g').item, 'home', taken)
 home = insertNode(home, copy, 'home_7', 'kids')
 const wrapper = instantiate({type: 'El', props: {tag: 'div', kids: []}}, 'home', taken)
 home = wrapNode(home, 'home_e', wrapper)
 home = unwrapNode(home, 'home_8')
 validateTree(home, {codes: codesFor(model, 'app/page.tsx'), doc: 'app/page.tsx'})
 docs['app/page.tsx'] = home
 const out = generate(pristine, source.files, docs)
 tsx(out['app/page.tsx'])
 assert.ok(out['app/ttros-studio.css'].includes('grid-template-columns:repeat(4, minmax(0, 1fr))'))
})

test('compose/split round-trips every Studio page exactly', {skip}, () => {
 const {model} = load()
 const docs = docsOf(model)
 for (const page of model.pages) {
  const back = split(page, compose(page, docs))
  for (const [doc, tree] of Object.entries(back)) assert.equal(canonical(tree), canonical(docs[doc]), `${page.id} → ${doc}`)
 }
})

test('instrumented full regeneration parses for every document', {skip}, () => {
 const {model, pristine, source} = load()
 const out = generate(pristine, source.files, docsOf(model), {mode: 'instrument'})
 // Every JSX document regenerates (data modules are patched in place, never instrumented).
 const mirrors = Object.values(model.docs).flatMap(d => d.html?.mirrors || [])
 assert.equal(Object.keys(out).length, Object.values(model.docs).filter(d => d.kind !== 'data').length + mirrors.length)
 for (const [file, text] of Object.entries(out)) if (/\.tsx?$/.test(file)) tsx(text)
})

test('protected: code cannot leave its file, be authored, or smuggle unsafe values', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 // An element with Header state/handlers moved into a page is refused by the validator and the generator.
 const solutions = findNode(docs['components/Header.tsx'], 'c-header_8').item
 const page = insertNode(docs['app/page.tsx'], structuredClone(solutions), 'home_7', 'kids')
 assert.throws(() => validateTree(page, {codes: codesFor(model, 'app/page.tsx'), doc: 'app/page.tsx'}), /can only stay inside that file/)
 assert.throws(() => generate(pristine, source.files, {...docs, 'app/page.tsx': page}), /was moved into app\/page\.tsx/)
 const codes = codesFor(model, 'app/page.tsx')
 const el = props => [{type: 'El', props: {id: 'home_ztest', tag: 'div', kids: [], ...props}}]
 assert.throws(() => validateTree(el({a: {onClick: 'alert(1)'}}), {codes}), /not allowed/)
 assert.throws(() => validateTree(el({a: {dangerouslySetInnerHTML: 'x'}}), {codes}), /not allowed/)
 assert.throws(() => validateTree([{type: 'El', props: {id: 'home_ztest', tag: 'a', a: {href: 'javascript:alert(1)'}, kids: []}}], {codes}), /Unsafe href/)
 assert.throws(() => validateTree([{type: 'El', props: {id: 'home_ztest', tag: 'script', kids: []}}], {codes}), /not allowed/)
 assert.throws(() => validateTree([{type: 'El', props: {id: 'home_ztest', tag: 'iframe', a: {src: 'https://evil.example/x'}}}], {codes}), /Frames may only embed/)
 assert.throws(() => validateTree(el({st: {d: {backgroundImage: 'url(https://evil.example/x.png)'}}}), {codes}), /asset library/)
 assert.throws(() => validateTree(el({st: {d: {color: 'red;}body{display:none'}}}), {codes}), /unsupported value/)
 assert.throws(() => validateTree(el({cls: 'x" onload="y'}), {codes}), /class list/)
 assert.throws(() => validateTree([{type: 'Code', props: {id: 'home_ztest', c: 'invented~ref'}}], {codes}), /Unknown source reference/)
 assert.throws(() => validateTree(el({x: {onClick: 'c-header~5'}}), {codes, doc: 'app/page.tsx'}), /source reference|only stay inside/)
 assert.equal(urlProblem('href', 'mailto:hello@core-site.example'), null)
 assert.equal(styleProblem('width', '320px'), null)
})

test('treeCss orders desktop, tablet then mobile rules', () => {
 const content = [{type: 'El', props: {id: 'home_a', tag: 'div', kids: [], st: {m: {width: '10px'}, d: {width: '30px'}, t: {width: '20px'}}}}]
 const css = treeCss(content)
 assert.ok(css.indexOf('30px') < css.indexOf('20px') && css.indexOf('20px') < css.indexOf('10px'))
})

test('repeated templates: FAQ item markup is editable once for every item, and stays bound inside its template', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const faq = model.docs['components/FaqAccordion.tsx']
 assert.equal(faq.kind, 'template')
 const map = [...walk(docs['components/FaqAccordion.tsx'])].find(e => e.item.type === 'Map').item
 assert.deepEqual(model.codes[map.props.c].list, {prop: 'items'})
 // Pages use it through a template reference; compose shows its markup in place and split returns it.
 const home = model.pages.find(p => p.route === '/')
 const use = [...walk(docs['app/page.tsx'])].find(e => e.item.type === 'Use' && e.item.props.name === 'FaqAccordion').item
 assert.equal(use.props.tpl, 'components/FaqAccordion.tsx')
 const composed = compose(home, docs)
 assert.ok([...walk(composed)].some(e => e.item.props.id === map.props.id))
 // Restyle the question button and move the +/- icon before the question text: one template edit.
 const button = map.props.kids[0].props.kids.find(k => k.type === 'El' && k.props.tag === 'button')
 const [question, icon] = button.props.kids
 let tree = setStyle(docs['components/FaqAccordion.tsx'], button.props.id, 'd', {gap: '12px', justifyContent: 'flex-start'})
 tree = moveNode(tree, icon.props.id, button.props.id, 'kids', 0)
 validateTree(tree, {codes: codesFor(model, 'components/FaqAccordion.tsx'), doc: 'components/FaqAccordion.tsx'})
 const out = generate(pristine, source.files, {...docs, 'components/FaqAccordion.tsx': tree})
 assert.deepEqual(Object.keys(out).sort(), ['app/layout.tsx', 'app/ttros-studio.css', 'components/FaqAccordion.tsx'])
 const text = out['components/FaqAccordion.tsx']
 tsx(text)
 assert.ok(text.includes('items.map((item, i) => {') && text.includes('onClick={() => setOpenIndex(isOpen ? null : i)}') && text.includes('aria-expanded={isOpen}'))
 assert.ok(text.indexOf('rotate(45deg)') < text.indexOf('{item.question}'), 'icon now precedes the question')
 // An element bound to an item (the question text) cannot leave its template.
 const outside = moveNode(docs['components/FaqAccordion.tsx'], question.props.id, null, 'kids', 0)
 assert.throws(() => validateTree(outside, {codes: codesFor(model, 'components/FaqAccordion.tsx'), doc: 'components/FaqAccordion.tsx'}), /repeated item/)
 assert.throws(() => generate(pristine, source.files, {...docs, 'components/FaqAccordion.tsx': outside}), /repeated item/)
})

test('data records: a blog post title edited once is written into content/blog.ts only', {skip}, () => {
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const page = model.pages.find(p => p.route === '/blog/from-prompts-to-ai-workflows')
 assert.deepEqual(page.data, {'content/blog.ts': 'from-prompts-to-ai-workflows'})
 const composed = compose(page, docs)
 const holder = composed.find(i => i.type === 'Data')
 const rec = holder.props.kids.find(r => r.props.key === 'from-prompts-to-ai-workflows')
 const edited = updateProps(composed, rec.props.id, {f: {...rec.props.f, title: 'Prompts to Workflows: "a practical guide"', excerpt: 'Short excerpt.'}})
 const back = split(page, edited)
 validateTree(back['content/blog.ts'], {codes: codesFor(model, 'content/blog.ts'), doc: 'content/blog.ts'})
 const out = generate(pristine, source.files, {...docs, ...back})
 assert.deepEqual(Object.keys(out), ['content/blog.ts'])
 const before = source.files.get('content/blog.ts'), after = out['content/blog.ts']
 tsx(after)
 assert.ok(after.includes('"Prompts to Workflows: \\"a practical guide\\""') && after.includes('excerpt:\n      "Short excerpt."'))
 assert.equal(after.split('\n').length, before.split('\n').length)
 // Records cannot be added, removed or reordered.
 const fewer = back['content/blog.ts'].slice(1)
 assert.throws(() => generate(pristine, source.files, {...docs, 'content/blog.ts': fewer}), /cannot be added, removed or reordered/)
 // The post page binds its visible fields to that record.
 const bound = [...walk(docs['app/blog/[slug]/page.tsx'])].filter(e => e.item.type === 'Code' && model.codes[e.item.props.c].bind).map(e => model.codes[e.item.props.c].bind.field)
 for (const f of ['title', 'excerpt', 'date', 'readingTime', 'category', 'description']) assert.ok(bound.includes(f), f)
})

test('state text and index lists: header menu label branches, blog clusters and per-route lists are editable', {skip}, () => {
 const {model} = load()
 const header = [...walk(model.docs['components/Header.tsx'].tree)]
 const cond = header.find(e => e.item.type === 'Cond' && e.item.props.kids.length === 1 && e.item.props.kids[0].type === 'Txt' && e.item.props.kids[0].props.text === 'Close menu')
 assert.ok(cond && cond.item.props.alt[0].props.text === 'Open menu' && cond.item.props.show === 'alt')
 const blog = [...walk(model.docs['app/blog/page.tsx'].tree)]
 assert.ok(!blog.some(e => e.item.type === 'Code'), 'blog index has no opaque parts')
 assert.ok(blog.filter(e => e.item.type === 'Txt' && e.item.props.text === 'Start Here').length === 1)
 const post = [...walk(model.docs['app/blog/[slug]/page.tsx'].tree)]
 assert.ok(post.filter(e => e.item.type === 'Map').length >= 4)
})

test('migration: saved edits carry onto a changed website source; source additions are kept', {skip}, async () => {
 const {migrate} = await import('../scripts/studio-live/migrate-live.mjs')
 const {model, pristine, source} = load()
 const docs = docsOf(model)
 const file = 'app/pricing/page.tsx'
 // New source commit: a paragraph added right after <main>, and the second heading reworded.
 const files = new Map(source.files)
 files.set(file, files.get(file).replace('<main id="main-content">', '<main id="main-content">\n        <p className="source-added">Added by the source</p>')
  .replace('The build reflects your business, not a fixed package.', 'The build reflects your business.'))
 const next = importSource(files, {commit: 'f'.repeat(40)})
 // Owner edits on the old commit: restyle and retext the first h2, insert a palette block, move the h1.
 const all = [...walk(docs[file])]
 const h2 = all.find(e => e.item.props.tag === 'h2' && e.item.props.a?.id === 'pricing-how-heading').item
 const h2text = h2.props.kids.find(k => k.type === 'Txt')
 const main = all.find(e => e.item.props.tag === 'main').item
 let tree = setStyle(docs[file], h2.props.id, 'd', {color: '#7a1f1f'})
 tree = updateProps(tree, h2text.props.id, {text: 'Built around your business.'})
 const block = instantiate(PALETTE.find(p => p.id === 'card').make(), model.docs[file].key, new Set())
 tree = insertNode(tree, block, main.props.id, 'kids', 1)
 const result = migrate({model, pristine}, next, {...docs, [file]: tree})
 assert.deepEqual(result.report.applied, [file])
 const migrated = result.docs[file]
 const flat = [...walk(migrated)].map(e => e.item)
 const newH2 = flat.find(e => e.props.tag === 'h2' && e.props.a?.id === 'pricing-how-heading')
 assert.equal(newH2.props.st.d.color, '#7a1f1f', 'owner style carried')
 assert.ok(flat.some(e => e.type === 'Txt' && e.props.text === 'Built around your business.'), 'owner text wins over the source rewording')
 assert.ok(flat.some(e => e.props.id === block.props.id), 'owner insertion carried')
 assert.ok(flat.some(e => e.props.cls === 'source-added'), 'source addition kept')
 const out = generate(next.pristine, files, {...docsOf(next.model), [file]: migrated})
 tsx(out[file])
 assert.ok(out[file].includes('Added by the source') && out[file].includes('Built around your business.') && out[file].includes('Card title'))
 assert.ok(result.report.added_by_source >= 1 && Array.isArray(result.report.conflicts))
})
