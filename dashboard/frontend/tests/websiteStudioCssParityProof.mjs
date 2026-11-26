// Computed-style and screenshot parity between the untouched 215679e website build (BASELINE_URL) and
// Studio builds (CASES="name=url,..."). Every computed property of every element and its ::before /
// ::after is compared, at desktop and mobile widths. Read-only HTTP against loopback servers.
// Env: PLAYWRIGHT_MODULE, BASELINE_URL, CASES, OUT, ROUTES (optional, comma-separated).
import fs from 'node:fs/promises'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
const out = process.env.OUT, baseline = process.env.BASELINE_URL
const cases = Object.fromEntries(process.env.CASES.split(',').map(c => c.split('=')))
const routes = (process.env.ROUTES || '/,/services/ai-implementation,/services/systems-integration,/about,/blog,/pricing,/how-we-work,/get-guide').split(',')
const viewports = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } }
await fs.mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true })
async function capture(root, route, size, shot) {
 const page = await browser.newPage({ viewport: size })
 await page.goto(root.replace(/\/$/, '') + route, { waitUntil: 'networkidle' })
 await page.evaluate(() => document.fonts.ready)
 // Settle layout before reading used values (auto margins resolve only after layout).
 await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => { document.body.getBoundingClientRect(); r() }))))
 const styles = await page.evaluate(() => {
  const rows = []
  for (const el of document.querySelectorAll('body *')) {
   if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName)) continue
   const path = []
   for (let n = el; n && n !== document.body; n = n.parentElement) path.unshift(n.tagName.toLowerCase() + (typeof n.className === 'string' && n.className.trim() ? '.' + n.className.trim().split(/\s+/).join('.') : ''))
   const r = el.getBoundingClientRect()
   for (const pseudo of [null, '::before', '::after']) {
    const cs = getComputedStyle(el, pseudo)
    if (pseudo && (cs.content === 'none' || cs.content === 'normal')) continue
    const values = {}
    for (let i = 0; i < cs.length; i++) values[cs[i]] = cs.getPropertyValue(cs[i])
    if (!pseudo) Object.assign(values, { 'box:x': r.x.toFixed(2), 'box:y': (r.y + scrollY).toFixed(2), 'box:w': r.width.toFixed(2), 'box:h': r.height.toFixed(2) })
    rows.push([path.join('>') + (pseudo || ''), values])
   }
  }
  const markup = ['header', 'main', 'footer'].map(s => document.querySelector(s)?.outerHTML.replace(/ data-studio-[a-z-]+="[^"]*"/g, '')).join('\n')
  return { rows, markup, scrollWidth: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }
 })
 const png = shot ? await page.screenshot({ fullPage: true, path: shot }) : await page.screenshot({ fullPage: true })
 await page.close()
 return { ...styles, png }
}
const report = {}
for (const [vp, size] of Object.entries(viewports)) for (const route of routes) {
 const slug = route === '/' ? 'home' : route.slice(1).replaceAll('/', '-')
 const shoot = ['/', '/services/ai-implementation'].includes(route)
 const base = await capture(baseline, route, size, shoot ? `${out}/baseline-${slug}-${vp}.png` : null)
 for (const [name, url] of Object.entries(cases)) {
  const next = await capture(url, route, size, shoot ? `${out}/${name}-${slug}-${vp}.png` : null)
  const diffs = []
  if (base.rows.length !== next.rows.length) diffs.push({ element: '(document)', property: 'element count', baseline: base.rows.length, studio: next.rows.length })
  for (let i = 0; i < Math.min(base.rows.length, next.rows.length); i++) {
   const [path, a] = base.rows[i], [path2, b] = next.rows[i]
   if (path !== path2) { diffs.push({ element: path, property: 'element', baseline: path, studio: path2 }); continue }
   for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) if (a[k] !== b[k]) diffs.push({ element: path, property: k, baseline: a[k], studio: b[k] })
  }
  const source = diffs.filter(d => !d.property.startsWith('--studio-')), privateProps = diffs.length - source.length
  // Group the source-property differences by element and property for review (complete, not capped).
  const grouped = {}
  for (const d of source) (grouped[d.element] ||= {})[d.property] = `${d.baseline} -> ${d.studio}`
  const entry = { differingDeclarations: diffs.length, sourcePropertyDifferences: source.length, studioPrivateCustomPropertyDifferences: privateProps, elementsCompared: base.rows.length,
   screenshotIdentical: Buffer.compare(base.png, next.png) === 0, markupIdenticalIgnoringStudioAttributes: base.markup === next.markup, documentHeight: [base.height, next.height], horizontalOverflow: next.scrollWidth > size.width, differences: grouped }
  report[`${name} ${route} ${vp}`] = entry
  console.log(`${name.padEnd(10)} ${vp.padEnd(8)} ${route.padEnd(32)} source=${String(entry.sourcePropertyDifferences).padStart(4)} private=${String(entry.studioPrivateCustomPropertyDifferences).padStart(4)} pixels=${entry.screenshotIdentical ? 'identical' : 'different'} markup=${entry.markupIdenticalIgnoringStudioAttributes ? 'identical' : 'different'}`)
 }
}
await fs.writeFile(`${out}/css-parity.json`, JSON.stringify(report, null, 1))
await browser.close()
