// CRM & Sales: people, companies and pipeline from the prospect ledger and the CRM event log,
// with every value labelled confirmed / estimate / suggestion / draft. Writes go through
// POST /api/crm/events (tools/aos_crm.py); nothing here contacts anyone.
// Revisit: when aos_crm's projection or event kinds change. · Last touched: 2026-11-16.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, BookOpen, CircleAlert, FileCheck2, ListChecks, MessageCircle, Search, Users } from 'lucide-react'
import { getCrmOverview, getCrmRecord } from '../api'
import { ActionNotice, Field, useCrmAction } from './crmForms'
import { CreateOpportunity, DeliveryHandoff, OpportunityEditor, OpportunityValue } from './OpportunityForms'
import SalesPipeline from './SalesPipeline'
import { persistDraft } from '../askDavidState'
import {
  CONTACT_STAGES,
  ENGAGEMENT_TONE,
  basisLabel,
  davidPromptForRecord,
  nextStages,
  severityTone,
  sourceLabel,
  stageLabel,
  crmToday,
} from '../commandCentreState'

const failure = error => error?.response?.data?.detail || error?.message || 'The CRM is unavailable.'

export function Badge({ tone = 'muted', children }) {
  return <span className={`job-badge ${tone}`}>{children}</span>
}

export function SourceLink({ refValue, onNavigate }) {
  const label = sourceLabel(refValue)
  if (refValue.startsWith('business_brain:'))
    return <button className="cc-source" onClick={() => onNavigate('search', { tab: 'memory', record: refValue })}><BookOpen size={12} />{label}<span className="mono">{refValue.replace('business_brain:', '')}</span></button>
  if (/^AOS-\d{4}-\d+$/.test(refValue))
    return <button className="cc-source" onClick={() => onNavigate('work-queue', { selectedId: refValue })}><ListChecks size={12} />{label}<span className="mono">{refValue}</span></button>
  if (refValue.startsWith('https://')) {
    try {
      const url = new URL(refValue)
      return <a className="cc-source" href={url.href} target="_blank" rel="noreferrer"><ArrowUpRight size={12} />{label}<span className="mono">{url.hostname}</span></a>
    } catch { /* Preserve malformed historical references as text, without breaking the record. */ }
  }
  return <span className="cc-source static"><FileCheck2 size={12} />{label}<span className="mono">{refValue.replace('queue/prospects.jsonl#', '')}</span></span>
}

function LogInteraction({ record, onSaved }) {
  const [form, setForm] = useState({ channel: 'email', direction: 'outbound', occurred_on: crmToday(record), summary: '', basis: 'confirmed', source: 'casey:dashboard-entry' })
  const [state, submit] = useCrmAction(record, onSaved)
  const set = field => event => setForm(value => ({ ...value, [field]: event.target.value }))
  return (
    <form className="cc-form" onSubmit={async event => {
      event.preventDefault()
      const done = await submit('interaction.log', { channel: form.channel, direction: form.direction, occurred_on: form.occurred_on, summary: form.summary },
        form.basis, form.basis === 'confirmed' ? [form.source] : [], form.basis === 'draft' ? 'Draft recorded (nothing was sent).' : 'Interaction recorded.')
      if (done) setForm(value => ({ ...value, summary: '' }))
    }}>
      <h4>Log an interaction</h4>
      <div className="cc-form-row">
        <Field label="Channel"><select value={form.channel} onChange={set('channel')}>{['email', 'call', 'meeting', 'linkedin', 'message', 'note'].map(v => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Direction"><select value={form.direction} onChange={set('direction')}>{['outbound', 'inbound', 'internal'].map(v => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Date"><input type="date" value={form.occurred_on} onChange={set('occurred_on')} required /></Field>
        <Field label="This is"><select value={form.basis} onChange={set('basis')}><option value="confirmed">Something that happened</option><option value="draft">A draft (not sent)</option></select></Field>
      </div>
      <Field label="What happened"><textarea value={form.summary} onChange={set('summary')} required maxLength={600} rows={2} placeholder="e.g. Casey called; they asked for a proposal next week." /></Field>
      {form.basis === 'confirmed' && <Field label="Source (message, event, or your own entry)"><input value={form.source} onChange={set('source')} placeholder="casey:dashboard-entry, gmail:<id>, AOS-2026-…" /></Field>}
      <button className="cc-button" disabled={state.busy}>{state.busy ? 'Saving…' : 'Save interaction'}</button>
      <ActionNotice state={state} />
    </form>
  )
}

function NextAction({ record, onSaved }) {
  const initial = () => ({ text: record.next_action?.completed_at ? '' : record.next_action?.text || '',
    due: record.next_action?.completed_at ? crmToday(record) : record.next_action?.due || crmToday(record), owner: record.next_action?.owner || 'casey' })
  const [form, setForm] = useState(initial)
  useEffect(() => { setForm(initial()) }, [record.next_action?.event_id, record.next_action?.completed_at])
  const [state, submit] = useCrmAction(record, onSaved)
  const [doneState, complete] = useCrmAction(record, onSaved)
  const open = record.next_action && !record.next_action.completed_at
  return (
    <form className="cc-form" onSubmit={async event => {
      event.preventDefault()
      await submit('next_action.set', form, 'confirmed', [], 'Next action saved.')
    }}>
      <h4>Next action & follow-up</h4>
      {open && (
        <div className="cc-current">
          <p><strong>{record.next_action.text}</strong> · {record.next_action.owner || 'casey'} · due {record.next_action.due}</p>
          <button type="button" className="text-link" disabled={state.busy || doneState.busy} onClick={() => complete('follow_up.complete', { note: 'Done' }, 'confirmed', [], 'Follow-up marked done.')}>Mark done</button>
        </div>
      )}
      <div className="cc-form-row">
        <Field label="Next action"><input value={form.text} onChange={e => setForm(v => ({ ...v, text: e.target.value }))} required maxLength={300} placeholder="e.g. Send recap and proposal outline" /></Field>
        <Field label="Due"><input type="date" value={form.due} onChange={e => setForm(v => ({ ...v, due: e.target.value }))} required /></Field>
        <Field label="Owner"><select value={form.owner} onChange={e => setForm(v => ({ ...v, owner: e.target.value }))}><option value="casey">Casey</option></select></Field>
      </div>
      <p className="source-caption">Due dates use America/Vancouver. Saving records a task; it sends nothing.</p>
      <button className="cc-button" disabled={state.busy || doneState.busy}>{state.busy ? 'Saving…' : open ? 'Update next action' : 'Set next action'}</button>
      <ActionNotice state={state} /><ActionNotice state={doneState} />
    </form>
  )
}

function StageChange({ record, onSaved }) {
  const options = nextStages(record)
  const confirmed = (record.interactions || []).filter(row => row.basis === 'confirmed')
  const [form, setForm] = useState({ stage: options[0] || '', interaction: confirmed.at(-1)?.event_id || '', reason: '' })
  const [state, submit] = useCrmAction(record, onSaved)
  if (record.ledger_kind === 'outreach_pilot_v1')
    return <p className="data-notice">This outreach-pilot prospect changes stage through the pilot’s own event flow, not here.</p>
  if (!options.length) return <p className="data-notice">This opportunity is closed; its stage cannot change.</p>
  const needsInteraction = record.workflow !== 'sales' && CONTACT_STAGES.has(form.stage)
  return (
    <form className="cc-form" onSubmit={event => {
      event.preventDefault()
      submit('stage.change', { stage: form.stage, interaction: needsInteraction ? form.interaction : '', reason: form.reason }, 'confirmed', [], `Stage changed to ${stageLabel(form.stage)}.`)
    }}>
      <h4>Change stage</h4>
      <div className="cc-form-row">
        <Field label="New stage"><select value={form.stage} onChange={e => setForm(v => ({ ...v, stage: e.target.value }))}>{options.map(v => <option key={v} value={v}>{stageLabel(v)}</option>)}</select></Field>
        {needsInteraction && (
          <Field label="Based on interaction">
            <select value={form.interaction} onChange={e => setForm(v => ({ ...v, interaction: e.target.value }))} required>
              <option value="">Choose a confirmed interaction…</option>
              {confirmed.map(row => <option key={row.event_id} value={row.event_id}>{row.occurred_on} · {row.channel} · {row.summary.slice(0, 40)}</option>)}
            </select>
          </Field>
        )}
        <Field label={form.stage === 'lost' ? 'Why lost' : 'Reason'}><input value={form.reason} onChange={e => setForm(v => ({ ...v, reason: e.target.value }))} maxLength={300} required={record.workflow === 'sales' || form.stage === 'lost'} /></Field>
      </div>
      {needsInteraction && !confirmed.length && <p className="data-notice warning">Log the interaction this stage rests on first.</p>}
      {record.origin === 'prospect_ledger' && <p className="source-caption">Also appended to the prospect ledger, only if its validator accepts the change.</p>}
      <button className="cc-button" disabled={state.busy || (needsInteraction && !form.interaction)}>{state.busy ? 'Saving…' : 'Change stage'}</button>
      <ActionNotice state={state} />
    </form>
  )
}

function LinkEvidence({ record, onSaved }) {
  const [form, setForm] = useState({ ref: '', note: '' })
  const [state, submit] = useCrmAction(record, onSaved)
  return (
    <form className="cc-form" onSubmit={async event => {
      event.preventDefault()
      if (await submit('link.add', { note: form.note }, 'confirmed', [form.ref], 'Link recorded.')) setForm({ ref: '', note: '' })
    }}>
      <h4>Link a job, receipt or evidence</h4>
      <div className="cc-form-row">
        <Field label="Reference"><input value={form.ref} onChange={e => setForm(v => ({ ...v, ref: e.target.value }))} required placeholder="AOS-2026-…, business_brain:…, https://…" /></Field>
        <Field label="Note"><input value={form.note} onChange={e => setForm(v => ({ ...v, note: e.target.value }))} maxLength={200} /></Field>
      </div>
      <button className="cc-button" disabled={state.busy}>{state.busy ? 'Checking…' : 'Add link'}</button>
      <ActionNotice state={state} />
    </form>
  )
}

export function CrmRecord({ id, onBack, onNavigate, onChanged }) {
  const [state, setState] = useState({ loading: true })
  const generation = useRef(0)
  const load = useCallback(() => {
    const request = ++generation.current
    return getCrmRecord(id).then(record => {
      if (request === generation.current) setState({ record })
    }, error => {
      if (request === generation.current) setState(old => ({ ...old, loading: false, error: failure(error) }))
    })
  }, [id])
  useEffect(() => { load(); return () => { generation.current++ } }, [load])
  const saved = record => { generation.current++; setState({ record }); onChanged?.() }
  const record = state.record
  const askDavid = () => {
    const prompt = davidPromptForRecord(record)
    persistDraft(window.sessionStorage, prompt)
    onNavigate('cockpit', { draft: prompt, draftKey: Date.now() })
  }
  return (
    <section className="editorial-card job-detail cc-record">
      <button className="text-link" onClick={onBack}><ArrowLeft size={15} />Back to CRM</button>
      <button className="text-link" onClick={load}>Reload record</button>
      <p className="source-caption">If a save reports a changed record, reload and review the latest values before retrying.</p>
      {state.loading && <p role="status">Reading the CRM record…</p>}
      {state.error && <p className="data-notice warning" role="alert">{state.error}</p>}
      {record && (
        <>
          <div className="detail-title">
            <h3>{record.name || record.id}</h3>
            {record.type === 'opportunity' && <Badge tone="neutral">{stageLabel(record.stage)}</Badge>}
          </div>
          <p className="mono cc-id">{record.id}</p>
          {record.type === 'contact' && record.email && <p className="source-caption">Email: {record.email} · Guide access consent: {record.consent?.guide_access ? 'yes' : 'not recorded'} · Follow-up consent: {record.consent?.follow_up ? 'yes' : 'no'}</p>}
          {record.type === 'contact' && record.subscription_summary && <dl className="detail-meta">
            <div><dt>Newsletter</dt><dd>{record.subscription_summary.status} · {record.subscription_summary.marketing_eligible ? 'Permission confirmed' : 'Not eligible for marketing'}</dd></div>
            <div><dt>Email ownership</dt><dd>{record.subscription_summary.email_verified ? 'Verified' : 'Not verified'}</dd></div>
            <div><dt>Suppression</dt><dd>{record.subscription_summary.delivery_review_required ? 'Delivery review required; marketing held' : record.subscription_summary.suppression_reason || 'None recorded'}</dd></div>
            <div><dt>Delivery</dt><dd>{record.subscription_summary.delivery_status || 'Not sent'}</dd></div>
            <div><dt>Consent notice</dt><dd>{record.subscription_summary.notice_version || 'Guide only; no newsletter consent'}</dd></div>
          </dl>}
          {record.type === 'opportunity' && record.offer && <p className="source-caption">Free guide request: {record.offer}</p>}
          {(record.attention || []).map(item => (
            <p key={item.id} className="data-notice warning"><CircleAlert size={13} /> {item.reason} <strong>Next:</strong> {item.next_permitted_action}</p>
          ))}
          {record.type === 'opportunity' && (
            <>
              <dl className="detail-meta">
                <div><dt>Contact</dt><dd>{record.contact_record ? <button className="text-link" onClick={() => onNavigate('crm', { record: record.contact_record.id })}>{record.contact_record.name}</button> : 'Not recorded'}{record.contact_record?.role ? ` · ${record.contact_record.role}` : ''}</dd></div>
                <div><dt>Organisation</dt><dd>{record.organization_record ? <button className="text-link" onClick={() => onNavigate('crm', { record: record.organization_record.id })}>{record.organization_record.name}</button> : 'Not recorded'}{record.organization_record?.domain ? ` · ${record.organization_record.domain}` : ''}</dd></div>
                <div><dt>Stage</dt><dd>{stageLabel(record.stage)} · {basisLabel(record.stage_basis)} · since {record.stage_date || '—'}</dd></div>
                <div><dt>Owner</dt><dd>{record.owner === 'casey' ? 'Casey' : 'Not recorded'}</dd></div>
                <div><dt>Engagement</dt><dd><Badge tone={ENGAGEMENT_TONE[record.engagement] || 'muted'}>{record.engagement}</Badge></dd></div>
                <div><dt>Value</dt><dd>{record.value ? `${record.value.currency} ${record.value.amount.toLocaleString()} · ${basisLabel(record.value.basis)}` : 'Not recorded (never estimated automatically)'}</dd></div>
                <div><dt>Fit score</dt><dd>{record.fit?.score != null ? `${record.fit.score} · tier ${record.fit.tier} · ${basisLabel(record.fit.basis)}` : 'Not recorded'}</dd></div>
                <div><dt>Next action</dt><dd>{record.next_action && !record.next_action.completed_at ? `${record.next_action.text} · due ${record.next_action.due}` : 'None set'}</dd></div>
                <div><dt>Cadence due (ledger)</dt><dd>{record.cadence_due || 'None'}</dd></div>
              </dl>
              {record.outcome_reason && <p><strong>{record.stage === 'lost' ? 'Why lost' : 'Outcome reason'}:</strong> {record.outcome_reason}</p>}
              {record.suggested_next_action && <p className="data-notice"><strong>Suggestion</strong> (from {sourceLabel(record.suggested_next_action.source)}): {record.suggested_next_action.text}</p>}
              {record.signal?.summary && <p className="result-copy"><strong>Signal</strong> ({record.signal.date}): {record.signal.summary}</p>}
              {record.do_not_contact && <p className="data-notice warning">Do not contact. No outreach action is offered.</p>}
              <div className="cc-actions-bar">
                <button className="cc-button secondary" onClick={askDavid}><MessageCircle size={14} />Ask David to draft a follow-up</button>
                <small>Opens a draft for you to review in Ask David. David delegates drafting only to released agents; nothing is sent.</small>
              </div>
            </>
          )}
          <h3>Sources</h3>
          <div className="cc-sources">{(record.sources || []).map(ref => <SourceLink key={ref} refValue={ref} onNavigate={onNavigate} />)}</div>
          {record.type === 'opportunity' && (
            <>
              <h3>Linked jobs & results</h3>
              {!(record.jobs || []).length && <p className="source-caption">No job linked yet. Link one below once David starts it.</p>}
              {(record.jobs || []).map(job => (
                <button key={job.id} className="job-list-row" onClick={() => onNavigate('work-queue', { selectedId: job.id })}>
                  <span className="row-icon"><ListChecks size={17} /></span>
                  <span className="row-copy"><span className="mono job-id">{job.id}</span><strong>{job.request}</strong><small>{job.lifecycle}{job.receipts?.length ? ` · receipt ${job.receipts[0]}` : ' · no receipt yet'}</small></span>
                  <Badge tone={job.status === 'done' ? 'success' : ['failed', 'refused', 'waiting_input'].includes(job.status) ? 'warning' : 'neutral'}>{job.status}</Badge>
                </button>
              ))}
              <h3>Activity</h3>
              {!(record.interactions || []).length && !(record.history || []).length && <p>No activity recorded.</p>}
              <ol className="cc-timeline">
                {[...(record.interactions || []).map(row => ({ at: row.occurred_on, key: row.event_id, text: `${row.direction} ${row.channel}: ${row.summary}`, basis: row.basis, sources: row.sources })),
                  ...(record.events || []).filter(row => ['next_action.set', 'follow_up.complete'].includes(row.kind)).map(row => ({ at: row.at, key: row.event_id, text: row.kind === 'next_action.set' ? `Next action: ${row.payload.text} · ${row.payload.owner || 'casey'} · due ${row.payload.due}` : `Follow-up completed: ${row.payload.note}`, basis: row.basis })),
                  ...(record.events || []).filter(row => ['opportunity.update', 'value.set', 'handoff.prepare', 'link.add'].includes(row.kind)).map(row => ({ at: row.at, key: row.event_id, text: row.kind === 'opportunity.update' ? `Opportunity: ${row.payload.name} · ${row.payload.owner}` : row.kind === 'value.set' ? `Value: ${row.payload.currency} ${row.payload.amount}` : row.kind === 'handoff.prepare' ? `Delivery handoff prepared: ${row.payload.confirmed_scope}` : `Evidence linked: ${row.source_refs.join(', ')}`, basis: row.basis })),
                  ...(record.history || []).map((row, i) => ({ at: row.at, key: `h${i}`, text: `Stage: ${stageLabel(row.stage)}${row.reason ? ` — ${row.reason}` : ''}${row.ledger ? ' (prospect ledger updated)' : ''}`, basis: row.basis, sources: [row.source] }))]
                  .sort((a, b) => String(b.at).localeCompare(String(a.at)))
                  .map(row => (
                    <li key={row.key}><span className="mono">{row.at}</span> <Badge tone={row.basis === 'draft' ? 'warning' : 'muted'}>{basisLabel(row.basis)}</Badge> {row.text}</li>
                  ))}
              </ol>
              {!record.do_not_contact && (
                <div className="cc-forms">
                  <OpportunityEditor record={record} onSaved={saved} />
                  <OpportunityValue record={record} onSaved={saved} />
                  <LogInteraction record={record} onSaved={saved} />
                  <NextAction record={record} onSaved={saved} />
                  <StageChange key={`${record.stage}-${(record.interactions || []).length}`} record={record} onSaved={saved} />
                  <LinkEvidence record={record} onSaved={saved} />
                </div>
              )}
              <DeliveryHandoff record={record} onSaved={saved} onNavigate={onNavigate} renderSource={ref => <SourceLink key={ref} refValue={ref} onNavigate={onNavigate} />} />
            </>
          )}
          {record.type !== 'opportunity' && (record.opportunity_records || []).map(opp => (
            <button key={opp.id} className="job-list-row" onClick={() => onNavigate('crm', { record: opp.id })}>
              <span className="row-copy"><strong>{opp.name}</strong><small>{stageLabel(opp.stage)}</small></span>
            </button>
          ))}
          {record.type !== 'opportunity' && !(record.opportunity_records || []).length && <p>No opportunities linked to this record.</p>}
        </>
      )}
    </section>
  )
}

function OpportunityRow({ opp, attention, onOpen }) {
  const top = attention[0]
  return (
    <button className="job-list-row cc-row" onClick={onOpen}>
      <span className="row-icon"><Users size={17} /></span>
      <span className="row-copy">
        <strong>{opp.name}</strong>
        <small>{stageLabel(opp.stage)} · {opp.engagement}{opp.next_action && !opp.next_action.completed_at ? ` · next: ${opp.next_action.text} (${opp.next_action.due})` : ''}</small>
        {top && <small className="cc-attention-line"><CircleAlert size={11} /> {top.reason}</small>}
      </span>
      {top ? <Badge tone={severityTone(top.severity)}>{top.rule === 'overdue_follow_up' ? 'Overdue' : top.rule === 'upcoming_follow_up' ? 'Upcoming' : 'Needs attention'}</Badge> : <Badge tone="muted">Clear</Badge>}
    </button>
  )
}

export default function CrmSales({ initialFilters = {}, onViewParamsPush, onNavigate }) {
  const [state, setState] = useState({ loading: true })
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const filter = initialFilters.filter || 'attention'
  const selected = initialFilters.record
  const setParams = params => onViewParamsPush?.({ ...initialFilters, ...params })
  const load = useCallback(() => getCrmOverview().then(data => setState({ data, at: new Date() }), error => setState(old => ({ ...old, loading: false, error: failure(error) }))), [])
  useEffect(() => {
    load()
    const poll = window.setInterval(load, 30000)
    return () => window.clearInterval(poll)
  }, [load])
  const data = state.data
  const byRecord = {}
  for (const item of data?.attention || []) (byRecord[item.record_id] ||= []).push(item)
  const opportunities = Object.values(data?.opportunities || {})
    .filter(opp => opp.classification !== 'guide_interest')
    .filter(opp => filter === 'all' || (filter === 'open' && opp.open) || (filter === 'attention' && byRecord[opp.id]))
    .filter(opp => `${opp.name} ${opp.id}`.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => (byRecord[b.id]?.length || 0) - (byRecord[a.id]?.length || 0) || a.name.localeCompare(b.name))
  const directory = filter === 'subscribers' ? Object.values(data?.contacts || {}).filter(c => c.subscription?.consent_requested) : filter === 'contacts' ? Object.values(data?.contacts || {}) : filter === 'organizations' ? Object.values(data?.organizations || {}) : null
  const records = directory?.filter(record => `${record.name} ${record.id}`.toLowerCase().includes(query.toLowerCase()))
  return (
    <div className="jobs-page cc-page">
      <div className="page-heading">
        <p className="eyebrow">CRM & SALES</p>
        <h1>Relationships and pipeline</h1>
        <p>People, companies and opportunities from your prospect ledger and logged activity. Every value says whether it is confirmed, an estimate, a suggestion or a draft.</p>
      </div>
      {selected ? (
        <CrmRecord key={selected} id={selected} onBack={() => setParams({ record: undefined })} onNavigate={onNavigate} onChanged={load} />
      ) : (
        <>
          {data && (
            <div className="signal-grid cc-counts">
              {[['Needs attention', data.counts.attention, 'attention'], ['Open opportunities', data.counts.open_opportunities, 'open'],
                ['Confirmed subscribers', data.counts.subscribers || 0, 'subscribers'], ['Contacts', data.counts.contacts, 'contacts'], ['Organisations', data.counts.organizations, 'organizations']].map(([label, value, next]) => (
                <button key={label} className="signal-card" onClick={() => setParams({ filter: next })}>
                  <span className="signal-label">{label}</span>
                  <span className={`signal-value ${label === 'Needs attention' && value ? 'amber' : ''}`}>{value}</span>
                </button>
              ))}
            </div>
          )}
          {data && <>
            <button className="cc-button" onClick={() => setCreating(value => !value)}>{creating ? 'Cancel new opportunity' : 'New opportunity'}</button>
            {creating && <CreateOpportunity data={data} onCreated={record => { setCreating(false); load(); setParams({ record: record.id }) }} />}
          </>}
          <section className="editorial-card">
            <div className="jobs-toolbar">
              <div className="filter-tabs" aria-label="Filter opportunities">
                {[['pipeline', 'Pipeline'], ['attention', 'Needs attention'], ['open', 'Open'], ['all', 'All opportunities'], ['contacts', 'Contacts'], ['subscribers', 'Subscribers'], ['organizations', 'Organisations']].map(([id, label]) => (
                  <button key={id} aria-pressed={filter === id} onClick={() => setParams({ filter: id })}>{label}</button>
                ))}
              </div>
              <label className="job-search"><Search size={15} /><input aria-label="Search CRM" placeholder="Search name or company…" value={query} onChange={e => setQuery(e.target.value)} /></label>
            </div>
            {state.loading && !data && <p className="data-notice" role="status">Reading the CRM…</p>}
            {state.error && <p className="data-notice warning" role="alert">{data ? 'Stale CRM data. ' : ''}{state.error}</p>}
            {records?.map(record => <button key={record.id} className="job-list-row" onClick={() => setParams({ record: record.id })}><span className="row-copy"><strong>{record.name || record.id}</strong><small>{filter === 'subscribers' ? `${data.subscribers?.[record.id]?.status || 'guide_only'} · ${data.subscribers?.[record.id]?.marketing_eligible ? 'marketing permission confirmed' : 'not eligible for marketing'}` : `${record.role || record.domain || record.id} · ${record.opportunities?.length || 0} opportunities`}</small></span></button>)}
            {data && filter === 'pipeline' && <SalesPipeline data={data} query={query} onOpen={id => setParams({ record: id })} />}
            {data && filter !== 'pipeline' && !(records || opportunities).length && (
              <div className="calm-empty"><Users size={26} /><h2>{directory ? 'No matching records.' : filter === 'attention' ? 'Nothing needs you here.' : 'No opportunities recorded.'}</h2><p>Records come from the prospect ledger and what you log here.</p></div>
            )}
            {!directory && filter !== 'pipeline' && opportunities.map(opp => <OpportunityRow key={opp.id} opp={opp} attention={byRecord[opp.id] || []} onOpen={() => setParams({ record: opp.id })} />)}
            {data && (
              <p className="source-caption">
                Sources: prospect ledger ({data.sources.prospect_ledger.records} prospects) · CRM log ({data.sources.crm_events.records} events) · as of {data.as_of}. {data.value_note}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  )
}
