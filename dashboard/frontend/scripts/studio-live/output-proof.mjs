// Persisted output vs editor preview, and preserved functionality, on a real isolated Next build.
// Usage: node output-proof.mjs <dashboard-origin> <built-url> <build-folder> <proof-dir> <playwright-module>
// Compares the editor canvas (saved version, desktop 1280 / mobile 390) with the built site at the
// same widths: positions and sizes of edited elements and the page text. Then checks the booking
// form, JSON-LD/SEO, the Brevo webhook's protected negative responses, robots/sitemap and the build's
// changed-file list. External requests are blocked; nothing is submitted anywhere.
import fs from 'node:fs'
import path from 'node:path'
import {JSDOM} from 'jsdom'
const [origin, built, buildFolder, out, pwModule] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const steps = []
const record = (name, ok, detail) => { steps.push({step: name, ok: !!ok, detail}); console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${JSON.stringify(detail).slice(0, 400)}`) }
const browser = await chromium.launch()
const block = page => page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
const buildJson = JSON.parse(fs.readFileSync(path.join(buildFolder, 'build.json'), 'utf8'))
const api = `${origin}/api/website/studio/sites/default-site/live`

// Elements edited in the browser proof (looked up the same way in both documents).
const PROBES = {
 pill: {canvas: '[data-sid="home_8"]', site: '.s-home_8'},
 grid: {canvas: '[data-sid="home_p"]', site: '.s-home_p'},
 heading: {canvas: '[data-sid="home_c"]', site: '.s-home_p ~ *, main h1', text: true},
 image: {canvas: 'img[src*="/assets/"]', site: 'img[src^="/studio-assets/"]'},
 button: {canvas: '[data-sid="home_p"] > [data-sid="home_h"]', site: '.s-home_p > a[href="/free-assessment"]'},
}
const measure = (sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y + window.scrollY), w: Math.round(r.width), h: Math.round(r.height), position: getComputedStyle(e).position, columns: getComputedStyle(e).gridTemplateColumns.split(' ').length} }
const mainText = () => document.querySelector('main').innerText.replace(/\s+/g, ' ').trim()

async function canvasAt(width) {
 const page = await (await browser.newContext({viewport: {width: 1920 + 0, height: 1080}})).newPage()
 await block(page)
 await page.goto(`${origin}/websites?tab=studio&site=default-site`)
 await page.waitForFunction(() => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelectorAll('[data-sid]').length > 50, null, {timeout: 90000})
 await page.getByRole('button', {name: 'Focus', exact: true}).click()
 if (width !== 1280) { await page.getByRole('button', {name: 'Mobile view'}).click() }
 await page.waitForTimeout(1500)
 const frame = await (await page.$('.live-canvas iframe')).contentFrame()
 const result = {}
 for (const [k, p] of Object.entries(PROBES)) result[k] = await frame.evaluate(measure, k === 'heading' ? '[data-sid="home_c"]' : p.canvas)
 result.text = await frame.evaluate(mainText)
 result.width = await frame.evaluate(() => document.documentElement.clientWidth)
 await page.screenshot({path: path.join(out, `canvas-${width}.png`)})
 await page.context().close()
 return result
}
async function siteAt(width) {
 const page = await (await browser.newContext({viewport: {width, height: 1000}})).newPage()
 await block(page)
 await page.goto(built, {waitUntil: 'networkidle'})
 await page.waitForTimeout(800)
 const result = {}
 for (const [k, p] of Object.entries(PROBES)) result[k] = await page.evaluate(measure, k === 'heading' ? 'main h1' : p.site)
 result.text = await page.evaluate(mainText)
 result.width = await page.evaluate(() => document.documentElement.clientWidth)
 await page.screenshot({path: path.join(out, `built-${width}.png`), fullPage: false})
 await page.context().close()
 return result
}
for (const width of [1280, 390]) {
 const [c, s] = [await canvasAt(width), await siteAt(width)]
 const same = (a, b) => a && b && Math.abs(a.x - b.x) <= 2 && Math.abs(a.w - b.w) <= 2 && Math.abs(a.h - b.h) <= 2 && Math.abs(a.y - b.y) <= 3 && a.position === b.position && a.columns === b.columns
 for (const k of Object.keys(PROBES)) record(`${width}px: ${k} — editor preview and built site match`, same(c[k], s[k]), {editor: c[k], built: s[k], viewport: [c.width, s.width]})
 record(`${width}px: page text — editor preview and built site match`, c.text === s.text, {chars: [c.text.length, s.text.length], firstDiff: c.text === s.text ? null : [...c.text].findIndex((ch, i) => ch !== s.text[i])})
}

// Functionality preserved on the built site.
const page = await (await browser.newContext({viewport: {width: 1280, height: 1000}})).newPage()
const external = []
await page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => { external.push(r.request().url()); r.abort() })
await page.goto(new URL('/free-assessment', built).href, {waitUntil: 'networkidle'})
await page.waitForTimeout(1000)
const form = await page.evaluate(() => {
 const f = document.querySelector('iframe[title="Free 15-Minute AI Fit Call booking form"]')
 const main = document.querySelector('main')
 return {src: f?.getAttribute('src'), first: main?.firstElementChild?.getAttribute('aria-label'), script: !!document.querySelector('script[src="https://tally.so/widgets/embed.js"]')}
})
record('booking form: Tally embed keeps its configured form id and loader script, now first in <main>', form.src?.startsWith('https://tally.so/embed/2Eb6ee?') && form.script && form.first === 'Assessment application form', {...form, blockedExternal: [...new Set(external.map(u => new URL(u).host))]})
for (const route of ['/', '/free-assessment', '/pricing', '/blog/ai-fast-start-scan']) {
 const captured = await (await fetch(`${api}/capture?route=${encodeURIComponent(route)}`)).json()
 const html = await (await fetch(new URL(route, built))).text()
 const doc = new JSDOM(html).window.document
 const ld = [...doc.querySelectorAll('script[type="application/ld+json"]')].map(x => x.textContent)
 const title = doc.title
 const canonical = doc.querySelector('link[rel=canonical]')?.getAttribute('href') || null
 record(`SEO preserved on ${route}: title, canonical and JSON-LD identical to the live source`, JSON.stringify(ld) === JSON.stringify(captured.seo.jsonld) && title === captured.seo.title && canonical === captured.seo.canonical, {jsonld: ld.length, title, canonical})
}
const hook = new URL('/api/brevo-booking-webhook', built).href
const get = await fetch(hook)
const post = await fetch(hook, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
record('Brevo webhook route preserved: GET 405, POST without participant 400 (no external call)', get.status === 405 && post.status === 400 && /No participant email/.test(await post.text()), {get: get.status, post: post.status})
const robots = await fetch(new URL('/robots.txt', built)), sitemap = await fetch(new URL('/sitemap.xml', built))
const sm = await sitemap.text()
record('robots.txt and sitemap.xml served; sitemap lists edited and unedited routes', robots.status === 200 && sitemap.status === 200 && sm.includes('/free-assessment') && sm.includes('/pricing'), {robots: robots.status, sitemap: sitemap.status})
const changed = buildJson.changes.map(c => c.path).sort()
const expected = ['app/free-assessment/page.tsx', 'app/layout.tsx', 'app/page.tsx', 'app/ttros-studio.css', 'components/Header.tsx', 'next.config.ts']
record('only edited source files changed (plus the generated stylesheet, its import, the asset and the preview framing config)', expected.every(f => changed.includes(f)) && changed.filter(f => !expected.includes(f)).every(f => f.startsWith('public/studio-assets/')), changed)
record('build is pinned to the live source commit with zero model calls', buildJson.base === '2e521be1ce7469538b0eff234860e7f97bc6c739' && buildJson.modelCalls === 0 && buildJson.sourceUntouched === true, {base: buildJson.base, nextBuildId: buildJson.nextBuildId})
await browser.close()
fs.writeFileSync(path.join(out, 'output-proof.json'), JSON.stringify({built, build: buildJson.nextBuildId, steps}, null, 2))
console.log(`${steps.filter(s => s.ok).length}/${steps.length} passed`)
