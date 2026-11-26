// Stage 2 proof of the Website Studio: a genuinely new landing page and a new blog post, each edited
// manually and through the Website Manager (typed and spoken), media (images, edited image, video file,
// video embed, links), custom CSS + a custom React component, then a real `next build` of the saved
// version checked in Chromium at desktop/tablet/mobile (metadata, JSON-LD, sitemap, blog index,
// related posts, media, component behaviour), negative tests and version restore.
// Drives an ISOLATED Studio (studio_local + worktree Vite; never the production Dashboard).
// Usage: node stage2-proof.mjs <studio-origin> <evidence-dir> <playwright-module> <media-dir>
// The microphone is Chromium's fake capture device playing <media-dir>/spoken.wav (real audio, transcribed
// by the server's local model). External network is blocked; nothing is published. The isolated preview
// build's CSP blocks third-party frames by design, so YouTube frames show a CSP console notice there.
// Revisit: when the Studio UI, created pages or the manager panel change. Last touched: 2026-11-23.
import {openStudioTool, openContentTool} from './ui-controls.mjs'
import fs from 'node:fs'
import path from 'node:path'
const [origin, out, pwModule, media] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const steps = [], errors = []
const save_ = () => fs.writeFileSync(path.join(out, 'stage2-proof.json'), JSON.stringify({steps, errors}, null, 1))
const record = (step, ok, detail) => { steps.push({step, ok: !!ok, detail, at: new Date().toISOString()}); save_(); console.log(`${ok ? 'PASS' : 'FAIL'} ${step} ${typeof detail === 'string' ? detail.slice(0, 300) : JSON.stringify(detail ?? null).slice(0, 400)}`) }
let n = 0
const shot = async (pg, name) => { const file = `${String(++n).padStart(2, '0')}-${name}.png`; await pg.screenshot({path: path.join(out, file)}); return file }
const walk = function* (list) { for (const item of list || []) { yield item; for (const s of ['kids', 'alt']) yield* walk(item.props[s]) } }
const find = (list, id) => { for (const i of walk(list)) if (i.props.id === id) return i; return null }
const first = (list, pred) => { for (const i of walk(list)) if (pred(i)) return i; return null }
const textOf = item => [...walk([item])].filter(i => i.type === 'Txt').map(i => i.props.text).join(' ')

const browser = await chromium.launch({args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(media, 'spoken.wav')}`, '--autoplay-policy=no-user-gesture-required']})
const context = await browser.newContext({viewport: {width: 1920, height: 1080}, permissions: ['microphone']})
await context.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
// Browser speech path: headless Chromium has no speech service, so its recognizer is stubbed here to
// deliver a transcript the way the Web Speech API does. Spoken replies are recorded, not played.
await context.addInitScript(() => {
 class StubRecognition { constructor() { this.lang = 'en'; this.interimResults = true; this.continuous = false }
  start() { const text = window.__stubSpeech || ''; setTimeout(() => { this.onresult?.({results: [Object.assign([{transcript: text.slice(0, 12)}], {isFinal: false})]}) }, 150)
   setTimeout(() => { this.onresult?.({results: [Object.assign([{transcript: text}], {isFinal: true})]}); this.onend?.() }, 450) }
  stop() { this.onend?.() } }
 window.SpeechRecognition = StubRecognition
 window.webkitSpeechRecognition = StubRecognition
 window.__spoken = []
 if (window.speechSynthesis) { const real = window.speechSynthesis.speak.bind(window.speechSynthesis); window.speechSynthesis.speak = u => { window.__spoken.push(u.text); try { real(u) } catch { /* silent */ } } }
})
const page = await context.newPage()
page.on('dialog', d => d.accept())
page.on('pageerror', e => errors.push('pageerror: ' + e.message.slice(0, 300)))
page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::|validateDOMNesting|violates the following Content Security Policy directive: "frame-src 'none'"/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
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
const ask = text => send(text)
async function proposalOf(row) { const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal'); return (await api(`/manager/proposals/${id}`)).body }
async function accept(row) {
 const id = await row.locator('[data-testid=live-proposal]').getAttribute('data-proposal')
 await row.locator('[data-testid=live-proposal-accept]').click()
 await page.waitForFunction(id => document.querySelector(`[data-proposal="${id}"]`)?.dataset.status === 'accepted' || document.querySelector(`[data-proposal="${id}"] .live-warn`), id, {timeout: 30000})
 await page.waitForTimeout(600)
 return status()
}
const SITE_API = `${origin}/api/website/studio/sites/default-site/live`
const api = (p, init) => page.evaluate(async ([u, init]) => { const r = await fetch(u, init); return {status: r.status, body: await r.json().catch(() => null)} }, [p.startsWith('http') ? p : SITE_API + p, init || {}])
const post = (p, body, method = 'POST') => api(p, {method, headers: {'Content-Type': 'application/json', 'X-TTROS-Connections': '1'}, body: JSON.stringify(body)})
async function upload(file) {
 await left('Assets')
 const before = (await api('/assets')).body.library.length
 await page.setInputFiles('input[aria-label="Upload asset"]', file)
 await page.waitForFunction(b => document.querySelectorAll('.live-asset-grid [data-asset]').length > b, before, {timeout: 30000}).catch(() => {})
 const lib = (await api('/assets')).body.library
 return lib.find(a => a.name === path.basename(file))
}

const LANDING = 'app/ai-follow-up-sprint/page.tsx'
const POST_SLUG = 'why-slow-follow-up-quietly-costs-service-businesses', POST = `content/blog-posts/${POST_SLUG}.tsx`
const result = {}
try {
 await openStudio()
 await post('/autosave', {}, 'DELETE')
 const model = (await api('/model')).body
 const v0 = (await api('/versions')).body
 result.start = {latest: v0.latest, pages: model.pages.length, intact: v0.intact}
 fs.writeFileSync(path.join(out, 'versions-before.json'), JSON.stringify(v0, null, 1))
 record('open isolated Studio (copy of production state)', model.pages.length === 47 && v0.intact, result.start)

 // ---------------- media: upload image + video, edit image, provider status ----------------
 const photo = await upload(path.join(media, 'team-follow-up.jpg'))
 const video = await upload(path.join(media, 'demo-walkthrough.webm'))
 record('upload image and video to the Studio library', photo?.type === 'image/jpeg' && video?.type === 'video/webm', {photo: photo?.id, video: video?.id})
 const gen = page.locator('[data-testid=live-image-generation]')
 record('image generation shows its honest unavailable status (no provider route)', (await gen.getAttribute('data-available')) === 'false' && /No authorized image-generation route/.test(await gen.textContent()), (await gen.textContent()).slice(0, 200))
 await page.click(`[data-edit-asset="${photo.id}"]`)
 await page.waitForSelector('[data-testid=live-image-editor]')
 await page.selectOption('[data-testid=live-image-ratio]', '1')
 await page.fill('[data-testid=live-image-width]', '1200')
 await page.waitForTimeout(600); await shot(page, 'image-editor')
 await page.click('[data-testid=live-image-save]')
 await page.waitForFunction(() => /Saved edited image/.test(document.querySelector('[data-testid=live-status]')?.textContent || ''), null, {timeout: 30000})
 const edited = (await api('/assets')).body.library.find(a => a.provenance?.kind === 'studio_edit' && a.provenance.from === photo.id)
 record('image editor: crop 16:9 + resize saved as a new asset with provenance', !!edited && /1200x675/.test(edited.name), edited && {id: edited.id, name: edited.name, provenance: edited.provenance})

 // ---------------- A. new landing page, manual ----------------
 await page.click('[data-testid=live-new-page]')
 await page.fill('[data-testid=live-new-title]', 'AI Follow-Up Sprint')
 await page.fill('[data-testid=live-new-description]', 'A two-week sprint that automates lead follow-up for service businesses, so no inquiry waits overnight.')
 await shot(page, 'new-page-dialog')
 await page.click('[data-testid=live-new-create]')
 await page.waitForFunction(() => document.querySelector('.live-studio')?.dataset.page === 'created:ai-follow-up-sprint', null, {timeout: 30000})
 await waitCanvas(10); await page.waitForTimeout(800)
 let d = await docs(), c = await code()
 record('manual: new landing page created from the campaign template', !!d[LANDING] && c.pages?.[LANDING]?.slug === 'ai-follow-up-sprint' && (await status()).includes('/ai-follow-up-sprint'), {settings: c.pages?.[LANDING], nodes: [...walk(d[LANDING])].length})
 await shot(page, 'landing-created-desktop')
 const L = () => d[LANDING]
 const h1 = first(L(), i => i.props.tag === 'h1'), h1Text = h1.props.kids[0]
 const cta = first(L(), i => i.props.tag === 'a' && /btn-primary/.test(i.props.cls || ''))
 const img = first(L(), i => i.props.tag === 'img')
 const grid = first(L(), i => i.props.st?.d?.gridTemplateColumns?.startsWith('repeat(3'))
 const card = grid.props.kids[0]
 const heroBox = first(L(), i => i.props.cls === 'max-w-3xl')
 const faqBox = first(L(), i => i.props.cls === 'max-w-3xl' && i !== heroBox)
 const lede = first(L(), i => i.props.tag === 'p' && /mb-8 max-w-2xl/.test(i.props.cls || ''))
 result.landing = {h1: h1.props.id, cta: cta.props.id, img: img.props.id, grid: grid.props.id, card: card.props.id, heroBox: heroBox.props.id, faqBox: faqBox.props.id, lede: lede.props.id}
 await select(h1Text.props.id)
 await page.fill('[data-testid=live-text]', 'Stop losing leads to slow follow-up'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(400)
 await select(cta.props.id)
 await page.fill('[data-testid=live-link-href]', '/no-such-page'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 const missing = await status()
 await page.fill('[data-testid=live-link-href]', '/pricing'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await select(h1.props.id)
 await page.click('[data-testid=live-style-bp-m]')
 await page.fill('[data-testid=live-font-size]', '34px'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await select(img.props.id)
 await left('Assets'); await page.click(`[data-asset="${edited.id}"]`); await page.waitForTimeout(400)
 d = await docs()
 record('manual: headline text, CTA link (missing page refused), mobile size, image replaced', textOf(find(L(), h1.props.id)) === 'Stop losing leads to slow follow-up' && find(L(), cta.props.id).props.a.href === '/pricing'
  && /no page or file/.test(missing) && find(L(), h1.props.id).props.st?.m?.fontSize === '34px' && find(L(), img.props.id).props.a.src === edited.ref,
  {h1: find(L(), h1.props.id).props.st, cta: find(L(), cta.props.id).props.a, img: find(L(), img.props.id).props.a.src})
 // video file + video embed, inside the questions block
 await select(faqBox.props.id)
 await left('Insert'); await page.click('[data-palette=video-file]'); await page.waitForTimeout(500)
 d = await docs()
 const vid = first(find(L(), faqBox.props.id).props.kids, i => i.props.tag === 'video')
 await select(vid.props.id)
 await left('Assets'); await page.click(`[data-asset="${video.id}"]`); await page.waitForTimeout(400)
 await select(faqBox.props.id)
 await left('Insert'); await page.click('[data-palette=video]'); await page.waitForTimeout(500)
 d = await docs()
 const frameEl = first(find(L(), faqBox.props.id).props.kids, i => i.props.tag === 'iframe')
 await select(frameEl.props.id)
 await page.getByLabel('Embed address (YouTube, Vimeo, Tally, Loom)').fill('https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await page.getByLabel('Embed address (YouTube, Vimeo, Tally, Loom)').fill('https://evil.example.com/embed/x'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 const badFrame = await status()
 d = await docs()
 record('manual: video file (uploaded WebM) and YouTube embed inserted; unapproved embed host refused', find(L(), vid.props.id).props.a.src === video.ref && find(L(), frameEl.props.id).props.a.src.includes('youtube-nocookie.com/embed/dQw4w9WgXcQ') && /approved|Frames may only/.test(badFrame),
  {video: find(L(), vid.props.id).props.a, embed: find(L(), frameEl.props.id).props.a.src, refused: badFrame.slice(0, 120)})
 // Page & SEO settings
 await tab('page')
 await page.fill('[data-testid=live-page-seo-title]', 'AI Follow-Up Sprint for Service Businesses'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 await page.selectOption('[data-testid=live-page-og]', edited.ref); await page.waitForTimeout(300)
 await page.selectOption('[data-testid=live-page-status]', 'published'); await page.waitForTimeout(300)
 c = await code()
 record('manual: Page & SEO settings (SEO title, social image, published)', c.pages[LANDING].seoTitle === 'AI Follow-Up Sprint for Service Businesses' && c.pages[LANDING].ogImage === edited.ref && c.pages[LANDING].status === 'published', c.pages[LANDING])
 await shot(page, 'landing-page-settings')
 // Code mode: owner CSS + a new functional React component placed visually
 await tab('code')
 await page.locator('textarea[data-testid=live-css-editor]').fill('#main-content h1 { text-wrap: balance; }\n.studio-proof-mark { outline: 1px dashed #B89B63; }\n')
 await page.click('[data-testid=live-css-apply]'); await page.waitForTimeout(300)
 await page.fill('input[aria-label="New file path"]', 'components/SprintChecklist.tsx')
 await page.getByRole('button', {name: 'New file'}).click()
 const component = `"use client";
import { useState } from "react";

// Written in TTROS Website Studio Code mode: a functional component placed visually on a page.
export default function SprintChecklist() {
  const [done, setDone] = useState(0);
  return (
    <div className="rounded-xl border border-warm-stone/60 bg-white/60 p-6 my-6" data-studio-custom="sprint-checklist">
      <p className="font-heading text-lg text-exec-graphite">Follow-up checklist: {done}/3 steps done</p>
      <button type="button" className="btn-primary mt-3" onClick={() => setDone((d) => Math.min(3, d + 1))}>
        Mark the next step done
      </button>
    </div>
  );
}
`
 await page.locator('textarea[data-testid=live-file-source]').fill(component)
 await page.click('[data-testid=live-file-apply]'); await page.waitForTimeout(400)
 await select(faqBox.props.id)
 await left('Insert')
 await page.waitForSelector('[data-component="components/SprintChecklist.tsx"]', {timeout: 15000})
 await page.click('[data-component="components/SprintChecklist.tsx"]'); await page.waitForTimeout(500)
 d = await docs(); c = await code()
 const comp = first(L(), i => i.type === 'Comp')
 record('code: owner CSS + new React component file, placed visually as a component', /text-wrap: balance/.test(c.css || '') && c.files?.['components/SprintChecklist.tsx'] === component && comp?.props.file === 'components/SprintChecklist.tsx',
  {css: c.css, files: Object.keys(c.files || {}), comp: comp?.props})
 for (const bp of ['Desktop', 'Tablet', 'Mobile']) { await viewport(bp); await shot(page, `landing-manual-canvas-${bp.toLowerCase()}`) }
 await viewport('Desktop')
 result.vManual = await save()
 record('landing page saved as a version', result.vManual === v0.latest + 1, {version: result.vManual})

 // ---------------- B. the landing page through the Website Manager ----------------
 await select(h1.props.id)
 let r = await ask('make the font size 64px on desktop')
 let pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager (typed): selected headline, desktop size — targets the selection and breakpoint', pr && pr.ops.length === 1 && pr.ops[0].id === h1.props.id && pr.ops[0].bp === 'd' && pr.ops[0].values.fontSize === '64px' && pr.capability === 'design', pr && {job: pr.job_id, ops: pr.ops})
 await accept(r.row)
 await viewport('Tablet')
 await select(grid.props.id)
 r = await ask('stack the columns')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager (typed, tablet view): stack columns applies at the tablet breakpoint only', pr && pr.ops[0].id === grid.props.id && pr.ops[0].bp === 't' && pr.ops[0].values.gridTemplateColumns === '1fr', pr && pr.ops)
 await accept(r.row)
 await viewport('Desktop')
 await select(card.props.id)
 r = await ask('add a soft shadow and a hover lift')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager: effects (shadow + hover lift via owner CSS on the selected card)', pr && pr.ops.some(o => o.op === 'set_style' && o.values.boxShadow) && pr.ops.some(o => o.op === 'set_css' && o.css.includes(`.s-${card.props.id}:hover`)), pr && pr.ops)
 await accept(r.row)
 await select(heroBox.props.id)
 r = await ask('add a button "See pricing" linking to /pricing')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager: insert a button into the selected container', pr && pr.ops[0].op === 'insert' && pr.ops[0].parent === heroBox.props.id && pr.ops[0].node.props.a.href === '/pricing', pr && pr.ops)
 await accept(r.row)
 await select(faqBox.props.id)
 r = await ask('add a video https://www.youtube.com/watch?v=aqz-KE-bpKQ')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager (media): YouTube URL → privacy-enhanced embed inserted', pr && pr.capability === 'media' && JSON.stringify(pr.ops).includes('youtube-nocookie.com/embed/aqz-KE-bpKQ'), pr && {cap: pr.capability})
 await accept(r.row)
 // Model route (Luna low, the released website.manager package) for a request the rules cannot read.
 await select(lede.props.id)
 r = await ask('this intro paragraph feels a bit cramped, give it more breathing room')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 result.modelTurn = {outcome: r.outcome, job: r.job, route: pr?.route, usage: pr?.usage, ops: pr?.ops, text: r.text.slice(0, 300)}
 record('manager (model route, Luna low): vague design request → validated ops on the selected element', pr && pr.route === 'model' && pr.ops.every(o => !o.id || o.id === lede.props.id), result.modelTurn)
 if (pr) await accept(r.row)

 // ---------------- C. spoken instruction (real audio → local transcription → same send) ----------------
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
 const note = await page.locator('[data-testid=live-voice-note]').textContent().catch(() => '')
 record('voice (fallback path): microphone recording transcribed by the local model into the request box', /font size/i.test(heard) && /20/.test(heard) && /mobile/i.test(heard), {heard, note})
 await shot(page, 'voice-transcribed')
 r = await send(null)
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 const turnsV = (await api('/manager')).body.turns
 const voiceTurn = turnsV.filter(t => t.job_id === r.job).at(-1)
 record('voice: the spoken request reaches the same Website Manager job → mobile-only style on the selected paragraph', pr && pr.input === 'voice' && voiceTurn?.input === 'voice' && pr.ops[0].id === lede.props.id && pr.ops[0].bp === 'm' && pr.ops[0].values.fontSize === '20px', pr && {job: pr.job_id, input: pr.input, ops: pr.ops})
 await accept(r.row)
 await viewport('Desktop')
 result.vManager = await save()
 record('landing page manager edits saved', result.vManager === result.vManual + 1, {version: result.vManager})

 // ---------------- D. new blog post through the Website Manager ----------------
 await openPage('home')
 await page.keyboard.press('Escape')
 await tab('manager')
 r = await ask('write a blog post titled "Why slow follow-up quietly costs service businesses" about lead response time in the Operations category tagged speed to lead, follow-up')
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 record('manager (content/SEO): new blog post proposal with record + body', pr && pr.capability === 'content_seo' && pr.ops[0].op === 'create_post' && pr.changed_docs.includes(POST) && pr.source_diff.changed.includes('content/blog.ts') && pr.source_diff.changed.includes('app/blog/[slug]/page.tsx'),
  pr && {job: pr.job_id, settings: pr.ops[0].settings, changed: pr.source_diff.changed})
 await r.row.locator('[data-testid=live-proposal-preview]').click(); await page.waitForTimeout(800); await shot(page, 'post-proposal-preview')
 await page.locator('[data-testid=live-proposal-preview-frame]').getByRole('button', {name: 'Close'}).click()
 await accept(r.row)
 await page.waitForFunction(id => document.querySelector('.live-studio')?.dataset.page === id, `created-post:${POST_SLUG}`, {timeout: 30000})
 await waitCanvas(10); await page.waitForTimeout(800)
 d = await docs(); c = await code()
 record('accepted: the new post opens in the editor (blog layout, its own body document)', !!d[POST] && c.pages[POST]?.category === 'Operations', {settings: c.pages[POST]})
 await shot(page, 'post-created-desktop')
 r = await ask('set the excerpt to "Every hour an inquiry waits, the chance of winning it drops. Here is what slow follow-up really costs."')
 if (r.outcome === 'proposal') await accept(r.row)
 r = await ask('relate it to slow-lead-follow-up-costs-revenue, how-to-find-ai-fast-starts')
 if (r.outcome === 'proposal') await accept(r.row)
 r = await ask(`set the cover image to ${edited.name}`)
 if (r.outcome === 'proposal') await accept(r.row)
 // browser speech path (stubbed recognizer) + spoken replies
 await page.check('[data-testid=live-speak]')
 await page.selectOption('[data-testid=live-voice-mode]', 'browser')
 await page.evaluate(() => { window.__stubSpeech = 'mark this post as published' })
 await page.click('[data-testid=live-manager-mic]')
 await page.waitForFunction(() => /mark this post as published/.test(document.querySelector('[data-testid=live-manager-input]')?.value || ''), null, {timeout: 10000})
 r = await send(null)
 pr = r.outcome === 'proposal' && await proposalOf(r.row)
 if (pr) await accept(r.row)
 const spoken = await page.evaluate(() => window.__spoken)
 c = await code()
 record('manager on the post: excerpt, related posts, cover image, published (browser speech path) + reply spoken', c.pages[POST].excerpt?.startsWith('Every hour') && c.pages[POST].relatedPosts?.length === 2 && c.pages[POST].coverImage === edited.ref && c.pages[POST].status === 'published' && pr?.input === 'voice' && spoken.length > 0,
  {settings: c.pages[POST], spoken: spoken.slice(-1)})
 await page.uncheck('[data-testid=live-speak]')
 // manual edits on the post
 d = await docs()
 const P = () => d[POST]
 const h2 = first(P(), i => i.props.tag === 'h2')
 await select(h2.props.kids[0].props.id)
 await page.fill('[data-testid=live-text]', 'What a slow reply really costs'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(400)
 const para = first(P(), i => i.props.tag === 'p' && /Explain the problem/.test(textOf(i)))
 await select(para.props.kids[0].props.id)
 await page.fill('[data-testid=live-text]', 'A lead that waits overnight often books with whoever answered first. Speed is not a nice-to-have; it is the sale.'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(400)
 await select(para.props.id)
 await left('Insert'); await page.click('[data-palette=link]'); await page.waitForTimeout(400)
 d = await docs()
 const link = first(P(), i => i.props.tag === 'a' && i.props.a?.href === '/')
 await select(link.props.id)
 await page.fill('[data-testid=live-link-href]', '/blog/how-to-find-ai-fast-starts'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await select(link.props.kids[0].props.id)
 await page.fill('[data-testid=live-text]', ' Read: how to find AI quick wins'); await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(300)
 await select(para.props.id)
 await left('Assets'); await page.click(`[data-asset="${photo.id}"]`); await page.waitForTimeout(400)
 await tab('page')
 await page.fill('[data-testid=live-page-seo-title]', 'What Slow Follow-Up Costs Service Businesses'); await page.keyboard.press('Enter'); await page.waitForTimeout(300)
 d = await docs(); c = await code()
 record('manual on the post: heading, body text, internal link, inserted image, SEO title', textOf(find(P(), h2.props.id)) === 'What a slow reply really costs' && first(P(), i => i.props.a?.href === '/blog/how-to-find-ai-fast-starts') && first(P(), i => i.props.tag === 'img' && i.props.a?.src === photo.ref) && c.pages[POST].seoTitle === 'What Slow Follow-Up Costs Service Businesses', null)
 for (const bp of ['Desktop', 'Tablet', 'Mobile']) { await viewport(bp); await shot(page, `post-canvas-${bp.toLowerCase()}`) }
 await viewport('Desktop')
 result.vPost = await save()
 record('blog post saved as a version', result.vPost === result.vManager + 1, {version: result.vPost})

 // ---------------- E. unsafe, stale and cross-site edits ----------------
 const vlist = (await api('/versions')).body
 const full = (await api(`/versions/${result.vPost}`)).body
 const pageDocsHome = {'app/page.tsx': full.docs['app/page.tsx'], 'components/Header.tsx': full.docs['components/Header.tsx'], 'components/Footer.tsx': full.docs['components/Footer.tsx']}
 const turn = (text, extra = {}) => post('/manager/turns', {text, page: 'home', selected: null, bp: 'd', base_version: vlist.latest, docs: pageDocsHome, code: full.code, ...extra})
 const neg = {}
 neg.existingRoute = (await turn('create a landing page called "Pricing" at /pricing')).body
 neg.reserved = (await turn('create a landing page called "Studio" at /studio')).body
 neg.existingPost = (await turn('write a blog post titled "AI Quick Win Scan" with the slug ai-fast-start-scan')).body
 neg.unsafeLink = (await turn('add a button "Go" linking to javascript:alert(1)')).body
 neg.stale = (await turn('make the font size 20px', {base_version: vlist.latest - 1})).status
 neg.crossSite = (await post(`${origin}/api/website/studio/sites/other-site/live/manager/turns`, {text: 'x', page: 'home', bp: 'd', base_version: 0, docs: {}})).status
 neg.unknownDoc = (await post('/versions', {base_version: vlist.latest, docs: {'app/made-up/page.tsx': []}})).status
 neg.fileOverCreated = (await post('/versions', {base_version: vlist.latest, docs: {}, code: {...full.code, files: {...(full.code.files || {}), [LANDING]: 'export default function X() { return null }\n'}}})).status
 neg.scriptInPage = (await post('/versions', {base_version: vlist.latest, docs: {[LANDING]: [...full.docs[LANDING], {type: 'El', props: {id: 'ai-follow-up-sprint_zz1', tag: 'script', kids: []}}]}})).status
 neg.badOg = (await post('/versions', {base_version: vlist.latest, docs: {}, code: {...full.code, pages: {...full.code.pages, [LANDING]: {...full.code.pages[LANDING], ogImage: 'https://evil.example.com/x.png'}}}})).status
 neg.staleSave = (await post('/versions', {base_version: vlist.latest - 2, docs: {}})).status
 record('refused: existing route, reserved route, existing post slug, unsafe link, stale turn (409), cross-site (404), unknown document, source override of a created page, script element, remote social image, stale save (409)',
  /already exists/.test(neg.existingRoute.reason || neg.existingRoute.reply || '') && /reserved/.test(neg.reserved.reason || '') && /already exists/.test(neg.existingPost.reason || '') && ['rejected', 'refused'].includes(neg.unsafeLink.outcome)
  && neg.stale === 409 && neg.crossSite === 404 && neg.unknownDoc === 422 && neg.fileOverCreated === 422 && neg.scriptInPage === 422 && neg.badOg === 422 && neg.staleSave === 409,
  {existingRoute: neg.existingRoute.reason, reserved: neg.reserved.reason, existingPost: neg.existingPost.reason, unsafe: [neg.unsafeLink.outcome, neg.unsafeLink.reason || neg.unsafeLink.reply], stale: neg.stale, crossSite: neg.crossSite, unknownDoc: neg.unknownDoc, fileOverCreated: neg.fileOverCreated, script: neg.scriptInPage, badOg: neg.badOg, staleSave: neg.staleSave})
 // A proposal made before a manual edit no longer applies.
 await openPage('created:ai-follow-up-sprint')
 await select(card.props.id)
 r = await ask('make the font size 18px')
 await select(h1.props.id)
 await page.click('[data-testid=live-style-bp-d]').catch(() => {})
 await page.fill('[data-testid=live-font-size]', '60px'); await page.keyboard.press('Enter'); await page.waitForTimeout(400)
 await tab('manager')
 await r.row.locator('[data-testid=live-proposal-accept]').click(); await page.waitForTimeout(800)
 const staleMsg = await r.row.locator('.live-warn').textContent().catch(() => '')
 record('stale proposal refused after a manual edit', /changed since this proposal|moved on/.test(staleMsg), staleMsg)
 await page.getByRole('button', {name: 'Undo', exact: true}).click(); await page.waitForTimeout(400)

 // ---------------- F. real Next.js build of the saved version ----------------
 await tab('build')
 await page.getByRole('button', {name: 'Build real Next.js preview'}).click()
 await page.waitForFunction(() => ['ready', 'failed'].includes(document.querySelector('[data-testid=live-build-status]')?.dataset.status), null, {timeout: 900000})
 const buildState = await page.locator('[data-testid=live-build-status]').getAttribute('data-status')
 const buildText = await page.locator('[data-testid=live-build-status] p').first().textContent()
 const href = await page.locator('[data-testid=live-build-status] a', {hasText: 'Open in new tab'}).getAttribute('href').catch(() => null)
 const base = href ? new URL(href).origin : null
 record('real `next build` of the saved version', buildState === 'ready' && !!base, {buildText, base})
 const diffText = await page.locator('[data-testid=live-build-status] pre.live-diff').textContent().catch(() => '')
 fs.writeFileSync(path.join(out, 'build-source.diff'), diffText)
 if (base) {
  const site = await browser.newContext({viewport: {width: 1280, height: 900}})
  const sp = await site.newPage()
  const check = {}
  for (const [route, name] of [['/ai-follow-up-sprint', 'landing'], [`/blog/${POST_SLUG}`, 'post']]) {
   const res = await sp.goto(base + route, {waitUntil: 'networkidle'})
   // lazy images load as they scroll into view
   await sp.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 500) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)) } window.scrollTo(0, 0) })
   await sp.waitForLoadState('networkidle'); await sp.waitForTimeout(500)
   check[name] = await sp.evaluate(() => ({title: document.title, description: document.querySelector('meta[name=description]')?.content, canonical: document.querySelector('link[rel=canonical]')?.href,
    robots: document.querySelector('meta[name=robots]')?.content, og: document.querySelector('meta[property="og:image"]')?.content, ldjson: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => JSON.parse(s.textContent)['@type']),
    h1: document.querySelector('h1')?.textContent, imgs: [...document.querySelectorAll('main img')].map(i => ({src: i.getAttribute('src'), ok: i.complete && i.naturalWidth > 0})),
    videos: [...document.querySelectorAll('main video')].map(v => ({src: v.getAttribute('src'), controls: v.controls})), frames: [...document.querySelectorAll('main iframe')].map(f => f.getAttribute('src')),
    links: [...document.querySelectorAll('main a')].map(a => a.getAttribute('href')).filter(h => h && h.startsWith('/')).slice(0, 30), custom: document.querySelector('[data-studio-custom]')?.textContent || null}))
   check[name].status = res.status()
   for (const v of check[name].videos) v.http = (await sp.request.get(base + v.src)).status()
   for (const [w, label] of [[1280, 'desktop'], [820, 'tablet'], [390, 'mobile']]) { await sp.setViewportSize({width: w, height: 900}); await sp.waitForTimeout(400); await sp.screenshot({path: path.join(out, `built-${name}-${label}.png`), fullPage: false}) }
   await sp.setViewportSize({width: 1280, height: 900})
  }
  // the component's behaviour runs in the built site
  await sp.goto(base + '/ai-follow-up-sprint', {waitUntil: 'networkidle'})
  await sp.locator('[data-studio-custom] button').click(); await sp.waitForTimeout(300)
  check.componentAfterClick = await sp.locator('[data-studio-custom] p').textContent()
  const sitemap = await (await sp.request.get(base + '/sitemap.xml')).text()
  check.sitemap = {landing: sitemap.includes('/ai-follow-up-sprint<'), post: sitemap.includes(`/blog/${POST_SLUG}<`)}
  await sp.goto(base + '/blog', {waitUntil: 'networkidle'})
  check.blogIndex = (await sp.content()).includes('Why slow follow-up quietly costs service businesses')
  await sp.goto(base + `/blog/${POST_SLUG}`, {waitUntil: 'networkidle'})
  check.related = await sp.evaluate(() => [...document.querySelectorAll('a[href^="/blog/"]')].map(a => a.getAttribute('href')))
  check.home = (await sp.goto(base + '/', {waitUntil: 'networkidle'})).status()
  const css = await sp.evaluate(async () => { let all = ''; for (const l of document.querySelectorAll('link[rel=stylesheet]')) all += await (await fetch(l.href)).text(); return all })
  check.ownerCss = css.includes('text-wrap:balance') || css.includes('text-wrap: balance')
  check.hoverCss = css.includes(`.s-${card.props.id}:hover`)
  fs.writeFileSync(path.join(out, 'built-site-checks.json'), JSON.stringify(check, null, 1))
  const Lc = check.landing, Pc = check.post
  record('built landing page: 200, SEO title/description/canonical/robots index, social image, WebPage JSON-LD, edited image loads, video file 200, YouTube embeds, links',
   Lc.status === 200 && Lc.title.startsWith('AI Follow-Up Sprint for Service Businesses') && /two-week sprint/.test(Lc.description) && Lc.canonical.endsWith('/ai-follow-up-sprint') && /index/.test(Lc.robots) && !/noindex/.test(Lc.robots)
   && /\/studio-assets\//.test(Lc.og) && Lc.ldjson.includes('WebPage') && Lc.imgs.every(i => i.ok) && Lc.videos.length === 1 && Lc.videos[0].http === 200 && Lc.frames.filter(f => /youtube-nocookie\.com\/embed\//.test(f)).length === 2 && Lc.links.includes('/pricing'), Lc)
  record('built landing page: custom React component renders and works (state click), owner CSS and hover effect in the built CSS',
   /0\/3/.test(Lc.custom || '') && /1\/3/.test(check.componentAfterClick) && check.ownerCss && check.hoverCss, {custom: Lc.custom, after: check.componentAfterClick, ownerCss: check.ownerCss, hover: check.hoverCss})
  record('built blog post: 200, SEO title, BlogPosting JSON-LD (published), cover image as the social image (record coverImage), body image, internal link, related posts',
   Pc.status === 200 && Pc.title.startsWith('What Slow Follow-Up Costs Service Businesses') && Pc.ldjson.includes('BlogPosting') && /\/studio-assets\//.test(Pc.og) && Pc.imgs.length >= 1 && Pc.imgs.every(i => i.ok)
   && Pc.links.includes('/blog/how-to-find-ai-fast-starts') && check.related.includes('/blog/slow-lead-follow-up-costs-revenue'), Pc)
  record('built sitemap lists both new URLs; blog index lists the new post; homepage still 200', check.sitemap.landing && check.sitemap.post && check.blogIndex && check.home === 200, {sitemap: check.sitemap, blogIndex: check.blogIndex, home: check.home})
  await site.close()
 }
 await tab('build')
 await page.getByRole('button', {name: 'Close preview'}).click().catch(() => {})

 // ---------------- G. versions: restore the pre-Stage-2 version, then the Stage 2 version ----------------
 await openPage('home')
 const vOld = await restore(v0.latest)
 await page.waitForTimeout(800)
 c = await code()
 const optsOld = await page.locator('select[aria-label="Studio page"] option').allTextContents()
 record(`restore v${v0.latest} (append-only): created page and post leave the working site`, !c.pages && !optsOld.some(o => /AI Follow-Up Sprint/.test(o)) && optsOld.length === 47, {version: vOld, options: optsOld.length})
 const vBack = await restore(result.vPost)
 await page.waitForTimeout(800)
 c = await code()
 const optsBack = await page.locator('select[aria-label="Studio page"] option').allTextContents()
 const vEnd = (await api('/versions')).body
 record(`restore v${result.vPost}: both come back intact; ledger intact; v1–v${v0.latest} unchanged`, !!c.pages?.[LANDING] && !!c.pages?.[POST] && optsBack.length === 49 && vEnd.intact
  && JSON.stringify(vEnd.versions.slice(0, v0.latest).map(v => v.sha256)) === JSON.stringify(v0.versions.map(v => v.sha256)), {version: vBack, options: optsBack.length, intact: vEnd.intact})
 fs.writeFileSync(path.join(out, 'versions-after.json'), JSON.stringify(vEnd, null, 1))
 const cov = (await api('/coverage')).body
 record('existing 47 imported pages and parity report preserved', (await api('/model')).body.pages.length === 47 && Object.values(cov.parity).filter(x => x.body && x.seo).length === Object.values(cov.parity).length, {parity: Object.values(cov.parity).length})
 fs.writeFileSync(path.join(out, 'manager-turns.json'), JSON.stringify((await api('/manager')).body.turns, null, 1))
} catch (e) {
 record('driver error', false, e.stack?.slice(0, 1500))
 await shot(page, 'error').catch(() => {})
} finally {
 fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 1))
 save_()
 await browser.close()
 console.log(`${steps.filter(s => s.ok).length}/${steps.length} steps passed; ${errors.length} page errors`)
}
