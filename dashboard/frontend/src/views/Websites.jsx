// Websites & Digital: ask for a scoped site change, then follow its one aos_jobs job through
// preflight, isolated branch, tests and a local preview. Publishing is shown as held, always:
// it needs Casey's explicit command and is never performed from here (tools/aos_website.py).
// Revisit: when aos_website's job view or the website registry changes. · Last touched: 2026-11-18.
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Globe, ListChecks, ShieldCheck } from 'lucide-react'
import { getWebsiteJob, getWebsiteProjects } from '../api'
import { publishCopy, websiteStatus } from '../commandCentreState'
import { formatJobTime } from '../jobPresentation'
import { Badge } from './CrmSales'
import WebsitePreparation, { WebsiteNav } from './WebsitePreparation'
import { studioApi } from '../studio/api'
import ProductLaunch from './ProductLaunch'
import CurrentWebsiteStudio from './CurrentWebsiteStudio'
import { SITE_ID } from '../studio/site-config.js'
const WebsiteStudio = lazy(() => import('./WebsiteStudio'))

const failure = error => error?.response?.data?.detail || error?.message || 'The website workflow is unavailable.'

function Steps({ steps }) {
  return (
    <ol className="cc-steps">
      {steps.map(step => (
        <li key={step.id} className={`cc-step ${step.status}`}>
          <strong>{step.id}</strong> <span>{step.status}{step.replayed ? ' (recovered)' : ''}</span>
          {step.detail && <small>{String(step.detail)}</small>}
          {step.summary && <small>{step.summary}</small>}
        </li>
      ))}
    </ol>
  )
}

export function WebsiteJob({ id, onBack, onNavigate }) {
  const [state, setState] = useState({ loading: true })
  useEffect(() => {
    let alive = true
    let timer
    const read = () => getWebsiteJob(id).then(view => {
      if (!alive) return
      setState({ view })
      if (['queued', 'running'].includes(view.status)) timer = window.setTimeout(read, 3000)
    }, error => alive && setState(old => ({ ...old, loading: false, error: failure(error) })))
    read()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [id])
  const view = state.view
  const status = websiteStatus(view)
  return (
    <section className="editorial-card job-detail cc-record">
      <button className="text-link" onClick={onBack}><ArrowLeft size={15} />Back to websites</button>
      {state.loading && !view && <p role="status">Reading the website job…</p>}
      {state.error && <p className="data-notice warning" role="alert">{state.error}</p>}
      {view && (
        <>
          <div className="detail-title"><h3>{view.request}</h3><Badge tone={status.tone}>{status.label}</Badge></div>
          <p className="mono cc-id">{view.job_id} · {view.project_name || view.project}</p>
          {view.readiness && <p><strong>Readiness: {view.readiness}</strong> · Released: no · Deployment requires separate authorization.</p>}
          {view.detail && view.status !== 'done' && <p className="data-notice warning">{view.detail}</p>}
          {view.summary && <p className="result-copy">{view.summary}</p>}
          <div className={`cc-publish ${view.publish.status}`}>
            <ShieldCheck size={16} />
            <div><strong>Publishing: {view.publish.status === 'held' ? 'held for Casey' : view.publish.status.replace('_', ' ')}</strong>
              <p>{publishCopy(view.publish)}</p>
              {view.publish.target && <small>Target: {view.publish.target}</small>}
            </div>
          </div>
          <h3>Progress</h3>
          <Steps steps={view.steps || []} />
          <dl className="detail-meta">
            <div><dt>Branch</dt><dd className="mono">{view.branch || '—'}</dd></div>
            <div><dt>Commit</dt><dd className="mono">{view.commit ? view.commit.slice(0, 12) : '—'}</dd></div>
            <div><dt>Base</dt><dd className="mono">{view.preflight ? `${view.preflight.base_ref} @ ${view.preflight.base_sha.slice(0, 12)}` : '—'}</dd></div>
            <div><dt>Checkout state</dt><dd>{view.preflight ? `${view.preflight.dirty.length} uncommitted file(s) · untouched` : '—'}</dd></div>
            <div><dt>Created · PT</dt><dd>{formatJobTime(view.created_at)}</dd></div>
            <div><dt>Finished · PT</dt><dd>{formatJobTime(view.finished_at)}</dd></div>
          </dl>
          {view.icm && (
            <>
              <h3>Project instructions used</h3>
              <ul className="cc-list">
                {view.icm.files.map(row => <li key={row.path}><span className="mono">{row.path}</span> · sha256 {row.sha256.slice(0, 12)}</li>)}
                {view.icm.brain.map(row => <li key={row.pointer}><button className="text-link" onClick={() => onNavigate('search', { tab: 'memory', record: row.pointer })}>{row.pointer}</button>{row.read ? ' · read' : ' · referenced'}</li>)}
              </ul>
            </>
          )}
          {!!view.changed_files.length && <><h3>Changed files</h3><ul className="cc-list">{view.changed_files.map(path => <li key={path} className="mono">{path}</li>)}</ul></>}
          {view.diff && <details className="cc-code" open={view.status !== 'done'}><summary>Diff</summary><pre>{view.diff}</pre></details>}
          <h3>Tests</h3>
          {view.tests ? (
            <p>{view.tests.passed === true ? 'Passed' : view.tests.passed === false ? 'Failed' : 'Not defined for this project'}{view.tests.command ? ` · ${view.tests.command.join(' ')}` : ''}</p>
          ) : <p>Not run yet.</p>}
          {view.tests_log && <details className="cc-code"><summary>Test log</summary><pre>{view.tests_log}</pre></details>}
          <h3>Preview</h3>
          {view.preview ? (
            <>
              <p className="source-caption">{view.preview.detail}</p>
              <iframe className="cc-preview" title={`Preview of ${view.job_id}`} sandbox="" src={view.preview.url} />
              <a className="text-link" href={view.preview.url} target="_blank" rel="noreferrer">Open preview <ArrowUpRight size={13} /></a>
            </>
          ) : <p>No verified preview.</p>}
          <h3>Receipt & artifacts</h3>
          {(view.receipts || []).map(row => <p key={row.path} className="mono">{row.path} · {row.status} · sha256 {String(row.sha256).slice(0, 12)}</p>)}
          {view.artifact_problems?.length > 0 && <p className="data-notice warning">{view.artifact_problems.join('; ')}</p>}
          <p className="source-caption">{view.artifacts.length} artifact(s) verified by SHA-256 · {view.usage?.model_calls ? `${view.usage.total_tokens} tokens over ${view.usage.model_calls} call(s)${view.usage.exact ? ' (exact)' : ' (some usage unavailable)'}` : 'No model call'}</p>
          <button className="text-link" onClick={() => onNavigate('work-queue', { selectedId: view.job_id })}><ListChecks size={13} />Open in Jobs</button>
        </>
      )}
    </section>
  )
}

const SITE_KEY = 'aos.websites.studioSite'
const storedSite = () => { try { return window.localStorage.getItem(SITE_KEY) || '' } catch { return '' } }

export default function Websites({ initialFilters = {}, onViewParamsPush, onNavigate }) {
  const [legacyStudio, setLegacyStudio] = useState(false)
  const [captureStudio, setCaptureStudio] = useState(false)
  const [state, setState] = useState({ loading: true })
  const [studioSites, setStudioSites] = useState([])
  const selected = initialFilters.job
  const tab = initialFilters.tab || 'seo'
  const studio = tab === 'studio' && !selected
  const setParams = params => onViewParamsPush?.({ ...initialFilters, ...params })
  // Registered Studio websites (deterministic registry read): Example Company is preselected.
  useEffect(() => { studioApi.sites().then(data => setStudioSites(data.sites), () => setStudioSites([])) }, [])
  const site = [initialFilters.site, storedSite()].find(id => id && (!studioSites.length || studioSites.some(s => s.id === id)))
    || studioSites.find(s => s.default)?.id || SITE_ID
  const chooseSite = id => { try { window.localStorage.setItem(SITE_KEY, id) } catch { /* per-browser convenience only */ } setParams({ tab: 'studio', site: id, job: undefined }) }
  const chooseTab = next => setParams({ tab: next, job: undefined, site: next === 'studio' ? site : undefined })
  const load = useCallback(() => getWebsiteProjects().then(data => setState({ data }), error => setState(old => ({ ...old, loading: false, error: failure(error) }))), [])
  useEffect(() => {
    load()
    const poll = window.setInterval(load, 15000)
    return () => window.clearInterval(poll)
  }, [load])
  const data = state.data
  return (
    <div className="jobs-page cc-page websites-page" data-studio={studio}>
      <div className="page-heading">
        <p className="eyebrow">WEBSITES & DIGITAL</p>
        <h1>Websites & Digital</h1>
        <p>Build an evidence-led SEO strategy, prepare a scoped website change, or edit your website visually in Website Studio. Publication stays held.</p>
      </div>
      {!selected && <WebsiteNav tab={tab} onTab={chooseTab} />}
      {!studio && <ProductLaunch openStudio={()=>{setLegacyStudio(true);chooseTab('studio')}} />}
      {studio && !legacyStudio && !captureStudio && <Suspense fallback={<p role="status">Opening Website Studio…</p>}><WebsiteStudio key={`${site}:live`} site={site} sites={studioSites} onSite={chooseSite} onOpenCapture={()=>setCaptureStudio(true)} /></Suspense>}
      {studio && captureStudio && <><button onClick={()=>setCaptureStudio(false)}>Return to the live website editor</button><CurrentWebsiteStudio onLegacy={()=>{setCaptureStudio(false);setLegacyStudio(true)}} /></>}
      {studio && legacyStudio && <><button onClick={()=>setLegacyStudio(false)}>Return to the live website editor</button><Suspense fallback={<p role="status">Opening preserved release candidate…</p>}><WebsiteStudio key={`${site}:candidate`} site={site} sites={studioSites} onSite={chooseSite} initialView="editor" /></Suspense></>}
      {selected ? (
        <WebsiteJob key={selected} id={selected} onBack={() => setParams({ job: undefined })} onNavigate={onNavigate} />
      ) : (
        <div hidden={studio}>
            {state.loading && !data && <p role="status">Reading registered websites…</p>}
            {state.error && <p className="data-notice warning" role="alert">{state.error}</p>}
            {data && <WebsitePreparation projects={data.projects} tab={tab} onTab={chooseTab} hideNav onQueued={id => { load(); setParams({ job: id }) }}>
            <details className="editorial-card website-history"><summary>Website job history ({data?.jobs.length || 0})</summary>
            <section className="editorial-card">
              <div className="card-heading"><div><h2>Change requests</h2><p>Every website job, newest first.</p></div></div>
              {state.loading && !data && <p className="data-notice" role="status">Reading website jobs…</p>}
              {state.error && <p className="data-notice warning" role="alert">{state.error}</p>}
              {data && !data.jobs.length && <div className="calm-empty compact"><Globe size={24} /><h3>No website changes yet.</h3><p>Requests from here or from David appear with their job ID.</p></div>}
              {data?.jobs.map(job => {
                const status = websiteStatus(job)
                return (
                  <button key={job.job_id} className="job-list-row" onClick={() => setParams({ job: job.job_id })}>
                    <span className="row-icon"><Globe size={17} /></span>
                    <span className="row-copy"><span className="mono job-id">{job.job_id}</span><strong>{job.request}</strong><small>{job.project_name || job.project} · {formatJobTime(job.finished_at || job.created_at)} PT</small></span>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </button>
                )
              })}
            </section>
            </details>
            </WebsitePreparation>}
        </div>
      )}
    </div>
  )
}
