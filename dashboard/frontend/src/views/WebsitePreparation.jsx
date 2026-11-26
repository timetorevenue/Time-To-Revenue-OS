// Non-executing website work orders; queue submission reuses the existing website executor.
// Revisit on preparation API/schema changes. Last touched: 2026-11-18.
import { useEffect, useState } from 'react'
import { LayoutTemplate } from 'lucide-react'
import { getWebsitePreparations, postWebsitePreparation, queueWebsitePreparation } from '../websitePreparationApi'
import { preparationInput } from '../websitePreparationState'
import WebsiteSeoPlanner from './WebsiteSeoPlanner'

const failure = error => error?.response?.data?.detail || error?.message || 'Preparation unavailable'
const blank = { project: '', request: '', branch: '', affected: '', sources: '', checks: '' }

export const WEBSITE_TABS = [['seo', 'SEO Strategy'], ['changes', 'Website Changes'], ['history', 'History'], ['studio', 'Website Studio']]

export function WebsiteNav({ tab, onTab, studio = true }) {
  return <nav className="website-nav" aria-label="Website workflow">{WEBSITE_TABS.filter(([id]) => studio || id !== 'studio').map(([id, label]) => <button key={id} className={`cc-button ${tab === id ? '' : 'secondary'}${id === 'studio' ? ' website-nav-studio' : ''}`} aria-current={tab === id ? 'page' : undefined} onClick={() => onTab(id)}>{id === 'studio' && <LayoutTemplate size={16} aria-hidden="true" />}{label}</button>)}</nav>
}

export default function WebsitePreparation({ projects, onQueued, children, tab: controlledTab, onTab, hideNav = false }) {
  const [form, setForm] = useState(blank)
  const [ownTab, setOwnTab] = useState('seo')
  const tab = controlledTab && controlledTab !== 'studio' ? controlledTab : ownTab
  const setTab = next => { setOwnTab(next); onTab?.(next) }
  const [saved, setSaved] = useState('')
  const [orders, setOrders] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = () => getWebsitePreparations().then(data => setOrders(data.preparations))
  useEffect(() => {
    let alive = true
    const read = () => getWebsitePreparations().then(data => { if (alive) setOrders(data.preparations) }, e => { if (alive) setError(failure(e)) })
    read()
    const timer = window.setInterval(read, 15000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [])
  const project = projects.find(row => row.id === form.project)
  const change = key => event => setForm(old => ({ ...old, [key]: event.target.value }))
  const save = async draft => {
    setBusy(true); setError('')
    try {
      const body = preparationInput(form, project)
      const result = await postWebsitePreparation({ ...body, draft })
      setSaved(`Work order ${result.state || 'saved'}. Open History to review readiness and blockers.`)
      await load()
    } catch (e) { setError(failure(e)) }
    finally { setBusy(false) }
  }
  const queue = async id => {
    setBusy(true); setError('')
    try {
      const result = await queueWebsitePreparation(id)
      onQueued(result.job.job_id)
      await load()
    } catch (e) { setError(failure(e)) }
    finally { setBusy(false) }
  }
  const selectProject = id => {
    const next = projects.find(row => row.id === id)
    setForm({ ...blank, project: id, sources: (next?.icm_files || []).join('\n') })
    setError(''); setSaved('')
  }
  const scopedOrders = orders.filter(order => !form.project || order.project === form.project)
  return <div className="website-workspace">
    <section className="editorial-card website-context">
      <label className="cc-field"><span>Registered website</span><select value={form.project} onChange={event => selectProject(event.target.value)}><option value="">Choose a website to begin</option>{projects.map(row => <option key={row.id} value={row.id}>{row.name || row.id}{row.fixture ? ' · test fixture' : ''}</option>)}</select></label>
      <div className={`website-readiness${!project ? ' website-readiness-empty' : project.state === 'ready' ? ' website-readiness-ready' : ''}`}>{project ? <><strong>{project.state === 'ready' ? 'Website configured for scoped work' : project.why || 'Website setup needs attention'}</strong><p>Scope: {project.client_scope} · Base: {project.base_ref || 'unconfigured'}</p><details><summary>Registered edit paths</summary><ul>{(project.allowed_paths || []).map(path => <li className="mono" key={path}>{path}</li>)}</ul><p className="website-mobile-note">SEO target paths and evidence must pass work-order validation. Website readiness does not approve new SEO paths.</p></details><p className="website-desktop-note">SEO target paths and evidence must pass work-order validation. Website readiness does not approve new SEO paths.</p></> : <><strong>Select a website to connect your strategy</strong><p>Start with your audience, compare competitors and collect evidence. A website selection connects the correct client scope and edit limits.</p></>}</div>
    </section>
    {!hideNav && <WebsiteNav tab={tab} onTab={setTab} studio={false} />}
    {error && <p className="data-notice warning" role="alert">{error}</p>}
    {saved && <p className="data-notice" role="status">{saved} <button className="text-link" onClick={() => setTab('history')}>Review saved work</button></p>}
    <div hidden={tab !== 'seo'}>
      <WebsiteSeoPlanner key={form.project} project={project} onSelect={(page, targetPath) => {
        setForm(old => ({ ...old,
          request: `Create evidence-based SEO comparison page: ${page.title}. Include a sourced fact table, real author, competitor-interest disclosure, verified internal links, and intent-matched CTA. Do not invent claims.`,
          branch: `feature/seo-${page.slug}`, affected: targetPath,
          sources: (project.icm_files || []).join('\n'), checks: `${targetPath} :: ${page.h1}`,
          seo_strategy: { version: 'competitive-comparisons-v1', kind: page.kind, slug: page.slug, target_path: targetPath, research: page.research },
        })); setSaved(''); setTab('changes')
      }} />
    </div>
    <section hidden={tab !== 'changes'} className="editorial-card cc-form">
      <div className="card-heading"><div><h2>{form.seo_strategy ? '5. Review SEO work order' : 'Prepare a website change'}</h2><p>Review the scope and validate inputs before creating a job. Preparation uses no model.</p></div></div>
      {!project && <p className="data-notice">Choose a website in the shared header to prepare a scoped change.</p>}
      <label className="cc-field"><span>Requested change</span><textarea rows={3} maxLength={1000} value={form.request} onChange={change('request')} /></label>
      <details className="website-advanced" open={form.seo_strategy ? true : undefined}><summary>Scope, references and acceptance checks</summary>
      <label className="cc-field"><span>Development branch</span><input placeholder="feature/website-homepage-copy" value={form.branch} onChange={change('branch')} /></label>
      <label className="cc-field"><span>Affected pages/assets — one repository path per line</span><textarea rows={2} value={form.affected} onChange={change('affected')} placeholder="site/index.html" /></label>
      <label className="cc-field"><span>Source and style references — existing repository files, one per line</span><textarea rows={2} value={form.sources} onChange={change('sources')} placeholder="AGENTS.md" /></label>
      <label className="cc-field"><span>Acceptance checks — path :: expected text, one per line</span><textarea rows={3} value={form.checks} onChange={change('checks')} placeholder="site/index.html :: Approved headline" /></label>
      </details>
      {form.seo_strategy && <p className="data-notice">SEO evidence is attached. Preparation checks evidence and the exact target path before queueing. <button className="text-link" onClick={() => setForm(old => ({ ...old, seo_strategy: null }))}>Clear SEO selection</button></p>}
      <p className="source-caption">Allowed: local edits, tests, builds, preview and commit. Push, deployment and publishing require separate authorization and are unavailable here.</p>
      <div className="cc-actions-bar"><button className="cc-button" disabled={busy || !project} onClick={() => save(false)}>Prepare work order</button><button className="cc-button secondary" disabled={busy || !project} onClick={() => save(true)}>Save draft</button></div>
    </section>
    <section hidden={tab !== 'history'} className="editorial-card">
      <div className="card-heading"><div><h2>Preparation history</h2><p>Drafts, historical blocked preparations and validated work. All records and blockers are preserved. {project ? 'Showing the selected website.' : 'Showing all registered websites.'}</p></div></div>
      <details className="website-history"><summary>Saved preparations ({scopedOrders.length})</summary>
      {!scopedOrders.length && <p>No saved work orders yet.</p>}
      {scopedOrders.map(order => <article className="cc-project" key={order.id}>
        <h3>{order.request || 'Untitled draft'}</h3><p><strong>{order.state}</strong> · {order.project || 'Project missing'} · Publishing held · Released: no</p>
        <p className="mono">{order.branch || 'Branch missing'}</p>
        {!!order.validation?.problems?.length && <ul>{order.validation.problems.map((problem, i) => <li key={i}>{problem}</li>)}</ul>}
        {!!order.validation?.external_steps?.length && <p>External steps unverified: {order.validation.external_steps.join('; ')}</p>}
        {order.job?.detail && <p className="data-notice warning">{order.job.detail}</p>}
        <details><summary>Work order and evidence</summary><p className="mono">{order.artifact_path}</p><pre className="cc-code" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify({ ...order, job: order.job ? { job_id: order.job.job_id, readiness: order.job.readiness, artifacts: order.job.artifacts, artifact_problems: order.job.artifact_problems, receipts: order.job.receipts } : undefined }, null, 2)}</pre></details>
        {order.job_id ? <button className="text-link" onClick={() => onQueued(order.job_id)}>Open job {order.job_id}</button> : <>
          <button className="cc-button secondary" onClick={() => { setTab('changes'); setSaved(''); setError(''); setForm({ project: order.project || '', request: order.request || '', branch: order.branch || '', affected: (order.affected_paths || []).join('\n'), sources: (order.source_references || []).join('\n'), checks: (order.acceptance_checks || []).map(c => `${c.path} :: ${c.contains}`).join('\n'), seo_strategy: order.seo_strategy || null }) }}>Revise as new work order</button>
          {order.state === 'prepared' && <button className="cc-button" disabled={busy} onClick={() => queue(order.id)}>Queue prepared change</button>}
        </>}
      </article>)}
      </details>
      {children}
    </section>
  </div>
}
