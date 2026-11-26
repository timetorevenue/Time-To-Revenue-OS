// Website Studio releases: inspect, compare, publish and roll back exact validated builds.
// Every call is deterministic (zero model calls). Publishing/rollback need the verified operator
// and the exact typed command the server issued for this release, target and published state.
// Revisit: when release routes or targets change. Last touched: 2026-11-22.
import { useEffect, useRef, useState } from 'react'
import { studioApi } from './api'
import { canConfirm, currentText, isTerminal, short, statusText, versionsText } from './releases'

function Checks({ rows }) {
  return <ul className="studio-release-checks">{rows.map(c => <li key={c.check} data-ok={c.ok}>{c.ok ? '✓' : '✗'} <strong>{c.check}</strong> — {c.detail}</li>)}</ul>
}

export function ReleasesPanel({ site, built, api = studioApi }) {
  const [overview, setOverview] = useState(null)
  const [target, setTarget] = useState('')
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState(null)
  const [compare, setCompare] = useState(null)
  const [history, setHistory] = useState(null)
  const [plan, setPlan] = useState(null)
  const [typed, setTyped] = useState('')
  const [operation, setOperation] = useState(null)
  const [message, setMessage] = useState('')
  const timer = useRef(null)

  async function refresh(nextTarget = target) {
    try {
      const value = await api.releases(site)
      setOverview(value)
      const ids = Object.keys(value.targets)
      const chosen = ids.includes(nextTarget) ? nextTarget : (ids.find(id => value.targets[id].available) || ids[0] || '')
      setTarget(chosen)
      if (chosen) setHistory(await api.releaseHistory(site, chosen))
    } catch (e) { setMessage(`Releases unavailable: ${e.message}`) }
  }
  useEffect(() => { refresh(); return () => clearInterval(timer.current) }, [site])
  useEffect(() => {
    if (!selected || !target) { setDetail(null); setCompare(null); return }
    let live = true
    Promise.all([api.release(site, selected), api.compareRelease(site, selected, target)])
      .then(([d, c]) => { if (live) { setDetail(d); setCompare(c) } })
      .catch(e => live && setMessage(e.message))
    return () => { live = false }
  }, [site, selected, target, overview])

  async function create() {
    try {
      const record = await api.createRelease(site, built.id)
      setMessage(record.existing ? `Release ${record.release_id} already exists for this build.` : `Release ${record.release_id} recorded (immutable).`)
      setSelected(record.release_id)
      await refresh()
    } catch (e) { setMessage(`Release not created: ${e.message}`) }
  }
  async function prepare(action, release = selected) {
    setPlan(null); setTyped(''); setOperation(null)
    try {
      const value = await api.prepareRelease(site, release, { action, target })
      setPlan(value)
      setMessage(value.noop ? 'This exact release is already published on this target; nothing would be deployed.'
        : value.allowed ? `Review the exact ${action}, then type the command to authorize it.` : `Refused: ${value.problems.join('; ')}`)
    } catch (e) { setMessage(`Not prepared: ${e.message}`) }
  }
  async function poll(id) {
    clearInterval(timer.current)
    timer.current = setInterval(async () => {
      try {
        const value = await api.releaseOperation(site, id)
        setOperation(value)
        if (isTerminal(value)) { clearInterval(timer.current); setMessage(statusText(value.status)); refresh() }
      } catch (e) { clearInterval(timer.current); setMessage(e.message) }
    }, 1000)
  }
  async function execute() {
    try {
      const value = await api.executeRelease(site, plan.release, { action: plan.action, target, expected_current: plan.expected_current, confirmation_token: plan.noop ? '' : plan.confirmation.confirmation_token, command: plan.noop ? '' : typed })
      setOperation(value); setPlan(null); setTyped('')
      setMessage(value.duplicate ? `Duplicate request: returning operation ${value.id}; nothing new was deployed.` : statusText(value.status))
      if (!isTerminal(value)) poll(value.id); else refresh()
    } catch (e) { setMessage(`Not executed: ${e.message}`) }
  }

  if (!overview) return <section aria-label="Releases" className="studio-releases"><h3>Releases</h3><p role="status">{message || 'Loading releases…'}</p></section>
  const info = overview.targets[target]
  const record = detail?.release
  const rollbackOk = info?.rollback_targets?.includes(selected)
  const buildable = built?.purpose === 'release' && built.validation && built.status !== 'building'
  return <section aria-label="Releases" className="studio-releases" data-testid="studio-releases">
    <h3>Releases</h3>
    <p className="studio-help">Exact validated builds only · 0 AI calls · ledger {overview.ledger.intact ? 'intact' : 'BROKEN'} ({overview.ledger.entries} entries) <button onClick={() => refresh()}>Refresh releases</button></p>
    <p role="status" className="studio-status" data-testid="release-message">{message}</p>
    {buildable && <p>Release build <code>{built.nextBuildId}</code> · validation {built.validation} · artifact <code>{short(built.artifact?.sha256, 16)}</code> <button onClick={create} disabled={built.validation !== 'passed'}>Create release from this build</button></p>}
    <fieldset><legend>Deployment target</legend>
      {Object.values(overview.targets).map(t => <label key={t.id} className="studio-release-target" data-testid={`target-${t.id}`}>
        <input type="radio" name="release-target" checked={t.id === target} onChange={() => { setTarget(t.id); setPlan(null); refresh(t.id) }}/> <strong>{t.label}</strong> ({t.provider}, {t.environment}) · {t.available ? 'available' : 'blocked'}
        <span className="studio-help" data-testid={`current-${t.id}`}>Published: {currentText(t)}</span>
        {!t.available && <ul className="studio-release-blockers">{t.blockers.map(b => <li key={b.id}><strong>{b.id}</strong>: {b.detail}</li>)}</ul>}
      </label>)}
    </fieldset>
    <table className="studio-release-table"><caption>Release history</caption>
      <thead><tr><th>Release</th><th>Created</th><th>Saved versions</th><th>Build</th><th>Artifact</th><th>Status</th></tr></thead>
      <tbody>{overview.releases.map(r => <tr key={r.release_id} data-testid={`release-${r.release_id}`} aria-selected={r.release_id === selected}>
        <td><button className="studio-link-button" onClick={() => { setSelected(r.release_id); setPlan(null) }}>{r.release_id}</button></td>
        <td>{r.created_at}</td><td>{versionsText(r.drafts)}</td><td><code>{r.build_id}</code></td><td><code>{short(r.artifact.sha256)}</code></td>
        <td>{r.validation === 'passed' ? 'validated' : 'validation failed'} · {r.verified ? 'verified' : 'NOT verified'}{r.pin_current ? '' : ' · stale pin'}{r.published_on.length ? ` · published on ${r.published_on.join(', ')}` : ''}</td>
      </tr>)}</tbody>
    </table>
    {record && <article className="studio-release-detail" data-testid="release-detail">
      <h4>{record.release_id}</h4>
      <dl>
        <dt>Saved drafts</dt><dd>{Object.entries(record.drafts).map(([p, v]) => <div key={p}>{p} v{v.version} · fingerprint <code>{short(v.fingerprint, 16)}</code> · record <code>{short(v.record_sha256)}</code></div>)}</dd>
        <dt>Website source</dt><dd>commit <code>{record.source.commit}</code> · lockfile <code>{short(record.source.lock_sha256, 16)}</code></dd>
        <dt>Build</dt><dd><code>{record.build.next_build_id}</code> · {record.build.routes} routes · environment {record.build.environment.profile}</dd>
        <dt>Build checksum</dt><dd>artifact <code data-testid="release-artifact">{record.artifact.sha256}</code> · manifest <code>{short(record.artifact.manifest_sha256, 16)}</code> · {record.artifact.files} files</dd>
        <dt>Record</dt><dd><code>{record.record_sha256}</code> · created {record.created_at} by {record.created_by}</dd>
      </dl>
      <h5>Validation evidence</h5><Checks rows={record.validation.checks}/>
      <h5>Verification now</h5><Checks rows={detail.verification.checks}/>
      {compare && <><h5>Compared with what {info?.label} serves</h5><p data-testid="release-compare">{compare.summary}</p>
        <table><thead><tr><th>Page</th><th>This release</th><th>Published</th><th>Changed</th></tr></thead>
          <tbody>{compare.pages.map(p => <tr key={p.page}><td>{p.page}</td><td>{p.release ? `v${p.release}` : '—'}</td><td>{p.published ? `v${p.published}` : '—'}</td><td>{p.changed ? 'yes' : 'no'}</td></tr>)}</tbody></table>
        {compare.artifact && <p>Artifact: this <code>{short(compare.artifact.release)}</code> · published <code>{short(compare.artifact.published)}</code></p>}</>}
      <p>
        <button onClick={() => prepare('publish')} disabled={!info}>Request publication to {info?.label}</button>
        <button onClick={() => prepare('rollback')} disabled={!rollbackOk} title={rollbackOk ? '' : 'Only a release this target has served successfully before can be a rollback target'}>Roll back {info?.label} to this release</button>
      </p>
    </article>}
    {plan && <section className="studio-release-plan" aria-label="Authorize release action" data-testid="release-plan">
      <p><strong>{plan.action === 'publish' ? 'Publish' : 'Roll back'}</strong> {plan.release} on <strong>{plan.target_label}</strong>{plan.production ? ' (production)' : ''} · {plan.external ? <>earlier deployment {plan.deployment?.url ? <code>{plan.deployment.url}</code> : null} (source {short(plan.deployment?.source_commit, 12) || 'unknown'}), promoted again unchanged</> : <>build <code>{plan.build_id}</code> · artifact <code>{short(plan.artifact_sha256, 16)}</code></>} · currently {plan.expected_current}</p>
      {!plan.allowed && <ul className="studio-release-blockers">{plan.problems.map(p => <li key={p}>{p}</li>)}</ul>}
      {plan.allowed && plan.noop && <button onClick={execute}>Record no-op</button>}
      {plan.allowed && !plan.noop && <>
        <label htmlFor="release-command">Type <code>{plan.phrase}</code> to authorize this exact action (expires in {plan.confirmation.expires_in_seconds} s)</label>
        <input id="release-command" value={typed} onChange={e => setTyped(e.target.value)} autoComplete="off" spellCheck={false}/>
        <button onClick={execute} disabled={!canConfirm(plan, typed)}>Confirm {plan.action}</button>
      </>}
    </section>}
    {operation && <section aria-label="Release operation" data-testid="release-operation" data-status={operation.status}>
      <p>Operation <code>{operation.id}</code> · {operation.action} {operation.release} · <strong>{statusText(operation.status)}</strong> · {operation.deployments_created ?? 0} deployment(s) created · {operation.model_calls} AI calls</p>
      <ol>{(operation.steps || []).map((s, i) => <li key={i}>{s.at} {statusText(s.status)}{s.error ? ` — ${s.error}` : ''}</li>)}</ol>
      {operation.deployment?.url && <p data-testid="staged-deployment">Staged deployment <code>{operation.deployment.deployment_id}</code> · <a href={operation.deployment.url} target="_blank" rel="noreferrer">{operation.deployment.url}</a> · {operation.deployment.files} files · {operation.deployment.created ? 'created' : 'reused (same release)'}</p>}
      {operation.staged_functional && <details open={!operation.staged_functional.ok}><summary>Staged deployment checks: {operation.staged_functional.ok ? 'passed' : 'FAILED'}</summary><Checks rows={operation.staged_functional.checks}/></details>}
      {operation.production_functional && <details open={!operation.production_functional.ok}><summary>Production checks: {operation.production_functional.ok ? 'passed' : 'FAILED'}</summary><Checks rows={operation.production_functional.checks}/></details>}
      {operation.published_identity && <p data-testid="published-identity">Published identity: deployment {operation.published_identity.deployment_id} · build {operation.published_identity.build_id} · artifact <code>{short(operation.published_identity.artifact_sha256, 16)}</code> · {operation.published_identity.matches_release ? 'matches the approved build' : 'DOES NOT MATCH'}</p>}
      {operation.error && <p role="alert">{operation.error}{operation.previous_available ? ' · The previous version is still published and healthy.' : ''}</p>}
    </section>}
    {history && <details open><summary>Operation history · {info?.label}</summary>
      <table className="studio-release-table" data-testid="operation-history"><thead><tr><th>Operation</th><th>Action</th><th>Release</th><th>Status</th><th>Deployments</th><th>By</th><th>When</th></tr></thead>
        <tbody>{history.operations.map(o => <tr key={o.id} data-status={o.status}><td><code>{o.id}</code></td><td>{o.action}</td><td>{o.release}</td><td>{statusText(o.status)}</td><td>{o.deployments_created ?? 0}</td><td>{o.actor}</td><td>{o.created_at}</td></tr>)}</tbody></table>
      <p className="studio-help">Rollback targets: {history.rollback_targets.join(', ') || 'none yet'}</p>
      {history.rollback_targets.filter(id => id.startsWith('dpl_')).map(id => <p key={id}>
        <button data-testid={`rollback-external-${id}`} onClick={() => prepare('rollback', id)}>Roll back {info?.label} to the earlier deployment {id}</button>
        <span className="studio-help"> (the site that served before a Studio release; promoted again exactly, nothing is rebuilt)</span></p>)}
    </details>}
  </section>
}
