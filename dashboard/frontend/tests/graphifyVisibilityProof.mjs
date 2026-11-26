// Revisit: when Graphify coverage UI changes. Last touched: 2026-11-09.
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'
const proofDir = path.resolve('../../workflows/queue_artifacts/graphify_visibility_2026-11-09')
const browser = await chromium.launch({headless: true, executablePath: '/home/operator/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome', args: ['--no-sandbox']})
try {
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}})
  page.setDefaultTimeout(65000)
  const errors = [], mutations = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('request', r => { if (r.method() !== 'GET') mutations.push(r.url()) })
  const loaded = page.waitForResponse(r => r.url().includes('/api/dashboard/graphify'))
  await page.goto('http://127.0.0.1:3010/search', {waitUntil: 'domcontentloaded'})
  await page.getByRole('button', {name: 'Graphify', exact: true}).first().click()
  const response = await loaded
  assert.equal(response.status(), 200)
  const payload = await response.json(), brain = payload.visibility.business_brain
  await page.getByTestId('graphify-system-overview').waitFor()
  await page.screenshot({path: path.join(proofDir, '01-system-overview.png'), fullPage: true})
  await page.getByRole('button', {name: 'Business Brain', exact: true}).click()
  const inventory = page.getByTestId('graphify-brain-inventory')
  assert.equal(await page.getByTestId('graphify-brain-document').count(), Math.min(40, brain.total))
  await inventory.getByRole('button', {name: 'Next', exact: true}).click()
  assert.match(await inventory.innerText(), /Page 2 of/)
  for (const [filter, expected] of [['unlinked', brain.documents.filter(d => !d.connections.length).length], ['unindexed', brain.documents.filter(d => d.search_status === 'unindexed').length]]) {
    await page.getByLabel('Brain coverage filter').selectOption(filter)
    assert.match(await inventory.innerText(), new RegExp(`${expected} of ${brain.total} documents`))
  }
  await page.getByLabel('Brain coverage filter').selectOption('all')
  const target = brain.documents.find(d => d.path === 'business_brain:memory/company.md' && d.connections.some(l => l.path)) || brain.documents.find(d => d.connections.some(l => l.path))
  await page.getByLabel('Search Business Brain inventory').fill(target.path)
  await page.getByTestId('graphify-brain-document').first().click()
  const detail = page.getByTestId('graphify-brain-detail'), neighbour = target.connections.find(l => l.path)
  const title = brain.documents.find(d => d.path === neighbour.path)?.title || neighbour.path
  await detail.getByRole('button', {name: title, exact: true}).first().click()
  assert.equal(await detail.getByRole('link', {name: 'Open in Memory'}).getAttribute('href'), `/memory?record=${encodeURIComponent(neighbour.path)}`)
  await page.screenshot({path: path.join(proofDir, '02-business-brain-connections.png'), fullPage: true})
  const memoryLoaded = page.waitForResponse(r => r.url().includes('/api/memory/document?'))
  await detail.getByRole('link', {name: 'Open in Memory'}).click()
  assert.equal((await memoryLoaded).status(), 200)
  await page.getByText('Technical details', {exact: true}).waitFor()
  await page.screenshot({path: path.join(proofDir, '03-memory-reader.png'), fullPage: true})
  await page.goBack({waitUntil: 'domcontentloaded'})
  await page.getByRole('button', {name: 'Graphify', exact: true}).first().click()
  await page.getByRole('button', {name: 'Code Repositories', exact: true}).click()
  await page.getByTestId('graphify-repository-coverage').getByText('owner-gh-account/TTROS', {exact: true}).waitFor()
  await page.screenshot({path: path.join(proofDir, '04-code-coverage.png'), fullPage: true})
  if (payload.repos.length) {
    await page.getByTestId('graphify-graph-preview').waitFor()
    await page.getByTestId('graphify-tree-preview').waitFor()
    const graph = page.frameLocator('[data-testid="graphify-graph-preview"]')
    await graph.locator('.node circle').last().click()
    await graph.locator('#details:not([hidden])').waitFor()
    await page.frameLocator('[data-testid="graphify-tree-preview"]').locator('body').waitFor()
    await page.getByRole('button', {name: 'Report', exact: true}).click()
    await page.getByText('Loading…', {exact: true}).waitFor({state: 'hidden'})
    await page.screenshot({path: path.join(proofDir, '05-code-repositories.png'), fullPage: true})
  }
  assert.deepEqual(errors, [])
  assert.deepEqual(mutations, [])
  fs.writeFileSync(path.join(proofDir, 'browser-proof.json'), JSON.stringify({passed: true, total: brain.total, searchIndexed: brain.search_indexed, graphDocuments: brain.graph_documents, unlinked: brain.unlinked, repositories: payload.repos.map(r => r.id), checked: ['overview', 'pagination', 'filters', 'search', 'connection navigation', 'Memory reader', 'repository graph selection', 'tree', 'report'], errors, mutations}, null, 2))
} finally { await browser.close() }
