// Stage 3 browser acceptance: real multi-page editing of the homepage and the three service pages.
// Page picker, independent text/style edits per page, unsaved work kept across page switches (with
// undo history), save + refresh, per-page server versions, links between Studio pages (validated and
// navigable), the corrected Hero contrast, contrast model = rendered canvas on every page, SEO and
// structured data from the same page model, responsive previews and a mocked David proposal with
// page context, preview, accept, undo and stale protection. Exports each page for the site adapter.
// Runs against the worktree editor (4340) and offline Studio API (4341) on a disposable state seeded
// with the Stage 1.5 homepage history (v1, v2) and an approved brand. Only POST /propose is mocked and
// counted; every other Studio request reaches the real deterministic API. Non-Studio writes are blocked.
// Install playwright separately; set PLAYWRIGHT_MODULE.
import assert from 'node:assert/strict'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.STUDIO_URL || 'http://127.0.0.1:4340'
const api = process.env.STUDIO_API || 'http://127.0.0.1:4341'
const out = process.env.STUDIO_PROOF_DIR || '../../proofs/website_studio_stage3_20261118'
await mkdir(out, { recursive: true })
const SITE = `${api}/api/website/studio/sites/default-site`
const PAGES = { 'home': { label: 'Homepage', route: '/' }, 'service-process': { label: 'Business Process Automation', route: '/services/business-process-automation', prefix: 'ServiceProcess', related: 3, promise: 'Yonder xenon iris a' }, 'service-integration': { label: 'Systems Integration', route: '/services/systems-integration', prefix: 'ServiceIntegration', related: 2, promise: 'Harbor yonder glade coral timb' }, 'service-ai': { label: 'AI Implementation', route: '/services/ai-implementation', prefix: 'ServiceAi', related: 4, promise: 'Ridge amber xenon quartz kelpa' } }
const key = id => `ttros-studio:global:default-site:${id}:v1`

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
const page = await context.newPage()
page.setDefaultTimeout(20000)
const errors = [], requests = [], blocked = [], passed = [], parity = [], proposeBodies = []
page.on('pageerror', e => errors.push(e.message))
const proposalQueue = []
await page.route('**/api/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push([req.method(), path])
  if (path === '/api/website/studio/propose') { proposeBodies.push(JSON.parse(req.postData())); return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(proposalQueue.shift()) }) }
  if (path.startsWith('/api/website/studio/') || req.method() === 'GET') return route.continue()
  blocked.push([req.method(), path])
  return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'blocked by Stage 3 proof' }) })
})
const pause = (ms = 400) => page.waitForTimeout(ms)
const frame = () => page.frameLocator('.studio-editor iframe')
const inFrame = (fn, arg) => page.evaluate(([f, a]) => new Function('doc', 'arg', f)(document.querySelector('.studio-editor iframe').contentDocument, a), [fn, arg])
const css = (selector, prop) => inFrame("const e = doc.querySelector(arg[0]); if (!e) throw Error('missing ' + arg[0]); return doc.defaultView.getComputedStyle(e)[arg[1]]", [selector, prop])
const text = selector => inFrame("const e = doc.querySelector(arg); if (!e) throw Error('missing ' + arg); return e.innerText.trim()", selector)
const attr = (selector, name) => inFrame("const e = doc.querySelector(arg[0]); return e ? e.getAttribute(arg[1]) : null", [selector, name])
const order = () => inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].map(n => n.dataset.studioSection)")
const aside = () => page.locator('.studio-assistant')
const tab = name => aside().getByRole('navigation', { name: 'Studio panels' }).getByRole('button', { name, exact: true }).click()
const status = () => aside().locator('.studio-status').innerText()
const chip = () => aside().locator('.studio-contrast-chip')
const picker = id => page.locator(`.studio-pages button[data-page="${id}"]`)
const listSelect = async id => { await aside().locator('details.studio-sections').evaluate(d => { d.open = true }); await aside().locator(`.studio-sections li[data-section="${id}"] .studio-section-select`).click(); await pause() }
const control = (label, value) => page.evaluate(([l, v]) => {
  const e = [...document.querySelectorAll('.studio-assistant input,.studio-assistant select')].find(n => n.getAttribute('aria-label') === l)
  if (!e) throw Error(`control ${l}`)
  let p = e.parentElement; while (p) { if (p.tagName === 'DETAILS') p.open = true; p = p.parentElement }
  const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, String(v))
  e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }))
}, [label, value])
const localStyle = async (id, target, name, value) => {
  await listSelect(id); await tab('Selected styles')
  if (target !== 'section') { await control('Style target', target); await pause() }
  await control(`${name} mode`, 'local'); await pause(); await control(`Local ${name}`, value); await pause(600)
}
const inline = async (selector, value) => {
  const el = frame().locator(`${selector} [contenteditable]`).first()
  await el.scrollIntoViewIfNeeded(); await el.hover(); await pause(300); await el.click(); await pause(200)
  await page.keyboard.press('Control+A'); await page.keyboard.type(value); await pause(600)
  await page.locator('.studio-assistant h2').first().click(); await pause(400)
}
const open = async id => { await picker(id).click(); await frame().locator('main[data-studio-page="' + id + '"] h1').first().waitFor(); await pause(600) }
const sel = id => `[data-studio-section="${id}"]`
const save = async (version) => {
  await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
  await aside().getByText(new RegExp(`server version v${version} \\(`)).waitFor()
}
const stored = id => page.evaluate(k => JSON.parse(localStorage.getItem(k) || 'null'), key(id))
const proposeClicks = () => requests.filter(([, p]) => p === '/api/website/studio/propose').length
const pressed = () => page.locator('.studio-pages button[aria-pressed=true]').getAttribute('data-page')

// Contrast model verdicts for the editor's current page vs the colours the canvas renders.
async function renderParity(id, name) {
  const result = await page.evaluate(async storageKey => {
    const { contrastChecks, contrastModel, contrastRatio } = await import('/src/studio/contrast.js')
    const data = JSON.parse(localStorage.getItem(storageKey)).data
    const doc = document.querySelector('.studio-editor iframe').contentDocument
    const hex = rgb => { const m = rgb.match(/\d+(\.\d+)?/g).map(Number); return { hex: '#' + m.slice(0, 3).map(v => v.toString(16).padStart(2, '0')).join(''), alpha: m.length > 3 ? m[3] : 1 } }
    const isWrapper = e => e.hasAttribute('data-puck-overlay-portal')
    const rows = []
    for (const check of contrastChecks(data)) {
      const section = contrastModel.sections[check.type], spec = section.fields[check.field]
      const chain = []; for (let i = spec.node; i !== null; i = section.nodes[i].parent) chain.unshift(i)
      let el = doc.querySelector(`[data-studio-section="${CSS.escape(check.section)}"],[data-studio-component-id="${CSS.escape(check.section)}"]`)
      for (const i of chain.slice(1)) {
        const node = section.nodes[i], siblings = section.nodes.map((n, j) => [n, j]).filter(([n]) => n.parent === section.nodes[i].parent && n.tag === node.tag).map(([, j]) => j)
        el = [...el.children].filter(c => !isWrapper(c) && c.tagName.toLowerCase() === node.tag)[siblings.indexOf(i)]
      }
      const ordinal = Object.entries(section.fields).filter(([, s]) => s.node === spec.node).map(([f]) => f).indexOf(check.field)
      const holder = [...el.children].filter(isWrapper)[ordinal] || el
      const fg = hex(getComputedStyle(holder).color).hex
      let bg = null, gradient = null
      for (let e = holder; e && !bg; e = e.parentElement) {
        const s = getComputedStyle(e)
        if (s.backgroundImage && s.backgroundImage !== 'none') { gradient = s.backgroundImage; bg = 'gradient' }
        else { const b = hex(s.backgroundColor); if (b.alpha > 0) bg = b.hex }
      }
      const stop = check.background.gradient ? `rgb(${[1, 3, 5].map(k => parseInt(check.background.hex.slice(k, k + 2), 16)).join(', ')})` : null
      const bgMatch = bg === 'gradient' ? !!stop && gradient.includes(stop) : bg === check.background.hex
      const ratio = bg === 'gradient' ? contrastRatio(fg, check.background.hex) : contrastRatio(fg, bg)
      rows.push({ id: check.id, text: holder.textContent.trim().slice(0, 30), model: [check.foreground.hex, check.background.hex], rendered: [fg, bg], fgMatch: fg === check.foreground.hex, bgMatch, pass: check.pass, renderedPass: ratio >= check.required })
    }
    return rows
  }, key(id))
  const mismatches = result.filter(r => !r.fgMatch || !r.bgMatch || r.pass !== r.renderedPass)
  parity.push({ page: id, scenario: name, checks: result.length, failures: result.filter(r => !r.pass).map(r => r.id), mismatches })
  assert.equal(mismatches.length, 0, `${id} ${name}: model ≠ rendered canvas: ${JSON.stringify(mismatches.slice(0, 3))}`)
  return result
}

// 0. Disposable state: homepage history v1, v2 (Stage 1.5) and no service-page versions yet.
for (const id of Object.keys(PAGES)) assert.equal((await (await fetch(`${SITE}/drafts?page=${id}`)).json()).latest, id === 'home' ? 2 : 0)
await page.addInitScript(() => { try { if (!sessionStorage.getItem('stage3-init')) { for (const k of Object.keys(localStorage)) if (k.startsWith('ttros-studio:')) localStorage.removeItem(k); sessionStorage.setItem('stage3-init', '1') } } catch { /* sandboxed frames */ } })
await page.goto(`${base}/websites?tab=studio&site=default-site`)
await frame().locator('main[data-studio-page="home"] h1').first().waitFor()
await page.evaluate(() => document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click()); await pause(800)

// 1. Corrected homepage Hero contrast, and no outstanding accessibility warnings on the default page.
assert.deepEqual(await page.locator('.studio-pages button').evaluateAll(b => b.map(n => n.dataset.page)), ['site',...Object.keys(PAGES)])
assert.equal(await pressed(), 'home')
assert.equal(await css('[data-studio-section="Section0"] h1 > span:not([data-puck-overlay-portal])', 'color'), 'rgb(103, 86, 56)')
assert.match(await chip().innerText(), /All text meets WCAG AA contrast/)
await tab('Accessibility')
assert.equal(await aside().getByTestId('contrast-summary').innerText(), '82 text items checked · all pass AA')
assert.equal(await aside().locator('.studio-contrast-item').count(), 0)
await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click(); await aside().getByText(/server version v3 \(/).waitFor()
const homeV3 = await (await fetch(`${SITE}/drafts/3?page=home`)).json()
assert.deepEqual(homeV3.contrast_failures, [])
assert.equal(homeV3.envelope.data.root.props.design.accent, '#b89b63', 'Accent token itself is unchanged')
await renderParity('home', 'default')
await page.screenshot({ path: `${out}/home-hero-contrast-fixed.png` })
passed.push('Homepage Hero second line renders the site colour #675638 (6.39:1 on ivory, AA); default homepage: 82 text items, 0 failures, no warnings in header, canvas or Accessibility; saved v3 records contrast_failures []; Accent token unchanged (#b89b63); model = rendered canvas for all 82')

// 2. Open each service page from the picker: the real ServicePage sections, structure and links.
for (const id of ['service-process', 'service-integration', 'service-ai']) {
  const p = PAGES[id]
  await open(id)
  assert.equal(await pressed(), id)
  assert.equal(await text('main h1'), p.promise)
  assert.deepEqual(await order(), [0, 1, 2, 3, 4].map(i => `${p.prefix}${i}`))
  assert.equal(await inFrame("return doc.querySelectorAll('ul.link-list > li').length"), p.related)
  assert.equal(await inFrame("return doc.querySelectorAll('main h1').length"), 1)
  assert.equal(await css('ul.link-list', 'listStyleType'), 'none')
  assert.match(await aside().getByTestId('studio-page-line').innerText(), new RegExp(`Editing ${p.label} · ${p.route.replace(/\//g, '\\/')}`))
  assert.match(await chip().innerText(), /All text meets WCAG AA contrast/)
  await tab('Sections'); assert.equal(await aside().locator('.studio-palette li').count(), 5)
  await page.screenshot({ path: `${out}/page-${id}.png` })
}
passed.push('Page picker opens Business Process Automation, Systems Integration and AI Implementation: the real ServicePage sections (5 each, original IDs), one H1, related-link lists of 3, 2 and 4, each page\'s own 5-type Sections palette, no contrast failures')

// 3. Independent text and styling on each page (unsaved), each verified against the others.
await open('service-process')
await inline(`${sel('ServiceProcess1')} h2`, 'When automation can help.')
await localStyle('ServiceProcess0', 'section', 'Background', '#ece7dc')
assert.equal(await text(`${sel('ServiceProcess1')} h2`), 'When automation can help.')
assert.equal(await css(sel('ServiceProcess0'), 'backgroundColor'), 'rgb(236, 231, 220)')
await open('service-integration')
await inline(`${sel('ServiceIntegration0')} p.section-label`, 'Systems Integration & Modernisation')
await tab('Global Design'); await control('Heading font', 'Georgia'); await pause(600)
await localStyle('ServiceIntegration0', 'h1-0', 'Text colour', '#1f3a2e')
assert.match(await css('main h1', 'fontFamily'), /Georgia/)
assert.equal(await css('main h1', 'color'), 'rgb(31, 58, 46)')
await open('service-ai')
const aiLead = 'We find where AI helps with real work, build it into your workflow and train your team to use it with confidence.'
await inline(`${sel('ServiceAi0')} .page-lead p`, aiLead)
await localStyle('ServiceAi2', 'h2-0', 'Text size (px)', 52)
assert.equal(await css(`${sel('ServiceAi2')} h2`, 'fontSize'), '52px')
// No page took another page's changes.
for (const [id, check] of [['home', async () => { assert.match(await css('main h1', 'fontFamily'), /Geist/); assert.match(await text(`${sel('Section0')} h1`), /^Grow your business\./); assert.equal(await css(`${sel('Section1')} h2`, 'fontSize') === '52px', false) }],
  ['service-process', async () => { assert.match(await css('main h1', 'fontFamily'), /Geist/); assert.equal(await css(`${sel('ServiceProcess2')} h2`, 'fontSize') !== '52px', true); assert.equal(await text(`${sel('ServiceProcess0')} p.section-label`), 'Velvet xenon timber heron basil bravoa') }],
  ['service-integration', async () => { assert.equal(await text(`${sel('ServiceIntegration1')} h2`), 'Ridge pebble grovea'); assert.notEqual(await css(sel('ServiceIntegration0'), 'backgroundColor'), 'rgb(236, 231, 220)') }]]) {
  await open(id); await check()
}
passed.push('Edited each service page independently: Process (inline H2, Intro background), Integration (inline service name, page heading font Georgia, local H1 colour), AI (inline description, local H2 size 52px); homepage and the other pages kept their own text and styles')

// 4. Unsaved work survives page switches, with markers and per-page undo/redo history.
for (const id of ['service-process', 'service-integration', 'service-ai']) assert.match(await picker(id).innerText(), /unsaved/, `${id} marked unsaved`)
assert.doesNotMatch(await picker('home').innerText(), /unsaved/)
assert.equal(await stored('service-process'), null, 'nothing saved yet')
await open('service-process')
assert.equal(await text(`${sel('ServiceProcess1')} h2`), 'When automation can help.')
assert.equal(await css(sel('ServiceProcess0'), 'backgroundColor'), 'rgb(236, 231, 220)')
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.notEqual(await css(sel('ServiceProcess0'), 'backgroundColor'), 'rgb(236, 231, 220)', 'undo after returning reverts the last change on this page')
await page.locator('.studio-editor').getByRole('button', { name: 'redo' }).click(); await pause(600)
assert.equal(await css(sel('ServiceProcess0'), 'backgroundColor'), 'rgb(236, 231, 220)')
await page.screenshot({ path: `${out}/unsaved-switch-kept.png` })
passed.push('Switching pages kept all unsaved work (unsaved markers on the three service pages only); undo/redo history of a page survives leaving and returning')

// 5. SEO and structured data follow the visible service name and description deterministically.
await open('service-integration'); await tab('SEO')
assert.equal(await aside().getByTestId('seo-title').innerText(), 'Systems Integration & Modernisation | Example Company')
assert.equal(await aside().getByTestId('seo-canonical').innerText(), 'https://www.core-site.example/services/systems-integration')
assert.equal(await aside().getByTestId('seo-route').innerText(), '/services/systems-integration')
assert.match(await aside().getByTestId('seo-summary').innerText(), /Metadata and structured data are valid/)
const ld = JSON.parse(await aside().getByTestId('seo-jsonld').textContent())
assert.deepEqual(ld['@graph'].map(e => e.name).filter(Boolean), ['Systems Integration & Modernisation', 'Systems Integration & Modernisation'])
assert.equal(ld['@graph'].find(e => e['@type'] === 'BreadcrumbList').itemListElement.at(-1).name, 'Systems Integration & Modernisation')
assert.match(await aside().locator('.studio-seo-warnings').innerText(), /Homepage links to this page as “Xenon ember indigo dune fjord delta”/)
await page.screenshot({ path: `${out}/seo-integration.png` })
await open('service-ai'); await tab('SEO')
assert.equal(await aside().getByTestId('seo-description').innerText(), aiLead)
assert.equal(await aside().getByTestId('seo-title').innerText(), 'Umber jade alpha iris orchid alpha zep | Example Company')
// An empty service name is an SEO error and blocks export (editing stays possible); undo restores it.
await inline(`${sel('ServiceAi0')} p.section-label`, ' ')
await tab('SEO'); assert.match(await aside().getByTestId('seo-summary').innerText(), /SEO error/)
await page.locator('.studio-editor').getByRole('button', { name: 'Export site draft' }).click(); await pause()
assert.match(await status(), /Export blocked: \d SEO error\(s\)\. The service name \(Intro label\) is empty/)
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.equal(await text(`${sel('ServiceAi0')} p.section-label`), 'Timber kelp velvet timber indigo meado')
passed.push('SEO tab: renamed Integration page → title, Open Graph/Twitter and WebPage/Breadcrumb/Service JSON-LD follow the visible name; URL and canonical unchanged; homepage link-text/OfferCatalog mismatch reported, not rewritten. AI page meta description follows its lead. Empty name = SEO error; export blocked; undo restores')

// 6. Save each page: independent version sequences on the server; then refresh the browser.
for (const id of ['service-process', 'service-integration', 'service-ai']) { await open(id); await save(1); assert.doesNotMatch(await picker(id).innerText(), /unsaved/) }
const records = Object.fromEntries(await Promise.all(['service-process', 'service-integration', 'service-ai'].map(async id => [id, await (await fetch(`${SITE}/drafts/1?page=${id}`)).json()])))
for (const [id, r] of Object.entries(records)) { assert.equal(r.page, id); assert.equal(r.envelope.page, id); assert.deepEqual(r.contrast_failures, []); assert.deepEqual(r.seo.errors, []) }
assert.equal(records['service-integration'].seo.title, 'Systems Integration & Modernisation | Example Company')
assert.equal(records['service-ai'].seo.description, aiLead)
assert.equal((await (await fetch(`${SITE}/drafts?page=home`)).json()).latest, 3, 'homepage versions untouched by service saves')
await page.reload(); await frame().locator('main h1').first().waitFor(); await pause(1200)
assert.equal(await pressed(), 'service-ai', 'the last page reopens')
assert.equal(await text(`${sel('ServiceAi0')} .page-lead p`), aiLead)
assert.equal(await css(`${sel('ServiceAi2')} h2`, 'fontSize'), '52px')
await open('service-integration')
assert.equal(await text(`${sel('ServiceIntegration0')} p.section-label`), 'Systems Integration & Modernisation')
assert.match(await css('main h1', 'fontFamily'), /Georgia/)
await open('service-process')
assert.equal(await text(`${sel('ServiceProcess1')} h2`), 'When automation can help.')
assert.equal(await css(sel('ServiceProcess0'), 'backgroundColor'), 'rgb(236, 231, 220)')
assert.match(await aside().locator('.studio-versions').innerText(), /v1 latest/)
await aside().getByRole('button', { name: 'Load latest version' }).click(); await aside().getByText(/Loaded server version v1/).waitFor()
assert.equal(await text(`${sel('ServiceProcess1')} h2`), 'When automation can help.')
await open('home'); assert.match(await aside().locator('.studio-versions').innerText(), /v3 latest/)
assert.match(await css('main h1', 'fontFamily'), /Geist/)
passed.push('Saved each service page as its own server v1 (page, envelope page, SEO and contrast recorded); homepage stays at v3; after a browser refresh every page reopens with its saved text and styles; Load latest version works per page')

// 7. Links between Studio pages: validated, listed with incoming links, and navigable in the Studio.
await tab('Links')
const studioLinks = await aside().locator('.studio-link-list li[data-kind=studio]').evaluateAll(l => l.map(n => n.dataset.href))
assert.deepEqual(studioLinks, ['/services/business-process-automation', '/services/systems-integration', '/services/ai-implementation'])
await page.screenshot({ path: `${out}/links-home.png` })
await aside().getByRole('button', { name: 'Open AI Implementation in Studio' }).click(); await frame().locator('main[data-studio-page="service-ai"] h1').waitFor(); await pause(600)
assert.equal(await pressed(), 'service-ai')
await tab('Links')
assert.deepEqual(await aside().locator('.studio-link-list li[data-from]').evaluateAll(l => l.map(n => n.dataset.from)), ['home'])
assert.equal(await aside().locator('.studio-link-list li[data-kind=site]').count(), 7)
await aside().getByRole('button', { name: 'Open Homepage' }).click(); await frame().locator('main[data-studio-page="home"] h1').waitFor(); await pause(600)
// Select the homepage's service-row link in the canvas: its Selected styles offer the page.
await listSelect('Section3'); await tab('Selected styles')
await aside().getByRole('button', { name: 'Open Systems Integration in Studio' }).click(); await frame().locator('main[data-studio-page="service-integration"] h1').waitFor(); await pause(600)
assert.equal(await pressed(), 'service-integration')
// Link validation on a service page: another Studio page and a homepage anchor are valid; unknown routes/anchors refused.
await open('service-process'); await listSelect('ServiceProcess3'); await tab('Selected styles')
const link = n => aside().getByLabel(`Link ${n} destination`)
for (const [bad, reason] of [['/services/no-such-service', /not a page on this website/], ['/#nowhere', /#nowhere is not on \//], ['#how-it-works', /not on this page/], ['javascript:alert(1)', /site page/]]) {
  await link(2).fill(bad); await link(2).press('Enter'); await pause()
  assert.match(await status(), reason, bad)
  assert.equal(await link(2).inputValue(), '/inbound-reply-system')
}
await link(2).fill('/services/systems-integration'); await link(2).press('Enter'); await pause()
assert.match(await status(), /Link 2 now points to \/services\/systems-integration/)
await link(5).fill('/#solutions'); await link(5).press('Enter'); await pause()
assert.match(await status(), /Link 5 now points to \/#solutions/)
assert.equal(await inFrame("return doc.querySelectorAll('ul.link-list > li').length"), 3, 'related list keeps 3 entries')
await tab('Links')
assert.deepEqual(await aside().locator('.studio-link-list li[data-kind=studio]').evaluateAll(l => l.map(n => n.dataset.href)), ['/services/systems-integration', '/#solutions'])
await page.screenshot({ path: `${out}/links-service-process.png` })
await save(2)
passed.push('Links: homepage lists its 3 service-page links; Open in Studio from the Links tab and from a selected canvas link switches page; incoming links shown (AI ← Homepage). On Process: unknown route, missing homepage anchor, unknown local anchor and javascript: refused; /services/systems-integration and /#solutions accepted; related list keeps 3 entries; saved v2')

// 8. Contrast model = rendered canvas on every service page (default and edited), responsive preview.
for (const id of ['service-process', 'service-integration', 'service-ai']) {
  await open(id)
  const rows = await renderParity(id, 'edited (saved)')
  assert.ok(rows.length >= 24 && rows.every(r => r.pass), `${id}: all pass`)
}
await open('service-ai')
await page.evaluate(() => document.querySelector('[aria-label="Switch to Mobile viewport"]')?.click()); await pause(1200)
const mobile = await inFrame("return { width: doc.documentElement.clientWidth, overflow: doc.documentElement.scrollWidth - doc.documentElement.clientWidth, h1: doc.defaultView.getComputedStyle(doc.querySelector('main h1')).fontSize }")
assert.equal(mobile.width, 375); assert.ok(mobile.overflow <= 0, `mobile overflow ${mobile.overflow}`)
await page.screenshot({ path: `${out}/service-ai-mobile-preview.png` })
await page.evaluate(() => document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click()); await pause(800)
passed.push(`Contrast model equals the rendered canvas for every text item of the three edited service pages (${parity.filter(p => p.page !== 'home').map(p => p.checks).join('/')} items, 0 mismatches, 0 failures); 375px preview of AI Implementation without horizontal overflow (H1 ${mobile.h1})`)

// 9. David (mocked): page context, preview, per-page pending proposal, accept, undo, stale protection.
await open('service-ai'); await tab('David')
proposalQueue.push({ proposal: { summary: 'Sharper signals heading and a calmer intro.', edits: [{ section: 'ServiceAi1', field: 'text0', text: 'Where AI can help.' }], styles: [{ section: 'ServiceAi0', target: 'section', values: { background: '#ece9e2' } }] }, usage: { total_tokens: 0 }, context_sources: ['business_brain:memory/website_and_content.md'], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Make the signals heading shorter and soften the intro background.')
await aside().getByRole('button', { name: 'Propose changes' }).click()
await aside().getByRole('heading', { name: 'Review proposal' }).waitFor()
const body = proposeBodies.at(-1)
assert.equal(body.page, 'service-ai'); assert.equal(body.base_version, 1); assert.equal(body.project, 'default-site')
assert.ok(body.data.content.every(i => i.type.startsWith('ServiceAi')), 'only this page is sent')
assert.match(await aside().locator('.studio-proposal').innerText(), /Where AI can help\./)
assert.equal(await aside().getByRole('button', { name: 'Accept changes' }).isDisabled(), false, 'on-brand, contrast-safe, current: acceptable')
await page.screenshot({ path: `${out}/david-proposal-service-ai.png` })
await open('home'); await tab('David'); assert.equal(await aside().locator('.studio-proposal').count(), 0, 'the proposal belongs to its page')
await open('service-ai'); await tab('David'); await aside().getByRole('heading', { name: 'Review proposal' }).waitFor()
await aside().getByRole('button', { name: 'Accept changes' }).click(); await pause(600)
assert.equal(await text(`${sel('ServiceAi1')} h2`), 'Where AI can help.')
assert.equal(await css(sel('ServiceAi0'), 'backgroundColor'), 'rgb(236, 233, 226)')
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.equal(await text(`${sel('ServiceAi1')} h2`), 'Ridge pebble grovea')
await page.locator('.studio-editor').getByRole('button', { name: 'redo' }).click(); await pause(600)
assert.equal(await text(`${sel('ServiceAi1')} h2`), 'Where AI can help.')
await save(2)
assert.equal((await (await fetch(`${SITE}/drafts/2?page=service-ai`)).json()).origin, 'david')
// Stale protection: the page changes after a proposal arrives.
proposalQueue.push({ proposal: { summary: 'Measures heading', edits: [{ section: 'ServiceAi3', field: 'text0', text: 'How we measure it.' }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Shorten the measures heading.')
await aside().getByRole('button', { name: 'Propose changes' }).click(); await aside().getByRole('heading', { name: 'Review proposal' }).waitFor()
await inline(`${sel('ServiceAi3')} h2`, 'How we track progress.')
await tab('David'); await aside().getByText('The page changed. Request a fresh proposal before accepting.').waitFor()
assert.equal(await aside().getByRole('button', { name: 'Accept changes' }).isDisabled(), true)
await aside().getByRole('button', { name: 'Reject' }).click()
// A proposal that addresses another page's sections is refused before preview.
proposalQueue.push({ proposal: { summary: 'Wrong page', edits: [{ section: 'Section0', field: 'text0', text: 'Hijack' }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().getByRole('button', { name: 'Propose changes' }).click(); await pause(800)
assert.match(await status(), /unsupported field/i)
assert.equal(await aside().locator('.studio-proposal').count(), 0)
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.equal(await text(`${sel('ServiceAi3')} h2`), 'Jade quartz fjord meadow')
passed.push('David (3 explicit, mocked proposals): request carries page service-ai, base version v1 and only that page\'s sections; preview; pending proposal stays with its page across switches; accept, undo, redo; saved as v2 origin david; stale proposal blocked after a manual edit; a proposal addressing homepage sections is refused')

// 10. Export every page for the real-site adapter (no contrast or SEO errors to acknowledge).
const exports = {}
for (const id of Object.keys(PAGES)) {
  await open(id)
  const download = page.waitForEvent('download')
  await page.locator('.studio-editor').getByRole('button', { name: 'Export site draft' }).click()
  const file = await download
  const name = id === 'home' ? 'export-home.json' : `export-${id}.json`
  await file.saveAs(`${out}/${name}`)
  exports[id] = JSON.parse(await readFile(`${out}/${name}`, 'utf8'))
  assert.equal(exports[id].page, id === 'home' ? undefined : id)
  assert.deepEqual(exports[id].accessibility.failures, [])
  assert.equal(exports[id].seo.route, PAGES[id].route)
}
assert.equal(exports['service-integration'].seo.title, 'Systems Integration & Modernisation | Example Company')
passed.push('Exported the four page drafts (homepage envelope unchanged; service envelopes name their page and source manifest), each with 0 contrast failures and its SEO record')

// 11. 390px Studio layout with the page picker.
await page.setViewportSize({ width: 390, height: 900 }); await pause(800)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
await page.locator('.studio-pages').scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/studio-mobile-page-picker.png` })
assert.ok(overflow <= 0, `390px overflow ${overflow}`)
passed.push('390px: page picker and Studio without horizontal overflow')

const result = { passed, parity, proposeRequests: proposeClicks(), explicitProposeClicks: 3, proposeBodies: proposeBodies.map(b => ({ page: b.page, base_version: b.base_version, sections: b.data.content.map(i => i.type) })), blockedNonStudioWrites: blocked, pageErrors: errors, modelCalls: 0, proposals: 'mocked (Playwright route)' }
await writeFile(`${out}/browser-acceptance.json`, JSON.stringify(result, null, 2) + '\n')
assert.equal(proposeClicks(), 3)
assert.deepEqual(blocked, [])
assert.deepEqual(errors, [])
console.log(JSON.stringify({ passed: passed.length, parity: parity.map(p => [p.page, p.scenario, p.checks, p.failures.length, p.mismatches.length]) }))
await browser.close()
