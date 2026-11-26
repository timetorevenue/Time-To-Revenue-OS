// Read and manage the same durable Jobs David uses.
import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowUpRight, FileCheck2, Search } from 'lucide-react'
import { changeDavidAutomation, getDavidJob } from '../api'
import {
  formatJobTime,
  hasReceipt,
  inMotion,
  needsAttention,
} from '../jobPresentation'
import { JobBadge, JobsNotice } from './CompanyHome'

function AutomationDetail({ rule, onOpenJob, onRefresh }) {
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { trigger, action } = rule.definition
  const latest = rule.last_job
  async function change(operation) {
    setError('')
    setBusy(true)
    try {
      const definition = operation === 'edit' ? {
        ...rule.definition,
        title: draft.title,
        trigger: trigger.type === 'schedule' ? { type: 'schedule', when: draft.when } : JSON.parse(draft.trigger),
        action: JSON.parse(draft.action),
        notify: draft.notify,
        ...(draft.expires ? { expires_at: draft.expires } : {}),
      } : undefined
      if (definition && !draft.expires) delete definition.expires_at
      await changeDavidAutomation(rule.id, operation, draft?.revision ?? rule.revision, definition)
      setDraft(null)
      onRefresh()
    } catch (failure) {
      setError(failure.response?.data?.detail || failure.message || 'Could not update the automation.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="job-result-block">
      <h3>{trigger.type === 'schedule' ? 'Scheduled workflow' : 'Waiting condition'}</h3>
      <p>{trigger.when || [trigger.source?.replaceAll('_', ' '), trigger.target].filter(Boolean).join(' · ')}</p>
      {trigger.where?.map(({ field, op, value }, index) => (
        <p className="mono" key={index}>{field} {op} {value === undefined ? '' : JSON.stringify(value)}</p>
      ))}
      <p>Action {action.ref || [action.service, action.operation].filter(Boolean).join(' ') || action.type}</p>
      {rule.next_execution && <p>Next run {formatJobTime(rule.next_execution)} PT</p>}
      {rule.next_check && !rule.next_execution && <p>Next check {formatJobTime(rule.next_check)} PT</p>}
      {rule.definition.expires_at && <p>Expires {formatJobTime(rule.definition.expires_at)} PT</p>}
      {rule.missed > 0 && <p>Missed execution windows {rule.missed}</p>}
      {rule.detail && <p>{rule.detail}</p>}
      {rule.last_event?.reference && <p>Last event {rule.last_event.reference}</p>}
      {latest && (
        <>
          <h3>Last execution</h3>
          <button type="button" className="text-link" onClick={() => onOpenJob(latest.id)}>{latest.id}</button>
          <JobBadge job={latest} />
          <p>{latest.result?.summary || latest.detail || 'Execution is pending.'}</p>
          {latest.usage?.exact && <p>{latest.usage.model_calls} model calls · {latest.usage.total_tokens} tokens</p>}
          {latest.receipts?.map((row) => <p key={row.path} className="mono">{row.path}</p>)}
        </>
      )}
      {['queued', 'paused'].includes(rule.status) && (
        <div className="filter-tabs" aria-label="Manage automation">
          <button disabled={busy} onClick={() => change(rule.status === 'paused' ? 'resume' : 'pause')}>
            {rule.status === 'paused' ? 'Resume' : 'Pause'}
          </button>
          <button disabled={busy} onClick={() => setDraft({
            revision: rule.revision, title: rule.definition.title, when: trigger.when,
            trigger: JSON.stringify(trigger, null, 2), action: JSON.stringify(action, null, 2),
            expires: rule.definition.expires_at || '', notify: rule.definition.notify,
          })}>Edit</button>
          <button disabled={busy} onClick={() => change('cancel')}>Cancel automation</button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {draft && (
        <form className="detail-meta" onSubmit={(event) => { event.preventDefault(); change('edit') }}>
          <label>Title <input required maxLength={160} value={draft.title}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
          {trigger.type === 'schedule' ? (
            <label>Schedule <input required value={draft.when}
              onChange={(event) => setDraft({ ...draft, when: event.target.value })} /></label>
          ) : (
            <label>Event and matching fields <textarea required rows={8} value={draft.trigger}
              onChange={(event) => setDraft({ ...draft, trigger: event.target.value })} /></label>
          )}
          <label>Delivery <select value={draft.notify} onChange={(event) => setDraft({ ...draft, notify: event.target.value })}>
            <option value="dashboard">Dashboard</option>
            <option value="telegram">Operator Telegram</option>
          </select></label>
          <label>Expires (ISO date and time with offset) <input value={draft.expires}
            onChange={(event) => setDraft({ ...draft, expires: event.target.value })} /></label>
          <label>Action <textarea required rows={5} value={draft.action}
            onChange={(event) => setDraft({ ...draft, action: event.target.value })} /></label>
          <button type="submit" disabled={busy}>Save changes</button>
          <button type="button" disabled={busy} onClick={() => setDraft(null)}>Discard edit</button>
        </form>
      )}
    </div>
  )
}

function JobDetail({ id, onBack, onOpenJob }) {
  const [state, setState] = useState({ loading: true })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    setState({ loading: true })
    let pending = false
    const read = async () => {
      if (pending) return
      pending = true
      try {
        const job = await getDavidJob(id)
        if (alive) setState({ job })
      } catch {
        if (alive)
          setState((previous) => ({
            ...previous,
            loading: false,
            error: previous.job
              ? 'Stale job record. The latest refresh failed.'
              : 'This job is unavailable. Its ID may be unknown or the backend may not support it.',
          }))
      } finally {
        pending = false
      }
    }
    read()
    const poll = window.setInterval(read, 15000)
    return () => {
      alive = false
      window.clearInterval(poll)
    }
  }, [id, attempt])
  const job = state.job
  return (
    <section className="editorial-card job-detail">
      <button className="text-link" onClick={onBack}>
        <ArrowLeft size={15} />
        Back to jobs
      </button>
      <h2 className="mono">{id}</h2>
      {state.loading && <p role="status">Reading the job record…</p>}
      {state.error && (
        <div role="alert">
          <p>{state.error}</p>
          <button
            className="text-link"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      )}
      {job && (
        <>
          <div className="detail-title">
            <h3>{job.reminder?.text || job.request || job.topic || 'Untitled request'}</h3>
            <JobBadge job={job} />
          </div>
          {job.automation && <AutomationDetail rule={job.automation} onOpenJob={onOpenJob}
            onRefresh={() => setAttempt((value) => value + 1)} />}
          {job.automation_origin && <button className="text-link" onClick={() => onOpenJob(job.automation_origin)}>
            Open automation {job.automation_origin}
          </button>}
          <dl className="detail-meta">
            <div>
              <dt>Topic</dt>
              <dd>{job.topic || 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Conversation</dt>
              <dd className="mono">{job.conversation_id || 'Not recorded'}</dd>
            </div>
            <div>
              <dt>Created · PT</dt>
              <dd>{formatJobTime(job.created_at)}</dd>
            </div>
            <div>
              <dt>Finished · PT</dt>
              <dd>{formatJobTime(job.finished_at)}</dd>
            </div>
            {job.scheduled_at && (
              <div>
                <dt>Scheduled · UTC</dt>
                <dd>{formatJobTime(job.scheduled_at, 'UTC')}</dd>
              </div>
            )}
            {job.reminder && inMotion(job) && (
              <div>
                <dt>{job.status === 'running' ? 'Due' : 'Next due'} · {job.reminder.timezone}</dt>
                <dd>{formatJobTime(job.reminder.next_at, job.reminder.timezone)}</dd>
              </div>
            )}
          </dl>
          {job.detail && <p className="data-notice warning">{job.detail}</p>}
          <h3>Result</h3>
          {job.result ? (
            <p className="result-copy">
              {typeof job.result === 'string'
                ? job.result
                : job.result.summary || 'No result summary recorded.'}
            </p>
          ) : (
            <p>No durable result is recorded yet.</p>
          )}
          {job.status === 'done' && !hasReceipt(job) && (
            <p className="data-notice warning">
              The job reports done, but a matching receipt is not recorded.
              Completion is unverified.
            </p>
          )}
          <h3>Receipts & evidence</h3>
          {!(job.receipts || []).length && <p>No receipt recorded.</p>}
          {(job.receipts || []).map((row) => (
            <div className="evidence-record" key={row.path}>
              <FileCheck2 size={18} />
              <div>
                <strong>{row.path}</strong>
                <p>
                  {row.status || 'Status unknown'} ·{' '}
                  {formatJobTime(row.created_at)} PT
                </p>
                <small className="mono">
                  SHA-256: {row.sha256 || 'Not recorded'}
                </small>
              </div>
            </div>
          ))}
          <p className="source-caption">
            Receipt metadata is reported by aos_jobs. File contents are not
            exposed by this API.
          </p>
          <h3>Artifacts</h3>
          {!(job.artifacts || []).length && <p>No artifacts recorded.</p>}
          {(job.artifacts || []).map((row) => (
            <div className="evidence-record" key={row.path}>
              <div>
                <strong>{row.path}</strong>
                <small className="mono">
                  SHA-256: {row.sha256 || 'Not recorded'}
                </small>
              </div>
            </div>
          ))}
          <h3>Effects & notification</h3>
          {(job.effects || []).map((row, index) => (
            <p key={row.key || index}>
              {row.action}: {row.status || 'Unknown'}
            </p>
          ))}
          {job.reminder ? (
            <p>
              Last Telegram delivery: {job.last_delivery?.status || 'Pending'}
              {job.last_delivery?.message_id ? ` · Message ${job.last_delivery.message_id}` : ''}
            </p>
          ) : (
            <p>Completion notice: {job.notification?.status || 'Not recorded'}</p>
          )}
          <p className="source-caption">
            {job.accounting?.invocation_count
              ? `Tokens: ${job.accounting.known_tokens ?? 'unavailable'} known · ${job.accounting.unknown_invocations ?? 'unknown'} invocations unmetered`
              : job.usage?.exact
                ? `Tokens: ${job.usage.total_tokens}`
                : 'Token usage: unavailable'}
          </p>
          <a
            className="text-link"
            href={`/api/david/jobs/${encodeURIComponent(id)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open original job JSON <ArrowUpRight size={14} />
          </a>
        </>
      )}
    </section>
  )
}
export default function DavidJobs({
  jobsState,
  initialFilters = {},
  onViewParamsPush,
  resultsMode = false,
}) {
  const state = jobsState
  const [query, setQuery] = useState('')
  const filter = initialFilters.filter || (resultsMode ? 'receipts' : 'all')
  const selected = initialFilters.selectedId
  const setParams = (params) =>
    onViewParamsPush?.({ ...initialFilters, ...params })
  const filtered = state.jobs.filter(
    (job) =>
      (filter === 'all' ||
        (filter === 'attention' && needsAttention(job)) ||
        (filter === 'active' && inMotion(job)) ||
        (filter === 'automations' && job.automation) ||
        (filter === 'receipts' && hasReceipt(job))) &&
      `${job.id} ${job.request} ${job.topic} ${job.reminder?.text || ''}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  )
  return (
    <div className="jobs-page">
      <div className="page-heading">
        <p className="eyebrow">
          {resultsMode ? 'EVIDENCE & OUTCOMES' : 'ACTIVITY & EXECUTION'}
        </p>
        <h1>{resultsMode ? 'Results & receipts' : 'Jobs and results'}</h1>
        <p>
          {resultsMode
            ? 'Recorded outcomes, preserved evidence and a clear trail back to the work.'
            : 'Follow the work. See what is moving, what needs you and what came back.'}
        </p>
      </div>
      {selected ? (
        <JobDetail
          key={selected}
          id={selected}
          onBack={() => setParams({ selectedId: undefined })}
          onOpenJob={(selectedId) => setParams({ selectedId })}
        />
      ) : (
        <section className="editorial-card">
          <div className="jobs-toolbar">
            <div className="filter-tabs" aria-label="Filter jobs">
              {[
                ['all', 'All jobs'],
                ['active', 'In motion'],
                ['automations', 'Automations'],
                ['attention', 'Needs attention'],
                ['receipts', 'With receipts'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  aria-pressed={filter === id}
                  onClick={() => setParams({ filter: id })}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="job-search">
              <Search size={15} />
              <input
                aria-label="Search jobs"
                placeholder="Search title or exact ID…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </div>
          <JobsNotice state={state} />
          {!state.loading && !state.error && !filtered.length && (
            <div className="calm-empty">
              <FileCheck2 size={28} />
              <h2>
                {state.jobs.length ? 'No matching jobs.' : 'No David jobs yet.'}
              </h2>
              <p>
                {state.jobs.length
                  ? 'Try another filter or search.'
                  : 'Delegated work will appear here with its permanent ID and evidence.'}
              </p>
            </div>
          )}
          {filtered.map((job) => (
            <button
              key={job.id}
              className="job-list-row"
              onClick={() => setParams({ selectedId: job.id })}
            >
              <span className="row-icon">
                <FileCheck2 size={19} />
              </span>
              <span className="row-copy">
                <span className="mono job-id">{job.id}</span>
                <strong>
                  {job.reminder?.text || job.request || job.topic || 'Untitled request'}
                </strong>
                <small>
                  {job.automation
                    ? job.automation.next_execution
                      ? `Next run ${formatJobTime(job.automation.next_execution)} PT`
                      : job.automation.waiting_for?.replaceAll('_', ' ') || job.automation.status
                    : job.reminder && inMotion(job)
                    ? `Due ${formatJobTime(job.reminder.next_at, job.reminder.timezone)} · ${job.reminder.timezone}`
                    : `${job.topic || 'No topic recorded'} · ${formatJobTime(job.finished_at || job.created_at)} PT`}
                </small>
              </span>
              <span className="job-list-end">
                <JobBadge job={job} />
                <small>
                  {hasReceipt(job) ? 'Receipt recorded' : 'No verified receipt'}
                </small>
              </span>
              <ArrowUpRight size={16} />
            </button>
          ))}
          <div className="list-footer">
            <span>
              {state.fetchedAt
                ? `Last read ${formatJobTime(state.fetchedAt)} PT${state.error ? ' · Stale' : ''} · Latest ${state.jobs.length} returned jobs`
                : 'No verified job read'}
            </span>
            <button className="text-link" onClick={state.refresh}>
              Refresh
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
export function DavidStatus({ cockpit }) {
  if (!cockpit) return <p role="alert">Runtime status is unavailable.</p>
  return (
    <section className="editorial-card card-body">
      <h1>System status</h1>
      <p>API: {cockpit.status || 'Unknown'}</p>
      <p>
        David:{' '}
        {typeof cockpit.david?.ready === 'boolean'
          ? cockpit.david.ready
            ? 'Ready'
            : 'Starting'
          : 'Unavailable from connected backend'}
      </p>
      <p>Running jobs: {cockpit.david?.jobs_running ?? 'Unavailable'}</p>
      <p>Queued jobs: {cockpit.david?.jobs_queued ?? 'Unavailable'}</p>
      <p className="source-caption">
        Read from /api/health. This view does not control the runtime.
      </p>
    </section>
  )
}
