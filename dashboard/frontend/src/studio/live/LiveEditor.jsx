// TTROS Website Studio — live website visual editor. Edits the real Next.js source of the live
// site (pinned commit) element by element. Every change is one Puck history step (Undo/Redo with
// Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z or Ctrl+Y), autosaved to this browser and to the server, kept
// across reloads, and saved as immutable timestamped versions that can be previewed, restored
// (as a new version) and built into the real site in an isolated fixture. Zero model calls.
// The version's code layer (owner CSS, header scroll behaviour, source-file edits; code.js) rides in
// Puck's root props, so code edits are history steps too. Manual edits and accepted Website Manager
// proposals go through the same operations (code.js applyOps).
// Revisit: with the live model schema, the Studio API or Puck upgrades. Last touched: 2026-11-22.
import {Component, useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {Puck, Render, createUsePuck} from '@puckeditor/core'
import {FileText, Plus, Layers, Images, MousePointer2, Sparkles, Search, Code2, Settings2, History, Play, ShieldCheck, PanelLeft, PanelRight, Undo2, Redo2, Monitor, Tablet, Smartphone, Maximize2, Minimize2, Check, Circle, X, Hand, Minus, MoreHorizontal, ArrowLeft} from 'lucide-react'
import '@puckeditor/core/puck.css'
import {liveClient} from './api.js'
import {compose, split, owners, changedDocs, canonical} from './compose.js'
import {liveConfig, LiveFrame, CanvasContext, DataContext, toPuck, fromPuck, bpForWidth} from './canvas.jsx'
import {BREAKPOINTS, PALETTE, STYLE_PROPS, VOID, AUTHORED_TAGS, findNode, walk, instantiate, removeNode, insertNode, moveNode, wrapNode, unwrapNode, updateProps, setStyle, duplicateSubtree, validateTree, labelOf, ID, newId, styleProblem, treeCss} from './model.js'
import {idbGet, idbSet, idbDel, keys, debounce} from './persist.js'
import {applyOps, normalizeCode, headerCss} from './code.js'
import {DesignControls, CodePanel, SitePanel, ManagerPanel, PagePanel, NewPageDialog, ImageEditor} from './panels.jsx'
import {studioPages, pageRoutes, docKey as pageDocKey} from './pages.js'
import './LiveEditor.css'

const usePuck = createUsePuck()
const VIEWPORTS = BREAKPOINTS.map(b => ({width: b.width, height: 'auto', label: b.label, icon: b.id === 'd' ? 'Monitor' : b.id === 't' ? 'Tablet' : 'Smartphone'}))
const clone = v => JSON.parse(JSON.stringify(v))
const hash36 = s => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0).toString(36) }
const fmtTime = iso => { try { return new Date(iso).toLocaleString('en-CA', {dateStyle: 'medium', timeStyle: 'medium'}) } catch { return iso } }
// Puck item selector {zone, index} computed from content itself (Puck's own lookup can be stale in callbacks).
export const selectorFor = (content, id) => { const hit = id && findNode(content, id); return hit ? {zone: hit.parent ? `${hit.parent.props.id}:${hit.slot}` : 'root:default-zone', index: hit.index} : null }
// Preview frames show the site at exactly 1280/390 px of content: content-box width plus the
// browser's classic scrollbar width (0 with overlay scrollbars), measured once.
const SCROLLBAR = (() => { try { const d = document.createElement('div'); d.style.cssText = 'position:absolute;top:-999px;width:100px;height:100px;overflow:scroll'; document.body.appendChild(d); const w = d.offsetWidth - d.clientWidth; d.remove(); return w } catch { return 0 } })()
const pageGroup = p => p.created ? 'Created in the Studio' : p.route.startsWith('/blog/') ? 'Blog posts' : p.route.startsWith('/resources/') ? 'Resources' : p.route === '/_not-found' ? 'System' : 'Pages'
// Imported pages plus the pages and posts created in the Studio (code layer `pages`).
const allPagesOf = (model, code) => (model ? [...model.pages, ...studioPages(code?.pages || {}, model)] : [])
// The capture a page's canvas uses: created pages borrow the shared header/footer rendering of the
// homepage (landing) or of a published post (post layout); their own elements are literal.
const captureRouteOf = (page, model) => !page?.created ? page?.route : page.created === 'post'
 ? (model.pages.find(p => p.doc === 'app/blog/[slug]/page.tsx' && p.bind && Object.keys(p.bind).length)?.route || '/') : '/'
// Documents the server knows: imported ones and those of created pages (a removed page's document is dropped).
const knownDocs = (docs, model, code) => Object.fromEntries(Object.entries(docs).filter(([id]) => model.docs[id] || code?.pages?.[id]))

export default function LiveEditor({site = 'default-site', onOpenReleases, onOpenCapture, onOpenSources, onOpenCandidate}) {
 const client = useMemo(() => liveClient(site), [site])
 const [status, setStatus] = useState(null)
 const [model, setModel] = useState(null)
 const [error, setError] = useState('')
 const [message, setMessage] = useState('')
 const docsRef = useRef(null) // working documents (all)
 const baselineRef = useRef(null) // documents of the latest saved version
 const codeRef = useRef({}) // working code layer
 const baselineCodeRef = useRef({}) // code layer of the latest saved version
 const acceptedRef = useRef(new Set()) // accepted Website Manager proposals not yet saved
 const [latest, setLatest] = useState(0)
 const [versions, setVersions] = useState([])
 const [dirty, setDirty] = useState([])
 const [pageId, setPageId] = useState(() => { try { return localStorage.getItem(keys.page(site)) || 'home' } catch { return 'home' } })
 const [captures, setCaptures] = useState({})
 const [mount, setMount] = useState(null) // {key, data, history}
 const [recovered, setRecovered] = useState(null)
 const [assets, setAssets] = useState({library: [], site: [], drive: []})
 const [autosaved, setAutosaved] = useState(null)
 const [docsReady, setDocsReady] = useState(0)
 // Workspace choices survive page/canvas remounts without touching draft storage keys.
 const [workspace, setWorkspace] = useState(() => ({left: 'pages', right: 'manager', showLeft: false, showRight: window.innerWidth >= 700, focus: true, leftWidth: 280, rightWidth: 360, zoom: 'fit', pan: false, canvasOnly: false}))
 const [previewBuild, setPreviewBuild] = useState(null)
 const managerSessions = useRef({})

 const allPages = allPagesOf(model, codeRef.current)
 const page = allPages.find(p => p.id === pageId) || model?.pages[0]
 const captureRoute = model && page ? captureRouteOf(page, model) : null
 const load = useCallback(async () => {
  setError('')
  try {
   const s = await client.status()
   setStatus(s)
   if (!s.ready) return
   const [m, v, auto, a] = await Promise.all([client.model(), client.versions(), client.autosave().catch(() => ({autosave: null})), client.assets().catch(() => null)])
   setModel(m)
   if (a) setAssets(a)
   setVersions(v.versions)
   setLatest(v.latest)
   const saved = v.latest ? await client.version(v.latest) : null
   const base = saved ? saved.docs : Object.fromEntries(Object.entries(m.docs).map(([id, d]) => [id, d.tree]))
   baselineRef.current = base
   baselineCodeRef.current = normalizeCode(saved?.code || {})
   // Crash/reopen: the newest autosave (browser or server) is reapplied over the saved version.
   const local = await idbGet(keys.autosave(site))
   const server = auto.autosave
   const pick = [local, server].filter(r => r?.docs && r.commit === m.commit).sort((x, y) => String(y.at).localeCompare(String(x.at)))[0]
   const docs = {...clone(base), ...(pick ? clone(pick.docs) : {})}
   docsRef.current = docs
   codeRef.current = normalizeCode(pick?.code ?? baselineCodeRef.current)
   setDocsReady(n => n + 1)
   const changed = dirtyOf(docs, base, codeRef.current, baselineCodeRef.current)
   if (pick && changed.length) setRecovered({at: pick.at, from: pick === local ? 'this browser' : 'the server autosave', base: pick.base_version, latest: v.latest})
   setDirty(changed)
  } catch (e) { setError(e.message) }
 }, [client, site])
 useEffect(() => { load() }, [load])
 // Read-only inspection hook for browser acceptance tests (no write access).
 useEffect(() => { window.__ttrosLive = {docs: () => clone(docsRef.current), baseline: () => clone(baselineRef.current), code: () => clone(codeRef.current), dirty: () => dirtyOf(docsRef.current || {}, baselineRef.current || {}, codeRef.current, baselineCodeRef.current)} }, [])

 // Capture (rendered attributes / functional HTML) per route.
 useEffect(() => {
  if (!page || !captureRoute || captures[captureRoute]) return
  client.capture(captureRoute).then(c => setCaptures(all => ({...all, [captureRoute]: c})), e => setError(e.message))
 }, [page, captureRoute, client, captures])

 // (Re)mount Puck for the page with its persisted undo history when it still matches the documents.
 const remount = useCallback(async (reason) => {
  if (!page || !docsRef.current) return
  const content = toPuck(compose(page, docsRef.current))
  const data = {root: {props: {code: clone(codeRef.current)}}, content}
  const stored = await idbGet(keys.history(site, page.id))
  let history
  // Puck stores empty slot arrays; compare normalised model forms (and the code layer).
  const top = stored?.histories?.[stored.index]?.state?.data
  const same = stored?.histories?.length && canonical(fromPuck(top?.content || [])) === canonical(fromPuck(content)) && canonical(normalizeCode(top?.root?.props?.code || {})) === canonical(codeRef.current)
  if (same) history = {histories: stored.histories, index: stored.index, appendData: false}
  setMount({key: `${page.id}:${Date.now()}`, page: page.id, data, history, reason})
 }, [page, site])
 useEffect(() => { if (model && page && captures[captureRoute] && docsReady) remount('open') }, [model, page?.id, captures[captureRoute], docsReady]) // eslint-disable-line react-hooks/exhaustive-deps

 const autosaveLocal = useMemo(() => debounce(async () => {
  const docs = docsRef.current, base = baselineRef.current
  if (!docs || !base || !model) return
  const changed = changedDocs(knownDocs(docs, model, codeRef.current), base)
  const record = {at: new Date().toISOString(), base_version: latest, page: page?.id, commit: model.commit, docs: Object.fromEntries(changed.map(id => [id, docs[id]])), code: codeRef.current}
  await idbSet(keys.autosave(site), record)
  setAutosaved({at: record.at, where: 'browser'})
 }, 600), [model, latest, page?.id, site]) // eslint-disable-line react-hooks/exhaustive-deps
 const autosaveServer = useMemo(() => debounce(async () => {
  const docs = docsRef.current, base = baselineRef.current
  if (!docs || !base || !model) return
  const changed = changedDocs(knownDocs(docs, model, codeRef.current), base)
  const codeChanged = canonical(codeRef.current) !== canonical(baselineCodeRef.current)
  try {
   if (!changed.length && !codeChanged) await client.dropAutosave()
   else { const r = await client.putAutosave({base_version: latest, page: page?.id || '', docs: Object.fromEntries(changed.map(id => [id, docs[id]])), code: codeRef.current}); setAutosaved({at: r.at, where: 'server'}) }
  } catch (e) { setMessage(`Autosave to server failed (kept in this browser): ${e.message}`) }
 }, 2500), [client, latest, page?.id, model]) // eslint-disable-line react-hooks/exhaustive-deps
 useEffect(() => () => { autosaveLocal.flush(); autosaveServer.flush() }, [autosaveLocal, autosaveServer])
 useEffect(() => { const flush = () => { autosaveLocal.flush() }; window.addEventListener('beforeunload', flush); return () => window.removeEventListener('beforeunload', flush) }, [autosaveLocal])

 // Every Puck change: documents are split back out, validated and autosaved.
 const onDocs = useCallback((next, code) => {
  docsRef.current = {...docsRef.current, ...next}
  if (code) codeRef.current = normalizeCode(code)
  setDirty(dirtyOf(docsRef.current, baselineRef.current, codeRef.current, baselineCodeRef.current))
  autosaveLocal(); autosaveServer()
 }, [autosaveLocal, autosaveServer])

 const openPage = id => {
  if (id === page?.id) return // already open (re-selecting it must not unmount the canvas)
  autosaveLocal.flush()
  setMount(null) // never render one page's canvas under another page's document
  setPageId(id)
  try { localStorage.setItem(keys.page(site), id) } catch { /* convenience */ }
 }
 const saveVersion = async note => {
  const docs = knownDocs(docsRef.current, model, codeRef.current), base = baselineRef.current
  const changed = changedDocs(docs, base)
  const codeChanged = canonical(codeRef.current) !== canonical(baselineCodeRef.current)
  if (!changed.length && !codeChanged) { setMessage('No unsaved changes.'); return null }
  const proposals = [...acceptedRef.current]
  try {
   const saved = await client.save({base_version: latest, docs: Object.fromEntries(changed.map(id => [id, docs[id]])), ...(codeChanged ? {code: codeRef.current} : {}), proposals,
    note: note || [changed.length ? `Visual edits: ${changed.length} document${changed.length === 1 ? '' : 's'}` : '', codeChanged ? 'code layer' : '', proposals.length ? `Website Manager ${proposals.join(', ')}` : ''].filter(Boolean).join(' · ')})
   baselineRef.current = clone(docs)
   baselineCodeRef.current = clone(codeRef.current)
   acceptedRef.current = new Set()
   setLatest(saved.version)
   setVersions(v => [...v, {...saved, documents: Object.keys(saved.docs)}])
   setDirty([])
   setRecovered(null)
   await idbDel(keys.autosave(site))
   setMessage(`Saved version v${saved.version} at ${fmtTime(saved.at)} (${saved.changed.length} document${saved.changed.length === 1 ? '' : 's'} changed).`)
   return saved
  } catch (e) { setMessage(`Not saved: ${e.message}`); return null }
 }
 const discardRecovered = async () => {
  docsRef.current = clone(baselineRef.current)
  codeRef.current = clone(baselineCodeRef.current)
  setDirty([]); setRecovered(null)
  await idbDel(keys.autosave(site)); await client.dropAutosave().catch(() => {})
  await idbDel(keys.history(site, page.id))
  remount('discard')
  setMessage('Recovered changes discarded; showing the latest saved version.')
 }

 if (error && !status) return <section className="live-studio"><p role="alert">{error}</p></section>
 if (!status) return <section className="live-studio"><p role="status">Opening the live website editor…</p></section>
 if (!status.ready) return <section className="live-studio"><ImportPanel status={status} client={client} onDone={load}/></section>
 if (!model || !page) return <section className="live-studio"><p role="status">Loading the live website model…</p></section>
 const capture = captures[captureRoute]
 return <section className="live-studio" data-page={page.id}>
  {error && <p role="alert">{error}</p>}
  {!capture || !mount || mount.page !== page.id ? <p role="status">Preparing {page.label}…</p> :
   <EditorBoundary key={`b:${mount.key}`} onReset={() => remount('recover')}><PuckEditor key={mount.key} mount={mount} page={page} model={model} capture={capture} client={client} assets={assets} setAssets={setAssets}
    docs={docsRef} baseline={baselineRef} codeRef={codeRef} baselineCodeRef={baselineCodeRef} acceptedRef={acceptedRef} onDocs={onDocs} dirty={dirty} latest={latest} versions={versions} setVersions={setVersions} setLatest={setLatest}
    saveVersion={saveVersion} message={message} setMessage={setMessage} autosaved={autosaved} openPage={openPage} remount={remount} site={site} allPages={allPages}
    onOpenReleases={onOpenReleases} onOpenCapture={onOpenCapture} onOpenSources={onOpenSources} onOpenCandidate={onOpenCandidate} recovered={recovered} discardRecovered={discardRecovered} workspace={workspace} setWorkspace={setWorkspace} previewBuild={previewBuild} setPreviewBuild={setPreviewBuild} managerSessions={managerSessions}/></EditorBoundary>}
 </section>
}

// The link an element belongs to: itself, or its nearest a/Link ancestor.
function linkOf(content, id) {
 const chain = []
 const find = (list, trail) => { for (const item of list) { if (item.props.id === id) { chain.push(item, ...trail.slice().reverse()); return true } for (const s of ['kids', 'alt']) if (Array.isArray(item.props[s]) && find(item.props[s], [...trail, item])) return true } return false }
 find(content, [])
 return chain.find(n => n.type === 'El' && ['a', 'Link'].includes(n.props.tag)) || null
}
// Owner CSS for a static render: asset references resolved, header CSS when header behaviour is on.
const ownerCss = (code, client) => (code?.css || '').replace(/asset:([a-f0-9]{64})/g, (_, id) => client.assetUrl(id)) + (code?.header ? `\n${headerCss()}` : '')
// A static render of one page from documents + code layer (version previews, proposal previews).
function staticPage({page, model, client, capture, assets, docs, code}) {
 const content = toPuck(compose(page, docs))
 const siteFiles = new Set(assets.site.map(a => a.path))
 const ctx = {editing: false, page, codes: model.codes, capture, siteFiles, pristineEmpty: new Set(), cssUrl: client.canvasUrl('site.css'), canvasUrl: n => client.canvasUrl(n), assetUrl: id => client.assetUrl(id), siteFileUrl: x => client.siteFileUrl(x)}
 const body = renderToStaticMarkup(<CanvasContext.Provider value={ctx}><DataContext.Provider value={{content}}><Render config={liveConfig} data={{root: {props: {}}, content}}/></DataContext.Provider></CanvasContext.Provider>)
 const css = treeCss(fromPuck(content), a => client.assetUrl(a.slice(6)))
 return `<!doctype html><html class="${capture.htmlClass}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${client.canvasUrl(page.css || 'site.css')}"><style>${css}</style><style data-studio-owner-css="">${ownerCss(code, client)}</style></head><body class="${capture.bodyClass}">${body}</body></html>`
}

// Unsaved documents plus 'code-layer' when the code layer differs from the saved version.
const dirtyOf = (docs, base, code, baseCode) => [...changedDocs(docs, base), ...(canonical(normalizeCode(code || {})) !== canonical(normalizeCode(baseCode || {})) ? ['code-layer'] : [])]

// A canvas fault must never take the Dashboard down; unsaved work is in the autosave.
class EditorBoundary extends Component {
 constructor(props) { super(props); this.state = {error: null} }
 static getDerivedStateFromError(error) { return {error} }
 render() {
  if (!this.state.error) return this.props.children
  return <div role="alert" className="live-banner">The editor hit an error: {String(this.state.error.message || this.state.error).slice(0, 300)}. Your changes are autosaved. <button onClick={() => { this.setState({error: null}); this.props.onReset?.() }}>Reload editor</button></div>
 }
}

function ImportPanel({status, client, onDone}) {
 const [busy, setBusy] = useState(false), [note, setNote] = useState('')
 const run = async () => {
  setBusy(true)
  try {
   await client.startImport()
   for (let i = 0; i < 400; i++) { await new Promise(r => setTimeout(r, 3000)); const s = await client.status(); setNote(s.importing?.state || ''); if (s.ready && s.importing?.state !== 'running') break; if (s.importing?.state === 'failed') throw Error(s.importing.error) }
   onDone()
  } catch (e) { setNote(e.message) } finally { setBusy(false) }
 }
 return <div className="live-import"><h2>Live website editor</h2>
  <p>Import the live website source <code>{status.commit.slice(0, 12)}</code> (read from Git objects; nothing is written to the website checkout). The import builds the site twice in an isolated folder to capture its stylesheet and prove the editor reproduces it exactly.</p>
  {!status.commit_present && <p role="alert">{status.detail}</p>}
  <button className="live-primary" disabled={busy || !status.commit_present} onClick={run}>{busy ? 'Importing…' : 'Import live website'}</button> <span role="status">{note}</span>
 </div>
}

function PuckEditor(props) {
 const {mount, page, model, capture, client, assets} = props
 const siteFiles = useMemo(() => new Set(assets.site.map(a => a.path)), [assets.site])
 const pristineEmpty = useMemo(() => { const s = new Set(); for (const d of Object.values(model.docs)) for (const {item} of walk(d.tree)) if (Array.isArray(item.props.kids) && !item.props.kids.length) s.add(item.props.id); return s }, [model])
 const styleRef = useRef(null), reparentHook = useRef(null)
 const ctx = useMemo(() => ({editing: true, page, onReparent: (...a) => reparentHook.current?.(...a), codes: model.codes, capture, siteFiles, pristineEmpty, cssUrl: client.canvasUrl('site.css'), canvasUrl: n => client.canvasUrl(n),
  assetUrl: id => client.assetUrl(id), siteFileUrl: p => client.siteFileUrl(p), onStyle: (...a) => styleRef.current?.(...a)}), [model, page, capture, siteFiles, pristineEmpty, client])
 const overrides = useMemo(() => ({iframe: LiveFrame}), [])
 return <CanvasContext.Provider value={ctx}>
  <Puck config={liveConfig} data={mount.data} initialHistory={mount.history} viewports={VIEWPORTS} overrides={overrides}
   ui={{viewports: {current: {width: 1280, height: 'auto'}, controlsVisible: true, options: []}}}
   permissions={{drag: true, duplicate: true, delete: true, insert: true, edit: true}} iframe={{enabled: true, waitForStyles: true, syncHostStyles: false}}
   dnd={{enableDragHandle: true, behavior: 'static'}} headerTitle={page.label}>
   <Layout {...props} styleRef={styleRef} reparentHook={reparentHook}/>
  </Puck>
 </CanvasContext.Provider>
}

function Layout(p) {
 const {page, model, client, docs, onDocs, dirty, latest, saveVersion, message, setMessage, autosaved, openPage, site, styleRef} = p
 const data = usePuck(s => s.appState.data)
 const ui = usePuck(s => s.appState.ui)
 const dispatch = usePuck(s => s.dispatch)
 const history = usePuck(s => s.history)
 const selected = usePuck(s => s.selectedItem)
 const getSelectorForId = usePuck(s => s.getSelectorForId)
 const {left, right, showLeft, showRight, focus, leftWidth, rightWidth, zoom, pan, canvasOnly} = p.workspace
 const setLeft = left => p.setWorkspace(w => ({...w, left}))
 const setRight = right => p.setWorkspace(w => ({...w, right}))
 const setShowLeft = showLeft => p.setWorkspace(w => ({...w, showLeft}))
 const setShowRight = showRight => p.setWorkspace(w => ({...w, showRight}))
 const setFocus = focus => p.setWorkspace(w => ({...w, focus}))
 const patchWorkspace = patch => p.setWorkspace(w => ({...w, ...patch}))
 const openLeft = id => { p.setWorkspace(w=>({...w,canvasOnly:false})); setLeft(id); setShowLeft(true); if (window.innerWidth < 900) setShowRight(false) }
 const openRight = id => { p.setWorkspace(w=>({...w,canvasOnly:false})); setRight(id); setShowRight(true); if (window.innerWidth < 900) setShowLeft(false) }
 const lastValid = useRef(null)
 const width = ui.viewports?.current?.width || 1280
 const bp = bpForWidth(width)
 const content = useMemo(() => fromPuck(data.content || []), [data])
 const code = useMemo(() => normalizeCode(data.root?.props?.code || {}), [data])
 const own = useMemo(() => owners(page, content), [page, content])
 const siteFiles = useMemo(() => new Set((p.assets?.site || []).map(a => a.path)), [p.assets])
 const linkCtx = useMemo(() => ({routes: new Set([...model.pages.filter(x => x.route !== '/_not-found').map(x => x.route), ...pageRoutes(code.pages)]), files: siteFiles,
  site: {routes: model.pages.map(x => x.route), posts: (model.docs['content/blog.ts']?.tree || []).map(r => r.props.key)}}), [model, siteFiles, code.pages])
 const [newPage, setNewPage] = useState(null)
 const [editImage, setEditImage] = useState(null)
 const [headerPreview, setHeaderPreview] = useState('')
 // Element ids are <document key>_<n>; created documents use their page's key (pages.js docKey).
 const keyOfDoc = doc => model.docs[doc]?.key || (code.pages?.[doc] ? pageDocKey(doc) : null)
 const docKeyOf = id => keyOfDoc(own[id] || page.doc) || 'x'

 // Commit one atomic change (model content) as one undoable history step.
 const selectedId = selected?.props?.id
 useEffect(() => { if (selectedId) { setRight('inspect'); setShowRight(true); if (window.innerWidth < 900) setShowLeft(false) } }, [selectedId])
 const commit = useCallback((next, note, selectId, nextCode) => {
  dispatch({type: 'setData', data: {...data, root: {...(data.root || {}), props: {...(data.root?.props || {}), code: nextCode === undefined ? code : nextCode}}, content: toPuck(next)}, recordHistory: true})
  // Puck's selection is positional; keep it on the same element (or the one given) after the change.
  const keep = selectId === undefined ? selectedId : selectId
  setTimeout(() => dispatch({type: 'setUi', ui: {itemSelector: selectorFor(next, keep)}}), 0)
  if (note) setMessage(`${note}. Undo is available (Ctrl/Cmd+Z).`)
 }, [data, dispatch, getSelectorForId, setMessage, selectedId, code])
 // Validate before committing: a change that would break a document (code moved out of its file,
 // shapes outside a graphic) is refused and never enters the history.
 const check = next => { const out = split(page, next); for (const [doc, tree] of Object.entries(out)) validateTree(tree, {codes: Object.fromEntries(Object.entries(model.codes).map(([k, c]) => [k, c.pure ? {...c, doc} : c])), doc}); return next }
 const tryCommit = (fn, note, selectId) => { try { commit(check(fn()), note, selectId) } catch (e) { setMessage(`Not applied: ${e.message}`) } }
 // The shared operations (code.js): the inspector, Code mode, Site settings and accepted Website
 // Manager proposals all change the page through these, as one undoable step each.
 // Page operations may add documents (a created page or post) that are not on this canvas: they go to
 // the working set first; the code layer (with `pages`) is committed as part of this history step.
 const runOps = (ops, note, selectId) => {
  try {
   const r = applyOps({content}, code, ops, linkCtx)
   const extra = Object.fromEntries(Object.entries(r.lists).filter(([k]) => k !== 'content'))
   for (const tree of Object.values(extra)) validateTree(tree, {codes: model.codes})
   if (Object.keys(extra).length) docs.current = {...docs.current, ...extra}
   p.codeRef.current = normalizeCode(r.code)
   commit(check(r.lists.content), note, selectId, r.code)
   return r
  } catch (e) { setMessage(`Not applied: ${e.message}`); return false }
 }
 const createPage = (kind, settings) => {
  const r = runOps([{op: kind === 'post' ? 'create_post' : 'create_page', settings}], `Created ${kind === 'post' ? 'blog post' : 'landing page'} ${kind === 'post' ? '/blog/' : '/'}${settings.slug}`)
  if (r) { autosaveNow(); setTimeout(() => openPage(kind === 'post' ? `created-post:${settings.slug}` : `created:${settings.slug}`), 60) }
  return r
 }
 const autosaveNow = () => { p.onDocs({}, p.codeRef.current) }
 const pageDocs = () => split(page, content)
 const applyProposal = proposal => {
  const all = {...docs.current, ...split(page, content), ...proposal.docs}
  const next = compose(page, all)
  try {
   docs.current = {...docs.current, ...proposal.docs}
   p.codeRef.current = normalizeCode(proposal.code || {})
   commit(check(next), 'Manager change applied', undefined, proposal.code)
   const opens = proposal.ops.find(o => o.op === 'create_page' || o.op === 'create_post')
   if (opens) { autosaveNow(); setTimeout(() => openPage(opens.op === 'create_post' ? `created-post:${opens.settings.slug}` : `created:${opens.settings.slug}`), 60) }
  } catch (e) { setMessage(`Not applied: ${e.message}`) }
 }
 const computedOf = id => {
  try {
   const doc = document.querySelector('.live-canvas iframe')?.contentDocument
   const el = doc?.querySelector(`[data-sid="${id}"]`)
   if (!el) return null
   const cs = doc.defaultView.getComputedStyle(el)
   return Object.fromEntries(['fontSize', 'fontFamily', 'fontWeight', 'lineHeight', 'letterSpacing', 'color', 'backgroundColor', 'display', 'gridTemplateColumns', 'flexDirection',
    'gap', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'marginTop', 'marginBottom', 'borderRadius', 'textAlign', 'width', 'boxShadow'].map(k => [k, cs[k]]))
  } catch { return null }
 }
 // Static render of the page with a proposal applied (same renderer as the version preview).
 // A proposal that creates a page previews that new page (header/footer as captured on this page).
 const renderPreview = (proposal, sent) => {
  const made = proposal.ops.find(o => o.op === 'create_page' || o.op === 'create_post')
  const target = made ? studioPages(proposal.code?.pages || {}, model).find(x => x.id === (made.op === 'create_post' ? `created-post:${made.settings.slug}` : `created:${made.settings.slug}`)) : page
  return staticPage({page: target || page, model, client, capture: p.capture, assets: p.assets, docs: {...docs.current, ...(sent?.docs || {}), ...proposal.docs}, code: proposal.code})
 }
 // Header simulation is canvas-only; source and saved documents stay untouched.
 useEffect(() => {
  const doc = document.querySelector('.live-canvas iframe')?.contentDocument
  doc?.dispatchEvent(new CustomEvent('ttros-header-preview', {detail:headerPreview}))
 })
 const reparentRef = useRef(null)
 reparentRef.current = (id, parentId, index) => {
  const target = findNode(content, parentId)?.item
  if (!target) return
  const slot = target.type === 'Cond' && target.props.show === 'alt' ? 'alt' : 'kids'
  tryCommit(() => moveNode(content, id, parentId, slot, index), `Moved into ${target.props.tag || target.props.name || target.type} ${parentId}`, id)
 }
 p.reparentHook.current = (...a) => reparentRef.current?.(...a)
 styleRef.current = (id, bpId, values, verb) => tryCommit(() => setStyle(content, id, bpId, values), `${verb} at ${BREAKPOINTS.find(b => b.id === bpId).label}`)

 // After every change: fix ids Puck generated (duplicate), split into documents, validate, autosave.
 useEffect(() => {
  const fixed = clone(content)
  let renamed = false
  for (const {item} of walk(fixed)) if (!ID.test(item.props.id)) { item.props.id = `${docKeyOf(item.props.id)}_d${hash36(item.props.id)}`.slice(0, 61); renamed = true }
  if (renamed) { dispatch({type: 'setData', data: {...data, content: toPuck(fixed)}, recordHistory: false}); return }
  let next
  try {
   next = split(page, fixed)
   for (const [doc, tree] of Object.entries(next)) validateTree(tree, {codes: Object.fromEntries(Object.entries(model.codes).map(([k, c]) => [k, c.pure ? {...c, doc} : c])), doc})
  } catch (e) {
   setMessage(`Change refused: ${e.message}`)
   if (history.hasPast) history.back()
   return
  }
  lastValid.current = next
  onDocs(next, data.root?.props?.code || {})
 }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

 // Persist this page's undo history (crash/reopen keeps Undo/Redo).
 const persistHistory = useMemo(() => debounce((histories, index) => {
  const start = Math.max(0, histories.length - 60)
  return idbSet(keys.history(site, page.id), {histories: histories.slice(start).map(e => ({id: e.id, state: {data: e.state.data, ui: {...e.state.ui, itemSelector: null}}})), index: Math.max(0, index - start), at: Date.now()})
 }, 800), [site, page.id])
 useEffect(() => { if (history.histories?.length) persistHistory(history.histories, history.index) }, [history.histories, history.index, persistHistory])

 // Keyboard: Ctrl/Cmd+S save, Ctrl/Cmd+D duplicate, Ctrl+Y redo (Puck handles Ctrl/Cmd+Z and Shift+Z).
 useEffect(() => {
  const onKey = e => {
   if (e.key === 'Escape' && !document.querySelector('.live-modal,.live-preview')) {
    e.preventDefault()
    document.querySelectorAll('.live-more[open],.live-draft-details[open]').forEach(menu => { menu.open = false })
    patchWorkspace({pan:false, showLeft:false, ...(window.innerWidth<900 ? {showRight:false} : {right:'manager'})})
    return
   }
   const mod = e.ctrlKey || e.metaKey
   if (!mod) return
   const k = e.key.toLowerCase()
   if (k === 's') { e.preventDefault(); saveVersion() }
   else if (k === 'y' && !e.shiftKey) { e.preventDefault(); if (history.hasFuture) history.forward() }
   else if (k === 'd' && selected) { e.preventDefault(); duplicate() }
  }
  const docs = [window.document, document.querySelector('.live-studio iframe')?.contentDocument].filter(Boolean)
  docs.forEach(d => d.addEventListener('keydown', onKey))
  return () => docs.forEach(d => d.removeEventListener('keydown', onKey))
 })

 const sel = selected ? findNode(content, selected.props.id) : null
 const duplicate = () => {
  if (!sel) return
  if (sel.item.type === 'Use' && sel.item.props.shared) { setMessage('Shared elements (header, footer) cannot be duplicated on one page.'); return }
  const taken = new Set(Object.keys(own))
  const {copy} = duplicateSubtree(sel.item, docKeyOf(sel.item.props.id), taken)
  tryCommit(() => insertNode(content, copy, sel.parent?.props.id ?? null, sel.slot === 'content' ? 'kids' : sel.slot, sel.index + 1), `Duplicated ${labelOf(sel.item)}`, copy.props.id)
 }
 const insert = (template, label) => {
  const taken = new Set(Object.keys(own))
  // Into the selected container, else after the selected element, else at the end of <main>.
  let parent = null, slot = 'kids', index = Infinity
  if (sel && ((sel.item.type === 'El' && !VOID.has(sel.item.props.tag)) || sel.item.type === 'Cond' || (sel.item.type === 'Use' && sel.item.props.shared)) && sel.item.type !== 'Txt') { parent = sel.item.props.id; slot = sel.item.type === 'Cond' && sel.item.props.show === 'alt' ? 'alt' : 'kids' }
  else if (sel) { parent = sel.parent?.props.id ?? null; slot = sel.slot === 'content' ? 'kids' : sel.slot; index = sel.index + 1 }
  else { const main = [...walk(content)].find(e => e.item.props.tag === 'main'); parent = main?.item.props.id ?? null }
  const owner = parent ? (findNode(content, parent).item.props.shared || own[parent]) : page.doc
  const node = instantiate(template, keyOfDoc(owner) || docKeyOf(parent || ''), taken)
  tryCommit(() => insertNode(content, node, parent, slot, index), `Inserted ${label}`, node.props.id)
 }
 const viewport = w => dispatch({type: 'setUi', ui: {viewports: {...ui.viewports, current: {width: w, height: 'auto'}}}})
 const groups = [...new Set(p.allPages.map(pageGroup))]
 const tools = [['inspect', 'Inspector', MousePointer2], ['manager', 'Website Manager', Sparkles], ['page', 'Page & SEO', FileText], ['code', 'Code', Code2], ['site', 'Site', Settings2], ['versions', 'Versions', History], ['build', 'Preview & build', Play], ['coverage', 'Coverage', ShieldCheck]]
 return <div className={`live-grid${focus ? ' live-focus' : ''}`} data-left-open={showLeft} data-right-open={showRight} data-tool={right} data-canvas-only={!!canvasOnly} style={{'--live-left-size': `${leftWidth}px`, '--live-right-size': `${rightWidth}px`}}>
  <header className="live-toolbar">
   <div className="live-document">
    <button className="live-back" aria-label="Back to Dashboard" title="Back to Dashboard" onClick={() => setFocus(false)}><ArrowLeft size={17}/></button>
    <div className="live-page"><span>Website Studio <small>Draft workspace</small></span><select aria-label="Studio page" title={`${page.label} · ${page.route}`} value={page.id} onChange={e => openPage(e.target.value)}>{groups.map(g => <optgroup key={g} label={g}>{p.allPages.filter(x => pageGroup(x) === g).map(x => <option key={x.id} value={x.id}>{x.label} · {x.route}</option>)}</optgroup>)}</select><button className="live-mobile-page" aria-label={`Choose page: ${page.label}`} onClick={()=>openLeft('pages')}>{page.label}</button></div>
   </div>
   <nav className="live-modes" aria-label="Studio working modes">{[['inspect','Design',MousePointer2],['manager','AI',Sparkles],['code','Code',Code2],['build','Preview',Play]].map(([id,label,Icon]) => <button key={id} data-tab={id} aria-pressed={right===id || (id==='inspect' && ['page','site','versions','coverage'].includes(right))} onClick={() => openRight(id)}><Icon size={15}/><span>{label}</span></button>)}</nav>
   <div className="live-actions live-history-actions">
    <button aria-label="Undo" title="Undo (Ctrl/Cmd+Z)" disabled={!history.hasPast} onClick={() => history.back()}><Undo2 size={16}/></button>
    <button aria-label="Redo" title="Redo (Ctrl/Cmd+Shift+Z or Ctrl+Y)" disabled={!history.hasFuture} onClick={() => history.forward()}><Redo2 size={16}/></button>
   </div>
   <div className="live-actions live-save-actions">
    <span className="live-save-state" data-dirty={!!dirty.length}>{dirty.length ? <Circle size={8} fill="currentColor"/> : <Check size={14}/>} {dirty.length ? 'Unsaved changes' : `Saved · v${latest}`}</span>
    <details className="live-more"><summary aria-label="More Studio tools" title="More Studio tools"><MoreHorizontal size={18}/></summary><div>
     <div className="live-mobile-tools">
      <div className="live-row"><button disabled={!history.hasPast} onClick={()=>history.back()}><Undo2 size={15}/>Undo</button><button disabled={!history.hasFuture} onClick={()=>history.forward()}><Redo2 size={15}/>Redo</button></div>
      <label className="live-field"><span>Canvas viewport</span><select aria-label="Canvas viewport" value={width} onChange={e=>viewport(Number(e.target.value))}>{BREAKPOINTS.map(b=><option key={b.id} value={b.width}>{b.label} · {b.width}px</option>)}</select></label>
      <label className="live-field"><span>Canvas zoom</span><select aria-label="Mobile canvas zoom" value={zoom} onChange={e=>patchWorkspace({zoom:e.target.value==='fit'?'fit':Number(e.target.value)})}><option value="fit">Fit</option>{[...new Set([.25,.5,.75,1,1.25,1.5,2,...(typeof zoom==='number'?[zoom]:[])])].sort((a,b)=>a-b).map(z=><option key={z} value={z}>{Math.round(z*100)}%</option>)}</select></label>
      <button aria-pressed={pan} onClick={()=>patchWorkspace({pan:!pan})}><Hand size={15}/>Pan canvas</button>
      <button onClick={e=>{e.currentTarget.closest('details').open=false;patchWorkspace({showLeft:false,showRight:false,zoom:'fit',canvasOnly:true})}}><Maximize2 size={15}/>Canvas only</button>
      <button onClick={()=>setFocus(!focus)}>{focus?'Exit focus':'Focus'}</button><hr/>
     </div>
     {tools.filter(t=>!['inspect','manager','code','build'].includes(t[0])).map(([id,label,Icon])=><button key={id} data-tab={id} onClick={e=>{e.currentTarget.closest('details').open=false;openRight(id)}}><Icon size={15}/>{label}</button>)}<hr/><button onClick={p.onOpenSources}>Sources & Branding</button><button onClick={()=>p.onOpenReleases?.()}>Releases</button><button onClick={p.onOpenCandidate}>Preserved candidate</button>{p.onOpenCapture&&<button onClick={p.onOpenCapture}>Published-page capture</button>}</div></details>
    <button className="live-primary" aria-label="Save version" title="Save a timestamped version (Ctrl/Cmd+S)" onClick={() => saveVersion()} disabled={!dirty.length}>Save<span className="live-save-detail"> version{dirty.length ? ` (${dirty.length})` : ''}</span></button>
   </div>
  </header>
  <div className="live-workspace-bar">
   <div className="live-actions"><button aria-label="Toggle navigation" aria-expanded={showLeft} aria-controls="live-navigation" onClick={() => { if (!showLeft && window.innerWidth < 900) setShowRight(false); setShowLeft(!showLeft) }}><PanelLeft size={15}/><span>Tools</span></button><span className="live-route" title={page.route}>{page.route}</span></div>
   <div className="live-devices" role="group" aria-label="Canvas viewport">{BREAKPOINTS.map((b, i) => { const Icon = [Monitor, Tablet, Smartphone][i]; return <button key={b.id} aria-pressed={bp === b.id} aria-label={`${b.label} view`} title={`${b.label} · ${b.width}px`} onClick={() => viewport(b.width)}><Icon size={15}/><span>{b.label}</span></button> })}</div>
   <label className="live-header-preview"><span>Header</span><select aria-label="Canvas header state" value={headerPreview} onChange={e=>setHeaderPreview(e.target.value)}><option value="">Auto</option><option value="top">Top</option><option value="scrolled">Scrolled</option><option value="hidden" disabled={!code.header || !Object.values(code.header).includes('auto-hide')}>Hidden</option><option value="menu">Menu open</option></select></label>
   <div className="live-zoom" role="group" aria-label="Canvas zoom"><button aria-label="Zoom out" onClick={()=>patchWorkspace({zoom: Math.max(.1,(typeof zoom==='number'?zoom:Math.min(1,(document.querySelector('.live-canvas').clientWidth-32)/width))-.1)})}><Minus size={14}/></button><select aria-label="Canvas zoom" value={zoom} onChange={e=>patchWorkspace({zoom:e.target.value==='fit'?'fit':Number(e.target.value)})}><option value="fit">Fit</option>{[...new Set([.25,.5,.75,1,1.25,1.5,2,...(typeof zoom==='number'?[zoom]:[])])].sort((a,b)=>a-b).map(z=><option key={z} value={z}>{Math.round(z*100)}%</option>)}</select><button aria-label="Zoom in" onClick={()=>patchWorkspace({zoom: Math.min(2,(typeof zoom==='number'?zoom:Math.min(1,(document.querySelector('.live-canvas').clientWidth-32)/width))+.1)})}><Plus size={14}/></button><button aria-label="Pan canvas" aria-pressed={pan} onClick={()=>patchWorkspace({pan:!pan})}><Hand size={15}/></button></div>
   <div className="live-actions"><button aria-label="Canvas only" aria-pressed={!!canvasOnly} title="Hide Studio panels" onClick={()=>patchWorkspace({showLeft:false,showRight:false,zoom:'fit',canvasOnly:true})}><Maximize2 size={15}/></button><button aria-pressed={focus} onClick={() => setFocus(!focus)} title="Use the whole window for editing">{focus ? <Minimize2 size={15}/> : <Maximize2 size={15}/>}<span>{focus ? 'Exit focus' : 'Focus'}</span></button><button aria-label="Toggle editing panel" aria-expanded={showRight} aria-controls="live-editing" onClick={() => { if (!showRight && window.innerWidth < 900) setShowLeft(false); setShowRight(!showRight) }}><PanelRight size={15}/></button></div>
  </div>
  <nav className="live-content-rail" aria-label="Studio content">{[['pages','Pages',FileText],['insert','Insert',Plus],['layers','Layers',Layers],['assets','Assets',Images]].map(([id,label,Icon])=><button key={id} aria-label={`Open ${label}`} title={label} aria-pressed={showLeft&&left===id} onClick={()=>showLeft&&left===id?setShowLeft(false):openLeft(id)}><Icon size={19}/><span>{label}</span></button>)}</nav>
  <aside className="live-left" id="live-navigation" aria-label="Page navigation and assets" hidden={!showLeft}>
   <nav className="live-tabs live-left-tabs" aria-label="Content tools">{[['pages', 'Pages', FileText], ['insert', 'Insert', Plus], ['layers', 'Layers', Layers], ['assets', 'Assets', Images]].map(([id, l, Icon]) => <button key={id} aria-pressed={left === id} onClick={() => setLeft(id)}><Icon size={16}/><span>{l}</span></button>)}</nav>
   <div className="live-panel-heading"><h2>{left === 'pages' ? 'Your website' : left === 'insert' ? 'Add to page' : left === 'layers' ? 'Page layers' : 'Asset library'}</h2><button className="live-close-panel" aria-label="Close navigation" onClick={() => setShowLeft(false)}><X size={14}/></button></div>
   <div className="live-create-actions"><button data-testid="live-new-page" onClick={() => setNewPage('landing')}><Plus size={14}/>New page</button><button data-testid="live-new-post" onClick={() => setNewPage('post')}><Plus size={14}/>New post</button></div>
   <div className="live-panel-body">
   {left === 'pages' && <PageNavigator pages={p.allPages} page={page} dirty={dirty} openPage={openPage}/>}
   {left === 'insert' && <InsertPanel insert={insert} sel={sel} client={client} code={code} setMessage={setMessage} content={content}/>}
   {left === 'layers' && <div className="live-outline"><p className="live-help">Drag to reorder or move into another container. Click to select.</p><Puck.Outline/></div>}
   {left === 'assets' && <AssetsPanel {...p} sel={sel} content={content} tryCommit={tryCommit} insert={insert} onEdit={setEditImage}/>}
   </div>
   <PanelResize side="left" value={leftWidth} onChange={leftWidth=>patchWorkspace({leftWidth})}/>
  </aside>
  <ScaledCanvas width={width} bp={bp} zoom={zoom} pan={pan}/>
  <aside className="live-right" id="live-editing" aria-label="Editing panel" hidden={!showRight}>
   <PanelResize side="right" value={rightWidth} onChange={rightWidth=>patchWorkspace({rightWidth})}/>
   <div className="live-panel-content">
   <div className="live-panel-heading"><h2>{right==='manager'&&<Sparkles size={16}/>} {tools.find(t => t[0] === right)[1]}</h2><button className="live-close-panel" aria-label="Close editing panel" onClick={() => setShowRight(false)}><X size={14}/></button></div>
   <div className="live-properties" hidden={right==='manager'}><div className="live-panel-body">
   {right === 'inspect' && !sel && <div className="live-empty"><MousePointer2 size={28}/><h3>Select and shape</h3><p>Click any element to edit it here, or add something new.</p><button onClick={() => openLeft('insert')}><Plus size={14}/>Add an element</button></div>}
   {right === 'inspect' && sel && <Inspector {...p} sel={sel} content={content} own={own} bp={bp} tryCommit={tryCommit} runOps={runOps} code={code} siteFiles={siteFiles} duplicate={duplicate} getSelectorForId={getSelectorForId} dispatch={dispatch}/>}
   {right === 'page' && <PagePanel page={page} model={model} code={code} runOps={runOps} assets={p.assets} client={client} openPage={openPage} setMessage={setMessage}/>}
   <div hidden={right !== 'code'}><CodePanel client={client} code={code} runOps={runOps} model={model} setMessage={setMessage}
    workingDocs={() => { const all = {...docs.current, ...split(page, content)}; return Object.fromEntries(Object.entries(all).filter(([id, t]) => canonical(t) !== canonical(model.docs[id]?.tree))) }}/></div>
   {right === 'site' && <SitePanel code={code} runOps={runOps} preview={headerPreview} setPreview={setHeaderPreview}/>}
   {right === 'versions' && <VersionsPanel {...p} dispatch={dispatch} data={data}/>}
   <div hidden={right !== 'build'}><BuildPanel {...p}/></div>
   {right === 'coverage' && <CoveragePanel {...p}/>}
   </div></div>
   <section className="live-manager-dock" aria-label="Website Manager" data-compact={right!=='manager'}><header><Sparkles size={16}/><strong>Website Manager</strong><button aria-label="Expand Website Manager" onClick={()=>openRight('manager')}>AI ↗</button></header><ManagerPanel sessions={p.managerSessions} client={client} page={page} sel={sel} bp={bp} latest={latest} pageDocs={pageDocs} code={code} computedOf={computedOf} applyProposal={applyProposal}
    setMessage={setMessage} renderPreview={renderPreview} onAccepted={id => p.acceptedRef.current.add(id)}/></section>
   </div>
  </aside>
  <footer className="live-workspace-status">
   <details className="live-draft-details"><summary aria-label="Draft status details">{p.recovered&&dirty.length ? 'Draft restored · ' : ''}{dirty.length ? (autosaved ? 'Autosaved' : 'Unsaved changes') : `Saved · v${latest}`}</summary><div><p>Source {model.commit.slice(0,8)} · Saved version v{latest}</p><p>{dirty.length ? `${dirty.length} changed document${dirty.length===1?'':'s'}` : 'All changes saved'}</p>{autosaved&&<p>Autosaved at {new Date(autosaved.at).toLocaleTimeString('en-CA')} · {autosaved.where}</p>}{p.recovered&&<><p data-testid="live-recovered">Recovered unsaved changes from {fmtTime(p.recovered.at)} ({p.recovered.from}).</p>{p.recovered.base!==p.recovered.latest&&<p>Restored from v{p.recovered.base}; v{p.recovered.latest} is now latest.</p>}<button onClick={p.discardRecovered}>Discard recovered changes</button></>}</div></details>
   <p className="live-status" role="status" data-testid="live-status">{message}</p>
   {!showRight&&<button className="live-manager-launch" onClick={()=>openRight('manager')}><Sparkles size={15}/><span>Ask Manager</span></button>}
   <span className="live-shortcut">⌘ / Ctrl + S to save</span>
  </footer>
  {newPage && <NewPageDialog kind={newPage} model={model} code={code} assets={p.assets} client={client} onCancel={() => setNewPage(null)} onCreate={(kind, settings) => { if (createPage(kind, settings)) setNewPage(null) }}/>}
  {editImage && <ImageEditor asset={editImage} client={client} onClose={() => setEditImage(null)} onSaved={async a => { setEditImage(null); p.setAssets(await client.assets()); setMessage(`Saved edited image ${a.name} (new asset ${a.id.slice(0, 12)}…, made from ${editImage.id.slice(0, 12)}…).`) }}/>}
 </div>
}

function PageNavigator({pages, page, dirty, openPage}) {
 const [query, setQuery] = useState('')
 const filtered = pages.filter(p => `${p.label} ${p.route}`.toLowerCase().includes(query.trim().toLowerCase()))
 const groups = [...new Set(filtered.map(pageGroup))]
 return <div className="live-pages">
  <label className="live-search"><Search size={15}/><input type="search" aria-label="Search pages" placeholder="Search pages or routes…" value={query} onChange={e => setQuery(e.target.value)}/></label>
  <p className="live-help">{filtered.length} of {pages.length} pages</p>
  {!filtered.length && <p className="live-empty-search">No pages found. Try a page name or route.</p>}
  {groups.map(g => <section key={g}><h3>{g}</h3><ul>{filtered.filter(p => pageGroup(p) === g).map(p => <li key={p.id}><button aria-current={page.id === p.id ? 'page' : undefined} onClick={() => openPage(p.id)}><FileText size={15}/><span><strong>{p.label}</strong><small>{p.route}</small></span>{dirty.includes(p.doc) && <Circle className="live-dirty-dot" size={7} fill="currentColor"><title>Unsaved document</title></Circle>}</button></li>)}</ul></section>)}
 </div>
}

// The chosen viewport width (1280/820/390 px), scaled down to fit the column like Puck's own canvas.
function ScaledCanvas({width, bp, zoom: requested = 'fit', pan}) {
 const ref = useRef(null), drag = useRef(null)
 const [box, setBox] = useState({w: 900, h: 800})
 useEffect(() => {
  const el = ref.current
  if (!el) return
  const ro = new ResizeObserver(() => setBox({w: el.clientWidth, h: el.clientHeight}))
  ro.observe(el)
  return () => ro.disconnect()
 }, [])
 const zoom = requested === 'fit' ? Math.min(1, Math.max(.05, (box.w - 32) / width)) : requested
 const start = e => {
  const frame = ref.current.querySelector('iframe')?.contentWindow
  drag.current = {x:e.clientX, y:e.clientY, scroll:ref.current.scrollLeft, top:frame?.scrollY || 0}
  e.currentTarget.setPointerCapture(e.pointerId)
 }
 const move = e => {
  if (!drag.current) return
  ref.current.scrollLeft = drag.current.scroll - (e.clientX - drag.current.x)
  ref.current.querySelector('iframe')?.contentWindow?.scrollTo(0, drag.current.top - (e.clientY - drag.current.y) / zoom)
 }
 return <main className="live-canvas" aria-label="Website canvas" data-breakpoint={bp} data-zoom={zoom.toFixed(3)} data-pan={!!pan} ref={ref}>
  <div className="live-canvas-space" style={{width: Math.max(box.w, width * zoom + 32), minHeight: box.h}}>
   <div className="live-canvas-page" style={{width: width * zoom, height: Math.max(200,box.h - 32), margin:'16px auto', position:'relative'}}>
    <div style={{width, height: Math.max(200,box.h - 32) / zoom, transform:`scale(${zoom})`, transformOrigin:'0 0', background:'#fff', boxShadow:'0 2px 12px #24363020'}}><Puck.Preview/></div>
    {pan && <div className="live-pan-surface" aria-label="Drag to pan the website" onPointerDown={start} onPointerMove={move} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}}/>}
   </div>
  </div>
 </main>
}

function PanelResize({side, value, onChange}) {
 const start = useRef(null)
 const min = side === 'left' ? 240 : 320, max = side === 'left' ? 360 : 480
 const set = n => onChange(Math.max(min, Math.min(max, n)))
 return <div className={`live-panel-resize ${side}`} role="separator" aria-label={`Resize ${side} Studio panel`} aria-orientation="vertical" aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0}
  onPointerDown={e=>{start.current={x:e.clientX,value};e.currentTarget.setPointerCapture(e.pointerId)}}
  onPointerMove={e=>{if(start.current)set(start.current.value+(e.clientX-start.current.x)*(side==='left'?1:-1))}}
  onPointerUp={()=>{start.current=null}} onPointerCancel={()=>{start.current=null}}
  onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();set(value+(e.key==='ArrowRight'?20:-20)*(side==='left'?1:-1))}else if(e.key==='Home'){e.preventDefault();set(min)}else if(e.key==='End'){e.preventDefault();set(max)}}}/>
}

function InsertPanel({insert, sel, client, code, setMessage}) {
 const groups = [...new Set(PALETTE.map(x => x.group))]
 const [query, setQuery] = useState('')
 const matches = x => `${x.label} ${x.group}`.toLowerCase().includes(query.trim().toLowerCase())
 const [sections, setSections] = useState([])
 const [components, setComponents] = useState([])
 const [name, setName] = useState('')
 useEffect(() => { client.sections().then(r => setSections(r.sections), () => {}) }, [client])
 useEffect(() => { client.sourceFiles().then(r => setComponents(r.files.filter(f => f.placeable).map(f => f.path)), () => {}) }, [client])
 // Only components whose default export takes no props can be placed (others fail the site's type check).
 const propless = /export\s+default\s+function\s+[A-Z][A-Za-z0-9]*\s*\(\s*\)/
 const files = [...new Set([...components, ...Object.entries(code.files || {}).filter(([f, t]) => /^components\/.+\.(tsx|jsx)$/.test(f) && propless.test(t || '')).map(([f]) => f)])].sort()
 const saveSection = async () => {
  if (!sel || !['El', 'Comp'].includes(sel.item.type)) { setMessage('Select an element (with its children) to save it as a reusable section.'); return }
  try { const row = await client.saveSection(name || labelOf(sel.item), sel.item); setSections(s => [...s, row]); setName(''); setMessage(`Saved reusable section “${row.name}”.`) } catch (e) { setMessage(`Not saved: ${e.message}`) }
 }
 return <div className="live-palette">
  <label className="live-search"><Search size={15}/><input type="search" aria-label="Search elements" placeholder="Search elements…" value={query} onChange={e => setQuery(e.target.value)}/></label>
  <p className="live-help">{sel ? <>Inserts {sel.item.type === 'El' && !VOID.has(sel.item.props.tag) ? 'inside' : 'after'} <strong>{labelOf(sel.item)}</strong>.</> : 'Inserts at the end of the page content. Select a container first to insert into it.'} Drag on the canvas or in Layers to move it.</p>
  {!PALETTE.some(matches) && <p className="live-empty-search">No elements found. Try “heading”, “image” or “container”.</p>}
  {groups.filter(g => PALETTE.some(x => x.group === g && matches(x))).map(g => <section key={g}><h4>{g}</h4><div className="live-palette-grid">{PALETTE.filter(x => x.group === g && matches(x)).map(x => <button key={x.id} data-palette={x.id} onClick={() => insert(x.make(), x.label)}>{x.label}</button>)}</div></section>)}
  <section data-testid="live-sections"><h4>Reusable sections ({sections.length})</h4>
   <div className="live-palette-grid">{sections.map(x => <button key={x.id} data-section={x.id} title={`Saved ${x.saved_at}`} onClick={() => insert(x.node, x.name)}>{x.name}</button>)}</div>
   <div className="live-row"><input aria-label="Section name" placeholder="Name" value={name} onChange={e => setName(e.target.value)}/><button onClick={saveSection} disabled={!sel}>Save selection</button></div>
  </section>
  <section data-testid="live-components"><h4>React components</h4>
   <p className="live-help">Components of the site or written in Code mode. They render in the real preview build; the canvas shows a placeholder. Listed: components whose default export takes no props.</p>
   <div className="live-palette-grid">{files.map(f => { const n = f.split('/').at(-1).replace(/\.(tsx|jsx)$/, ''); return /^[A-Z]/.test(n) ? <button key={f} data-component={f} onClick={() => insert({type: 'Comp', props: {name: n, file: f}}, `<${n} />`)}>{n}</button> : null })}</div>
  </section>
 </div>
}

function Field({label, value, onCommit, placeholder, type = 'text', list, testid, multiline}) {
 const [v, setV] = useState(value ?? '')
 useEffect(() => setV(value ?? ''), [value])
 const done = () => { if ((value ?? '') !== v) onCommit(v) }
 const common = {value: v, placeholder, onChange: e => setV(e.target.value), onBlur: done, 'aria-label': label, 'data-testid': testid, list}
 return <label className="live-field"><span>{label}</span>{multiline ? <textarea rows={3} {...common}/> : <input type={type} {...common} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}/>}</label>
}

const SUGGEST = {display: ['block', 'flex', 'grid', 'inline-block', 'inline-flex', 'inline', 'none', 'contents'], position: ['static', 'relative', 'absolute', 'sticky', 'fixed'],
 flexDirection: ['row', 'column', 'row-reverse', 'column-reverse'], justifyContent: ['flex-start', 'center', 'flex-end', 'space-between', 'space-around', 'space-evenly'],
 alignItems: ['stretch', 'flex-start', 'center', 'flex-end', 'baseline'], textAlign: ['left', 'center', 'right', 'justify'], fontWeight: ['300', '400', '500', '600', '700'],
 flexWrap: ['nowrap', 'wrap'], objectFit: ['cover', 'contain', 'fill', 'none'], overflow: ['visible', 'hidden', 'auto', 'clip'], textTransform: ['none', 'uppercase', 'lowercase', 'capitalize']}

function Inspector({sel, content, own, bp, tryCommit, runOps, code, siteFiles, duplicate, getSelectorForId, dispatch, model, page, assets, client, setMessage}) {
 const [group, setGroup] = useState('layout')
 const [stylesBp, setStylesBp] = useState(null)
 const at = stylesBp || bp
 if (!sel) return <div className="live-inspector"><p className="live-help">Select any element on the canvas (or in Layers). Every element can be edited, moved, resized, restyled, duplicated or deleted. Use the breakpoint buttons to style desktop, tablet and mobile separately.</p><p className="live-help">Shortcuts: Ctrl/Cmd+Z undo · Ctrl/Cmd+Shift+Z or Ctrl+Y redo · Ctrl/Cmd+D duplicate · Delete removes · Ctrl/Cmd+S saves a version.</p>
  <RecordFields content={content} page={page} model={model} tryCommit={tryCommit}/></div>
 const {item, parent} = sel
 const p = item.props
 const id = p.id
 const doc = own[id] || page.doc
 const set = (patch, note) => tryCommit(() => updateProps(content, id, patch), note)
 const selectId = target => dispatch({type: 'setUi', ui: {itemSelector: selectorFor(content, target)}})
 const isEl = item.type === 'El'
 const kept = Object.entries(p.x || {}).filter(([k]) => k !== 'className')
 const st = p.st?.[at] || {}
 const move = dir => {
  const list = parent ? parent.props[sel.slot] : content
  const index = sel.index + dir
  if (index < 0 || index >= list.length) return
  tryCommit(() => moveNode(content, id, parent?.props.id ?? null, sel.slot === 'content' ? 'kids' : sel.slot, dir > 0 ? index + 1 : index), dir > 0 ? 'Moved down' : 'Moved up')
 }
 const outdent = () => {
  if (!parent) return
  const grand = findNode(content, parent.props.id)
  tryCommit(() => moveNode(content, id, grand.parent?.props.id ?? null, grand.slot === 'content' ? 'kids' : grand.slot, grand.index + 1), 'Moved out of its container')
 }
 const indent = () => {
  const list = parent ? parent.props[sel.slot] : content
  const prev = list[sel.index - 1]
  if (!prev || !(prev.type === 'El' && !VOID.has(prev.props.tag))) { setMessage('The element before this one is not a container.'); return }
  tryCommit(() => moveNode(content, id, prev.props.id, 'kids'), 'Moved into the previous container')
 }
 const styleSet = (k, v) => { const prob = v ? styleProblem(k, v) : null; if (prob) { setMessage(prob); return } tryCommit(() => setStyleSafe(clone(content), k, v), `Style ${k} ${v ? `= ${v}` : 'cleared'} at ${BREAKPOINTS.find(b => b.id === at).label}`) }
 const setStyleSafe = (c, k, v) => { const hit = findNode(c, id); const s = {...(hit.item.props.st || {})}; const layer = {...(s[at] || {})}; if (v) layer[k] = v; else delete layer[k]; if (Object.keys(layer).length) s[at] = layer; else delete s[at]; hit.item.props.st = Object.keys(s).length ? s : undefined; if (!hit.item.props.st) delete hit.item.props.st; return c }
 const columns = n => tryCommit(() => setStyleSafe(setStyleSafe(JSON.parse(JSON.stringify(content)), 'display', 'grid'), 'gridTemplateColumns', `repeat(${n}, minmax(0, 1fr))`), `${n} column${n === 1 ? '' : 's'} at ${BREAKPOINTS.find(b => b.id === at).label}`)
 const flex = dir => tryCommit(() => setStyleSafe(setStyleSafe(JSON.parse(JSON.stringify(content)), 'display', 'flex'), 'flexDirection', dir), `Flex ${dir}`)
 const position = mode => tryCommit(() => { let c = JSON.parse(JSON.stringify(content)); c = setStyleSafe(c, 'position', mode === 'flow' ? '' : mode); if (mode === 'flow') { c = setStyleSafe(c, 'left', ''); c = setStyleSafe(c, 'top', '') } else if (mode === 'absolute') { c = setStyleSafe(c, 'left', st.left || '0px'); c = setStyleSafe(c, 'top', st.top || '0px') } return c }, mode === 'flow' ? 'Back in normal flow' : mode === 'absolute' ? 'Free position (absolute inside its container)' : 'Offset position')
 const pickAsset = ref => {
  if (isEl && ['img', 'Image', 'video', 'source'].includes(p.tag)) set({a: {...(p.a || {}), src: ref}, x: p.x?.src ? Object.fromEntries(Object.entries(p.x).filter(([k]) => k !== 'src')) : p.x}, 'Image replaced')
  else tryCommit(() => setStyleSafe(JSON.parse(JSON.stringify(content)), 'backgroundImage', ref), 'Background image set')
 }
 return <div className="live-inspector" data-testid="live-inspector" data-selected={id} data-selected-type={item.type} data-selected-tag={p.tag || ''}>
  <h3>{labelOf(item)} <small>{item.type === 'El' ? `<${p.tag === 'Link' ? 'a' : p.tag === 'Image' ? 'img' : p.tag}>` : item.type}</small></h3>
  <details className="live-element-meta"><summary>Element & source details</summary><p className="live-help">ID <code>{id}</code> · in <code>{doc}</code>{p.shared ? <> · shared document <code>{p.shared}</code> (edits apply on every page)</> : ''}</p></details>
  <div className="live-row">
   {parent && <button onClick={() => selectId(parent.props.id)}>Select parent</button>}
   <button onClick={() => move(-1)} aria-label="Move up">↑</button><button onClick={() => move(1)} aria-label="Move down">↓</button>
   <button onClick={indent} title="Move into the previous container">→ into previous</button><button onClick={outdent} disabled={!parent} title="Move out of this container">← out</button>
  </div>
  <div className="live-row">
   <button onClick={duplicate}>Duplicate</button>
   <button onClick={() => tryCommit(() => removeNode(content, id), `Deleted ${labelOf(item)}`, null)} disabled={!!p.shared && false}>Delete</button>
   <button onClick={() => { const taken = new Set(Object.keys(own)); const w = instantiate({type: 'El', props: {tag: 'div', kids: []}}, model.docs[doc]?.key || (code.pages?.[doc] ? pageDocKey(doc) : 'x'), taken); tryCommit(() => wrapNode(content, id, w), 'Wrapped in a container', w.props.id) }}>Wrap in container</button>
   {isEl && !VOID.has(p.tag) && <button onClick={() => tryCommit(() => unwrapNode(content, id), 'Container removed; children kept')}>Unwrap</button>}
  </div>
  {code.files?.[doc] !== undefined && <p className="live-warn" role="note" data-testid="live-code-only">{doc} has a source edit in Code mode: it is code-only, and element edits to it are refused on save until that source edit is removed.</p>}
  {item.type === 'Txt' && <Field label="Text" multiline value={p.text} testid="live-text" onCommit={v => v.trim() ? runOps([{op: 'set_text', id, text: v}], 'Text changed') : setMessage('Not applied: Text must not be empty.')}/>}
  {item.type === 'Txt' && p.x?.text && <p className="live-help">This text comes from the site configuration ({model.codes[p.x.text]?.preview}). Editing it here sets this element's own text.</p>}
  {isEl && <>
   {AUTHORED_TAGS.has(p.tag) && <label className="live-field"><span>Element type</span><select aria-label="Element type" value={p.tag} onChange={e => set({tag: e.target.value}, `Changed to <${e.target.value}>`)}>{[...AUTHORED_TAGS].filter(t => VOID.has(t) === VOID.has(p.tag)).map(t => <option key={t}>{t}</option>)}</select></label>}
   {['img', 'Image', 'video', 'source'].includes(p.tag) && <><Field label="Image / media source" value={p.a?.src ?? (p.x?.src ? model.codes[p.x.src]?.value : '')} testid="live-src" onCommit={v => set({a: {...(p.a || {}), src: v}, x: p.x?.src ? Object.fromEntries(Object.entries(p.x).filter(([k]) => k !== 'src')) : p.x}, 'Source changed')}/>
    <Field label="Alternative text" value={p.a?.alt} onCommit={v => set({a: {...(p.a || {}), alt: v}}, 'Alt text changed')}/></>}
   {p.tag === 'iframe' && !p.x?.src && <Field label="Embed address (YouTube, Vimeo, Tally, Loom)" value={p.a?.src} onCommit={v => set({a: {...(p.a || {}), src: v}}, 'Embed changed')}/>}
   <details className="live-advanced-element"><summary>Advanced attributes</summary><Field label="Classes (Tailwind)" value={p.x?.className ? '' : p.cls} placeholder={p.x?.className ? 'Computed by code (state-dependent)' : ''} onCommit={v => set({cls: v || undefined}, 'Classes changed')}/>
   <Field label="Anchor id" value={p.a?.id} onCommit={v => set({a: {...(p.a || {}), id: v || undefined}}, 'Anchor changed')}/></details>
  </>}
  {item.type === 'Cond' && <><label className="live-field"><span>Show in editor</span><select aria-label="Conditional branch" value={p.show || 'kids'} onChange={e => set({show: e.target.value}, 'Branch shown')}><option value="kids">When true ({p.kids.length})</option><option value="alt">Otherwise ({p.alt.length})</option><option value="none">Neither (as on load)</option></select></label><p className="live-help">Condition <code>{model.codes[p.c]?.preview}</code> is decided by the site at run time (for example an open menu). Choose which branch to edit; the site keeps the condition.</p></>}
  {item.type === 'Use' && <ComponentData item={item} model={model} set={set}/>}
  {item.type === 'Map' && <p className="live-help" data-testid="live-template-help">Repeated item: this template renders every entry of <code>{model.codes[p.c]?.preview.replace(/^\{/, '').split('.map(')[0]}</code>{p.kids.length ? '' : ' (empty template)'}. Move, restyle, add or remove elements inside it and every entry changes; the entries' data and behaviour stay functional. Elements bound to an entry can only move inside this template.</p>}
  {item.type === 'Code' && model.codes[p.c]?.bind && <RecordFields content={content} page={page} model={model} tryCommit={tryCommit} only={model.codes[p.c].bind}/>}
  {(item.type === 'Rec' || item.type === 'Data') && <RecordFields content={content} page={page} model={model} tryCommit={tryCommit}/>}
  {!!kept.length && <details className="live-code"><summary>Behaviour kept from the source ({kept.length})</summary><ul>{kept.map(([k, r]) => <li key={k}><code>{k}</code> = <code>{model.codes[r]?.preview}</code></li>)}</ul><p className="live-help">Event handlers, state and data bindings stay functional. This element can move anywhere inside <code>{model.codes[kept[0][1]]?.doc}</code>.</p></details>}
  <DesignControls item={item} at={at} setAt={setStylesBp} bp={bp} runOps={runOps} model={model} siteFiles={siteFiles} linkItem={linkOf(content, id)}/>
  {(item.type !== 'Txt') && <details className="live-all"><summary>All style properties & position</summary>
   <h4>Layout & style <span className="live-bps">{BREAKPOINTS.map(b => <button key={b.id} aria-pressed={at === b.id} onClick={() => setStylesBp(b.id === bp ? null : b.id)}>{b.label}{p.st?.[b.id] ? ' •' : ''}</button>)}</span></h4>
   <p className="live-help">Editing <strong>{BREAKPOINTS.find(b => b.id === at).label}</strong>{at !== 'd' ? ' (overrides desktop at this width and below)' : ''}.</p>
   <div className="live-row"><span>Position</span><button aria-pressed={!st.position || st.position === 'static'} onClick={() => position('flow')}>Flow</button><button aria-pressed={st.position === 'relative'} onClick={() => position('relative')}>Offset</button><button aria-pressed={st.position === 'absolute'} onClick={() => position('absolute')}>Free</button></div>
   <div className="live-row"><span>Columns</span>{[1, 2, 3, 4, 5, 6].map(n => <button key={n} aria-label={`${n} columns`} onClick={() => columns(n)}>{n}</button>)}<button onClick={() => flex('row')}>Row</button><button onClick={() => flex('column')}>Stack</button></div>
   <nav className="live-tabs small">{Object.keys(STYLE_PROPS).map(g => <button key={g} aria-pressed={group === g} onClick={() => setGroup(g)}>{g}</button>)}</nav>
   <div className="live-styles">{STYLE_PROPS[group].map(k => <StyleField key={`${id}:${at}:${k}`} name={k} value={st[k]} onCommit={v => styleSet(k, v)} assets={assets} pick={pickAsset}/>)}</div>
   {p.st && <button onClick={() => tryCommit(() => updateProps(content, id, {st: undefined}), 'Studio styles cleared')}>Clear Studio styles</button>}
  </details>}
 </div>
}

// The data record(s) this route shows (content/blog.ts, content/resources.ts): string fields, edited
// in place in the source module, so the post's page, cards, sitemap and SEO all change together.
function RecordFields({content, page, model, tryCommit, only}) {
 const holders = content.filter(i => i.type === 'Data')
 if (!holders.length) return null
 return <div className="live-records" data-testid="live-record-fields">{holders.map(h => {
  const file = h.props.shared
  const rec = (h.props.kids || []).find(r => r.props.key === page.data?.[file])
  const fields = (model.docs[file]?.data?.fields || []).filter(f => !only || (only.file === file && only.field === f))
  if (!rec || !fields.length) return null
  return <fieldset key={file}><legend>Content record <code>{rec.props.key}</code> · {file}</legend>
   <p className="live-help">Changes this {file.includes('blog') ? 'blog post' : 'resource'} everywhere it appears (its page, cards, sitemap, search metadata).</p>
   {fields.map(f => <Field key={`${rec.props.id}:${f}`} label={f} multiline={(rec.props.f?.[f] || '').length > 60} value={rec.props.f?.[f] ?? ''} testid={`live-record-${f}`}
    onCommit={v => tryCommit(() => updateProps(content, rec.props.id, {f: {...rec.props.f, [f]: v}}), `${f} changed in ${file}`)}/>)}
  </fieldset>
 })}</div>
}

function StyleField({name, value, onCommit, assets, pick}) {
 const color = /color$/i.test(name)
 if (name === 'backgroundImage') return <label className="live-field"><span>{name}</span><select aria-label={name} value={value || ''} onChange={e => e.target.value ? pick(e.target.value) : onCommit('')}><option value="">none</option>{assets.library.filter(a => a.type.startsWith('image/')).map(a => <option key={a.id} value={a.ref}>{a.name}</option>)}</select></label>
 return <div className="live-style">
  <Field label={name} value={value} list={SUGGEST[name] ? `suggest-${name}` : undefined} onCommit={onCommit}/>
  {color && <input type="color" aria-label={`${name} picker`} value={/^#[0-9a-f]{6}$/i.test(value || '') ? value : '#000000'} onChange={e => onCommit(e.target.value)}/>}
  {SUGGEST[name] && <datalist id={`suggest-${name}`}>{SUGGEST[name].map(o => <option key={o} value={o}/>)}</datalist>}
 </div>
}

function ComponentData({item, model, set}) {
 const p = item.props
 const inputs = Object.entries(p.x || {}).filter(([, r]) => model.codes[r]?.data !== undefined)
 const [text, setText] = useState('')
 const [key, setKey] = useState(inputs[0]?.[0])
 useEffect(() => { if (key) setText(JSON.stringify(p.d?.[key] ?? model.codes[p.x[key]].data, null, 1)) }, [key, p.d]) // eslint-disable-line react-hooks/exhaustive-deps
 if (p.shared) return <p className="live-help">Shared document: its elements are edited directly on the canvas (changes apply on every page that shows it).</p>
 if (!inputs.length) return <p className="live-help">Functional component <code>{p.name}</code>: its behaviour and output are kept. Move, wrap, style, duplicate or delete it here.</p>
 return <div className="live-component"><p className="live-help">Functional component <code>{p.name}</code>. Its behaviour is kept; edit its content below.</p>
  <label className="live-field"><span>Input</span><select value={key} onChange={e => setKey(e.target.value)}>{inputs.map(([k]) => <option key={k}>{k}</option>)}</select></label>
  <textarea className="live-json" rows={10} aria-label={`${p.name} ${key} content`} value={text} onChange={e => setText(e.target.value)}/>
  <button onClick={() => { try { const v = JSON.parse(text); set({d: {...(p.d || {}), [key]: v}}, `${p.name} content changed`) } catch (e) { alert(`Not valid JSON: ${e.message}`) } }}>Apply content</button>
  {p.d?.[key] !== undefined && <button onClick={() => { const d = {...p.d}; delete d[key]; set({d: Object.keys(d).length ? d : undefined}, `${p.name} content reset to source`) }}>Reset to source</button>}
 </div>
}

function AssetsPanel({client, assets, setAssets, sel, content, tryCommit, insert, setMessage, onEdit}) {
 const [busy, setBusy] = useState('')
 const refresh = async () => setAssets(await client.assets())
 const isMedia = sel && sel.item.type === 'El' && ['img', 'Image', 'video', 'source'].includes(sel.item.props.tag)
 const use = (ref, name) => {
  // alt belongs to images only (a <video alt> fails the site's type check); videos keep their title.
  if (isMedia) tryCommit(() => updateProps(content, sel.item.props.id, {a: {...(sel.item.props.a || {}), src: ref, ...(sel.item.props.a?.alt || !['img', 'Image'].includes(sel.item.props.tag) ? {} : {alt: name})}, x: sel.item.props.x?.src ? Object.fromEntries(Object.entries(sel.item.props.x).filter(([k]) => k !== 'src')) : sel.item.props.x}), ['img', 'Image'].includes(sel.item.props.tag) ? 'Image replaced' : 'Media replaced')
  else if (/\.(mp4|webm)$/i.test(name)) insert({type: 'El', props: {tag: 'video', a: {src: ref, title: name.replace(/\.[a-z0-9]+$/i, ''), controls: true, playsInline: true, preload: 'metadata'}, st: {d: {width: '100%', height: 'auto', display: 'block'}}, kids: []}}, `video ${name}`)
  else insert({type: 'El', props: {tag: 'img', a: {src: ref, alt: name.replace(/\.[a-z0-9]+$/i, ''), loading: 'lazy'}, st: {d: {width: '320px', maxWidth: '100%', height: 'auto'}}}}, `image ${name}`)
 }
 const [providers, setProviders] = useState(null)
 useEffect(() => { client.mediaProviders().then(setProviders, () => {}) }, [client])
 const upload = async e => {
  const file = e.target.files?.[0]
  if (!file) return
  setBusy('Uploading…')
  try {
   const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(file) })
   const added = await client.upload(file.name, data)
   await refresh(); setMessage(`Uploaded ${added.name} (sha256 ${added.id.slice(0, 12)}…). Provenance recorded.`)
  } catch (err) { setMessage(`Upload refused: ${err.message}`) } finally { setBusy(''); e.target.value = '' }
 }
 return <div className="live-assets" data-testid="live-assets">
  <p className="live-help">{isMedia ? 'Click an asset to replace the selected image.' : 'Click an asset to insert it as an image block.'}</p>
  <label className="live-upload">Upload image or video<input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/avif,image/svg+xml,video/mp4,video/webm" onChange={upload} aria-label="Upload asset"/></label>
  {busy && <p role="status">{busy}</p>}
  <div className="live-provider" data-testid="live-image-generation" data-available={providers?.image_generation?.available ? 'true' : 'false'}>
   <button disabled title={providers?.image_generation?.detail || ''}>Generate image</button>
   <small>{providers ? providers.image_generation.detail : 'Checking image providers…'}</small>
  </div>
  <h4>Studio library ({assets.library.length})</h4>
  <div className="live-asset-grid">{assets.library.map(a => <div key={a.id} className="live-asset"><button data-asset={a.id} title={`${a.name} · ${a.provenance?.kind}${a.provenance?.file_id ? ' ' + a.provenance.file_id : ''}`} onClick={() => use(a.ref, a.name)}>{a.type.startsWith('image/') ? <img src={client.assetUrl(a.id)} alt=""/> : <span>▶</span>}<small>{a.name}</small><small className="live-prov">{a.provenance?.kind === 'google_drive' ? 'Drive' : a.provenance?.kind === 'website_source' ? 'Site' : a.provenance?.kind === 'studio_edit' ? 'Edited' : 'Upload'}</small></button>
   {a.type.startsWith('image/') && a.type !== 'image/svg+xml' && <button className="live-asset-edit" data-edit-asset={a.id} onClick={() => onEdit?.(a)}>Edit</button>}</div>)}</div>
  <h4>Website files ({assets.site.filter(a => /\.(png|jpe?g|gif|webp|avif|svg)$/i.test(a.path)).length})</h4>
  <div className="live-asset-grid">{assets.site.filter(a => /\.(png|jpe?g|gif|webp|avif|svg)$/i.test(a.path)).map(a => <button key={a.path} data-site-file={a.path} title={a.path} onClick={() => use(a.path, a.path.split('/').at(-1))}><img src={client.siteFileUrl(a.path)} alt="" loading="lazy"/><small>{a.path.split('/').at(-1)}</small></button>)}</div>
  <h4>Approved Google Drive files ({assets.drive.length})</h4>
  {!assets.drive.length && <p className="live-help">Register brand assets or select a website asset folder in Sources & Branding.</p>}
  <ul className="live-drive">{assets.drive.map(d => <li key={d.id}><span>{d.label || d.name}<small>{d.approval}{d.modifiedTime ? ` · ${d.modifiedTime.slice(0, 10)}` : ''}</small></span><button disabled={!!busy} onClick={async () => { setBusy('Importing from Drive…'); try { const a = await client.fromDrive(d.id); await refresh(); setMessage(`Imported ${a.name} from Drive (${d.id}); provenance recorded.`) } catch (e) { setMessage(`Drive import failed: ${e.message}`) } finally { setBusy('') } }}>Import</button></li>)}</ul>
 </div>
}

function VersionsPanel({client, versions, latest, setLatest, setVersions, docs, baseline, codeRef, baselineCodeRef, page, model, dispatch, data, setMessage, dirty, capture, assets}) {
 const [preview, setPreview] = useState(null)
 const [busy, setBusy] = useState(false)
 const rows = [...versions].reverse()
 const show = async n => {
  setBusy(true)
  try {
   const v = await client.version(n)
   setPreview({n, html: staticPage({page, model, client, capture, assets, docs: v.docs, code: v.code}), mobile: false})
  } catch (e) { setMessage(e.message) } finally { setBusy(false) }
 }
 const restore = async n => {
  if (dirty.length && !window.confirm('You have unsaved changes. Restoring creates a new version from the older one; your unsaved changes stay in Undo history but are replaced on the canvas. Continue?')) return
  setBusy(true)
  try {
   const saved = await client.restore(n, latest)
   const v = await client.version(saved.version)
   docs.current = v.docs
   baseline.current = JSON.parse(JSON.stringify(v.docs))
   codeRef.current = normalizeCode(v.code || {})
   baselineCodeRef.current = normalizeCode(v.code || {})
   setLatest(saved.version)
   setVersions(list => [...list, {...saved, documents: Object.keys(saved.docs)}])
   dispatch({type: 'setData', data: {...data, root: {...(data.root || {}), props: {...(data.root?.props || {}), code: normalizeCode(v.code || {})}}, content: toPuck(compose(page, v.docs))}, recordHistory: true})
   setMessage(`Restored ${n ? `v${n}` : 'the imported live website'} as new version v${saved.version}. Nothing was overwritten; Undo returns to the previous canvas.`)
  } catch (e) { setMessage(`Restore refused: ${e.message}`) } finally { setBusy(false) }
 }
 return <div className="live-versions" data-testid="live-versions">
  <p className="live-help">Every saved version is immutable and timestamped. Restore appends a new version; older and newer versions are kept.</p>
  <ol>{rows.map(v => <li key={v.version} data-version={v.version}><div><strong>v{v.version}</strong> · {fmtTime(v.at)}<small>{v.origin}{v.restored_from !== null && v.restored_from !== undefined ? ` of v${v.restored_from}` : ''} · {v.note}</small><small>{(v.changed || []).length} change(s){(v.changed || []).includes('code-layer') ? ' incl. code layer' : ''}{v.proposals?.length ? ` · Website Manager ${v.proposals.join(', ')}` : ''} · {v.actor}</small></div>
   <div className="live-row"><button disabled={busy} onClick={() => show(v.version)}>Preview</button><button disabled={busy || v.version === latest} onClick={() => restore(v.version)}>Restore</button></div></li>)}
   <li data-version="0"><div><strong>Imported live site</strong><small>{model.commit.slice(0, 12)}</small></div><div className="live-row"><button disabled={busy} onClick={() => show(0)}>Preview</button><button disabled={busy || !latest} onClick={() => restore(0)}>Restore</button></div></li>
  </ol>
  <SourcePanel client={client} model={model} setMessage={setMessage}/>
  {preview && <div className="live-preview" data-testid="live-version-preview" data-version={preview.n}><div className="live-row"><strong>Preview of {preview.n ? `v${preview.n}` : 'the imported site'} · {page.label}</strong><button onClick={() => setPreview({...preview, mobile: !preview.mobile})}>{preview.mobile ? 'Desktop' : 'Mobile'}</button><button onClick={() => setPreview(null)}>Close</button></div>
   <iframe title={`Version ${preview.n} preview`} sandbox="allow-same-origin" srcDoc={preview.html} style={{width: preview.mobile ? 390 : '100%', height: 560}}/></div>}
 </div>
}

// The pinned live source. When the website's source moves on (a new commit on its production line),
// the operator imports it and the latest saved version and autosave are carried onto it (conflicts
// listed); older versions are carried over when restored. Nothing is published by this.
function SourcePanel({client, model, setMessage}) {
 const [commit, setCommit] = useState('')
 const [status, setStatus] = useState(null)
 useEffect(() => {
  if (status?.importing?.state !== 'running') return
  const t = setInterval(async () => {
   try {
    const s = await client.status(); setStatus(s)
    if (s.importing?.state === 'finished') { const m = s.importing.result?.migration; setMessage(m ? `Now editing ${m.to.slice(0, 8)}; ${m.version ? `saved v${m.version} carried over` : 'no saved versions to carry'} (${(m.version_report?.conflicts || []).length} conflicts). Reloading…` : 'Import finished.'); setTimeout(() => window.location.reload(), 1500) }
    if (s.importing?.state === 'failed') setMessage(`Source update failed: ${s.importing.error}`)
   } catch (e) { setMessage(e.message) }
  }, 4000)
  return () => clearInterval(t)
 }, [status?.importing?.state, client, setMessage])
 const start = async () => { try { setStatus({importing: await client.rebase(commit.trim())}); setMessage(`Importing ${commit.trim().slice(0, 8)} and carrying your latest saved version onto it (two real builds, a few minutes)…`) } catch (e) { setMessage(`Not started: ${e.message}`) } }
 return <details className="live-source" data-testid="live-source"><summary>Website source · {model.commit.slice(0, 12)}</summary>
  <p className="live-help">When the website's source changes, import the new commit here. Your latest saved version and unsaved autosave are carried onto it; anything that cannot be carried over is listed. Older versions are carried over when you restore them.</p>
  <label className="live-field"><span>New source commit (full SHA)</span><input value={commit} onChange={e => setCommit(e.target.value)} placeholder="40-character commit" spellCheck={false}/></label>
  <button disabled={!/^[0-9a-f]{40}$/.test(commit.trim()) || status?.importing?.state === 'running'} onClick={start}>Update to this source</button>
  {status?.importing && <p className="live-help">Import: {status.importing.state}</p>}
 </details>
}

function BuildPanel({client, latest, page, setMessage, dirty, onOpenReleases, previewBuild: build, setPreviewBuild: setBuild}) {
 const [mobile, setMobile] = useState(false)
 const [expanded, setExpanded] = useState(false)
 useEffect(() => {
  if (!expanded) return
  const close = e => { if (e.key === 'Escape') { e.preventDefault(); setExpanded(false) } }
  window.addEventListener('keydown', close)
  return () => window.removeEventListener('keydown', close)
 }, [expanded])
 useEffect(() => {
  // A release build reports "ready" a few seconds before its validation is attached: keep polling.
  if (build?.status !== 'building' && !(build?.status === 'ready' && build.purpose === 'release' && !build.validation)) return
  const t = setInterval(async () => { try { setBuild(await client.buildStatus(build.id)) } catch (e) { setMessage(e.message) } }, 3000)
  return () => clearInterval(t)
 }, [build?.status, build?.id, build?.validation, client, setMessage])
 const start = async purpose => { try { setBuild(await client.build(latest, purpose)); setMessage(`Building v${latest} into the real Next.js site (${purpose}). About a minute.`) } catch (e) { setMessage(`Build not started: ${e.message}`) } }
 return <div className="live-build" data-testid="live-build">
  <p className="live-help">Builds the saved version <strong>v{latest || '—'}</strong> into the real website source ({'2e521be'}) in an isolated folder with integrations disabled, runs <code>next build</code> and serves it on loopback for 30 minutes. Nothing is published.</p>
  {!!dirty.length && <p className="live-help" role="note">Unsaved changes are not included; save a version first.</p>}
  <div className="live-row"><button className="live-primary" disabled={!latest || build?.status === 'building'} onClick={() => start('preview')}>Build real Next.js preview</button><button disabled={!latest || build?.status === 'building'} onClick={() => start('release')}>Build release candidate</button></div>
  {build && <div data-testid="live-build-status" data-status={build.status}><p>Build {build.id.slice(0, 8)} · v{build.versions?.live?.version} · <strong>{build.status}</strong>{build.nextBuildId ? ` · Next build ${build.nextBuildId}` : ''}{build.validation ? ` · release validation ${build.validation}` : ''}{build.error ? ` · ${build.error}` : ''}</p>
   {build.status === 'ready' && <>
    <div className="live-row"><button onClick={() => setMobile(false)} aria-pressed={!mobile}>Desktop</button><button onClick={() => setMobile(true)} aria-pressed={mobile}>Mobile</button><a href={build.url + page.route.replace(/^\/_not-found$/, '/missing').slice(1)} target="_blank" rel="noreferrer">Open in new tab</a><button onClick={async () => setBuild(await client.stopBuild(build.id))}>Close preview</button>{build.purpose === 'release' && build.validation === 'passed' && onOpenReleases && <button onClick={() => onOpenReleases(build)}>Open Releases</button>}</div>
    {build.purpose !== 'release' && <button onClick={() => setExpanded(true)}>Expand preview</button>}
    {build.purpose !== 'release' && <div className={expanded ? 'live-preview' : ''} data-testid="live-next-frame" data-expanded={expanded}>
     {expanded && <div className="live-row"><strong>Real Next.js build · v{build.versions?.live?.version} · {page.route}</strong><button aria-pressed={!mobile} onClick={() => setMobile(false)}>Desktop 1280</button><button aria-pressed={mobile} onClick={() => setMobile(true)}>Mobile 390</button><button onClick={() => setExpanded(false)}>Close</button></div>}
     <iframe data-testid="live-next-preview" title="Built Next.js website" src={build.url + page.route.slice(1).replace(/^_not-found$/, 'missing')} style={{boxSizing: 'content-box', width: mobile ? 390 + SCROLLBAR : expanded ? 1280 + SCROLLBAR : '100%', maxWidth: expanded ? 'none' : '100%', height: expanded ? 'calc(100% - 40px)' : 640, border: '1px solid #c9c2b5', display: 'block', margin: '0 auto'}}/>
    </div>}
    <details><summary>Exact source changes</summary><pre className="live-diff">{build.sourceDiff}</pre></details>
   </>}
  </div>}
 </div>
}

function CoveragePanel({client, model}) {
 const [data, setData] = useState(null)
 useEffect(() => { client.coverage().then(setData, () => setData({routes: [], parity: {}})) }, [client])
 const counts = useMemo(() => {
  const out = {El: 0, Txt: 0, Code: 0, Cond: 0, Use: 0}
  for (const d of Object.values(model.docs)) for (const {item} of walk(d.tree)) out[item.type]++
  return out
 }, [model])
 if (!data) return <p role="status">Reading coverage…</p>
 const parity = Object.values(data.parity || {})
 return <div className="live-coverage" data-testid="live-coverage">
  <p><strong>{model.pages.length}</strong> pages from {Object.keys(model.docs).length} source documents · {counts.El + counts.Txt} elements and text runs individually editable · {counts.Cond} conditions with editable branches · {counts.Use} components · {counts.Code} data-driven parts.</p>
  <p>Regeneration parity: <strong>{parity.filter(x => x.body && x.seo).length}/{parity.length}</strong> routes render identically when every element is rebuilt from the model.</p>
  <h4>Not element-editable</h4>
  <ul>{data.routes.filter(r => r.status !== 'visual').map(r => <li key={r.route}><code>{r.route}</code> · {r.detail}</li>)}
   <li>Data-driven parts ({counts.Code}): blog post titles/dates/related lists and resource pages come from <code>content/blog.ts</code> / <code>content/resources.ts</code>; their containers move and restyle, their entries are data.</li>
   <li>FAQ accordions: functional component; questions/answers are edited as component content, their item markup is the component's own.</li>
   <li>State-dependent text (for example the menu button's “Open menu/Close menu”) stays code.</li>
   <li><code>components/HeroDiagram.tsx</code> is not rendered by any live page, so no page shows it.</li>
   <li>Tailwind classes typed into Classes render in the built site, not in the canvas; Studio style properties render in both.</li></ul>
 </div>
}
