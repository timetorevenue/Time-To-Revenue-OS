// Live website editor HTTP client. Every route is deterministic except /manager/turns, whose Job may
import { SITE_ID } from '../site-config.js'
// reach the website.manager agent's pinned model (only when that package is released).
const CSRF = {'X-TTROS-Connections': '1'}
export function liveClient(site = SITE_ID, fetcher = (...a) => fetch(...a)) {
 const base = `/api/website/studio/sites/${encodeURIComponent(site)}/live`
 async function call(path, options = {}) {
  const init = options.body === undefined ? {...options, headers: {...(options.method && options.method !== 'GET' ? CSRF : {}), ...options.headers}}
   : {...options, headers: {'Content-Type': 'application/json', ...CSRF, ...options.headers}, body: JSON.stringify(options.body)}
  const response = await fetcher(base + path, init)
  let value = null
  try { value = await response.json() } catch { value = null }
  if (!response.ok) {
   const detail = value?.detail
   throw Object.assign(new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`), {status: response.status})
  }
  return value
 }
 return {
  base,
  status: () => call(''),
  startImport: () => call('/import', {method: 'POST'}),
  rebase: commit => call('/rebase', {method: 'POST', body: {commit}}),
  model: () => call('/model'),
  coverage: () => call('/coverage'),
  capture: route => call(`/capture?route=${encodeURIComponent(route)}`),
  versions: () => call('/versions'),
  version: n => call(`/versions/${n}`),
  save: body => call('/versions', {method: 'POST', body}),
  restore: (n, base_version) => call(`/versions/${n}/restore`, {method: 'POST', body: {base_version}}),
  autosave: () => call('/autosave'),
  putAutosave: body => call('/autosave', {method: 'PUT', body}),
  dropAutosave: () => call('/autosave', {method: 'DELETE'}),
  assets: () => call('/assets'),
  upload: (name, data) => call('/assets/upload', {method: 'POST', body: {name, data}}),
  fromSite: path => call(`/assets/from-site?path=${encodeURIComponent(path)}`, {method: 'POST'}),
  fromDrive: file_id => call('/assets/from-drive', {method: 'POST', body: {file_id}}),
  build: (version, purpose) => call('/builds', {method: 'POST', body: {version, purpose}}),
  buildStatus: id => call(`/builds/${id}`),
  stopBuild: id => call(`/builds/${id}`, {method: 'DELETE'}),
  sourceFiles: () => call('/source/files'),
  sourceFile: path => call(`/source/file?path=${encodeURIComponent(path)}`),
  sourceDiff: body => call('/source/diff', {method: 'POST', body}),
  manager: () => call('/manager'),
  managerTurn: body => call('/manager/turns', {method: 'POST', body}),
  acceptProposal: (id, body) => call(`/manager/proposals/${id}/accept`, {method: 'POST', body}),
  rejectProposal: (id, body) => call(`/manager/proposals/${id}/reject`, {method: 'POST', body}),
  voice: () => call('/manager/voice'),
  transcribe: (audio, mime) => call('/manager/transcribe', {method: 'POST', body: {audio, mime}}),
  derive: body => call('/assets/derive', {method: 'POST', body}),
  mediaProviders: () => call('/media/providers'),
  sections: () => call('/sections'),
  saveSection: (name, node) => call('/sections', {method: 'POST', body: {name, node}}),
  canvasUrl: name => `${base}/canvas/${name}`,
  siteFileUrl: path => `${base}/site-file?path=${encodeURIComponent(path)}`,
  assetUrl: id => `${base}/assets/${id}`,
 }
}
