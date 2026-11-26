// Revisit: when the Memory reader or shell routes change. · Last touched: 2026-11-09.
// Read-only browser proof of /memory?record=<pointer> on a phone viewport. Brain files are never written.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const base = process.env.BRAIN_LINK_BASE_URL || 'http://127.0.0.1:3010'
const out = '/opt/ttros-core/proofs/brain-record-link'
const good = 'business_brain:index/MEMORY_INDEX.md'
const url = pointer => `${base}/memory?record=${encodeURIComponent(pointer)}`
const results = []
const check = (name, ok, detail = '') => { results.push({ name, ok: Boolean(ok), detail }); console.log(ok ? 'PASS' : 'FAIL', name, detail) }

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
const title = () => page.locator('[data-testid=memory-reader-title]')

await page.goto(url(good))
await page.waitForSelector('[data-testid=memory-document-body]', { timeout: 15000 })
check('deep link opens the record', (await title().innerText()) === 'Memory Index', await title().innerText())
const box = await page.locator('[data-testid=memory-reader]').boundingBox()
check('mobile: reader fills the viewport width', box.width >= 388 && box.x <= 1, JSON.stringify(box))
check('mobile: no horizontal page scroll', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
await page.screenshot({ path: path.join(out, 'mobile-open.png') })

await page.reload()
await page.waitForSelector('[data-testid=memory-document-body]', { timeout: 15000 })
check('refresh keeps the record open', (await title().innerText()) === 'Memory Index' && page.url().includes('record='))

await page.click('[aria-label="Close document"]')
await page.waitForSelector('[data-testid=memory-reader]', { state: 'detached' })
check('close removes the reader and the record param', !page.url().includes('record='), page.url())
await page.goBack()
await page.waitForSelector('[data-testid=memory-document-body]', { timeout: 15000 })
check('browser Back reopens the record', (await title().innerText()) === 'Memory Index')
await page.goForward()
await page.waitForSelector('[data-testid=memory-reader]', { state: 'detached' })
check('browser Forward closes it again', true)

for (const [name, pointer] of [['missing record', 'business_brain:index/does-not-exist-xyz.md'], ['traversal pointer', 'business_brain:../etc/passwd'], ['filesystem path', '/etc/passwd'], ['out-of-scope or non-Brain scheme', 'workspace:secrets.md']]) {
  await page.goto(url(pointer))
  await page.waitForTimeout(1500)
  const body = await page.locator('body').innerText()
  const opened = await page.locator('[data-testid=memory-document-body]').count()
  const reader = await page.locator('[data-testid=memory-reader]').count()
  check(`${name}: no document content is shown`, opened === 0 && !/root:.*:0:0/.test(body), `reader=${reader}`)
  if (reader) check(`${name}: reader reports unable to open`, /Unable to open document/.test(body))
}
await page.screenshot({ path: path.join(out, 'mobile-invalid.png') })
await browser.close()
fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2))
const failed = results.filter(r => !r.ok)
console.log(`${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
