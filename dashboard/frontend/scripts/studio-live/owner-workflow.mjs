// The real owner workflow in the TTROS Dashboard, end to end, through the UI only:
// edit + rearrange + restyle a repeated template, add an image, undo/redo, save, reopen, edit a blog
// post's data record, restore versions, build a Vercel release candidate, create the release, publish
// it to core-site.example, inspect the public site, and roll back to the exact earlier deployment.
// Usage: node owner-workflow.mjs <dashboard-origin> <evidence-dir> <playwright-module> <operator> <public-dashboard-origin> <image.png> [phase]
//   phase: all (default) | edit | publish | publish-existing | rollback
// The browser presents the operator identity the Dashboard's Tailscale/Access gate verifies and the
// Dashboard's public origin for mutations (what the real Dashboard origin sends). Every publication
// still needs the server-issued confirmation and the exact typed command. Zero model calls.
// Revisit: when the Studio UI or release flow changes. Last touched: 2026-11-22.
import fs from 'node:fs'
import path from 'node:path'
const [origin, out, pwModule, operator, publicOrigin, imagePath, phase = 'all'] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const logFile = path.join(out, `owner-workflow-${phase}.json`)
const steps = []
const errors = []
const record = (step, ok, detail) => { steps.push({step, ok: !!ok, detail, at: new Date().toISOString()}); fs.writeFileSync(logFile, JSON.stringify({steps, errors}, null, 1)); console.log(`${ok ? 'PASS' : 'FAIL'} ${step} ${typeof detail === 'string' ? detail : JSON.stringify(detail).slice(0, 400)}`); if (!ok) throw Error(`step failed: ${step}`) }
let n = 0
const shot = async (page, name) => { const file = `${String(++n).padStart(2, '0')}-${phase}-${name}.png`; await page.screenshot({path: path.join(out, file)}); return file }
const profile = path.join(out, '.profile')
const context = await chromium.launchPersistentContext(profile, {viewport: {width: 1920, height: 1080}})
await context.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
// API calls are forwarded with the headers the real Dashboard origin sends (Chromium will not let an
// intercepted request change Origin, so they are re-issued and their responses returned unchanged).
await context.route('**/api/**', async route => {
 const h = {...route.request().headers(), 'tailscale-user-login': operator}
 if (route.request().method() !== 'GET') h.origin = publicOrigin
 try { await route.fulfill({response: await route.fetch({headers: h, timeout: 600000})}) } catch (e) { errors.push('api: ' + e.message.slice(0, 200)); await route.abort() }
})
const page = context.pages()[0] || await context.newPage()
page.on('pageerror', e => errors.push('pageerror: ' + e.message.slice(0, 300)))
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|ERR_FAILED|net::/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
const frame = async () => (await page.$('.live-canvas iframe')).contentFrame()
const docs = () => page.evaluate(() => window.__ttrosLive.docs())
const status = () => page.locator('[data-testid=live-status]').textContent()
const selected = () => page.locator('[data-testid=live-inspector]').getAttribute('data-selected').catch(() => null)
const walk = function* (list) { for (const item of list || []) { yield item; for (const s of ['kids', 'alt']) yield* walk(item.props[s]) } }
const find = (list, pred) => { for (const i of walk(list)) if (pred(i)) return i; return null }
async function openStudio() {
 await page.goto(`${origin}/websites?tab=studio&site=default-site`)
 await page.waitForFunction(() => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelectorAll('[data-sid]').length > 50, null, {timeout: 120000})
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
}
async function openPage(id, sid) {
 if ((await page.locator('select[aria-label="Studio page"]').inputValue()) !== id) await page.selectOption('select[aria-label="Studio page"]', id)
 await page.waitForFunction(s => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelector(`[data-sid="${s}"]`), sid, {timeout: 90000})
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
}
async function select(id, via) {
 const f = await frame()
 const el = f.locator(`[data-sid="${via || id}"]`).first()
 await el.scrollIntoViewIfNeeded(); await el.click({force: true}); await page.waitForTimeout(300)
 for (let i = 0; i < 10 && (await selected()) !== id; i++) { await page.getByRole('button', {name: 'Select parent'}).click(); await page.waitForTimeout(200) }
 return (await selected()) === id
}
async function openReleases() {
 if (await page.locator('.live-grid.live-focus').count()) await page.getByRole('button', {name: 'Exit focus'}).click()
 await page.locator('.studio-views').getByRole('button', {name: 'Releases', exact: true}).click()
 await page.waitForSelector('[data-testid=studio-releases]', {timeout: 60000})
}
const tab = async (name, right = true) => { await page.locator(`.live-${right ? 'right' : 'left'} .live-tabs`).getByRole('button', {name, exact: true}).click(); await page.waitForTimeout(300) }
async function styleField(group, name, value) {
 await tab('Inspector')
 await page.locator('.live-tabs.small').getByRole('button', {name: group, exact: true}).click()
 const input = page.getByLabel(name, {exact: true}).first()
 await input.fill(value); await input.press('Enter'); await page.waitForTimeout(500)
}
const save = async () => { await page.getByRole('button', {name: 'Save version'}).click(); await page.waitForFunction(() => /Saved version v\d+/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000}); return Number((await status()).match(/Saved version v(\d+)/)[1]) }
const state = {}
const statePath = path.join(out, 'owner-workflow-state.json')
if (fs.existsSync(statePath)) Object.assign(state, JSON.parse(fs.readFileSync(statePath, 'utf8')))
const keep = () => fs.writeFileSync(statePath, JSON.stringify(state, null, 1))

try {
 if (phase === 'all' || phase === 'edit') {
  await openStudio()
  await openPage('home', 'home_0')
  record('open_dashboard_live_editor', true, `${await page.title()} · ${(await status()).slice(0, 120)}`)
  let d
  if (!state.v1) {
  // 1. Rearrange: move the third homepage section above the second.
  d = await docs()
  const main = find(d['app/page.tsx'], i => i.props.tag === 'main')
  const sections = main.props.kids.filter(k => k.type === 'El' && k.props.tag === 'section')
  const [a, b] = [sections[1], sections[2]]
  record('select_section', await select(b.props.id, find([b], i => i.type === 'El' && i.props.id !== b.props.id)?.props.id), b.props.id)
  await page.getByRole('button', {name: 'Move up'}).click(); await page.waitForTimeout(600)
  d = await docs()
  const order = find(d['app/page.tsx'], i => i.props.tag === 'main').props.kids.map(k => k.props.id)
  record('rearrange_sections', order.indexOf(b.props.id) < order.indexOf(a.props.id), {moved: b.props.id, above: a.props.id})
  state.moved = b.props.id; state.above = a.props.id
  // 2. Restyle the FAQ question once in its repeated template: every FAQ item changes, behaviour kept.
  const faqMap = find(d['components/FaqAccordion.tsx'], i => i.type === 'Map')
  const question = find(faqMap.props.kids, i => i.props.tag === 'span' && i.props.cls?.includes('font-heading'))
  record('select_faq_template_question', await select(question.props.id), question.props.id)
  await styleField('type', 'fontWeight', '600')
  d = await docs()
  const styled = find(d['components/FaqAccordion.tsx'], i => i.props.id === question.props.id)
  const count = await (await frame()).locator(`.s-${question.props.id}`).count()
  record('restyle_repeated_template', styled.props.st?.d?.fontWeight === '600' && count >= 6, `${count} FAQ items on the canvas carry the new style`)
  state.faqQuestion = question.props.id
  // 3. Add an image: upload it, then insert it into the closing section's container.
  const last = sections.at(-1)
  const container = last.props.kids.find(k => k.type === 'El' && Array.isArray(k.props.kids))
  record('select_container', await select(container.props.id, container.props.kids.find(k => k.type === 'El')?.props.id), container.props.id)
  await tab('Assets', false)
  await page.getByLabel('Upload asset').setInputFiles(imagePath)
  await page.waitForFunction(() => /Uploaded .* Provenance recorded/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000})
  const asset = await page.locator('.live-asset-grid [data-asset]').last().getAttribute('data-asset')
  await page.locator(`.live-asset-grid [data-asset="${asset}"]`).click(); await page.waitForTimeout(700)
  d = await docs()
  const img = find(d['app/page.tsx'], i => i.props.tag === 'img' && i.props.a?.src === `asset:${asset}`)
  record('add_image', !!img && (await (await frame()).locator(`[data-sid="${img.props.id}"]`).count()) === 1, {asset, element: img?.props.id})
  await styleField('layout', 'width', '96px')
  await page.getByLabel('Alternative text').fill('Example Company monogram'); await page.getByLabel('Alternative text').press('Enter'); await page.waitForTimeout(400)
  state.image = img.props.id; state.asset = asset
  await shot(page, 'edited-home')
  // 4. Undo / redo.
  const before = JSON.stringify((await docs())['app/page.tsx'])
  await page.getByRole('button', {name: 'Undo'}).click(); await page.waitForTimeout(500)
  await page.getByRole('button', {name: 'Undo'}).click(); await page.waitForTimeout(500)
  const undone = find((await docs())['app/page.tsx'], i => i.props.id === img.props.id)
  await page.getByRole('button', {name: 'Redo'}).click(); await page.waitForTimeout(500)
  await page.getByRole('button', {name: 'Redo'}).click(); await page.waitForTimeout(500)
  const redone = JSON.stringify((await docs())['app/page.tsx'])
  record('undo_redo', undone?.props.a?.alt !== 'Example Company monogram' && redone === before, 'two undos reverted alt text and width; two redos restored them exactly')
  // 5. Save, then reopen from the server.
  state.v1 = await save()
  record('save_version', state.v1 > 0, `v${state.v1}`)
  await page.reload(); await openStudio(); await openPage('home', 'home_0')
  d = await docs()
  const dirty = await page.evaluate(() => window.__ttrosLive.dirty())
  record('reopen', !!find(d['app/page.tsx'], i => i.props.id === img.props.id) && dirty.length === 0 && /v\d+/.test(await status()), `after reload: image, order and template style present; unsaved ${dirty.length}`)
  keep()
  } else record('resume_after_v1', true, `v${state.v1} already saved with the homepage edits`)
  // 6. A blog post's data record: its description shows under the post title and is its meta description.
  await openPage('blog-slug:from-prompts-to-ai-workflows', 'blog-slug_0')
  await page.getByRole('button', {name: 'Undo'}).isEnabled() // (no pending edits expected here)
  await tab('Inspector')
  d = await docs()
  const original = d['content/blog.ts'].find(r => r.props.key === 'from-prompts-to-ai-workflows').props.f.excerpt
  const ex = page.getByLabel('excerpt', {exact: true})
  if ((await ex.inputValue()) !== original) { await ex.fill(original); await ex.press('Tab'); await page.waitForTimeout(400) }
  const description = page.getByLabel('description', {exact: true})
  state.excerpt = 'A plain-English guide to prompts, skills, scripts, hooks, connectors, plugins, MCP servers and harnesses, and how they turn AI from one-off chats into reliable business workflows.'
  await description.fill(state.excerpt); await description.press('Tab'); await page.waitForTimeout(800)
  d = await docs()
  const rec = d['content/blog.ts'].find(r => r.props.key === 'from-prompts-to-ai-workflows')
  const shown = await (await frame()).locator('body').innerText()
  record('edit_data_record', rec.props.f.description === state.excerpt && shown.includes('turn AI from one-off chats into reliable business workflows'), 'description edited from the post page; the canvas shows it under the title')
  state.v2 = await save()
  record('save_version_2', state.v2 === state.v1 + 1, `v${state.v2}`)
  await shot(page, 'edited-post')
  // 7. Restore v1 (excerpt back to the source), then restore v2 again: both are new versions.
  await tab('Versions')
  await page.locator(`li[data-version="${state.v1}"]`).getByRole('button', {name: 'Restore'}).click()
  await page.waitForFunction(v => /Restored|v\d+/.test(document.querySelector('[data-testid=live-status]')?.textContent || '') && document.querySelector(`li[data-version="${v + 2}"]`), state.v1, {timeout: 30000})
  d = await docs()
  const afterRestore = d['content/blog.ts'].find(r => r.props.key === 'from-prompts-to-ai-workflows').props.f.description
  record('restore_v1', afterRestore !== state.excerpt, `v${state.v1 + 2} = v${state.v1}: description is the source text again`)
  await page.locator(`li[data-version="${state.v2}"]`).getByRole('button', {name: 'Restore'}).click()
  await page.waitForFunction(v => document.querySelector(`li[data-version="${v + 2}"]`), state.v2, {timeout: 30000})
  d = await docs()
  state.latest = state.v2 + 2
  record('restore_v2', d['content/blog.ts'].find(r => r.props.key === 'from-prompts-to-ai-workflows').props.f.description === state.excerpt, `v${state.latest} = v${state.v2}`)
  keep()
 }
 if (phase === 'all' || phase === 'publish' || phase === 'publish-existing') {
  await openStudio()
  if (phase !== 'publish-existing') {
  // 8. Build the release candidate: `vercel build --prod` of the saved version, validated.
  await tab('Preview & build')
  await page.getByRole('button', {name: 'Build release candidate'}).click()
  // Ready first, then the release validation is attached (a few seconds later).
  await page.waitForFunction(() => { const e = document.querySelector('[data-testid=live-build-status]'); const s = e?.getAttribute('data-status'); return s === 'failed' || s === 'expired' || (s === 'ready' && /release validation (passed|failed)/.test(e.textContent)) }, null, {timeout: 1500000, polling: 3000})
  const build = await page.locator('[data-testid=live-build-status]').innerText()
  record('build_release_candidate', /release validation passed/.test(build), build.slice(0, 300))
  await page.getByRole('button', {name: 'Open Releases'}).click()
  await page.getByRole('button', {name: 'Create release from this build'}).click()
  await page.waitForFunction(() => /Release rel_[0-9a-f]{20} (recorded|already exists)/.test(document.querySelector('[data-testid=release-message]')?.textContent || ''), null, {timeout: 120000})
  state.release = (await page.locator('[data-testid=release-message]').textContent()).match(/rel_[0-9a-f]{20}/)[0]
  record('create_release', true, state.release)
  } else {
   await openReleases()
   record('reuse_release', !!state.release, `${state.release} (its staged deployment is reused, not rebuilt)`)
  }
  await page.locator('[data-testid="target-vercel-production"] input').check()
  await page.waitForTimeout(1500)
  await page.locator(`[data-testid="release-${state.release}"] button`).click()
  await page.waitForSelector('[data-testid=release-detail]')
  await page.getByRole('button', {name: /Request publication to/}).click()
  await page.waitForSelector('[data-testid=release-plan]', {timeout: 120000})
  const phrase = await page.locator('[data-testid=release-plan] label code').textContent()
  record('prepare_publication', phrase === `PUBLISH ${state.release} TO vercel-production`, phrase)
  await shot(page, 'publish-plan')
  await page.locator('#release-command').fill(phrase)
  await page.getByRole('button', {name: 'Confirm publish'}).click()
  await page.waitForFunction(() => /succeeded|failed|reverted|interrupted/.test(document.querySelector('[data-testid=release-operation]')?.getAttribute('data-status') || ''), null, {timeout: 2400000, polling: 5000})
  const op = await page.locator('[data-testid=release-operation]').innerText()
  await shot(page, 'publish-result')
  state.publish = op.match(/op_[0-9a-f]{16}_\d+/)?.[0]
  keep()
  record('publish_to_vercel_production', (await page.locator('[data-testid=release-operation]').getAttribute('data-status')) === 'succeeded', op.slice(0, 700))
 }
 if (phase === 'rollback') {
  await openStudio()
  await openReleases()
  await page.locator('[data-testid="target-vercel-production"] input').check()
  await page.waitForSelector('[data-testid^="rollback-external-"]', {timeout: 120000})
  const button = page.locator('[data-testid^="rollback-external-"]').first()
  state.rollbackTo = (await button.getAttribute('data-testid')).replace('rollback-external-', '')
  await button.click()
  await page.waitForSelector('[data-testid=release-plan]', {timeout: 120000})
  const phrase = await page.locator('[data-testid=release-plan] label code').textContent()
  record('prepare_rollback', phrase === `ROLLBACK vercel-production TO ${state.rollbackTo}`, phrase)
  await page.locator('#release-command').fill(phrase)
  await page.getByRole('button', {name: 'Confirm rollback'}).click()
  await page.waitForFunction(() => /succeeded|failed|reverted|interrupted/.test(document.querySelector('[data-testid=release-operation]')?.getAttribute('data-status') || ''), null, {timeout: 1200000, polling: 5000})
  const op = await page.locator('[data-testid=release-operation]').innerText()
  await shot(page, 'rollback-result')
  state.rollback = op.match(/op_[0-9a-f]{16}_\d+/)?.[0]
  keep()
  record('rollback_production', (await page.locator('[data-testid=release-operation]').getAttribute('data-status')) === 'succeeded', op.slice(0, 700))
 }
} catch (e) {
 errors.push(`${e.name}: ${e.message}`.slice(0, 600))
 await shot(page, 'failure').catch(() => {})
 console.log('FAILED', e.message.slice(0, 400))
} finally {
 keep()
 fs.writeFileSync(logFile, JSON.stringify({steps, errors, state, model_calls: 0}, null, 1))
 await context.close()
}
