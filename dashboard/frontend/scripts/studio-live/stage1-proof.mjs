// Stage 1 proof of the professional live editor + Website Manager, driven through the real UI of an
// isolated Studio (studio_local + worktree Vite; never the production Dashboard), then the real
// Next.js build of the saved version, in Chromium at desktop, tablet and mobile.
// Usage: node stage1-proof.mjs <studio-origin> <evidence-dir> <playwright-module> [operator] [public-origin]
// With operator + public-origin it drives the PRODUCTION Dashboard the way its Tailscale/Access gate
// expects (API calls re-issued with the verified operator login and the public origin for mutations),
// and finishes by restoring the starting version as a new version (append-only) and closing previews.
// External network is blocked. No model calls are needed (simple requests take the deterministic
// route); nothing is published.
// Revisit: when the Studio UI, the manager panel or the code layer changes. Last touched: 2026-11-22.
import {openStudioTool} from './ui-controls.mjs'
import fs from 'node:fs'
import path from 'node:path'
const [origin, out, pwModule, operator, publicOrigin] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const steps = [], errors = []
const record = (step, ok, detail) => { steps.push({step, ok: !!ok, detail, at: new Date().toISOString()}); fs.writeFileSync(path.join(out, 'stage1-proof.json'), JSON.stringify({steps, errors}, null, 1)); console.log(`${ok ? 'PASS' : 'FAIL'} ${step} ${typeof detail === 'string' ? detail : JSON.stringify(detail).slice(0, 500)}`) }
let n = 0
const shot = async (page, name) => { const file = `${String(++n).padStart(2, '0')}-${name}.png`; await page.screenshot({path: path.join(out, file)}); return file }
const canon = v => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().filter(key => x[key] !== undefined).map(key => [key, x[key]])) : x))
const walk = function* (list) { for (const item of list || []) { yield item; for (const s of ['kids', 'alt']) yield* walk(item.props[s]) } }
const find = (list, id) => { for (const i of walk(list)) if (i.props.id === id) return i; return null }

const browser = await chromium.launch()
const context = await browser.newContext({viewport: {width: 1920, height: 1080}})
await context.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
if (operator) await context.route('**/api/**', async route => {
 const h = {...route.request().headers(), 'tailscale-user-login': operator}
 if (route.request().method() !== 'GET') h.origin = publicOrigin
 try { await route.fulfill({response: await route.fetch({headers: h, timeout: 900000})}) } catch (e) { errors.push('api: ' + e.message.slice(0, 200)); await route.abort() }
})
const page = await context.newPage()
page.on('dialog', d => d.accept())
page.on('pageerror', e => errors.push('pageerror: ' + e.message.slice(0, 300)))
// validateDOMNesting: pre-existing React dev warning from the canvas rendering captured site HTML (canvas.jsx HtmlRender), not a page error.
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::|rsms\.me|validateDOMNesting/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
const frame = async () => (await page.$('.live-canvas iframe')).contentFrame()
const docs = () => page.evaluate(() => window.__ttrosLive.docs())
const code = () => page.evaluate(() => window.__ttrosLive.code())
const status = () => page.locator('[data-testid=live-status]').textContent()
const selected = () => page.locator('[data-testid=live-inspector]').getAttribute('data-selected').catch(() => null)
const tab = id => openStudioTool(page, id)
async function openStudio() {
 await page.goto(`${origin}/websites?tab=studio&site=default-site`)
 await page.waitForFunction(() => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelectorAll('[data-sid]').length > 50, null, {timeout: 120000})
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
}
async function select(id) {
 await tab('inspect')
 const f = await frame()
 const el = f.locator(`[data-sid="${id}"]`).first()
 await el.scrollIntoViewIfNeeded(); await el.click({force: true}); await page.waitForTimeout(300)
 for (let i = 0; i < 10 && (await selected()) !== id; i++) { await page.getByRole('button', {name: 'Select parent'}).click(); await page.waitForTimeout(200) }
 return (await selected()) === id
}
const undo = async () => { await page.getByRole('button', {name: 'Undo', exact: true}).click(); await page.waitForTimeout(500) }
const redo = async () => { await page.getByRole('button', {name: 'Redo', exact: true}).click(); await page.waitForTimeout(500) }
const viewport = async label => { await page.getByRole('button', {name: `${label} view`}).click(); await page.waitForTimeout(500) }
async function save(note) {
 await page.getByRole('button', {name: /^Save version/}).click()
 await page.waitForFunction(() => /Saved version v\d+/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000})
 return Number((await status()).match(/Saved version v(\d+)/)[1])
}
async function restore(v) {
 await tab('versions')
 await page.click(`li[data-version="${v}"] >> role=button[name="Restore"]`)
 await page.waitForFunction(() => /Restored .* as new version v\d+/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000})
 return Number((await status()).match(/new version v(\d+)/)[1])
}
async function ask(text) {
 await tab('manager')
 const before = await page.locator('.live-thread > li.agent').count()
 await page.fill('[data-testid=live-manager-input]', text)
 await page.click('[data-testid=live-manager-send]')
 await page.waitForFunction(b => document.querySelectorAll('.live-thread > li.agent:not(.pending)').length > b, before, {timeout: 120000})
 const row = page.locator('.live-thread > li.agent:not(.pending)').last()
 return {row, outcome: await row.getAttribute('data-outcome'), job: await row.getAttribute('data-job'), text: (await row.textContent()).slice(0, 400)}
}
async function accept(row) {
 const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal')
 await row.locator('[data-testid=live-proposal-accept]').click()
 await page.waitForFunction(id => document.querySelector(`[data-proposal="${id}"]`)?.dataset.status === 'accepted' || document.querySelector(`[data-proposal="${id}"] .live-warn`), id, {timeout: 30000})
 return (await status())
}

const CTA = 'home_h', SITE_API = `${origin}/api/website/studio/sites/default-site/live`
let latest0 = null, before = null
const api = (p, init) => page.evaluate(async ([u, init]) => { const r = await fetch(u, init); return {status: r.status, body: await r.json().catch(() => null)} }, [SITE_API + p, init || {}])
try {
 await openStudio()
 record(operator ? 'open live editor (production Dashboard, verified operator)' : 'open live editor (isolated copy of production Studio state)', true, await status())
 const original = await docs()
 latest0 = Number((await status()).match(/· v(\d+)/)?.[1] || 0)
 before = await api('/versions')
 fs.writeFileSync(path.join(out, 'versions-before.json'), JSON.stringify(before.body, null, 1))
 // The homepage proof-point grid: 2 columns on phones in the source (grid-cols-2 sm:grid-cols-3 md:grid-cols-5).
 const grid = find(original['app/page.tsx'], 'home_p') && /(^|\s)grid(\s|$)/.test(find(original['app/page.tsx'], 'home_p').props.cls || '') ? 'home_p' : null
 record('targets', !!grid, {cta: CTA, grid})

 // ---------------- A. manual edits (inspector) ----------------
 record('select hero CTA', await select(CTA), CTA)
 await page.fill('[data-testid=live-link-href]', 'htps//broken'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 record('manual invalid link is refused', /Not applied: Use a full https/.test(await status()) && find((await docs())['app/page.tsx'], CTA).props.a.href === '/free-assessment', await status())
 await page.fill('[data-testid=live-link-href]', '/no-such-page'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 record('manual link to a missing page is refused', /no page or file/.test(await status()), await status())
 await page.fill('[data-testid=live-link-href]', 'https://cal.com/timetorevenue/fit-call'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 record('manual: button URL', find((await docs())['app/page.tsx'], CTA).props.a.href === 'https://cal.com/timetorevenue/fit-call', await status())
 await page.selectOption('[data-testid=live-font-family]', 'var(--font-geist), "Helvetica Neue", sans-serif'); await page.waitForTimeout(300)
 await page.click('[data-testid=live-style-bp-m]')
 await page.fill('[data-testid=live-font-size]', '20px'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 const ctaManual = find((await docs())['app/page.tsx'], CTA).props
 record('manual: font (desktop) + size (mobile)', ctaManual.st?.d?.fontFamily?.includes('--font-geist') && ctaManual.st?.m?.fontSize === '20px', ctaManual.st)
 record('select grid', await select(grid), grid)
 await page.click('[data-testid=live-style-bp-m]')
 await page.getByLabel('Grid template columns').fill('1fr'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 record('manual: responsive layout (1 column on mobile)', find((await docs())['app/page.tsx'], grid).props.st?.m?.gridTemplateColumns === '1fr', find((await docs())['app/page.tsx'], grid).props.st)
 await page.keyboard.press('Control+z'); await page.waitForTimeout(400)
 const undone = find((await docs())['app/page.tsx'], grid).props.st?.m?.gridTemplateColumns
 await page.keyboard.press('Control+Shift+z'); await page.waitForTimeout(400)
 record('undo / redo of a manual edit', undone === undefined && find((await docs())['app/page.tsx'], grid).props.st?.m?.gridTemplateColumns === '1fr', {undone})
 await viewport('Mobile'); await shot(page, 'manual-edits-mobile'); await viewport('Desktop')
 const vManual = await save()
 const manualDoc = (await docs())['app/page.tsx']
 record('manual edits saved', vManual === latest0 + 1, {version: vManual})

 // ---------------- B. the same through the Website Manager ----------------
 // Back to the starting point (the latest production version), as a new version.
 const vReset = await restore(latest0)
 await openStudio()
 record(`restored v${latest0} (the starting point) as a new version`, canon((await docs())['app/page.tsx']) === canon(original['app/page.tsx']), {version: vReset})
 await select(CTA)
 let r = await ask('point this button to htps//broken')
 record('manager: invalid link rejected by validation', r.outcome === 'rejected', r)
 r = await ask('use https://cal.com/timetorevenue/fit-call on this button')
 record('manager: button URL proposal (deterministic job)', r.outcome === 'proposal', r)
 await r.row.locator('details').first().click(); await shot(page, 'manager-link-proposal')
 await r.row.locator('[data-testid=live-proposal-preview]').click(); await page.waitForTimeout(800); await shot(page, 'manager-proposal-preview')
 await page.locator('[data-testid=live-proposal-preview-frame]').getByRole('button', {name: 'Close'}).click()
 record('manager: accept applies the proposal', /Manager change applied/.test(await accept(r.row)) && find((await docs())['app/page.tsx'], CTA).props.a.href === 'https://cal.com/timetorevenue/fit-call', await status())
 r = await ask('use the heading font'); await accept(r.row)
 r = await ask('make the font size 20px on mobile'); await accept(r.row)
 record('manager: font + mobile size', canon(find((await docs())['app/page.tsx'], CTA).props) === canon(ctaManual), find((await docs())['app/page.tsx'], CTA).props.st)
 await select(grid)
 r = await ask('on mobile stack the columns')
 record('manager: responsive layout proposal', r.outcome === 'proposal', r)
 // Stale: change the page after the proposal, then accept.
 await undo() // undo the size edit made through the manager: the page no longer matches the proposal's base
 await r.row.locator('[data-testid=live-proposal-accept]').click(); await page.waitForTimeout(800)
 const staleText = await r.row.textContent()
 record('manager: stale proposal refused after the page changed', /changed since this proposal/.test(staleText), staleText.slice(-200))
 await redo()
 record('redo restores the manager size edit', find((await docs())['app/page.tsx'], CTA).props.st?.m?.fontSize === '20px', find((await docs())['app/page.tsx'], CTA).props.st)
 // Undo/redo restore Puck's selection with the step; select the grid again before asking.
 await select(grid)
 r = await ask('on mobile stack the columns'); await accept(r.row)
 await undo()
 const undoneAgent = find((await docs())['app/page.tsx'], grid).props.st?.m
 await redo()
 record('manager edit is undoable like a manual edit', undoneAgent === undefined && find((await docs())['app/page.tsx'], grid).props.st?.m?.gridTemplateColumns === '1fr', {undoneAgent})
 const vAgent = await save()
 const agentDoc = (await docs())['app/page.tsx']
 record('PARITY: manual and manager produce identical documents', canon(agentDoc) === canon(manualDoc), {manual: vManual, agent: vAgent})
 const diffOf = async v => page.evaluate(async ([api, v]) => { const d = await (await fetch(`${api}/versions/${v}`)).json(); const r = await fetch(`${api}/source/diff`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({docs: {'app/page.tsx': d.docs['app/page.tsx']}, code: d.code})}); return r.json() }, [SITE_API, v])
 const [dm, da] = [await diffOf(vManual), await diffOf(vAgent)]
 record('PARITY: identical generated Next.js source', JSON.stringify(dm.files) === JSON.stringify(da.files) && dm.changed.length > 0, {files: dm.changed})
 fs.writeFileSync(path.join(out, 'parity-source.diff'), dm.files.map(f => f.diff).join('\n'))
 // Cross-site mutation (direct API call from the page).
 const cross = await page.evaluate(async () => (await fetch('/api/website/studio/sites/other-site/live/manager/turns', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({text: 'use /pricing on this button', page: 'home', base_version: 0, docs: {}})})).status)
 const crossSave = await page.evaluate(async () => (await fetch('/api/website/studio/sites/other-site/live/versions', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({base_version: 0, docs: {}})})).status)
 record('cross-site mutations refused', cross === 404 && crossSave === 404, {manager: cross, save: crossSave})
 const staleSave = await page.evaluate(async api => (await fetch(`${api}/versions`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({base_version: 1, docs: {}})})).status, SITE_API)
 record('stale save refused', staleSave === 409, {status: staleSave})

 // ---------------- C. Code mode + header settings ----------------
 await tab('code')
 const css = '.btn-primary{letter-spacing:.04em}\n@media (max-width:767.98px){#hero-heading{text-wrap:balance}}\n'
 await page.fill('[data-testid=live-css-editor]', '@import url(https://evil.example/x.css);')
 record('owner CSS with @import is refused before apply', await page.locator('.live-code-mode [role=alert]').first().isVisible(), await page.locator('.live-code-mode [role=alert]').first().textContent())
 await page.fill('[data-testid=live-css-editor]', css)
 await page.click('[data-testid=live-css-apply]'); await page.waitForTimeout(400)
 await page.click('button[data-file="app/layout.tsx"]'); await page.waitForSelector('[data-testid=live-file-editor][data-path="app/layout.tsx"]')
 const layout = await page.inputValue('[data-testid=live-file-source]')
 const edited = layout.replace('      <body>', '      <body data-studio-code-edit="layout">')
 record('layout source loaded in the code editor', edited !== layout, {bytes: layout.length})
 await page.fill('[data-testid=live-file-source]', edited)
 await page.click('[data-testid=live-file-apply]'); await page.waitForTimeout(400)
 await shot(page, 'code-mode')
 await tab('site')
 await page.click('[data-testid=live-header-enabled]'); await page.waitForTimeout(400)
 record('header settings on (desktop sticky-transparent, tablet+mobile auto-hide)', canon((await code()).header) === canon({desktop: 'sticky-transparent', tablet: 'auto-hide', mobile: 'auto-hide', threshold: 8}), (await code()).header)
 await viewport('Mobile'); await page.click('[data-testid=live-header-state-hidden]'); await page.waitForTimeout(500)
 const hiddenCanvas = await (await frame()).evaluate(() => { const h = document.querySelector('header'); return {hidden: h.dataset.studioHidden, transform: getComputedStyle(h).transform} })
 await shot(page, 'canvas-header-hidden-mobile')
 await page.click('[data-testid=live-header-state-scrolled]'); await page.waitForTimeout(300); await shot(page, 'canvas-header-scrolled-mobile')
 await page.click('[data-testid=live-header-state-none]'); await viewport('Desktop')
 record('canvas previews header states', hiddenCanvas.hidden === 'true' && hiddenCanvas.transform !== 'none', hiddenCanvas)
 const vCode = await save()
 await page.reload(); await openStudio()
 const reloaded = await code()
 record('code layer survives save + reload', reloaded.css === css && reloaded.files?.['app/layout.tsx'] === edited && reloaded.header?.mobile === 'auto-hide', {version: vCode, keys: Object.keys(reloaded)})
 await tab('code')
 record('Code mode lists the edited file after reload', (await page.locator('[data-testid=live-edited-files]').textContent()).includes('app/layout.tsx'), '')
 await page.click('[data-testid=live-source-diff]'); await page.waitForSelector('[data-testid=live-source-diff-result]', {timeout: 60000})
 const diffList = await page.locator('[data-testid=live-source-diff-result] p').first().textContent()
 record('generated source diff covers element, CSS, header and source edits', ['app/page.tsx', 'app/globals.css', 'app/layout.tsx', 'components/StudioSiteBehavior.tsx'].every(f => diffList.includes(f)), diffList)

 // ---------------- D. real Next.js build of the saved version ----------------
 await tab('build')
 await page.getByRole('button', {name: 'Build real Next.js preview'}).click()
 await page.waitForSelector('[data-testid=live-build-status][data-status=ready], [data-testid=live-build-status][data-status=failed]', {timeout: 900000})
 const buildStatus = await page.locator('[data-testid=live-build-status]').getAttribute('data-status')
 const url = buildStatus === 'ready' ? await page.locator('[data-testid=live-next-preview]').getAttribute('src') : null
 record('real next build of the saved version', buildStatus === 'ready', {url, text: await page.locator('[data-testid=live-build-status]').textContent()})
 if (url) {
  fs.writeFileSync(path.join(out, 'build-source.diff'), await page.locator('.live-build pre.live-diff').textContent())
  const base = url.replace(/\/$/, '')
  const html = await (await context.request.get(base + '/')).text()
  record('built HTML carries the source edit (app/layout.tsx <body> attribute)', html.includes('data-studio-code-edit="layout"'), html.match(/<body[^>]*>/)?.[0])
  record('built HTML carries the manager/manual link', html.includes('href="https://cal.com/timetorevenue/fit-call"'), '')
  const cssHrefs = [...html.matchAll(/href="([^"]+\.css)"/g)].map(m => m[1])
  let builtCss = ''
  for (const h of cssHrefs) builtCss += await (await context.request.get(new URL(h, base + '/').href)).text()
  record('built CSS carries owner CSS and header behaviour', /letter-spacing:\.04em/.test(builtCss.replace(/0\.04em/, '.04em')) && /data-studio-hidden=(")?true/.test(builtCss), {sheets: cssHrefs.length})
  // ---------------- E. header scrolling in the built site ----------------
  const results = {}
  for (const [name, vp] of [['desktop', {width: 1280, height: 800}], ['tablet', {width: 820, height: 1180}], ['mobile', {width: 390, height: 844}]]) {
   const ctx = await browser.newContext({viewport: vp, isMobile: name === 'mobile', hasTouch: name !== 'desktop'})
   await ctx.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, rr => rr.abort())
   const p = await ctx.newPage()
   await p.goto(base + '/', {waitUntil: 'networkidle'})
   const state = () => p.evaluate(() => { const h = document.querySelector('header'); const r = h.getBoundingClientRect(); const cs = getComputedStyle(h); return {mode: h.dataset.studioMode, hidden: h.dataset.studioHidden, solid: h.dataset.studioSolid, top: Math.round(r.top), bottom: Math.round(r.bottom), position: cs.position, bg: cs.backgroundColor, y: Math.round(scrollY)} })
   const scroll = async y => { await p.evaluate(y => window.scrollTo(0, y), y); await p.waitForTimeout(450) }
   await p.waitForFunction(() => document.querySelector('header')?.dataset.studioMode, null, {timeout: 15000})
   const top = await state(); await p.screenshot({path: path.join(out, `built-${name}-top.png`)})
   await scroll(300); await scroll(900); const down = await state(); await p.screenshot({path: path.join(out, `built-${name}-scrolled-down.png`)})
   await scroll(700); const up = await state(); await p.screenshot({path: path.join(out, `built-${name}-scrolled-up.png`)})
   const row = {top, down, up}
   if (name === 'desktop') {
    row.ok = top.mode === 'sticky-transparent' && top.solid === 'false' && /rgba\(0, 0, 0, 0\)|transparent/.test(top.bg) && down.hidden === 'false' && down.top === 0 && down.solid === 'true' && up.top === 0
   } else {
    // Menu: open it while scrolled, scroll further down: the header stays; a menu link navigates.
    await p.click('button[aria-controls="mobile-menu"]'); await p.waitForTimeout(300)
    await scroll(1300); const menu = await state(); await p.screenshot({path: path.join(out, `built-${name}-menu-open-scrolled.png`)})
    const link = p.locator('#mobile-menu a[href="/pricing"]')
    await link.click(); await p.waitForURL(/\/pricing\/?$/, {timeout: 15000}); await p.waitForTimeout(500)
    const after = await state()
    row.menu = menu; row.navigated = {url: p.url(), header: after}
    row.ok = top.mode === 'auto-hide' && top.hidden === 'false' && down.hidden === 'true' && down.bottom <= 1 && up.hidden === 'false' && up.top === 0 && up.solid === 'true' &&
     menu.hidden === 'false' && menu.top === 0 && /\/pricing\/?$/.test(p.url()) && after.mode === 'auto-hide'
   }
   results[name] = row
   record(`built site header scrolling · ${name}`, row.ok, row)
   await ctx.close()
  }
  fs.writeFileSync(path.join(out, 'header-runtime.json'), JSON.stringify(results, null, 1))
 }
 await tab('coverage')
 await page.waitForSelector('[data-testid=live-coverage]')
 record('coverage/parity panel unchanged', true, await page.locator('[data-testid=live-coverage] p').nth(1).textContent())
} catch (e) {
 record('driver error', false, String(e.stack || e).slice(0, 800))
} finally {
 if (operator && latest0) {
  // Production: leave the Studio where it started. Restore appends a new version; nothing is deleted.
  try {
   await page.goto(`${origin}/websites?tab=studio&site=default-site`)
   await page.waitForFunction(() => window.__ttrosLive, null, {timeout: 120000})
   const now = (await api('/versions')).body
   const restored = now.latest !== latest0 ? await api(`/versions/${latest0}/restore`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({base_version: now.latest})}) : {status: 200, body: {version: now.latest}}
   const dropped = await api('/autosave', {method: 'DELETE', headers: {'X-TTROS-Connections': '1'}})
   const after = (await api('/versions')).body
   const same = (await api(`/versions/${restored.body.version}`)).body, start = (await api(`/versions/${latest0}`)).body
   const kept = before.body.versions.every(v => after.versions.some(w => w.version === v.version && w.sha256 === v.sha256))
   fs.writeFileSync(path.join(out, 'versions-after.json'), JSON.stringify(after, null, 1))
   record('cleanup: starting version restored as a new version; every earlier version kept unchanged; autosave cleared',
    restored.status === 200 && dropped.status === 200 && kept && after.intact && canon(same.docs) === canon(start.docs) && canon(same.code) === canon(start.code),
    {restored_as: restored.body.version, from: latest0, intact: after.intact, earlier_versions_unchanged: kept})
  } catch (e) { record('cleanup', false, String(e).slice(0, 300)) }
 }
 record('no page errors', errors.length === 0, errors.slice(0, 10))
 await browser.close()
 const failed = steps.filter(s => !s.ok)
 console.log(`${steps.length - failed.length}/${steps.length} steps passed`)
 process.exitCode = failed.length ? 1 : 0
}
