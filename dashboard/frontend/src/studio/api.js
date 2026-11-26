// Website Studio HTTP client. Every routine call below is deterministic on the server and
// cannot reach a model. requestProposal is the single exception: it is called only from
// the explicit "Propose changes" button and never from autosave, sync or other UI events.
// Stage 3: drafts, previews and proposals name the Studio page (default home, the Stage 1–2 routes).
// Revisit: when Studio routes change. Last touched: 2026-11-21.
const base = site => `/api/website/studio/sites/${encodeURIComponent(site)}`
const pageQuery = page => `?page=${encodeURIComponent(page || 'home')}`
const CSRF = { 'X-TTROS-Connections': '1' }
// Publication needs the verified operator gate (Cloudflare Access/Tailscale identity) as well as CSRF.
const OWNER = { 'X-TTROS-Connections': '1', 'X-TTROS-Remote-PC': '1' }

export function studioClient(fetcher = (...args) => fetch(...args)) {
  async function call(url, options = {}) {
    const response = await fetcher(url, options.body === undefined ? options : { ...options, headers: { 'Content-Type': 'application/json', ...options.headers }, body: JSON.stringify(options.body) })
    let value = null
    try { value = await response.json() } catch { value = null }
    if (!response.ok) {
      const detail = value?.detail
      const message = response.status === 404 && !detail
        ? 'Studio source API is not available on this backend. It needs the Stage 1.5 backend (restart required).'
        : (typeof detail === 'string' ? detail : `Request failed (${response.status})`)
      throw Object.assign(new Error(message), { status: response.status })
    }
    return value
  }
  return {
    sites: () => call('/api/website/studio/sites'),
    sources: site => call(`${base(site)}/sources`),
    refresh: (site, reference = true) => call(`${base(site)}/sources/refresh?reference=${reference}`, { method: 'POST' }),
    driveFolder: (site, folder) => call(`${base(site)}/drive-folder`, { method: 'PUT', body: { folder } }),
    brand: site => call(`${base(site)}/brand`),
    approveBrand: (site, fingerprint) => call(`${base(site)}/brand/approve`, { method: 'POST', body: { fingerprint, actor: 'owner' } }),
    withdrawBrand: site => call(`${base(site)}/brand/approve`, { method: 'DELETE' }),
    preview: (site, data, operations, page = 'home') => call(`${base(site)}/preview`, { method: 'POST', body: { data, operations, page } }),
    drafts: (site, page = 'home') => call(`${base(site)}/drafts${pageQuery(page)}`),
    draft: (site, version, page = 'home') => call(`${base(site)}/drafts/${Number(version)}${pageQuery(page)}`),
    saveDraft: (site, body, page = 'home') => call(`${base(site)}/drafts${pageQuery(page)}`, { method: 'POST', body }),
    build: (site, body) => call(`${base(site)}/builds`, {method:'POST', headers:{'Content-Type':'application/json','X-TTROS-Connections':'1'}, body}),
    stopBuild: (site, id) => call(`${base(site)}/builds/${encodeURIComponent(id)}`, {method:'DELETE',headers:{'X-TTROS-Connections':'1'}}),
    buildStatus: (site, id) => call(`${base(site)}/builds/${encodeURIComponent(id)}`),
    assetUrl: (site, id) => `${base(site)}/assets/${encodeURIComponent(id)}`,
    // Releases (deterministic, zero model calls). Publish/rollback: verified operator + typed command.
    releases: site => call(`${base(site)}/releases`),
    createRelease: (site, buildId) => call(`${base(site)}/releases`, { method: 'POST', headers: CSRF, body: { build_id: buildId } }),
    release: (site, id) => call(`${base(site)}/releases/${encodeURIComponent(id)}`),
    compareRelease: (site, id, target) => call(`${base(site)}/releases/${encodeURIComponent(id)}/compare?target=${encodeURIComponent(target)}`),
    releaseHistory: (site, target) => call(`${base(site)}/release-targets/${encodeURIComponent(target)}/history`),
    releaseOperation: (site, id) => call(`${base(site)}/release-operations/${encodeURIComponent(id)}`),
    prepareRelease: (site, id, body) => call(`${base(site)}/releases/${encodeURIComponent(id)}/prepare`, { method: 'POST', headers: OWNER, body }),
    executeRelease: (site, id, body) => call(`${base(site)}/releases/${encodeURIComponent(id)}/execute`, { method: 'POST', headers: OWNER, body }),
    // Explicit, operator-invoked model request. Do not call from effects, timers or sync.
    requestProposal: body => call('/api/website/studio/propose', { method: 'POST', body }),
  }
}

export const studioApi = studioClient()
export const RELEASE_CALLS = ['releases', 'createRelease', 'release', 'compareRelease', 'releaseHistory', 'releaseOperation', 'prepareRelease', 'executeRelease']
export const ROUTINE_CALLS = ['sites', 'sources', 'refresh', 'driveFolder', 'brand', 'approveBrand', 'withdrawBrand', 'preview', 'drafts', 'draft', 'saveDraft']
