import { test } from 'node:test'
import assert from 'node:assert/strict'
import { initialDataFor } from '../src/studio/state.js'
import { contrastChecks, contrastFailures, contrastModel } from '../src/studio/contrast.js'

test('all 34 test-offer fields (215679e ServicePage) have AA checks in default, hover and focus states', () => {
  const data = initialDataFor('test-offer')
  const expected = data.content.flatMap(item => Object.keys(item.props).filter(key => /^text\d+$/.test(key)).map(key => `${item.props.id}:${key}`))
  assert.equal(expected.length, 34)
  for (const state of ['default', 'hover', 'focus-visible']) {
    const checks = contrastChecks(data, state)
    assert.deepEqual(checks.map(check => check.id), expected)
    assert.ok(checks.every(check => check.pass))
  }
  assert.equal(contrastChecks(data, 'hover').find(check => check.id === 'TestOffer4:text2').background.hex, '#f0deba')
})

test('every offer field rejects unreadable colours and accepts compliant local styles', () => {
  const base = initialDataFor('test-offer')
  for (const item of base.content) {
    for (const [field, spec] of Object.entries(contrastModel.sections[item.type].fields)) {
      const bad = structuredClone(base)
      bad.content.find(next => next.props.id === item.props.id).props.styles = { [spec.target]: { text: '#ffffff', background: '#ffffff' } }
      assert.ok(contrastFailures(bad).some(check => check.id === `${item.props.id}:${field}`), `${item.props.id}:${field}`)
      bad.content.find(next => next.props.id === item.props.id).props.styles[spec.target].text = '#111315'
      assert.equal(contrastFailures(bad).length, 0)
    }
  }
})

test('an omitted contrast type or text field cannot silently pass', () => {
  const bad = initialDataFor('test-offer')
  bad.content[0].props.text999 = 'Unmodelled text'
  assert.throws(() => contrastChecks(bad), /Missing contrast field/)
  bad.content[0].type = 'MissingType'
  assert.throws(() => contrastChecks(bad), /Missing contrast model/)
})
