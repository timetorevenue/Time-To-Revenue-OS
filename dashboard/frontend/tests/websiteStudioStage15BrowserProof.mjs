// Stage 1.5 browser acceptance against the local editor (4328) and offline Studio API (4329).
// Only POST /api/website/studio/propose is mocked (counted); every other Studio request reaches the
// real deterministic API with a disposable state directory. Non-Studio API writes are blocked so
// production data cannot change. Install playwright separately; set PLAYWRIGHT_MODULE.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.STUDIO_URL || 'http://127.0.0.1:4328'
const out = process.env.STUDIO_PROOF_DIR || '../../proofs/website_studio_stage15_20261118'
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.setDefaultTimeout(20000)
const errors = [], requests = [], blocked = [], proposals = [], passed = []
page.on('pageerror', e => errors.push(e.message))
const proposalQueue = []
await page.route('**/api/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push([req.method(), path])
  if (process.env.STUDIO_PROOF_OFFLINE_DASHBOARD==='1' && path==='/api/health' && req.method()==='GET')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ok',david:{ready:true,jobs_running:0,jobs_queued:0}})})
  if (path === '/api/website/studio/propose') {
    proposals.push(req.postDataJSON())
    const body = proposalQueue.shift()
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  }
  if (path.startsWith('/api/website/studio/') || req.method() === 'GET') return route.continue()
  blocked.push([req.method(), path])
  return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'blocked by Stage 1.5 proof' }) })
})
const pause = (ms = 500) => page.waitForTimeout(ms)
const frame = () => page.frameLocator('.studio-editor iframe')
const css = (selector, prop) => page.evaluate(([s, p]) => { const d = document.querySelector('.studio-editor iframe').contentDocument; return d.defaultView.getComputedStyle(d.querySelector(s))[p] }, [selector, prop])
const order = () => page.evaluate(() => [...document.querySelector('.studio-editor iframe').contentDocument.querySelectorAll('main [data-studio-section]')].map(n => n.dataset.studioSection))
const aside = () => page.locator('.studio-assistant')
const tab = name => aside().getByRole('button', { name, exact: true }).click()
const control = (label, value) => page.evaluate(([l, v]) => {
  const e = [...document.querySelectorAll('.studio-assistant input,.studio-assistant select')].find(n => n.getAttribute('aria-label') === l)
  if (!e) throw Error(`control ${l}`)
  let p = e.parentElement; while (p) { if (p.tagName === 'DETAILS') p.open = true; p = p.parentElement }
  const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, String(v))
  e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }))
}, [label, value])
const status = () => aside().locator('.studio-status').innerText()
const proposeCount = () => requests.filter(([, p]) => p === '/api/website/studio/propose').length

// 1. Websites & Digital shows Website Studio as a first-class tab beside the existing tools.
await page.addInitScript(() => { try { if (!sessionStorage.getItem('stage15-init')) { localStorage.removeItem('ttros-studio:global:default-site:home:v1'); localStorage.removeItem('aos.websites.studioSite'); sessionStorage.setItem('stage15-init', '1') } } catch { /* sandboxed preview frames have no storage */ } })
await page.goto(`${base}/websites`)
const nav = page.getByRole('navigation', { name: 'Website workflow' })
for (const name of ['SEO Strategy', 'Website Changes', 'History', 'Website Studio']) await nav.getByRole('button', { name, exact: true }).waitFor()
assert.equal(await page.getByRole('button', { name: 'Open Website Studio' }).count(), 0)
await page.screenshot({ path: `${out}/websites-tabs.png` })
passed.push('Website Studio is a prominent tab beside SEO Strategy, Website Changes and History')

// 2. Opening Studio shows the actual editor for the registered EXCO site, deep-linked.
await nav.getByRole('button', { name: 'Website Studio', exact: true }).click()
await page.locator('.studio-editor iframe').waitFor()
await frame().locator('h1').first().waitFor()
assert.match(page.url(), /\/websites\?tab=studio&site=default-site$/)
assert.equal(await page.getByLabel('Studio website').inputValue(), 'default-site')
assert.match(await frame().locator('h1').first().innerText(), /Grow your business/)
for (const name of ['Global Design', 'Selected styles', 'Brand', 'David']) await aside().getByRole('button', { name, exact: true }).waitFor()
const siteH1 = await css('h1', 'fontSize')
await page.screenshot({ path: `${out}/studio-editor-desktop.png` })
passed.push('Studio opens directly with canvas, panels and the Example Company site preselected (/websites?tab=studio&site=default-site)')

// 3. Sources & Branding: bounded check of local, Windows reference, Drive, Brain and brand kit.
await page.getByRole('button', { name: 'Sources & Branding', exact: true }).click()
await page.getByRole('button', { name: 'Check sources', exact: true }).click()
await page.getByText('Sources checked.', { exact: true }).waitFor({ timeout: 180000 })
const states = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('.studio-source-card[data-source]')].map(c => [c.dataset.source, c.querySelector('header .studio-state')?.textContent])))
assert.deepEqual(states, { local: 'Connected', drive: 'Connected', brain: 'Connected', brand: 'Connected' })
const sourcesText = await page.locator('.studio-sources').innerText()
for (const text of ['/home/operator/ttros-projects/default-site', 'business_brain:memory/website_and_content.md', 'time-to-revenue-brand-kit.pdf', 'Executive Graphite', 'Champagne Signal', 'proposed — awaiting your approval', 'No website asset folder selected', 'EXAMPLE_DRIVE_ID_0005_000000000000', '0 model calls']) assert.ok(sourcesText.includes(text), text)
assert.ok(await page.locator('.studio-logos img').count() >= 4)
await page.screenshot({ path: `${out}/sources-and-branding.png`, fullPage: true })
passed.push('Sources & Branding shows truthful Connected states, last sync, references, brand kit source, palette, logos and conflicts')

// 4. Drive folder selection refuses the excluded client folder; nothing is saved.
await page.getByLabel('Choose asset folder (Drive link or ID)').fill('https://drive.google.com/drive/folders/EXAMPLE_DRIVE_ID_0005_000000000000')
await page.getByRole('button', { name: 'Verify & select' }).click()
await page.getByText('That folder is excluded: it holds unrelated client material.').waitFor()
passed.push('Drive asset folder selection verifies IDs and refuses the excluded client folder')

// 5. Approve the proposed brand. The draft is not silently restyled.
await page.getByRole('button', { name: 'Approve brand configuration' }).click()
await page.getByText(/Brand configuration approved/).first().waitFor()
await page.getByRole('button', { name: 'Visual editor', exact: true }).click()
assert.equal(await css('h1', 'fontSize'), siteH1)
await tab('Brand')
await aside().getByText('Owner overrides (5)').waitFor()
passed.push('Approval makes brand rules authoritative without restyling the draft; 5 existing deviations shown as owner overrides')

// 6. An explicit owner override is possible and stays visible.
await tab('Global Design'); await control('Accent', '#0969a5'); await pause()
await tab('Brand'); await aside().getByText('Owner overrides (6)').waitFor()
assert.ok((await aside().locator('.studio-overrides').innerText()).includes('accent #0969a5'))
await page.screenshot({ path: `${out}/brand-panel-owner-override.png` })
await aside().getByRole('button', { name: 'Apply approved brand to draft' }).click(); await pause()
await aside().getByText('The draft follows the approved brand.').waitFor()
await page.evaluate(() => { const v = document.querySelector('[aria-label="Switch to Desktop viewport"]'); v?.click() }); await pause()
assert.equal(await css('[data-studio-section="Section0"] .site-button', 'borderRadius'), '6px')
assert.equal(await css('.editorial-link .site-arrow', 'color'), 'rgb(184, 155, 99)')
passed.push('Owner override listed, then Apply approved brand produced a compliant draft (6px buttons, Champagne accent)')
await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
await aside().getByText(/server version v1 \(manual\)/).waitFor()
passed.push('Manual edits saved as server version v1 (manual) with zero model requests')
assert.equal(proposeCount(), 0, 'routine editing must not request a proposal')

// 7. David: explicit request → structured operations → preview → accept/undo → versioned draft.
const request = 'Using our brand guidelines, change the homepage headline, update the accent colour, move the workflow section and round the buttons.'
const newOrder = ['Section0', 'Section4', 'Section1', 'Section2', 'Section3', 'Section5', 'Section6', 'Section7', 'Section8']
proposalQueue.push({ proposal: { summary: 'Brand-led headline, Revenue Gold accent, process earlier, rounder buttons.', edits: [{ section: 'Section0', field: 'text0', text: 'Grow revenue. ' }, { section: 'Section0', field: 'text1', text: 'Willow grove ember' }], order: newOrder, design: { accent: '#c6a15b', buttonRadius: 10 } }, usage: { total_tokens: 0 }, context_sources: ['drive:1pAR0Hi83yb1jjyaHBjqniVJqROIAtwz_', 'business_brain:memory/website_and_content.md', 'business_brain:memory/positioning.md'], brand_status: 'approved' })
await tab('David')
await aside().locator('#studio-request').fill(request)
await aside().getByRole('button', { name: 'Propose changes' }).click()
await aside().getByRole('heading', { name: 'Review proposal' }).waitFor()
assert.equal(proposeCount(), 1)
assert.equal(proposals[0].project, 'default-site'); assert.equal(proposals[0].request, request)
const review = await aside().locator('.studio-proposal').innerText()
for (const text of ['approved rules enforced', 'business_brain:memory/website_and_content.md', 'Grow revenue.', 'Section order: Hero → How we work']) assert.ok(review.includes(text), text)
await aside().locator('.studio-proposal-frame iframe').waitFor()
await page.screenshot({ path: `${out}/david-proposal-preview.png` })
assert.equal(await css('[data-studio-section="Section0"] .site-button', 'borderRadius'), '6px', 'preview does not touch the draft')
await aside().getByRole('button', { name: 'Accept changes' }).click(); await pause()
assert.deepEqual(await order(), newOrder)
assert.match(await frame().locator('h1').first().innerText(), /^Grow revenue\./)
assert.equal(await css('[data-studio-section="Section0"] .site-button', 'borderRadius'), '10px')
assert.equal(await css('.editorial-link .site-arrow', 'color'), 'rgb(198, 161, 91)')
await page.locator('[aria-label=undo]').click(); await pause()
assert.equal(await css('[data-studio-section="Section0"] .site-button', 'borderRadius'), '6px')
await page.locator('[aria-label=redo]').click(); await pause()
assert.equal(await css('[data-studio-section="Section0"] .site-button', 'borderRadius'), '10px')
passed.push('David request → structured operations → preview → accept → undo/redo on the live canvas')

// 8. An off-brand proposal is blocked and can be rejected.
proposalQueue.push({ proposal: { summary: 'Neon accent', edits: [], design: { accent: '#7c3aed' } }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Make the accent bright purple.')
await aside().getByRole('button', { name: 'Propose changes' }).click()
await aside().getByText(/Off-brand proposal blocked: accent #7c3aed/).waitFor()
assert.ok(await aside().getByRole('button', { name: 'Accept changes' }).isDisabled())
await aside().getByRole('button', { name: 'Reject' }).click()
passed.push('Off-brand David proposal blocked in review and rejected (server also rejects; see pytest)')

// 9. Save the accepted David change as a versioned server draft through the same edit API.
await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
await aside().getByText(/server version v2 \(david\)/).waitFor()
passed.push('Accepted David change saved as server version v2 (origin david, operations re-validated server-side)')
await page.screenshot({ path: `${out}/studio-after-david.png` })

// 10. Mobile layout of Studio and Sources.
await page.setViewportSize({ width: 390, height: 844 })
await page.getByRole('button', { name: 'Sources & Branding', exact: true }).click(); await pause()
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
await page.screenshot({ path: `${out}/sources-mobile.png`, fullPage: false })
assert.ok(overflow <= 1, `mobile overflow ${overflow}`)
await page.getByRole('button', { name: 'Visual editor', exact: true }).click(); await pause()
await page.screenshot({ path: `${out}/studio-mobile.png` })
await page.setViewportSize({ width: 1440, height: 1000 })
passed.push('390px Sources & Branding has no horizontal overflow')

// 11. Existing Websites tools and every Dashboard module still render.
await nav.getByRole('button', { name: 'SEO Strategy', exact: true }).click()
await page.getByRole('heading', { name: 'SEO Strategy', exact: true }).or(page.getByText('Registered website').first()).first().waitFor()
await nav.getByRole('button', { name: 'Website Changes', exact: true }).click()
await page.getByRole('heading', { name: 'Prepare a website change' }).waitFor()
await nav.getByRole('button', { name: 'History', exact: true }).click()
await page.getByRole('heading', { name: 'Preparation history' }).waitFor()
passed.push('SEO Strategy, Website Changes and History still work beside Studio')
const modules = {}
for (const path of ['/', '/websites', '/crm']) { await page.goto(`${base}${path}`); await pause(1200) }
const sidebar = await page.evaluate(() => [...document.querySelectorAll('nav[aria-label="Workspace navigation"] button, nav[aria-label="Workspace navigation"] a')].map(b => b.textContent.trim()).filter(Boolean))
for (const label of sidebar) {
  const before = errors.length
  await page.locator('nav[aria-label="Workspace navigation"]').getByText(label, { exact: true }).first().click()
  await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 40, null, {timeout:15000})
  const text = (await page.locator('main').first().innerText().catch(() => '')).trim()
  modules[label] = { rendered: text.length > 40, page_errors: errors.length - before }
}
assert.ok(Object.values(modules).every(m => m.rendered && m.page_errors === 0), JSON.stringify(modules))
passed.push(`${Object.keys(modules).length} Dashboard modules rendered without page errors`)

const result = { passed, modules, mocked_dashboard_health:process.env.STUDIO_PROOF_OFFLINE_DASHBOARD==='1', page_errors: errors, proposal_requests: proposeCount(), proposal_requests_before_david: 0,
  mocked_proposals: proposals.map(p => ({ project: p.project, request: p.request })), blocked_non_studio_writes: blocked,
  studio_requests: requests.filter(([, p]) => p.startsWith('/api/website/studio/')).length, real_model_calls: 0 }
await writeFile(`${out}/browser-acceptance.json`, JSON.stringify(result, null, 2))
console.log(JSON.stringify({ ...result, modules: Object.keys(modules) }, null, 1))
await browser.close()
