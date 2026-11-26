// Schema v5 (website 215679e) migration of saved c58e641 drafts. Deterministic; no model access.
// Fixtures in fixtures/studio-v4 were produced by the c58e641-pinned Studio code itself (3e8f542).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { build } from 'esbuild'
import { migrateEnvelope, migrationSpec as spec } from '../src/studio/migrate.js'
import { validateEnvelope, validateData, exportDraft, applyProposal, initialDataFor, snapshot, DRAFT_VERSION, loadDraft, saveDraft } from '../src/studio/state.js'
import { typeOf } from '../src/studio/sections.js'
import { defaultGraphic } from '../src/studio/workflow-graphic.js'
import shared from '../src/studio/shared.json' with { type: 'json' }

const fixtures = new URL('./fixtures/studio-v4/', import.meta.url)
const fixture = name => JSON.parse(fs.readFileSync(new URL(`${name}.json`, fixtures)))
const FIXTURES = fs.readdirSync(fixtures).map(f => f.replace(/\.json$/, ''))
const ids = data => data.content.map(i => i.props.id)
const proofs = new URL('../../../proofs/', import.meta.url)
// Every Studio draft committed as evidence by earlier stages (v2–v4, every page).
function historical() {
 const out = []
 for (const dir of fs.readdirSync(proofs)) for (const f of fs.readdirSync(new URL(`${dir}/`, proofs))) {
  if (!f.endsWith('.json')) continue
  let value; try { value = JSON.parse(fs.readFileSync(new URL(`${dir}/${f}`, proofs))) } catch { continue }
  const env = value?.envelope ?? value
  if (env && typeof env === 'object' && 'version' in env && Array.isArray(env.data?.content)) out.push([`${dir}/${f}`, env])
 }
 return out
}

test('the spec is consistent with both snapshots: destinations exist, unchanged types are byte-identical', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
 assert.equal(spec.version, DRAFT_VERSION)
 assert.equal(spec.to.commit, snapshot.commit)
 for (const [type, rule] of Object.entries(spec.types)) {
  const section = typeOf(type)
  for (const to of Object.values(rule.texts)) assert.ok(Object.hasOwn(section.texts, to), `${type}.${to}`)
  for (const to of Object.values(rule.links)) assert.match(to, /^link\d+$/)
  for (const to of Object.values(rule.styles)) assert.ok(to === 'section' || /^[a-z0-9]+-\d+$/.test(to))
 }
 const figure = typeOf('WorkflowFigure')
 for (const to of Object.values(spec.components.WorkflowFigure.texts).flat()) assert.ok(Object.hasOwn(figure.texts, to), to)
 // spec.unchanged hashes (Python json.dumps sort_keys) are verified byte-exactly in tests/test_website_studio_migration.py.
 for (const type of Object.keys(spec.unchanged)) assert.ok(typeOf(type) || shared.sections.some(s => s.type === type), type)
 for (const rule of spec.insert) assert.ok(typeOf(rule.type) && typeOf(rule.type).page === rule.page, rule.type)
})

test('every genuine fixture and historical proof draft migrates deterministically, purely and idempotently', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
 const corpus = [...FIXTURES.map(name => [name, fixture(name)]), ...historical()]
 assert.ok(corpus.length >= 30, `corpus has ${corpus.length} drafts`)
 for (const [name, env] of corpus) {
  const bytes = JSON.stringify(env)
  const first = migrateEnvelope(env), second = migrateEnvelope(env)
  assert.deepEqual(first, second, `${name} deterministic`)
  assert.equal(JSON.stringify(env), bytes, `${name} input untouched`)
  assert.equal(first.envelope.version, DRAFT_VERSION)
  const data = validateEnvelope(env)
  assert.deepEqual(migrateEnvelope(first.envelope), { envelope: first.envelope, report: null }, `${name} idempotent`)
  assert.deepEqual(validateEnvelope(exportDraft(data, env.page ?? 'home')), data, `${name} round trip`)
  // Every saved instance survives with its ID and relative order; only new source sections are added.
  const before = env.data.content.map(i => i.props.id), after = ids(data)
  assert.deepEqual(after.filter(id => before.includes(id)), before, `${name} IDs/order`)
  assert.deepEqual(after.filter(id => !before.includes(id)), first.report.inserted.map(i => i.id), `${name} insertions`)
 }
})

test('owner edits carry to the field with the same meaning; untouched text takes the new source', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
 const env = fixture('home-edited'), { report } = migrateEnvelope(env), data = validateEnvelope(env)
 const hero = data.content.find(i => i.type === 'Section0'), child = hero.props.workflow[0].props
 const fresh = initialDataFor('home').content[0]
 assert.equal(hero.props.text0, 'Orchid basil indigo quartz summit kelp dune yonder jade', 'new eyebrow from source')
 assert.equal(hero.props.text2, 'Not your overtime.', 'edited headline line 2 (old text1 → text2)')
 assert.equal(hero.props.text1, fresh.props.text1, 'untouched line 1 is the source text')
 assert.deepEqual(hero.props.styles, { 'p-1': { text: '#2f2f2a' } }, 'lead paragraph style follows the lead (p-0 → p-1)')
 assert.deepEqual([child.titleLine1, child.titleLine2], ['From first call to signed quote', ''], 'edited caption becomes the title')
 assert.equal(child.step2Title, 'Name the owner')
 assert.equal(child.step1Title, 'Yonder indigo delta yon', 'untouched old step takes the new source step')
 assert.equal(child.step2Text, fresh.props.workflow[0].props.step2Text)
 assert.deepEqual(child.styles, { section: { paddingBlock: 40 } })
 assert.deepEqual(child.graphic, { ...defaultGraphic, width: 80, horizontal: 'end' })
 assert.deepEqual(data.root.props.unsupported, [{ path: 'Section0-workflow.graphic.signal', value: '#527a65', reason: spec.components.WorkflowFigure.graphic.reason }])
 assert.equal(data.content.find(i => i.props.id === 'Section3').props.text0, 'We build the systems that keep you moving.', 'unchanged section keeps its edit')
 assert.deepEqual(ids(data), ['Section0', 'HeroSignature', 'Section1', 'Section2', 'outcomes-legacy1', 'Section3', 'Section4', 'Section6', 'Section7', 'Section8', 'Section5'])
 assert.deepEqual(report.carried.map(c => `${c.id}.${c.from}`), ['Section0.text1', 'Section0-workflow.caption', 'Section0-workflow.step2'])
 const ai = validateEnvelope(fixture('service-ai-edited'))
 assert.deepEqual(ids(ai), ['ServiceAi0', 'ServiceAi1', 'help-legacy1', 'ServiceAi2', 'ServiceAi3', 'ServiceAi5', 'ServiceAi4'])
 assert.match(ai.content[0].props.text2, /^We find where AI helps/)
 assert.deepEqual(ai.content[0].props.styles, { 'p-1': { fontSize: 22 } })
 assert.equal(ai.content[0].props.text3, initialDataFor('service-ai').content[0].props.text3, 'new local introduction from source')
 assert.deepEqual(validateEnvelope(fixture('service-process-untouched')), initialDataFor('service-process'), 'an untouched old draft equals the new source page')
 assert.deepEqual(validateEnvelope(fixture('home-untouched')), initialDataFor('home'))
 assert.deepEqual(validateEnvelope(fixture('site-untouched')), initialDataFor('site'))
})

test('genuinely unsupported values are retained verbatim, survive edits and exports, and are never rendered', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, async () => {
 const env = fixture('home-image'), image = env.data.content[0].props.workflow[0].props.graphic.asset
 const data = validateEnvelope(env)
 assert.deepEqual(data.root.props.unsupported.map(u => u.path), ['Section0-workflow.graphic.source', 'Section0-workflow.graphic.asset', 'Section0-workflow.graphic.alt', 'Section0-workflow.graphic.fit'])
 assert.deepEqual(data.root.props.unsupported[1].value, image)
 const edited = applyProposal(data, { edits: [{ section: 'Section0-workflow', field: 'after', text: 'Fewer missed opportunities.' }] })
 assert.deepEqual(edited.root.props.unsupported, data.root.props.unsupported)
 assert.deepEqual(validateEnvelope(exportDraft(edited)), edited)
 let raw; const storage = { getItem: () => raw, setItem: (_, v) => { raw = v } }
 saveDraft(storage, edited); assert.deepEqual(loadDraft(storage), edited)
 for (const bad of [[{ path: '', value: 1, reason: 'x' }], [{ path: 'a', value: 1 }], Array(21).fill({ path: 'a', value: 1, reason: 'x' }), [{ path: 'a', value: 'x'.repeat(400001), reason: 'x' }], {}])
  assert.throws(() => validateData({ ...edited, root: { props: { ...edited.root.props, unsupported: bad } } }), /retained/)
 const pre = fixture('home-untouched'); pre.data.root.props.unsupported = []
 assert.throws(() => migrateEnvelope(pre), /cannot carry retained/)
 // The real-site adapter never renders retained values.
 const bundled = await build({ entryPoints: [new URL('../src/studio/site-tree.jsx', import.meta.url).pathname], bundle: true, write: false, platform: 'node', format: 'esm', external: ['react'], jsx: 'automatic' })
 const dir = fs.mkdtempSync(path.join(new URL('../node_modules/.cache', import.meta.url).pathname, 'ttros-studio-migration-test-'))
 fs.writeFileSync(path.join(dir, 'site-tree.mjs'), bundled.outputFiles[0].text)
 const { adaptHomeChildren } = await import(path.join(dir, 'site-tree.mjs'))
 fs.rmSync(dir, { recursive: true, force: true })
 const { default: parse } = await import('html-react-parser')
 const fill = (html, texts) => html.replace(/<studio-text data-field="(text\d+)"><\/studio-text>/g, (_, k) => texts[k].replace(/&/g, '&amp;').replace(/</g, '&lt;'))
 const tree = snapshot.sections.map((s, i) => React.cloneElement(parse(fill(s.html.replace('<figure class="workflow-figure"', '<figure data-studio-component="WorkflowFigure" class="workflow-figure"'), s.texts)), { key: i }))
 const html = renderToStaticMarkup(React.createElement(React.Fragment, null, adaptHomeChildren(tree, edited, [])))
 assert.doesNotMatch(html, /data:image|workflow\.png|Inquiry workflow sketch/)
 assert.match(html, /Fewer missed opportunities\./)
 // An edited old caption becomes title line 1 with an empty line 2; the real adapter renders it.
 const captioned = validateEnvelope(fixture('home-edited'))
 const figure = renderToStaticMarkup(React.createElement(React.Fragment, null, adaptHomeChildren(snapshot.sections.map((s, i) => React.cloneElement(parse(fill(s.html.replace('<figure class="workflow-figure"', '<figure data-studio-component="WorkflowFigure" class="workflow-figure"'), s.texts)), { key: i })), captioned, [])))
 assert.match(figure, /<strong>From first call to signed quote<br\/><\/strong>/)
 assert.match(figure, /Name the owner/)
})

test('drafts that cannot be migrated safely fail closed with a reason, leaving the stored bytes untouched', () => {
 const cases = [
  [e => { e.version = 0 }, /version 0 is not supported/],
  [e => { e.source.commit = 'stale' }, /does not match/],
  [e => { e.project = 'another-client' }, /does not match/],
  [e => { e.data.content.push({ type: 'Section99', props: { id: 'x1' } }) }, /Section99 is not part of the c58e641 schema/],
  [e => { e.data.content[0].props.text77 = 'x' }, /text77 has no destination/],
  [e => { delete e.data.content[0].props.workflow }, /incomplete|exactly one workflow/],
  [e => { e.data.content[0].props.workflow[0].props.extra = 1 }, /extra has no destination/],
  [e => { e.data.content[0].props.styles = { 'h2-9': { text: '#000000' } } }, /h2-9, which has no destination/],
  [e => { e.data.content[2].props.id = 'HeroSignature' }, /reserved for the new source section/],
 ]
 for (const [mutate, message] of cases) {
  const env = fixture('home-untouched'); mutate(env)
  const bytes = JSON.stringify(env)
  assert.throws(() => validateEnvelope(env), message)
  assert.equal(JSON.stringify(env), bytes)
 }
 const service = fixture('service-ai-edited'); service.version = 2
 assert.throws(() => migrateEnvelope(service), /does not match/, 'service pages never had v1/v2 drafts')
})
