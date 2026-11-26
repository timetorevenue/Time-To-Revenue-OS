import { useEffect, useMemo, useRef, useState } from 'react'
import './CurrentWebsiteStudio.css'
import { currentPreview } from '../currentWebsitePreview'

const base = '/api/current-website'
export async function currentApi(path = '', body, method = 'POST') {
  const res = await fetch(base + path, body === undefined ? {} : { method, headers: { 'Content-Type': 'application/json', 'X-TTROS-Connections': '1' }, body: JSON.stringify(body) })
  const data = await res.json()
  if (!res.ok) throw Error(data.detail || `Request failed (${res.status})`)
  return data
}
const clone = value => JSON.parse(JSON.stringify(value))
function PageEditor({ page, meta, onDirty }) {
  const [loaded, setLoaded] = useState(null), [data, setData] = useState(null)
  const [past, setPast] = useState([]), [future, setFuture] = useState([])
  const [selected, setSelected] = useState('s0'), [field, setField] = useState('')
  const [status, setStatus] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [request, setRequest] = useState('Replace selected text with: '), [proposal, setProposal] = useState(null)
  const [width, setWidth] = useState('desktop'), [panel, setPanel] = useState('content')
  const frame = useRef(null)
  async function load() {
    const value = await currentApi('/pages/' + page)
    setLoaded(value); setData(value.data); setPast([]); setFuture([]); setProposal(null)
    setSelected(value.data.sections[0]?.id); setField(''); onDirty(false)
    return value
  }
  useEffect(() => { load().catch(e => setError(e.message)) }, [page])
  useEffect(() => { if (data && !data.sections.some(s => s.id === selected)) { setSelected(data.sections[0]?.id); setField('') } }, [data, selected])
  const dirty = !!data && JSON.stringify(data) !== JSON.stringify(loaded?.data)
  useEffect(() => { onDirty(dirty); const prevent = e => { if (dirty) { e.preventDefault(); e.returnValue = '' } }; window.addEventListener('beforeunload', prevent); return () => window.removeEventListener('beforeunload', prevent) }, [dirty])
  const markup = useMemo(() => data && loaded ? currentPreview(loaded.capture, data) : '', [data, loaded])
  const item = data?.sections.find(s => s.id === selected)
  const source = loaded?.capture.sections.find(s => s.id === item?.source)
  const activeField = source?.texts.find(f => f.id === field) || source?.texts[0]
  function change(next, message = 'Unsaved local changes') {
    setPast(old => [...old.slice(-99), clone(data)]); setFuture([]); setData(next); setStatus(message); setError('')
  }
  function update(fn) { const next = clone(data); fn(next.sections.find(s => s.id === selected)); change(next) }
  function undo() { if (!past.length) return; setFuture(x => [clone(data), ...x]); setData(past.at(-1)); setPast(x => x.slice(0, -1)); setStatus('Undid change') }
  function redo() { if (!future.length) return; setPast(x => [...x, clone(data)]); setData(future[0]); setFuture(x => x.slice(1)); setStatus('Redid change') }
  async function save() {
    setBusy(true); setError('')
    try { const result = await currentApi('/pages/' + page + '/drafts', { data, base_version: loaded.version, source_hash: loaded.source_hash }); setLoaded(old => ({ ...old, data: clone(data), version: result.version, versions: [{ version: result.version, at: Date.now() / 1000 }, ...old.versions] })); setStatus(`Draft v${result.version} saved locally. Production unchanged.`) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  function move(delta) { const next = clone(data), index = next.sections.findIndex(s => s.id === selected), target = index + delta; if (target < 0 || target >= next.sections.length) return; [next.sections[index], next.sections[target]] = [next.sections[target], next.sections[index]]; change(next) }
  function duplicate() { const next = clone(data), index = next.sections.findIndex(s => s.id === selected), copy = clone(item); let n = 1; while (next.sections.some(s => s.id === item.source + '-copy' + n)) n++; copy.id = item.source + '-copy' + n; next.sections.splice(index + 1, 0, copy); change(next); setSelected(copy.id) }
  async function propose() {
    setBusy(true); setError('')
    try { const value = await currentApi('/pages/' + page + '/propose', { data, base_version: loaded.version, section: selected, field: activeField.id, request }); setProposal(value); setStatus('David proposal ready for review. No draft was changed.') }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  function applyProposal() {
    const edit = proposal.edit, current = data.sections.find(s => s.id === edit.section)
    const original = loaded.capture.sections.find(s => s.id === current?.source)?.texts.find(f => f.id === edit.field)
    if (!current || (current.texts[edit.field] ?? original?.text) !== edit.before || proposal.source_hash !== loaded.source_hash) { setError('Proposal is stale. Request a new one.'); return }
    const next = clone(data); next.sections.find(s => s.id === edit.section).texts[edit.field] = edit.after; change(next, 'Accepted into local draft. Save to persist.'); setProposal(null)
  }
  function wireFrame() {
    const doc = frame.current?.contentDocument
    if (!doc) return
    doc.addEventListener('submit', e => e.preventDefault(), true)
    doc.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation()
      const section = e.target.closest('[data-current-section]')
      if (section) { setSelected(section.dataset.currentSection); setField(e.target.closest('[data-current-field]')?.dataset.currentField || ''); setPanel(e.target.closest('img') ? 'images' : 'content') }
    }, true)
  }
  if (!loaded || !data) return <p role={error ? 'alert' : 'status'}>{error || 'Opening the current published page…'}</p>
  return <>
    <div className="current-toolbar">
      <strong>{loaded.capture.route} · Draft v{loaded.version}{dirty ? ' · Unsaved' : ''}</strong>
      <button onClick={undo} disabled={!past.length}>Undo</button><button onClick={redo} disabled={!future.length}>Redo</button>
      <button onClick={save} disabled={busy || !dirty}>Save draft</button>
      <button onClick={() => load().then(() => setStatus('Latest server draft loaded')).catch(e => setError(e.message))} disabled={dirty || busy}>Reload saved draft</button>
      <select aria-label="Restore draft version" value="" onChange={async e => { if (!e.target.value) return; try { const old = await currentApi(`/pages/${page}/versions/${e.target.value}`); if (old.source_hash !== loaded.source_hash) throw Error('Version belongs to a different published capture'); change(old.data, 'Previous version restored as an unsaved draft') } catch (e) { setError(e.message) } }}><option value="">Version history</option>{loaded.versions.map(v => <option key={v.version} value={v.version}>Draft v{v.version}</option>)}</select>
      <select aria-label="Preview viewport" value={width} onChange={e => setWidth(e.target.value)}><option value="desktop">Desktop</option><option value="mobile">Mobile</option></select>
    </div>
    <p role="status" className="current-status">{status || 'Select text or an image in the real page preview to edit it.'}</p>
    {error && <p role="alert" className="data-notice warning">{error}</p>}
    <div className="current-editor">
      <aside className="current-controls">
        <label>Page sections<select aria-label="Page sections" value={selected} onChange={e => { setSelected(e.target.value); setField('') }}>{data.sections.map(s => <option key={s.id} value={s.id}>{loaded.capture.sections.find(x => x.id === s.source)?.label} {s.id.includes('copy') ? '(copy)' : ''}</option>)}</select></label>
        <div className="current-buttons"><button onClick={() => move(-1)}>Move up</button><button onClick={() => move(1)}>Move down</button><button onClick={duplicate}>Duplicate section</button><button disabled={data.sections.length <= 1} onClick={() => { const next = clone(data); next.sections = next.sections.filter(s => s.id !== selected); change(next); setSelected(next.sections[0].id) }}>Remove section</button></div>
        <div className="current-buttons">{['content', 'images', 'design', 'david'].map(id => <button key={id} aria-pressed={panel === id} onClick={() => setPanel(id)}>{id === 'david' ? 'David proposal' : id[0].toUpperCase() + id.slice(1)}</button>)}</div>
        {item && source && panel === 'content' && <>
          <label>Text field<select aria-label="Text field" value={activeField?.id || ''} onChange={e => setField(e.target.value)}>{source.texts.map(f => <option key={f.id} value={f.id}>{f.tag} · {f.text.slice(0, 75)}</option>)}</select></label>
          {activeField && <label>Selected text<textarea aria-label="Selected text" rows={6} maxLength={6000} value={item.texts[activeField.id] ?? activeField.text} onChange={e => update(s => s.texts[activeField.id] = e.target.value)} /></label>}
        </>}
        {item && panel === 'images' && <>{!source.images.length && <p>This section has no image. Select the Founder section to edit its portrait.</p>}{source.images.map(img => {
          const value = item.images[img.id] || img
          return <fieldset key={img.id}><legend>Existing image</legend><img className="current-image" src={value.src} alt={value.alt} /><label>Image URL<input value={value.src} onChange={e => update(s => s.images[img.id] = { src: e.target.value, alt: value.alt })} /></label><label>Alternative text<input value={value.alt} onChange={e => update(s => s.images[img.id] = { src: value.src, alt: e.target.value })} /></label><label>Upload replacement<input type="file" accept="image/png,image/jpeg,image/webp" onChange={async e => { const file = e.target.files?.[0]; if (!file) return; try { const res = await fetch(base + '/assets', { method: 'POST', headers: { 'X-TTROS-Connections': '1' }, body: file }); const result = await res.json(); if (!res.ok) throw Error(result.detail); update(s => s.images[img.id] = { src: result.url, alt: value.alt }) } catch (e) { setError(e.message) } }} /></label></fieldset>
        })}</>}
        {item && panel === 'design' && <>
          <p>Override this section’s published design in the local draft.</p>
          {['color', 'backgroundColor'].map(key => <label key={key}>{key === 'color' ? 'Text colour' : 'Background colour'}<input type="color" aria-label={key} value={item.styles[key] || (key === 'color' ? '#111827' : '#ffffff')} onChange={e => update(s => s.styles[key] = e.target.value)} /></label>)}
          <label>Font<select value={item.styles.fontFamily || 'inherit'} onChange={e => update(s => s.styles.fontFamily = e.target.value)}>{['inherit', 'Arial, sans-serif', 'Georgia, serif', 'system-ui, sans-serif'].map(x => <option key={x}>{x}</option>)}</select></label>
          {['fontSize', 'padding', 'borderRadius'].map(key => <label key={key}>{key} (px)<input aria-label={key} type="number" min="0" max="160" value={item.styles[key] ?? ''} onChange={e => update(s => { if (e.target.value === '') delete s.styles[key]; else s.styles[key] = Number(e.target.value) })} /></label>)}
          <label>Text alignment<select value={item.styles.textAlign || 'left'} onChange={e => update(s => s.styles.textAlign = e.target.value)}>{['left', 'center', 'right'].map(x => <option key={x}>{x}</option>)}</select></label><button onClick={() => update(s => s.styles = {})}>Reset section design</button>
        </>}
        {panel === 'david' && <>
          <h3>David · Propose a local change</h3><p>Selected text: {activeField ? item.texts[activeField.id] ?? activeField.text : 'Select text first'}</p>
          <p>Use “Replace selected text with: …” for an exact replacement. This bounded David action uses no model calls. Freeform generation needs separate authorization.</p>
          <label>Request to David<textarea aria-label="Request to David" rows={5} value={request} onChange={e => setRequest(e.target.value)} /></label><button disabled={busy || !activeField} onClick={propose}>Propose change</button>
          {proposal && <article className="current-proposal"><h4>David proposal #{proposal.id}</h4><p>{proposal.summary}</p><del>{proposal.edit.before}</del><p><ins>{proposal.edit.after}</ins></p><p>Applied: no · Published: no · Model calls: 0</p><button onClick={applyProposal}>Accept into local draft</button><button onClick={() => setProposal(null)}>Dismiss proposal</button></article>}
        </>}
      </aside>
      <div className="current-canvas"><iframe ref={frame} onLoad={wireFrame} title={`Current EXCO page preview: ${loaded.capture.route}`} sandbox="allow-same-origin" srcDoc={markup} style={{ width: width === 'mobile' ? 390 : '100%' }} /></div>
    </div>
    <p className="source-caption">Captured {loaded.capture.captured_at} · Deployment {meta.website.deployment} · Capture SHA-256 {loaded.source_hash} · Published-page preview; source build not yet verified.</p>
  </>
}

export default function CurrentWebsiteStudio({ onLegacy }) {
  const [meta, setMeta] = useState(null), [error, setError] = useState(''), [page, setPage] = useState('home'), [dirty, setDirty] = useState(false)
  useEffect(() => { currentApi().then(setMeta).catch(e => setError(e.message)) }, [])
  const choose = id => { if (dirty && !window.confirm('Discard unsaved changes on this page? Saved versions remain available.')) return; setDirty(false); setPage(id) }
  return <section className="current-studio" aria-label="Current live website Studio">
    <div className="current-heading"><div><h2>Website Studio · Current Example Company site</h2><a href="https://www.core-site.example" target="_blank" rel="noreferrer">www.core-site.example</a><p>Local drafts · Publication held · No production website changes</p></div><button onClick={() => { if (!dirty || window.confirm('Discard unsaved changes and open the preserved release candidate?')) onLegacy() }}>Open unpublished release candidate</button></div>
    {error && <p role="alert">{error}</p>}
    {meta && <><details className="current-provenance"><summary>Source access needs attention · Published-page capture</summary><p>{meta.source_blocker}</p><p>Vercel-verified source supplied by operator: {meta.website.source_commit} on {meta.website.source_branch}. This SHA is not yet available locally. Existing release candidates are preserved.</p><a href="/settings/connections">Settings → Connections and secure setup</a></details>
      <label className="current-page-picker">Actual website page<select aria-label="Actual website page" value={page} onChange={e => choose(e.target.value)}>{meta.pages.map(p => <option key={p.id} value={p.id}>{p.route} · {p.title}</option>)}</select></label>
      <PageEditor key={page} page={page} meta={meta} onDirty={setDirty} />
    </>}
  </section>
}
