// Live editor canvas: every model node is a real Puck component whose root IS the site element
// (slots render `as` the element itself), so selection, drag-and-drop, reparenting, duplicate and
// delete work on the actual markup. Values that come from source code are shown as the import
// rendered them (capture); functional parts (FAQ accordion, scripts, data lists) show their
// rendered HTML and keep their behaviour in the built site. Third-party frames are placeholders.
import React, {createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {createUsePuck} from '@puckeditor/core'
import {VOID, SVG, styleClass, treeCss, BREAKPOINTS} from './model.js'
import {headerCss, headerState} from './code.js'
export {EntryContext}

export const CanvasContext = createContext(null)
// The entry of a repeated template being rendered ({index, entry}) and a template component's inputs.
const EntryContext = createContext(null)
const InputsContext = createContext(null)
// The page's current Puck data (provided by the editor frame, or by a static version preview).
export const DataContext = createContext(null)
const usePuck = createUsePuck()
const pathValue = (path, entry, names) => {
 if (!path || entry === undefined || !names?.length || path[0] !== names[0]) return undefined
 let v = entry
 for (const k of path.slice(1)) { if (v === null || typeof v !== 'object') return undefined; v = v[k] }
 return ['string', 'number'].includes(typeof v) ? String(v) : undefined
}
// Capture of one entry (k > 0 shows each further entry as the site rendered it).
const entryCapture = (capture, k) => !k || !capture ? capture : {...capture,
 attrs: Object.fromEntries(Object.entries(capture.attrsAll || {}).map(([id, list]) => [id, list[k] ?? list[0]]).concat(Object.entries(capture.attrs || {}).filter(([id]) => !capture.attrsAll?.[id]))),
 html: Object.fromEntries(Object.entries(capture.htmlAll || {}).map(([id, list]) => [id, list[k] ?? list[0]]).concat(Object.entries(capture.html || {}).filter(([id]) => !capture.htmlAll?.[id])))}

const camel = s => s.replace(/^-webkit-/, 'Webkit-').replace(/-([a-z])/g, (_, c) => c.toUpperCase())
export function parseStyle(text) {
 const out = {}
 for (const part of String(text || '').split(';')) {
  const i = part.indexOf(':')
  if (i > 0) out[camel(part.slice(0, i).trim())] = part.slice(i + 1).trim()
 }
 return out
}
const htmlNames = k => [k, k.toLowerCase(), k.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), k === 'className' ? 'class' : null, k === 'htmlFor' ? 'for' : null].filter(Boolean)
const DROP = new Set(['key', 'priority', 'unoptimized', 'quality', 'fill', 'prefetch', 'scroll', 'replace', 'placeholder', 'blurDataURL', 'loader', 'onLoad'])

export function resolveUrl(value, ctx) {
 if (typeof value !== 'string') return value
 if (value.startsWith('asset:')) return ctx.assetUrl(value.slice(6))
 if (value.startsWith('/') && !value.startsWith('//') && ctx.siteFiles.has(value.split(/[?#]/)[0])) return ctx.siteFileUrl(value.split(/[?#]/)[0])
 return value
}

// DOM props for one element as it renders on the site.
export function domProps(p, ctx) {
 const cap = ctx.capture?.attrs?.[p.id]
 const out = {}
 for (const [k, v] of Object.entries(p.a || {})) if (!DROP.has(k)) out[k] = v
 for (const [k, ref] of Object.entries(p.x || {})) {
  if (k.startsWith('...') || k === 'className' || DROP.has(k) || /^on[A-Z]/.test(k) || k === 'ref' || k === 'dangerouslySetInnerHTML') continue
  const c = ctx.codes[ref]
  if (c?.value !== undefined && c.value !== null) out[k] = c.value
  else if (k === 'style' && c?.data && typeof c.data === 'object') out.style = c.data
  else if (cap) { const name = htmlNames(k).find(n => cap[n] !== undefined); if (name) out[k] = k === 'style' ? parseStyle(cap[name]) : cap[name] }
 }
 let cls = p.cls
 if (p.x?.className) { const c = ctx.codes[p.x.className]; cls = typeof c?.value === 'string' ? c.value : cap?.class }
 const classes = [cls, p.st ? styleClass(p.id) : null].filter(Boolean).join(' ')
 if (classes) out.className = classes
 if (cap?.style && !out.style && p.x?.style) out.style = parseStyle(cap.style)
 for (const k of ['src', 'href', 'poster', 'srcSet', 'xlinkHref']) if (out[k] !== undefined) out[k] = k === 'srcSet' ? String(out[k]).split(',').map(s => { const [u, ...r] = s.trim().split(' '); return [resolveUrl(u, ctx), ...r].join(' ') }).join(', ') : resolveUrl(out[k], ctx)
 // Transient header/menu simulation never enters the document or generated source.
 if (ctx.headerPreview === 'menu' && ctx.codes[p.x?.['aria-expanded']]?.preview === 'menuOpen') {
  out['aria-expanded'] = true
  if (out['aria-controls'] === 'mobile-menu') out['aria-label'] = 'Close menu'
 }
 return out
}

function tagOf(tag, props) {
 if (tag === 'Link') return 'a'
 if (tag === 'Image') {
  if (props.fill) Object.assign(props, {style: {position: 'absolute', inset: 0, width: '100%', height: '100%', ...(props.style || {})}})
  return 'img'
 }
 return tag
}
const minEmpty = (p, ctx) => (ctx.pristineEmpty.has(p.id) ? '0px' : '40px')

function ElementRender(props) {
 const ctx = useContext(CanvasContext)
 const {id, tag, kids: Kids, puck} = props
 const dom = {...domProps(props, ctx), 'data-sid': id}
 if (dom.style?.position) dom['data-studio-inline-position'] = ''
 const T = tagOf(tag, dom)
 if (T === 'iframe') {
  // Embeds (booking form, video) are not run in the editor: a selectable box of the same size and
  // classes stands in; the built site keeps the real <iframe> with its source binding.
  const label = String(dom.src || '').replace(/^https:\/\//, '').slice(0, 80)
  const {src, srcDoc, allow, allowFullScreen, frameBorder, loading, referrerPolicy, sandbox, scrolling, title, width, height, ...rest} = dom
  const size = v => (v === undefined ? undefined : /^\d+$/.test(String(v)) ? `${v}px` : String(v))
  return <div ref={puck?.dragRef} {...rest} role="img" aria-label={title || 'Embedded content'} data-studio-embed={label}
   style={{display: 'grid', placeItems: 'center', boxSizing: 'border-box', width: size(width) || '300px', height: size(height) || '150px', minHeight: 120, background: 'repeating-linear-gradient(45deg,#f4f1ea,#f4f1ea 10px,#ece7dc 10px,#ece7dc 20px)', border: '1px dashed #b8ab95', color: '#2b2f32', font: '14px system-ui', textAlign: 'center', ...(rest.style || {})}}>
   <span><b>Embedded: {label || 'frame'}</b><br/><small>{title}</small><br/><small>Runs on the website; not loaded in the editor.</small></span></div>
 }
 if (T === 'video' || T === 'audio') { dom.autoPlay = false; dom.preload = 'metadata' }
 if (VOID.has(tag) || VOID.has(T) || typeof Kids !== 'function') return React.createElement(T, {...dom, ref: puck?.dragRef})
 // Call the slot function so its DropZone receives the drag ref (React 18 drops refs on function components).
 return Kids({as: T, ...dom, 'data-studio-container': '', ...(puck?.dragRef ? {ref: puck.dragRef} : {}), minEmptyHeight: minEmpty(props, ctx)})
}

function TextRender({id, text, puck}) {
 const ctx = useContext(CanvasContext)
 if (!ctx.editing) return <>{text}</>
 return <span ref={puck?.dragRef} data-sid={id} data-studio-text="">{text}</span>
}

function HtmlRender({id, st, puck, html, label}) {
 const ctx = useContext(CanvasContext)
 const className = st ? styleClass(id) : undefined
 const value = html ?? ''
 if (!ctx.editing) return <div className={className} style={st ? undefined : {display: 'contents'}} dangerouslySetInnerHTML={{__html: value}}/>
 return <div ref={puck?.dragRef} data-sid={id} data-studio-opaque={label} className={className} style={st ? undefined : {display: 'contents'}} dangerouslySetInnerHTML={{__html: value || ''}}/>
}

function CodeRender(props) {
 const ctx = useContext(CanvasContext)
 const entry = useContext(EntryContext)
 const data = useContext(DataContext) || {content: []}
 const code = ctx.codes[props.c]
 // A post created in the Studio: its record fields come from the post settings.
 if (code?.bind?.file === 'content/blog.ts' && ctx.page?.record) {
  const value = ctx.page.record[code.bind.field] ?? (code.bind.field === 'excerpt' ? ctx.page.record.description : undefined)
  if (typeof value === 'string') return <HtmlRender {...props} label="data" html={escapeHtml(value)}/>
 }
 // A route's data field (blog post title, resource preview…) shows the record as edited.
 if (code?.bind && ctx.page?.data?.[code.bind.file]) {
  const holder = (data.content || []).find(i => i.type === 'Data' && i.props.shared === code.bind.file)
  const rec = holder?.props.kids?.find(r => r.props.key === ctx.page.data[code.bind.file])
  const value = rec?.props.f?.[code.bind.field]
  if (typeof value === 'string') return <HtmlRender {...props} label="data" html={escapeHtml(value)}/>
 }
 const fromEntry = pathValue(code?.path, entry?.entry, entry?.names)
 if (fromEntry !== undefined) return <HtmlRender {...props} label="data" html={escapeHtml(fromEntry)}/>
 return <HtmlRender {...props} label="data" html={ctx.capture?.html?.[props.id]}/>
}
const escapeHtml = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]))

// A repeated template: the first entry is the editable template; the others render the same
// template with their own values (read-only), so edits show on every entry at once.
function MapRender({id, c, kids: Kids, puck}) {
 const ctx = useContext(CanvasContext)
 const outer = useContext(EntryContext)
 const inputs = useContext(InputsContext)
 const data = useContext(DataContext) || {content: []}
 const code = ctx.codes[c]
 const list = code?.list?.prop && Array.isArray(inputs?.[code.list.prop]) ? inputs[code.list.prop] : null
 const byEntry = (ctx.rootCapture || ctx.capture)?.countsByEntry?.[id]
 const captured = byEntry ? byEntry[outer?.index ?? 0] ?? 0 : ctx.capture?.counts?.[id]
 const count = list ? list.length : captured ?? 1
 const names = code?.names
 const template = useMemo(() => findPuck(data.content || [], id)?.props.kids || [], [data, id])
 if (typeof Kids !== 'function') return null
 return <>
  <EntryContext.Provider value={{index: 0, entry: list?.[0], names, map: id}}>
   {Kids({as: 'div', 'data-sid': id, 'data-studio-map': String(count), 'data-studio-container': '', style: {display: 'contents'}, ...(puck?.dragRef ? {ref: puck.dragRef} : {}), minEmptyHeight: '24px'})}
  </EntryContext.Provider>
  {Array.from({length: Math.max(0, count - 1)}, (_, k) => <EntryContext.Provider key={k + 1} value={{index: k + 1, entry: list?.[k + 1], names, map: id}}>
   <CanvasContext.Provider value={{...ctx, rootCapture: ctx.rootCapture || ctx.capture, capture: entryCapture(ctx.rootCapture || ctx.capture, k + 1), editing: false}}><StaticList items={template} inert/></CanvasContext.Provider>
  </EntryContext.Provider>)}
 </>
}
const findPuck = (list, id) => { for (const item of list || []) { if (item.props?.id === id) return item; for (const s of ['kids', 'alt']) { const hit = findPuck(item.props?.[s], id); if (hit) return hit } } return null }

// Read-only rendering of model nodes (Puck form) for further entries of a template.
function StaticList({items, inert}) {
 return <>{(items || []).map(item => <StaticNode key={item.props.id} item={item} inert={inert}/>)}</>
}
function StaticNode({item, inert}) {
 const ctx = useContext(CanvasContext)
 const p = item.props
 if (item.type === 'Txt') return <>{p.text}</>
 if (item.type === 'Code' || (item.type === 'Use' && !p.shared)) return <CodeRender {...p} html={ctx.capture?.html?.[p.id]}/>
 if (item.type === 'Cond') { const branch = (p.show || 'kids') === 'alt' ? p.alt : p.show === 'none' ? [] : p.kids; return <StaticList items={branch}/> }
 if (item.type === 'Map') return <StaticMap item={item}/>
 if (item.type === 'Use' && p.shared) return <StaticList items={p.kids}/>
 if (item.type === 'Rec' || item.type === 'Data') return null
 if (item.type === 'Comp') return <CompRender {...p}/>
 const dom = domProps(p, ctx)
 const T = tagOf(p.tag, dom)
 const extra = inert ? {'data-studio-entry': '', 'aria-hidden': 'true'} : {}
 if (VOID.has(p.tag) || VOID.has(T)) return React.createElement(T, {...dom, ...extra})
 return React.createElement(T, {...dom, ...extra}, <StaticList items={p.kids}/>)
}

// A nested template inside a read-only entry: its own entries, from the capture of that entry.
function StaticMap({item}) {
 const ctx = useContext(CanvasContext)
 const outer = useContext(EntryContext)
 const root = ctx.rootCapture || ctx.capture
 const per = root?.countsByEntry?.[item.props.id]
 const k = outer?.index ?? 0
 const n = per ? per[k] ?? 0 : 1
 const offset = per ? per.slice(0, k).reduce((a, b) => a + b, 0) : 0
 return <>{Array.from({length: n}, (_, j) => <CanvasContext.Provider key={j} value={{...ctx, capture: entryCapture(root, offset + j)}}><StaticList items={item.props.kids}/></CanvasContext.Provider>)}</>
}

// Content records (hidden on the canvas; edited in the Inspector, one undo step per change).
function DataRender({kids: Kids, shared, id}) {
 if (typeof Kids !== 'function') return null
 return Kids({as: 'div', 'data-sid': id, 'data-studio-data': shared, style: {display: 'none'}, minEmptyHeight: '0px'})
}
function RecRender({id, puck}) {
 return <span ref={puck?.dragRef} data-sid={id} hidden/>
}

function CondRender({id, c, kids: Kids, alt: Alt, show, puck}) {
 const ctx = useContext(CanvasContext)
 if (ctx.headerPreview === 'menu' && ctx.codes[c]?.doc === 'components/Header.tsx' && ctx.codes[c]?.preview === 'menuOpen') show = 'kids'
 if (show === 'none') return ctx.editing ? <div ref={puck?.dragRef} data-sid={id} data-studio-cond="none" style={{display: 'none'}}/> : null
 const branch = (show || 'kids') === 'alt' ? Alt : Kids
 if (typeof branch !== 'function') return null
 return branch({as: 'div', 'data-sid': id, 'data-studio-cond': show || 'kids', 'data-studio-container': '', style: {display: 'contents'}, ...(puck?.dragRef ? {ref: puck.dragRef} : {}), minEmptyHeight: ctx.editing ? '24px' : '0px'})
}

function UseRender(props) {
 const ctx = useContext(CanvasContext)
 const {id, name, st, kids: Kids, shared, puck} = props
 if (shared && typeof Kids === 'function') {
  const style = st ? undefined : {display: 'contents'}
  const slot = Kids({as: 'div', 'data-sid': id, 'data-studio-shared': shared, 'data-studio-container': '', className: st ? styleClass(id) : undefined, style, ...(puck?.dragRef ? {ref: puck.dragRef} : {}), minEmptyHeight: '0px'})
  if (!props.tpl) return slot
  // A template component: its repeated items render this page's inputs (as edited).
  const inputs = Object.fromEntries(Object.entries(props.x || {}).map(([k, r]) => [k, props.d?.[k] ?? ctx.codes[r]?.data]).filter(([, v]) => v !== undefined))
  return <InputsContext.Provider value={inputs}>{slot}</InputsContext.Provider>
 }
 return <HtmlRender {...props} label={name} html={ctx.capture?.html?.[id] ?? ''}/>
}

// A React component placed visually (written in Code mode or one of the site's): it renders in the
// built site; the canvas shows a selectable, labelled box (component code is not run in the editor).
function CompRender({id, name, file, st, puck}) {
 return <div ref={puck?.dragRef} data-sid={id} data-studio-comp={name} className={st ? styleClass(id) : undefined}
  style={{display: 'grid', placeItems: 'center', minHeight: 96, padding: 16, boxSizing: 'border-box', background: 'repeating-linear-gradient(135deg,#eef2f7,#eef2f7 10px,#e3e9f1 10px,#e3e9f1 20px)', border: '1px dashed #6b7f99', color: '#1f2a37', font: '14px system-ui', textAlign: 'center'}}>
  <span><b>{'<'}{name} /{'>'}</b><br/><small>{file}</small><br/><small>React component · renders in the real preview build</small></span></div>
}

const common = {inline: true}
const field = {type: 'slot'}
export const liveConfig = {
 root: {render: ({children}) => <>{children}</>},
 components: {
  El: {...common, label: 'Element', fields: {kids: {...field, disallow: ['Shape']}}, render: ElementRender},
  Graphic: {...common, label: 'Graphic', fields: {kids: {...field, allow: ['Shape']}}, render: ElementRender},
  Shape: {...common, label: 'Shape', fields: {kids: {...field, allow: ['Shape', 'Txt']}}, render: ElementRender},
  Txt: {...common, label: 'Text', fields: {}, render: TextRender},
  Code: {...common, label: 'Data-driven content', fields: {}, render: CodeRender},
  Cond: {...common, label: 'Conditional content', fields: {kids: field, alt: field}, render: CondRender},
  Use: {...common, label: 'Component', fields: {kids: {...field, disallow: ['Shape']}}, render: UseRender,
   resolvePermissions: ({props}) => (props.shared ? {duplicate: false} : {})},
  Map: {...common, label: 'Repeated item (template)', fields: {kids: {...field, disallow: ['Shape']}}, render: MapRender,
   resolvePermissions: () => ({duplicate: false})},
  Data: {...common, label: 'Content records', fields: {kids: {...field, allow: ['Rec']}}, render: DataRender,
   resolvePermissions: () => ({duplicate: false, delete: false, drag: false})},
  Rec: {...common, label: 'Content record', fields: {}, render: RecRender, resolvePermissions: () => ({duplicate: false, delete: false, drag: false})},
  Comp: {...common, label: 'React component', fields: {}, render: CompRender},
 },
}
// Puck component type names for model nodes (SVG content has its own types so shapes stay in graphics).
export const puckType = item => item.type !== 'El' ? item.type : item.props.tag === 'svg' ? 'Graphic' : SVG.has(item.props.tag) ? 'Shape' : 'El'
export const modelType = type => (type === 'Graphic' || type === 'Shape' ? 'El' : type)
export function toPuck(list) {
 return list.map(item => {
  const props = {...item.props}
  for (const s of ['kids', 'alt']) if (Array.isArray(props[s])) props[s] = toPuck(props[s])
  return {type: puckType(item), props}
 })
}
export function fromPuck(list) {
 return list.map(item => {
  const props = {...item.props}
  for (const s of ['kids', 'alt']) if (Array.isArray(props[s])) props[s] = fromPuck(props[s])
  const type = modelType(item.type)
  if (type === 'El' && VOID.has(props.tag)) delete props.kids
  if (type === 'Use' && !props.shared && Array.isArray(props.kids) && !props.kids.length) delete props.kids
  if (type === 'Cond') { props.kids = props.kids || []; props.alt = props.alt || [] }
  if (type === 'Map') props.kids = props.kids || []
  return {type, props}
 })
}

// ---------------------------------------------------------------- iframe: site styles + guards
function neutralise(doc) {
 for (const sheet of [...doc.styleSheets]) {
  let rules
  try { rules = sheet.cssRules } catch { continue }
  for (const rule of [...rules]) {
   const sel = rule.selectorText || ''
   if (/^\._DropZone_[a-z0-9]+_1$/.test(sel)) for (const p of ['position', 'height', 'width', 'min-height']) rule.style.removeProperty(p)
   if (/^\._DropZone_[a-z0-9]+_1:empty$/.test(sel)) { rule.style.removeProperty('background'); rule.style.removeProperty('outline') }
   if (/^\._DropZone-item_/.test(sel)) rule.style.removeProperty('position')
  }
 }
}
export function LiveFrame({children, document: doc}) {
 const ctx = useContext(CanvasContext)
 const data = usePuck(s => s.appState.data)
 const css = useMemo(() => treeCss(data.content || [], a => ctx.assetUrl(a.slice(6))), [data, ctx])
 // The version's owner CSS and header behaviour CSS, exactly as the build appends them to globals.css.
 const code = data.root?.props?.code
 const [headerPreview, setHeaderPreview] = useState('')
 useEffect(() => {
  if (!doc) return
  const preview = e => { setHeaderPreview(e.detail || ''); if (e.detail === 'top') doc.defaultView.scrollTo(0, 0) }
  doc.addEventListener('ttros-header-preview', preview)
  return () => doc.removeEventListener('ttros-header-preview', preview)
 }, [doc])
 useLayoutEffect(() => {
  if (!doc) return
  const header = doc.querySelector('header')
  if (!header) return
  const node = findPuck(data.content, header.dataset.sid)
  const sourceHeader = ctx.codes[node?.props.x?.style]?.preview === 'scrolledStyle'
  if (!sourceHeader && !code?.header) return
  let lastY = doc.defaultView.scrollY
  const update = () => {
   const y = Math.max(0, doc.defaultView.scrollY)
   const menuOpen = headerPreview === 'menu' || !!header.querySelector('[aria-controls][aria-expanded="true"]')
   if (code?.header) {
    const bp = doc.defaultView.innerWidth >= 1024 ? 'desktop' : doc.defaultView.innerWidth >= 768 ? 'tablet' : 'mobile'
    const state = headerState(code.header, bp, {y, lastY, menuOpen, height: header.offsetHeight})
    const mode = state.mode
    header.dataset.studioMode = mode
    header.dataset.studioHidden = String(headerPreview === 'hidden' ? mode === 'auto-hide' && !menuOpen : headerPreview ? false : state.hidden)
    header.dataset.studioSolid = String(mode === 'sticky-solid' || menuOpen || (mode !== 'static' && (headerPreview ? headerPreview !== 'top' : state.solid)))
   } else {
    // The pinned Header.tsx changes these source-bound styles at scrollY > 8.
    // Its React scroll listener does not run inside Puck, so simulate it here.
    const solid = headerPreview && headerPreview !== 'menu' ? headerPreview !== 'top' : y > 8
    Object.assign(header.style, solid ? {backgroundColor:'rgba(247, 243, 234, 0.96)',borderBottomColor:'rgba(216, 208, 194, 0.5)',backdropFilter:'blur(12px)',WebkitBackdropFilter:'blur(12px)',boxShadow:'0 1px 0 0 rgba(216,208,194,0.4)'}
     : {backgroundColor:'transparent',borderBottomColor:'transparent',backdropFilter:'none',WebkitBackdropFilter:'none',boxShadow:'none'})
    for (const name of ['studioMode','studioHidden','studioSolid']) delete header.dataset[name]
   }
   header.dataset.studioCanvasHeader = headerPreview || 'auto'
   lastY = y
  }
  update()
  doc.defaultView.addEventListener('scroll', update, {passive:true})
  doc.defaultView.addEventListener('resize', update)
  return () => { doc.defaultView.removeEventListener('scroll', update); doc.defaultView.removeEventListener('resize', update) }
 }, [doc, data, code?.header, headerPreview, ctx.codes])
 const owner = useMemo(() => (code?.css || '').replace(/asset:([a-f0-9]{64})/g, (_, id) => ctx.assetUrl(id)) + (code?.header ? '\n' + headerCss() : ''), [code?.css, code?.header, ctx])
 useLayoutEffect(() => {
  if (!doc) return
  doc.documentElement.className = ctx.capture?.htmlClass || ''
  doc.documentElement.lang = 'en-CA'
  if (ctx.capture?.bodyClass) doc.body.className = ctx.capture.bodyClass
  neutralise(doc)
  const observer = new MutationObserver(() => neutralise(doc))
  observer.observe(doc.head, {childList: true, subtree: true})
  // Puck marks each draggable element position:relative inline; the site's own positioning
  // (sticky header, absolute decorations) must win in the canvas, as on the site.
  const unpin = el => { if (el.style?.position === 'relative' && !el.hasAttribute('data-studio-inline-position')) el.style.removeProperty('position') }
  doc.querySelectorAll('[data-puck-component]').forEach(unpin)
  const positions = new MutationObserver(list => { for (const m of list) if (m.target.nodeType === 1) unpin(m.target) })
  positions.observe(doc.body, {attributes: true, attributeFilter: ['style', 'data-puck-component'], subtree: true})
  const onLoad = () => neutralise(doc)
  doc.addEventListener('load', onLoad, true)
  // The canvas never navigates, submits or runs site links.
  const block = e => { const a = e.target.closest?.('a,button,summary'); if (a) e.preventDefault() }
  const submit = e => e.preventDefault()
  doc.addEventListener('click', block, true)
  doc.addEventListener('submit', submit, true)
  return () => { observer.disconnect(); positions.disconnect(); doc.removeEventListener('load', onLoad, true); doc.removeEventListener('click', block, true); doc.removeEventListener('submit', submit, true) }
 }, [doc, ctx.capture])
 return <>
  <link rel="stylesheet" href={ctx.page?.css ? ctx.canvasUrl(ctx.page.css) : ctx.cssUrl}/>
  <style data-studio-canvas="">{`[data-studio-text]{display:inline}[data-studio-opaque]{cursor:default}html{scroll-behavior:auto}`}</style>
  <style data-studio-owner-styles="">{css}</style>
  <style data-studio-owner-css="">{owner}</style>
  <CanvasContext.Provider value={{...ctx, headerPreview}}><DataContext.Provider value={data}>{children}</DataContext.Provider></CanvasContext.Provider>
  {doc && <Handles doc={doc}/>}
 </>
}

// ---------------------------------------------------------------- free move / resize handles
export const bpForWidth = w => (w >= 1024 ? 'd' : w >= 768 ? 't' : 'm')
function Handles({doc}) {
 const ctx = useContext(CanvasContext)
 const selected = usePuck(s => s.selectedItem)
 const width = usePuck(s => s.appState.ui.viewports?.current?.width) || 1280
 const [box, setBox] = useState(null)
 const drag = useRef(null)
 const id = selected?.props?.id
 const isText = selected?.type === 'Txt'
 const target = id ? doc.querySelector(`[data-sid="${CSS.escape(id)}"]`) : null
 useEffect(() => {
  if (!target) { setBox(null); return }
  let frame
  const view = doc.defaultView
  if (!view) return
  const tick = () => {
   const r = target.getBoundingClientRect()
   const rect = r.width || r.height ? r : [...target.children].map(c => c.getBoundingClientRect()).reduce((a, b) => (a ? {left: Math.min(a.left, b.left), top: Math.min(a.top, b.top), right: Math.max(a.right, b.right), bottom: Math.max(a.bottom, b.bottom)} : b), null)
   if (rect) setBox(b => { const n = {left: rect.left, top: rect.top, width: (rect.right ?? rect.left + rect.width) - rect.left, height: (rect.bottom ?? rect.top + rect.height) - rect.top}; return b && Object.keys(n).every(k => Math.abs(b[k] - n[k]) < 0.5) ? b : n })
   frame = view.requestAnimationFrame(tick)
  }
  tick()
  return () => { try { view.cancelAnimationFrame(frame) } catch { /* frame already gone */ } }
 }, [target, doc])
 if (!box || !target) return null
 const win = doc.defaultView
 const start = (mode, e) => {
  e.preventDefault(); e.stopPropagation()
  const cs = win.getComputedStyle(target)
  const rect = target.getBoundingClientRect()
  drag.current = {mode, x: e.clientX, y: e.clientY, w: rect.width, h: rect.height, position: cs.position,
   left: parseFloat(cs.left) || 0, top: parseFloat(cs.top) || 0, inline: {width: target.style.width, height: target.style.height, left: target.style.left, top: target.style.top, position: target.style.position}}
  const move = ev => {
   const d = drag.current
   if (!d) return
   const dx = ev.clientX - d.x, dy = ev.clientY - d.y
   if (d.mode === 'move') {
    if (d.position === 'static') target.style.setProperty('position', 'relative', 'important')
    target.style.setProperty('left', `${Math.round(d.left + dx)}px`, 'important')
    target.style.setProperty('top', `${Math.round(d.top + dy)}px`, 'important')
   } else {
    if (d.mode.includes('e')) target.style.setProperty('width', `${Math.max(8, Math.round(d.w + dx))}px`, 'important')
    if (d.mode.includes('w')) target.style.setProperty('width', `${Math.max(8, Math.round(d.w - dx))}px`, 'important')
    if (d.mode.includes('s')) target.style.setProperty('height', `${Math.max(8, Math.round(d.h + dy))}px`, 'important')
    if (d.mode.includes('n')) target.style.setProperty('height', `${Math.max(8, Math.round(d.h - dy))}px`, 'important')
   }
   d.moved = true
  }
  const up = ev => {
   win.removeEventListener('pointermove', move, true)
   win.removeEventListener('pointerup', up, true)
   const d = drag.current
   drag.current = null
   if (!d?.moved) return
   const values = {}
   const dx = ev.clientX - d.x, dy = ev.clientY - d.y
   if (d.mode === 'move') Object.assign(values, {position: d.position === 'static' ? 'relative' : d.position, left: `${Math.round(d.left + dx)}px`, top: `${Math.round(d.top + dy)}px`})
   else {
    if (/[ew]/.test(d.mode)) values.width = `${Math.max(8, Math.round(d.w + (d.mode.includes('e') ? dx : -dx)))}px`
    if (/[ns]/.test(d.mode)) values.height = `${Math.max(8, Math.round(d.h + (d.mode.includes('s') ? dy : -dy)))}px`
    if (d.mode.includes('w') && d.position !== 'static') values.left = `${Math.round(d.left + dx)}px`
    if (d.mode.includes('n') && d.position !== 'static') values.top = `${Math.round(d.top + dy)}px`
   }
   for (const [k, v] of Object.entries(d.inline)) target.style[k] = v
   ctx.onStyle?.(id, bpForWidth(width), values, d.mode === 'move' ? 'Moved' : 'Resized')
  }
  win.addEventListener('pointermove', move, true)
  win.addEventListener('pointerup', up, true)
 }
 // Drag into any container: deepest container under the pointer (not the element itself), insertion
 // index from the children's positions along the container's flow axis; one validated move on drop.
 const reparent = e => {
  e.preventDefault(); e.stopPropagation()
  const line = doc.createElement('div')
  Object.assign(line.style, {position: 'fixed', zIndex: 2147483001, background: '#2563eb', pointerEvents: 'none', borderRadius: '2px', display: 'none'})
  line.setAttribute('data-studio-drop-line', '')
  const label = doc.createElement('div')
  Object.assign(label.style, {position: 'fixed', zIndex: 2147483002, background: '#2563eb', color: '#fff', font: '600 11px/18px system-ui', padding: '0 6px', borderRadius: '4px', pointerEvents: 'none', display: 'none'})
  doc.body.append(line, label)
  target.style.opacity = '0.45'
  let drop = null
  const find = ev => {
   const stack = doc.elementsFromPoint(ev.clientX, ev.clientY)
   const container = stack.find(el => el.hasAttribute?.('data-studio-container') && el !== target && !target.contains(el) && !el.closest('[data-studio-handles]'))
   if (!container) return null
   const flex = win.getComputedStyle(container)
   const horizontal = (flex.display.includes('flex') && !flex.flexDirection.startsWith('column')) || flex.display.includes('grid')
   const kids = [...container.children].filter(k => k.hasAttribute('data-sid') && k !== target)
   let index = kids.length, before = null
   for (let i = 0; i < kids.length; i++) {
    const r = kids[i].getBoundingClientRect()
    if (!r.width && !r.height) continue
    const past = horizontal && r.height < container.getBoundingClientRect().height * 0.9 ? (ev.clientY < r.top ? true : ev.clientY > r.bottom ? false : ev.clientX < r.left + r.width / 2) : ev.clientY < r.top + r.height / 2
    if (past) { index = i; before = kids[i]; break }
   }
   // Index among the model children: count kids (including the dragged one if it is a sibling) before `before`.
   const all = [...container.querySelectorAll(':scope > [data-sid]')]
   const modelIndex = before ? all.indexOf(before) : all.length
   return {container, parent: container.getAttribute('data-sid'), index: modelIndex < 0 ? index : modelIndex, before, horizontal}
  }
  const move = ev => {
   drop = find(ev)
   if (!drop) { line.style.display = 'none'; label.style.display = 'none'; return }
   const cr = drop.container.getBoundingClientRect()
   const r = drop.before ? drop.before.getBoundingClientRect() : null
   if (drop.horizontal && r) Object.assign(line.style, {display: 'block', left: `${r.left - 2}px`, top: `${r.top}px`, width: '3px', height: `${r.height}px`})
   else Object.assign(line.style, {display: 'block', left: `${cr.left}px`, top: `${r ? r.top - 2 : cr.bottom - 2}px`, width: `${cr.width}px`, height: '3px'})
   Object.assign(label.style, {display: 'block', left: `${cr.left}px`, top: `${Math.max(0, cr.top - 18)}px`})
   label.textContent = `into ${drop.container.tagName.toLowerCase()} ${drop.parent}`
  }
  const up = () => {
   win.removeEventListener('pointermove', move, true)
   win.removeEventListener('pointerup', up, true)
   line.remove(); label.remove(); target.style.opacity = ''
   if (drop) ctx.onReparent?.(id, drop.parent, drop.index)
  }
  win.addEventListener('pointermove', move, true)
  win.addEventListener('pointerup', up, true)
 }
 const H = ({m, style}) => <div data-studio-handle={m} onPointerDown={e => start(m, e)} style={{position: 'absolute', width: 10, height: 10, background: '#fff', border: '2px solid #2563eb', borderRadius: 2, boxSizing: 'border-box', pointerEvents: 'auto', cursor: `${m}-resize`, ...style}}/>
 return createPortal(<div data-studio-handles={id} style={{position: 'fixed', left: box.left, top: box.top, width: box.width, height: box.height, pointerEvents: 'none', zIndex: 2147483000, outline: '1px dashed rgba(37,99,235,.7)'}}>
  <div style={{position: 'absolute', left: -2, top: -24, display: 'flex', gap: 4, pointerEvents: 'none'}}>
   {!isText && <div data-studio-handle="move" title="Drag to move freely (left/top at this breakpoint)" onPointerDown={e => start('move', e)} style={{height: 20, padding: '0 6px', background: '#2563eb', color: '#fff', font: '600 11px/20px system-ui', borderRadius: 4, pointerEvents: 'auto', cursor: 'move', whiteSpace: 'nowrap'}}>✥ move · {BREAKPOINTS.find(b => b.id === bpForWidth(width)).label}</div>}
   <div data-studio-handle="reparent" title="Drag into another container (or another position in this one)" onPointerDown={reparent} style={{height: 20, padding: '0 6px', background: '#0f766e', color: '#fff', font: '600 11px/20px system-ui', borderRadius: 4, pointerEvents: 'auto', cursor: 'grab', whiteSpace: 'nowrap'}}>⇄ drag into container</div>
  </div>
  {!isText && <><H m="e" style={{right: -5, top: 'calc(50% - 5px)'}}/><H m="w" style={{left: -5, top: 'calc(50% - 5px)'}}/>
  <H m="s" style={{bottom: -5, left: 'calc(50% - 5px)'}}/><H m="n" style={{top: -5, left: 'calc(50% - 5px)'}}/>
  <H m="se" style={{right: -5, bottom: -5}}/><H m="sw" style={{left: -5, bottom: -5}}/><H m="ne" style={{right: -5, top: -5}}/><H m="nw" style={{left: -5, top: -5}}/></>}
 </div>, doc.body)
}
