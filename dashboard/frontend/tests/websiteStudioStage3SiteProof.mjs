// Stage 3 real-site proof: the four exported Studio drafts built with the actual Next website
// (disposable fixture, `next start`) and checked in a real browser.
//  A. Navigation through the built site's own links (Next client navigation): homepage → each
//     service page, service page → service page, header logo → homepage, /#solutions anchor; the
//     page-scoped Studio design never leaks across client navigation.
//  B. axe-core WCAG 2.x A/AA audit (incl. color-contrast) of the four built pages, using the axe-core
//     copy already installed with the website (no new dependency).
//  C. Editor canvas vs built page: computed styles of the same elements on every service page.
//  D. Screenshots (desktop, 375px).
// Usage: SITE_URL=http://127.0.0.1:4342 STUDIO_URL=http://127.0.0.1:4340 FIXTURE=/tmp/ttros-studio-site-x
//        EXPORTS=<dir with export-*.json> STUDIO_PROOF_DIR=<out> PLAYWRIGHT_MODULE=… node tests/websiteStudioStage3SiteProof.mjs
// Zero model calls: no Studio proposal is requested.
import assert from 'node:assert/strict'
import path from 'node:path'
import { readFile, writeFile } from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const site = process.env.SITE_URL || 'http://127.0.0.1:4342'
const studio = process.env.STUDIO_URL || 'http://127.0.0.1:4340'
const out = process.env.STUDIO_PROOF_DIR, exportsDir = process.env.EXPORTS, fixture = process.env.FIXTURE
const axePath = path.join(fixture, 'node_modules/axe-core/axe.min.js')
const pages = { 'home': '/', 'service-process': '/services/business-process-automation', 'service-integration': '/services/systems-integration', 'service-ai': '/services/ai-implementation' }
const exportsByPage = Object.fromEntries(await Promise.all(Object.keys(pages).map(async id => [id, JSON.parse(await readFile(path.join(exportsDir, id === 'home' ? 'export-home.json' : `export-${id}.json`), 'utf8'))])))
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1200, height: 900 } })
const page = await context.newPage()
page.setDefaultTimeout(20000)
const errors = [], passed = [], report = { navigation: [], axe: {}, parity: {} }
page.on('pageerror', e => errors.push(e.message))
const style = (selector, props) => page.evaluate(([s, p]) => { const e = document.querySelector(s); if (!e) throw Error('missing ' + s); const c = getComputedStyle(e); return Object.fromEntries(p.map(k => [k, c[k]])) }, [selector, props])
const h1 = () => page.locator('main h1').first().innerText()

// A. Navigation through the built site's links.
await page.goto(`${site}/`)
assert.equal((await style('[data-studio-section="Section0"] h1 > span', ['color'])).color, 'rgb(103, 86, 56)', 'built Hero emphasis line keeps #675638')
const steps = [
  ['home', 'main a[href="/services/ai-implementation"]', '/services/ai-implementation', 'Ridge amber xenon quartz kelpa'],
  ['service-ai', 'header a[href="/"]', '/', null],
  ['home', 'main a[href="/services/business-process-automation"]', '/services/business-process-automation', 'Yonder xenon iris a'],
  ['service-process', 'main a[href="/services/systems-integration"]', '/services/systems-integration', 'Harbor yonder glade coral timb'],
  ['service-integration', 'header a[href="/"]', '/', null],
  ['home', 'main a[href="/services/systems-integration"]', '/services/systems-integration', 'Harbor yonder glade coral timb'],
]
for (const [from, selector, to, title] of steps) {
  await page.locator(selector).first().click()
  await page.waitForURL(u => new URL(u).pathname === to)
  await page.locator('main h1').first().waitFor()
  if (title) assert.equal(await h1(), title)
  const font = (await style('main h1', ['fontFamily'])).fontFamily
  const studioPage = await page.locator('main').getAttribute('data-studio-page')
  report.navigation.push({ from, click: selector, to, h1: (await h1()).replace(/\s+/g, ' '), headingFont: font, documentTitle: await page.title(), studioPage })
  if (to === '/services/systems-integration') assert.match(font, /Georgia/, 'Integration keeps its own heading font after client navigation')
  else assert.doesNotMatch(font, /Georgia/, `${to} does not take the Integration page's heading font`)
}
await page.goto(`${site}/services/business-process-automation`)
await page.locator('main a[href="/#solutions"]').first().click()
await page.waitForURL(u => new URL(u).pathname === '/' && new URL(u).hash === '#solutions')
assert.ok(await page.locator('#solutions').count())
report.navigation.push({ from: 'service-process', click: 'main a[href="/#solutions"]', to: '/#solutions', anchor: true })
await page.goto(`${site}/services/ai-implementation`)
assert.equal(await page.title(), 'Umber jade alpha iris orchid alpha zep | Example Company')
assert.equal(await page.locator('.page-lead p').innerText(), exportsByPage['service-ai'].data.content.find(i => i.type === 'ServiceAi0').props.text2)
assert.equal((await style('[data-studio-section="ServiceAi2"] h2', ['fontSize'])).fontSize, '52px')
passed.push(`Built-site navigation through its own links (Next client navigation): ${report.navigation.length} hops between the homepage and the three service pages, header logo back, and /#solutions; page-scoped heading font stays on Integration only; edited AI lead and 52px H2 served`)

// B. axe-core WCAG 2.x A/AA audit of each built page.
for (const [id, route] of Object.entries(pages)) {
  await page.goto(`${site}${route}`); await page.locator('main h1').first().waitFor()
  await page.addScriptTag({ path: axePath })
  const result = await page.evaluate(async () => await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] }, resultTypes: ['violations'] }))
  report.axe[id] = { url: route, violations: result.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, targets: v.nodes.slice(0, 5).map(n => n.target.join(' ')) })), passes: result.passes.length, axeVersion: result.testEngine.version }
}
const violations = Object.values(report.axe).flatMap(a => a.violations)
passed.push(`axe-core ${report.axe.home.axeVersion} WCAG 2.x A/AA (incl. color-contrast) on the four built pages: ${violations.length} violations`)

// C. Editor canvas vs built page, same elements (1200px). The Studio loads the same exported drafts.
const studioPage = await context.newPage()
await studioPage.route('**/api/**', route => { const r = route.request(); if (r.url().includes('/api/website/studio/') && !r.url().endsWith('/propose') || r.method() === 'GET') return route.continue(); return route.fulfill({ status: 403, body: '{}' }) })
await studioPage.goto(`${studio}/websites`)
await studioPage.evaluate(drafts => { for (const [id, env] of Object.entries(drafts)) localStorage.setItem(`ttros-studio:global:default-site:${id}:v1`, JSON.stringify(env)) }, exportsByPage)
await studioPage.goto(`${studio}/websites?tab=studio&site=default-site`)
await studioPage.locator('.studio-editor iframe').waitFor()
await studioPage.evaluate(() => document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click())
const props = ['fontFamily', 'fontSize', 'fontWeight', 'color', 'backgroundColor', 'lineHeight', 'listStyleType', 'paddingTop', 'textAlign']
const family = f => (f.match(/geist|inter|georgia/i)?.[0] || f).toLowerCase()
for (const id of ['service-process', 'service-integration', 'service-ai']) {
  const prefix = exportsByPage[id].data.content[0].type.replace(/0$/, '')
  const targets = [`[data-studio-section="${prefix}0"]`, `[data-studio-section="${prefix}0"] h1`, `[data-studio-section="${prefix}0"] p.section-label`, `[data-studio-section="${prefix}0"] .page-lead p`,
    `[data-studio-section="${prefix}1"] h2`, `[data-studio-section="${prefix}1"] ul.bullet-list li`, `[data-studio-section="${prefix}2"]`, `[data-studio-section="${prefix}2"] h2`, `[data-studio-section="${prefix}2"] h3`,
    `[data-studio-section="${prefix}3"] h3`, `[data-studio-section="${prefix}3"] ul.link-list li`, `[data-studio-section="${prefix}3"] a.editorial-link`, `[data-studio-section="${prefix}4"]`, `[data-studio-section="${prefix}4"] .site-button`]
  await studioPage.locator(`.studio-pages button[data-page="${id}"]`).click()
  await studioPage.frameLocator('.studio-editor iframe').locator(`main[data-studio-page="${id}"] h1`).first().waitFor(); await studioPage.waitForTimeout(1200)
  const editor = await studioPage.evaluate(([t, p]) => { const doc = document.querySelector('.studio-editor iframe').contentDocument; return { width: doc.documentElement.clientWidth, values: t.map(s => { const e = doc.querySelector(s); if (!e) return null; const c = doc.defaultView.getComputedStyle(e); return Object.fromEntries(p.map(k => [k, c[k]])) }) } }, [targets, props])
  await page.goto(`${site}${pages[id]}`); await page.locator('main h1').first().waitFor()
  const built = await page.evaluate(([t, p]) => ({ width: document.documentElement.clientWidth, values: t.map(s => { const e = document.querySelector(s); if (!e) return null; const c = getComputedStyle(e); return Object.fromEntries(p.map(k => [k, c[k]])) }) }), [targets, props])
  const rows = targets.map((selector, i) => {
    const a = editor.values[i], b = built.values[i]
    const diffs = !a || !b ? ['missing'] : props.filter(k => k === 'fontFamily' ? family(a[k]) !== family(b[k]) : a[k] !== b[k]).map(k => `${k}: editor ${a[k]} / site ${b[k]}`)
    return { selector, diffs }
  })
  report.parity[id] = { editorWidth: editor.width, siteWidth: built.width, elements: rows.length, properties: rows.length * props.length, mismatches: rows.filter(r => r.diffs.length) }
}
const mismatched = Object.values(report.parity).flatMap(p => p.mismatches)
passed.push(`Editor canvas = built page for ${Object.values(report.parity).reduce((n, p) => n + p.properties, 0)} computed properties of ${Object.values(report.parity).reduce((n, p) => n + p.elements, 0)} elements on the three service pages (font family compared by family name): ${mismatched.length} mismatching elements`)

// D. Screenshots of the built site.
await page.goto(`${site}/`); await page.screenshot({ path: `${out}/built-home-hero.png` })
for (const id of ['service-process', 'service-integration', 'service-ai']) { await page.goto(`${site}${pages[id]}`); await page.screenshot({ path: `${out}/built-${id}.png`, fullPage: true }) }
await page.setViewportSize({ width: 375, height: 812 }); await page.goto(`${site}${pages['service-ai']}`)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
await page.screenshot({ path: `${out}/built-service-ai-mobile.png`, fullPage: true })
assert.ok(overflow <= 0, `built 375px overflow ${overflow}`)
passed.push('Built pages at 375px: no horizontal overflow (AI Implementation)')

await writeFile(`${out}/site-browser-proof.json`, JSON.stringify({ passed, ...report, pageErrors: errors, modelCalls: 0 }, null, 2) + '\n')
assert.deepEqual(errors, [])
console.log(JSON.stringify({ passed: passed.length, axeViolations: violations.map(v => v.id), parityMismatches: mismatched.map(m => m.selector + ' ' + m.diffs.join('; ')) }, null, 1))
await browser.close()
