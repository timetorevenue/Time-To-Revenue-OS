// Sources & Branding for the selected Studio website. Read-only views plus three explicit,
// deterministic operator actions: Check sources, choose the Drive asset folder, approve brand.
// No model call is possible from this view. Revisit with the Studio sources API. Last touched: 2026-11-18.
import { useState } from 'react'
import { studioApi } from '../studio/api'

const stateLabel = { connected: 'Connected', disconnected: 'Disconnected', stale: 'Stale', error: 'Error' }
const when = value => value ? new Date(value).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' }) : 'never'

export function StateBadge({ state }) {
  return <span className={`studio-state studio-state-${state || 'error'}`}>{stateLabel[state] || 'Unknown'}</span>
}

function Card({ title, status, children }) {
  return <section className="studio-source-card" data-source={status?.kind || title}>
    <header><h3>{title}</h3>{status && <StateBadge state={status.state} />}</header>
    {status?.detail && <p>{status.detail}</p>}
    {status && <p className="studio-source-meta">Last successful sync: {when(status.last_success)}{status.checked_at ? ` · checked ${when(status.checked_at)}` : ''}</p>}
    {children}
  </section>
}

function Swatch({ hex, label }) {
  return <span className="studio-swatch"><i style={{ background: hex }} />{label ? <b>{label}</b> : null}<code>{hex}</code></span>
}

function BrandReview({ site, brand, onChanged, busy, setBusy, setMessage }) {
  const proposed = brand?.proposed, approved = brand?.approved
  if (!proposed) return <p>The brand kit has not been read yet. Use <strong>Check sources</strong>.</p>
  const kit = proposed.brand
  const approve = async () => {
    setBusy(true)
    try { await studioApi.approveBrand(site, proposed.fingerprint); setMessage('Brand configuration approved. Studio defaults and David proposals now follow it; your current draft was not restyled.'); await onChanged() }
    catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  const withdraw = async () => {
    setBusy(true)
    try { await studioApi.withdrawBrand(site); setMessage('Brand approval withdrawn. Brand rules are no longer enforced.'); await onChanged() }
    catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  return <div className="studio-brand-review">
    <p className="studio-source-meta">Source: <a href={proposed.source.webViewLink} target="_blank" rel="noreferrer">{proposed.source.name || 'Brand kit'}</a> · Drive {proposed.source.drive_id} · version {proposed.source.version || '—'} · modified {when(proposed.source.modifiedTime)} · sha256 {proposed.source.sha256.slice(0, 12)} · {proposed.source.pages} pages{proposed.source.local_copy_matches ? ` · identical to ${proposed.source.local_copy.path} in the website` : ''}</p>
    <p className="studio-source-meta">Extraction: {proposed.extraction.method}, {proposed.extraction.model_calls} model calls{proposed.extraction.unextracted.length ? ` · not extracted: ${proposed.extraction.unextracted.join(', ')} (needs your review)` : ' · all sections read'}.</p>
    <p><strong>{approved ? `Approved ${when(approved.approved_at)}` : 'Proposed — not yet authoritative.'}</strong> {proposed.precedence}</p>
    <h4>Colour palette — {kit.direction}</h4>
    <div className="studio-brand-palette">{kit.palette.map(c => <span key={c.hex} title={c.use}><Swatch hex={c.hex} label={c.name} /></span>)}</div>
    <h4>Fonts and type scale</h4>
    <p>Headings {kit.fonts.heading.join(' or ')} (recommended {kit.fonts.recommended_heading || kit.fonts.heading[0]}) · body {kit.fonts.body.join(', ')} · accent font {kit.fonts.accent}</p>
    <table className="studio-table"><thead><tr><th>Element</th><th>Desktop</th><th>Mobile</th><th>Use</th></tr></thead><tbody>
      {Object.entries(kit.type_scale).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.desktop.join('–')}px</td><td>{v.mobile.join('–')}px</td><td>{v.usage}</td></tr>)}
    </tbody></table>
    <h4>Component rules</h4>
    <ul>{kit.ui_treatment.map(r => <li key={r}>{r}</li>)}{kit.colour_rules.map(r => <li key={r}>{r}</li>)}</ul>
    <h4>Logos and visual assets</h4>
    <div className="studio-logos">{proposed.logos.map(l => <figure key={l.id}>{l.url && !l.missing ? <img src={l.url} alt={l.label} loading="lazy" /> : <span className="studio-logo-drive">Drive</span>}<figcaption>{l.label}<small>{l.source}{l.path ? ` · ${l.path}` : ''}{l.source === 'google drive' ? (l.verified ? ' · verified' : ' · unverified') : ''}{l.webViewLink ? <> · <a href={l.webViewLink} target="_blank" rel="noreferrer">open</a></> : null}</small></figcaption></figure>)}</div>
    <h4>Copy, tone and positioning</h4>
    <p><strong>Voice:</strong> {kit.voice.use} <strong>Avoid:</strong> {kit.voice.avoid}</p>
    <p><strong>Brand-kit messaging:</strong> “{kit.messaging.headline}” · tagline “{kit.messaging.tagline}”. Current copy follows the newer positioning (see conflicts).</p>
    <h4>Conflicts with the actual website ({proposed.conflicts.filter(c => c.severity !== 'consistent').length})</h4>
    <table className="studio-table studio-conflicts"><thead><tr><th>Area</th><th>Website</th><th>Brand guide</th><th>Status</th></tr></thead><tbody>
      {proposed.conflicts.map((c, i) => <tr key={i} data-severity={c.severity}><td>{c.area}</td><td>{c.site}<small>{c.site_source}</small></td><td>{c.brand}{c.note && <small>{c.note}</small>}</td><td>{c.severity}</td></tr>)}
    </tbody></table>
    <h4>Proposed Studio defaults</h4>
    <ul className="studio-token-changes">{Object.entries(proposed.token_changes).map(([k, v]) => <li key={k}><code>{k}</code> {typeof v.site === 'string' && v.site.startsWith('#') ? <><Swatch hex={v.site} /> → <Swatch hex={v.brand} /></> : <>{String(v.site)} → <strong>{String(v.brand)}</strong></>}</li>)}</ul>
    {proposed.exceptions.map(e => <p key={e.token} className="data-notice">Exception · <code>{e.token}</code> {e.value}: {e.reason}</p>)}
    <p className="studio-source-meta">Approving makes these rules authoritative for Studio defaults and David. It does not change the website or your draft; apply the brand to a draft explicitly from the Brand panel.</p>
    <div className="studio-actions">
      <button className="studio-primary" disabled={busy || approved?.fingerprint === proposed.fingerprint} onClick={approve}>{approved?.fingerprint === proposed.fingerprint ? 'Brand configuration approved' : 'Approve brand configuration'}</button>
      {approved && <button disabled={busy} onClick={withdraw}>Withdraw approval</button>}
    </div>
  </div>
}

export default function WebsiteSources({ site, status, brand, error, onRefresh, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [folder, setFolder] = useState('')
  const check = async () => { setBusy(true); setMessage('Checking registered sources (bounded, read-only, no model)…'); try { await onRefresh(); setMessage('Sources checked.') } catch (e) { setMessage(e.message) } finally { setBusy(false) } }
  const choose = async value => {
    setBusy(true)
    try { await studioApi.driveFolder(site, value); setMessage(value ? 'Drive folder verified and selected.' : 'Drive asset folder cleared.'); setFolder(''); await onChanged() }
    catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  if (error && !status) return <div className="studio-sources"><p className="data-notice warning" role="alert">{error}</p></div>
  if (!status) return <div className="studio-sources"><p role="status">Reading sources…</p></div>
  const drive = status.drive
  return <div className="studio-sources">
    <div className="studio-sources-bar">
      <p>Sources for <strong>{status.site.name}</strong> · scope {status.site.client_scope} · 0 model calls · last full check {when(status.last_refresh)}</p>
      <button className="studio-primary" disabled={busy} onClick={check}>{busy ? 'Checking…' : 'Check sources'}</button>
    </div>
    {message && <p role="status" className="studio-status">{message}</p>}
    {error && <p className="data-notice warning" role="alert">{error}</p>}
    <div className="studio-source-grid">
      <Card title="Local website" status={status.local}>
        <dl><dt>Directory</dt><dd className="mono">{status.local.repository}</dd><dt>Repository</dt><dd className="mono">{status.local.origin || '—'}</dd><dt>Branch</dt><dd className="mono">{status.local.branch || '—'}</dd><dt>HEAD</dt><dd className="mono">{status.local.head?.slice(0, 12) || '—'} (Studio base {status.local.base_ref.slice(0, 12)})</dd><dt>Uncommitted</dt><dd>{status.local.dirty ?? '—'}</dd><dt>Writes</dt><dd>{status.local.writes}</dd></dl>
        {status.reference_checkouts.map(ref => <details key={ref.id} className="studio-reference"><summary>{ref.label} <StateBadge state={ref.state} /></summary>
          <p className="mono">{ref.path}</p><p>{ref.detail}</p><p className="studio-source-meta">{ref.note}</p>
          {ref.head && <p className="studio-source-meta">Windows HEAD {ref.head.slice(0, 12)} · snapshot taken from {ref.snapshot_head?.slice(0, 12)} → WSL {ref.snapshot_commit?.slice(0, 12)}</p>}
          {!!ref.not_in_snapshot?.length && <details><summary>Windows-only changes ({ref.not_in_snapshot_count})</summary><ul>{ref.not_in_snapshot.map(p => <li className="mono" key={p}>{p}</li>)}</ul></details>}
        </details>)}
      </Card>
      <Card title="Google Drive" status={drive}>
        <p className="studio-source-meta">{drive.connector}</p>
        <p><strong>Website asset folder:</strong> {drive.asset_folder ? <>{drive.asset_folder.name} <code>{drive.asset_folder.id}</code> · {drive.asset_folder.images} images, {drive.asset_folder.documents} documents · verified {when(drive.asset_folder.verified_at)} <button disabled={busy} onClick={() => choose(null)}>Clear</button></> : 'not selected'}</p>
        <div className="studio-folder-pick"><label htmlFor="studio-drive-folder">Choose asset folder (Drive link or ID)</label><input id="studio-drive-folder" value={folder} onChange={e => setFolder(e.target.value)} placeholder="https://drive.google.com/drive/folders/…" /><button disabled={busy || !folder.trim()} onClick={() => choose(folder)}>Verify & select</button></div>
        {Object.entries(drive.folders || {}).map(([id, f]) => <details key={id}><summary>{f.label} · {f.children.length} item(s)</summary><p className="studio-source-meta">{f.name} · <code>{id}</code> · modified {when(f.modifiedTime)}</p><ul>{f.children.map(c => <li key={c.id}>{c.webViewLink ? <a href={c.webViewLink} target="_blank" rel="noreferrer">{c.name}</a> : c.name} <small>{c.mimeType.split('/').pop().split('.').pop()} · {when(c.modifiedTime)}</small></li>)}</ul></details>)}
        {drive.excluded_folders.map(f => <p key={f.id} className="studio-source-meta">Excluded <code>{f.id}</code>: {f.reason}</p>)}
      </Card>
      <Card title="Business Brain" status={status.brain}>
        {status.brain.reader && <p className="studio-source-meta">{status.brain.reader}</p>}
        {status.brain.records.map(r => <details key={r.reference}><summary><code>{r.reference}</code> <StateBadge state={r.state} /></summary>
          {r.state === 'connected' ? <><p className="studio-source-meta">{r.title} · {r.authority} · modified {when(r.modified)} · sha256 {r.sha256.slice(0, 12)}</p><pre className="studio-excerpt">{r.excerpt}{r.truncated ? '\n…' : ''}</pre></> : <p>{r.detail}</p>}
        </details>)}
        <h4>Website guidance files</h4>
        <ul>{status.guidance.map(g => <li key={g.path}><span className="mono">{g.path}</span> {g.state === 'connected' ? <small>sha256 {g.sha256.slice(0, 12)}</small> : <StateBadge state={g.state} />}</li>)}</ul>
      </Card>
      <Card title="Brand Guide" status={status.brand}>
        <p><strong>Approval:</strong> {status.brand.approval === 'approved' ? `approved ${when(status.brand.approved_at)}` : status.brand.approval === 'proposed' ? 'proposed — awaiting your approval' : 'none'}{status.brand.conflicts ? ` · ${status.brand.conflicts} conflict(s) with the website` : ''}</p>
      </Card>
    </div>
    <section className="studio-source-card studio-brand-card"><header><h3>Brand configuration</h3>{status.brand && <StateBadge state={status.brand.state} />}</header>
      <BrandReview site={site} brand={brand} onChanged={onChanged} busy={busy} setBusy={setBusy} setMessage={setMessage} />
    </section>
  </div>
}
