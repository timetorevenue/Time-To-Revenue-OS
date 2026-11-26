// Mount the production shell; every API read/write goes to the combined fixture backend.
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import axios from 'axios'
import { createServer } from 'vite'
import { websiteStatus } from '../src/commandCentreState.js'

// The backend's own virtualenv when present, otherwise the python3 on PATH (set TTROS_TEST_PYTHON to override).
function backendPython() {
  const venv = fileURLToPath(new URL('../../backend/.venv/bin/python', import.meta.url))
  return existsSync(venv) ? venv : 'python3'
}

test('shared website status honours current evidence over historical execution success', () => {
  assert.deepEqual(websiteStatus({ status: 'done', readiness: 'blocked', publish: { status: 'held' } }), { label: 'Blocked · review evidence', tone: 'warning' })
  assert.deepEqual(websiteStatus({ status: 'done', readiness: 'validated', publish: { status: 'held' } }), { label: 'Validated · publish held', tone: 'success' })
})

test('Company Home → CRM → Websites → jobs → won handoff works through the combined shell and real fixture APIs', { timeout: 45000 }, async t => {
  const worker = spawn(process.env.TTROS_TEST_PYTHON || backendPython(), ['-m', 'tests.company_ops_fixture'], {
    cwd: fileURLToPath(new URL('../../../', import.meta.url)), stdio: ['pipe', 'pipe', 'pipe'],
  })
  const pending = []
  let errors = ''
  worker.stderr.on('data', chunk => { errors += chunk })
  worker.on('exit', () => { while (pending.length) pending.shift().reject(new Error(errors || 'Fixture process exited')) })
  createInterface({ input: worker.stdout }).on('line', line => pending.shift()?.resolve(JSON.parse(line)))
  const http = (method, path = '', body) => new Promise((resolve, reject) => {
    pending.push({ resolve, reject })
    worker.stdin.write(JSON.stringify({ method, path, body }) + '\n')
  })
  const meta = (await http('META')).body
  const adapter = axios.defaults.adapter
  const calls = []
  axios.defaults.adapter = async config => {
    const path = `${config.baseURL.replace(/\/$/, '')}${config.url ? '/' + config.url.replace(/^\/+/, '') : ''}`
    assert.match(path, /^\/api\/(health|crm\/|website\/|david\/jobs)/, 'no provider, connector or external API allowed')
    const url = new URL(path, 'http://fixture.invalid')
    for (const [key, value] of Object.entries(config.params || {})) url.searchParams.set(key, value)
    calls.push({ method: config.method, path: url.pathname })
    const result = await http(config.method.toUpperCase(), url.pathname + url.search, typeof config.data === 'string' ? JSON.parse(config.data) : config.data)
    const response = { status: result.status, data: result.body, config, headers: result.headers || {} }
    if (result.status >= 400) throw Object.assign(new Error(result.body.detail), { response })
    return response
  }
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://fixture.invalid/home' })
  const previous = { window: globalThis.window, document: globalThis.document }
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  dom.window.HTMLElement.prototype.scrollTo = () => {}
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
    globalThis.window = previous.window
    globalThis.document = previous.document
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  })
  const { default: App } = await vite.ssrLoadModule('/src/App.jsx')
  const main = () => document.querySelector('main')
  const settle = async predicate => {
    for (let i = 0; i < 200; i++) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
      if (predicate()) return
    }
    assert.fail(`UI did not settle: ${main()?.textContent}; ${errors}`)
  }
  const button = (label, scope = main()) => [...scope.querySelectorAll('button')].find(node => node.textContent.trim() === label)
  const click = async node => { assert.ok(node, 'target exists'); await act(async () => node.click()) }
  const nav = async view => click(document.querySelector(`[data-testid="sidebar"] [data-nav-destination="${view}"]`))
  const form = heading => [...main().querySelectorAll('form')].find(node => node.querySelector('h4')?.textContent === heading)
  const fill = async (node, value) => {
    assert.ok(node, 'field exists')
    await act(async () => {
      const prototype = node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : node.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(node, value)
      node.dispatchEvent(new window.Event('input', { bubbles: true }))
      node.dispatchEvent(new window.Event('change', { bubbles: true }))
    })
  }
  const labelled = text => [...main().querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === text)?.querySelector('input,select,textarea')
  const stage = async name => {
    await fill(form('Change stage').querySelector('select'), name)
    await fill(form('Change stage').querySelector('input'), `Synthetic ${name} reviewed by Casey`)
    await click(button('Change stage'))
    await settle(() => main().querySelector('.detail-title .job-badge')?.textContent.toLowerCase() === name)
  }
  await act(async () => root.render(React.createElement(App)))
  await settle(() => main().textContent.includes('Sales attention') && main().textContent.includes('Draft awaiting review'))
  assert.equal(window.location.pathname, '/home')
  await nav('crm')
  await settle(() => button('New opportunity'))
  await click(button('New opportunity'))
  await fill(form('Create opportunity').querySelector('input'), 'Synthetic combined sale')
  await fill(form('Create opportunity').querySelector('select'), meta.contact)
  await click(button('Create opportunity'))
  await settle(() => main().querySelector('.detail-title')?.textContent.includes('Synthetic combined sale'))
  const opportunityId = main().querySelector('.cc-id').textContent
  assert.equal(new URLSearchParams(window.location.search).get('record'), opportunityId)
  await stage('qualified')
  await stage('proposal')
  await fill(form('Log an interaction').querySelector('textarea'), 'Synthetic local scope discussion')
  await click(button('Save interaction'))
  await settle(() => main().querySelector('.cc-timeline')?.textContent.includes('Synthetic local scope discussion'))
  await fill(form('Next action & follow-up').querySelector('input'), 'Review synthetic website evidence')
  await fill(form('Next action & follow-up').querySelector('input[type="date"]'), '2026-11-15')
  await click(button('Set next action'))
  await settle(() => button('Update next action'))
  await nav('company-home')
  await settle(() => main().textContent.includes('Overdue follow-up'))
  assert.match(main().textContent, /Proposal: 1/)
  await nav('websites')
  await settle(() => button('Prepare work order'))
  await fill(labelled('Registered website'), meta.website.project)
  await fill(labelled('Requested change'), meta.website.request)
  await fill(labelled('Development branch'), meta.website.branch)
  await fill(labelled('Affected pages/assets — one repository path per line'), 'site/index.html')
  await fill(labelled('Acceptance checks — path :: expected text, one per line'), `site/index.html :: ${meta.website.acceptance_checks[0].contains}`)
  await click(button('Prepare work order'))
  await settle(() => button('Queue prepared change'))
  assert.equal((await http('GET', '/api/david/jobs')).body.jobs.length, 0)
  await click(button('Queue prepared change'))
  await settle(() => main().querySelector('.cc-id')?.textContent.includes('AOS-'))
  const jobId = new URLSearchParams(window.location.search).get('job')
  const executed = await http('RUN_FIXTURE', '', { job_id: jobId })
  assert.equal(executed.body.status, 'done')
  await nav('company-home')
  await settle(() => main().textContent.includes('Validated · publish held'))
  await click(main().querySelector('[data-testid="crm-attention"] .cc-attention'))
  await settle(() => form('Link a job, receipt or evidence'))
  await fill(form('Link a job, receipt or evidence').querySelector('input'), jobId)
  await click(button('Add link'))
  await settle(() => [...main().querySelectorAll('.job-list-row')].some(node => node.textContent.includes(jobId)))
  await fill(form('Next action & follow-up').querySelector('input[type="date"]'), '2026-11-18')
  await click(button('Update next action'))
  await settle(() => form('Next action & follow-up').textContent.includes('Next action saved'))
  await stage('won')
  await fill(form('Prepare handoff').querySelectorAll('textarea')[0], 'Synthetic review of a local-only website change')
  await fill(form('Prepare handoff').querySelectorAll('textarea')[1], 'Casey reviews verified build and preview evidence')
  await fill(form('Prepare handoff').querySelector('input'), jobId)
  await click(button('Save handoff for review'))
  await settle(() => main().querySelector('[data-testid="delivery-handoff"]'))
  assert.match(main().querySelector('[data-testid="delivery-handoff"]').textContent, new RegExp(jobId))
  await nav('company-home')
  await settle(() => main().textContent.includes('Won / lost in the last 7 days'))
  assert.doesNotMatch(main().textContent, /Overdue follow-up|Delivery handoff needed/)
  // Open the same website job from Home, with shared readiness and receipt navigation.
  await click([...main().querySelectorAll('.cc-attention')].find(node => node.textContent.includes(meta.website.request)))
  await settle(() => main().querySelector('.cc-record')?.textContent.includes('Readiness: validated'))
  assert.match(main().textContent, /Receipt & artifacts/)
  const previewUrl = main().querySelector('iframe').getAttribute('src')
  const preview = await http('GET', previewUrl)
  assert.equal(preview.status, 200)
  assert.match(preview.body, /Synthetic Company Command Centre website change/)
  assert.match(preview.headers['content-security-policy'], /sandbox/)
  await click(button('Open in Jobs'))
  await settle(() => main().textContent.includes('Receipts & evidence'))
  assert.match(main().textContent, /SHA-256:/)
  assert.equal(window.location.pathname, '/queue')
  assert.match(main().textContent, new RegExp(jobId))
  // Current artifact integrity must override the historical done flag everywhere.
  await http('TAMPER_FIXTURE_PREVIEW', '', { job_id: jobId })
  await nav('company-home')
  await settle(() => main().textContent.includes('Blocked · review evidence'))
  assert.doesNotMatch(main().textContent, /Validated · publish held|Ready for review · publish held/)
  const refused = await http('GET', previewUrl)
  assert.notEqual(refused.status, 200)
  assert.ok(calls.some(call => call.path === '/api/website/preparations' && call.method === 'post'))
  assert.ok(calls.some(call => call.path === '/api/crm/events' && call.method === 'post'))
  assert.ok(calls.some(call => call.path === `/api/david/jobs/${jobId}`))
  assert.ok(!calls.some(call => /ask-david|publish|deploy|connectors/.test(call.path)))
  const sale = (await http('GET', `/api/crm/records/${opportunityId}`)).body
  const finalJob = (await http('GET', `/api/website/jobs/${jobId}`)).body
  t.diagnostic(JSON.stringify({ fixture: true, application: 'actual App shell and FastAPI routes',
    real_provider_calls: 0, real_site_build: false, listener_started: false,
    opportunity_id: opportunityId, stage_history: sale.history.map(row => row.stage),
    handoff_status: sale.handoff.status, handoff_evidence: sale.handoff.sources,
    job_id: jobId, execution_status: finalJob.status, validated_before_tamper: true,
    readiness_after_tamper: finalJob.readiness, preview_status_after_tamper: refused.status,
    receipt: finalJob.receipts[0], publish_status: finalJob.publish.status, released: finalJob.released,
    crm_websites_home_jobs_navigation: 'PASS' }))
})
