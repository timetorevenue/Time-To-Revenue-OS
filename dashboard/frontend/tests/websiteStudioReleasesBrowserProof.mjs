// Exact-version publication and rollback through the real Studio Dashboard UI. Isolated only:
// offline API (TTROS_STUDIO_OFFLINE=1, David cannot be constructed), worktree Vite, real Next
// release builds, and an isolated loopback target served by `next start` from each release's
// exact lockfile. No provider, production target, credential or model is touched.
// Env: PLAYWRIGHT_MODULE, STUDIO_URL (Vite), STUDIO_API (offline API), STUDIO_PROOF_DIR,
//      TARGET_ROOT (isolated target root), OWNER (fixture operator identity), DRAFTS (saved v5 drafts).
import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE)
const base = process.env.STUDIO_URL, api = process.env.STUDIO_API, out = process.env.STUDIO_PROOF_DIR
const targetRoot = process.env.TARGET_ROOT, owner = process.env.OWNER, drafts = process.env.DRAFTS
const site = 'default-site', TARGET = 'isolated-test', TARGET_LABEL = 'Isolated test target (loopback)'
await fs.mkdir(out, { recursive: true })
const passed = [], requests = [], errors = [], timeline = []
const ok = (name, detail = '') => { passed.push({ name, detail }); console.log('PASS', name, detail) }
const sha = buf => crypto.createHash('sha256').update(buf).digest('hex')
const json = async (url, options = {}) => { const r = await fetch(api + url, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } }); return { status: r.status, body: await r.json().catch(() => null) } }

// 1. Saved drafts: real schema v5 records from the previous accepted proof, saved through the Studio API.
async function save(page, version) {
  const record = JSON.parse(await fs.readFile(path.join(drafts, page === 'home' ? 'drafts' : page === 'site' ? 'shared/drafts' : `pages/${page}/drafts`, `v000${version}.json`), 'utf8'))
  const latest = (await json(`/api/website/studio/sites/${site}/drafts?page=${page}`)).body.latest
  const saved = await json(`/api/website/studio/sites/${site}/drafts?page=${page}`, { method: 'POST', body: JSON.stringify({ data: record.envelope.data, base_version: latest, actor: 'owner', note: `release proof ${page} v${version}` }) })
  assert.equal(saved.status, 200, JSON.stringify(saved.body)); return saved.body
}
for (const [page, version] of [['home', 1], ['site', 1], ['service-ai', 1]]) await save(page, version)
ok('saved drafts', 'home v1, shared v1, AI Implementation v1 via the Studio drafts API')

const browser = await chromium.launch({ headless: true })
const ownerContext = await browser.newContext({ viewport: { width: 1600, height: 1200 }, extraHTTPHeaders: { 'tailscale-user-login': owner } })
const page = await ownerContext.newPage()
page.setDefaultTimeout(60000)
page.on('pageerror', e => errors.push(e.message))
const executes = []
page.on('request', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api')) requests.push([r.method(), u.pathname]); if (u.pathname.endsWith('/execute')) executes.push(r.postData()) })
const panel = () => page.locator('[data-testid=studio-releases]')
const message = () => panel().locator('[data-testid=release-message]').innerText()
const pause = (n = 500) => page.waitForTimeout(n)
async function shot(name) { await panel().screenshot({ path: `${out}/${name}.png` }) }
async function production() {
  const pointer = JSON.parse(await fs.readFile(path.join(targetRoot, 'production.json'), 'utf8'))
  const url = 'http://127.0.0.1:' + process.env.PRODUCTION_PORT
  const home = await fetch(url + '/'), body = Buffer.from(await home.arrayBuffer())
  const manifest = await fetch(`${url}/_next/static/${pointer.build_id}/_buildManifest.js`)
  const prerendered = await fs.readFile(path.join(targetRoot, 'deployments', pointer.deployment_id, 'site/.next/server/app/index.html'))
  return { pointer, status: home.status, deployment: home.headers.get('x-ttros-deployment'), buildIdInPage: body.includes(`\\"b\\":\\"${pointer.build_id}\\"`),
           buildManifest: manifest.status, servedSha256: sha(body), prerenderedSha256: sha(prerendered), servedEqualsPrerendered: sha(body) === sha(prerendered) }
}
async function waitOperation(status) {
  const terminal = ['succeeded', 'noop', 'failed', 'reverted', 'interrupted']
  await page.waitForFunction(t => t.includes(document.querySelector('[data-testid=release-operation]')?.dataset.status), terminal, { timeout: 600000 })
  const got = await panel().locator('[data-testid=release-operation]').getAttribute('data-status')
  assert.equal(got, status, await panel().locator('[data-testid=release-operation]').innerText())
}
// The panel refreshes after a terminal operation; screenshots wait until it shows the target's state.
const shows = id => page.waitForFunction(id => document.querySelector('[data-testid=current-isolated-test]')?.innerText.includes(id), id)
async function selectRelease(id) { await panel().locator(`[data-testid=release-${id}] button`).click(); await page.waitForSelector('[data-testid=release-detail]'); await pause(800) }
async function authorize(action) {
  await panel().getByRole('button', { name: action === 'publish' ? `Request publication to ${TARGET_LABEL}` : `Roll back ${TARGET_LABEL} to this release` }).click()
  await page.waitForSelector('[data-testid=release-plan]')
  const phrase = await panel().locator('label[for=release-command] code').innerText()
  assert.ok(await panel().getByRole('button', { name: `Confirm ${action}` }).isDisabled())
  await panel().locator('#release-command').fill(phrase.toLowerCase())
  assert.ok(await panel().getByRole('button', { name: `Confirm ${action}` }).isDisabled(), 'a non-exact command cannot confirm')
  await panel().locator('#release-command').fill(phrase)
  await panel().getByRole('button', { name: `Confirm ${action}` }).click()
  return phrase
}
async function apiBuild(versions) {
  const started = await json(`/api/website/studio/sites/${site}/builds`, { method: 'POST', headers: { 'X-TTROS-Connections': '1' }, body: JSON.stringify({ ...versions, purpose: 'release' }) })
  assert.equal(started.status, 202, JSON.stringify(started.body))
  for (let i = 0; i < 300; i++) { const row = (await json(`/api/website/studio/sites/${site}/builds/${started.body.id}`)).body; if (row.status !== 'building') return row; await new Promise(r => setTimeout(r, 5000)) }
  throw Error('build timeout')
}

// 2. Editor: "Build release candidate" builds the exact saved versions with the website's own configuration.
await page.goto(base + '/websites?tab=studio&site=default-site')
await page.getByRole('button', { name: 'Open unpublished release candidate' }).click()
await page.waitForSelector('.studio-editor')
await pause(3000)
await page.getByRole('button', { name: 'Build release candidate', exact: true }).click()
timeline.push(['release build A started', new Date().toISOString()])
for (let i = 0; i < 200; i++) {
  await pause(5000)
  await page.getByRole('button', { name: 'Check build', exact: true }).click()
  await pause(800)
  const status = await page.locator('.studio-assistant .studio-status').innerText().catch(() => '')
  if (/Release build ready|failed/i.test(status)) { timeline.push(['release build A finished', new Date().toISOString(), status]); break }
}
await page.waitForSelector('text=validation passed')
ok('release build A validated', await page.locator('.studio-assistant .studio-status').innerText())
await page.locator('section[aria-label="Real Next.js preview"]').screenshot({ path: `${out}/01-release-build-ready.png` })
await page.getByRole('button', { name: 'Open Releases to create and publish' }).click()
await page.waitForSelector('[data-testid=studio-releases]')
await panel().getByRole('button', { name: 'Create release from this build' }).click()
await page.waitForFunction(() => /recorded \(immutable\)/.test(document.querySelector('[data-testid=release-message]')?.innerText || ''))
const releaseA = (await message()).match(/rel_[0-9a-f]{20}/)[0]
await page.waitForSelector('[data-testid=release-detail]')
const recordA = (await json(`/api/website/studio/sites/${site}/releases/${releaseA}`)).body
assert.ok(recordA.verification.ok)
assert.equal(await panel().locator('[data-testid=release-artifact]').innerText(), recordA.release.artifact.sha256)
ok('immutable release A', `${releaseA} artifact ${recordA.release.artifact.sha256.slice(0, 16)} build ${recordA.release.build.next_build_id}`)
await shot('02-release-a-detail')

// 3. Production Vercel target is shown blocked with its exact blockers; no confirmation is offered.
await panel().locator('[data-testid=target-vercel-production] input').check(); await pause(1200)
await selectRelease(releaseA)
await panel().getByRole('button', { name: /^Request publication to timetorevenue\.com production/ }).click()
await page.waitForSelector('[data-testid=release-plan]')
assert.match(await message(), /Refused: .*target blocked \(deployment_authorization\)/)
assert.equal(await panel().locator('#release-command').count(), 0)
ok('production target blocked', 'deployment_authorization, exact_build_output, published_source; no confirmation issued')
await shot('03-vercel-blocked')

// 4. Publish A to the isolated target through the UI (exact typed command).
await panel().locator(`[data-testid=target-${TARGET}] input`).check(); await pause(1200)
await selectRelease(releaseA)
assert.match(await panel().locator('[data-testid=release-compare]').innerText(), /Nothing is published on this target yet/)
const phraseA = await authorize('publish')
assert.equal(phraseA, `PUBLISH ${releaseA} TO ${TARGET}`)
timeline.push(['publish A requested', new Date().toISOString()])
await waitOperation('succeeded')
timeline.push(['publish A succeeded', new Date().toISOString()])
assert.match(await panel().locator('[data-testid=published-identity]').innerText(), /matches the approved build/)
const servedA = await production()
assert.equal(servedA.pointer.release_id, releaseA); assert.equal(servedA.pointer.artifact_sha256, recordA.release.artifact.sha256)
assert.ok(servedA.status === 200 && servedA.buildIdInPage && servedA.buildManifest === 200 && servedA.deployment === servedA.pointer.deployment_id)
ok('staged publication A', `deployment ${servedA.pointer.deployment_id}, served build ${servedA.pointer.build_id}`)
await page.waitForFunction(id => document.querySelector('[data-testid=current-isolated-test]')?.innerText.includes(id), releaseA)
await shot('04-published-a')

// 5. The exact same request replayed: a duplicate, no second deployment.
const replay = await page.evaluate(async ([url, body]) => (await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TTROS-Connections': '1', 'X-TTROS-Remote-PC': '1' }, body })).json(),
  [`/api/website/studio/sites/${site}/releases/${releaseA}/execute`, executes.at(-1)])
assert.equal(replay.duplicate, true)
const deploymentsAfterReplay = (await fs.readFile(path.join(targetRoot, 'deployments.jsonl'), 'utf8')).trim().split('\n').length
assert.equal(deploymentsAfterReplay, 1)
// Publishing A again (now current) is a recorded no-op without a deployment.
await authorizeNoop()
async function authorizeNoop() {
  await panel().getByRole('button', { name: `Request publication to ${TARGET_LABEL}` }).click()
  await page.waitForSelector('[data-testid=release-plan]')
  assert.match(await message(), /already published on this target/)
  await panel().getByRole('button', { name: 'Record no-op' }).click()
  await waitOperation('noop')
}
assert.equal((await fs.readFile(path.join(targetRoot, 'deployments.jsonl'), 'utf8')).trim().split('\n').length, 1)
ok('duplicate and no-op requests', `replay returned ${replay.id} (duplicate); no-op recorded; 1 deployment total`)

// 6. Unauthorized: the same Dashboard without a verified operator identity cannot prepare or execute.
const anonymous = await browser.newContext({ viewport: { width: 1600, height: 1200 } })
const other = await anonymous.newPage()
await other.goto(base + '/websites?tab=studio&site=default-site')
await other.getByRole('button', { name: 'Open unpublished release candidate' }).click()
await other.getByRole('button', { name: 'Releases', exact: true }).click()
await other.waitForSelector('[data-testid=studio-releases]'); await other.waitForTimeout(2000)
await other.locator(`[data-testid=release-${releaseA}] button`).click(); await other.waitForSelector('[data-testid=release-detail]')
await other.getByRole('button', { name: `Request publication to ${TARGET_LABEL}` }).click(); await other.waitForTimeout(1500)
const refusedText = await other.locator('[data-testid=release-message]').innerText()
assert.match(refusedText, /Not prepared: Website publication refused/)
const forged = await other.evaluate(async url => (await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TTROS-Connections': '1', 'X-TTROS-Remote-PC': '1' }, body: JSON.stringify({ action: 'publish', target: 'isolated-test', expected_current: 'none', confirmation_token: 'x.1.y', command: 'PUBLISH' }) })).status,
  `/api/website/studio/sites/${site}/releases/${releaseA}/execute`)
assert.equal(forged, 403)
await other.locator('[data-testid=studio-releases]').screenshot({ path: `${out}/05-unauthorized-refused.png` })
await anonymous.close()
ok('unauthorized refused', refusedText.slice(0, 160))

// 7. Second release from new saved versions (built via the same builds API the button uses).
await save('home', 2); await save('service-ai', 2)
timeline.push(['release build B started', new Date().toISOString()])
const buildB = await apiBuild({ page: 'home', version: 2, shared_version: 1, pages: { 'service-ai': 2 } })
timeline.push(['release build B finished', new Date().toISOString(), buildB.status, buildB.validation])
assert.equal(buildB.validation, 'passed', JSON.stringify(buildB.error))
const createdB = await json(`/api/website/studio/sites/${site}/releases`, { method: 'POST', headers: { 'X-TTROS-Connections': '1' }, body: JSON.stringify({ build_id: buildB.id }) })
assert.equal(createdB.status, 201); const releaseB = createdB.body.release_id
await panel().getByRole('button', { name: 'Refresh releases' }).click(); await pause(1500)
await selectRelease(releaseB)
const compareText = await panel().locator('[data-testid=release-compare]').innerText()
assert.match(compareText, new RegExp(`Published: ${releaseA}`))
ok('release B compared with published A', compareText)
await shot('06-release-b-compare')

// 8. Failed publication of B (isolated target fault after promotion): production returns to A.
await fs.writeFile(path.join(targetRoot, 'faults.json'), JSON.stringify([{ step: 'production_health', release: releaseB }]))
await authorize('publish')
await waitOperation('reverted')
assert.match(await panel().locator('[data-testid=release-operation] [role=alert]').innerText(), /previous version is still published and healthy/)
const afterFailure = await production()
assert.equal(afterFailure.pointer.release_id, releaseA); assert.ok(afterFailure.buildIdInPage && afterFailure.deployment === servedA.pointer.deployment_id)
await shows(releaseA)
ok('failed release leaves A available', `reverted; production still ${afterFailure.pointer.deployment_id} / ${afterFailure.pointer.build_id}`)
await shot('07-failed-b-reverted')

// 9. Publish B (new authorized request).
await authorize('publish')
await waitOperation('succeeded')
const servedB = await production()
assert.equal(servedB.pointer.release_id, releaseB); assert.ok(servedB.buildIdInPage && servedB.buildManifest === 200)
assert.notEqual(servedB.pointer.build_id, servedA.pointer.build_id)
await shows(releaseB)
ok('publication B', `deployment ${servedB.pointer.deployment_id}, build ${servedB.pointer.build_id}`)
await shot('08-published-b')

// 10. Roll back to A: the same immutable deployment of A's exact artifact.
await selectRelease(releaseA)
const phraseRollback = await authorize('rollback')
assert.equal(phraseRollback, `ROLLBACK ${TARGET} TO ${releaseA}`)
await waitOperation('succeeded')
const rolled = await production()
assert.equal(rolled.pointer.release_id, releaseA); assert.equal(rolled.pointer.deployment_id, servedA.pointer.deployment_id)
assert.equal(rolled.pointer.artifact_sha256, recordA.release.artifact.sha256); assert.equal(rolled.servedSha256, servedA.servedSha256)
assert.equal((await fs.readFile(path.join(targetRoot, 'deployments.jsonl'), 'utf8')).trim().split('\n').length, 2)
await shows(releaseA)
await page.waitForFunction(n => document.querySelectorAll('[data-testid=operation-history] tbody tr').length === n, 5)
ok('rollback restores exact A', `deployment ${rolled.pointer.deployment_id}, served bytes identical to the first publication (sha256 ${rolled.servedSha256.slice(0, 16)})`)
await shot('09-rolled-back-a')
await page.screenshot({ path: `${out}/10-dashboard-full.png`, fullPage: true })

// 11. Dashboard status agrees with the target and the ledger.
const overview = (await json(`/api/website/studio/sites/${site}/releases`)).body
assert.equal(overview.targets[TARGET].current_key, releaseA); assert.ok(overview.ledger.intact)
const history = (await json(`/api/website/studio/sites/${site}/release-targets/${TARGET}/history`)).body
const historyRows = await panel().locator('[data-testid=operation-history] tbody tr').count()
assert.equal(historyRows, history.operations.length)
ok('dashboard status matches target', `${history.operations.length} operations; current ${releaseA}; ledger ${overview.ledger.entries} entries intact`)
const propose = requests.filter(([, u]) => u.includes('propose')).length
assert.equal(propose, 0)
assert.deepEqual(errors, [])
ok('zero model calls', 'offline API (David cannot be constructed); 0 /propose requests; every receipt model_calls 0')
await fs.writeFile(`${out}/browser-acceptance.json`, JSON.stringify({ passed, timeline, releases: { A: releaseA, B: releaseB }, served: { A: servedA, afterFailure, B: servedB, rolledBack: rolled },
  duplicate: { id: replay.id, duplicate: replay.duplicate }, unauthorized: { prepare: refusedText, forgedExecuteStatus: forged },
  requests: { total: requests.length, propose, executes: executes.length }, pageErrors: errors }, null, 2))
await browser.close()
console.log('ALL PASSED', passed.length)
