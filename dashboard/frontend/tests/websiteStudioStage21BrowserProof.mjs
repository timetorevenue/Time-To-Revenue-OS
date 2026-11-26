// Stage 2.1 browser acceptance: WCAG AA contrast warnings, inherited colours checked against the
// real rendered canvas, verified alternatives, export acknowledgement, David gating, and the
// Sections palette as the only section-insertion interface (Puck's Blocks rail replaced).
// Runs against the worktree editor (4340) and offline Studio API (4341) with a disposable state
// directory. Only POST /propose is mocked and counted; every other Studio request reaches the real
// deterministic API. Non-Studio writes are blocked. Install playwright separately; set PLAYWRIGHT_MODULE.
import assert from 'node:assert/strict'
import { mkdir, writeFile, readFile } from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.STUDIO_URL || 'http://127.0.0.1:4340'
const api = process.env.STUDIO_API || 'http://127.0.0.1:4341'
const out = process.env.STUDIO_PROOF_DIR || '../../proofs/website_studio_stage21_20261118'
await mkdir(out, { recursive: true })
const SITE = `${api}/api/website/studio/sites/default-site`

const brand = await (await fetch(`${SITE}/brand`)).json()
if (!brand.approved) {
  const r = await fetch(`${SITE}/brand/approve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fingerprint: brand.proposed.fingerprint, actor: 'owner' }) })
  assert.ok(r.ok, 'brand approval in disposable state')
}

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
const page = await context.newPage()
page.setDefaultTimeout(20000)
const errors = [], requests = [], blocked = [], passed = [], parity = []
page.on('pageerror', e => errors.push(e.message))
const proposalQueue = []
await page.route('**/api/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push([req.method(), path])
  if (path === '/api/website/studio/propose') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(proposalQueue.shift()) })
  if (path.startsWith('/api/website/studio/') || req.method() === 'GET') return route.continue()
  blocked.push([req.method(), path])
  return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'blocked by Stage 2.1 proof' }) })
})
const pause = (ms = 400) => page.waitForTimeout(ms)
const frame = () => page.frameLocator('.studio-editor iframe')
const inFrame = (fn, arg) => page.evaluate(([f, a]) => new Function('doc', 'arg', f)(document.querySelector('.studio-editor iframe').contentDocument, a), [fn, arg])
const attr = (selector, name) => inFrame("const e = doc.querySelector(arg[0]); return e ? e.getAttribute(arg[1]) : null", [selector, name])
const aside = () => page.locator('.studio-assistant')
const tab = name => aside().getByRole('navigation', { name: 'Studio panels' }).getByRole('button', { name, exact: true }).click()
const status = () => aside().locator('.studio-status').innerText()
const chip = () => aside().locator('.studio-contrast-chip')
const listSelect = async id => { await aside().locator('details.studio-sections').evaluate(d => { d.open = true }); await aside().locator(`.studio-sections li[data-section="${id}"] .studio-section-select`).click(); await pause() }
const control = (label, value) => page.evaluate(([l, v]) => {
  const e = [...document.querySelectorAll('.studio-assistant input,.studio-assistant select')].find(n => n.getAttribute('aria-label') === l)
  if (!e) throw Error(`control ${l}`)
  let p = e.parentElement; while (p) { if (p.tagName === 'DETAILS') p.open = true; p = p.parentElement }
  const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, String(v))
  e.dispatchEvent(new Event(e.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true }))
}, [label, value])
const localStyle = async (id, target, key, value) => {
  await listSelect(id); await tab('Selected styles')
  if (target !== 'section') {
    await control('Style target', target); await pause()
    // Regression (fixed in 2.1): the element list must belong to the section selected now, even when
    // Selected styles was already open for another section.
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('.studio-assistant select')].find(n => n.getAttribute('aria-label') === 'Style target').value), target)
    assert.equal(await page.evaluate(() => document.querySelector('.studio-sections li[aria-current=true]')?.dataset.section), id)
  }
  await control(`${key} mode`, 'local'); await pause(); await control(`Local ${key}`, value); await pause(600)
}
const proposeClicks = () => requests.filter(([, p]) => p === '/api/website/studio/propose').length
const sel = id => `[data-studio-section="${id}"]`

// The model's verdict for the editor's current page, compared with the colours the canvas actually
// renders (getComputedStyle on the element that holds each text, background found by walking up).
async function renderParity(name) {
  await page.locator('.studio-editor').getByRole('button', { name: 'Save draft' }).click()
  await aside().getByText(/Draft saved in this browser/).waitFor(); await pause(300)
  const result = await page.evaluate(async () => {
    const { contrastChecks, contrastModel, contrastRatio } = await import('/src/studio/contrast.js')
    const data = JSON.parse(localStorage.getItem('ttros-studio:global:default-site:home:v1')).data
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
      const style = getComputedStyle(holder)
      const fg = hex(style.color).hex
      let bg = null, gradient = null
      for (let e = holder; e && !bg; e = e.parentElement) {
        const s = getComputedStyle(e)
        if (s.backgroundImage && s.backgroundImage !== 'none') { gradient = s.backgroundImage; bg = 'gradient' }
        else { const b = hex(s.backgroundColor); if (b.alpha > 0) bg = b.hex }
      }
      const modelGradientStop = check.background.gradient ? `rgb(${[1, 3, 5].map(k => parseInt(check.background.hex.slice(k, k + 2), 16)).join(', ')})` : null
      const bgMatch = bg === 'gradient' ? !!modelGradientStop && gradient.includes(modelGradientStop) : bg === check.background.hex
      const renderedRatio = bg === 'gradient' ? contrastRatio(fg, check.background.hex) : contrastRatio(fg, bg)
      rows.push({ id: check.id, text: holder.textContent.trim().slice(0, 30), model: [check.foreground.hex, check.background.hex], rendered: [fg, bg], fgMatch: fg === check.foreground.hex, bgMatch, pass: check.pass, renderedPass: renderedRatio >= check.required })
    }
    return rows
  })
  const mismatches = result.filter(r => !r.fgMatch || !r.bgMatch || r.pass !== r.renderedPass)
  parity.push({ scenario: name, checks: result.length, failures: result.filter(r => !r.pass).map(r => r.id), mismatches })
  assert.equal(mismatches.length, 0, `${name}: model ≠ rendered canvas: ${JSON.stringify(mismatches.slice(0, 3))}`)
  assert.ok(result.length >= 80)
  return result
}

// 1. Open Studio fresh. Stage 3: the default page has no failures (the Hero accent line keeps the
// site colour #675638; Stage 2.1 reported it at #b89b63, 2.39:1).
await page.addInitScript(() => { try { if (!sessionStorage.getItem('stage21-init')) { localStorage.removeItem('ttros-studio:global:default-site:home:v1'); sessionStorage.setItem('stage21-init', '1') } } catch { /* sandboxed frames */ } })
await page.goto(`${base}/websites?tab=studio&site=default-site`)
await frame().locator('h1').first().waitFor()
await page.evaluate(() => document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click()); await pause(800)
assert.equal(await chip().innerText(), '✓ All text meets WCAG AA contrast')
assert.equal(await attr(`${sel('Section0')} h1`, 'data-studio-contrast'), null)
await tab('Accessibility')
assert.match(await aside().getByTestId('contrast-summary').innerText(), /82 text items checked · all pass AA/)
assert.equal(await aside().locator('.studio-contrast-item[data-check="Section0:text1"]').count(), 0)
await page.screenshot({ path: `${out}/default-hero-passes.png` })
const defaultRows = await renderParity('default page')
assert.deepEqual(defaultRows.filter(r => !r.pass).map(r => r.id), [])
assert.deepEqual(defaultRows.find(r => r.id === 'Section0:text1').rendered, ['#675638', '#f7f3ea'])
passed.push('Default page: 82 text items checked, all pass (Stage 3: the Hero accent line renders the site colour #675638 on #f7f3ea); no header, canvas or Accessibility warnings; model = rendered canvas for all 82')

// 2. The previously unreadable Contact case: a light local background on the dark contact band.
await localStyle('Section8', 'section', 'Background', '#f7f3ea')
assert.equal(await chip().innerText(), '⚠ 2 text contrast failures (WCAG AA) · Review')
const target = aside().getByTestId('target-contrast')
assert.match(await target.innerText(), /2 of 3 text items below WCAG AA · lowest 1\.00:1 \(needs 3:1\)/)
assert.match(await target.innerText(), /What’s holding your business back\?[\s\S]*#f7f3ea on #f7f3ea/)
assert.match(await target.innerText(), /1\.41:1[\s\S]*needs 4\.5:1[\s\S]*#d5cec1 on #f7f3ea[\s\S]*background is a local override on Whole section/)
for (const t of ['h2', 'p']) assert.equal(await attr(`${sel('Section8')} ${t}`, 'data-studio-contrast'), 'fail', t)
assert.equal(await attr(`${sel('Section8')} a`, 'data-studio-contrast'), null, 'the button keeps its own fill')
await frame().locator(`${sel('Section8')}`).scrollIntoViewIfNeeded()
await target.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/contact-unreadable-caught.png` })
const contactRows = await renderParity('contact light background')
assert.deepEqual(contactRows.filter(r => !r.pass).map(r => r.id), ['Section8:text0', 'Section8:text1'])
const savedVersion = Number((await status()).match(/server version v(\d+) \(manual, .*?\)\. 2 known contrast failure\(s\) recorded with this version\./)?.[1])
const record = (await (await fetch(`${SITE}/drafts`)).json()).drafts.at(-1)
assert.equal(record.version, savedVersion)
assert.deepEqual(record.contrast_failures.map(f => [f.id, f.ratio]), [['Section8:text0', 1], ['Section8:text1', 1.41]])
passed.push(`Contact band with a light local background is caught: heading 1.00:1 (needs 3:1), paragraph 1.41:1 (needs 4.5:1), both outlined on the canvas; editing and saving stay allowed and server v${savedVersion} records the 2 known failures`)

// 3. Export for the site adapter is blocked until the owner acknowledges the exact failures.
const exportButton = page.locator('.studio-editor').getByRole('button', { name: 'Export site draft' })
await exportButton.click(); await pause()
assert.match(await status(), /Export blocked: 2 known WCAG AA contrast failure/)
assert.equal(await aside().getByRole('button', { name: 'Accessibility', exact: true }).getAttribute('aria-pressed'), 'true')
await aside().locator('.studio-ack input').check()
const [download] = await Promise.all([page.waitForEvent('download'), exportButton.click()])
const exported = JSON.parse(await readFile(await download.path(), 'utf8'))
await writeFile(`${out}/exported-with-acknowledged-failures.json`, JSON.stringify(exported, null, 2) + '\n')
assert.equal(exported.version, 4)
assert.deepEqual(exported.accessibility.failures.map(f => f.id), ['Section8:text0', 'Section8:text1'])
assert.equal(exported.accessibility.acknowledged.by, 'owner')
assert.match(await status(), /exported with 2 acknowledged contrast failure/)
passed.push('Export blocked until the owner acknowledges the 2 known failures; the export then records them (ids, colours, ratios, requirement, acknowledgement)')

// 4. Accessible alternatives are applied as normal undoable edits; acknowledgement resets when failures change.
const paragraph = aside().locator('.studio-contrast-item[data-check="Section8:text1"]')
assert.match(await paragraph.locator('.studio-alternatives').innerText(), /Remove the background override on Whole section · 9\.56:1/)
await paragraph.locator('.studio-alternatives button').first().click(); await pause(600)
assert.match(await status(), /Contrast now 9\.56:1/)
assert.equal(await chip().innerText(), '✓ All text meets WCAG AA contrast')
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.equal(await chip().innerText(), '⚠ 2 text contrast failures (WCAG AA) · Review')
const heading = aside().locator('.studio-contrast-item[data-check="Section8:text0"]')
const brandText = heading.locator('.studio-alternatives button', { hasText: 'Text #7a746a on H2 1' })
await brandText.click(); await pause(600)
assert.equal(await chip().innerText(), '⚠ 1 text contrast failure (WCAG AA) · Review')
assert.equal(await attr(`${sel('Section8')} h2`, 'data-studio-contrast'), null)
assert.equal(await aside().locator('.studio-ack input').isChecked(), false, 'acknowledgement is tied to the exact failure set')
await exportButton.click(); await pause()
assert.match(await status(), /Export blocked: 1 known/)
passed.push('Alternatives are verified, one-click, undoable edits: removing the override (9.56:1) and undo; on-brand Slate Taupe heading text; acknowledgement resets when the failure set changes')

// 5. Inherited styles and global design, checked against the canvas.
await localStyle('Section1', 'section', 'Text colour', '#ece9e2')
const inheritedRows = await renderParity('section text override inherited')
assert.ok(['Section1:text0', 'Section1:text1', 'Section1:text2', 'Section1:text3'].every(id => inheritedRows.find(r => r.id === id && !r.pass)))
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
await localStyle('Section8', 'a-0', 'Button appearance', 'outline')
const outlineRows = await renderParity('contact button outline')
const outlined = outlineRows.find(r => r.id === 'Section8:text2')
assert.deepEqual([outlined.model, outlined.rendered, outlined.pass], [['#111315', '#f7f3ea'], ['#111315', '#f7f3ea'], true], 'an outline button shows the section background it sits on (here the light override)')
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
await tab('Global Design'); await aside().getByRole('button', { name: 'Forest' }).click(); await pause(800)
await renderParity('Forest palette')
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
passed.push('Model = rendered canvas for inherited section text (pale on ivory fails in 4 descendants), an outline button inheriting the section override background, and a global palette; element targets follow the selected section')

// 6. David: a proposal that introduces a failure is blocked; one that does not is accepted with known failures listed.
await listSelect('Section5'); await tab('David')
proposalQueue.push({ proposal: { summary: 'Dark proof band', edits: [], styles: [{ section: 'Section5', target: 'section', values: { background: '#111315' } }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Make the Our work band dark.')
await aside().getByRole('button', { name: 'Propose changes' }).click()
const blockedProposal = aside().getByTestId('proposal-contrast')
await blockedProposal.waitFor()
assert.match(await blockedProposal.innerText(), /proposal blocked\. It introduces \d+ WCAG AA contrast failures?: Our work/)
assert.equal(await aside().getByRole('button', { name: 'Accept changes' }).isDisabled(), true)
await blockedProposal.scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/david-proposal-contrast-blocked.png` })
await aside().getByRole('button', { name: 'Reject' }).click()
proposalQueue.push({ proposal: { summary: 'Sharper proof heading', edits: [{ section: 'Section5', field: 'text1', text: 'Results from recent client systems.' }] }, usage: { total_tokens: 0 }, context_sources: [], brand_status: 'approved' })
await aside().locator('#studio-request').fill('Sharpen the Our work heading.')
await aside().getByRole('button', { name: 'Propose changes' }).click()
await aside().getByTestId('proposal-known-contrast').waitFor()
assert.match(await aside().getByTestId('proposal-known-contrast').innerText(), /already has 1 known contrast failure this proposal does not introduce/)
assert.equal(await aside().getByRole('button', { name: 'Accept changes' }).isDisabled(), false)
await aside().getByRole('button', { name: 'Accept changes' }).click(); await pause(600)
assert.equal(await inFrame("return doc.querySelector('[data-studio-section=\"Section5\"] h2').innerText.trim()"), 'Results from recent client systems.')
passed.push('David: a proposal introducing contrast failures is blocked (Accept disabled); a proposal that adds none is accepted and lists the 1 known failure it did not introduce')

// 7. Puck's Blocks rail is gone; its rail entry opens the registered Sections palette, which still works.
const editor = page.locator('.studio-editor')
assert.equal(await editor.getByText('Blocks', { exact: true }).count(), 0)
const rail = editor.getByText('Sections', { exact: true }).first()
await rail.click(); await pause(600)
assert.equal(await aside().getByRole('button', { name: 'Sections', exact: true }).getAttribute('aria-pressed'), 'true')
assert.equal(await editor.getByRole('button', { name: 'Open Sections palette' }).isVisible(), false, 'the left sidebar closes again')
assert.equal(await editor.locator('[class*="Drawer-item"],[class*="DrawerItem"]').count(), 0, 'no Puck component drawer items exist')
await page.screenshot({ path: `${out}/sections-rail-opens-palette.png` })
const before = await inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].length")
await aside().getByRole('button', { name: 'Add Our work at end of page' }).click(); await pause(600)
assert.equal(await inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].length"), before + 1)
assert.match(await status(), /Added Our work \(our-work-[a-z0-9]{6}\)/)
await page.locator('.studio-editor').getByRole('button', { name: 'undo' }).click(); await pause(600)
assert.equal(await inFrame("return [...doc.querySelectorAll('main [data-studio-section]')].length"), before)
passed.push('Puck Blocks rail replaced: no Blocks entry or component drawer; the Sections rail entry opens the registered palette, which adds a section (undo restores)')

// 8. Mobile layout of the warnings.
await page.setViewportSize({ width: 390, height: 900 }); await pause(800)
await tab('Accessibility'); await pause()
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
await aside().scrollIntoViewIfNeeded()
await page.screenshot({ path: `${out}/accessibility-mobile.png` })
assert.ok(overflow <= 0, `390px overflow ${overflow}`)
passed.push('390px: Accessibility panel without horizontal overflow')

const result = { passed, parity, proposeRequests: proposeClicks(), explicitProposeClicks: 2, blockedNonStudioWrites: blocked, pageErrors: errors, modelCalls: 0, proposals: 'mocked (Playwright route)' }
await writeFile(`${out}/browser-acceptance.json`, JSON.stringify(result, null, 2) + '\n')
assert.equal(proposeClicks(), 2)
assert.deepEqual(blocked, [])
assert.deepEqual(errors, [])
console.log(JSON.stringify({ passed: passed.length, parity: parity.map(p => [p.scenario, p.checks, p.failures.length, p.mismatches.length]) }))
await browser.close()
