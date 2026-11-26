// Company Command Centre presentation rules: departments, CRM labels, CRM write bodies,
// David prompts and website job status. Pure functions only; the backend is the authority.
// Revisit: when aos_crm event kinds, aos_website outcome fields or the seven departments change. · Last touched: 2026-11-16.

// The seven departments. Each opens a real destination or states plainly that no source is connected.
export const DEPARTMENTS = [
  { id: 'crm', label: 'CRM & Sales', view: 'crm', detail: 'Contacts, pipeline, follow-ups' },
  { id: 'clients', label: 'Clients & Projects', view: 'search', params: { tab: 'memory' }, detail: 'Client and project records in the Business Brain' },
  { id: 'websites', label: 'Websites & Digital', view: 'websites', detail: 'Scoped site changes, tests and previews' },
  { id: 'marketing', label: 'Marketing & Growth', view: 'search', params: { tab: 'memory' }, detail: 'Voice and content records; drafting agents are not released yet' },
  { id: 'finance', label: 'Finance & Commercial', view: null, detail: 'No verified finance source is connected' },
  { id: 'operations', label: 'Operations & Agents', view: 'work-queue', detail: 'Jobs, receipts and system status' },
  { id: 'research', label: 'Research & Intelligence', view: 'graphify', detail: 'Search, Graphify and source evidence' },
]

export const BASIS_LABELS = {
  confirmed: 'Confirmed',
  estimate: 'Estimate',
  suggestion: 'Suggestion',
  draft: 'Draft — not sent',
}
export const basisLabel = basis => BASIS_LABELS[basis] || 'Unlabelled'

export const STAGE_LABELS = {
  discovery: 'Discovery', qualified: 'Qualified', proposal: 'Proposal', negotiation: 'Negotiation', outreach: 'Outreach / other',
  identified: 'Identified', drafted: 'Draft awaiting Casey', sent: 'First touch sent', touch_2: 'Follow-up 1 sent',
  touch_3: 'Final touch sent', replied_positive: 'Replied — interested', replied_negative: 'Replied — declined',
  no_response: 'No response', call_booked: 'Call booked', fit_call_done: 'Fit call done', offer_sent: 'Offer sent',
  won: 'Won', lost: 'Lost', nurture: 'Nurture', withdrawn: 'Withdrawn', rejected: 'Rejected', do_not_contact: 'Do not contact',
}
export const stageLabel = stage => STAGE_LABELS[stage] || stage || 'Unknown stage'

// Same transitions as tools/validate-prospect-ledger.py; the backend still decides.
export const NEXT_STAGES = {
  identified: ['drafted', 'rejected', 'do_not_contact'],
  drafted: ['sent', 'rejected', 'do_not_contact'],
  sent: ['touch_2', 'replied_positive', 'replied_negative', 'do_not_contact', 'withdrawn'],
  touch_2: ['touch_3', 'replied_positive', 'replied_negative', 'do_not_contact'],
  touch_3: ['replied_positive', 'replied_negative', 'no_response', 'do_not_contact'],
  replied_positive: ['call_booked', 'nurture', 'lost', 'do_not_contact'],
  call_booked: ['fit_call_done', 'lost', 'nurture', 'do_not_contact'],
  fit_call_done: ['offer_sent', 'lost', 'nurture', 'do_not_contact'],
  offer_sent: ['won', 'lost', 'nurture', 'do_not_contact'],
  nurture: ['replied_positive', 'call_booked', 'lost', 'do_not_contact'],
  withdrawn: ['sent', 'rejected', 'do_not_contact'],
}
// Moving here says contact happened: the backend requires a confirmed interaction first.
export const CONTACT_STAGES = new Set(['sent', 'touch_2', 'touch_3', 'replied_positive', 'replied_negative', 'call_booked', 'fit_call_done', 'offer_sent', 'won'])
const SALES_NEXT = { discovery: ['qualified', 'lost'], qualified: ['discovery', 'proposal', 'lost'], proposal: ['qualified', 'negotiation', 'won', 'lost'], negotiation: ['proposal', 'won', 'lost'] }
export const nextStages = opp => (opp?.ledger_kind === 'outreach_pilot_v1' ? [] : (opp?.workflow === 'sales' ? SALES_NEXT : NEXT_STAGES)[opp?.stage] || [])

export const SEVERITY_TONE = { high: 'warning', medium: 'neutral', low: 'muted' }
export const severityTone = severity => SEVERITY_TONE[severity] || 'muted'
export const crmAttentionLabel = rule => ({
  overdue_follow_up: 'Overdue follow-up', follow_up_due_today: 'Due today', upcoming_follow_up: 'Upcoming follow-up',
  no_next_action: 'Missing next action', draft_awaiting_casey: 'Draft awaiting review', dormant: 'Dormant relationship',
  linked_job_needs_attention: 'Job needs attention',
  handoff_needed: 'Delivery handoff needed', stalled_opportunity: 'Stalled opportunity',
}[rule] || 'Needs review')

export const ENGAGEMENT_TONE = { active: 'success', cooling: 'neutral', dormant: 'warning', closed: 'muted' }

export function sourceLabel(ref) {
  const value = String(ref || '')
  if (value.startsWith('queue/prospects.jsonl#')) return 'Prospect ledger'
  if (value.startsWith('business_brain:')) return 'Business Brain'
  if (/^AOS-\d{4}-\d+$/.test(value)) return 'Job'
  if (value.startsWith('queue/receipts/')) return 'Receipt'
  if (value.startsWith('https://')) return 'External page'
  if (value.startsWith('gmail:')) return 'Gmail message'
  if (value.startsWith('gcal:')) return 'Calendar event'
  if (value.startsWith('drive:')) return 'Drive file'
  if (value === 'casey:dashboard-entry') return "Casey's own entry"
  return 'Source'
}

// A request key makes a retried click record once (aos_crm idempotency).
export const newRequestKey = (prefix = 'dash') => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`

export function crmEventBody({ kind, recordId, payload = {}, basis = 'confirmed', sourceRefs = [], requestKey, scope = 'global', expectedRevision }) {
  return {
    client_scope: scope,
    kind,
    record_id: recordId,
    payload,
    basis,
    source_refs: sourceRefs.map(ref => String(ref).trim()).filter(Boolean),
    request_key: requestKey || newRequestKey('crm'),
    ...(expectedRevision ? { expected_revision: expectedRevision } : {}),
  }
}

export const todayIso = (now = new Date()) => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Vancouver', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(now)
// Prefer the server's business date: browser ICU and Linux tzdata may differ.
export const crmToday = (record, now = new Date()) => record?.as_of || todayIso(now)

// Ask David about a record: a draft Casey reviews; the exact Brain page goes with it when one is linked.
export function davidPromptForRecord(opp) {
  if (!opp) return ''
  const who = opp.contact_record?.name || opp.name || opp.id
  const company = opp.organization_record?.name ? ` at ${opp.organization_record.name}` : ''
  const page = opp.brain_page ? ` (${opp.brain_page})` : ''
  return `Draft a follow-up email for ${who}${company}${page}. Stage: ${stageLabel(opp.stage)}. Keep it as a draft for my review; do not send anything.`
}

export function websiteStatus(view) {
  if (view?.readiness === 'blocked') return { label: 'Blocked · review evidence', tone: 'warning' }
  if (view?.readiness === 'validated') return { label: 'Validated · publish held', tone: 'success' }
  const status = view?.status
  if (status === 'done' && view?.publish?.status === 'held') return { label: 'Ready for review · publish held', tone: 'success' }
  if (status === 'done') return { label: 'Done', tone: 'success' }
  if (status === 'queued' || status === 'running') return { label: status === 'queued' ? 'Queued' : 'Working', tone: 'neutral' }
  if (status === 'failed' || status === 'refused' || status === 'interrupted') return { label: `Stopped · ${view?.failure_class || status}`, tone: 'warning' }
  return { label: status || 'Unknown', tone: 'muted' }
}

export const PUBLISH_COPY = {
  held: 'Held. Publishing needs your explicit command naming the action and target; this workflow never publishes.',
  not_ready: 'Not ready. There is no verified, tested preview to publish.',
}
export const publishCopy = publish => PUBLISH_COPY[publish?.status] || publish?.detail || 'Unknown'
