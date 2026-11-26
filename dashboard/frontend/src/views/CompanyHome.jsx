import { useEffect, useState } from 'react'
import {
  ArrowDownLeft,
  CircleAlert,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  CircleCheck,
  Clock3,
  Inbox,
  Layers3,
  Network,
  Search,
  Sparkles,
} from 'lucide-react'
import {
  formatJobTime,
  jobSignals,
  jobStatus,
  needsAttention,
} from '../jobPresentation'
import { persistDraft } from '../askDavidState'
import { getCrmHome, getWebsiteProjects } from '../api'
import { DEPARTMENTS, crmAttentionLabel, severityTone, websiteStatus } from '../commandCentreState'
import { SourceLink } from './CrmSales'
import { PipelineSummary } from './SalesPipeline'

export function SalesAttention({ crm, error, onNavigate }) {
  return <>
    {error && <p className="data-notice warning" role="alert">{crm ? 'Stale CRM data. ' : ''}{error}</p>}
    {!crm && !error && <p role="status">Reading the CRM…</p>}
    {crm && <p className="source-caption">Read {formatJobTime(crm.read_at)} PT · Due dates: {crm.timezone} · Showing {crm.attention.length} of {crm.counts.attention} attention items.</p>}
    {crm && !crm.counts.opportunities && <p>No CRM opportunities connected. Add or connect records before tracking follow-ups.</p>}
    {crm && crm.counts.opportunities > 0 && !crm.attention.length && !error && <p>No sales attention items in the connected records.</p>}
    {crm?.attention.map(item => <div key={item.id}>
      <button className="cc-attention" onClick={() => onNavigate('crm', { record: item.record_id })}>
        <CircleAlert size={13} /><span>
          <strong>{item.title}</strong><small>{item.reason}</small>
          <small>Owner: {item.owner === 'casey' ? 'Casey' : item.owner || 'Not recorded'} · {crmAttentionLabel(item.rule)}</small>
          <small>{item.next_permitted_action}</small>
          <small>Last confirmed interaction: {item.last_interaction || 'Not recorded'}</small>
        </span><span className={`job-badge ${severityTone(item.severity)}`}>{item.severity}</span>
      </button>
      <div className="cc-sources">{item.sources?.length ? item.sources.map(ref => <SourceLink key={ref} refValue={ref} onNavigate={onNavigate} />) : <small>No source linked.</small>}</div>
      {item.job_id && <button className="text-link" onClick={() => onNavigate('work-queue', { selectedId: item.job_id })}>Open job {item.job_id}</button>}
    </div>)}
  </>
}

// Live department signals for Home; each read fails visibly, never as a made-up number.
function useCommandCentre() {
  const [state, setState] = useState({ crm: null, crmError: '', web: null, webError: '' })
  useEffect(() => {
    let alive = true
    const read = () => {
      getCrmHome().then(crm => alive && setState(old => ({ ...old, crm, crmError: '' })),
        error => alive && setState(old => ({ ...old, crmError: error?.response?.data?.detail || 'CRM unavailable' })))
      getWebsiteProjects().then(web => alive && setState(old => ({ ...old, web, webError: '' })),
        error => alive && setState(old => ({ ...old, webError: error?.response?.data?.detail || 'Website workflow unavailable' })))
    }
    read()
    const poll = window.setInterval(read, 30000)
    return () => { alive = false; window.clearInterval(poll) }
  }, [])
  return state
}

function departmentStatus(id, centre, signals, known) {
  if (id === 'crm') return centre.crm ? `${centre.crm.counts.attention} need attention · ${centre.crm.counts.open_opportunities} open` : centre.crmError || 'Reading…'
  if (id === 'websites') {
    if (!centre.web) return centre.webError || 'Reading…'
    const ready = centre.web.projects.filter(row => row.state === 'ready').length
    const moving = centre.web.jobs.filter(job => ['queued', 'running'].includes(job.status)).length
    const held = centre.web.jobs.filter(job => job.publish?.status === 'held').length
    return `${ready} site(s) ready · ${moving} in progress · ${held} awaiting publish decision`
  }
  if (id === 'operations') return known ? `${signals.motion} in motion · ${signals.attention} need attention` : 'Reading jobs…'
  return null
}

function Departments({ onNavigate, centre, signals, known }) {
  return (
    <section className="cc-departments" aria-label="Departments">
      {DEPARTMENTS.map(dept => {
        const status = departmentStatus(dept.id, centre, signals, known)
        const body = (
          <>
            <strong>{dept.label}</strong>
            <small>{status || dept.detail}</small>
          </>
        )
        return dept.view ? (
          <button key={dept.id} className="cc-department" data-department={dept.id} onClick={() => onNavigate(dept.view, dept.params || {})}>{body}<ArrowUpRight size={13} /></button>
        ) : (
          <div key={dept.id} className="cc-department unavailable" data-department={dept.id}>{body}</div>
        )
      })}
    </section>
  )
}

export function JobBadge({ job }) {
  const { label, tone } = jobStatus(job)
  return <span className={`job-badge ${tone}`}>{label}</span>
}
export function JobsNotice({ state }) {
  if (state.loading)
    return (
      <p className="data-notice" role="status">
        Reading David jobs…
      </p>
    )
  if (state.error)
    return (
      <div className="data-notice warning" role="alert">
        <p>
          {state.fetchedAt ? 'Stale data. ' : ''}
          {state.error}
        </p>
        <button className="text-link" onClick={state.refresh}>
          Try again <ArrowRight size={13} />
        </button>
      </div>
    )
  return null
}
function JobRow({ job, onNavigate }) {
  return (
    <button
      className="home-job-row"
      onClick={() => onNavigate('work-queue', { selectedId: job.id })}
    >
      <span className="row-icon">
        <ArrowDownLeft size={16} />
      </span>
      <span className="row-copy">
        <strong>{job.request || job.topic || 'Untitled request'}</strong>
        <small>
          <span className="mono">{job.id}</span> ·{' '}
          {formatJobTime(job.finished_at || job.created_at)} PT
        </small>
      </span>
      <JobBadge job={job} />
    </button>
  )
}
export default function CompanyHome({ jobsState, onNavigate }) {
  const state = jobsState
  const centre = useCommandCentre()
  const signals = jobSignals(state.jobs)
  const known = !state.loading && (!state.error || state.fetchedAt)
  const priorities = state.jobs.filter(needsAttention).slice(0, 4)
  const hour = Number(
    new Intl.DateTimeFormat('en', {
      hour: 'numeric',
      hourCycle: 'h23',
      timeZone: 'America/Vancouver',
    }).format(new Date()),
  )
  const greeting =
    hour < 12
      ? 'Good morning.'
      : hour < 18
        ? 'Good afternoon.'
        : 'Good evening.'
  const ask = (prompt) => {
    persistDraft(window.sessionStorage, prompt)
    onNavigate('cockpit', { draft: prompt, draftKey: Date.now() })
  }
  return (
    <div className="company-home">
      <div className="home-hero">
        <div>
          <p className="eyebrow">YOUR COMPANY AT A GLANCE</p>
          <h1>{greeting} Here’s what matters.</h1>
          <p>One place to decide, act and keep things moving.</p>
        </div>
        <span className="quiet-tag">
          <span className="status-dot" />
          Operator workspace
        </span>
      </div>
      <div className="signal-grid">
        {[
          [
            'Needs your attention',
            known ? signals.attention : '—',
            'Jobs needing review or intervention',
            Inbox,
            'work-queue',
            { filter: 'attention' },
          ],
          [
            'Work in motion',
            known ? signals.motion : '—',
            'Queued and running David jobs',
            Clock3,
            'work-queue',
            { filter: 'active' },
          ],
          [
            'Verified outcomes',
            known ? signals.receipts : '—',
            'Completed jobs with recorded receipts',
            CircleCheck,
            'results',
            {},
          ],
          [
            'Business knowledge',
            'One Brain',
            'Decisions, original evidence and context',
            BookOpen,
            'search',
            {},
          ],
        ].map(([label, value, detail, Icon, view, params]) => (
          <button
            key={label}
            className="signal-card"
            onClick={() => onNavigate(view, params)}
          >
            <span className="signal-label">
              {label}
              <ArrowUpRight size={13} />
            </span>
            <span
              className={`signal-value ${typeof value === 'number' && label === 'Needs your attention' ? 'amber' : ''}`}
            >
              {value}
            </span>
            <span className="signal-detail">
              <Icon size={12} />
              {detail}
            </span>
          </button>
        ))}
      </div>
      <Departments onNavigate={onNavigate} centre={centre} signals={signals} known={known} />
      <p className="source-caption">
        Jobs reflect the latest{' '}
        {state.jobs.length === 100
          ? '100 records returned'
          : 'available records'}{' '}
        from David
        {state.fetchedAt
          ? ` · Read ${formatJobTime(state.fetchedAt)} PT`
          : ' · Awaiting a verified read'}
        {state.error && state.fetchedAt ? ' · STALE' : ''}
      </p>
      <div className="home-columns">
        <div className="home-left">
          <section className="editorial-card priority-card">
            <div className="card-heading">
              <div>
                <h2>Priority inbox</h2>
                <p>The few things worth looking at first.</p>
              </div>
              <button
                className="text-link"
                onClick={() =>
                  onNavigate('work-queue', { filter: 'attention' })
                }
              >
                View jobs <ArrowRight size={13} />
              </button>
            </div>
            <JobsNotice state={state} />
            {!state.loading && !state.error && !priorities.length && (
              <div className="calm-empty">
                <span className="empty-icon">
                  <Inbox size={22} />
                </span>
                <h3>You’re clear here.</h3>
                <p>
                  No jobs currently need your attention.
                  <br />
                  New requests and reviews will appear here.
                </p>
              </div>
            )}
            {priorities.map((job) => (
              <JobRow key={job.id} job={job} onNavigate={onNavigate} />
            ))}
            {!state.loading && state.error && !state.fetchedAt && (
              <div className="calm-empty compact">
                <Inbox size={24} />
                <h3>A clear view starts with verified data.</h3>
                <p>Your inbox will appear when the jobs source is available.</p>
              </div>
            )}
          </section>
          <div className="home-small-grid">
            <section className="editorial-card">
              <div className="card-heading">
                <div>
                  <h2>Conversations</h2>
                  <p>A little context. A clearer next step.</p>
                </div>
              </div>
              <div className="card-body">
                <span className="feature-icon">
                  <Sparkles size={20} />
                </span>
                <h3>Pick up with David.</h3>
                <p>
                  Your current conversation and unsent draft stay in this
                  browser session.
                </p>
                <button
                  className="text-link"
                  onClick={() => onNavigate('cockpit')}
                >
                  Open conversation <ArrowRight size={14} />
                </button>
              </div>
            </section>
            <section className="editorial-card" data-testid="crm-attention">
              <div className="card-heading">
                <div>
                  <h2>Sales attention</h2>
                  <p>From the prospect ledger and CRM log.</p>
                </div>
                <button className="text-link" onClick={() => onNavigate('crm', { filter: 'attention' })}>
                  Open CRM <ArrowRight size={13} />
                </button>
              </div>
              <div className="card-body">
                <p className="source-caption">Newsletter: {centre.crm?.counts?.subscribers || 0} confirmed · {centre.crm?.counts?.subscription_pending || 0} pending · {centre.crm?.counts?.subscription_suppressed || 0} suppressed · {centre.crm?.counts?.subscription_review || 0} held for review. Subscribers are separate from sales opportunities.</p>
                <SalesAttention crm={centre.crm} error={centre.crmError} onNavigate={onNavigate} />
                {(centre.crm?.guide_leads || []).map(lead => (
                  <button key={lead.id} className="cc-attention" onClick={() => onNavigate('crm', { record: lead.id })}>
                    <BookOpen size={13} />
                    <span><strong>{lead.name}</strong><small>Free guide · {lead.offer}</small>
                      <small>Draft job: {lead.draft_job || 'not requested'} · Next: {lead.next_action || 'review CRM record'}</small></span>
                  </button>
                ))}
                <PipelineSummary summary={centre.crm?.pipeline_summary} onOpen={id => onNavigate('crm', { record: id })} />
                <button className="text-link" onClick={() => onNavigate('crm', { filter: 'pipeline' })}>Open sales pipeline <ArrowRight size={13} /></button>
                {centre.web?.jobs.slice(0, 2).map((job) => (
                  <button key={job.job_id} className="cc-attention" onClick={() => onNavigate('websites', { job: job.job_id })}>
                    <Layers3 size={13} />
                    <span>
                      <strong>{job.request}</strong>
                      <small>Website · {job.project_name || job.project}</small>
                    </span>
                    <span className={`job-badge ${websiteStatus(job).tone}`}>{websiteStatus(job).label}</span>
                  </button>
                ))}
                <p className="source-caption">Projects & finance: no verified source connected.</p>
              </div>
            </section>
          </div>
          <div className="workflow-note">
            <Layers3 size={16} />
            <span>
              Capture <span>→</span> Decide <span>→</span> Act <span>→</span>{' '}
              Track <span>→</span> Close
            </span>
            <small>One workspace. A traceable result.</small>
          </div>
        </div>
        <div className="home-right">
          <section className="david-panel">
            <div className="david-panel-top">
              <span className="eyebrow">YOUR EXECUTIVE ASSISTANT</span>
              <span className="david-mark">
                <Sparkles size={20} />
              </span>
            </div>
            <h2>Ask David anything.</h2>
            <p>
              A quick answer when you need clarity. Background work when
              something needs to get done.
            </p>
            <div className="prompt-shortcuts">
              {[
                'What needs me today?',
                'Find the latest client decision',
                'What finished overnight?',
              ].map((prompt) => (
                <button key={prompt} onClick={() => ask(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
            <button
              className="david-entry"
              onClick={() => onNavigate('cockpit')}
            >
              <span>Open your conversation…</span>
              <span className="send-square">
                <ArrowRight size={17} />
              </span>
            </button>
            <small>Shortcuts prepare a draft for you to review.</small>
          </section>
          <section className="editorial-card">
            <div className="card-heading">
              <div>
                <h2>Activity & execution</h2>
                <p>One queue. Traceable outcomes.</p>
              </div>
              <button
                className="text-link"
                onClick={() => onNavigate('work-queue')}
              >
                Open jobs <ArrowRight size={13} />
              </button>
            </div>
            {state.loading ? (
              <p className="card-body" role="status">
                Loading activity…
              </p>
            ) : state.error && !state.fetchedAt ? (
              <div className="card-body">
                <p>Activity unavailable</p>
                <small>Waiting for the connected jobs API.</small>
              </div>
            ) : !state.jobs.length ? (
              <div className="card-body">
                <p>No David jobs yet.</p>
                <small>
                  Delegated work will appear here with its permanent ID.
                </small>
              </div>
            ) : (
              state.jobs
                .slice(0, 3)
                .map((job) => (
                  <JobRow key={job.id} job={job} onNavigate={onNavigate} />
                ))
            )}
          </section>
          <section className="editorial-card knowledge-card">
            <div className="card-heading">
              <div>
                <h2>Knowledge, connected</h2>
                <p>One Business Brain. Every decision traceable.</p>
              </div>
            </div>
            <button
              className="knowledge-link"
              onClick={() => onNavigate('search')}
            >
              <span className="row-icon">
                <Search size={18} />
              </span>
              <span>
                <strong>Search people, projects and decisions</strong>
                <small>Confirmed knowledge and original evidence</small>
              </span>
              <ArrowUpRight size={16} />
            </button>
            <button
              className="knowledge-link"
              onClick={() => onNavigate('graphify')}
            >
              <span className="row-icon">
                <Network size={18} />
              </span>
              <span>
                <strong>Explore the connections</strong>
                <small>Open the existing Graphify workspace</small>
              </span>
              <ArrowUpRight size={16} />
            </button>
          </section>
        </div>
      </div>
    </div>
  )
}
