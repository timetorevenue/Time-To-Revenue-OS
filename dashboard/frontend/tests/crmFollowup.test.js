import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import React, { act, useState } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { JSDOM } from 'jsdom'
import axios from 'axios'
import { createServer } from 'vite'
import { crmToday, todayIso, crmEventBody } from '../src/commandCentreState.js'
import { shellRouteFromPath } from '../src/shellState.js'

// The checkout's own virtualenv when present, otherwise the python3 on PATH (set TTROS_TEST_PYTHON to override).
function checkoutPython() {
  const venv = fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url))
  return existsSync(venv) ? venv : 'python3'
}

test('CRM dates prefer the authoritative business day and directory routes survive reload', () => {
  assert.equal(todayIso(new Date('2025-01-02T01:00:00Z')), '2025-01-01')
  assert.equal(crmToday({ as_of: '2026-11-17' }, new Date('2026-11-17T07:30:00Z')), '2026-11-17')
  for (const filter of ['contacts', 'subscribers', 'organizations']) assert.equal(shellRouteFromPath('/crm', `?filter=${filter}`).viewParams.filter, filter)
  assert.equal(crmEventBody({ recordId: 'opp:x', kind: 'next_action.set', expectedRevision: 'abc' }).expected_revision, 'abc')
})

test('mounted CRM follow-ups and sales pipeline reconcile Home and persist a won delivery handoff', { timeout: 60000 }, async t => {
  const repo = fileURLToPath(new URL('../../../', import.meta.url))
  const worker = spawn(process.env.TTROS_TEST_PYTHON || checkoutPython(), ['-m', 'tests.crm_http_fixture'], { cwd: repo, stdio: ['pipe', 'pipe', 'pipe'] })
  let stderr = '', failure = null
  worker.stderr.on('data', chunk => { stderr += chunk })
  const pending = []
  const failAll = error => { failure = error; while (pending.length) pending.shift().reject(error) }
  createInterface({ input: worker.stdout }).on('line', line => pending.shift()?.resolve(JSON.parse(line)))
  // A missing interpreter emits 'error' and may never emit 'exit': fail the requests instead of waiting forever.
  worker.on('error', error => failAll(new Error(`fixture could not start: ${error.message}`)))
  worker.on('exit', () => failAll(new Error(`fixture exited: ${stderr}`)))
  worker.stdin.on('error', error => failAll(new Error(`fixture input closed (${error.code}): ${stderr}`)))
  const http = (method, path, body, headers) => new Promise((resolve, reject) => {
    if (failure) return reject(failure)
    pending.push({ resolve, reject })
    worker.stdin.write(JSON.stringify({ method, path, body, headers }) + '\n')
  })
  const adapter = axios.defaults.adapter
  axios.defaults.adapter = async config => {
    assert.match(config.url, /^\/crm\//, 'only CRM fixture APIs permitted')
    const result = await http(config.method.toUpperCase(), `/api${config.url}`, typeof config.data === 'string' ? JSON.parse(config.data) : config.data)
    const response = { status: result.status, data: result.body, config, headers: {} }
    if (result.status >= 400) throw Object.assign(new Error(result.body.detail), { response })
    return response
  }
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://fixture.invalid/crm' })
  const previousWindow = globalThis.window, previousDocument = globalThis.document
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true, hmr: false } })
  const root = createRoot(document.getElementById('root'))
  t.after(async () => {
    await act(async () => root.unmount())
    await vite.close()
    worker.stdin.end()
    worker.kill()
    axios.defaults.adapter = adapter
    dom.window.close()
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  })
  const { default: Crm, SourceLink } = await vite.ssrLoadModule('/src/views/CrmSales.jsx')
  const { SalesAttention } = await vite.ssrLoadModule('/src/views/CompanyHome.jsx')
  const { PipelineSummary } = await vite.ssrLoadModule('/src/views/SalesPipeline.jsx')
  let home, navigation
  function Harness() {
    const [params, setParams] = useState({ filter: 'contacts' })
    const [summary, setSummary] = useState(null)
    const navigate = (view, next) => { navigation = { view, next }; setParams(next) }
    home = async () => {
      const result = await http('GET', '/api/crm/home')
      setSummary(result.body)
    }
    return React.createElement(React.Fragment, null,
      React.createElement(Crm, { initialFilters: params, onViewParamsPush: setParams, onNavigate: navigate }),
      React.createElement('aside', null, React.createElement(SalesAttention, { crm: summary, onNavigate: navigate }), React.createElement(PipelineSummary, { summary: summary?.pipeline_summary, onOpen: id => navigate('crm', { record: id }) })))
  }
  // Deadline, not an iteration count: under a parallel `npm test` on a small CI runner 100 x 10 ms was not enough.
  const settle = async predicate => {
    for (const deadline = Date.now() + 8000; Date.now() < deadline;) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
      if (predicate()) return
    }
    assert.fail(`UI did not settle: ${document.body.textContent}; ${stderr}`)
  }
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent.trim() === text)
  const click = async node => { assert.ok(node, 'button exists'); await act(async () => node.click()) }
  const fill = async (node, value) => {
    await act(async () => {
      const prototype = node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : node.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value)
      node.dispatchEvent(new window.Event('input', { bubbles: true }))
      node.dispatchEvent(new window.Event('change', { bubbles: true }))
    })
  }
  await act(async () => root.render(React.createElement(Harness)))
  await settle(() => document.body.textContent.includes('Ada Example'))
  await click([...document.querySelectorAll('.job-list-row')].find(node => node.textContent.includes('Ada Example')))
  await settle(() => document.querySelector('.cc-record')?.textContent.includes('contact:2026-11-fixture-alpha'))
  assert.match(document.body.textContent, /contact:2026-11-fixture-alpha/)
  await click([...document.querySelectorAll('.cc-record .job-list-row')][0])
  await settle(() => button('Save interaction'))
  await fill(document.querySelector('textarea'), 'Synthetic meeting recorded through the mounted form')
  await click(button('Save interaction'))
  await settle(() => document.querySelector('.cc-timeline')?.textContent.includes('Synthetic meeting'))
  const nextForm = [...document.querySelectorAll('form')].find(node => node.textContent.includes('Next action & follow-up'))
  await fill(nextForm.querySelector('input:not([type="date"])'), 'Review synthetic recap')
  await fill(nextForm.querySelector('input[type="date"]'), '2026-11-15')
  await click(button('Set next action'))
  await settle(() => button('Update next action'))
  await act(home)
  assert.match(document.querySelector('aside').textContent, /Overdue follow-up/)
  assert.match(document.querySelector('aside').textContent, /Owner: Casey/)
  // Concurrent edit changes the real fixture event log behind the open form.
  const id = document.querySelector('.cc-id').textContent
  const changed = await http('POST', '/api/crm/events', { kind: 'next_action.set', record_id: id,
    payload: { text: 'Another operator edit', due: '2026-11-18', owner: 'casey' }, request_key: 'ui-concurrent-edit' })
  assert.equal(changed.status, 200)
  await click(button('Update next action'))
  await settle(() => document.body.textContent.includes('This CRM record changed'))
  await click(button('Reload record'))
  await settle(() => nextForm.querySelector('input').value === 'Another operator edit')
  await fill(nextForm.querySelector('input'), 'Updated synthetic recap')
  await click(button('Update next action'))
  await settle(() => nextForm.textContent.includes('Next action saved'))
  await act(home)
  assert.doesNotMatch(document.querySelector('aside').textContent, /Overdue follow-up/)
  assert.match(document.querySelector('aside').textContent, /Upcoming follow-up/)
  const attentionButton = [...document.querySelectorAll('aside .cc-attention')].find(node => node.textContent.includes('Upcoming follow-up'))
  await click(attentionButton)
  assert.deepEqual(navigation, { view: 'crm', next: { record: id } })
  await click(button('Mark done'))
  await settle(() => nextForm.textContent.includes('Follow-up marked done'))
  await act(home)
  assert.doesNotMatch(document.querySelector('aside').textContent, /Upcoming follow-up|Overdue follow-up/)
  assert.match(document.querySelector('aside').textContent, /Missing next action/)
  const persisted = await http('GET', `/api/crm/records/${id}`)
  assert.ok(persisted.body.next_action.completed_at)
  assert.equal(persisted.body.next_action.text, 'Updated synthetic recap')
  assert.equal(persisted.body.stage, 'drafted')
  assert.equal(persisted.body.interactions.length, 1)
  // Phase 2: create from an existing fixture relationship, then operate the actual forms.
  await click(button('Back to CRM'))
  await click(button('Pipeline'))
  await settle(() => document.querySelector('[aria-label="Sales pipeline"]'))
  assert.match(document.querySelector('.cc-pipeline').textContent, /Discovery/)
  await click(button('New opportunity'))
  const formWith = heading => [...document.querySelectorAll('form')].find(node => node.querySelector('h4')?.textContent === heading)
  const create = formWith('Create opportunity')
  await fill(create.querySelector('input'), 'Synthetic Phase 2 sale')
  await fill(create.querySelector('select'), 'contact:2026-11-fixture-alpha-ada-example')
  await click(button('Create opportunity'))
  await settle(() => document.querySelector('.detail-title')?.textContent.includes('Synthetic Phase 2 sale'))
  const salesId = document.querySelector('.cc-id').textContent
  const changeStage = async (stage, reason) => {
    const form = formWith('Change stage')
    await fill(form.querySelector('select'), stage)
    await fill(form.querySelector('input'), reason)
    await click(button('Change stage'))
    await settle(() => document.querySelector('.detail-title .job-badge')?.textContent.toLowerCase() === stage)
  }
  await changeStage('qualified', 'Synthetic need qualified')
  await changeStage('proposal', 'Synthetic proposal ready for review')
  await fill(formWith('Log an interaction').querySelector('textarea'), 'Synthetic acceptance discussion')
  await click(button('Save interaction'))
  await settle(() => document.querySelector('.cc-timeline').textContent.includes('Synthetic acceptance discussion'))
  const salesAction = formWith('Next action & follow-up')
  await fill(salesAction.querySelector('input'), 'Review proposal locally')
  await fill(salesAction.querySelector('input[type="date"]'), '2026-11-15')
  await click(button('Set next action'))
  await settle(() => button('Update next action'))
  await act(home)
  assert.match(document.querySelector('aside').textContent, /Overdue follow-up/)
  const value = formWith('Recorded opportunity value')
  await fill(value.querySelector('input[type="number"]'), '3500')
  await click(button('Save value'))
  await settle(() => value.textContent.includes('Value saved'))
  await fill(salesAction.querySelector('input[type="date"]'), '2026-11-18')
  await click(button('Update next action'))
  await settle(() => salesAction.textContent.includes('Next action saved'))
  await act(home)
  assert.doesNotMatch(document.querySelector('aside').textContent, /Overdue follow-up/)
  await click(button('Back to CRM'))
  await settle(() => document.querySelector('.cc-pipeline')?.textContent.includes('Synthetic Phase 2 sale'))
  const card = [...document.querySelectorAll('.cc-pipeline-card')].find(node => node.textContent.includes('Synthetic Phase 2 sale'))
  assert.match(card.textContent, /CAD 3,500.*Estimate/)
  assert.match(card.textContent, /2026-11-18/)
  await click(card)
  await settle(() => button('Change stage'))
  await changeStage('won', 'Synthetic acceptance recorded by Casey')
  await act(home)
  assert.match(document.querySelector('aside').textContent, /Won \/ lost in the last 7 days/)
  assert.match(document.querySelector('aside').textContent, /Delivery handoff needed/)
  const handoff = formWith('Prepare handoff')
  await fill(handoff.querySelectorAll('textarea')[0], 'Synthetic internal workflow review')
  await fill(handoff.querySelectorAll('textarea')[1], 'Casey reviews the kickoff checklist')
  await click(button('Save handoff for review'))
  await settle(() => document.querySelector('[data-testid="delivery-handoff"]'))
  assert.match(document.querySelector('[data-testid="delivery-handoff"]').textContent, /Fixture Alpha Ltd/)
  const sale = await http('GET', `/api/crm/records/${salesId}`)
  assert.equal(sale.body.handoff.confirmed_scope, 'Synthetic internal workflow review')
  assert.deepEqual(sale.body.history.map(row => row.stage), ['discovery', 'qualified', 'proposal', 'won'])
  await click(button('Reload record'))
  await settle(() => document.querySelector('[data-testid="delivery-handoff"]')?.textContent.includes('Casey reviews the kickoff checklist'))
  await act(home)
  assert.doesNotMatch(document.querySelector('aside').textContent, /Delivery handoff needed/)
  // Same CRM and HTTP fixture: subscriber identity never creates a sales deal.
  const beforeSubscribe = await http('GET', '/api/crm/overview')
  const auth = { 'x-ttros-capture-token': 'ui-fixture-token' }
  const subscribe = await http('POST', '/api/subscribers/request', { name: 'Newsletter Fixture',
    email: 'newsletter@example.invalid', consent: true, notice_version: 'newsletter-2026-11-18', request_id: 'ui-fixture-subscription' }, auth)
  assert.equal(subscribe.status, 200)
  const token = subscribe.body.transport.verification_token
  const unsubscribeToken = subscribe.body.transport.unsubscribe_token
  function SubscriberHarness() {
    const [params, setParams] = useState({ filter: 'subscribers' })
    return React.createElement(Crm, { initialFilters: params, onViewParamsPush: setParams, onNavigate() {} })
  }
  await act(async () => root.render(React.createElement(SubscriberHarness)))
  await settle(() => document.querySelector('.job-list-row')?.textContent.includes('Newsletter Fixture'))
  assert.match(document.body.textContent, /pending.*not eligible for marketing/)
  await click(document.querySelector('.job-list-row'))
  await settle(() => document.querySelector('.detail-meta')?.textContent.includes('Not eligible for marketing'))
  assert.match(document.body.textContent, /Not verified/)
  assert.equal((await http('POST', '/api/subscribers/confirm', { token }, auth)).status, 200)
  await click(button('Reload record'))
  await settle(() => document.querySelector('.detail-meta')?.textContent.includes('Permission confirmed'))
  assert.match(document.body.textContent, /Verified/)
  const afterSubscribe = await http('GET', '/api/crm/overview')
  assert.equal(afterSubscribe.body.counts.opportunities, beforeSubscribe.body.counts.opportunities)
  assert.equal(afterSubscribe.body.counts.subscribers, 1)
  assert.equal((await http('POST', '/api/subscribers/unsubscribe', { token: unsubscribeToken }, auth)).status, 200)
  await click(button('Reload record'))
  await settle(() => document.querySelector('.detail-meta')?.textContent.includes('suppressed'))
  assert.match(document.querySelector('.detail-meta').textContent, /Not eligible for marketing/)
  // Truthful missing/stale/source states and direct Brain/job links.
  const render = (crm, error) => renderToStaticMarkup(React.createElement(SalesAttention, { crm, error, onNavigate() {} }))
  assert.match(render({ counts: { opportunities: 0, attention: 0 }, attention: [] }), /No CRM opportunities connected/)
  const summary = { counts: { opportunities: 1, attention: 1 }, attention: [{ id: 'x', title: 'Synthetic', rule: 'upcoming_follow_up', record_id: id }] }
  assert.match(render(summary), /No source linked/)
  assert.match(render(summary, 'Offline'), /Stale CRM data/)
  assert.match(renderToStaticMarkup(React.createElement(SourceLink, { refValue: 'https://:::', onNavigate() {} })), /https:\/\/:::/)
  await act(async () => root.render(React.createElement(SalesAttention, { crm: { ...summary, attention: [{ ...summary.attention[0], sources: ['business_brain:prospects/fixture.md'], job_id: 'AOS-2026-10001' }] }, onNavigate: (view, next) => { navigation = { view, next } } })))
  await click(document.querySelector('.cc-source'))
  assert.deepEqual(navigation, { view: 'search', next: { tab: 'memory', record: 'business_brain:prospects/fixture.md' } })
  await click(button('Open job AOS-2026-10001'))
  assert.deepEqual(navigation, { view: 'work-queue', next: { selectedId: 'AOS-2026-10001' } })
})
