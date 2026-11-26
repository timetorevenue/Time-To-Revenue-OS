import test from 'node:test'
import assert from 'node:assert/strict'
import { preparationInput } from '../src/websitePreparationState.js'
const form = { project: 'fixture', request: 'Change headline', branch: 'feature/copy', affected: 'site/index.html\n', sources: 'AGENTS.md', checks: 'site/index.html :: Approved :: headline' }
test('preserves exact acceptance text, selected project identity and source paths', () => {
  const body = preparationInput(form, { repository: '/tmp/fixture', client_scope: 'fixture-client' })
  assert.deepEqual(body.acceptance_checks, [{ path: 'site/index.html', contains: 'Approved :: headline' }])
  assert.deepEqual(body.affected_paths, ['site/index.html'])
  assert.equal(body.repository, '/tmp/fixture')
  assert.equal(body.client_scope, 'fixture-client')
})
test('missing project remains missing and cannot fall back to an arbitrary site', () => {
  assert.equal(preparationInput(form).repository, '')
  assert.equal(preparationInput(form).client_scope, '')
})
test('refuses malformed or empty acceptance conditions', () => {
  for (const checks of ['Looks good', 'site/index.html :: ']) assert.throws(() => preparationInput({ ...form, checks }), /expected text/)
})
test('SEO research survives the existing preparation serializer without changing allowed actions', () => {
  const seo_strategy = { version: 'competitive-comparisons-v1', kind: 'alternatives', research: { author: 'Fixture Reviewer' } }
  const body = preparationInput({ ...form, seo_strategy }, { repository: '/tmp/fixture', client_scope: 'global' })
  assert.deepEqual(body.seo_strategy, seo_strategy)
  assert.equal(body.allowed_actions, undefined)
  assert.equal(preparationInput(form).seo_strategy, null)
})
