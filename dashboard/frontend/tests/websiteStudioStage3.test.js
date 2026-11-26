// Stage 3: real multi-page editing. Page registry, page-scoped types and validation, per-page
// envelopes and browser drafts, cross-page link checks, SEO/structured data from the page model,
// page-scoped site CSS, the Hero contrast fix and the editor wiring. No model access.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { initialData, initialDataFor, validateData, applyProposal, exportDraft, validateEnvelope, envelopePage, loadDraft, saveDraft, draftKey, DRAFT_KEY } from '../src/studio/state.js'
import { pageIds, pageInfo, pageTypes, typeOf, linkProblem, pageAnchors, canAdd, studioPages } from '../src/studio/sections.js'
import { designCss, scopeCss, defaultDesign } from '../src/studio/design.js'
import { contrastChecks, contrastFailures } from '../src/studio/contrast.js'
import { pageSeo, seoProblems, siteLinkReport, pageLinks, linkTextFields } from '../src/studio/seo.js'
import services from '../src/studio/services.json' with { type: 'json' }
import homeManifest from '../src/studio/source-manifest.json' with { type: 'json' }
import serviceManifest from '../src/studio/service-manifest.json' with { type: 'json' }

const memory = () => { const m = new Map(); return { getItem: k => m.has(k) ? m.get(k) : null, setItem: (k, v) => m.set(k, String(v)), keys: () => [...m.keys()] } }
const SERVICE_PAGES = ['service-process', 'service-integration', 'service-ai']
const edit = (page, proposal) => applyProposal(initialDataFor(page), { edits: [], ...proposal })

test('Hero emphasis line keeps the site colour; owner values and overrides stay checked', () => {
  assert.equal(contrastFailures(initialData).length, 0)
  assert.doesNotMatch(designCss(defaultDesign), /home-hero h1 > span/)
  const hero = contrastChecks(initialData).find(c => c.id === 'Section0:text2')
  assert.deepEqual([hero.foreground.hex, hero.foreground.token, hero.pass], ['#675638', null, true])
  const owner = applyProposal(initialData, { edits: [], design: { accent: '#c6a15b' }, styles: [{ section: 'Section0', target: 'h1-0', values: { text: '#f7f3ea' } }] })
  assert.equal(owner.root.props.design.accent, '#c6a15b', 'genuine owner token kept')
  assert.deepEqual(owner.content[0].props.styles, { 'h1-0': { text: '#f7f3ea' } }, 'genuine owner override kept')
  assert.deepEqual(contrastFailures(owner).map(f => f.id), ['Section0:text1'], 'and still checked (Stage 2.1 protection)')
})

test('five Studio pages with stable IDs, real routes and their own section types', () => {
  assert.deepEqual(pageIds, ['home', 'service-process', 'service-integration', 'service-ai', 'test-offer'])
  assert.deepEqual(pageIds.map(id => pageInfo(id).route), ['/', '/services/business-process-automation', '/services/systems-integration', '/services/ai-implementation', '/offers/test-workflow-audit'])
  assert.equal(draftKey('home'), DRAFT_KEY, 'homepage keeps its Stage 1 browser key')
  // 215679e: the hero signature, and each service page's "Dune cedar xenon yonde" (index 5, before Contact).
  assert.deepEqual(pageIds.map(id => pageTypes(id).length), [10, 6, 6, 6, 6])
  assert.deepEqual(pageTypes('service-ai').map(s => s.type), ['ServiceAi0', 'ServiceAi1', 'ServiceAi2', 'ServiceAi3', 'ServiceAi5', 'ServiceAi4'])
  assert.equal(services.commit, homeManifest.commit)
  assert.equal(serviceManifest.files['content/services.ts'], homeManifest.files['content/services.ts'])
  for (const [id, related] of [['service-process', 3], ['service-integration', 2], ['service-ai', 4]]) {
    const measures = pageTypes(id)[3]
    assert.equal(services.pages[id].related.length, related)
    assert.deepEqual(measures.links, ['/how-we-work', ...services.pages[id].related, '/services'])
    assert.equal(initialDataFor(id).content.map(i => i.props.id).join(), pageTypes(id).map(s => s.type).join(), 'original instance IDs are the type names')
    assert.deepEqual([typeOf(pageTypes(id)[0].type).min, typeOf(pageTypes(id)[0].type).max], [1, 1])
  }
})

test('content, operations and proposals never cross pages', () => {
  assert.throws(() => validateData({ ...initialData, content: [...initialData.content, initialDataFor('service-ai').content[1]] }), /belongs to another page/)
  assert.throws(() => validateData(initialDataFor('service-ai'), 'home'), /belongs to another page/)
  assert.throws(() => edit('service-ai', { sections: [{ op: 'add', type: 'Section4', id: 'how-we-work-abc234' }] }), /belongs to another page/)
  assert.throws(() => edit('service-ai', { edits: [{ section: 'Section0', field: 'text0', text: 'x' }] }), /unsupported field/)
  assert.equal(canAdd(initialDataFor('service-ai').content, 'Section1'), 'Where work gets stuck belongs to another page.')
  assert.match(canAdd(initialDataFor('service-ai').content, 'ServiceAi0'), /exactly one intro/)
  const copy = edit('service-integration', { sections: [{ op: 'duplicate', source: 'ServiceIntegration2', id: 'what-we-build-abc234' }] })
  assert.equal(copy.content.length, 7)
})

test('envelopes: homepage shape unchanged; service pages name their page and source manifest', () => {
  const home = exportDraft(initialData)
  assert.deepEqual(Object.keys(home), ['version', 'project', 'source', 'data'])
  assert.deepEqual(home.source, homeManifest)
  const ai = exportDraft(initialDataFor('service-ai'))
  assert.deepEqual([ai.version, ai.page, ai.source], [5, 'service-ai', serviceManifest])
  assert.equal(envelopePage(home), 'home'); assert.equal(envelopePage(ai), 'service-ai')
  assert.deepEqual(validateEnvelope(ai, 'service-ai'), initialDataFor('service-ai'))
  assert.throws(() => validateEnvelope(ai, 'service-process'), /different Studio page/)
  assert.throws(() => validateEnvelope({ ...ai, source: homeManifest }), /does not match/)
  assert.throws(() => validateEnvelope({ ...home, page: 'home' }), /does not match/)
  // A pre-v5 label is only accepted with its own c58e641 source; genuine old drafts migrate (migration tests).
  assert.throws(() => validateEnvelope({ ...home, version: 2 }), /does not match/)
})

test('browser drafts are independent per page', () => {
  const storage = memory()
  const process = edit('service-process', { edits: [{ section: 'ServiceProcess1', field: 'text0', text: 'When automation can help.' }] })
  saveDraft(storage, process, 'service-process')
  saveDraft(storage, edit('service-ai', { design: { headingFont: 'Georgia' } }))
  assert.deepEqual(storage.keys().sort(), ['ttros-studio:global:default-site:service-ai:v1', 'ttros-studio:global:default-site:service-process:v1'])
  assert.deepEqual(loadDraft(storage, 'service-process'), process)
  assert.equal(loadDraft(storage, 'service-ai').root.props.design.headingFont, 'Georgia')
  assert.deepEqual(loadDraft(storage, 'home'), initialData, 'the homepage takes nothing from the service pages')
  assert.equal(loadDraft(storage, 'service-integration').root.props.design.headingFont, defaultDesign.headingFont)
})

test('links are validated against the page they point into', () => {
  const route = pageInfo('service-process').route, anchors = pageAnchors(initialDataFor('service-process').content)
  assert.equal(linkProblem('/#solutions', anchors, route), null)
  assert.equal(linkProblem('/services/systems-integration', anchors, route), null)
  assert.match(linkProblem('/#nowhere', anchors, route), /not on \//)
  assert.match(linkProblem('#how-it-works', anchors, route), /not on this page/)
  assert.match(linkProblem('/services/ai-implementation#x', anchors, route), /not on \/services\/ai-implementation/)
  assert.equal(linkProblem('#how-it-works', pageAnchors(initialData.content)), null)
  assert.throws(() => edit('service-process', { edits: [{ section: 'ServiceProcess3', field: 'link1', text: '/services/nope' }] }), /not a page on this website/)
  assert.deepEqual(linkTextFields('ServiceAi3'), ['text2', 'text4', 'text5', 'text6', 'text7', 'text8'])
})

test('default SEO equals the original metadata and JSON-LD of every page', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  for (const id of SERVICE_PAGES) {
    const seo = pageSeo(id, initialDataFor(id)), original = services.pages[id].seo
    assert.deepEqual([seo.title, seo.description, seo.canonical, seo.openGraph.url], [original.title, original.description, original.canonical, original.openGraph.url])
    assert.equal(seo.documentTitle, `${original.title} | Example Company`)
    assert.deepEqual(seo.jsonLd, original.jsonLd)
    assert.deepEqual(seoProblems(id, initialDataFor(id)), { seo, errors: [], warnings: [] })
  }
  assert.deepEqual(pageSeo('home', initialData).jsonLd, services.homeSeo.jsonLd)
})

test('SEO follows the visible service name and description; URL and IDs never change', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
  const data = edit('service-integration', { edits: [{ section: 'ServiceIntegration0', field: 'text0', text: 'Systems Integration & Modernisation' }] })
  const seo = pageSeo('service-integration', data), graph = Object.fromEntries(seo.jsonLd['@graph'].map(e => [e['@type'], e]))
  assert.equal(seo.documentTitle, 'Systems Integration & Modernisation in Example City, EX | Example Company')
  assert.deepEqual([graph.WebPage.name, graph.Service.name, graph.Service.serviceType, graph.BreadcrumbList.itemListElement.at(-1).name], Array(4).fill('Systems Integration & Modernisation'))
  assert.deepEqual(seo.jsonLd['@graph'].map(e => e['@id']), services.pages['service-integration'].seo.jsonLd['@graph'].map(e => e['@id']))
  assert.equal(seo.canonical, 'https://www.core-site.example/services/systems-integration')
  const blank = edit('service-ai', { edits: [{ section: 'ServiceAi0', field: 'text2', text: '  ' }] })
  assert.match(seoProblems('service-ai', blank).errors.join(), /description \(Intro lead\) is empty/)
  const long = edit('service-ai', { edits: [{ section: 'ServiceAi0', field: 'text0', text: 'A'.repeat(60) }] })
  assert.match(seoProblems('service-ai', long).warnings.join(), /Page title is 95 characters/)
  const report = siteLinkReport({ home: initialData, 'service-integration': data, 'service-process': initialDataFor('service-process'), 'service-ai': initialDataFor('service-ai') })
  assert.deepEqual(report.pages['service-ai'].incoming.map(l => [l.from, l.text]), [['home', 'Timber kelp velvet timber indigo meado']])
  assert.ok(report.warnings.some(w => w.page === 'service-integration' && /Homepage links to this page as “Xenon ember indigo dune fjord delta”/.test(w.message)))
  // 215679e: the hero signature links to #solutions, so Solutions cannot be removed while it does.
  assert.throws(() => applyProposal(initialData, { edits: [], sections: [{ op: 'remove', id: 'Section3' }] }), /#solutions is not on this page/)
  const orphan = siteLinkReport({ home: applyProposal(initialData, { edits: [], sections: [{ op: 'remove', id: 'HeroSignature' }, { op: 'remove', id: 'Section3' }] }) })
  assert.deepEqual(orphan.warnings.filter(w => /No other Studio page links/.test(w.message)).map(w => w.page), ['service-process', 'service-integration', 'service-ai'])
  // 215679e intro: Discuss (site) + Where to start (#starting-point on this page); then measures (6) and first step (1).
  assert.deepEqual(pageLinks('service-process', initialDataFor('service-process')).map(l => l.kind), ['site', 'anchor', 'site', 'site', 'site', 'site', 'site', 'site', 'site'])
})

test('site CSS: every non-home page design is scoped to its page without raising specificity', () => {
  const css = scopeCss(designCss({ ...defaultDesign, accent: '#7a6236' }), 'service-ai')
  const rules = [...css.matchAll(/(^|[{}])([^{}@]+)\{/g)].map(m => m[2].trim())
  assert.ok(rules.length > 15)
  for (const selector of rules) for (const part of selector.split(',')) assert.ok(part.startsWith(':where(main[data-studio-page="service-ai"])'),part)
  assert.ok(css.includes(':where(main[data-studio-page="service-ai"]){--paper:#f7f3ea'))
  assert.ok(css.includes('@media(prefers-reduced-motion:reduce){:where(main[data-studio-page="service-ai"]) .site-button'))
  assert.throws(() => scopeCss('a{}', 'x"]{}'), /Invalid page scope/)
  const base = designCss(defaultDesign)
  assert.match(base, /\.editorial \.page-lead p\{font-size:clamp\(16\.8px,1\.65vw,20\.8px\)\}/, 'site lead size at default')
  assert.match(base, /\.editorial p\.section-label\{font-size:14px;font-weight:630;line-height:1\.35\}/, 'site label at default')
  assert.match(base, /\.contact-band\{padding-block:clamp\(48px,7vw,100px\)\}/, 'site contact padding at default')
})

test('every service page is fully contrast-modelled and passes AA by default', () => {
  for (const id of SERVICE_PAGES) {
    const checks = contrastChecks(initialDataFor(id))
    const fields = pageTypes(id).reduce((n, s) => n + Object.keys(s.texts).length, 0)
    assert.equal(checks.length, fields, `${id}: every text field`)
    assert.equal(checks.filter(c => !c.pass).length, 0, id)
  }
  const pale = edit('service-ai', { styles: [{ section: 'ServiceAi1', target: 'section', values: { text: '#ece9e2' } }] })
  assert.ok(contrastFailures(pale).some(f => f.id === 'ServiceAi1:text1'), 'bullet text inherits the section override')
})

test('the editor keeps one Puck per page with its own history, drafts, server versions and proposals', () => {
  const view = fs.readFileSync(new URL('../src/views/WebsiteStudio.jsx', import.meta.url), 'utf8')
  assert.match(view, /<Puck key=\{`\$\{page\}:\$\{DRAFT_VERSION\}`\} config=\{config\} data=\{editor\.data\} initialHistory=\{editor\.initialHistory\}/)
  assert.match(view, /<nav className="studio-pages" aria-label="Studio pages">/)
  assert.match(view, /remember\(page,data,\{histories:history\.histories,index:history\.index\}\)/)
  assert.match(view, /saveDraft\(localStorage,data,page\)/)
  assert.match(view, /studioApi\.saveDraft\(site,\{data,base_version:server\.latest[\s\S]*?'Manual Studio edits'\},page\)/)
  assert.match(view, /requestProposal\(\{project:site,page,base_version:server\.available\?server\.latest:null,request,data:base,selection:[\s\S]*?:selection\}\)/)
  assert.match(view, /Object\.assign\(session,\{request,status,proposal,accepted,ack\}\)/)
  assert.match(view, /seoReport\.errors\.length\)\{setTab\('seo'\);setStatus\(`Export blocked/)
  assert.equal((view.match(/requestProposal\(/g) || []).length, 1, 'one explicit model path')
  const api = fs.readFileSync(new URL('../src/studio/api.js', import.meta.url), 'utf8')
  assert.match(api, /drafts: \(site, page = 'home'\) => call\(`\$\{base\(site\)\}\/drafts\$\{pageQuery\(page\)\}`\)/)
  for (const name of ['seo.js', 'state.js', 'sections.js', 'site-tree.jsx', 'config.jsx']) {
    const body = fs.readFileSync(new URL(`../src/studio/${name}`, import.meta.url), 'utf8')
    assert.ok(!body.includes('fetch(') && !body.includes('requestProposal') && !body.includes('/propose'), name)
  }
  assert.equal(studioPages.default, 'home')
})
