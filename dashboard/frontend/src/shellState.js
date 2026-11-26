export const MAX_SESSION_TABS = 8

export const LANE_ROUTE_NAMES = ['marketing', 'revenue', 'delivery', 'operations', 'unassigned']

const normalizeLaneRouteName = value => {
  const lane = String(value || '').trim().toLowerCase()
  return LANE_ROUTE_NAMES.includes(lane) ? lane : null
}

export const laneRoutePath = lane => {
  const normalized = normalizeLaneRouteName(lane)
  return normalized ? `/lane/${normalized}` : null
}

// Stable, shareable URL for one Business Brain record: /memory?record=<pointer>.
// The pointer is only a Business Brain reference ("business_brain:<relative
// path>"), never a filesystem path; the backend's existing scope check remains
// the authority on whether it can be read.
const RECORD_POINTER_RE = /^business_brain:[^\\\u0000-\u001f\u007f]{1,500}$/
export const normalizeRecordPointer = value => {
  const pointer = String(value || '').trim()
  if (!RECORD_POINTER_RE.test(pointer)) return ''
  if (pointer.slice('business_brain:'.length).split('/').some(part => part === '..' || part === '.')) return ''
  return pointer
}
export const memoryRecordPath = pointer => {
  const record = normalizeRecordPointer(pointer)
  return record ? `/memory?record=${encodeURIComponent(record)}` : null
}

export const shellRouteFromPath = (pathname, search = '') => {
  const path = String(pathname || '')
  const directView = { '/graphify': 'graphify', '/ingest': 'memory-intake', '/repositories': 'repo-ingest', '/settings/connections': 'settings' }[path.replace(/\/+$/, '')]
  if (directView) return { view: directView, viewParams: {} }
  if (path.replace(/\/+$/, '') === '/memory') {
    let record = ''
    try { record = normalizeRecordPointer(new URLSearchParams(String(search || '')).get('record')) } catch { record = '' }
    return { view: 'search', viewParams: record ? { tab: 'memory', record } : { tab: 'memory' } }
  }
  const match = path.match(/^\/lane\/([^/]+)\/?$/)
  if (match) {
    let lane
    try {
      lane = normalizeLaneRouteName(decodeURIComponent(match[1]))
    } catch {
      return null
    }
    return lane ? { view: 'lane-workspace', viewParams: { lane } } : null
  }
  const destination = PATH_TO_DESTINATION[path.replace(/\/+$/, '') || '/']
  if (!destination) return null
  const params = new URLSearchParams(search)
  const viewParams = {}
  if (['work-queue', 'results'].includes(destination)) {
    if (params.get('job')) viewParams.selectedId = params.get('job')
    if (['all', 'active', 'attention', 'receipts', 'automations'].includes(params.get('filter'))) viewParams.filter = params.get('filter')
  }
  if (destination === 'crm') {
    if (/^(opp|contact|org):[^\s]{1,140}$/.test(params.get('record') || '')) viewParams.record = params.get('record')
    if (['pipeline', 'attention', 'open', 'all', 'contacts', 'subscribers', 'organizations'].includes(params.get('filter'))) viewParams.filter = params.get('filter')
  }
  if (destination === 'websites' && /^AOS-\d{4}-\d+$/.test(params.get('job') || '')) viewParams.job = params.get('job')
  if (destination === 'websites' && ['seo', 'changes', 'history', 'studio'].includes(params.get('tab'))) viewParams.tab = params.get('tab')
  if (destination === 'websites' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(params.get('site') || '')) viewParams.site = params.get('site')
  return { view: destination, viewParams }
}

export const shellPathForView = (view, viewParams = {}) => {
  const directPath = { graphify: '/graphify', 'memory-intake': '/ingest', 'repo-ingest': '/repositories', settings: '/settings/connections', 'connections-spine': '/settings/connections' }[view]
  if (directPath) return directPath
  if (view === 'lane-workspace') return laneRoutePath(viewParams.lane) || '/'
  if (view === 'search' && viewParams?.tab === 'memory' && viewParams.record) return memoryRecordPath(viewParams.record) || '/search'
  const base = DESTINATION_PATH[destinationForView(view)] || '/'
  if (['work-queue', 'results', 'results-receipts', 'artifacts'].includes(view)) {
    const params = new URLSearchParams()
    if (viewParams.selectedId) params.set('job', viewParams.selectedId)
    if (viewParams.filter) params.set('filter', viewParams.filter)
    return `${base}${params.size ? `?${params}` : ''}`
  }
  if (view === 'crm' || view === 'websites') {
    const params = new URLSearchParams()
    if (view === 'crm' && viewParams.record) params.set('record', viewParams.record)
    if (view === 'crm' && viewParams.filter) params.set('filter', viewParams.filter)
    if (view === 'websites' && viewParams.job) params.set('job', viewParams.job)
    if (view === 'websites' && viewParams.tab && !viewParams.job) params.set('tab', viewParams.tab)
    if (view === 'websites' && viewParams.site && viewParams.tab === 'studio') params.set('site', viewParams.site)
    return `${base}${params.size ? `?${params}` : ''}`
  }
  return base
}

export const shellViewForNavigation = (view, viewParams = {}) =>
  view === 'work-queue' && laneRoutePath(viewParams.lane) ? 'lane-workspace' : view

export const VIEW_META = {
  'company-home': { label: 'Company Home', workbench: 'hermes' },
  crm: { label: 'CRM & Sales', workbench: 'codex' },
  websites: { label: 'Websites & Digital', workbench: 'codex' },
  cockpit: { label: 'David', workbench: 'hermes', pinned: true },
  'work-queue': { label: 'Work Queue', workbench: 'codex' },
  'lane-workspace': { label: 'Lane Workspace', workbench: 'codex' },
  'workflow-bench': { label: 'Workflow Bench', workbench: 'hermes' },
  'message-board': { label: 'Message Board', workbench: 'hermes' },
  'skills-board': { label: 'Skills Board', workbench: 'hermes' },
  'memory-board': { label: 'Memory Board', workbench: 'hermes' },
  'memory-intake': { label: 'Add to Memory', workbench: 'hermes' },
  'prompt-library': { label: 'Prompt Library', workbench: 'codex' },
  graphify: { label: 'Graphify', workbench: 'hermes' },
  'repo-ingest': { label: 'Repo Ingest', workbench: 'codex' },
  'results-receipts': { label: 'Results & Receipts', workbench: 'hermes' },
  'tokens-roi': { label: 'Tokens & ROI', workbench: 'hermes' },
  artifacts: { label: 'Artifacts', workbench: 'codex' },
  'connections-spine': { label: 'Connections / Spine', workbench: 'hermes' },
  'mission-control': { label: 'Mission Control', workbench: 'hermes' },
  settings: { label: 'Settings / Connections', workbench: 'hermes' },
  search: { label: 'Search', workbench: 'hermes' },
  results: { label: 'Results', workbench: 'hermes' },
  system: { label: 'System', workbench: 'hermes' },
  'remote-pc': { label: 'Remote PC', workbench: 'hermes' },
}

export const initialSessionTabs = () => [
  { id: 'cockpit', ...VIEW_META.cockpit, preview: false },
]

// The dashboard's global destinations: the five from BUILD_SPECIFICATION "I —
// Information hierarchy", plus Remote PC (Windows host control). `cockpit`
// keeps its existing technical id (and every existing test/session-storage
// value that names it) but is presented throughout the UI as "David" via
// VIEW_META.cockpit.label above.
export const DESTINATIONS = [
  { id: 'cockpit', label: 'David' },
  { id: 'work-queue', label: 'Work Queue' },
  { id: 'results', label: 'Results' },
  { id: 'search', label: 'Search' },
  { id: 'system', label: 'System' },
  { id: 'remote-pc', label: 'Remote PC' },
]

// Every routable view id — including legacy views now folded into a hub's
// local tabs — resolves to exactly one of the five destinations above, so
// nav active-state and the mobile bottom bar stay correct no matter which
// internal tab or legacy deep link is actually open.
const VIEW_DESTINATION = {
  'company-home': 'company-home',
  crm: 'crm',
  websites: 'websites',
  cockpit: 'cockpit',
  'message-board': 'cockpit',
  'work-queue': 'work-queue',
  'lane-workspace': 'work-queue',
  results: 'results',
  'results-receipts': 'results',
  artifacts: 'results',
  search: 'search',
  'memory-board': 'search',
  'memory-intake': 'search',
  graphify: 'search',
  'repo-ingest': 'search',
  system: 'system',
  'mission-control': 'system',
  'connections-spine': 'system',
  'workflow-bench': 'system',
  'skills-board': 'system',
  'tokens-roi': 'system',
  settings: 'system',
  'prompt-library': 'system',
  'remote-pc': 'remote-pc',
}

export const destinationForView = view => VIEW_DESTINATION[view] || 'cockpit'

// Existing root-session restoration stays compatible. Home and David now also
// have explicit URLs; job selection and filters are encoded for reload/Back.
const DESTINATION_PATH = {
  'company-home': '/home',
  crm: '/crm',
  websites: '/websites',
  cockpit: '/david',
  'work-queue': '/queue',
  results: '/results',
  search: '/search',
  system: '/system',
  'remote-pc': '/remote-pc',
}
const PATH_TO_DESTINATION = Object.fromEntries(Object.entries(DESTINATION_PATH).map(([id, path]) => [path, id]))

export function restoreShellSession(raw, pathname = '', search = '') {
  const fallback = { view: 'cockpit', viewParams: {}, sessionTabs: initialSessionTabs() }
  let restored = fallback
  try {
    if (raw) {
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
      const view = VIEW_META[parsed?.view] ? parsed.view : fallback.view
      const supplied = Array.isArray(parsed?.sessionTabs) ? parsed.sessionTabs : []
      const valid = supplied
        .filter(tab => tab && VIEW_META[tab.id])
        .slice(0, MAX_SESSION_TABS)
        .map(tab => ({ ...tab, ...VIEW_META[tab.id], params: tab.params && typeof tab.params === 'object' ? tab.params : {} }))
      const withCockpit = valid.some(tab => tab.id === 'cockpit') ? valid : [fallback.sessionTabs[0], ...valid]
      const tabs = withCockpit.some(tab => tab.id === view) ? withCockpit : openSessionTab(withCockpit, view, parsed?.viewParams || {})
      const active = tabs.find(tab => tab.id === view)
      restored = { view, viewParams: active?.params || parsed?.viewParams || {}, sessionTabs: tabs.slice(0, MAX_SESSION_TABS) }
    }
  } catch {
    restored = fallback
  }
  const route = shellRouteFromPath(pathname, search)
  if (!route) return restored
  const sessionTabs = openSessionTab(restored.sessionTabs, route.view, route.viewParams)
  return { ...route, sessionTabs }
}

export const shellSessionSnapshot = (view, viewParams, sessionTabs) => JSON.stringify({ view, viewParams: viewParams || {}, sessionTabs })

export const needsMeCollapseKey = (view, viewParams) => view === 'work-queue' ? viewParams?.selectedId || null : null

export function openSessionTab(tabs, id, params = {}) {
  const existing = tabs.find(tab => tab.id === id)
  if (existing) return tabs.map(tab => tab.id === id ? { ...tab, params } : tab)

  const meta = VIEW_META[id] || { label: id, workbench: 'hermes' }
  const next = { id, ...meta, params, preview: !meta.pinned }
  const previewIndex = tabs.findIndex(tab => tab.preview && !tab.pinned)
  let result = previewIndex >= 0
    ? tabs.map((tab, index) => index === previewIndex ? next : tab)
    : [...tabs, next]

  while (result.length > MAX_SESSION_TABS) {
    const removable = result.findIndex((tab, index) => index > 0 && !tab.pinned)
    if (removable < 0) break
    result = result.filter((_, index) => index !== removable)
  }
  return result
}

export const pinSessionTab = (tabs, id) => tabs.map(tab => tab.id === id ? { ...tab, pinned: true, preview: false } : tab)

export function closeSessionTab(tabs, id) {
  const target = tabs.find(tab => tab.id === id)
  if (!target || target.id === 'cockpit') return tabs
  return tabs.filter(tab => tab.id !== id)
}

export const normalizeWorkbench = value => {
  const workbench = String(value || '').toLowerCase()
  if (workbench.includes('claude')) return 'claude'
  if (workbench.includes('codex')) return 'codex'
  if (workbench.includes('hermes')) return 'hermes'
  if (workbench.includes('antigravity') || workbench === 'anti') return 'anti'
  return 'unattributed'
}

export const stateShade = status => {
  if (['agent_working', 'running'].includes(status)) return 'working'
  if (['done', 'cancelled'].includes(status)) return 'done'
  return 'queued'
}

export const workbenchColor = (workbench, status) => {
  if (['human_review', 'needs_input'].includes(status)) return 'var(--needs-review)'
  const normalized = normalizeWorkbench(workbench)
  if (normalized === 'unattributed') return 'var(--hairline)'
  return `var(--wb-${normalized}-${stateShade(status)})`
}

export const laneName = item => {
  const tag = (item?.tags || []).find(value => String(value).startsWith('lane:'))
  const lane = String(tag ? tag.split(':')[1] : item?.lane || '').toLowerCase()
  return ['marketing', 'revenue', 'delivery', 'operations'].includes(lane) ? lane : 'operations'
}

export const laneColor = lane => `var(--lane-${lane || 'operations'})`
