// End-to-end browser proof of the live website editor, driven through the real UI.
// Usage: node browser-proof.mjs <dashboard-origin> <proof-dir> <playwright-module> [api-origin]
// Every step records what was done, what was checked and the result into <proof-dir>/browser-proof.json,
// with screenshots. External network requests are blocked (no third-party loads or submissions).
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import zlib from 'node:zlib'
const [origin, out, pwModule] = process.argv.slice(2)
const {chromium} = await import(pwModule)
fs.mkdirSync(out, {recursive: true})
const profile = path.join(out, '.profile')
fs.rmSync(profile, {recursive: true, force: true})
const steps = []
const record = (name, ok, detail) => { steps.push({step: name, ok: !!ok, detail, at: new Date().toISOString()}); console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${typeof detail === 'string' ? detail : JSON.stringify(detail).slice(0, 300)}`) }
const shot = async (page, name) => { const file = `${String(steps.length).padStart(2, '0')}-${name}.png`; await page.screenshot({path: path.join(out, file)}); return file }
const errors = []

async function open() {
 const context = await chromium.launchPersistentContext(profile, {viewport: {width: 1920, height: 1080}})
 const page = context.pages()[0] || await context.newPage()
 page.on('dialog', d => d.accept())
 page.on('pageerror', e => errors.push('pageerror: ' + e.message.slice(0, 300)))
 page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|rsms\.me/.test(m.text())) errors.push('console: ' + m.text().slice(0, 300)) })
 await page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, r => r.abort())
 await page.goto(`${origin}/websites?tab=studio&site=default-site`)
 await page.waitForFunction(() => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelectorAll('[data-sid]').length > 50, null, {timeout: 90000})
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
 return {context, page}
}
const frameOf = async page => (await page.$('.live-canvas iframe')).contentFrame()
const docs = page => page.evaluate(() => window.__ttrosLive.docs())
function find(list, id, parent = null) {
 for (const item of list || []) {
  if (item.props.id === id) return {item, parent}
  for (const s of ['kids', 'alt']) { const hit = find(item.props[s], id, item); if (hit) return hit }
 }
 return null
}
const where = (d, id) => { for (const [doc, tree] of Object.entries(d)) { const hit = find(tree, id); if (hit) return {doc, ...hit} } return null }
const selected = page => page.locator('[data-testid=live-inspector]').getAttribute('data-selected').catch(() => null)
const status = page => page.locator('[data-testid=live-status]').textContent()
async function clickNode(page, id, opts = {}) {
 const frame = await frameOf(page)
 await frame.locator(`[data-sid="${id}"]`).first().scrollIntoViewIfNeeded()
 await frame.locator(`[data-sid="${id}"]`).first().click({force: true, ...opts})
 await page.waitForTimeout(250)
}
async function selectNode(page, id, viaChild) {
 await clickNode(page, viaChild || id)
 for (let i = 0; i < 8 && (await selected(page)) !== id; i++) { await page.getByRole('button', {name: 'Select parent'}).click(); await page.waitForTimeout(200) }
 return (await selected(page)) === id
}
const rect = async (page, id) => (await frameOf(page)).evaluate(sid => { const e = document.querySelector(`[data-sid="${sid}"]`); const r = e.getBoundingClientRect(); return {x: Math.round(r.x + window.scrollX), y: Math.round(r.y + window.scrollY), w: Math.round(r.width), h: Math.round(r.height)} }, id)
async function iframeOffset(page) { const b = await (await page.$('.live-canvas iframe')).boundingBox(); return b }
async function dragInFrame(page, from, to, steps = 18) {
 const off = await iframeOffset(page)
 await page.mouse.move(off.x + from.x, off.y + from.y)
 await page.mouse.down()
 for (let i = 1; i <= steps; i++) { await page.mouse.move(off.x + from.x + (to.x - from.x) * i / steps, off.y + from.y + (to.y - from.y) * i / steps); await page.waitForTimeout(25) }
 await page.waitForTimeout(250)
 await page.mouse.up()
 await page.waitForTimeout(600)
}
async function switchPage(page, id, sid) {
 await page.selectOption('select[aria-label="Studio page"]', id)
 try { await page.waitForFunction(s => document.querySelector('.live-canvas iframe')?.contentDocument?.querySelector(`[data-sid="${s}"]`), sid, {timeout: 60000}) }
 catch (e) { await page.screenshot({path: path.join(out, `switch-failed-${id}.png`)}); throw Error(`page ${id} did not open: ${await page.locator('.live-studio').innerText().catch(() => '')}`.slice(0, 600)) }
 if (!(await page.locator('.live-grid.live-focus').count())) await page.getByRole('button', {name: 'Focus', exact: true}).click()
 await page.waitForTimeout(800)
}
const vpRect = async (page, selector) => (await frameOf(page)).evaluate(sel => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height} }, selector)
const key = (page, combo) => page.keyboard.press(combo)
function png(width, height, rgb) {
 const raw = Buffer.alloc((width * 3 + 1) * height)
 for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) { const o = y * (width * 3 + 1) + 1 + x * 3; raw[o] = rgb[0] ^ (x * 4); raw[o + 1] = rgb[1] ^ (y * 4); raw[o + 2] = rgb[2] }
 const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const body = Buffer.concat([Buffer.from(type), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body) >>> 0); return Buffer.concat([len, body, crc]) }
 const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 2
 return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

let {context, page} = await open()
const t0 = Date.now()
let base = Number((await status(page)).match(/ v(\d+) /)?.[1] || 0)
if (base > 0) {
 // Start from the imported live website: restore it as a new version (non-destructive).
 await page.getByRole('button', {name: 'Versions', exact: true}).click()
 await page.locator('[data-version="0"] >> role=button[name="Restore"]').click()
 await page.waitForFunction(b => new RegExp(`as new version v${b + 1}`).test(document.querySelector('[data-testid=live-status]')?.textContent || ''), base, {timeout: 30000})
 base += 1
 record('start from the imported live site (restored as a new version)', true, await status(page))
 await page.getByRole('button', {name: 'Inspector', exact: true}).click()
}
const V = n => `v${base + n}`
record('open editor on live homepage', (await (await frameOf(page)).locator('[data-sid]').count()) > 300, {status: await status(page), nodes: await (await frameOf(page)).locator('[data-sid]').count()})
await shot(page, 'opened')

// 1. Select a nested element (6 levels deep) by clicking it on the canvas, then its parent.
await clickNode(page, 'home_9')
const nested = await selected(page)
await page.getByRole('button', {name: 'Select parent'}).click(); await page.waitForTimeout(200)
record('select nested element on canvas, then its parent', nested === 'home_9' && (await selected(page)) === 'home_8', {clicked: nested, parent: await selected(page)})

// 2. Move it freely with the on-canvas handle; check the canvas and the model; undo/redo by keyboard.
const before = await rect(page, 'home_8')
const handle = await vpRect(page, '[data-studio-handle="move"]')
await dragInFrame(page, {x: handle.x + 10, y: handle.y + 8}, {x: handle.x + 10 + 160, y: handle.y + 8 + 40}, 12)
const after = await rect(page, 'home_8')
let d = await docs(page)
const moved = where(d, 'home_8')
record('free-move nested element by dragging (desktop)', Math.abs(after.x - before.x - 160) <= 2 && Math.abs(after.y - before.y - 40) <= 2 && moved.item.props.st?.d?.left === '160px', {before, after, st: moved.item.props.st})
await shot(page, 'moved-nested')
await key(page, 'Control+z'); await page.waitForTimeout(500)
const undone = await rect(page, 'home_8')
d = await docs(page)
const undoOk = Math.abs(undone.x - before.x) <= 1 && !where(d, 'home_8').item.props.st
await key(page, 'Control+Shift+z'); await page.waitForTimeout(500)
const redone = await rect(page, 'home_8')
record('undo (Ctrl+Z) and redo (Ctrl+Shift+Z) of the move', undoOk && Math.abs(redone.x - after.x) <= 1, {undone, redone})
await key(page, 'Control+z'); await page.waitForTimeout(400)
await key(page, 'Control+y'); await page.waitForTimeout(400)
record('redo with Ctrl+Y', Math.abs((await rect(page, 'home_8')).x - after.x) <= 1, await rect(page, 'home_8'))

// 3. Drag the hero heading into another container (the stats band) with the action-bar drag handle.
await selectNode(page, 'home_c', 'home_d')
const target = await vpRect(page, '[data-sid="home_p"]')
const grip = await vpRect(page, '[data-studio-handle="reparent"]')
await dragInFrame(page, {x: grip.x + grip.w / 2, y: grip.y + grip.h / 2}, {x: target.x + target.w - 40, y: target.y - 4}, 24)
d = await docs(page)
const h1 = where(d, 'home_c')
record('drag heading <h1> from the hero into the stats band container', h1.parent?.props.id === 'home_o', {newParent: h1.parent?.props.id, index: h1.parent?.props.kids.findIndex(k => k.props.id === 'home_c')})
await shot(page, 'heading-dragged')

// 4. Drag a button into a different container.
await selectNode(page, 'home_h', 'home_i')
// Drop point: in the gap between the grid's first two items (inside the grid, not inside an item).
const gap = await (await frameOf(page)).evaluate(() => { const g = document.querySelector('[data-sid="home_p"]'); const [a, b] = [...g.children].filter(k => k.getBoundingClientRect().width); const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect(); return {x: (ra.right + rb.left) / 2, y: ra.top + ra.height / 2} })
const grip2 = await vpRect(page, '[data-studio-handle="reparent"]')
await dragInFrame(page, {x: grip2.x + grip2.w / 2, y: grip2.y + grip2.h / 2}, gap, 24)
d = await docs(page)
record('drag button link into another container (stats grid)', where(d, 'home_h').parent?.props.id === 'home_p', {newParent: where(d, 'home_h').parent?.props.id})

// 5. Change columns of a grid at desktop and at mobile.
await selectNode(page, 'home_p', 'home_s')
await page.getByTestId('live-inspector').getByRole('button', {name: '3 columns'}).click(); await page.waitForTimeout(400)
const cols = await (await frameOf(page)).evaluate(() => getComputedStyle(document.querySelector('[data-sid="home_p"]')).gridTemplateColumns.split(' ').length)
await page.getByRole('button', {name: 'Mobile view'}).click(); await page.waitForTimeout(900)
await selectNode(page, 'home_p', 'home_s')
await page.getByTestId('live-inspector').getByRole('button', {name: '1 columns'}).click(); await page.waitForTimeout(400)
const mcols = await (await frameOf(page)).evaluate(() => getComputedStyle(document.querySelector('[data-sid="home_p"]')).gridTemplateColumns.split(' ').length)
d = await docs(page)
record('change grid columns: 3 on desktop, 1 on mobile', cols === 3 && mcols === 1, {desktop: cols, mobile: mcols, st: where(d, 'home_p').item.props.st})
await shot(page, 'mobile-columns')
await page.getByRole('button', {name: 'Desktop view'}).click(); await page.waitForTimeout(900)

// 7. Upload an image and add it as a new image block from the asset library (inside the hero).
const file = path.join(out, 'studio-proof-image.png')
fs.writeFileSync(file, png(96, 64, [190, 150, 90]))
await page.getByRole('button', {name: 'Assets', exact: true}).click()
await selectNode(page, 'home_7', 'home_f')
await page.setInputFiles('input[aria-label="Upload asset"]', file)
await page.waitForSelector('[data-asset]', {timeout: 15000})
const assetId = await page.locator('[data-asset]').first().getAttribute('data-asset')
await page.locator(`[data-asset="${assetId}"]`).click(); await page.waitForTimeout(600)
d = await docs(page)
const inserted = Object.values(d).flatMap(t => JSON.stringify(t).match(new RegExp(`"src":"asset:${assetId}"`, 'g')) || [])
const loaded = await (await frameOf(page)).evaluate(sha => { const i = [...document.querySelectorAll('img')].find(x => x.src.includes(sha)); return i ? {w: i.naturalWidth, h: i.naturalHeight} : null }, assetId)
record('upload image and insert an image block from the asset library', inserted.length === 1 && loaded?.w === 96, {asset: assetId, loaded, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') === assetId})
await shot(page, 'image-inserted')
await page.getByRole('button', {name: 'Insert', exact: true}).click()
// 6. Resize the new image with the corner handle.
const newImg = await (await frameOf(page)).evaluate(sha => [...document.querySelectorAll('img')].find(x => x.src.includes(sha)).getAttribute('data-sid'), assetId)
await selectNode(page, newImg)
const img0 = await rect(page, newImg)
const se = await vpRect(page, '[data-studio-handle="se"]')
await dragInFrame(page, {x: se.x + 5, y: se.y + 5}, {x: se.x + 5 + 80, y: se.y + 5 + 80}, 10)
const img1 = await rect(page, newImg)
d = await docs(page)
record('resize image with the corner handle', Math.abs(img1.w - (img0.w + 80)) <= 2 && Math.abs(img1.h - (img0.h + 80)) <= 2 && where(d, newImg).item.props.st?.d?.width === `${img0.w + 80}px`, {before: img0, after: img1, st: where(d, newImg).item.props.st})
await shot(page, 'image-resized')


// 8. Move a header element: the CTA moves before the main navigation (shared Header, every page).
await selectNode(page, 'c-header_13', 'c-header_14')
const headerBefore = (await docs(page))['components/Header.tsx']
for (let i = 0; i < 3; i++) { await page.getByRole('button', {name: 'Move up'}).click(); await page.waitForTimeout(250) }
await page.getByRole('button', {name: '← out'}).click(); await page.waitForTimeout(300)
d = await docs(page)
const cta = where(d, 'c-header_13')
record('move header CTA out of its wrapper into the header row (shared Header document)', cta.doc === 'components/Header.tsx' && cta.parent?.props.id === 'c-header_2', {parent: cta.parent?.props.id, headerChanged: JSON.stringify(headerBefore) !== JSON.stringify(d['components/Header.tsx'])})
await shot(page, 'header-moved')

// 9. Protected: moving a functional header element (with its handlers) into the page is refused.
await selectNode(page, 'c-header_8', 'c-header_9')
const sol = await vpRect(page, '[data-sid="c-header_8"]')
const grip3 = await vpRect(page, '[data-studio-handle="reparent"]')
const mainTarget = await vpRect(page, '[data-sid="home_g"]')
await dragInFrame(page, {x: grip3.x + grip3.w / 2, y: grip3.y + grip3.h / 2}, {x: mainTarget.x + 20, y: mainTarget.y + 10}, 24)
d = await docs(page)
record('refuse moving the Solutions dropdown (uses Header state/handlers) into the page', where(d, 'c-header_8')?.doc === 'components/Header.tsx', {doc: where(d, 'c-header_8')?.doc, status: await status(page), from: sol})

// 10. Save a version (Ctrl+S).
await key(page, 'Control+s'); await page.waitForTimeout(1500)
const v1status = await status(page)
record(`save version with Ctrl+S (${V(1)})`, v1status.includes(`Saved version ${V(1)} `), v1status)

// 11. Functional booking form on /free-assessment: move the form section above the hero.
await switchPage(page, 'free-assessment', 'free-assessment_p')
await selectNode(page, 'free-assessment_m', 'free-assessment_p')
await page.getByRole('button', {name: 'Move up'}).click(); await page.waitForTimeout(300)
await page.getByRole('button', {name: 'Move up'}).click(); await page.waitForTimeout(300)
d = await docs(page)
const fa = d['app/free-assessment/page.tsx']
const mainKids = fa.find(i => i.props.tag === 'main')?.props.kids.map(k => k.props.id)
const iframe = where(d, 'free-assessment_p').item
record('move the functional booking form section (Tally embed) above the hero', mainKids?.[0] === 'free-assessment_m' && !!iframe.props.x?.src, {order: mainKids?.slice(0, 3), iframeCodeBinding: iframe.props.x})
await shot(page, 'form-moved')
await key(page, 'Control+s'); await page.waitForTimeout(1500)
record(`save version ${V(2)}`, (await status(page)).includes(`Saved version ${V(2)} `), await status(page))

// 12. Unsaved edit, then close the browser without saving (crash), and reopen.
await switchPage(page, 'home', 'home_c')
await clickNode(page, 'home_d')
await page.fill('[data-testid=live-text]', 'Edited headline before a crash')
await page.locator('[data-testid=live-text]').blur(); await page.waitForTimeout(3500)
await context.close()
;({context, page} = await open())
const recovered = await page.locator('[data-testid=live-recovered]').textContent().catch(() => '')
const headline = await (await frameOf(page)).locator('[data-sid="home_d"]').textContent()
record('reopen after closing without saving: autosave recovered', /Recovered unsaved changes/.test(recovered) && headline === 'Edited headline before a crash', {recovered, headline})
const undoEnabled = await page.getByRole('button', {name: 'Undo'}).isEnabled()
await page.getByRole('button', {name: 'Undo'}).click(); await page.waitForTimeout(500)
const afterUndo = await (await frameOf(page)).locator('[data-sid="home_d"]').textContent()
record('undo history survives the reopen (Undo reverts the recovered edit)', undoEnabled && afterUndo !== 'Edited headline before a crash', {afterUndo})
await page.getByRole('button', {name: 'Redo'}).click(); await page.waitForTimeout(500)
await key(page, 'Control+s'); await page.waitForTimeout(1500)
record(`save version ${V(3)} (headline edit)`, (await status(page)).includes(`Saved version ${V(3)} `), await status(page))
await shot(page, 'reopened-v3')

// 13. Version history: preview v1, restore v1 as a new version (v4); v2 and v3 remain.
await page.getByRole('button', {name: 'Versions', exact: true}).click()
await page.locator(`[data-version="${base + 1}"] >> role=button[name="Preview"]`).click()
await page.waitForSelector('[data-testid=live-version-preview]')
const previewText = await page.frameLocator('[data-testid=live-version-preview] iframe').locator('h1').first().textContent().catch(() => '')
record(`preview version ${V(1)} (timestamped list)`, /AI operations/.test(previewText), {previewH1: previewText, rows: await page.locator('[data-testid=live-versions] li').count()})
await shot(page, 'version-preview')
await page.getByRole('button', {name: 'Close'}).click()
await page.locator(`[data-version="${base + 1}"] >> role=button[name="Restore"]`).click(); await page.waitForTimeout(2000)
const restoredHeadline = await (await frameOf(page)).locator('[data-sid="home_d"]').textContent()
const versionsRows = await page.locator('[data-testid=live-versions] li').count()
record(`restore ${V(1)} non-destructively (new version ${V(4)}; ${V(2)}, ${V(3)} kept)`, (await status(page)).includes(`version ${V(4)}`) && restoredHeadline !== 'Edited headline before a crash' && versionsRows === base + 5, {status: await status(page), restoredHeadline, versionsRows})
await key(page, 'Control+z'); await page.waitForTimeout(600)
record(`undo the restore on the canvas returns to ${V(3)} content`, (await (await frameOf(page)).locator('[data-sid="home_d"]').textContent()) === 'Edited headline before a crash', await (await frameOf(page)).locator('[data-sid="home_d"]').textContent())
await key(page, 'Control+Shift+z'); await page.waitForTimeout(600)
await page.getByRole('button', {name: 'Versions', exact: true}).click()
await page.locator(`[data-version="${base + 3}"] >> role=button[name="Restore"]`).click(); await page.waitForTimeout(2000)
record(`restore ${V(3)} (new version ${V(5)}) for the build`, (await status(page)).includes(`version ${V(5)}`), await status(page))

// 14. Real Next.js build of the saved version and preview desktop/mobile.
await page.getByRole('button', {name: 'Preview & build'}).click()
await page.getByRole('button', {name: 'Build real Next.js preview'}).click()
await page.waitForFunction(() => ['ready', 'failed'].includes(document.querySelector('[data-testid=live-build-status]')?.dataset.status), null, {timeout: 900000})
const buildStatus = await page.locator('[data-testid=live-build-status]').getAttribute('data-status')
const buildText = await page.locator('[data-testid=live-build-status] p').first().textContent()
record('build saved version into the real Next.js site (isolated)', buildStatus === 'ready', buildText)
const builtUrl = await page.locator('[data-testid=live-next-preview]').getAttribute('src').catch(() => null)
await page.getByRole('button', {name: 'Expand preview'}).click(); await page.waitForTimeout(4000)
await shot(page, 'next-preview-desktop')
const builtHeadline = await page.frameLocator('[data-testid=live-next-preview]').locator('h1').first().textContent().catch(() => null)
record('real Next.js preview shows the saved edits (desktop 1280)', builtHeadline === 'Edited headline before a crash', {builtHeadline})
await page.getByRole('button', {name: 'Mobile 390'}).click(); await page.waitForTimeout(3000)
await shot(page, 'next-preview-mobile')
const mobileWidth = await page.frameLocator('[data-testid=live-next-preview]').locator('html').evaluate(h => h.clientWidth).catch(() => null)
record('real Next.js preview at mobile width', mobileWidth === 390 || mobileWidth === 375 || (mobileWidth > 360 && mobileWidth <= 390), {mobileWidth})
await page.getByRole('button', {name: 'Close', exact: true}).click()
fs.writeFileSync(path.join(out, 'built-url.txt'), builtUrl || '')
fs.writeFileSync(path.join(out, 'browser-proof.json'), JSON.stringify({origin, seconds: Math.round((Date.now() - t0) / 1000), steps, errors: errors.slice(0, 50), builtUrl}, null, 2))
await context.close()
console.log(`${steps.filter(s => s.ok).length}/${steps.length} passed; ${errors.length} browser errors`)
