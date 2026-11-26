// Schema v5 (website 215679e) acceptance in the real Studio editor. Runs only against the isolated
// offline API (TTROS_STUDIO_OFFLINE=1: David cannot be constructed) and the worktree's Vite server.
// Real Puck canvas edits → server save → reload from the server → re-edit → save → the editor's own
// "Build saved real-site preview" → real Next.js build, compared with an untouched 215679e build.
// Env: PLAYWRIGHT_MODULE, STUDIO_URL, STUDIO_API, BASELINE_URL, STUDIO_PROOF_DIR.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
const base = process.env.STUDIO_URL, api = process.env.STUDIO_API, baselineUrl = process.env.BASELINE_URL, out = process.env.STUDIO_PROOF_DIR
await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } })
page.setDefaultTimeout(30000)
const errors = [], passed = [], requests = []
page.on('pageerror', e => errors.push(e.message))
page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api')) requests.push([r.method(), u.pathname]) })
const pause = (n = 500) => page.waitForTimeout(n)
const frame = () => page.frameLocator('.studio-editor iframe')
const aside = () => page.locator('.studio-assistant')
const native = () => page.locator('.studio-native-inspector')
const tab = async label => { await aside().locator('.studio-tabs').getByRole('button', { name: label, exact: true }).click(); await pause() }
const norm = v => String(v ?? '').replace(/\s+/g, ' ').trim()
const text = selector => frame().locator(selector).innerText().then(norm)
const status = () => aside().locator('.studio-status').innerText()
async function control(label, value) {
 await page.evaluate(([label, value]) => {
  const e = [...document.querySelectorAll('.studio-assistant [aria-label]')].find(n => n.getAttribute('aria-label') === label)
  if (!e) throw Error(`No control ${label}`)
  const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, String(value))
  e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }))
 }, [label, value]); await pause(500)
}
// Real inline canvas editing (Puck contenteditable), exactly as an owner types.
async function inline(selector, value, nth = 0) {
 const el = frame().locator(`${selector} [contenteditable]`).nth(nth)
 await el.scrollIntoViewIfNeeded(); await el.hover(); await pause(300); await el.click(); await pause(200)
 await page.keyboard.press('Control+A'); await page.keyboard.type(value); await pause(600)
 await page.locator('.studio-assistant h2').first().click(); await pause(400)
}
async function openStudio() {
 await page.goto(base + '/websites?tab=studio&site=default-site')
 await page.getByRole('button', { name: 'Open unpublished release candidate' }).click()
 await frame().locator('figure.workflow-figure').waitFor(); await pause(1000)
}
async function pick(id) { await page.locator(`.studio-pages [data-page="${id}"]`).click(); await pause(800); if (id !== 'site') await frame().locator(`main[data-studio-page="${id}"]`).waitFor() }
async function selectGraphic() {
 const el = frame().locator('figure.workflow-figure'); await el.scrollIntoViewIfNeeded(); const r = await el.boundingBox()
 await page.mouse.click(r.x + r.width * .5, r.y + r.height * .55); await pause()
 assert.equal(await page.locator('.studio-page-line').getAttribute('data-puck-selected-type'), 'WorkflowFigure')
 await tab('Selected styles'); await native().waitFor()
}
async function save(page_, version) {
 await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
 await aside().getByText(new RegExp(`server version v${version} \\(`)).waitFor()
}
async function loadServerVersion(version) {
 await page.getByLabel('Saved draft version', { exact: true }).selectOption(String(version))
 await page.getByRole('button', { name: 'Load selected version', exact: true }).click(); await pause(800)
 assert.match(await status(), new RegExp(`Loaded server version v${version}`))
}
// A clean browser: no local Studio drafts, so everything shown comes from the server.
const fresh = async () => { await page.evaluate(() => localStorage.clear()); assert.equal(await page.evaluate(() => localStorage.length), 0); await openStudio() }

// ---- 1. Homepage: hero, hero signature and workflow graphic ------------------------------------
await openStudio()
await pick('home')
await page.locator('.studio-editor').screenshot({ path: out + '/editor-home-baseline.png' })
await inline('.home-hero h1', 'Grow with clear systems. ')
await inline('.home-hero .hero-eyebrow', 'Systems consulting · Example City, Canada')
await inline('.hero-signature p', 'Clear processes. Connected systems. Practical help.')
assert.equal(await text('.home-hero h1'), 'Grow with clear systems. Not your workload.')
await selectGraphic()
await native().getByLabel('Title line 1', { exact: true }).fill('A new lead.'); await pause()
await native().getByLabel('Step 2 title', { exact: true }).fill('Assign one owner'); await pause()
await native().getByLabel('Graphic accent colour', { exact: true }).fill('#527a65'); await pause()
await native().getByLabel('Graphic title size (%)', { exact: true }).fill('115'); await pause()
await native().getByLabel('Graphic position on phones', { exact: true }).selectOption('before'); await pause()
const figure = frame().locator('figure.workflow-figure')
assert.equal(await frame().locator('.workflow-story li').nth(1).locator('strong').innerText(), 'Assign one owner')
assert.equal(await figure.evaluate(e => getComputedStyle(e).borderTopColor), 'rgb(82, 122, 101)')
assert.equal(await figure.getAttribute('data-studio-mobile-order'), 'before')
assert.match(await figure.evaluate(e => e.style.getPropertyValue('--studio-workflow-title-scale')), /1\.15/)
await page.locator('.studio-editor').screenshot({ path: out + '/editor-home-edited.png' })
passed.push('homepage canvas: inline hero eyebrow/headline and hero-signature edits; WorkflowFigure title, step and accent/title-size/phone-order properties in the native inspector')
await save('home', 1)
await pick('site'); await save('site', 1)
// Reload from the server only: browser drafts cleared, then the saved server version loaded.
await fresh(); await pick('home')
assert.equal(await text('.home-hero h1'), 'Grow with clear systems. Not your workload.', 'a clean browser opens the latest saved server version')
await loadServerVersion(1)
assert.equal(await text('.home-hero h1'), 'Grow with clear systems. Not your workload.')
assert.equal(await frame().locator('.hero-signature p').innerText(), 'Clear processes. Connected systems. Practical help.')
assert.equal(await frame().locator('.workflow-story li').nth(1).locator('strong').innerText(), 'Assign one owner')
assert.equal(await frame().locator('figure.workflow-figure').getAttribute('data-studio-mobile-order'), 'before')
await selectGraphic()
assert.equal(await native().getByLabel('Graphic title size (%)', { exact: true }).inputValue(), '115')
await native().getByLabel('Step 3 title', { exact: true }).fill('Follow up the same day'); await pause()
await save('home', 2)
passed.push('homepage saved v1, reloaded from the server in a clean browser, re-edited (step 3) and saved v2')

// ---- 2. Service page: intro and the new "Dune cedar xenon yonde" section ------------------------
await pick('service-ai')
await page.locator('.studio-editor').screenshot({ path: out + '/editor-service-ai-baseline.png' })
await inline('#starting-point h2', 'Start with one repeated task.')
await inline('.page-intro .page-lead', 'Based in Example City, EX. We work with your team from the first workflow to adoption.', 1)
await page.locator('.studio-assistant').getByText('Select a section', { exact: false }).count()
await frame().locator('#starting-point').click({ position: { x: 20, y: 20 } }); await pause()
await tab('Selected styles')
await control('Background mode', 'local'); await control('Local Background', '#e9e3d7')
assert.equal(await frame().locator('#starting-point').evaluate(e => getComputedStyle(e).backgroundColor), 'rgb(233, 227, 215)')
await page.locator('.studio-editor').screenshot({ path: out + '/editor-service-ai-edited.png' })
await save('service-ai', 1)
await fresh(); await pick('service-ai'); await loadServerVersion(1)
assert.equal(await frame().locator('#starting-point h2').innerText(), 'Start with one repeated task.')
await inline('#starting-point .editorial-copy p', 'After a first conversation, we agree scope, responsibilities and cost before work begins.', 1)
await save('service-ai', 2)
passed.push('service page: inline intro and first-step heading edits plus a local section style, saved v1, reloaded from the server, re-edited and saved v2')

// ---- 3. Build the saved versions with the editor's own control --------------------------------
await pick('home'); await loadServerVersion(2)
await page.getByLabel(/I reviewed known contrast failures/).check()
await page.getByRole('button', { name: 'Build saved real-site preview', exact: true }).click()
const started = Date.now()
let line = ''
while (Date.now() - started < 1500000) {
 await pause(10000)
 await page.getByRole('button', { name: 'Check build', exact: true }).click(); await pause(800)
 line = await page.locator('section[aria-label="Real Next.js preview"] p').first().innerText()
 if (!/building/.test(line)) break
}
assert.match(line, /ready/, line)
const previewUrl = await page.getByRole('link', { name: 'Open isolated preview' }).getAttribute('href')
const buildId = await page.locator('section[aria-label="Real Next.js preview"] code').first().innerText()
const sourceDiff = await page.locator('section[aria-label="Real Next.js preview"] pre').textContent()
await fs.writeFile(out + '/source.diff', sourceDiff)
assert.match(line, /homepage v2 · shared v1 · AI Implementation v2/)
passed.push(`editor built saved versions in an isolated real Next.js copy: ${line}`)

// ---- 4. Visual and computed-layout acceptance against the untouched 215679e build -------------
const viewports = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } }
const layout = {}
for (const [name, root] of [['baseline', baselineUrl], ['edited', previewUrl]]) for (const [vp, size] of Object.entries(viewports)) for (const route of ['/', '/services/ai-implementation']) {
 const view = await browser.newPage({ viewport: size })
 await view.goto(root.replace(/\/$/, '') + route, { waitUntil: 'networkidle' })
 const slug = route === '/' ? 'home' : 'service-ai'
 await view.screenshot({ path: `${out}/${name}-${slug}-${vp}.png`, fullPage: true })
 layout[`${name}:${slug}:${vp}`] = await view.evaluate(() => {
  const box = s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { top: Math.round(r.top + scrollY), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height) } }
  const css = (s, p) => { const e = document.querySelector(s); return e ? getComputedStyle(e)[p] : null }
  return { horizontalOverflow: document.documentElement.scrollWidth > innerWidth, h1Count: document.querySelectorAll('h1').length, ids: (() => { const all = [...document.querySelectorAll('[id]')].map(e => e.id); return all.length - new Set(all).size })(),
   h1: document.querySelector('h1')?.innerText, heroH1: box('.home-hero h1'), figure: box('figure.workflow-figure'), figureBorder: css('figure.workflow-figure', 'borderTopColor'), figureTitleSize: css('.workflow-figure figcaption strong', 'fontSize'),
   storyPSize: css('.workflow-story p', 'fontSize'), signature: document.querySelector('.hero-signature p')?.innerText, heroPadding: css('.home-hero', 'paddingTop'), eyebrow: document.querySelector('.hero-eyebrow')?.innerText,
   steps: [...document.querySelectorAll('.workflow-story li strong')].map(e => e.innerText), startingPoint: box('#starting-point'), startingPointH2: document.querySelector('#starting-point h2')?.innerText,
   startingPointBg: css('#starting-point', 'backgroundColor'), contactBand: box('.contact-band'), title: document.title, nav: [...document.querySelectorAll('header nav a')].map(a => a.getAttribute('href') + ' ' + a.innerText.trim()) }
 })
 await view.close()
}
await fs.writeFile(out + '/computed-layout.json', JSON.stringify(layout, null, 2))
for (const [key, value] of Object.entries(layout)) { assert.equal(value.horizontalOverflow, false, key); assert.equal(value.h1Count, 1, key); assert.equal(value.ids, 0, key) }
const L = k => layout[k]
assert.equal(norm(L('edited:home:desktop').h1), 'Grow with clear systems. Not your workload.')
assert.equal(L('edited:home:desktop').eyebrow, 'Systems consulting · Example City, Canada')
assert.equal(L('edited:home:desktop').signature, 'Clear processes. Connected systems. Practical help.')
assert.deepEqual(L('edited:home:desktop').steps, ['Yonder indigo delta yon', 'Assign one owner', 'Follow up the same day'])
assert.equal(L('edited:home:desktop').figureBorder, 'rgb(82, 122, 101)'); assert.equal(L('baseline:home:desktop').figureBorder, 'rgb(184, 155, 99)')
assert.ok(Math.abs(parseFloat(L('edited:home:desktop').figureTitleSize) / parseFloat(L('baseline:home:desktop').figureTitleSize) - 1.15) < 0.01)
assert.ok(L('baseline:home:mobile').figure.top > L('baseline:home:mobile').heroH1.top, 'source: graphic below the headline on phones')
assert.ok(L('edited:home:mobile').figure.top < L('edited:home:mobile').heroH1.top, 'edited: graphic above the headline on phones')
assert.ok(L('edited:home:desktop').figure.left > L('edited:home:desktop').heroH1.left, 'desktop keeps the two-column hero')
assert.equal(L('edited:service-ai:desktop').startingPointH2, 'Start with one repeated task.')
assert.equal(L('edited:service-ai:desktop').startingPointBg, 'rgb(233, 227, 215)')
for (const vp of ['desktop', 'mobile']) assert.ok(L(`edited:service-ai:${vp}`).startingPoint.top < L(`edited:service-ai:${vp}`).contactBand.top)
for (const k of ['home:desktop', 'home:mobile', 'service-ai:desktop', 'service-ai:mobile']) {
 assert.deepEqual(L('edited:' + k).nav, L('baseline:' + k).nav, `navigation unchanged ${k}`)
 assert.equal(L('edited:' + k).title, L('baseline:' + k).title, `title unchanged ${k}`)
}
for (const vp of ['desktop', 'mobile']) {
 assert.equal(L(`edited:home:${vp}`).heroPadding, L(`baseline:home:${vp}`).heroPadding, `hero padding parity ${vp}`)
 assert.equal(L(`edited:home:${vp}`).storyPSize, L(`baseline:home:${vp}`).storyPSize, `workflow step text size parity ${vp}`)
}
passed.push('desktop/mobile screenshots and computed layout: edits visible, no horizontal overflow, one H1, unique IDs, source parity for untouched hero spacing and step text, phone order and title scale as edited')

// ---- 5. Unrelated routes, navigation, graphics and asset references unchanged -----------------
const routes = ['/about', '/blog', '/resources', '/privacy', '/terms', '/pricing', '/how-we-work', '/our-work', '/services', '/services/systems-integration', '/services/business-process-automation', '/get-guide', '/free-assessment', '/training']
const unchanged = {}
for (const route of routes) {
 const read = async root => { const v = await browser.newPage(); await v.goto(root.replace(/\/$/, '') + route, { waitUntil: 'domcontentloaded' }); const r = await v.evaluate(() => ({
  status: document.title, main: document.querySelector('main')?.innerText, header: document.querySelector('header')?.innerText, footer: document.querySelector('footer')?.innerText,
  links: [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')), images: [...document.querySelectorAll('img')].map(i => i.getAttribute('src')), svgs: document.querySelectorAll('svg').length,
  jsonLd: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent) })); await v.close(); return r }
 const [a, b] = [await read(baselineUrl), await read(previewUrl)]
 unchanged[route] = Object.fromEntries(Object.keys(a).map(k => [k, JSON.stringify(a[k]) === JSON.stringify(b[k])]))
 for (const [k, same] of Object.entries(unchanged[route])) assert.ok(same, `${route} ${k} changed`)
}
await fs.writeFile(out + '/unchanged-routes.json', JSON.stringify(unchanged, null, 2))
passed.push(`${routes.length} unrelated routes identical to the untouched build: title, main text, header, footer, every link, image source, SVG count and JSON-LD`)

// ---- 6. Zero model calls; David stays a separate explicit action ------------------------------
assert.equal(requests.filter(([, p]) => p.endsWith('/propose')).length, 0, 'manual editing, saving, building and previewing never requested a proposal')
await tab('David')
assert.ok(await page.getByRole('button', { name: 'Propose changes', exact: true }).isVisible())
assert.ok(await page.getByRole('button', { name: 'Propose changes', exact: true }).isDisabled(), 'no request until the owner writes one')
const local = await (await fetch(api + '/api/website/studio/local-mode')).json()
assert.deepEqual(local, { offline: true, model_calls: 0 })
passed.push('zero proposal requests across editing, saving, reloading, building and previewing; David is only the explicit Propose changes action (offline: cannot be constructed)')

await page.getByRole('button', { name: 'Close preview', exact: true }).click(); await pause(1000)
assert.match(await page.locator('section[aria-label="Real Next.js preview"] p').first().innerText(), /stopped/)
assert.deepEqual(errors, [])
const evidence = { passed, buildLine: line, nextBuildId: buildId, previewUrl, requests: requests.length, apiCalls: [...new Set(requests.map(([m, p]) => `${m} ${p}`))].sort(), proposeRequests: 0, pageErrors: errors }
await fs.writeFile(out + '/browser-acceptance.json', JSON.stringify(evidence, null, 2))
console.log(JSON.stringify(evidence, null, 2))
await browser.close()
