// Website Studio releases: pure display helpers (no network, no model).
// Revisit: when release records or operation states change. Last touched: 2026-11-21.

export const TERMINAL = ['succeeded', 'noop', 'failed', 'reverted', 'interrupted']

export const STATUS_TEXT = {
  requested: 'Requested', staging: 'Staging deployment', staged: 'Deployment staged', promoting: 'Moving production alias',
  succeeded: 'Published and verified', noop: 'Already published (no deployment)', failed: 'Failed — production unchanged',
  reverted: 'Failed after promotion — previous version restored', interrupted: 'Interrupted — see observed state',
}

export const short = (value, n = 12) => (value ? String(value).slice(0, n) : '—')

export function statusText(status) {
  return STATUS_TEXT[status] || status
}

// What a target currently serves, in words.
export function currentText(target) {
  const current = target?.current
  if (!current) return 'Nothing published yet'
  if (current.release_id) return `Release ${current.release_id} · build ${current.build_id} · artifact ${short(current.artifact_sha256)}`
  return `External deployment ${current.deployment_id}${current.source_commit ? ` · source ${short(current.source_commit)}` : ''} (not a Studio release)`
}

export function versionsText(drafts = {}) {
  const label = { home: 'Homepage', site: 'Shared site' }
  return Object.entries(drafts).map(([page, v]) => `${label[page] || page} v${v.version}`).join(' · ')
}

// The single exact action a request names; the server issues and checks the same phrase.
export function canConfirm(plan, typed) {
  return Boolean(plan?.allowed && !plan.noop && plan.confirmation && typed.trim() === plan.phrase)
}

export function isTerminal(operation) {
  return TERMINAL.includes(operation?.status)
}
