import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('Dashboard renders David and the retained tools without legacy execution controls', async t => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  t.after(() => vite.close())
  const original = globalThis.window
  globalThis.window = { sessionStorage: { getItem: () => null }, location: { pathname: '/', search: '' } }
  t.after(() => { globalThis.window = original })
  const { default: App } = await vite.ssrLoadModule('/src/App.jsx')
  const markup = renderToStaticMarkup(React.createElement(App))
  assert.match(markup, /data-testid="ask-david"/)
  assert.match(markup, /Capture note/)
  assert.match(markup, /Ingest \/ Add to Memory/)
  assert.match(markup, />Jobs</)
  assert.match(markup, /YOUR COMPANY AT A GLANCE/)
  assert.match(markup, />Graphify</)
  assert.doesNotMatch(markup, /data-nav-tool="(?:workflow-bench|skills-board|tokens-roi|prompt-library)"/)
})

test('a restored queue link renders durable David jobs without old Run or review actions', async t => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  t.after(() => vite.close())
  const original = globalThis.window
  globalThis.window = { sessionStorage: { getItem: () => null }, location: { pathname: '/queue', search: '' } }
  t.after(() => { globalThis.window = original })
  const { default: App } = await vite.ssrLoadModule('/src/App.jsx')
  const markup = renderToStaticMarkup(React.createElement(App))
  assert.match(markup, /Jobs and results/)
  assert.doesNotMatch(markup, />Run<|Close review|Create queue item/)
})

test('the Automations filter shows persisted rules and their next execution', async t => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  t.after(() => vite.close())
  const { default: DavidJobs } = await vite.ssrLoadModule('/src/views/DavidJobs.jsx')
  const ordinary = { id: 'AOS-2026-0110', request: 'Ordinary task', status: 'queued' }
  const rule = {
    id: 'AOS-2026-0111', request: 'Monday workflow', status: 'queued',
    automation: { next_execution: '2026-11-30T16:00:00Z' },
  }
  const markup = renderToStaticMarkup(React.createElement(DavidJobs, {
    initialFilters: { filter: 'automations' }, jobsState: { jobs: [ordinary, rule] },
  }))
  assert.match(markup, /aria-pressed="true">Automations/)
  assert.match(markup, /Monday workflow/)
  assert.match(markup, /Next run/)
  assert.doesNotMatch(markup, /Ordinary task/)
})
