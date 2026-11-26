// Stage 2 browser acceptance: add, duplicate, edit, move, remove and restore real website sections.
// Runs against the worktree editor (4340) and offline Studio API (4341) with a disposable state
// directory seeded with two Stage 1.5 (version 2) server drafts. Only POST /propose is mocked and
// counted; every other Studio request reaches the real deterministic API. Non-Studio writes are
// blocked. Install playwright separately; set PLAYWRIGHT_MODULE.
import assert from 'node:assert/strict'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.STUDIO_URL || 'http://127.0.0.1:4340'
const api = process.env.STUDIO_API || 'http://127.0.0.1:4341'
const out = process.env.STUDIO_PROOF_DIR || '../../proofs/website_studio_stage2_20261118'
await mkdir(out, { recursive: true })
const SITE = `${api}/api/website/studio/sites/default-site`

// Owner approval of the extracted brand in the disposable state (the same route the Sources panel uses).
const brand = await (await fetch(`${SITE}/brand`)).json()
if (!brand.approved) {
  const r = await fetch(`${SITE}/brand/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fingerprint: brand.proposed.fingerprint, actor: 'owner' }) })
  assert.ok(r.ok, 'brand approval in disposable state')
}

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
const page = await context.newPage()
page.setDefaultTimeout(20000)
const errors = [], requests = [], blocked = [], proposals = [], passed = [], clicks = { propose: 0 }
page.on('pageerror', e => errors.push(e.message))
const proposalQueue = []
await page.route('**/api/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push([req.method(), path])
  if (path === '/api/website/studio/propose') {
    proposals.push(req.postDataJSON())
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(proposalQueue.shift()) })
  }
  if (path.startsWith('/api/website/studio/') || req.method() === 'GET') return route.continue()
  blocked.push([req.method(), path])
  return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'blocked by Stage 2 proof' }) })
})
const pause = (ms = 400) => page.waitForTimeout(ms)
const frame = () => page.frameLocator('.studio-editor iframe')
const inFrame = (fn, arg) => page.evaluate(([f, a]) => new Function('doc', 'arg', f)(document.querySelector('.studio-editor iframe').contentDocument, a), [fn, arg])
const order = () => inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].map(n => n.dataset.studioSection)")
const types = () => inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].map(n => n.dataset.studioType)")
const css = (selector, prop) => inFrame("const e = doc.querySelector(arg[0]); if (!e) throw Error('missing ' + arg[0]); return doc.defaultView.getComputedStyle(e)[arg[1]]", [selector, prop])
const text = selector => inFrame("const e = doc.querySelector(arg); if (!e) throw Error('missing ' + arg); return e.innerText.trim()", selector)
const attr = (selector, name) => inFrame("const e = doc.querySelector(arg[0]); return e ? e.getAttribute(arg[1]) : null", [selector, name])
const domIds = () => inFrame("return [...doc.querySelectorAll('main.editorial [id]')].map(e => e.id)")
const aside = () => page.locator('.studio-assistant')
const tab = name => aside().getByRole('navigation', { name: 'Studio panels' }).getByRole('button', { name, exact: true }).click()
const status = () => aside().locator('.studio-status').innerText()
const listSelect = async id => { await aside().locator('details.studio-sections').evaluate(d => { d.open = true }); await aside().locator(`.studio-sections li[data-section="${id}"] .studio-section-select`).click(); await pause() }
const control = (label, value) => page.evaluate(([l, v]) => {
  const e = [...document.querySelectorAll('.studio-assistant input,.studio-assistant select')].find(n => n.getAttribute('aria-label') === l)
  if (!e) throw Error(`control ${l}`)
  let p = e.parentElement; while (p) { if (p.tagName === 'DETAILS') p.open = true; p = p.parentElement }
  const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, String(v))
  e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }))
}, [label, value])
const routine = () => requests.filter(([, p]) => p === '/api/website/studio/propose').length
const save = async version => {
  await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
  await aside().getByText(new RegExp(`server version v${version} \\(`)).waitFor()
}
const unique = list => new Set(list).size === list.length
const sel = id => `[data-studio-section="${id}"]`

// 1. Open Studio; load the latest server draft, saved by Stage 1.5 as a version-2 envelope.
await page.addInitScript(() => { try { if (!sessionStorage.getItem('stage2-init')) { localStorage.removeItem('ttros-studio:global:default-site:home:v1'); sessionStorage.setItem('stage2-init', '1') } } catch { /* sandboxed frames */ } })
await page.goto(`${base}/websites?tab=studio&site=default-site`)
await frame().locator('h1').first().waitFor()
await page.evaluate(() => document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click()); await pause()
await aside().getByRole('button', { name: 'Load latest version' }).click()
await aside().getByText(/Loaded server version v2/).waitFor()
const legacyOrder = ['Section0', 'Section4', 'Section1', 'Section2', 'Section3', 'Section5', 'Section6', 'Section7', 'Section8']
assert.deepEqual(await order(), legacyOrder)
assert.match(await text(`${sel('Section0')} h1`), /^Grow revenue\./)
assert.equal(await attr(`${sel('Section0')} .site-button`, 'href'), '/free-assessment')
passed.push('Stage 1.5 server history (version-2 envelopes v1, v2) loads unchanged: IDs, David-accepted text, order and design; links take the original destinations')

// 2. The section palette shows every registered real website component, with limits explained.
await tab('Sections')
const cards = aside().locator('.studio-palette li')
assert.equal(await cards.count(), 9)
await aside().locator('.studio-palette iframe').first().waitFor()
assert.match(await aside().locator('.studio-palette li[data-section-type="Section0"] .studio-limit').innerText(), /exactly one hero/)
assert.match(await aside().locator('.studio-palette li[data-section-type="Section7"] .studio-limit').innerText(), /FAQ structured data/)
await page.screenshot({ path: `${out}/sections-palette.png` })
passed.push('Visual palette of the 9 registered website section types (live thumbnails); Hero and FAQ limits explained')

// 3. Add "Iris bravoa" below the hero. It is selected for direct editing and inherits the design.
await listSelect('Section0'); await tab('Sections')
await aside().getByRole('button', { name: 'Add How we work below selected section' }).click(); await pause()
const A = (await status()).match(/\((how-we-work-[a-z0-9]{6})\)/)?.[1]
assert.ok(A, `added id in status: ${await status()}`)
assert.deepEqual(await order(), ['Section0', A, ...legacyOrder.slice(1)])
assert.equal(await aside().getByTestId('selected-section-id').innerText(), A)
assert.equal(await attr(sel(A), 'data-studio-type'), 'Section4')
assert.equal(await attr(sel(A), 'id'), `how-it-works-${A}`)
assert.equal(await attr(sel('Section4'), 'id'), 'how-it-works')
assert.equal(await text(`${sel(A)} h2`), await text(`${sel('Section4')} h2`))
assert.equal(await css(`${sel(A)} h2`, 'fontFamily'), await css(`${sel('Section4')} h2`, 'fontFamily'))
await frame().locator(`${sel(A)} h2`).scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/added-section.png` })
passed.push(`Add How we work below the hero: new instance ${A}, selected, original content, inherited global design, scoped anchor #how-it-works-${A}`)

// 4. Duplicate it, then edit ONLY the duplicate: inline text, link and a local style.
await aside().getByRole('button', { name: 'Duplicate section' }).click(); await pause()
const B = (await status()).match(/ as (how-we-work-[a-z0-9]{6})\./)?.[1]
assert.ok(B && B !== A, `duplicate id: ${await status()}`)
assert.deepEqual(await order(), ['Section0', A, B, ...legacyOrder.slice(1)])
const h2 = frame().locator(`${sel(B)} h2 [contenteditable]`).first()
await h2.hover(); await pause(300); await h2.click(); await pause(200); await page.keyboard.press('Control+A'); await page.keyboard.type('Two ways to start working together.'); await pause(600)
await frame().locator(`${sel(B)} p`).first().click(); await pause()
assert.equal(await text(`${sel(B)} h2`), 'Two ways to start working together.')
assert.equal(await text(`${sel(A)} h2`), 'Orchid iris kelp ember basil zephy')
await listSelect(B); await tab('Selected styles')
const link = aside().getByLabel('Link 1 destination')
for (const [bad, reason] of [['javascript:alert(1)', /site page/], ['/no-such-page', /not a page on this website/], ['#nowhere', /not on this page/], ['https://user:pw@example.com', /site page|credentials/]]) {
  await link.fill(bad); await link.press('Enter'); await pause()
  assert.match(await status(), reason, bad)
  assert.equal(await link.inputValue(), '/how-we-work')
}
await link.fill('#how-it-works'); await link.press('Enter'); await pause()
assert.match(await status(), /Link 1 now points to #how-it-works/)
assert.equal(await attr(`${sel(B)} a`, 'href'), '#how-it-works')
assert.equal(await attr(`${sel(A)} a`, 'href'), '/how-we-work')
await control('Background mode', 'local'); await pause()
await control('Local Background', '#d8d0c2'); await pause()
assert.equal(await css(sel(B), 'backgroundColor'), 'rgb(216, 208, 194)')
assert.equal(await css(sel(A), 'backgroundColor'), await css(sel('Section4'), 'backgroundColor'))
await frame().locator(sel(B)).scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/duplicate-edited.png` })
passed.push(`Duplicate ${A} → ${B}; only ${B} changed: inline heading, link (#how-it-works; javascript:, unknown page, missing anchor and credential URLs refused), local background`)

// 5. Reorder both independently: drag the duplicate in the section list, move the other with arrows.
await tab('Sections')
// HTML5 drag-and-drop on the existing section list (same events as the Stage 1 proof; real dragTo
// is unreliable inside the scrolling panel).
await page.evaluate(([from, to]) => {
  const source = document.querySelector(`.studio-sections li[data-section="${from}"]`), target = document.querySelector(`.studio-sections li[data-section="${to}"]`)
  const transfer = new DataTransfer()
  source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: transfer }))
  target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }))
  target.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }))
}, [B, 'Section6']); await pause()
// Existing list semantics: the dragged section takes the drop target's position.
let expected = ['Section0', A, B, ...legacyOrder.slice(1)]
const dropAt = expected.indexOf('Section6'); expected.splice(expected.indexOf(B), 1); expected.splice(dropAt, 0, B)
assert.deepEqual(await order(), expected, 'drag moved only the duplicate')
await aside().getByRole('button', { name: `Move ${A} down` }).click(); await pause()
await aside().getByRole('button', { name: `Move ${A} down` }).click(); await pause()
expected = expected.filter(id => id !== A); expected.splice(3, 0, A)
assert.deepEqual(await order(), expected, 'arrows moved only the added section')
passed.push(`Reordered independently: ${B} dragged onto Founder's position; ${A} moved down twice`)

// 6. Global branding: approved brand + a global token flow to both copies; the local override stays.
await tab('Brand')
await aside().getByRole('button', { name: 'Apply approved brand to draft' }).click(); await pause()
await tab('Global Design'); await control('Accent', '#c6a15b'); await control('H2 weight', 650); await pause()
for (const id of [A, B, 'Section4']) {
  assert.equal(await css(`${sel(id)} .site-arrow`, 'color'), 'rgb(198, 161, 91)', `${id} accent inherited`)
  assert.equal(await css(`${sel(id)} h2`, 'fontWeight'), '650', `${id} H2 weight inherited`)
}
assert.equal(await css(sel(B), 'backgroundColor'), 'rgb(216, 208, 194)', 'local override survives global change')
assert.equal(await css(sel(A), 'backgroundColor'), await css(sel('Section4'), 'backgroundColor'))
await tab('Brand'); assert.match(await aside().locator('.studio-brand-panel').innerText(), new RegExp(`${B}\\.section\\.background|Owner overrides`))
passed.push('Approved brand and global accent/H2 weight inherited by both copies and the original; the duplicate\'s local background is kept')

// 7. Save (browser + server v3), reload: IDs, order, text, links and styles persist.
const before = { order: await order(), ids: await domIds() }
assert.ok(unique(before.ids), 'unique DOM IDs on the page')
await save(3)
const v3 = await (await fetch(`${SITE}/drafts/3`)).json()
assert.equal(v3.envelope.version, 4)
assert.deepEqual(v3.envelope.data.content.map(i => i.props.id), before.order)
await page.reload(); await frame().locator('h1').first().waitFor(); await pause(800)
assert.deepEqual(await order(), before.order)
assert.deepEqual(await domIds(), before.ids)
assert.equal(await text(`${sel(B)} h2`), 'Two ways to start working together.')
assert.equal(await attr(`${sel(B)} a`, 'href'), '#how-it-works')
assert.equal(await css(sel(B), 'backgroundColor'), 'rgb(216, 208, 194)')
passed.push('Saved as server v3 (envelope version 4) and in the browser; reload keeps IDs, order, text, links and styles')

// 8. Remove, undo, redo; guarded removals explain themselves.
await listSelect(B); await tab('Sections')
await aside().getByRole('button', { name: 'Remove section', exact: true }).click(); await pause()
assert.ok(!(await order()).includes(B))
await aside().getByRole('button', { name: 'Undo', exact: true }).click(); await pause()
assert.deepEqual(await order(), before.order)
assert.equal(await text(`${sel(B)} h2`), 'Two ways to start working together.')
await aside().getByRole('button', { name: 'Redo', exact: true }).click(); await pause()
assert.ok(!(await order()).includes(B))
await aside().getByRole('button', { name: 'Undo', exact: true }).click(); await pause()
assert.deepEqual(await order(), before.order)
assert.deepEqual(await domIds(), before.ids)
await listSelect('Section0'); await tab('Sections')
assert.ok(await aside().getByRole('button', { name: 'Remove section', exact: true }).isDisabled())
assert.ok(await aside().getByRole('button', { name: 'Duplicate section' }).isDisabled())
await listSelect('Section4'); await tab('Sections')
assert.ok(await aside().getByRole('button', { name: 'Remove section', exact: true }).isDisabled())
assert.match(await aside().locator('.studio-section-tools > p[role=note]').innerText(), new RegExp(`\\(${B}\\) links to #how-it-works`))
passed.push(`Remove ${B} → Undo restores the same ID and edits → Redo → Undo; Hero cannot be removed or duplicated; the original How we work cannot be removed while ${B} links to #how-it-works`)

// 9. David (explicit, mocked): add and duplicate through the same validated operations; no removal.
proposalQueue.push({ proposal: { summary: 'Add a proof band after the hero and a second contact band at the end.', sections: [{ op: 'add', type: 'Section5', id: 'our-work-proof', place: 'after', ref: 'Section0' }, { op: 'duplicate', source: 'Section8', id: 'contact-closing', place: 'end' }], edits: [{ section: 'our-work-proof', field: 'text1', text: 'Results from recent client systems.' }, { section: 'contact-closing', field: 'text0', text: 'Ready when you are.' }], styles: [{ section: 'contact-closing', target: 'section', values: { background: '#2b2f32' } }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await tab('David')
await aside().locator('#studio-request').fill('Add a proof band after the hero and repeat the contact band at the end.')
await aside().getByRole('button', { name: 'Propose changes' }).click(); clicks.propose++
await aside().getByRole('heading', { name: 'Review proposal' }).waitFor()
const review = await aside().locator('.studio-proposal').innerText()
for (const t of ['Add Our work', 'new section our-work-proof after Hero', 'Duplicate Contact', 'new section contact-closing at end', 'Ready when you are.']) assert.ok(review.includes(t), t)
assert.ok(!(await order()).includes('our-work-proof'), 'preview does not touch the draft')
await aside().getByRole('button', { name: 'Accept changes' }).click(); await pause()
const afterDavid = await order()
assert.equal(afterDavid[1], 'our-work-proof'); assert.equal(afterDavid.at(-1), 'contact-closing')
assert.equal(await text(`${sel('contact-closing')} h2`), 'Ready when you are.')
assert.equal(await text(`${sel('Section8')} h2`), 'Indigo grove iris quartz glade fjo')
assert.equal(await attr(sel('contact-closing'), 'id'), 'final-cta-contact-closing')
await save(4)
const v4 = await (await fetch(`${SITE}/drafts`)).json()
assert.equal(v4.drafts.at(-1).origin, 'david')
proposalQueue.push({ proposal: { summary: 'Remove the founder section.', edits: [], sections: [{ op: 'remove', id: 'Section6' }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Remove the founder section.')
await aside().getByRole('button', { name: 'Propose changes' }).click(); clicks.propose++
await aside().getByText('Section removal is a manual action.').waitFor()
assert.ok((await order()).includes('Section6'))
passed.push('Explicit David request → add + duplicate with new IDs → preview → accept → saved v4 (origin david); a David removal is refused')

// 10. Export the validated draft for the real website renderer. Stage 2.1: known WCAG AA contrast
// failures (the default Hero accent line) must be acknowledged by the owner before export.
if (await aside().locator('.studio-contrast-chip[data-state=fail]').count()) { await aside().locator('.studio-contrast-chip').click(); await aside().locator('.studio-ack input').check() }
const [download] = await Promise.all([page.waitForEvent('download'), page.locator('.studio-editor').getByRole('button', { name: 'Export site draft' }).click()])
await download.saveAs(`${out}/stage2-site-draft.json`)
const exported = JSON.parse(await readFile(`${out}/stage2-site-draft.json`, 'utf8'))
assert.equal(exported.version, 4)
assert.deepEqual(exported.data.content.map(i => i.props.id), await order())
assert.ok(unique(await domIds()), 'unique DOM IDs after David additions')
passed.push('Export site draft: version-3 envelope matching the canvas')

// 11. Mobile Studio without horizontal overflow.
await page.setViewportSize({ width: 390, height: 844 }); await tab('Sections'); await pause()
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
await page.screenshot({ path: `${out}/studio-mobile-sections.png` })
assert.ok(overflow <= 1, `mobile overflow ${overflow}`)
await page.setViewportSize({ width: 1440, height: 1000 })
await tab('Sections'); await page.screenshot({ path: `${out}/studio-editor-desktop.png` })
passed.push('390px Studio with the Sections panel has no horizontal overflow')

// Zero-AI accounting: proposal requests equal explicit David clicks; nothing else reached a model route.
assert.equal(routine(), clicks.propose, 'only explicit David clicks reached /propose')
assert.deepEqual(blocked, [])
assert.deepEqual(errors, [])
const result = { passed, ids: { added: A, duplicate: B, david: ['our-work-proof', 'contact-closing'] }, finalOrder: await order(), finalTypes: await types(),
  proposeRequests: routine(), explicitDavidClicks: clicks.propose, nonProposalStudioRequests: requests.filter(([, p]) => p !== '/api/website/studio/propose' && p.startsWith('/api/website/studio/')).length,
  blockedWrites: blocked, pageErrors: errors, modelCalls: 0, proposalsMocked: true }
await writeFile(`${out}/browser-acceptance.json`, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify({ passed: passed.length, ...result.ids, proposeRequests: result.proposeRequests }, null, 2))
await browser.close()
