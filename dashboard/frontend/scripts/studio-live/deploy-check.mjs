// Browser/functional check of one deployed website URL (staged Vercel deployment or production).
// Usage: node deploy-check.mjs <base-url> <evidence-dir> <playwright-module>
// Read-only: it never submits a form, books, subscribes or posts to the Brevo webhook. Prints one JSON
// line {ok, checks:[{check, ok, detail}]} and writes desktop/mobile screenshots to the evidence dir.
// Revisit: when the website's navigation, FAQ, booking embed or routes change. Last touched: 2026-11-22.
import fs from 'node:fs'
import path from 'node:path'

const [base, out, pwModule] = process.argv.slice(2)
if (!base || !out || !pwModule) throw Error('Usage: deploy-check.mjs <base-url> <evidence-dir> <playwright-module>')
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const checks = []
const add = (check, ok, detail = '') => checks.push({check, ok: !!ok, detail: String(detail).slice(0, 400)})
const browser = await chromium.launch()
const errors = []
async function page(viewport) {
 const context = await browser.newContext({viewport, userAgent: 'ttros-website-studio-release-check'})
 // Third-party scripts (analytics, Tally) are not loaded: the check is about the site's own code.
 await context.route(url => !url.href.startsWith(base), route => route.abort())
 const p = await context.newPage()
 p.on('pageerror', e => errors.push(`${viewport.width}: ${e.message}`))
 return p
}
try {
 const d = await page({width: 1280, height: 900})
 const r = await d.goto(base + '/', {waitUntil: 'networkidle'})
 add('home_desktop', r.status() === 200 && await d.locator('h1').count() > 0, `status ${r.status()}, title "${await d.title()}"`)
 await d.screenshot({path: path.join(out, 'home-desktop.png'), fullPage: false})
 const solutions = d.getByRole('button', {name: 'Solutions'})
 if (await solutions.count()) {
  await solutions.first().hover()
  if ((await solutions.first().getAttribute('aria-expanded')) !== 'true') await solutions.first().click()
  add('navigation_dropdown', (await solutions.first().getAttribute('aria-expanded')) === 'true' && await d.locator('a[href="/inbound-reply-system"]:visible').count() > 0, 'Solutions menu opens with service links')
 } else add('navigation_dropdown', false, 'Solutions button missing')
 const faq = d.locator('button[aria-controls^="faq-answer-"]').first()
 if (await faq.count()) {
  await faq.scrollIntoViewIfNeeded(); await faq.click()
  add('faq_accordion', (await faq.getAttribute('aria-expanded')) === 'true', 'first FAQ question expands')
 } else add('faq_accordion', false, 'no FAQ accordion on the homepage')
 const fa = await d.goto(base + '/free-assessment', {waitUntil: 'domcontentloaded'})
 const frame = await d.locator('iframe[src*="tally.so/embed/"]').count()
 add('booking_form_embed', fa.status() === 200 && frame > 0, `status ${fa.status()}, Tally booking frames ${frame}`)
 const blog = await d.goto(base + '/blog', {waitUntil: 'domcontentloaded'})
 const first = d.locator('main a[href^="/blog/"]').first()
 const href = await first.getAttribute('href')
 await first.click(); await d.waitForURL(u => u.pathname === href, {timeout: 15000})
 add('blog_navigation', blog.status() === 200 && new URL(d.url()).pathname === href && await d.locator('h1').count() > 0, `blog → ${href}`)
 const guide = await d.goto(base + '/guides/4-prompt-project-workflow', {waitUntil: 'domcontentloaded'})
 add('static_guide', guide.status() === 200 && (await d.title()).length > 0, `status ${guide.status()}, "${await d.title()}"`)
 const m = await page({width: 390, height: 844})
 const rm = await m.goto(base + '/', {waitUntil: 'networkidle'})
 const menu = m.getByRole('button', {name: /open menu/i})
 let detail = `status ${rm.status()}`
 let ok = rm.status() === 200 && await menu.count() > 0
 if (ok) {
  await menu.first().click()
  ok = await m.getByRole('button', {name: /close menu/i}).count() > 0 && await m.locator('a[href="/pricing"]:visible').count() > 0
  detail += ', mobile menu opens (Close menu, links visible)'
 }
 add('mobile_menu', ok, detail)
 await m.screenshot({path: path.join(out, 'home-mobile.png'), fullPage: false})
 add('no_page_errors', errors.length === 0, errors.join(' | ') || 'none')
} catch (e) {
 add('browser', false, `${e.name}: ${e.message}`)
} finally {
 await browser.close()
}
const result = {ok: checks.length > 0 && checks.every(c => c.ok), base, checks, model_calls: 0}
fs.writeFileSync(path.join(out, 'browser-checks.json'), JSON.stringify(result, null, 2))
console.log(JSON.stringify(result))
