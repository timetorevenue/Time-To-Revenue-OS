// Same-origin requests only; provider credentials never enter the browser.
export async function connectionRequest(path, body, method = 'POST') {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 45000)
  let response
  try {
    response = await fetch(path, { signal: controller.signal,
      ...(body === undefined ? {} : { method, headers: { 'Content-Type': 'application/json', 'X-TTROS-Connections': '1' }, body: JSON.stringify(body) }),
    })
    let value
    try { value = await response.json() } catch { throw Error(`TTROS returned HTTP ${response.status} without a readable response. Check the local backend and try again.`) }
    if (!response.ok) {
      const detail = value.detail || {}
      throw Object.assign(new Error(typeof detail === 'string' ? detail : detail.message || `Request failed (HTTP ${response.status})`), { detail, status: response.status })
    }
    return value
  } catch (error) {
    if (error.name === 'AbortError') throw Error('The TTROS request timed out. Check the backend/provider status before retrying.')
    if (!response) throw Error('Cannot reach the TTROS backend. Check that the local server is running, then use Check configuration again.')
    throw error
  } finally { clearTimeout(timer) }
}

// All new app authorizations use the linked/managed Platform project.
export const connectPath = () => '/connect'

// Callback parameters trigger verification; they never supply account identity
// or prove approval. Resolve only attempts stored for the authenticated member.
export const hasConnectionReturn = search => {
  const params = new URLSearchParams(search)
  return params.get('composio') === 'returned' || params.has('status')
}

export async function verifyConnectionReturn(request = connectionRequest, search = typeof location === 'undefined' ? '' : location.search) {
  const base = '/api/account-connections/hub'
  const state = await request(base + '/state')
  const params = new URLSearchParams(search)
  const callbackAccount = params.get('connected_account_id') || params.get('connectedAccountId')
  // A callback is only a hint: the backend checks the saved account's exact project and owner.
  // Older consumer attempts and unrelated expired approvals must not mask this result.
  const attempts = (state.connections || []).filter(row =>
    row.enabled && row.config?.source === 'project' &&
    row.config?.project === state.project?.key_fingerprint &&
    row.config?.issued_account_id && row.access?.mode !== 'Disabled')
  const chosen = callbackAccount
    ? attempts.filter(row => row.account === callbackAccount && row.config.issued_account_id === callbackAccount)
    : [...attempts].sort((a, b) =>
        (b.config.started_at || Date.parse(b.config.expires_at || '') || 0) -
        (a.config.started_at || Date.parse(a.config.expires_at || '') || 0)).slice(0, 1)
  if (!chosen.length) return { status: 'NOT_FOUND', connections: [],
    message: 'No matching Platform authorization was saved for this signed-in member. Open Connect in TTROS to start authorization.' }
  const verified = []
  for (const row of chosen) {
    try {
      const result = await request(`${base}/connections/${encodeURIComponent(row.id)}/status`, {})
      verified.push({ id: row.id, service: row.service, status: result.connection_status || result.status,
        reason: result.config?.provider_account?.status_reason || '' })
    } catch (error) {
      verified.push({ id: row.id, service: row.service, status: row.status, error: error.message })
    }
  }
  const active = verified.every(row => row.status === 'ACTIVE' && !row.error)
  return { status: active ? 'CONNECTED' : verified.some(row => row.error) ? 'ERROR' : 'PENDING', connections: verified,
    message: active ? 'Connected. Your app is ready to use.'
      : 'The connection still needs attention. Check the account status below.' }
}
