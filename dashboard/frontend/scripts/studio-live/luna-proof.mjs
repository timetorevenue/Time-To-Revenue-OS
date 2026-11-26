// Website Manager model route (website.manager on its pinned Luna-low model) proved through the real
// Studio UI: natural-language requests the deterministic rules cannot read become Jobs that call the
// model, return validated operations as proposals, and go through preview / accept / reject / undo;
// stale proposals, unsafe requests and other sites are refused.
// Usage: node luna-proof.mjs <studio-origin> <evidence-dir> <playwright-module> [operator] [public-origin]
// With operator + public-origin it drives the PRODUCTION Dashboard through its Tailscale/Access gate
// (same as stage1-proof.mjs). No version is saved; the autosave draft is cleared at the end and the
// starting version is restored (append-only) only if anything moved it. Nothing is published.
// Revisit: when the manager panel, the proposal flow or the package's model changes. Last touched: 2026-11-23.
import {openStudioTool} from './ui-controls.mjs'
import fs from 'node:fs'
import path from 'node:path'
const [origin, out, pwModule, operator, publicOrigin] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const steps = [], errors = [], turns = []
const record = (step, ok, detail) => { steps.push({step, ok: !!ok, detail, at: new Date().toISOString()}); fs.writeFileSync(path.join(out, 'luna-proof.json'), JSON.stringify({steps, turns, errors}, null, 1)); console.log(`${ok ? 'PASS' : 'FAIL'} ${step} ${typeof detail === 'string' ? detail : String(JSON.stringify(detail ?? null)).slice(0, 500)}`) }
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
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::|rsms\.me|validateDOMNesting/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
const frame = async () => (await page.$('.live-canvas iframe')).contentFrame()
const docs = () => page.evaluate(() => window.__ttrosLive.docs())
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
const viewport = async label => { await page.getByRole('button', {name: `${label} view`}).click(); await page.waitForTimeout(500) }
const SITE_API = `${origin}/api/website/studio/sites/default-site/live`
const api = (p, init) => page.evaluate(async ([u, init]) => { const r = await fetch(u, init); return {status: r.status, body: await r.json().catch(() => null)} }, [SITE_API + p, init || {}])
async function ask(text) {
 await tab('manager')
 const before = await page.locator('.live-thread > li.agent').count()
 await page.fill('[data-testid=live-manager-input]', text)
 await page.click('[data-testid=live-manager-send]')
 await page.waitForFunction(b => document.querySelectorAll('.live-thread > li.agent:not(.pending)').length > b, before, {timeout: 180000})
 const row = page.locator('.live-thread > li.agent:not(.pending)').last()
 const job = await row.getAttribute('data-job')
 const turn = ((await api('/manager')).body?.turns || []).filter(t => t.job_id === job && t.request).pop() || {}
 const pid = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal').catch(() => null)
 const proposal = pid ? (await api(`/manager/proposals/${pid}`)).body : null
 const r = {request: text, job, outcome: await row.getAttribute('data-outcome'), route: turn.route, usage: turn.usage, failure_class: turn.failure_class,
  reply: turn.reply, proposal: pid, ops: proposal?.ops, page: turn.page, selected: turn.selected, bp: turn.bp, base_version: turn.base_version}
 turns.push(r)
 return Object.defineProperty({...r}, 'row', {value: row, enumerable: false})
}
const modelUsed = r => r.route === 'model' && (r.usage?.model_calls || 0) >= 1 && (r.usage?.total_tokens || 0) > 0
async function decide(row, verdict) {
 const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal')
 await row.locator(`[data-testid=live-proposal-${verdict}]`).click()
 await page.waitForFunction(([id, v]) => document.querySelector(`[data-proposal="${id}"]`)?.dataset.status === (v === 'accept' ? 'accepted' : 'rejected') || document.querySelector(`[data-proposal="${id}"] .live-warn`), [id, verdict], {timeout: 30000})
 return {status: await row.locator('[data-testid=live-proposal]').getAttribute('data-status'), warn: await row.locator('.live-warn').textContent().catch(() => '')}
}

const CTA = 'home_h', HEAD = 'home_c'
let latest0 = null, before = null
try {
 await openStudio()
 const mgr = (await api('/manager')).body
 record('manager status: model route available on the released package', mgr.model_route === 'available' && ['canary', 'active'].includes(mgr.state),
  {state: mgr.state, level: mgr.level, model: mgr.model, model_route: mgr.model_route, detail: mgr.model_route_detail})
 before = await api('/versions'); latest0 = before.body.latest
 fs.writeFileSync(path.join(out, 'versions-before.json'), JSON.stringify(before.body, null, 1))
 const original = await docs()
 const ctaStart = find(original['app/page.tsx'], CTA)?.props

 // 1. Natural language outside the rules, on the mobile view: model -> proposal -> preview -> accept -> undo.
 await viewport('Mobile')
 record('select the hero call-to-action', await select(CTA), CTA)
 let r = await ask('make this button stand out a bit more on phones: slightly bigger text and a little more padding')
 record('model route: interpretive request becomes a proposal from website.manager', r.outcome === 'proposal' && modelUsed(r), r)
 record('model ops are scoped to the selected element, current page and mobile breakpoint',
  (r.ops || []).length > 0 && r.ops.every(o => o.op === 'set_style' && o.id === CTA && o.bp === 'm') && r.page === 'home' && r.selected === CTA && r.bp === 'm' && r.base_version === latest0, r.ops)
 await r.row.locator('details').first().click(); await shot(page, 'model-proposal-diff')
 await r.row.locator('[data-testid=live-proposal-preview]').click(); await page.waitForTimeout(800); await shot(page, 'model-proposal-preview')
 await page.locator('[data-testid=live-proposal-preview-frame]').getByRole('button', {name: 'Close'}).click()
 const acc = await decide(r.row, 'accept')
 const ctaAfter = find((await docs())['app/page.tsx'], CTA)?.props
 const applied = (r.ops || []).every(o => Object.entries(o.values).every(([k, v]) => (ctaAfter?.st?.m || {})[k] === (v ?? undefined)))
 record('accept applies exactly the proposed operations', acc.status === 'accepted' && applied, {status: acc.status, mobile: ctaAfter?.st?.m})
 await shot(page, 'model-accepted')

 // 2. Stale: a proposal made on the edited page cannot be accepted after the page changes underneath it.
 const r2 = await ask('round the corners of this button a little more')
 record('second model proposal on the edited page', r2.outcome === 'proposal' && modelUsed(r2), r2)
 await undo()
 record('undo reverts the accepted model edit like any manual edit', canon(find((await docs())['app/page.tsx'], CTA)?.props) === canon(ctaStart), find((await docs())['app/page.tsx'], CTA)?.props?.st)
 const stale = await decide(r2.row, 'accept')
 record('stale model proposal refused after the page changed', stale.status !== 'accepted' && /changed since this proposal|moved on/.test(stale.warn || ''), stale)

 // 3. Different element, desktop: model proposal rejected leaves the page unchanged.
 await viewport('Desktop')
 record('select the hero headline', await select(HEAD), HEAD)
 const headStart = canon((await docs())['app/page.tsx'])
 const r3 = await ask('this headline feels cramped — give the lines a little more room and open up the letter spacing slightly')
 record('model route on another element at desktop', r3.outcome === 'proposal' && modelUsed(r3) && (r3.ops || []).every(o => o.id === HEAD && (o.bp === 'd' || o.op !== 'set_style')), r3)
 const rej = await decide(r3.row, 'reject')
 record('reject leaves the page unchanged', rej.status === 'rejected' && canon((await docs())['app/page.tsx']) === headStart, rej)

 // 4. Unsafe and cross-site requests never reach the model.
 await select(CTA)
 const r4 = await ask('set this link to javascript:alert(document.cookie)')
 // Refused by the package before its model call, or (when the deterministic rules read it as a link) rejected by
 // the Studio's link validation: either way no proposal, nothing applied and no model call.
 record('unsafe link refused or rejected before any model call', ['refused', 'rejected'].includes(r4.outcome) && !r4.proposal && (r4.usage?.model_calls || 0) === 0, r4)
 const cross = await page.evaluate(async () => (await fetch('/api/website/studio/sites/other-site/live/manager/turns', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({text: 'make this a little calmer', page: 'home', base_version: 0, docs: {}})})).status)
 record('cross-site manager turn refused', cross === 404, {status: cross})
 await tab('manager'); await shot(page, 'manager-thread')
 record('page is back at its starting content', canon((await docs())['app/page.tsx']) === canon(original['app/page.tsx']), '')
 const used = turns.filter(t => t.route === 'model').map(t => ({job: t.job, ...t.usage}))
 fs.writeFileSync(path.join(out, 'model-usage.json'), JSON.stringify(used, null, 1))
 record('actual model usage recorded per turn', used.length >= 3 && used.every(u => u.exact !== false && u.total_tokens > 0), used)
} catch (e) {
 record('driver error', false, String(e.stack || e).slice(0, 800))
} finally {
 if (latest0 !== null) {
  try {
   const now = (await api('/versions')).body
   const restored = now.latest !== latest0 ? await api(`/versions/${latest0}/restore`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify({base_version: now.latest})}) : {status: 200, body: {version: now.latest}}
   const dropped = await api('/autosave', {method: 'DELETE', headers: {'X-TTROS-Connections': '1'}})
   const after = (await api('/versions')).body
   const kept = before.body.versions.every(v => after.versions.some(w => w.version === v.version && w.sha256 === v.sha256))
   fs.writeFileSync(path.join(out, 'versions-after.json'), JSON.stringify(after, null, 1))
   record('cleanup: no version added, every version unchanged, autosave cleared', restored.status === 200 && dropped.status === 200 && kept && after.intact && after.latest === latest0,
    {latest: after.latest, from: latest0, intact: after.intact, earlier_versions_unchanged: kept})
  } catch (e) { record('cleanup', false, String(e).slice(0, 300)) }
 }
 record('no page errors', errors.length === 0, errors.slice(0, 10))
 await browser.close()
 const failed = steps.filter(s => !s.ok)
 console.log(`${steps.length - failed.length}/${steps.length} steps passed`)
 process.exitCode = failed.length ? 1 : 0
}
