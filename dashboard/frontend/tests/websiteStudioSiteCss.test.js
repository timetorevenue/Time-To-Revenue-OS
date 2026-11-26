// Source-first site CSS: an unchanged draft adds nothing visible to the website; deliberate edits do.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { defaultDesign, designCss, siteDesignCss, componentCss, scopeCss } from '../src/studio/design.js'
import { sharedCss, sharedBaseCss, effectiveDesign } from '../src/studio/shared.js'
import { initialDataFor, exportDraft, applyProposal, validateEnvelope } from '../src/studio/state.js'
import { createSiteFixture, PREVIEW_CANDIDATE } from '../scripts/studio-site-fixture.mjs'

// Declarations a stylesheet would apply to the source website (Studio-private custom properties excluded).
const visible = css => [...css.replace(componentCss, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
 .flatMap(([, selector, body]) => body.split(';').filter(Boolean).map(d => `${selector.trim()} ${d}`))
 .filter(d => !/^:root --studio-/.test(d) && !d.startsWith('@media'))

test('defaults add no declaration at all beyond the WorkflowFigure hooks; the canvas stylesheet is unchanged', () => {
 assert.equal(siteDesignCss(defaultDesign), componentCss, 'WorkflowFigure property hooks only (identical to the source at defaults)')
 // A local button appearance reads the Studio-private properties, so they are then defined.
 assert.match(siteDesignCss(defaultDesign, defaultDesign, { privateVars: true }), /^:root\{--studio-body-family:[^}]*--studio-primary:#111315/)
 assert.deepEqual(visible(siteDesignCss(defaultDesign, defaultDesign, { privateVars: true })), [])
 assert.match(designCss(defaultDesign), /\.editorial h1\{font-family/, 'the canvas keeps its full stylesheet')
})

test('a deliberate token change emits exactly its declarations and their dependants', () => {
 assert.deepEqual(visible(siteDesignCss({ ...defaultDesign, h1Size: 90 })), ['.editorial h1 font-size:clamp(48px,5.561320754716981vw,90px)'])
 const accent = visible(siteDesignCss({ ...defaultDesign, accent: '#527a65' }))
 assert.deepEqual(accent, [':root --gold:#527a65', '.editorial-link .site-arrow,.service-row .site-arrow color:var(--gold)', '.section-label::before,.process-line li::before background:var(--gold)', '.feature-link border-left-color:var(--gold)'])
 // The source fills buttons with --ink; Studio's Primary keeps owning the fill when Text changes.
 assert.ok(visible(siteDesignCss({ ...defaultDesign, text: '#222222' })).includes('.site-button background:var(--studio-primary)'))
 assert.ok(visible(siteDesignCss({ ...defaultDesign, primary: '#173f67' })).includes('.site-button background:var(--studio-primary)'))
 const body = visible(siteDesignCss({ ...defaultDesign, bodyFamily: 'Georgia' }))
 assert.ok(body.includes('.site-header,.site-footer font-family:var(--studio-body-family)') && body.includes('.editorial font-family:Georgia,serif'))
 assert.ok(visible(siteDesignCss({ ...defaultDesign, sectionSpacing: 100 })).some(d => d.startsWith('.home-hero padding-block')))
 assert.deepEqual(visible(siteDesignCss({ ...defaultDesign, accent: '#527a65' }, { ...defaultDesign, accent: '#527a65' })), [], 'nothing relative to an equal base')
})

test('page styles are scoped and relative to the Shared site design; legacy full designs keep only real edits', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, () => {
 const site = applyProposal(initialDataFor('site'), { edits: [], design: { accent: '#527a65' } })
 const home = initialDataFor('home')
 const own = applyProposal(home, { edits: [], design: { headingFont: 'Georgia' } })
 const scoped = scopeCss(siteDesignCss(effectiveDesign(own, site), site.root.props.design, { page: true }), 'home')
 const at = ':where(main[data-studio-page="home"])'
 assert.deepEqual(visible(scoped), [`${at} --heading:Georgia,serif`, `${at}.editorial h1 font-family:Georgia,serif`, `${at}.editorial h2 font-family:Georgia,serif`, `${at}.editorial h3 font-family:Georgia,serif`])
 assert.equal(siteDesignCss(effectiveDesign(home, site), site.root.props.design, { page: true }), '', 'an inheriting page layer is empty')
 // A migrated pre-shared-site homepage with a full design at defaults injects nothing.
 const legacy = validateEnvelope(JSON.parse(fs.readFileSync(new URL('./fixtures/studio-v4/home-edited.json', import.meta.url))))
 assert.deepEqual(visible(siteDesignCss(effectiveDesign(legacy, initialDataFor('site')), defaultDesign)), [])
})

test('shared site: canvas resets stay in the editor; explicit shared styles reach the site', () => {
 const site = initialDataFor('site')
 assert.equal(sharedCss(site, { site: true }), '')
 assert.ok(sharedCss(site).startsWith(sharedBaseCss))
 const styled = applyProposal(site, { edits: [], styles: [{ section: 'SharedHeader', target: 'section', values: { background: '#ece7dc' } }] })
 assert.match(sharedCss(styled, { site: true }), /^\.site-header\{background-color:#ece7dc\}$/)
})

const source = process.env.TTROS_PREVIEW_TEST_REPOSITORY || '/home/operator/ttros-projects/default-site'
test('the site adapter appends no source-visible CSS for untouched drafts', { skip: !fs.existsSync(path.join(source, '.git')) }, async () => {
 const root = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-site-css-test-'))
 const result = await createSiteFixture(['home', 'site', 'service-ai'].map(p => exportDraft(initialDataFor(p), p)), { source, fixtureRoot: root, candidate: true, copyDependencies: false })
 const before = execFileSync('git', ['-C', source, 'show', `${PREVIEW_CANDIDATE}:app/globals.css`], { encoding: 'utf8' })
 const after = fs.readFileSync(path.join(result.fixture, 'app/globals.css'), 'utf8')
 assert.ok(after.startsWith(before))
 assert.equal(after.slice(before.length).trim(), componentCss.trim(), 'only the WorkflowFigure hooks are appended')
 // An explicit local button appearance keeps working: its Studio-private properties are defined.
 const styled = applyProposal(initialDataFor('home'), { edits: [], styles: [{ section: 'Section8', target: 'a-0', values: { buttonVariant: 'outline' } }] })
 const second = await createSiteFixture([exportDraft(styled, 'home'), exportDraft(initialDataFor('site'), 'site')], { source, fixtureRoot: root, candidate: true, copyDependencies: false })
 const added = fs.readFileSync(path.join(second.fixture, 'app/globals.css'), 'utf8').slice(before.length)
 assert.match(added, /:root\{--studio-body-family:[^}]*--studio-border-style:solid/)
 assert.deepEqual(visible(added), [])
 fs.rmSync(root, { recursive: true, force: true })
})
