// Company Command Centre presentation rules and routes (2026-11-16). Pure functions; no backend.
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEPARTMENTS,
  basisLabel,
  crmEventBody,
  davidPromptForRecord,
  nextStages,
  publishCopy,
  sourceLabel,
  stageLabel,
  websiteStatus,
} from '../src/commandCentreState.js'
import { destinationForView, shellPathForView, shellRouteFromPath } from '../src/shellState.js'

test('all seven departments are present and each opens a real destination or says it is not connected', () => {
  assert.deepEqual(DEPARTMENTS.map(d => d.label), [
    'CRM & Sales', 'Clients & Projects', 'Websites & Digital', 'Marketing & Growth',
    'Finance & Commercial', 'Operations & Agents', 'Research & Intelligence',
  ])
  for (const dept of DEPARTMENTS) {
    if (dept.view) assert.notEqual(destinationForView(dept.view), undefined)
    else assert.match(dept.detail, /No verified .* connected/)
  }
})

test('CRM and website routes survive a reload with their record or job', () => {
  for (const [view, params] of [['crm', { record: 'opp:2026-07-fixture-a', filter: 'open' }], ['websites', { job: 'AOS-2026-10077' }]]) {
    const url = new URL(shellPathForView(view, params), 'http://test')
    assert.deepEqual(shellRouteFromPath(url.pathname, url.search), { view, viewParams: params })
  }
  assert.deepEqual(shellRouteFromPath('/crm', '?record=../../etc'), { view: 'crm', viewParams: {} })
  assert.deepEqual(shellRouteFromPath('/websites', '?job=not-a-job'), { view: 'websites', viewParams: {} })
  assert.equal(destinationForView('crm'), 'crm')
})

test('basis, stage and source labels never present a draft or estimate as a fact', () => {
  assert.equal(basisLabel('draft'), 'Draft — not sent')
  assert.equal(basisLabel('estimate'), 'Estimate')
  assert.equal(basisLabel('anything-else'), 'Unlabelled')
  assert.equal(stageLabel('drafted'), 'Draft awaiting Casey')
  assert.equal(sourceLabel('queue/prospects.jsonl#x'), 'Prospect ledger')
  assert.equal(sourceLabel('casey:dashboard-entry'), "Casey's own entry")
  assert.deepEqual(nextStages({ stage: 'drafted' }), ['sent', 'rejected', 'do_not_contact'])
  assert.deepEqual(nextStages({ stage: 'drafted', ledger_kind: 'outreach_pilot_v1' }), [])
  assert.deepEqual(nextStages({ stage: 'won' }), [])
})

test('a CRM write carries scope, basis, cleaned sources and a retry key', () => {
  const body = crmEventBody({ kind: 'interaction.log', recordId: 'opp:x', payload: { channel: 'call' }, sourceRefs: [' gmail:abc ', ''], requestKey: 'k-123456' })
  assert.deepEqual(body, { client_scope: 'global', kind: 'interaction.log', record_id: 'opp:x', payload: { channel: 'call' },
    basis: 'confirmed', source_refs: ['gmail:abc'], request_key: 'k-123456' })
  assert.match(crmEventBody({ kind: 'link.add', recordId: 'opp:x' }).request_key, /^crm-/)
})

test('Ask David drafts name the exact record and forbid sending', () => {
  const prompt = davidPromptForRecord({ id: 'opp:x', stage: 'drafted', brain_page: 'business_brain:prospects/x.md',
    contact_record: { name: 'Ada Example' }, organization_record: { name: 'Fixture Alpha' } })
  assert.match(prompt, /Ada Example at Fixture Alpha \(business_brain:prospects\/x\.md\)/)
  assert.match(prompt, /do not send anything/)
})

test('website status always shows publishing as a separate, held decision', () => {
  assert.deepEqual(websiteStatus({ status: 'done', publish: { status: 'held' } }), { label: 'Ready for review · publish held', tone: 'success' })
  assert.equal(websiteStatus({ status: 'failed', failure_class: 'tests_failed' }).label, 'Stopped · tests_failed')
  assert.match(publishCopy({ status: 'held' }), /never publishes/)
  assert.match(publishCopy({ status: 'not_ready' }), /no verified, tested preview/)
})
