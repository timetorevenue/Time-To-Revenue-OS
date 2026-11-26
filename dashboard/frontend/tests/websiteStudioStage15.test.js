// Stage 1.5: deterministic-first Studio client, brand checks and Dashboard deep links.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { studioClient, ROUTINE_CALLS } from '../src/studio/api.js'
import { tokenViolations, proposalViolations, designViolations } from '../src/studio/brand.js'
import { initialData, applyProposal } from '../src/studio/state.js'
import { shellRouteFromPath, shellPathForView } from '../src/shellState.js'

const rules = {
  colours: ['#111315', '#0D1418', '#2B2F32', '#F7F3EA', '#D8D0C2', '#ECE9E2', '#B89B63', '#C6A15B', '#7A746A', '#4E5A50', '#52616B', '#A56C53', '#FFFFFF', '#B9B7B0'],
  fonts: ['Geist', 'Inter'], ranges: { h1Size: [64, 88], h2Size: [40, 56], h3Size: [24, 32], bodySize: [16, 18], buttonRadius: [6, 10], sectionSpacing: [96, 160] },
  no_fill: { colours: ['#B89B63', '#C6A15B'], tokens: ['background', 'secondary'], reason: 'Gold is a signal detail.' },
  not_core: { colours: ['#A56C53'], tokens: ['primary', 'accent', 'background', 'text'], reason: 'Clay only for friction.' },
  exceptions: [{ token: 'muted', value: '#57564f' }],
}

function recorder(status = 200, body = {}) {
  const urls = []
  const fetcher = async (url, options = {}) => { urls.push([options.method || 'GET', url]); return { ok: status < 400, status, json: async () => body } }
  return { urls, client: studioClient(fetcher) }
}

test('every routine Studio call avoids the model route, including failures and retries', async () => {
  for (const status of [200, 404, 409, 422, 500]) {
    const { urls, client } = recorder(status, status < 400 ? { latest: 0 } : { detail: 'failed' })
    const args = { sites: [], sources: ['default-site'], refresh: ['default-site'], driveFolder: ['default-site', 'x'], brand: ['default-site'],
      approveBrand: ['default-site', 'f'], withdrawBrand: ['default-site'], preview: ['default-site', initialData, { edits: [] }], drafts: ['default-site'],
      draft: ['default-site', 1], saveDraft: ['default-site', { data: initialData, base_version: 0 }] }
    for (const name of ROUTINE_CALLS) {
      for (let attempt = 0; attempt < 2; attempt++) { try { await client[name](...args[name]) } catch { /* error path stays deterministic */ } }
    }
    assert.equal(urls.length, ROUTINE_CALLS.length * 2)
    assert.ok(urls.every(([, url]) => !url.includes('propose')), `status ${status}`)
  }
  const { urls, client } = recorder(200, { proposal: {} })
  await client.requestProposal({ project: 'default-site' })
  assert.deepEqual(urls, [['POST', '/api/website/studio/propose']])
})

test('old backend without Stage 1.5 routes reports the missing capability, not a guess', async () => {
  const { client } = recorder(404, {})
  await assert.rejects(client.sources('default-site'), /restart required/)
})

test('UI sources call the model only from the explicit Propose changes button', () => {
  const studio = readFileSync(new URL('../src/views/WebsiteStudio.jsx', import.meta.url), 'utf8')
  assert.equal(studio.match(/requestProposal\(/g).length, 1)
  assert.equal(studio.match(/(?<!function )\bpropose\(\)/g)?.length ?? 0, 0, 'propose() must not be called programmatically')
  assert.equal(studio.match(/onClick=\{propose\}/g).length, 1)
  const body = studio.slice(studio.indexOf('async function propose()'), studio.indexOf('function move('))
  assert.match(body, /studioApi\.requestProposal/)
  assert.ok(!/useEffect\([^)]*propose/.test(studio))
  for (const file of ['../src/views/WebsiteSources.jsx', '../src/views/Websites.jsx', '../src/views/WebsitePreparation.jsx', '../src/studio/brand.js', '../src/studio/state.js', '../src/studio/design.js', '../src/studio/config.jsx', '../src/studio/fields.jsx']) {
    const text = readFileSync(new URL(file, import.meta.url), 'utf8')
    assert.ok(!/requestProposal|studio\/propose/.test(text), file)
  }
  const api = readFileSync(new URL('../src/studio/api.js', import.meta.url), 'utf8')
  assert.equal(api.match(/studio\/propose/g).length, 1)
})

test('brand checks flag off-brand values, honour exceptions and approve compliant changes', () => {
  assert.deepEqual(tokenViolations({ accent: '#c6a15b', buttonRadius: 8, headingFont: 'Geist', muted: '#57564f' }, rules), [])
  const found = tokenViolations({ accent: '#7c3aed', buttonRadius: 3, headingFont: 'Georgia', background: '#B89B63', primary: '#A56C53', h1Size: 106 }, rules).map(v => v.token)
  assert.deepEqual(found.sort(), ['accent', 'background', 'buttonRadius', 'h1Size', 'headingFont', 'primary'])
  assert.deepEqual(proposalViolations({ styles: [{ section: 'Section0', target: 'a-0', values: { radius: 30 } }, { section: 'Section0', target: 'h1-0', values: { fontSize: 100 } }] }, rules).map(v => v.token), ['Section0.a-0.buttonRadius', 'Section0.h1-0.h1Size'])
  assert.deepEqual(proposalViolations({ design: { accent: '#7c3aed' } }, null), [])
})

test('owner overrides stay visible: the real site defaults list every deviation from the brand', () => {
  const tokens = designViolations(initialData, rules).map(v => v.token).sort()
  assert.deepEqual(tokens, ['borderColor', 'buttonRadius', 'h1Size', 'h2Size', 'secondary'])
  const branded = applyProposal(initialData, { edits: [], design: { secondary: '#ece9e2', borderColor: '#d8d0c2', h1Size: 88, h2Size: 56, buttonRadius: 6 } })
  assert.deepEqual(designViolations(branded, rules), [])
})

test('Dashboard deep link opens Website Studio for the registered site', () => {
  const route = shellRouteFromPath('/websites', '?tab=studio&site=default-site')
  assert.deepEqual(route, { view: 'websites', viewParams: { tab: 'studio', site: 'default-site' } })
  assert.equal(shellPathForView('websites', route.viewParams), '/websites?tab=studio&site=default-site')
  assert.deepEqual(shellRouteFromPath('/websites', '?tab=studio&site=../../etc').viewParams, { tab: 'studio' })
  assert.deepEqual(shellRouteFromPath('/websites', '?tab=evil').viewParams, {})
  assert.equal(shellPathForView('websites', { job: 'AOS-2026-10001', tab: 'studio' }), '/websites?job=AOS-2026-10001')
})
