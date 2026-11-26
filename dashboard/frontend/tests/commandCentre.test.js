import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import {
  hasReceipt,
  jobStatus,
  jobSignals,
  formatJobTime,
} from '../src/jobPresentation.js'
import { shellPathForView, shellRouteFromPath } from '../src/shellState.js'

const receipt = {
  path: 'queue/receipts/AOS-2026-9001.md',
  status: 'done',
  sha256: 'a'.repeat(64),
}
test('a done flag without a matching durable receipt never counts as a verified outcome', () => {
  assert.equal(jobStatus({ status: 'done' }).label, 'Receipt pending')
  assert.equal(
    hasReceipt({
      status: 'done',
      receipts: [{ ...receipt, status: 'failed' }],
    }),
    false,
  )
  assert.equal(
    hasReceipt({ status: 'done', receipts: [{ ...receipt, sha256: '' }] }),
    false,
  )
  assert.deepEqual(
    jobSignals([
      { status: 'done' },
      { status: 'done', receipts: [receipt] },
      { status: 'failed' },
      { status: 'queued' },
      { status: 'uncertain' },
    ]),
    { attention: 2, motion: 1, receipts: 1 },
  )
  assert.equal(jobStatus({ status: 'new_backend_state' }).label, 'Unknown')
  assert.equal(formatJobTime('invalid'), 'Time unavailable')
})
test('permanent job IDs, result filters and memory links survive route round trips', () => {
  const id = 'AOS-2026-900123456789012345678901234567890'
  for (const view of ['work-queue', 'results']) {
    const url = shellPathForView(view, { selectedId: id, filter: 'receipts' })
    const parsed = new URL(url, 'http://test')
    assert.deepEqual(shellRouteFromPath(parsed.pathname, parsed.search), {
      view,
      viewParams: { selectedId: id, filter: 'receipts' },
    })
  }
  assert.equal(shellRouteFromPath('/home').view, 'company-home')
  assert.equal(shellRouteFromPath('/david').view, 'cockpit')
  for (const view of ['graphify', 'repo-ingest', 'memory-intake']) {
    assert.equal(shellRouteFromPath(shellPathForView(view)).view, view)
  }
})
test('Home distinguishes loading, unavailable, known zero and stale data; jobs retain unverified states', async (t) => {
  const vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  t.after(() => vite.close())
  const { default: Home } = await vite.ssrLoadModule(
    '/src/views/CompanyHome.jsx',
  )
  const { default: Jobs } = await vite.ssrLoadModule('/src/views/DavidJobs.jsx')
  const markup = (Component, state) =>
    renderToStaticMarkup(
      React.createElement(Component, { jobsState: state, onNavigate() {} }),
    )
  const base = {
    jobs: [],
    loading: false,
    error: '',
    fetchedAt: null,
    refresh() {},
  }
  assert.match(markup(Home, { ...base, loading: true }), /Reading David jobs/)
  const unavailable = markup(Home, { ...base, error: 'Source unavailable' })
  assert.match(unavailable, /Source unavailable/)
  assert.match(unavailable, /signal-value[^>]*>—</)
  assert.doesNotMatch(unavailable, /You’re clear here/)
  const empty = markup(Home, base)
  assert.match(empty, /signal-value[^>]*>0</)
  assert.match(empty, /You’re clear here/)
  assert.match(
    markup(Home, {
      ...base,
      fetchedAt: '2026-11-15T12:00:00Z',
      error: 'Network failed',
    }),
    /Stale data/,
  )
  const jobs = markup(Jobs, {
    ...base,
    jobs: [{ id: 'AOS-2026-9001', request: 'Fixture request', status: 'done' }],
  })
  assert.match(jobs, /Receipt pending/)
  assert.match(jobs, /No verified receipt/)
  assert.doesNotMatch(jobs, />Completed</)
})
