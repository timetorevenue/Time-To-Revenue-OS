import axios from 'axios'

// Revisit: when dashboard backend route or timeout contracts change. · Last touched: 2026-11-16.

const api = axios.create({ baseURL: '/api', timeout: 5000 })
// Longer timeout for WSL commands that may take up to 2 minutes
const apiWsl = axios.create({ baseURL: '/api', timeout: 130000 })

export const transcribeDavidRecording = (audio, signal) => apiWsl.post('/dashboard/ask-david/transcribe', audio, {
  headers: { 'X-TTROS-Connections': '1', 'Content-Type': audio.type },
  signal,
}).then(response => response.data.text)

export const speakDavidReply = (text, signal) => apiWsl.post('/dashboard/ask-david/speak', { text }, {
  headers: { 'X-TTROS-Connections': '1' },
  responseType: 'blob',
  signal,
}).then(response => response.data)
const apiGraphify = axios.create({ baseURL: '/api', timeout: 620000 })
// Measured detailed-list baseline was ~1.6s before compact list rows; 5s keeps
// a deliberate >3x margin without hiding queue starvation behind a long wait.
const apiQueue = axios.create({ baseURL: '/api', timeout: 5000 })
// Uploads and Business Brain ingestion (git commit + search/Graphify refresh)
// can run considerably longer than an ordinary read; same order as apiWsl.
const apiUpload = axios.create({ baseURL: '/api', timeout: 60000 })
const apiIngest = axios.create({ baseURL: '/api', timeout: 130000 })

export const getHealth = () => api.get('/health').then(r => r.data)
export const getDavidJobs = () => api.get('/david/jobs', { params: { limit: 100 } }).then(r => r.data)
export const getDavidJob = id => api.get(`/david/jobs/${encodeURIComponent(id)}`).then(r => r.data)
export const changeDavidAutomation = (id, operation, revision, definition) =>
  api.patch(`/david/jobs/${encodeURIComponent(id)}/automation`, { operation, revision, definition }, {
    headers: { 'X-TTROS-Connections': '1' },
  }).then(r => r.data)
export const getEntities = () => api.get('/entities').then(r => r.data)
export const getOverview = () => api.get('/overview').then(r => r.data)
export const getLogs = () => api.get('/logs').then(r => r.data)
export const getResults = () => api.get('/results').then(r => r.data)
export const getPackets = () => api.get('/packets').then(r => r.data)
export const getTracker = () => api.get('/tracker').then(r => r.data)
export const getConnectors = () => api.get('/connectors').then(r => r.data)
export const getComposioConnections = () => api.get('/composio/connections').then(r => r.data)
export const getQueueSummary = () => apiQueue.get('/queue/summary').then(r => r.data)
export const getQueueStatus = () => apiQueue.get('/queue/status').then(r => r.data)
export const getQueueItems = () => apiQueue.get('/queue/items').then(r => r.data)
export const getQueueItemsForScope = scope => api.get('/queue/items', { params: { scope } }).then(r => r.data)
export const getQueueItem = (id) => apiQueue.get(`/queue/items/${id}`).then(r => r.data)
export const getQueueNext = () => apiQueue.get('/queue/next').then(r => r.data)
export const getQueueReceipt = (path) => apiQueue.get('/queue/receipt', { params: { path } }).then(r => r.data)
export const getQueueArtifact = (path) => apiQueue.get('/queue/artifact', { params: { path } }).then(r => r.data)
export const openQueueArtifactFolder = (path) => apiQueue.post('/queue/artifact/open-folder', { path }).then(r => r.data)
export const createQueueItem = (data) => apiQueue.post('/queue/items', data).then(r => r.data)
export const getQueuePrompt = (id, target) => apiQueue.get(`/queue/items/${id}/prompt`, { params: { target } }).then(r => r.data)
export const attachQueueReceipt = (id, data) => apiQueue.post(`/queue/items/${id}/receipt`, data).then(r => r.data)
export const saveQueueReviewNote = (id, data) => apiQueue.post(`/queue/items/${id}/review-note`, data).then(r => r.data)
export const recordQueueOutreachEvent = (id, data) => apiQueue.post(`/queue/items/${id}/outreach-event`, data).then(r => r.data)
export const closeQueueItemReview = (id, data) => apiQueue.post(`/queue/items/${id}/review-close`, data).then(r => r.data)
export const updateQueueItemStatus = (id, status) => apiQueue.post(`/queue/items/${id}/status`, { status }).then(r => r.data)
export const deleteQueueItem = (id, data) => apiQueue.delete(`/queue/items/${id}`, { data }).then(r => r.data)
export const dismissQueueItem = (id, reason) => apiQueue.post(`/queue/items/${id}/dismiss`, { reason }).then(r => r.data)
export const closeFinishedQueueItem = (id) => apiQueue.post(`/queue/items/${id}/close-finished`, {}, { timeout: 620000 }).then(r => r.data)
export const runQueueItem = (id) => apiQueue.post(`/queue/items/${id}/run`, {}, { timeout: 30000 }).then(r => r.data)
export const updateTracker = (data) => api.post('/tracker', data).then(r => r.data)
export const createPacket = (data) => api.post('/packets', data).then(r => r.data)

// WSL / AgenticOSClean runtime
export const wslStatus = () => apiWsl.get('/wsl/status').then(r => r.data)

export const telegramStatus = () => api.get('/connectors/telegram/status').then(r => r.data)
export const getBackupsStatus = () => api.get('/backups/status').then(r => r.data)
export const getDashboardCockpit = () => api.get('/dashboard/cockpit').then(r => r.data)
export const createInboxCapture = (text, capture_id) => api.post('/dashboard/capture', { text, capture_id }).then(r => r.data)
export const getDashboardTokens = () => api.get('/dashboard/tokens').then(r => r.data)
export const getDashboardResults = () => api.get('/dashboard/results', { timeout: 20000 }).then(r => r.data)
export const getDashboardAgents = () => api.get('/dashboard/agents').then(r => r.data)
export const getDashboardSystemWatch = (stalledMinutes = 15) => api.get('/dashboard/system-watch', { params: { stalled_minutes: stalledMinutes } }).then(r => r.data)
export const getLatitudeStatus = () => api.get('/latitude/status').then(r => r.data)
export const sendLatitudeHeartbeat = () => api.post('/latitude/heartbeat').then(r => r.data)
export const sendAgentMailDigest = (data = {}) => api.post('/agentmail/daily-digest', data).then(r => r.data)
export const externalActionDryRun = (data) => api.post('/external-actions/dry-run', data).then(r => r.data)
export const consultExecutive = data => apiWsl.post('/executive-team/consult', data).then(r => r.data)
// A slow David turn is acknowledged with a job id (kind "david_job") instead of
// holding the connection past the ~120 s proxy limit; poll it to the final reply.
export const pollDavidJob = async (jobId, { intervalMs = 3000, maxMs = 3 * 60 * 60 * 1000 } = {}) => {
  const started = Date.now()
  for (;;) {
    const job = await api.get(`/dashboard/ask-david/jobs/${encodeURIComponent(jobId)}`, { timeout: 15000 }).then(r => r.data)
    if (job.status === 'done') return job.result
    if (job.status === 'failed' || job.status === 'interrupted') throw new Error(job.error || `David job ${job.status}`)
    if (Date.now() - started > maxMs) throw new Error(`David job ${jobId} is still running; check it later.`)
    await new Promise(resolve => setTimeout(resolve, intervalMs))
  }
}
let davidConversationId
const getDavidConversationId = () => {
  if (!davidConversationId) {
    try { davidConversationId = window.sessionStorage.getItem('ttros-david-conversation') } catch {}
    if (!davidConversationId) {
      const unique = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
      davidConversationId = `dashboard-${unique}`
      try { window.sessionStorage.setItem('ttros-david-conversation', davidConversationId) } catch {}
    }
  }
  return davidConversationId
}
export const askDavid = async (text, source_refs = [], { onPending } = {}) => {
  const conversation_id = getDavidConversationId()
  const data = await apiWsl.post('/dashboard/ask-david', { text, source_refs, conversation_id }, { headers: { 'X-TTROS-Connections': '1' } }).then(r => r.data)
  if (data?.kind === 'david_job') {
    onPending?.(data.job_id)
    return pollDavidJob(data.job_id)
  }
  return data
}
export const uploadFile = (file, { david = false } = {}) => {
  const form = new FormData()
  form.append('file', file)
  if (david) form.append('david_conversation_id', getDavidConversationId())
  const options = david ? { headers: { 'X-TTROS-Connections': '1' } } : undefined
  return apiUpload.post('/uploads', form, options).then(r => r.data)
}
export const getUpload = upload_id => api.get(`/uploads/${encodeURIComponent(upload_id)}`).then(r => r.data)
export const getMemoryIntake = () => api.get('/memory-intake').then(r => r.data)
export const getMemoryIntakeUnfiledPreview = path => api.get('/memory-intake/unfiled-preview', { params: { path } }).then(r => r.data)
export const ingestMemoryIntakeUpload = upload_id => apiIngest.post('/memory-intake/ingest', { upload_id }).then(r => r.data)
export const ingestMemoryIntakeInboxPath = inbox_path => apiIngest.post('/memory-intake/ingest', { inbox_path }).then(r => r.data)
export const getDashboardWorkflows = () => api.get('/dashboard/workflows').then(r => r.data)
export const getDashboardWorkflow = (id) => api.get(`/dashboard/workflows/${encodeURIComponent(id)}`).then(r => r.data)
export const saveDashboardWorkflow = (data) => api.post('/dashboard/workflows/save', data).then(r => r.data)
export const getDashboardSkills = () => api.get('/dashboard/skills').then(r => r.data)
export const getDashboardMemory = () => api.get('/dashboard/memory').then(r => r.data)
export const getDashboardMemoryNote = (path) => api.get('/dashboard/memory/note', { params: { path } }).then(r => r.data)
export const saveDashboardMemory = (data) => api.post('/dashboard/memory/save', data).then(r => r.data)
export const getDashboardPrompts = () => api.get('/dashboard/prompts').then(r => r.data)
// Scoped Brain metadata/hash checks on the Windows mount can exceed the normal 5s read budget.
// Share concurrent reads (including React's development effect replay), but let
// explicit later refreshes obtain a fresh authorised snapshot.
let graphifyRead = null
export const getDashboardGraphify = () => {
  if (!graphifyRead) graphifyRead = api.get('/dashboard/graphify', { timeout: 60000 })
    .then(r => r.data).finally(() => { graphifyRead = null })
  return graphifyRead
}
export const getDashboardRepoIngest = () => api.get('/dashboard/repo-ingest').then(r => r.data)
export const fetchGraphifyRepository = (url) => apiGraphify.post('/graphify/fetch', { url }).then(r => r.data)
export const refetchGraphifyRepository = (url) => apiGraphify.post('/graphify/refetch', { url }).then(r => r.data)
export const rebuildGraphifyRepository = (owner, repository) => apiGraphify.post('/graphify/rebuild', { owner, repository }).then(r => r.data)
export const runGraphifyAction = (owner, repository, action, inputs) => apiGraphify.post('/graphify/action', { owner, repository, action, inputs }).then(r => r.data)
export const getGraphifyArtifactText = (url) => apiGraphify.get(url.replace(/^\/api/, ''), { responseType: 'text', transformResponse: value => value }).then(r => r.data)
export const createDashboardTask = (data) => createQueueItem({ source: 'dashboard', ...data })
export const saveDashboardSkill = (data) => api.post('/dashboard/skills/save', data).then(r => r.data)
export const openDashboardPath = (data) => api.post('/dashboard/open-path', data).then(r => r.data)
export const searchIndex = (params) => api.get('/search', { params: { client_scope: 'global', ...params } }).then(r => r.data)
export const getSearchStatus = () => api.get('/search/status').then(r => r.data)
export const reindexSearch = () => api.post('/search/reindex').then(r => r.data)
export const ingestTick = () => api.post('/ingest/tick').then(r => r.data)
export const getArtifacts = (params) => api.get('/artifacts', { params: { client_scope: 'global', ...params } }).then(r => r.data)

// Memory entity browser — deterministic, zero-model reads (tools/aos_entity_index.py).
export const searchMemory = (q, params = {}) => api.get('/memory/search', { params: { q, ...params } }).then(r => r.data)
export const getMemoryEntity = id => api.get('/memory/entity', { params: { id } }).then(r => r.data)
export const getMemoryDocument = path => api.get('/memory/document', { params: { path } }).then(r => r.data)
export const browseMemory = dir => api.get('/memory/browse', { params: { dir } }).then(r => r.data)
export const openMemoryPath = (path, kind = 'file') => api.post('/memory/open', { path, kind }).then(r => r.data)

// Remote PC — Windows PowerShell, files and desktop status. Every route checks the
// operator identity at the backend; the custom header forces a CORS preflight so no
// other site can drive these routes from a browser. PowerShell runs and uploads need
// a prepare → confirm round trip bound to the exact action.
const remotePcHeaders = { 'X-TTROS-Remote-PC': '1' }
const apiRemote = axios.create({ baseURL: '/api/remote-pc', timeout: 30000, headers: remotePcHeaders })
const apiRemoteLong = axios.create({ baseURL: '/api/remote-pc', timeout: 330000, headers: remotePcHeaders })
export const getRemotePcStatus = (refresh = false) => apiRemoteLong.get('/status', { params: { refresh } }).then(r => r.data)
export const prepareRemotePowerShell = (command, timeout_seconds) => apiRemote.post('/powershell/prepare', { command, timeout_seconds }).then(r => r.data)
export const runRemotePowerShell = (command, timeout_seconds, confirmation_token) => apiRemoteLong.post('/powershell/run', { command, timeout_seconds, confirmation_token }).then(r => r.data)
export const getRemotePcReceipts = () => apiRemote.get('/receipts').then(r => r.data)
export const getRemotePcReceipt = id => apiRemote.get(`/receipts/${encodeURIComponent(id)}`).then(r => r.data)
export const listRemotePcFiles = path => apiRemote.get('/files', { params: { path } }).then(r => r.data)
export const downloadRemotePcFile = path => apiRemoteLong.get('/files/download', { params: { path }, responseType: 'blob' }).then(r => r.data)
export const prepareRemotePcUpload = (directory, filename, size) => apiRemote.post('/files/upload/prepare', { directory, filename, size }).then(r => r.data)
export const uploadRemotePcFile = (directory, file, confirmation_token) => {
  const form = new FormData()
  form.append('directory', directory)
  form.append('filename', file.name)
  form.append('size', String(file.size))
  form.append('confirmation_token', confirmation_token)
  form.append('file', file)
  return apiRemoteLong.post('/files/upload', form).then(r => r.data)
}
export const revealRemotePcPath = path => apiRemote.post('/files/reveal', { path }).then(r => r.data)

// Company Command Centre: CRM & Sales and Websites & Digital (tools/aos_crm.py, tools/aos_website.py).
export const getCrmOverview = (scope = 'global') => api.get('/crm/overview', { params: { scope } }).then(r => r.data)
export const getCrmHome = (scope = 'global') => api.get('/crm/home', { params: { scope } }).then(r => r.data)
export const getCrmRecord = (id, scope = 'global') => api.get(`/crm/records/${encodeURIComponent(id)}`, { params: { scope } }).then(r => r.data)
export const postCrmEvent = body => api.post('/crm/events', body, { timeout: 20000 }).then(r => r.data)
export const getWebsiteProjects = () => api.get('/website/projects', { timeout: 15000 }).then(r => r.data)
export const getWebsiteJob = id => api.get(`/website/jobs/${encodeURIComponent(id)}`, { timeout: 15000 }).then(r => r.data)
export const postWebsiteRequest = body => api.post('/website/requests', body, { timeout: 20000 }).then(r => r.data)
