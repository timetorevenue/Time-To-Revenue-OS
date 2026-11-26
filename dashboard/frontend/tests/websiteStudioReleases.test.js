// Release client calls are deterministic, carry the operator gates, and never reach the model route.
import test from 'node:test'
import assert from 'node:assert/strict'
import { studioClient, RELEASE_CALLS } from '../src/studio/api.js'
import { canConfirm, currentText, isTerminal, statusText, versionsText } from '../src/studio/releases.js'

test('release calls use exact routes and gates; none calls the model', async () => {
  const calls = []
  const client = studioClient(async (url, options = {}) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) } })
  const body = { action: 'publish', target: 'isolated-test' }
  await client.releases('default-site')
  await client.createRelease('default-site', 'a'.repeat(32))
  await client.release('default-site', 'rel_x')
  await client.compareRelease('default-site', 'rel_x', 'isolated-test')
  await client.releaseHistory('default-site', 'isolated-test')
  await client.releaseOperation('default-site', 'op_x')
  await client.prepareRelease('default-site', 'rel_x', body)
  await client.executeRelease('default-site', 'rel_x', { ...body, expected_current: 'none', confirmation_token: 't', command: 'PUBLISH rel_x TO isolated-test' })
  assert.equal(calls.length, RELEASE_CALLS.length)
  assert.deepEqual(calls.map(c => c.url), [
    '/api/website/studio/sites/default-site/releases', '/api/website/studio/sites/default-site/releases',
    '/api/website/studio/sites/default-site/releases/rel_x', '/api/website/studio/sites/default-site/releases/rel_x/compare?target=isolated-test',
    '/api/website/studio/sites/default-site/release-targets/isolated-test/history', '/api/website/studio/sites/default-site/release-operations/op_x',
    '/api/website/studio/sites/default-site/releases/rel_x/prepare', '/api/website/studio/sites/default-site/releases/rel_x/execute'])
  assert.equal(calls[1].options.headers['X-TTROS-Connections'], '1')
  for (const call of calls.slice(6)) {
    assert.equal(call.options.method, 'POST')
    assert.equal(call.options.headers['X-TTROS-Connections'], '1')
    assert.equal(call.options.headers['X-TTROS-Remote-PC'], '1')
  }
  assert.equal(JSON.parse(calls[7].options.body).command, 'PUBLISH rel_x TO isolated-test')
  assert(calls.every(c => !c.url.includes('propose')))
})

test('confirmation needs the exact server phrase and a live confirmation', () => {
  const plan = { allowed: true, noop: false, phrase: 'PUBLISH rel_x TO isolated-test', confirmation: { confirmation_token: 't' } }
  assert.equal(canConfirm(plan, 'PUBLISH rel_x TO isolated-test'), true)
  assert.equal(canConfirm(plan, 'publish rel_x to isolated-test'), false)
  assert.equal(canConfirm({ ...plan, allowed: false }, plan.phrase), false)
  assert.equal(canConfirm({ ...plan, confirmation: undefined }, plan.phrase), false)
  assert.equal(canConfirm({ ...plan, noop: true }, plan.phrase), false)
})

test('status text describes what is published', () => {
  assert.equal(currentText({ current: null }), 'Nothing published yet')
  assert.match(currentText({ current: { release_id: 'rel_a', build_id: 'b1', artifact_sha256: 'abcdef0123456789' } }), /^Release rel_a · build b1 · artifact abcdef012345$/)
  assert.match(currentText({ current: { deployment_id: 'dpl_x', source_commit: '2e521be1ce74' } }), /External deployment dpl_x · source 2e521be1ce74 \(not a Studio release\)/)
  assert.equal(versionsText({ home: { version: 2 }, site: { version: 1 }, 'service-ai': { version: 3 } }), 'Homepage v2 · Shared site v1 · service-ai v3')
  assert.equal(statusText('reverted'), 'Failed after promotion — previous version restored')
  assert.equal(isTerminal({ status: 'promoting' }), false)
  assert.equal(isTerminal({ status: 'noop' }), true)
})
