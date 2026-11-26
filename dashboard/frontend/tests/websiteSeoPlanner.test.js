import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import axios from 'axios'
import { createServer } from 'vite'

test('SEO selection fills existing preparation; project changes reset evidence; planning never queues', async t => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://fixture.invalid/websites' })
  const before = { window: globalThis.window, document: globalThis.document }
  globalThis.window = dom.window; globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const adapter = axios.defaults.adapter, calls = []
  axios.defaults.adapter = async config => {
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
    calls.push({ method: config.method, path: config.url, body })
    assert.equal(config.baseURL, '/api/website/preparations')
    let data
    if (config.method === 'get') data = { preparations: [] }
    else if (config.url === '/seo-plan') data = { pages: [{ kind: 'alternatives', title: 'Example alternatives', h1: 'Example alternatives', slug: 'example-alternatives', priority: 1, research_gaps: [], readiness: 'editorial_review' }] }
    else { assert.equal(config.url, ''); data = { state: 'prepared' } }
    return { status: 200, data, config, headers: {} }
  }
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true, hmr: false } })
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => {
    await act(async () => root.unmount()); await vite.close(); axios.defaults.adapter = adapter
    dom.window.close(); globalThis.window = before.window; globalThis.document = before.document
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  })
  const { default: Preparation } = await vite.ssrLoadModule('/src/views/WebsitePreparation.jsx')
  const projects = [{ id: 'fixture-a', repository: '/tmp/a', client_scope: 'client-a', icm_files: ['AGENTS.md'] }, { id: 'fixture-b', repository: '/tmp/b', client_scope: 'client-b', icm_files: ['BRAND.md'] }]
  await act(async () => root.render(React.createElement(Preparation, { projects, onQueued() { assert.fail('No queue submission') } })))
  const field = label => [...document.querySelectorAll('label')].find(e => e.textContent.startsWith(label))?.querySelector('input,textarea,select')
  const fill = async (label, value) => {
    const node = field(label); assert.ok(node, label)
    await act(async () => {
      const proto = node.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value)
      node.dispatchEvent(new window.Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
    })
  }
  const click = async label => { const b = [...document.querySelectorAll('button')].find(e => e.textContent === label); assert.ok(b, label); await act(async () => b.click()) }
  await fill('Registered website', 'fixture-a')
  assert.equal(field('Call to action URL').value, '')
  await fill('Business name', 'Fixture A'); await fill('Competitors', 'Example')
  await fill('Real author', 'Fixture Reviewer')
  await click('Generate SEO page briefs')
  assert.equal(calls.filter(c => c.method === 'post').length, 1)
  await fill('Exact repository target', 'site/index.html')
  await click('Use in website work order')
  assert.match(field('Requested change').value, /Example alternatives/)
  assert.equal(field('Development branch').value, 'feature/seo-example-alternatives')
  await click('Prepare work order')
  const saved = calls.find(c => c.method === 'post' && c.path === '')
  assert.equal(saved.body.seo_strategy.research.author, 'Fixture Reviewer')
  assert.equal(saved.body.seo_strategy.research.client_scope, 'client-a')
  assert.deepEqual(saved.body.acceptance_checks, [{ path: 'site/index.html', contains: 'Example alternatives' }])
  assert.ok(document.body.textContent.includes('Clear SEO selection'))
  await fill('Registered website', 'fixture-b')
  assert.equal(field('Real author').value, '')
  assert.ok(!document.body.textContent.includes('Clear SEO selection'))
  await click('Save draft')
  const last = calls.filter(c => c.method === 'post' && c.path === '').at(-1)
  assert.equal(last.body.seo_strategy, null); assert.equal(last.body.client_scope, 'client-b')
  assert.ok(!calls.some(c => c.path?.includes('/queue')))
})
