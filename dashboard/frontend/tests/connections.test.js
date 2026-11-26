import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'
import { connectionRequest, verifyConnectionReturn } from '../src/connectionsApi.js'

// Isolated UI transport doubles. These are regression tests, not provider proof.
async function mount(t, transport, hubState = () => ({ connections: [], receipts: [] }), apps = [{ slug: 'gmail', name: 'Gmail' }], pageUrl = 'http://127.0.0.1:4390/settings/connections') {
  const dom = new JSDOM('<div id="root"></div>', { url: pageUrl })
  const previous = { window: globalThis.window, document: globalThis.document, location: globalThis.location, fetch: globalThis.fetch }
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.location = dom.window.location
  const catalogueRequests = []
  globalThis.fetch = (path, options) => path === '/api/account-connections/hub/refresh' ? Promise.resolve(json({ connections: [] })) : path === '/api/account-connections/hub/state' ? Promise.resolve(json(hubState())) : path.startsWith('/api/account-connections/hub/catalogue') ? (catalogueRequests.push(path), Promise.resolve(json(typeof apps === 'function' ? apps(path) : { items: apps, source: 'Test catalogue', checked_at: 1 }))) : transport(path, options); globalThis.IS_REACT_ACT_ENVIRONMENT = true
  dom.window.HTMLElement.prototype.scrollIntoView = () => {}
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true }
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false }
  const vite = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), appType: 'custom', logLevel: 'silent', server: { middlewareMode: true, hmr: false } })
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  const navigation = []
  t.after(async () => {
    await act(async () => root.unmount()); await vite.close(); dom.window.close()
    Object.assign(globalThis, previous); delete globalThis.IS_REACT_ACT_ENVIRONMENT
  })
  const { default: Connections } = await vite.ssrLoadModule('/src/views/Connections.jsx')
  await act(async () => root.render(React.createElement(Connections, { onNavigate: value => navigation.push(value) })))
  await act(async () => new Promise(resolve => setTimeout(resolve, 10)))
  return { navigation, catalogueRequests, click: async selector => {
    const button = document.querySelector(selector); assert.ok(button, selector); assert.equal(button.disabled, false)
    await act(async () => button.click())
  } }
}
const clickText = async (label, scope = document) => {
  const button = [...scope.querySelectorAll('button')].find(node => node.textContent.trim() === label)
  assert.ok(button, label)
  await act(async () => button.click())
}
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const session = { identity: { method: 'local_preview', workspace: 'local-preview', user: 'local-operator' }, google_configured: false,
  google_setup: { callback_url: 'http://127.0.0.1:4390/api/auth/google/callback', missing_configuration: ['TTROS_GOOGLE_CLIENT_ID', 'TTROS_GOOGLE_CLIENT_SECRET'] } }
const providers = ['vercel', 'github', 'googledrive', 'gmail'].map(id => ({ id, label: id, status: 'NOT_CONNECTED', configured: false, granted: [], permissions: ['read'], setup: `${id} setup required`, configuration: { missing_configuration: [`providers.${id}.auth_config_id`], identity_blocker: 'Sign in first' } }))
const config = { providers, stripe: { configured: false } }

test('retained scoped provider controls give truthful setup, status and disconnect feedback', async t => {
  const calls = []
  const ui = await mount(t, async (path, options = {}) => {
    calls.push([options.method || 'GET', path])
    if (path === '/api/auth/session') return json(session)
    if (path === '/api/account-connections') return json(config)
    if (path === '/api/auth/google/start') return json({ detail: { message: 'Missing TTROS_GOOGLE_CLIENT_ID and TTROS_GOOGLE_CLIENT_SECRET' } }, 503)
    if (path.endsWith('/status')) return json({ status: 'NOT_CONNECTED', message: 'No account linked. Connect after setup.', checked_at: '2026-11-19T12:00:00Z', provider_checked: false, status_source: 'Local binding; provider not contacted', missing_configuration: ['COMPOSIO_API_KEY'] })
    if (path.endsWith('/disconnect')) return json({ status: 'NOT_CONNECTED', changed: false, message: 'Nothing was disconnected. Existing accounts unchanged.' })
    if (path === '/api/account-connections/audit') return json({ receipts: [] })
    return json({ detail: { message: 'Provider authorization unavailable. Complete setup first.' } }, 403)
  })
  assert.equal(document.querySelectorAll('.connections-page button:disabled').length, 0)
  await clickText('Sign-in settings')
  await ui.click('[data-action="sign-in"]')
  assert.match(document.querySelector('[role=alert]').textContent, /TTROS_GOOGLE_CLIENT_ID/)
  await ui.click('[data-provider="google"] [data-action="setup"]')
  assert.match(document.querySelector('#connections-setup').textContent, /4390\/api\/auth\/google\/callback/)
  assert.equal(document.activeElement.id, 'connections-setup')
  for (const provider of providers) {
    for (const action of ['connect', 'reconnect', 'status', 'disconnect', 'setup']) {
      await ui.click(`[data-provider="${provider.id}"] [data-action="${action}"]`)
      if (action === 'setup') assert.match(document.querySelector('#connections-setup').textContent, new RegExp(provider.id))
      else {
        assert.ok(calls.some(([method, path]) => method === 'POST' && path === `/api/account-connections/${provider.id}/${action}`))
        const feedback = document.querySelector(`[data-provider="${provider.id}"] .connections-feedback`).textContent
        assert.match(feedback, action === 'status' ? /provider not contacted/ : action === 'disconnect' ? /Nothing was disconnected/ : /Complete setup/)
        assert.ok(document.activeElement.classList.contains('connections-feedback'), 'completed feedback is focused after pending/refresh finishes')
      }
    }
  }
  await ui.click('[data-provider="vercel"] [data-action="verify"]')
  assert.match(document.querySelector('[role=alert]').textContent, /Complete setup/)
  await ui.click('[data-provider="stripe"] [data-action="verify"]')
  assert.match(document.querySelector('[role=alert]').textContent, /Complete setup/)
  await ui.click('[data-provider="stripe"] [data-action="setup"]')
  assert.match(document.querySelector('#connections-setup').textContent, /TTROS_STRIPE_TEST_KEY/)
  await ui.click('[data-action="refresh"]')
  assert.match(document.querySelector('.connections-feedback').textContent, /0 of 4/)
  await ui.click('[data-action="audit"]')
  assert.match(document.querySelector('.connections-feedback').textContent, /No audit receipts/)
  await ui.click('[data-action="launch"]')
  assert.deepEqual(ui.navigation, ['websites'])
})

test('backend outage, malformed response and pending requests stay visible and retry recovers', async t => {
  let mode = 'offline', release
  const ui = await mount(t, async path => {
    if (mode === 'offline') throw new TypeError('Test transport offline')
    if (mode === 'malformed') return new Response('bad gateway', { status: 502 })
    if (path === '/api/auth/session') return json(session)
    if (path === '/api/account-connections') return json(config)
    if (mode === 'pending') return await new Promise(resolve => { release = () => resolve(json({ status: 'NOT_CONNECTED', message: 'No account linked' })) })
    return json({ receipts: [] })
  })
  assert.match(document.querySelector('[role=alert]').textContent, /Cannot reach/)
  mode = 'normal'; await clickText('Retry')
  assert.match(document.querySelector('.connections-feedback').textContent, /Configuration checked/)
  mode = 'malformed'; await ui.click('[data-action="audit"]')
  assert.match(document.querySelector('[role=alert]').textContent, /HTTP 502 without a readable response/)
  mode = 'pending'; await ui.click('[data-provider="vercel"] [data-action="status"]')
  assert.match(document.querySelector('[data-provider="vercel"] [role=status]').textContent, /Contacting the TTROS backend/)
  assert.ok(document.querySelector('[data-action="status"]').disabled)
  mode = 'normal'; await act(async () => release())
  assert.equal(document.querySelector('[data-action="status"]').disabled, false)
  assert.match(document.querySelector('[data-provider="vercel"] .connections-feedback').textContent, /No account linked/)
  assert.ok(document.activeElement.classList.contains('connections-feedback'))
})

test('request helper preserves actionable structured errors and handles timeouts', async t => {
  const original = globalThis.fetch; t.after(() => { globalThis.fetch = original })
  globalThis.fetch = async () => json({ detail: { message: 'Reauthorize the expired account', setup: 'vercel' } }, 403)
  await assert.rejects(connectionRequest('/api/account-connections/vercel/status', {}), error => error.status === 403 && error.detail.setup === 'vercel' && /Reauthorize/.test(error.message))
  globalThis.fetch = async () => { throw new DOMException('aborted', 'AbortError') }
  await assert.rejects(connectionRequest('/api/auth/session'), /timed out/)
})

test('conditional existing-user linking and sign-out buttons issue their real route contracts', async t => {
  let identity = { method: 'tailscale_serve', user: 'operator-unit', workspace: 'ttros-operator', email: 'unit@example.invalid' }
  const calls = []
  const ui = await mount(t, async (path, options = {}) => {
    calls.push([options.method || 'GET', path])
    if (path === '/api/auth/session') return json({ ...session, identity })
    if (path === '/api/account-connections') return json(config)
    if (path === '/api/auth/google/start?link=true') return json({ detail: 'Configure Google OIDC before linking' }, 503)
    if (path === '/api/auth/logout') { identity = null; return json({ signed_out: true }) }
    assert.fail(`Unexpected test request: ${path}`)
  })
  await clickText('Sign-in settings')
  await ui.click('[data-action="link"]')
  assert.match(document.querySelector('[role=alert]').textContent, /Configure Google OIDC/)
  assert.ok(calls.some(([method, path]) => method === 'POST' && path === '/api/auth/google/start?link=true'))
  identity = { ...identity, method: 'google_oidc' }
  await ui.click('[data-action="refresh"]')
  await ui.click('[data-action="logout"]')
  assert.match(document.querySelector('[data-provider="google"]').textContent, /Sign in to manage app connections/)
  assert.match(document.querySelector('.connections-feedback').textContent, /Provider accounts were preserved/)
  assert.equal(document.querySelectorAll('[data-provider="vercel"]').length, 0)
})

const changeInput = async (selector, value) => {
  const input = document.querySelector(selector); assert.ok(input, selector)
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  await act(async () => { setter.call(input, value); input.dispatchEvent(new window.Event('input', { bubbles: true })) })
}
const base = '/api/account-connections/hub'
const quiet = path => path === '/api/auth/session' ? json(session) : path === '/api/account-connections' ? json(config) : null

test('a workspace links its own Composio project; the key is write-only and cleared', async t => {
  const posts = []
  await mount(t, async (path, options = {}) => {
    const known = quiet(path); if (known) return known
    if (path === base + '/project') { posts.push(JSON.parse(options.body)); return json({ mode: 'customer', status: 'LINKED', key_fingerprint: 'abc123', owner: true, composio_user_id: 'casey-in-acme', accounts_found: 2 }) }
    return json({}, 404)
  })
  assert.match(document.querySelector('[aria-label="Composio project"]').textContent, /Link an existing Composio project/)
  assert.equal(document.querySelector('[aria-label="Link an existing Composio project"] input[type=password]').autocomplete, 'off')
  await changeInput('[aria-label="Link an existing Composio project"] input[type=password]', 'ak_customer_project_key_0123456789')
  await changeInput('[aria-label="Link an existing Composio project"] input:not([type=password])', 'casey-in-acme')
  const form = document.querySelector('[aria-label="Link an existing Composio project"]')
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  assert.deepEqual(posts, [{ mode: 'customer', api_key: 'ak_customer_project_key_0123456789', composio_user_id: 'casey-in-acme' }])
  assert.equal(document.querySelector('[aria-label="Link an existing Composio project"] input[type=password]').value, '')
  assert.doesNotMatch(document.body.innerHTML, /ak_customer_project_key/)
})

test('a linked project requires explicit exact-account selection and keeps hosted sign-in available', async t => {
  const calls = []
  const state = { connections: [], receipts: [],
    project: { mode: 'managed', status: 'LINKED', key_fingerprint: 'managed', owner: true, composio_user_id: 'ttros_abc' },
    project_accounts: [{ service: 'gmail', account: 'ca_alice1', status: 'ACTIVE', identity: 'Alice Gmail' }] }
  const ui = await mount(t, async (path, options = {}) => {
    const known = quiet(path); if (known) return known
    calls.push([options.method || 'GET', path, options.body ? JSON.parse(options.body) : null])
    if (path === base + '/apps/gmail') return json({ name: 'Gmail', schemes: [{ mode: 'OAUTH2', oauth: true }] })
    if (path === base + '/project/bind') return json({ id: 'cx_1', status: 'ACTIVE' })
    if (path === base + '/connect') return json({ id: 'cx_2', url: 'https://connect.composio.dev/link/lt_x', status: 'PENDING' })
    if (path === base + '/project') return json({ status: 'UNLINKED' })
    return json({}, 404)
  }, () => state)
  await ui.click('[aria-label="Connect Gmail"]')
  const modal = document.querySelector('dialog')
  assert.deepEqual(calls, [['GET', base + '/apps/gmail', null]])
  await clickText('Use existing account', modal)
  await act(async () => {
    const select = modal.querySelector('select')
    select.value = 'ca_alice1'
    select.dispatchEvent(new window.Event('change', { bubbles: true }))
  })
  await clickText('Use selected account', modal)
  assert.deepEqual(calls.at(-1), ['POST', base + '/project/bind', { account_id: 'ca_alice1', service: 'gmail' }])
  await clickText('Done', modal)
  await ui.click('[aria-label="Connect Gmail"]')
  await clickText('Continue', document.querySelector('dialog'))
  assert.deepEqual(calls.at(-1), ['POST', base + '/connect', { service: 'gmail', scheme: 'OAUTH2', fields: {} }])
  assert.equal(document.querySelector('a[href="https://connect.composio.dev/link/lt_x"]').textContent, 'Continue to provider')
  await ui.click('[data-project-action="unlink"]')
  assert.deepEqual(calls.at(-1), ['DELETE', base + '/project', {}])
})

test('ordinary customer browses apps and connects without project or OAuth setup', async t => {
  const posts = []
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice', email: 'alice@example.invalid' }, google_configured: true }
  let state = { connections: [], receipts: [] }
  const ui = await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') return json(customer)
    assert.notEqual(path, '/api/account-connections', 'ordinary customer never loads legacy operator setup')
    if (path === base + '/apps/gmail') return json({ name: 'Gmail', no_auth: true, schemes: [] })
    if (path === base + '/connect') {
      posts.push(JSON.parse(options.body))
      state = { ...state, project: { key_fingerprint: 'managed', status: 'LINKED' }, connections: [{ id: 'cx_a', service: 'gmail', name: 'Gmail', kind: 'composio', status: 'ACTIVE', enabled: true, operations: [], permissions: [], config: { source: 'project', project: 'managed' }, access: { mode: 'Automatic' } }] }
      return json({ id: 'cx_a', status: 'ACTIVE', message: 'Connected.' })
    }
    assert.fail(`Unexpected request ${path}`)
  }, () => state)
  assert.ok(document.querySelector('[aria-label="Search apps"]'))
  await changeInput('[aria-label="Search apps"]', 'mail')
  await act(async () => new Promise(resolve => setTimeout(resolve, 240)))
  assert.equal(ui.catalogueRequests.at(-1), base + '/catalogue?q=mail')
  assert.ok(document.querySelector('.app-tile img').src.endsWith('/gmail'))
  assert.match(document.querySelector('.app-tile').textContent, /Gmail.*Available to connect/)
  const advanced = document.querySelector('.connections-advanced')
  assert.equal(advanced.open, false)
  assert.match(advanced.querySelector('summary').textContent, /Advanced settings/)
  assert.equal(document.querySelectorAll('[data-project-action="managed"]').length, 0)
  assert.equal(document.querySelectorAll('[data-provider="google"] [data-action="setup"]').length, 0)
  assert.equal(document.querySelectorAll('[data-provider="stripe"]').length, 0)
  await act(async () => [...document.querySelectorAll('.app-tile button')].find(b => b.getAttribute('aria-label') === 'Connect Gmail').click())
  assert.deepEqual(posts, [])
  await clickText('Connect securely', document.querySelector('dialog'))
  assert.deepEqual(posts, [{ service: 'gmail', fields: {} }])
  await clickText('Done', document.querySelector('dialog'))
  assert.match(document.querySelector('.app-tile').textContent, /Connected/)
  assert.ok(document.querySelector('.app-tile button.connection-remove'))
  await ui.click('nav[aria-label="Connection sections"] button:nth-child(2)')
  assert.match(document.querySelector('[data-connection="cx_a"]').textContent, /Connected/)
})

test('Forge panel saves exact version and named operation with UI controls', async t => {
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' }, google_configured: true }
  let row = { id: 'cx_a', service: 'github', name: 'GitHub', kind: 'composio', status: 'ACTIVE', enabled: true,
    operations: ['profile'], permissions: ['profile:read'], config: {}, access: { mode: 'Automatic', agents: {} } }
  const saved = []
  const agent = { ref: 'shared.github_profile@0.1.0', grants: ['connector.read:github:profile'],
    connections: { resolved: { 'connector.read:github:profile': [{ connection: 'cx_a', operations: ['profile'] }] }, missing_required: [], missing_reasons: {} } }
  const missing = { ref: 'shared.github_profile@0.2.0', grants: ['connector.read:github:source'],
    connections: { resolved: { 'connector.read:github:source': [] }, missing_required: ['connector.read:github:source'],
      missing_reasons: { 'connector.read:github:source': 'The named operation is not supported or enabled on this account.' } } }
  const ui = await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') return json(customer)
    if (path === base + '/agents') return json({ agents: [agent, missing] })
    if (path === base + '/connections/cx_a/access') {
      const value = JSON.parse(options.body); saved.push(value); row = { ...row, access: value }; return json(row)
    }
    assert.fail(`Unexpected request ${path}`)
  }, () => ({ connections: [row], receipts: [] }))
  await clickText('Agent Forge permissions')
  assert.match(document.querySelector('[aria-label="Agent Forge Connections and Permissions"]').textContent, /connector.read:github:profile/)
  const selected = document.querySelector('[aria-label="Selected agent version"]')
  await act(async () => { selected.value = missing.ref; selected.dispatchEvent(new window.Event('change', { bubbles: true })) })
  assert.match(document.querySelector('[aria-label="Agent Forge Connections and Permissions"]').textContent, /named operation is not supported/)
  await act(async () => { selected.value = agent.ref; selected.dispatchEvent(new window.Event('change', { bubbles: true })) })
  const mode = document.querySelector('[aria-label="Access mode · GitHub"]')
  await act(async () => { mode.value = 'Restricted'; mode.dispatchEvent(new window.Event('change', { bubbles: true })) })
  const version = document.querySelector('[aria-label="Add agent version · GitHub"]')
  await act(async () => { version.value = agent.ref; version.dispatchEvent(new window.Event('change', { bubbles: true })) })
  const checkbox = document.querySelector('[aria-label="shared.github_profile@0.1.0 · profile · GitHub"]')
  assert.ok(checkbox)
  await act(async () => checkbox.click())
  await act(async () => [...document.querySelectorAll('[data-agent-account="cx_a"] button')].find(button => button.textContent === 'Save access').click())
  assert.deepEqual(saved, [{ mode: 'Restricted', agents: { [agent.ref]: ['profile'] } }])
  assert.equal(document.querySelector('[aria-label="Access mode · GitHub"]').value, 'Restricted')
  assert.equal(document.querySelector('[aria-label="shared.github_profile@0.1.0 · profile · GitHub"]').checked, true)
})

test('signed-out customer sees Google sign-in without owner setup requirements', async t => {
  const ui = await mount(t, async path => {
    if (path === '/api/auth/session') return json({ ...session, identity: null })
    if (path === '/api/auth/google/start') return json({ detail: { message: 'Missing TTROS_GOOGLE_CLIENT_SECRET' } }, 503)
    assert.fail(`Unexpected request ${path}`)
  })
  assert.equal(document.querySelectorAll('[aria-label="Connections hub"]').length, 0)
  assert.equal(document.querySelectorAll('[data-action="setup"]').length, 0)
  await ui.click('[data-action="sign-in"]')
  assert.match(document.querySelector('[role=alert]').textContent, /temporarily unavailable/)
  assert.doesNotMatch(document.querySelector('[role=alert]').textContent, /CLIENT_SECRET|OAuth|configure/)
})

test('success return verifies only saved attempts and offers retry without new authorization', async t => {
  const calls = []
  let verified = false
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' }, google_configured: true }
  const row = { id: 'cx_saved', service: 'github', name: 'GitHub', account: 'ca_saved', status: 'PENDING', enabled: true, kind: 'composio',
    permissions: ['profile'], operations: ['profile'], access: { mode: 'Automatic' },
    config: { source: 'project', project: 'managed', issued_account_id: 'ca_saved', started_at: 1 } }
  const ui = await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') {
      // App replaces the route before the async session response mounts the hub.
      window.history.replaceState({}, '', '/settings/connections')
      return json(customer)
    }
    calls.push([path, options.method, options.body])
    if (path === base + '/connections/cx_saved/status') return json({ status: verified ? 'ACTIVE' : 'INITIALIZING' })
    assert.fail(`Unexpected callback request: ${path}`)
  }, () => ({ project: { key_fingerprint: 'managed', status: 'LINKED' }, connections: [row], receipts: [] }), [],
  'https://ttros.core-site.example/settings/connections?status=success&connected_account_id=ca_saved')
  const panel = () => document.querySelector('[aria-label="Connection return verification"]')
  assert.match(panel().textContent, /connection still needs attention/)
  assert.match(panel().textContent, /Awaiting approval/)
  assert.doesNotMatch(panel().textContent, /Verified with Composio/)
  assert.deepEqual(calls, [[base + '/connections/cx_saved/status', 'POST', '{}']])
  verified = true
  await ui.click('[aria-label="Connection return verification"] button')
  assert.match(panel().textContent, /Connected\. Your app is ready to use/)
  assert.equal(calls.length, 2)
  assert.ok(calls.every(([path]) => path === base + '/connections/cx_saved/status'))
})

test('forged success without a saved attempt never reports Connected', async () => {
  const { verifyConnectionReturn } = await import('../src/connectionsApi.js')
  const paths = []
  const result = await verifyConnectionReturn(async path => { paths.push(path); return { connections: [] } })
  assert.equal(result.status, 'NOT_FOUND')
  assert.deepEqual(paths, [base + '/state'])
  const disabled = await verifyConnectionReturn(async () => ({ connections: [{ enabled: true, config: { issued_account_id: 'ca_disabled' }, access: { mode: 'Disabled' } }] }))
  assert.equal(disabled.status, 'NOT_FOUND')
})

test('operator uses Platform and a failed attempt does not starve callback verification', async () => {
  const { connectPath, verifyConnectionReturn } = await import('../src/connectionsApi.js')
  assert.equal(connectPath({ project: null }), '/connect')
  const result = await verifyConnectionReturn(async path => {
    if (path.endsWith('/state')) return { project: { key_fingerprint: 'managed' }, connections: [
      { id: 'old', service: 'gmail', account: 'ca_old', enabled: true, status: 'PENDING',
        config: { source: 'project', project: 'managed', issued_account_id: 'ca_old', started_at: 1 } },
      { id: 'approved', service: 'gmail', account: 'ca_approved', enabled: true, status: 'PENDING',
        config: { source: 'project', project: 'managed', issued_account_id: 'ca_approved', started_at: 2 } },
    ] }
    if (path.includes('/old/')) throw Error('Saved attempt expired; exact owner unavailable')
    return { status: 'ACTIVE' }
  })
  // Without a callback account the newest attempt is verified; the expired one is never consulted.
  assert.equal(result.status, 'CONNECTED')
  assert.deepEqual(result.connections.map(row => row.id), ['approved'])
})

test('Platform callback checks only its exact account and never treats an old personal grant as Platform', async () => {
  const state = { project: { key_fingerprint: 'managed' }, connections: [
    { id: 'cx_personal', service: 'gmail', account: 'ca_personal', status: 'ACTIVE', enabled: true,
      config: { source: 'consumer', issued_account_id: 'ca_personal' } },
    { id: 'cx_old', service: 'gmail', account: 'ca_old', status: 'EXPIRED', enabled: true,
      config: { source: 'project', project: 'managed', issued_account_id: 'ca_old', started_at: 1 } },
    { id: 'cx_current', service: 'gmail', account: 'ca_current', status: 'PENDING', enabled: true,
      config: { source: 'project', project: 'managed', issued_account_id: 'ca_current', started_at: 2 } },
  ] }
  const called = []
  const request = async path => {
    called.push(path)
    if (path.endsWith('/state')) return state
    if (path.includes('cx_current')) return { status: 'ACTIVE', config: { provider_account: { status_reason: '' } } }
    throw Error('An unrelated connection must not be checked')
  }
  const result = await verifyConnectionReturn(request, '?composio=returned&status=success&connected_account_id=ca_current')
  assert.equal(result.status, 'CONNECTED')
  assert.deepEqual(result.connections.map(row => row.id), ['cx_current'])
  assert.equal(called.length, 2)
  const unknown = await verifyConnectionReturn(request, '?composio=returned&connected_account_id=ca_outsider')
  assert.equal(unknown.status, 'NOT_FOUND')
  called.length = 0
  const camel = await verifyConnectionReturn(request, '?composio=returned&status=success&connectedAccountId=ca_current')
  assert.deepEqual(camel.connections.map(row => row.id), ['cx_current'])
  assert.equal(called.length, 2)
})

test('personal Gmail connection must not disable connecting Gmail to the Platform project', async t => {
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' } }
  const state = { project: { key_fingerprint: 'managed', status: 'LINKED' }, receipts: [],
    connections: [{ id: 'cx_old', service: 'gmail', name: 'Gmail personal', account: 'ca_personal', kind: 'composio',
      status: 'ACTIVE', enabled: true, operations: ['profile'], permissions: ['profile:read'],
      config: { source: 'consumer' }, access: { mode: 'Automatic' } }] }
  const requested = []
  await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') return json(customer)
    if (path === base + '/apps/gmail') return json({ name: 'Gmail', schemes: [{ mode: 'OAUTH2', oauth: true }] })
    if (path === base + '/connect') {
      requested.push(JSON.parse(options.body))
      return json({ status: 'PENDING', message: 'Hosted Platform authorization started' })
    }
    assert.fail('Unexpected backend call: ' + path)
  }, () => state)
  const card = document.querySelector('.app-tile')
  assert.match(card.textContent, /Available to connect/)
  const button = card.querySelector('button')
  assert.equal(button.disabled, false)
  await act(async () => button.click())
  await clickText('Continue', document.querySelector('dialog'))
  assert.deepEqual(requested, [{ service: 'gmail', scheme: 'OAUTH2', fields: {} }])
})

test('the old For You account list, bind and tool discovery are gone from Connections', async t => {
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' } }
  const row = { id: 'cx_platform', service: 'gmail', name: 'Gmail', account: 'ca_platform', kind: 'composio', status: 'ACTIVE', enabled: true,
    operations: ['search'], permissions: ['search:read'], config: { source: 'project', project: 'managed' }, access: { mode: 'Automatic' } }
  const requested = []
  await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') return json(customer)
    requested.push(path)
    assert.fail('Unexpected backend call: ' + path)
  }, () => ({ project: { key_fingerprint: 'managed', status: 'LINKED' }, connections: [row], receipts: [] }), [],
  'http://127.0.0.1:4390/settings/connections')
  const tab = [...document.querySelectorAll('nav button')].find(b => b.textContent.startsWith('My accounts'))
  await act(async () => tab.click())
  const text = document.body.textContent
  for (const removed of ['Available accounts', 'Find and enable a provider read', 'Verify binding'])
    assert.equal(text.includes(removed), false, removed)
  assert.match(text, /Connected/)
  assert.ok(requested.every(path => !path.endsWith('/bind') && !path.includes('/tools')))
})

test('API-key apps get a secure generated form; keys are write-only and never shown again', async t => {
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' } }
  const posts = []
  let state = { connections: [], receipts: [] }
  const form = { status: 'NEEDS_INPUT', service: 'agent_mail', app: { name: 'Agent Mail' }, scheme: 'API_KEY', stage: 'account',
    message: 'Agent Mail connects with an API key.', help_urls: ['https://console.agentmail.to'],
    fields: [{ name: 'region', label: 'API Region', required: true, secret: false, default: 'us', description: 'Use us unless EU.' },
             { name: 'generic_api_key', label: 'API Key', required: true, secret: true, default: null, description: '' }],
    alternatives: [] }
  await mount(t, async (path, options = {}) => {
    if (path === '/api/auth/session') return json(customer)
    if (path === base + '/apps/agent_mail') return json({ name: 'Agent Mail', schemes: [{
      mode: 'API_KEY', account_fields: form.fields, help_urls: form.help_urls }] })
    if (path === base + '/connect') {
      const body = JSON.parse(options.body); posts.push(body)
      state = { ...state, project: { key_fingerprint: 'managed', status: 'LINKED' }, connections: [{ id: 'cx_m', service: 'agent_mail', identity: 'ops@example.invalid', kind: 'composio', status: 'ACTIVE', enabled: true, operations: ['tools', 'tool'], permissions: ['tools:read', 'tool:policy'], config: { source: 'project', project: 'managed' }, access: { mode: 'Automatic' } }] }
      return json({ id: 'cx_m', status: 'ACTIVE', message: 'Connected.' })
    }
    if (path === base + '/connections/cx_m/remove') { posts.push({ removed: true }); state = { ...state, connections: [] }; return json({ status: 'REMOVED', message: 'Removed from TTROS' }) }
    assert.fail('Unexpected backend call: ' + path)
  }, () => state, [{ slug: 'agent_mail', name: 'Agent Mail', description: 'Email inboxes for agents' }])
  await act(async () => document.querySelector('[aria-label="Connect Agent Mail"]').click())
  const panel = document.querySelector('dialog[open]')
  assert.ok(panel, 'secure form opens automatically')
  const key = panel.querySelector('input[name="generic_api_key"]')
  assert.equal(key.type, 'password'); assert.equal(key.getAttribute('autocomplete'), 'new-password')
  assert.equal(panel.querySelector('input[name="region"]').value, 'us')
  assert.ok(panel.querySelector('a[href="https://console.agentmail.to"]'))
  await changeInput('input[name="generic_api_key"]', 'am_secret_value_123')
  await act(async () => panel.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  assert.deepEqual(posts.at(-1), { service: 'agent_mail', scheme: 'API_KEY', fields: { region: 'us', generic_api_key: 'am_secret_value_123' } })
  assert.equal(document.body.textContent.includes('am_secret_value_123'), false)
  assert.equal(key.value, '')
  await clickText('Done', panel)
  assert.equal(document.querySelector('dialog'), null)
  assert.match(document.querySelector('[data-app="agent_mail"]').textContent, /Connected.*ops@example\.invalid/)
  await act(async () => [...document.querySelectorAll('nav button')].find(b => b.textContent.startsWith('My accounts')).click())
  await act(async () => document.querySelector('[aria-label="Remove Agent Mail ops@example.invalid"]').click())
  assert.deepEqual(posts.at(-1), { removed: true })
  assert.equal(document.querySelector('[data-connection="cx_m"]'), null)
})

test('the catalogue loads further pages by cursor instead of stopping at the first page', async t => {
  const customer = { ...session, identity: { method: 'google_oidc', workspace: 'customer-a', user: 'alice' } }
  const ui = await mount(t, async path => {
    if (path === '/api/auth/session') return json(customer)
    assert.fail('Unexpected backend call: ' + path)
  }, () => ({ connections: [], receipts: [] }), path => path.includes('cursor=page2')
    ? { items: [{ slug: 'zendesk', name: 'Zendesk' }], next_cursor: null, total: 2, source: 'App catalogue' }
    : { items: [{ slug: 'gmail', name: 'Gmail' }], next_cursor: 'page2', total: 2, source: 'App catalogue' })
  assert.match(document.body.textContent, /2 apps/)
  await act(async () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Load more apps').click())
  assert.equal(ui.catalogueRequests.at(-1), base + '/catalogue?q=&cursor=page2')
  assert.ok(document.querySelector('[data-app="gmail"]') && document.querySelector('[data-app="zendesk"]'))
  assert.equal([...document.querySelectorAll('button')].some(b => b.textContent === 'Load more apps'), false)
})
