// Stage 2 PRODUCTION acceptance through the authenticated Dashboard (3010 → 8010): one DRAFT landing page
// and one DRAFT blog post, edited manually and through the Website Manager (typed + a real spoken
// instruction transcribed by the server's local model), media from the site's files and an embed, Code
// mode, metadata/noindex/sitemap checked in a real loopback `next build` preview, then the starting
// version restored as a new version (append-only) and the preview closed. Never publishes.
// Usage: node stage2-production.mjs <dashboard-origin> <evidence-dir> <playwright-module> <spoken.wav> <operator> <public-origin>
// API calls are re-issued with the verified operator login and the public origin (as the Tailscale /
// Access gate expects). External network is blocked. Deterministic Website Manager routes only (0 model calls).
// Revisit: when the Studio UI or the production gate changes. Last touched: 2026-11-23.
import {openStudioTool, openContentTool} from './ui-controls.mjs'
import fs from 'node:fs'
import path from 'node:path'
const [origin, out, pwModule, wav, operator, publicOrigin] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const steps = [], errors = []
const save_ = () => fs.writeFileSync(path.join(out, 'production-acceptance.json'), JSON.stringify({steps, errors}, null, 1))
const record = (step, ok, detail) => { steps.push({step, ok: !!ok, detail, at: new Date().toISOString()}); save_(); console.log(`${ok ? 'PASS' : 'FAIL'} ${step} ${typeof detail === 'string' ? detail.slice(0, 300) : JSON.stringify(detail ?? null).slice(0, 400)}`) }
let n = 0
const shot = async (pg, name) => { const file = `${String(++n).padStart(2, '0')}-${name}.png`; await pg.screenshot({path: path.join(out, file)}); return file }
const walk = function* (list) { for (const item of list || []) { yield item; for (const s of ['kids', 'alt']) yield* walk(item.props[s]) } }
const find = (list, id) => { for (const i of walk(list)) if (i.props.id === id) return i; return null }
const first = (list, pred) => { for (const i of walk(list)) if (pred(i)) return i; return null }
const textOf = item => [...walk([item])].filter(i => i.type === 'Txt').map(i => i.props.text).join(' ')
const canon = v => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().filter(key => x[key] !== undefined).map(key => [key, x[key]])) : x))

const browser = await chromium.launch({args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${wav}`]})
const context = await browser.newContext({viewport: {width: 1920, height: 1080}, permissions: ['microphone']})
await context.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
await context.route('**/api/**', async route => {
 const h = {...route.request().headers(), 'tailscale-user-login': operator}
 if (route.request().method() !== 'GET') h.origin = publicOrigin
 try { await route.fulfill({response: await route.fetch({headers: h, timeout: 900000})}) } catch (e) { errors.push('api: ' + e.message.slice(0, 200)); await route.abort() }
})
const page = await context.newPage()
page.on('dialog', d => d.accept())
page.on('pageerror', e => errors.push('pageerror: ' + e.message.slice(0, 300)))
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::|validateDOMNesting|frame-src 'none'/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
const frame = async () => (await page.$('.live-canvas iframe')).contentFrame()
const docs = () => page.evaluate(() => window.__ttrosLive.docs())
const code = () => page.evaluate(() => window.__ttrosLive.code())
const status = () => page.locator('[data-testid=live-status]').textContent()
const current = () => page.locator('.live-studio').getAttribute('data-page')
const selected = () => page.locator('[data-testid=live-inspector]').getAttribute('data-selected').catch(() => null)
const tab = id => openStudioTool(page, id)
const left = label => openContentTool(page, label)
const waitCanvas = (min = 20) => page.waitForFunction(m => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelectorAll('[data-sid]').length > m, min, {timeout: 120000})
async function openStudio() {
 await page.goto(`${origin}/websites?tab=studio&site=default-site`)
 await waitCanvas(50)
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
}
async function openPage(id) {
 if ((await current()) === id) return
 await page.selectOption('select[aria-label="Studio page"]', id)
 await page.waitForFunction(id => document.querySelector('.live-studio')?.dataset.page === id, id, {timeout: 30000})
 await waitCanvas(10); await page.waitForTimeout(800)
}
async function select(id) {
 await tab('inspect')
 const f = await frame()
 const el = f.locator(`[data-sid="${id}"]`).first()
 await el.scrollIntoViewIfNeeded(); await el.click({force: true}); await page.waitForTimeout(300)
 for (let i = 0; i < 12 && (await selected()) !== id; i++) { const btn = page.getByRole('button', {name: 'Select parent'}); if (!(await btn.count())) break; await btn.click(); await page.waitForTimeout(200) }
 return (await selected()) === id
}
const viewport = async label => { await page.getByRole('button', {name: `${label} view`}).click(); await page.waitForTimeout(500) }
async function save() {
 await page.getByRole('button', {name: /^Save version/}).click()
 await page.waitForFunction(() => /Saved version v\d+|Not saved/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000})
 const s = await status()
 if (!/Saved version/.test(s)) throw Error(s)
 return Number(s.match(/Saved version v(\d+)/)[1])
}
const SITE_API = `${origin}/api/website/studio/sites/default-site/live`
const api = (p, init) => page.evaluate(async ([u, init]) => { const r = await fetch(u, init); return {status: r.status, body: await r.json().catch(() => null)} }, [p.startsWith('http') ? p : SITE_API + p, init || {}])
async function restore(v) {
 await tab('versions')
 const was = (await api('/versions')).body.latest
 await page.click(`li[data-version="${v}"] >> role=button[name="Restore"]`)
 await page.waitForFunction(w => new RegExp(`Restored .* as new version v${w + 1}\\b|Restore refused`).test(document.querySelector('[data-testid=live-status]')?.textContent || ''), was, {timeout: 60000})
 return Number(((await status()).match(/new version v(\d+)/) || [])[1])
}
async function send(text) {
 await tab('manager')
 const before = await page.locator('.live-thread > li.agent').count()
 if (text !== null) await page.fill('[data-testid=live-manager-input]', text)
 await page.click('[data-testid=live-manager-send]')
 await page.waitForFunction(b => document.querySelectorAll('.live-thread > li.agent:not(.pending)').length > b, before, {timeout: 180000})
 const row = page.locator('.live-thread > li.agent:not(.pending)').last()
 return {row, outcome: await row.getAttribute('data-outcome'), job: await row.getAttribute('data-job'), text: (await row.textContent()).slice(0, 500)}
}
async function proposalOf(row) { const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal'); return (await api(`/manager/proposals/${id}`)).body }
async function accept(row) {
 const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal')
 await row.locator('[data-testid=live-proposal-accept]').click()
 await page.waitForFunction(id => document.querySelector(`[data-proposal="${id}"]`)?.dataset.status === 'accepted' || document.querySelector(`[data-proposal="${id}"] .live-warn`), id, {timeout: 30000})
 await page.waitForTimeout(600)
 return status()
}

const SLUG = 'ttros-acceptance-draft-20261123', LANDING = `app/${SLUG}/page.tsx`
const POST_SLUG = 'ttros-acceptance-draft-post-20261123', POST = `content/blog-posts/${POST_SLUG}.tsx`
const result = {jobs: []}
let v0 = null, build = null
try {
 await openStudio()
 const model = (await api('/model')).body
 v0 = (await api('/versions')).body
 const assets0 = (await api('/assets')).body
 result.start = {latest: v0.latest, pages: model.pages.length, intact: v0.intact, library: assets0.library.length, manager: (await api('/manager')).body.state}
 fs.writeFileSync(path.join(out, 'versions-before.json'), JSON.stringify(v0, null, 1))
 const startDocs = (await api(`/versions/${v0.latest}`)).body
 record('production Studio opened through the authenticated Dashboard', model.pages.length === 47 && v0.intact && !(await api('/autosave')).body.autosave, result.start)
 const voice = (await api('/manager/voice')).body, providers = (await api('/media/providers')).body
 record('production: voice transcription available (local model); image generation honestly unavailable', voice.server_transcription === 'available' && providers.image_generation.available === false, {voice: voice.engine, image: providers.image_generation.detail.slice(0, 80)})

 // ---------------- draft landing page: manual ----------------
 await page.click('[data-testid=live-new-page]')
 await page.fill('[data-testid=live-new-title]', 'TTROS acceptance draft (do not publish)')
 await page.fill('[data-testid=live-new-slug]', SLUG)
 await page.fill('[data-testid=live-new-description]', 'Internal Studio acceptance page. Draft only; never published.')
 await page.click('[data-testid=live-new-create]')
 await page.waitForFunction(id => document.querySelector('.live-studio')?.dataset.page === id, `created:${SLUG}`, {timeout: 30000})
 await waitCanvas(10); await page.waitForTimeout(800)
 let d = await docs(), c = await code()
 record('manual: new DRAFT landing page created', !!d[LANDING] && c.pages?.[LANDING]?.status === 'draft', c.pages?.[LANDING])
 await shot(page, 'draft-landing-created')
 const L = () => d[LANDING]
 const h1 = first(L(), i => i.props.tag === 'h1'), cta = first(L(), i => i.props.tag === 'a' && /btn-primary/.test(i.props.cls || ''))
 const img = first(L(), i => i.props.tag === 'img'), heroBox = first(L(), i => i.props.cls === 'max-w-3xl')
 const lede = first(L(), i => i.props.tag === 'p' && /mb-8 max-w-2xl/.test(i.props.cls || ''))
 const faqBox = first(L(), i => i.props.cls === 'max-w-3xl' && i !== heroBox)
 await select(h1.props.kids[0].props.id)
 await page.fill('[data-testid=live-text]', 'Acceptance draft headline'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(400)
 await select(cta.props.id)
 await page.fill('[data-testid=live-link-href]', '/no-such-page'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 const missing = await status()
 await page.fill('[data-testid=live-link-href]', '/pricing'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await select(img.props.id)
 await left('Assets'); await page.click('[data-site-file="/images/casey-profile.jpg"]'); await page.waitForTimeout(400)
 await select(faqBox.props.id)
 await left('Insert'); await page.click('[data-palette=video]'); await page.waitForTimeout(500)
 d = await docs()
 const frameEl = first(find(L(), faqBox.props.id).props.kids, i => i.props.tag === 'iframe')
 await select(frameEl.props.id)
 await page.getByLabel('Embed address (YouTube, Vimeo, Tally, Loom)').fill('https://www.youtube-nocookie.com/embed/aqz-KE-bpKQ'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await tab('page')
 await page.fill('[data-testid=live-page-seo-title]', 'TTROS Acceptance Draft'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 await tab('code')
 await page.locator('textarea[data-testid=live-css-editor]').fill(((await code()).css || '') + `\n#main-content .acceptance-proof { outline: 1px dashed #B89B63; }\n`)
 await page.click('[data-testid=live-css-apply]'); await page.waitForTimeout(300)
 await page.click('[data-testid=live-source-diff]')
 await page.waitForSelector('[data-testid=live-source-diff-result]', {timeout: 60000})
 const diffText = await page.locator('[data-testid=live-source-diff-result]').textContent()
 d = await docs(); c = await code()
 record('manual: headline, CTA link (missing page refused), site image, YouTube embed, SEO title, Code-mode CSS + source diff shows the new route file',
  textOf(find(L(), h1.props.id)) === 'Acceptance draft headline' && find(L(), cta.props.id).props.a.href === '/pricing' && /no page or file/.test(missing)
  && find(L(), img.props.id).props.a.src === '/images/casey-profile.jpg' && find(L(), frameEl.props.id).props.a.src.includes('aqz-KE-bpKQ') && c.pages[LANDING].seoTitle === 'TTROS Acceptance Draft'
  && c.pages[LANDING].status === 'draft' && /acceptance-proof/.test(c.css) && diffText.includes(LANDING), {diff: diffText.slice(0, 200)})
 result.vManual = await save()
 record('draft landing page saved', result.vManual === v0.latest + 1, {version: result.vManual})

 // ---------------- Website Manager: typed + spoken ----------------
 await select(h1.props.id)
 let r = await send('make the font size 56px on desktop')
 let pr = r.outcome === 'proposal' && await proposalOf(r.row)
 result.jobs.push(r.job)
 record('manager (typed): selected headline, desktop only', pr && pr.ops[0].id === h1.props.id && pr.ops[0].bp === 'd' && pr.ops[0].values.fontSize === '56px', pr && {job: pr.job_id, ops: pr.ops})
 await accept(r.row)
 await select(heroBox.props.id)
 r = await send('add a button "See pricing" linking to /pricing')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 result.jobs.push(r.job)
 record('manager: insert a button into the selected container', pr && pr.ops[0].op === 'insert' && pr.ops[0].parent === heroBox.props.id, pr && {job: pr.job_id, cap: pr.capability})
 await accept(r.row)
 await viewport('Mobile')
 await select(lede.props.id)
 await tab('manager')
 await page.selectOption('[data-testid=live-voice-mode]', 'server')
 await page.click('[data-testid=live-manager-mic]')
 await page.waitForFunction(() => document.querySelector('[data-testid=live-manager-mic]')?.dataset.voiceState === 'recording', null, {timeout: 10000})
 await page.waitForTimeout(6000)
 await page.click('[data-testid=live-manager-mic]')
 await page.waitForFunction(() => document.querySelector('[data-testid=live-manager-mic]')?.dataset.voiceState === 'idle', null, {timeout: 90000})
 const heard = await page.inputValue('[data-testid=live-manager-input]')
 record('production voice: microphone recording transcribed by the server-local model', /font size/i.test(heard) && /mobile/i.test(heard), {heard, note: await page.locator('[data-testid=live-voice-note]').textContent().catch(() => '')})
 await shot(page, 'voice-transcribed')
 r = await send(null)
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 result.jobs.push(r.job)
 record('production voice: spoken request → same Website Manager job (input voice) → mobile-only style on the selected paragraph',
  pr && pr.input === 'voice' && pr.ops[0].id === lede.props.id && pr.ops[0].bp === 'm' && pr.ops[0].values.fontSize === '20px', pr && {job: pr.job_id, input: pr.input, ops: pr.ops})
 await accept(r.row)
 await viewport('Desktop')

 // ---------------- draft blog post via the Website Manager ----------------
 await openPage('home')
 await tab('manager')
 r = await send(`write a blog post titled "TTROS acceptance draft post" with the slug ${POST_SLUG} in the Operations category tagged acceptance`)
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 result.jobs.push(r.job)
 record('manager (content/SEO): new DRAFT blog post proposal (record + body + registry)', pr && pr.ops[0].op === 'create_post' && pr.ops[0].settings.status === 'draft' && pr.changed_docs.includes(POST),
  pr && {job: pr.job_id, settings: pr.ops[0].settings, changed: pr.source_diff?.changed})
 await accept(r.row)
 await page.waitForFunction(id => document.querySelector('.live-studio')?.dataset.page === id, `created-post:${POST_SLUG}`, {timeout: 30000})
 await waitCanvas(10); await page.waitForTimeout(800)
 r = await send('set the excerpt to "Internal acceptance post; draft only."')
 result.jobs.push(r.job)
 if (r.outcome === 'proposal') await accept(r.row)
 d = await docs()
 const P = () => d[POST]
 const h2 = first(P(), i => i.props.tag === 'h2')
 await select(h2.props.kids[0].props.id)
 await page.fill('[data-testid=live-text]', 'Acceptance section'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(400)
 const para = first(P(), i => i.props.tag === 'p' && /Explain the problem/.test(textOf(i)))
 await select(para.props.id)
 await left('Insert'); await page.click('[data-palette=link]'); await page.waitForTimeout(400)
 d = await docs()
 const link = first(P(), i => i.props.tag === 'a' && i.props.a?.href === '/')
 await select(link.props.id)
 await page.fill('[data-testid=live-link-href]', '/blog/how-to-find-ai-fast-starts'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 d = await docs(); c = await code()
 record('draft post: manager excerpt + manual heading and internal link; status stays draft',
  c.pages[POST]?.excerpt === 'Internal acceptance post; draft only.' && textOf(find(P(), h2.props.id)) === 'Acceptance section' && first(P(), i => i.props.a?.href === '/blog/how-to-find-ai-fast-starts') && c.pages[POST].status === 'draft', {settings: c.pages[POST]})
 await shot(page, 'draft-post')
 result.vPost = await save()
 record('draft page + post saved', result.vPost === result.vManual + 1, {version: result.vPost})

 // ---------------- loopback preview build: draft metadata, noindex, sitemap ----------------
 await tab('build')
 await page.getByRole('button', {name: 'Build real Next.js preview'}).click()
 await page.waitForFunction(() => ['ready', 'failed'].includes(document.querySelector('[data-testid=live-build-status]')?.dataset.status), null, {timeout: 900000})
 const buildText = await page.locator('[data-testid=live-build-status] p').first().textContent()
 const href = await page.locator('[data-testid=live-build-status] a', {hasText: 'Open in new tab'}).getAttribute('href').catch(() => null)
 const base = href ? new URL(href).origin : null
 build = {text: buildText, base}
 record('real `next build` preview of the draft version (loopback only)', /ready/.test(buildText) && !!base, build)
 if (base) {
  const site = await browser.newContext({viewport: {width: 1280, height: 900}})
  const sp = await site.newPage()
  const meta = async route => { const res = await sp.goto(base + route, {waitUntil: 'networkidle'}); return {status: res.status(), ...(await sp.evaluate(() => ({title: document.title, robots: document.querySelector('meta[name=robots]')?.content,
   canonical: document.querySelector('link[rel=canonical]')?.href, h1: document.querySelector('h1')?.textContent, frames: [...document.querySelectorAll('main iframe')].map(f => f.getAttribute('src')),
   imgs: [...document.querySelectorAll('main img')].map(i => i.getAttribute('src')), links: [...document.querySelectorAll('main a')].map(a => a.getAttribute('href'))})))} }
  const lp = await meta(`/${SLUG}`); await sp.screenshot({path: path.join(out, 'built-draft-landing.png')})
  await sp.setViewportSize({width: 390, height: 844}); await sp.screenshot({path: path.join(out, 'built-draft-landing-mobile.png')}); await sp.setViewportSize({width: 1280, height: 900})
  const pp = await meta(`/blog/${POST_SLUG}`); await sp.screenshot({path: path.join(out, 'built-draft-post.png')})
  const sitemap = await (await sp.request.get(base + '/sitemap.xml')).text()
  const blogIndex = await (await sp.request.get(base + '/blog')).text()
  const home = (await sp.goto(base + '/', {waitUntil: 'domcontentloaded'})).status()
  const checks = {landing: lp, post: pp, sitemapHasLanding: sitemap.includes(SLUG), sitemapHasPost: sitemap.includes(POST_SLUG), blogIndexHasPost: blogIndex.includes('TTROS acceptance draft post'), home}
  fs.writeFileSync(path.join(out, 'built-draft-checks.json'), JSON.stringify(checks, null, 1))
  record('built draft landing: 200, SEO title, canonical, noindex, edited headline, site image, embed, /pricing link',
   lp.status === 200 && lp.title.startsWith('TTROS Acceptance Draft') && lp.canonical.endsWith(`/${SLUG}`) && /noindex/.test(lp.robots) && lp.h1 === 'Acceptance draft headline'
   && lp.imgs.includes('/images/casey-profile.jpg') && lp.frames.some(f => f.includes('aqz-KE-bpKQ')) && lp.links.includes('/pricing'), lp)
  record('built draft post: 200, noindex (site draft semantics), not in sitemap; landing not in sitemap; draft post not listed on /blog; homepage 200',
   pp.status === 200 && /noindex/.test(pp.robots) && !checks.sitemapHasLanding && !checks.sitemapHasPost && !checks.blogIndexHasPost && home === 200,
   {post: {status: pp.status, robots: pp.robots, title: pp.title}, sitemapHasLanding: checks.sitemapHasLanding, sitemapHasPost: checks.sitemapHasPost, blogIndexHasPost: checks.blogIndexHasPost, home})
  await site.close()
 }
 await tab('build')
 await page.getByRole('button', {name: 'Close preview'}).click().catch(() => {})
 await page.waitForTimeout(1000)
 fs.writeFileSync(path.join(out, 'manager-turns.json'), JSON.stringify((await api('/manager')).body.turns.slice(-12), null, 1))

 // ---------------- restore the starting content (append-only) ----------------
 await openPage('home')
 result.vRestore = await restore(v0.latest)
 await page.waitForTimeout(800)
 const after = (await api('/versions')).body
 const restored = (await api(`/versions/${result.vRestore}`)).body
 const opts = await page.locator('select[aria-label="Studio page"] option').allTextContents()
 record(`restored v${v0.latest} as v${result.vRestore}: content identical to the start, created page and post gone from the working site, history kept`,
  canon(restored.docs) === canon(startDocs.docs) && canon(restored.code) === canon(startDocs.code) && opts.length === 47 && after.intact
  && JSON.stringify(after.versions.slice(0, v0.latest).map(v => v.sha256)) === JSON.stringify(v0.versions.map(v => v.sha256)) && after.latest === v0.latest + 3,
  {latest: after.latest, intact: after.intact, options: opts.length})
 await page.waitForTimeout(3500)
 record('no server autosave left behind', !(await api('/autosave')).body.autosave, null)
 fs.writeFileSync(path.join(out, 'versions-after.json'), JSON.stringify(after, null, 1))
 const assets1 = (await api('/assets')).body
 record('asset library unchanged by the acceptance (site files and embeds only)', assets1.library.length === result.start.library, {before: result.start.library, after: assets1.library.length})
} catch (e) {
 record('driver error', false, e.stack?.slice(0, 1500))
 await shot(page, 'error').catch(() => {})
} finally {
 fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify({...result, build}, null, 1))
 save_()
 await browser.close()
 console.log(`${steps.filter(s => s.ok).length}/${steps.length} steps passed; ${errors.length} page errors`)
}
