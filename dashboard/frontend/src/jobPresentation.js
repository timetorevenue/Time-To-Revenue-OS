// Presentation only. aos_jobs and its API remain the state and receipt authority.
export const needsAttention = (job) =>
  [
    'waiting_input',
    'needs_input',
    'human_review',
    'review',
    'failed',
    'interrupted',
    'refused',
    'uncertain',
    'blocked',
  ].includes(job.status) ||
  (job.status === 'queued' &&
    ['failed', 'uncertain'].includes(job.last_delivery?.status))
export const inMotion = (job) => ['queued', 'running'].includes(job.status)
export const hasReceipt = (job) =>
  (job.receipts || []).some(
    (row) =>
      row.path &&
      (job.reminder || row.status === job.status) &&
      /^[a-f0-9]{64}$/i.test(row.sha256 || ''),
  )
export function jobStatus(job) {
  if (job.status === 'done')
    return hasReceipt(job)
      ? { label: 'Completed', tone: 'success' }
      : { label: 'Receipt pending', tone: 'warning' }
  const labels = {
    queued: (job.reminder || job.scheduled_at) ? 'Scheduled' : 'Queued',
    running: 'Running',
    paused: 'Paused',
    waiting_input: 'Waiting for you',
    needs_input: 'Ended: needed input',
    human_review: 'Review',
    review: 'Review',
    failed: 'Failed',
    interrupted: 'Interrupted',
    refused: 'Refused',
    uncertain: 'Uncertain',
    blocked: 'Blocked',
    cancelled: 'Cancelled',
  }
  return {
    label: labels[job.status] || 'Unknown',
    tone: needsAttention(job) ? 'warning' : inMotion(job) ? 'neutral' : 'muted',
  }
}
export function jobSignals(jobs) {
  return {
    attention: jobs.filter(needsAttention).length,
    motion: jobs.filter(inMotion).length,
    receipts: jobs.filter((job) => job.status === 'done' && hasReceipt(job))
      .length,
  }
}
export function formatJobTime(value, timeZone = 'America/Vancouver') {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time unavailable'
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}
export function jobsFailure(error) {
  return error?.response?.status === 404
    ? 'The connected backend does not expose David jobs yet. Jobs and receipts are unavailable.'
    : 'Jobs could not be refreshed. Check the connection and try again.'
}
