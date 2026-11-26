// Stage 2: section instances with stable IDs, safe links and the real-site adapter. No model access.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import React from 'react'
import { renderToStaticMarkup, renderToString } from 'react-dom/server'
import parse from 'html-react-parser'
import { initialData, snapshot, validateData, applyProposal, exportDraft, validateEnvelope, saveDraft, loadDraft, fingerprint, DRAFT_VERSION } from '../src/studio/state.js'
import { migrationSpec } from '../src/studio/migrate.js'
import { sectionTypes, typeOf, allocateId, anchorMap, pageAnchors, linkProblem, canAdd, canRemove, instanceLabel, ID_PATTERN, isOriginal } from '../src/studio/sections.js'
import { proposalViolations, designViolations } from '../src/studio/brand.js'
import path from 'node:path'
import { build } from 'esbuild'
// site-tree.jsx is bundled exactly as scripts/studio-site-fixture.mjs bundles it for the website.
// Written under node_modules/.cache so the bundle resolves this checkout's React (never the website's).
fs.mkdirSync(new URL('../node_modules/.cache', import.meta.url), { recursive: true })
const bundleDir = fs.mkdtempSync(path.join(new URL('../node_modules/.cache', import.meta.url).pathname, 'ttros-studio-stage2-test-'))
const bundled = await build({ entryPoints: [new URL('../src/studio/site-tree.jsx', import.meta.url).pathname], bundle: true, write: false, platform: 'node', format: 'esm', external: ['react'], jsx: 'automatic' })
fs.writeFileSync(path.join(bundleDir, 'site-tree.mjs'), bundled.outputFiles[0].text)
const { adaptHomeChildren } = await import(path.join(bundleDir, 'site-tree.mjs'))
fs.rmSync(bundleDir, { recursive: true, force: true })

const A = 'how-we-work-aaaaaa', B = 'how-we-work-bbbbbb'
const twoCopies = () => ({ edits: [{ section: B, field: 'text1', text: 'Two ways to start.' }, { section: B, field: 'link0', text: '#how-it-works' }],
  sections: [{ op: 'add', type: 'Section4', id: A, place: 'after', ref: 'Section0' }, { op: 'duplicate', source: A, id: B }],
  styles: [{ section: B, target: 'section', values: { background: '#d8d0c2' } }] })
const ids = data => data.content.map(i => i.props.id)
// A genuine Stage 1.5 draft in the c58e641 schema (no link fields, flat hero graphic text).
const legacy = () => structuredClone(JSON.parse(fs.readFileSync(new URL('./fixtures/studio-v4/home-v2-flat.json', import.meta.url))).data)

test('registry: every real component, explicit limits, original instance IDs preserved', () => {
  assert.deepEqual(sectionTypes.filter(s => s.page === 'home'&&!s.nested).map(s => s.type), snapshot.sections.map(s => s.type)) // Stage 3: plus service pages
  assert.deepEqual([typeOf('Section0').min, typeOf('Section0').max, typeOf('Section7').max], [1, 1, 1])
  assert.deepEqual(ids(initialData), snapshot.sections.map(s => s.type))
  for (const s of sectionTypes) if (/<svg[^]*\sid="/.test(s.html)) assert.equal(s.max, 1, `${s.type} has SVG IDs and must not be duplicated`)
})

test('Stage 1.5 drafts migrate: same IDs, text, order, styles; links take original destinations', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  const old = legacy(); old.content.reverse(); old.content[0].props.styles = { section: { background: '#ece9e2' } }
  const env = { version: 2, project: 'default-site', source: migrationSpec.from.manifests['source-manifest.json'], data: old }
  const data = validateEnvelope(env)
  // Same IDs and order; the new source HeroSignature is inserted right after the hero.
  assert.deepEqual(ids(data).filter(id => id !== 'HeroSignature'), ids(old))
  assert.equal(ids(data)[ids(data).indexOf('Section0') + 1], 'HeroSignature')
  assert.deepEqual(data.content[0].props.styles, { section: { background: '#ece9e2' } })
  assert.equal(data.content.find(i => i.type === 'Section0').props.link0, '/free-assessment')
  assert.equal(exportDraft(data).version, DRAFT_VERSION)
  let raw = JSON.stringify(env); const storage = { getItem: () => raw, setItem: (k, v) => { raw = v } }
  assert.deepEqual(loadDraft(storage), data); saveDraft(storage, data); assert.equal(JSON.parse(raw).version, 5); assert.deepEqual(loadDraft(storage), data)
})

test('add, duplicate and edit only the copy; move and remove independently', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  const next = applyProposal(initialData, twoCopies())
  assert.deepEqual(ids(next).slice(0, 3), ['Section0', A, B])
  const [a, b] = [next.content[1].props, next.content[2].props]
  assert.equal(a.text1, 'Orchid iris kelp ember basil zephy'); assert.equal(b.text1, 'Two ways to start.')
  assert.equal(a.link0, '/how-we-work'); assert.equal(b.link0, '#how-it-works')
  assert.deepEqual(a.styles, {}); assert.deepEqual(b.styles, { section: { background: '#d8d0c2' } })
  const order = ids(next); [order[1], order[5]] = [order[5], order[1]]
  const moved = applyProposal(next, { edits: [], order })
  assert.equal(ids(moved)[5], A); assert.equal(ids(moved)[2], B)
  const removed = applyProposal(moved, { edits: [], sections: [{ op: 'remove', id: A }] })
  assert.ok(!ids(removed).includes(A) && ids(removed).includes(B))
  assert.throws(() => applyProposal(moved, { edits: [], sections: [{ op: 'remove', id: A }] }, { allowRemove: false }), /manual action/)
  assert.equal(instanceLabel(next.content, B), 'How we work (2 of 3)') // document order: Section0, A, B, …, Section4
  assert.equal(instanceLabel(next.content, 'Section1'), 'Delta bravo jade embe')
})

test('invalid section operations and limits are refused with reasons', () => {
  const bad = [[{ op: 'add', type: 'Section0', id: 'hero-2' }, /exactly one hero/], [{ op: 'add', type: 'Section7', id: 'faq-2' }, /FAQ structured data/],
    [{ op: 'duplicate', source: 'Section0', id: 'hero-copy' }, /exactly one hero/], [{ op: 'remove', id: 'Section0' }, /required/],
    [{ op: 'add', type: 'Section4', id: 'Section1' }, /new, unique ID/], [{ op: 'add', type: 'Section4', id: '9x' }, /new, unique ID/],
    [{ op: 'add', type: 'Nope', id: 'nope-1' }, /Unknown section type/], [{ op: 'add', type: 'Section4', id: 'ok-1', place: 'after', ref: 'x' }, /insertion point/],
    [{ op: 'add', type: 'Section4', id: 'ok-1', place: 'end', ref: 'Section0' }, /end insertion/], [{ op: 'add', type: 'Section4', id: 'ok-1', html: '<b>' }, /Unsupported/],
    [{ op: 'duplicate', source: 'x', id: 'ok-1' }, /duplicate/], [{ op: 'remove', id: 'x' }, /Unknown section/], [null, /Invalid section operation/]]
  for (const [op, reason] of bad) assert.throws(() => applyProposal(initialData, { edits: [], sections: [op] }), reason, JSON.stringify(op))
  assert.throws(() => applyProposal(initialData, { edits: [], sections: Array.from({ length: 21 }, (_, i) => ({ op: 'add', type: 'Section5', id: `w-${i}` })) }), /section operations/)
  assert.equal(canAdd(initialData.content, 'Section0'), typeOf('Section0').limitReason)
  assert.equal(canAdd(initialData.content, 'Section5'), null)
  assert.match(canRemove(initialData.content, 'Section0'), /exactly one hero/)
  const linked = applyProposal(initialData, twoCopies())
  assert.match(canRemove(linked.content, 'Section4'), new RegExp(`\\(${B}\\) links to #how-it-works`))
  assert.throws(() => applyProposal(linked, { edits: [], sections: [{ op: 'remove', id: 'Section4' }] }), /#how-it-works is not on this page/)
})

test('stable IDs survive JSON export/import, fingerprints and repeated validation; allocation is fresh', () => {
  const next = applyProposal(initialData, twoCopies())
  const round = validateEnvelope(JSON.parse(JSON.stringify(exportDraft(next))))
  assert.deepEqual(round, next); assert.equal(fingerprint(round), fingerprint(next))
  const taken = new Set(ids(next))
  for (let i = 0; i < 200; i++) { const id = allocateId('Section4', taken); assert.ok(ID_PATTERN.test(id) && id.startsWith('how-we-work-') && !taken.has(id)); taken.add(id) }
  let calls = 0
  const stuck = n => { calls++; return calls < 3 ? new Uint8Array(n) : Uint8Array.from({ length: n }, (_, i) => i + 1) }
  assert.equal(allocateId('Section4', ['how-we-work-aaaaaa'], stuck), 'how-we-work-bcdefg')
  assert.equal(calls, 3, 'collision retried with a new suffix')
})

test('anchors: originals keep component anchors, copies are scoped; page IDs stay unique', () => {
  const next = applyProposal(initialData, twoCopies())
  assert.deepEqual(anchorMap(next.content[0]), { 'home-title': 'home-title', 'workflow-caption': 'workflow-caption' })
  assert.deepEqual(anchorMap(next.content[1]), { 'how-it-works': `how-it-works-${A}` })
  assert.ok(isOriginal(next.content.find(i => i.props.id === 'Section4')) && !isOriginal(next.content[1]))
  const all = next.content.flatMap(i => Object.values(anchorMap(i)))
  assert.equal(new Set(all).size, all.length)
  const anchors = pageAnchors(next.content)
  for (const [href, ok] of [['/about', true], [`#how-it-works-${B}`, true], ['#how-it-works', true], ['/#final-cta', true], ['#main-content', true], ['/pricing#starting-points', true],
    ['/blog/how-to-find-ai-fast-starts', true], ['https://example.com/x?y=1', true], ['mailto:hello@core-site.example', true],
    ['javascript:alert(1)', false], ['//evil.example', false], ['http://example.com', false], ['/nope', false], ['#nowhere', false], ['https://u:p@x.com', false], ['', false], ['/about\n', false]])
    assert.equal(linkProblem(href, anchors) === null, ok, href)
  assert.throws(() => applyProposal(initialData, { edits: [{ section: 'Section0', field: 'link0', text: 'javascript:alert(1)' }] }), /Hero link 1/)
})

test('brand: types resolve through instances, including added ones and duplicated overrides', () => {
  const rules = { colours: ['#D8D0C2', '#ECE9E2', '#111315'], fonts: ['Geist', 'Inter'], ranges: { buttonRadius: [6, 10] }, exceptions: [],
    no_fill: { tokens: [], colours: [], reason: '' }, not_core: { tokens: [], colours: [], reason: '' } }
  const add = { edits: [], sections: [{ op: 'add', type: 'Section5', id: 'proof' }], styles: [{ section: 'proof', target: 'a-0', values: { radius: 30 } }] }
  assert.deepEqual(proposalViolations(add, rules, initialData).map(v => v.token), ['proof.a-0.buttonRadius'])
  const styled = applyProposal(initialData, { edits: [], styles: [{ section: 'Section8', target: 'section', values: { background: '#ff00ff' } }] })
  const dup = { edits: [], sections: [{ op: 'duplicate', source: 'Section8', id: 'cta-2' }] }
  assert.deepEqual(proposalViolations(dup, rules, styled).map(v => v.token), ['cta-2.section.background'])
  assert.deepEqual(designViolations(applyProposal(styled, dup), rules).filter(v => v.token.includes('.')).map(v => v.token), ['Section8.section.background', 'cta-2.section.background'])
})

// The adapter applied to a website tree built from the original component markup (the real
// Next build is proven separately by scripts/studio-build-proof.mjs).
const fill = (html, texts) => html.replace(/<studio-text data-field="(text\d+)"><\/studio-text>/g, (_, k) => texts[k].replace(/&/g, '&amp;').replace(/</g, '&lt;'))
function websiteTree() {
  const sections = snapshot.sections.map(s => parse(fill(s.html.replace('<figure class="workflow-figure"','<figure data-studio-component="WorkflowFigure" class="workflow-figure"'), s.texts)))
  const JsonLd = () => null
  const faq = snapshot.sections.find(s => s.type === 'Section7').texts
  const graph = { '@graph': [{ '@type': 'Organization' }, { '@type': 'FAQPage', mainEntity: [0, 1, 2, 3, 4, 5].map(i => ({ name: faq[`text${i * 2 + 1}`], acceptedAnswer: { text: faq[`text${i * 2 + 2}`] } })) }] }
  return [React.createElement(JsonLd, { key: 'ld', data: graph }), ...sections.map((s, i) => React.cloneElement(s, { key: i }))]
}
test('real-site adapter renders every instance from its template with its own text, links, styles and anchors', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  const data = applyProposal(initialData, twoCopies())
  const out = adaptHomeChildren(websiteTree(), data, [])
  const html = renderToStaticMarkup(React.createElement(React.Fragment, null, out))
  const sections = [...html.matchAll(/<(?:section|div)[^>]*data-studio-section="([^"]+)"[^>]*data-studio-type="([^"]+)"/g)].map(m => [m[1], m[2]])
  assert.deepEqual(sections.map(([id]) => id), ids(data))
  assert.equal(sections.filter(([, type]) => type === 'Section4').length, 3)
  const block = id => { const at = html.indexOf(`data-studio-section="${id}"`); return html.slice(html.lastIndexOf('<section', at), html.indexOf('</section>', at)) }
  assert.match(block(B), /Two ways to start\./); assert.match(block(B), /href="#how-it-works"/); assert.match(block(B), /background-color:#d8d0c2/)
  assert.match(block(A), /From bottleneck to working system\./); assert.match(block(A), /href="\/how-we-work"/); assert.doesNotMatch(block(A), /background-color/)
  assert.match(html, new RegExp(`id="how-it-works-${B}"`)); assert.equal(html.match(/id="how-it-works"/g).length, 1)
  const noFaq = applyProposal(initialData, { edits: [], sections: [{ op: 'remove', id: 'Section7' }] })
  const ld = adaptHomeChildren(websiteTree(), noFaq, []).props.children[0].find(n => n.props?.data)
  assert.deepEqual(ld.props.data['@graph'].map(e => e['@type']), ['Organization'])
  const tree = websiteTree(), at = snapshot.sections.findIndex(s => s.type === 'Section4'); tree[at + 1] = parse(fill(snapshot.sections[at].html.replace('href="/how-we-work"', 'href="/somewhere-else"'), snapshot.sections[at].texts))
  assert.throws(() => adaptHomeChildren(tree, data, []), /link drift at Section4/)
})

test('zero-AI: section model, adapter and manual handlers have no model or network path', () => {
  for (const file of ['sections.js', 'state.js', 'site-tree.jsx', 'config.jsx', 'brand.js', 'fields.jsx']) {
    const body = fs.readFileSync(new URL(`../src/studio/${file}`, import.meta.url), 'utf8')
    assert.ok(!/fetch\(|requestProposal|\/propose|XMLHttpRequest/.test(body), file)
  }
  const view = fs.readFileSync(new URL('../src/views/WebsiteStudio.jsx', import.meta.url), 'utf8')
  assert.equal(view.match(/requestProposal\(/g).length, 1)
  const panel = view.slice(view.indexOf('function SectionsPanel'), view.indexOf('function LinkFields'))
  const links = view.slice(view.indexOf('function LinkFields'), view.indexOf('function Actions'))
  for (const part of [panel, links]) assert.ok(!/studioApi|requestProposal|\bpropose\(|fetch\(/.test(part))
  assert.match(view, /onClick=\{propose\}/)
})

test('unchanged text keeps the source text-node segments; edited text replaces them', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  // The real source renders `{label} <Arrow/>` as two text nodes; split them as React would.
  const split = node => !React.isValidElement(node) ? node : React.cloneElement(node, {}, ...React.Children.toArray(node.props.children).flatMap(child =>
    typeof child === 'string' && child.endsWith(' ') && child.trim() ? [child.slice(0, -1), ' '] : [split(child)]))
  const tree = websiteTree().map(split)
  // renderToString keeps text-node separators, as Next's server rendering does.
  const html = data => renderToString(React.createElement(React.Fragment, null, adaptHomeChildren(tree, data, [])))
  assert.match(html(initialData), /See how we work<!-- --> <svg/)
  assert.match(html(applyProposal(initialData, { edits: [{ section: 'Section0', field: 'text5', text: 'How we work ' }] })), /data-studio-element="a-1">How we work <svg/)
})
