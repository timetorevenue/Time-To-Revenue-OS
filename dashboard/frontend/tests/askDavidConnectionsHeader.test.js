import test from 'node:test'
import assert from 'node:assert/strict'
import axios from 'axios'

test('actual Ask David client supplies existing Connections CSRF header on natural app requests', async () => {
  const previousAdapter = axios.defaults.adapter
  const previousWindow = globalThis.window
  let sent
  axios.defaults.adapter = async config => {
    sent = config
    return { data: { success: true, kind: 'david_reply', response: 'verified' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  globalThis.window = { sessionStorage: { getItem: () => 'dashboard-csrf-proof', setItem: () => {} } }
  try {
    const { askDavid } = await import('../src/api.js?connections-header-proof')
    const reply = await askDavid('Search Gmail for the confirmation')
    assert.equal(reply.success, true)
    assert.equal(sent.url, '/dashboard/ask-david')
    assert.equal(sent.method, 'post')
    assert.equal(sent.headers.get('X-TTROS-Connections'), '1')
    assert.deepEqual(JSON.parse(sent.data), { text: 'Search Gmail for the confirmation', source_refs: [], conversation_id: 'dashboard-csrf-proof' })
  } finally {
    axios.defaults.adapter = previousAdapter
    globalThis.window = previousWindow
  }
})
