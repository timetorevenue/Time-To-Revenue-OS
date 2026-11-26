// TTROS Website Studio — the code layer and the shared editing operations (schema ttros.studio.code/1).
// A saved version carries element documents (model.js) plus one optional code layer:
//   css     owner CSS appended to app/globals.css (advanced styling the inspector cannot express)
//   header  site header scroll behaviour per breakpoint (desktop / tablet / mobile)
//   files   whole-file source edits of code-only files (React/Next modules, config, CSS), applied
//           in the build fixture over the pinned commit; visual documents with edits cannot be
//           overridden (and an overridden document is code-only until its edit is removed)
//   pages   owner-created landing pages and blog posts (pages.js): settings per new source document;
//           their element trees travel with the version's documents
// Operations are the one vocabulary for manual inspector edits and Website Manager proposals, so
// the same request through either produces the same documents and the same generated source. The
// server (website_studio_code.py) is the authority and re-validates everything.
// Revisit: when ops, header modes, editable file rules or the CSS policy change. Last touched: 2026-11-22.
import {findNode, styleProblem, urlProblem} from './model.js'
import {pagesProblem, normalizePages, applyPageOp} from './pages.js'

export const CODE_SCHEMA = 'ttros.studio.code/1'
// The site's brand tokens (tailwind.config.ts at the pinned live source) for swatches and font pickers.
export const BRAND_COLORS = {'exec-graphite': '#111315', 'deep-ink': '#0D1418', 'soft-graphite': '#2B2F32', ivory: '#F7F3EA', 'warm-stone': '#D8D0C2',
 'mist-grey': '#ECE9E2', champagne: '#B89B63', 'rev-gold': '#C6A15B', 'slate-taupe': '#7A746A', 'olive-graphite': '#4E5A50', 'signal-blue': '#52616B', 'alert-clay': '#A56C53'}
export const FONT_OPTIONS = [
 {label: 'Heading · Geist', value: 'var(--font-geist), "Helvetica Neue", sans-serif'},
 {label: 'Body · Inter', value: 'var(--font-inter), "Helvetica Neue", Arial, sans-serif'},
 {label: 'Display · Inter Display', value: 'var(--font-inter-display), var(--font-inter), sans-serif'},
 {label: 'Serif · Georgia', value: 'Georgia, "Times New Roman", serif'},
 {label: 'System sans', value: '"Helvetica Neue", Arial, sans-serif'},
 {label: 'Monospace', value: 'ui-monospace, SFMono-Regular, Menlo, monospace'},
]
export const HEADER_MODES = ['sticky-transparent', 'sticky-solid', 'auto-hide', 'static']
export const HEADER_MODE_LABELS = {'sticky-transparent': 'Sticky · transparent at top, solid after scrolling', 'sticky-solid': 'Sticky · always solid',
 'auto-hide': 'Auto-hide · hides scrolling down, reappears solid scrolling up', static: 'Static · scrolls away with the page'}
export const HEADER_BPS = [{id: 'desktop', label: 'Desktop', media: '(min-width: 1024px)'}, {id: 'tablet', label: 'Tablet', media: '(min-width: 768px) and (max-width: 1023.98px)'},
 {id: 'mobile', label: 'Mobile', media: '(max-width: 767.98px)'}]
export const HEADER_DEFAULT = {desktop: 'sticky-transparent', tablet: 'auto-hide', mobile: 'auto-hide', threshold: 8}
export const MAX_CSS = 100_000
export const MAX_FILE = 300_000
export const MAX_FILES = 40
export const BEHAVIOR_FILE = 'components/StudioSiteBehavior.tsx'
export const CSS_MARK = '/* ttros-studio: owner CSS */'
const BLOCKED_FILES = /^(app\/api\/|middleware\.|next\.config|package(-lock)?\.json|tsconfig|eslint|postcss|vercel|\.)/
export const EDITABLE_FILE = /^(?:(?:app|components|content|lib|styles)\/[A-Za-z0-9_\-[\].]+(?:\/[A-Za-z0-9_\-[\].]+)*\.(?:tsx|ts|jsx|js|mjs|css)|tailwind\.config\.ts)$/

export function fileProblem(path, text) {
 if (typeof path !== 'string' || !EDITABLE_FILE.test(path) || path.includes('..') || path.includes('//') || BLOCKED_FILES.test(path) || /(^|\/)route\.[jt]sx?$/.test(path) || path === BEHAVIOR_FILE)
  return `${String(path).slice(0, 80)} is not an editable source file (pages, components, content, lib, styles and tailwind.config.ts; never API routes, config or packages).`
 if (text === null) return null
 if (typeof text !== 'string' || text.length > MAX_FILE) return `${path} must be text up to ${MAX_FILE} characters.`
 if (text.includes('\u0000')) return `${path} contains a NUL character.`
 return null
}

// Owner CSS: plain style rules. No imports, scripts or remote fetches; url() only for Studio assets
// (asset:<sha256>) and the site's own files (/path).
export function cssProblem(css) {
 if (typeof css !== 'string') return 'CSS must be text.'
 if (css.length > MAX_CSS) return `CSS is limited to ${MAX_CSS} characters.`
 if (/[<\\\u0000]/.test(css)) return 'CSS may not contain <, backslashes or NUL characters.'
 if (/@import|@charset|expression\s*\(|javascript:|vbscript:|behavior\s*:|-moz-binding/i.test(css)) return 'CSS may not import, script or bind behaviour.'
 for (const m of css.matchAll(/url\s*\(\s*(['"]?)([^'")]*)\1\s*\)/gi)) if (!/^asset:[a-f0-9]{64}$/.test(m[2]) && !/^\/(?!\/)[A-Za-z0-9_\-./]*$/.test(m[2])) return `url(${m[2].slice(0, 60)}) is not allowed; use a Studio asset or a site file path.`
 if ((css.match(/url\s*\(/gi) || []).length !== [...css.matchAll(/url\s*\(\s*(['"]?)([^'")]*)\1\s*\)/gi)].length) return 'Malformed url().'
 const plain = css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/"[^"\n]*"|'[^'\n]*'/g, 'S')
 if (/\/\*|["']/.test(plain)) return 'Unclosed comment or string.'
 let depth = 0
 for (const c of plain) { if (c === '{') depth++; else if (c === '}' && --depth < 0) return 'Unbalanced braces.' }
 return depth ? 'Unbalanced braces.' : null
}
export const cssAssets = css => [...String(css || '').matchAll(/asset:([a-f0-9]{64})/g)].map(m => m[1])

export function headerProblem(h) {
 if (h === null || h === undefined) return null
 if (typeof h !== 'object' || Array.isArray(h)) return 'Invalid header settings.'
 for (const k of Object.keys(h)) if (!['desktop', 'tablet', 'mobile', 'threshold'].includes(k)) return `Unknown header setting ${k}.`
 for (const bp of ['desktop', 'tablet', 'mobile']) if (!HEADER_MODES.includes(h[bp])) return `Header ${bp} mode must be one of ${HEADER_MODES.join(', ')}.`
 if (!Number.isInteger(h.threshold) || h.threshold < 0 || h.threshold > 400) return 'Header threshold must be 0–400 px.'
 return null
}

export function codeProblem(code) {
 if (code === null || code === undefined) return null
 if (typeof code !== 'object' || Array.isArray(code)) return 'Invalid code layer.'
 for (const k of Object.keys(code)) if (!['css', 'header', 'files', 'pages'].includes(k)) return `Unknown code layer field ${k}.`
 if (code.pages !== undefined) { const p = pagesProblem(code.pages); if (p) return p }
 if (code.css !== undefined) { const p = cssProblem(code.css); if (p) return p }
 if (code.header !== undefined) { const p = headerProblem(code.header); if (p) return p }
 if (code.files !== undefined) {
  if (!code.files || typeof code.files !== 'object' || Array.isArray(code.files)) return 'Invalid source files.'
  const entries = Object.entries(code.files)
  if (entries.length > MAX_FILES) return `At most ${MAX_FILES} edited source files.`
  for (const [p, t] of entries) { const prob = fileProblem(p, t); if (prob) return prob; if (t === null) return `${p}: removed files are not supported.`; if (code.pages?.[p]) return `${p} is an owner-created page; edit it visually (or remove the page) instead of overriding its source.` }
 }
 return null
}
// Canonical form: empty parts dropped, so an untouched layer is {}.
export function normalizeCode(code) {
 const out = {}
 if (code?.css && code.css.trim()) out.css = code.css
 if (code?.header) out.header = {desktop: code.header.desktop, tablet: code.header.tablet, mobile: code.header.mobile, threshold: code.header.threshold}
 if (code?.files && Object.keys(code.files).length) out.files = Object.fromEntries(Object.keys(code.files).sort().map(k => [k, code.files[k]]))
 if (code?.pages && Object.keys(code.pages).length) out.pages = normalizePages(code.pages)
 return out
}

// ---- links: one rule for the inspector and the Website Manager ----
// Internal paths must be a page of this site or one of its files; external links must be complete
// https/http addresses with a real host; plus #anchors, mailto: and tel:.
export function linkProblem(href, {routes, files} = {}) {
 const v = typeof href === 'string' ? href.trim() : ''
 if (!v) return 'A link needs a destination.'
 if (v !== href) return 'Remove the spaces around the link.'
 if (/\s/.test(v)) return 'A link cannot contain spaces.'
 const unsafe = urlProblem('href', v, 'a')
 if (unsafe) return unsafe
 if (/^#[A-Za-z][\w-]{0,80}$/.test(v)) return null
 if (/^mailto:[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(\?.*)?$/.test(v)) return null
 if (/^tel:\+?[0-9][0-9 ()-]{3,24}$/.test(v)) return null
 if (/^https?:\/\//i.test(v)) {
  let u
  try { u = new URL(v) } catch { return 'That web address is not valid.' }
  if (u.username || u.password) return 'Links may not carry credentials.'
  if (!/^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i.test(u.hostname)) return `${u.hostname || 'That address'} is not a valid web host.`
  return null
 }
 if (/^\/(?!\/)/.test(v)) {
  const path = v.split(/[?#]/)[0]
  const clean = path.length > 1 ? path.replace(/\/$/, '') : path
  if (routes && !routes.has(clean) && !(files && files.has(path))) return `There is no page or file at ${clean} on this site.`
  return null
 }
 return 'Use a full https:// address, a site path such as /pricing, an #anchor, mailto: or tel:.'
}

// ---- the operations ----
const clone = v => JSON.parse(JSON.stringify(v))
const MEDIA_ATTRS = {alt: 'string', title: 'string', 'aria-label': 'string', loading: ['lazy', 'eager'], controls: 'boolean', autoPlay: 'boolean', muted: 'boolean', loop: 'boolean', playsInline: 'boolean',
 preload: ['none', 'metadata', 'auto'], src: 'string', poster: 'string'}
// Media sources are checked again as URLs by the document validation (assets, site files, https).
const MEDIA_TAGS = ['img', 'Image', 'video', 'audio', 'source', 'iframe']
export const OPS = ['set_link', 'set_style', 'set_text', 'set_attr', 'set_class', 'set_css', 'set_header', 'set_file', 'insert', 'remove', 'create_page', 'create_post', 'set_page', 'remove_page']
export const PAGE_OPS = ['create_page', 'create_post', 'set_page', 'remove_page']
const BPS = ['d', 't', 'm']

// Apply operations to element trees + code layer. `lists` is {name: treeList} (one composed page or
// the per-document trees); ids are unique across them. Throws on the first invalid operation. Page
// operations add or remove created documents in the returned lists (ctx.site: {routes, posts} of the
// imported site, so new routes stay unique).
export function applyOps(lists, code, ops, ctx = {}) {
 const out = Object.fromEntries(Object.entries(lists).map(([k, v]) => [k, clone(v)]))
 let next = clone(code || {})
 if (!Array.isArray(ops) || !ops.length || ops.length > 40) throw Error('A change needs 1 to 40 operations.')
 const node = id => { for (const list of Object.values(out)) { const hit = findNode(list, id); if (hit) return hit.item } throw Error(`Element ${String(id).slice(0, 60)} is not on this page.`) }
 for (const op of ops) {
  if (!op || typeof op !== 'object' || !OPS.includes(op.op)) throw Error(`Unknown operation ${String(op?.op).slice(0, 30)}.`)
  if (PAGE_OPS.includes(op.op)) {
   const pages = clone(next.pages || {})
   applyPageOp(op, out, pages, {routes: new Set([...(ctx.routes || []), ...(ctx.site?.routes || [])]), posts: new Set(ctx.site?.posts || [])})
   if (Object.keys(pages).length) next.pages = pages; else delete next.pages
   continue
  }
  if (op.op === 'insert') {
   const item = op.node
   if (!item || typeof item !== 'object' || !['El', 'Txt', 'Comp'].includes(item.type) || !item.props || typeof item.props !== 'object') throw Error('Insert one element (with its children).')
   let items
   if (op.parent) {
    const target = node(op.parent), slot = op.slot || 'kids'
    if (!['kids', 'alt'].includes(slot) || !Array.isArray(target.props[slot]) || !['El', 'Cond'].includes(target.type)) throw Error('That element cannot hold other elements.')
    items = target.props[slot]
   } else {
    if (!out[op.doc]) throw Error('Name the document to insert into.')
    items = out[op.doc]
   }
   const index = op.index === undefined || op.index === null ? items.length : op.index
   if (!Number.isInteger(index) || index < 0 || index > items.length) throw Error('Invalid insert position.')
   items.splice(index, 0, clone(item))
   continue
  }
  if (op.op === 'remove') {
   let done = false
   for (const list of Object.values(out)) { const hit = findNode(list, op.id); if (hit) { if (['Rec', 'Data'].includes(hit.item.type)) throw Error('Content records cannot be removed.'); hit.list.splice(hit.index, 1); done = true; break } }
   if (!done) throw Error(`Element ${String(op.id).slice(0, 60)} is not on this page.`)
   continue
  }
  if (op.op === 'set_link') {
   const n = node(op.id), p = n.props
   if (n.type !== 'El' || !['a', 'Link'].includes(p.tag)) throw Error('Only links and buttons that are links have a destination.')
   if (p.x?.href) throw Error('This link’s destination is computed by the site’s code; change it in Code mode.')
   const prob = linkProblem(op.href, ctx)
   if (prob) throw Error(prob)
   const a = {...(p.a || {}), href: op.href}
   if (op.newTab === true) { a.target = '_blank'; a.rel = 'noopener noreferrer' } else if (op.newTab === false && a.target === '_blank') { delete a.target; delete a.rel }
   p.a = a
  } else if (op.op === 'set_style') {
   const n = node(op.id), p = n.props
   if (n.type === 'Txt' || n.type === 'Rec') throw Error('Select the element around this text to style it.')
   if (!BPS.includes(op.bp)) throw Error('Unknown breakpoint.')
   if (!op.values || typeof op.values !== 'object' || !Object.keys(op.values).length) throw Error('No style values.')
   const st = clone(p.st || {})
   const layer = {...(st[op.bp] || {})}
   for (const [k, v] of Object.entries(op.values)) {
    if (v === null || v === '') delete layer[k]
    else { const prob = styleProblem(k, v); if (prob) throw Error(prob); layer[k] = String(v) }
   }
   if (Object.keys(layer).length) st[op.bp] = layer; else delete st[op.bp]
   if (Object.keys(st).length) p.st = st; else delete p.st
  } else if (op.op === 'set_text') {
   const n = node(op.id)
   if (n.type !== 'Txt') throw Error('Only text can be rewritten.')
   if (typeof op.text !== 'string' || !op.text.trim() || op.text.length > 40000) throw Error('Text must not be empty.')
   n.props.text = op.text
  } else if (op.op === 'set_attr') {
   const n = node(op.id), p = n.props
   const rule = MEDIA_ATTRS[op.name]
   if (n.type !== 'El' || !rule || (['src', 'poster'].includes(op.name) && !MEDIA_TAGS.includes(p.tag))) throw Error(`Attribute ${String(op.name).slice(0, 30)} cannot be set here.`)
   if (p.x?.[op.name]) throw Error(`${op.name} is computed by the site’s code.`)
   const a = {...(p.a || {})}
   if (op.value === null) delete a[op.name]
   else if (Array.isArray(rule) ? !rule.includes(op.value) : typeof op.value !== rule || (rule === 'string' && op.value.length > 400)) throw Error(`Invalid ${op.name}.`)
   else a[op.name] = op.value
   if (Object.keys(a).length) p.a = a; else delete p.a
  } else if (op.op === 'set_class') {
   const n = node(op.id), p = n.props
   if (n.type !== 'El') throw Error('Only elements have classes.')
   if (p.x?.className) throw Error('This element’s classes are computed by the site’s code.')
   if (typeof op.cls !== 'string' || op.cls.length > 2000 || /[<>"'`{}\\]/.test(op.cls)) throw Error('Invalid class list.')
   if (op.cls.trim()) p.cls = op.cls.trim(); else delete p.cls
  } else if (op.op === 'set_css') {
   const prob = cssProblem(op.css ?? '')
   if (prob) throw Error(prob)
   if ((op.css || '').trim()) next.css = op.css; else delete next.css
  } else if (op.op === 'set_header') {
   const prob = headerProblem(op.header)
   if (prob) throw Error(prob)
   if (op.header) next.header = {...op.header}; else delete next.header
  } else if (op.op === 'set_file') {
   const prob = fileProblem(op.path, op.text)
   if (prob) throw Error(prob)
   if (next.pages?.[op.path]) throw Error(`${op.path} is an owner-created page; edit it visually instead of overriding its source.`)
   const files = {...(next.files || {})}
   if (op.text === null) delete files[op.path]; else files[op.path] = op.text
   if (Object.keys(files).length) next.files = files; else delete next.files
  }
 }
 return {lists: out, code: normalizeCode(next)}
}

// ---- header scroll behaviour: identical CSS for the Studio canvas and the built site ----
const SOLID = 'background-color:rgba(247,243,234,.96)!important;border-bottom-color:rgba(216,208,194,.5)!important;backdrop-filter:blur(12px)!important;-webkit-backdrop-filter:blur(12px)!important;box-shadow:0 1px 0 0 rgba(216,208,194,.4)!important'
const CLEAR = 'background-color:transparent!important;border-bottom-color:transparent!important;backdrop-filter:none!important;-webkit-backdrop-filter:none!important;box-shadow:none!important'
export function headerCss() {
 return [
  '/* ttros-studio: header scroll behaviour */',
  'header[data-studio-mode="static"]{position:relative!important;top:auto!important}',
  'header[data-studio-mode="sticky-transparent"],header[data-studio-mode="sticky-solid"],header[data-studio-mode="auto-hide"]{position:sticky!important;top:0!important}',
  'header[data-studio-mode="auto-hide"]{transition:transform .28s ease,background-color .3s ease,box-shadow .3s ease,border-color .3s ease!important}',
  'header[data-studio-mode="auto-hide"][data-studio-hidden="true"]{transform:translateY(-100%)!important}',
  `header[data-studio-solid="true"]{${SOLID}}`,
  `header[data-studio-solid="false"]{${CLEAR}}`,
  '@media (prefers-reduced-motion:reduce){header[data-studio-mode]{transition:none!important}}',
 ].join('\n')
}
// Which state a header is in (shared by the runtime component and the canvas state preview).
export function headerState(settings, bp, {y, lastY, menuOpen, height}) {
 const mode = settings[bp]
 const scrolled = y > settings.threshold
 let hidden = false
 if (mode === 'auto-hide' && !menuOpen) hidden = y > Math.max(height, settings.threshold) && y > lastY
 const solid = mode === 'sticky-solid' || (mode !== 'static' && scrolled) || !!menuOpen
 return {mode, hidden, solid}
}

// The client component the build writes (components/StudioSiteBehavior.tsx) and mounts in app/layout.tsx.
export function behaviorSource(settings) {
 const s = JSON.stringify({desktop: settings.desktop, tablet: settings.tablet, mobile: settings.mobile, threshold: settings.threshold})
 return `"use client";
// Generated by TTROS Website Studio: site header scroll behaviour (settings below). Do not edit by hand.
import { useEffect } from "react";

type Bp = "desktop" | "tablet" | "mobile";
type Mode = "sticky-transparent" | "sticky-solid" | "auto-hide" | "static";
const SETTINGS: Record<Bp, Mode> & { threshold: number } = ${s};

function breakpoint(): Bp {
  if (window.matchMedia("(max-width: 767.98px)").matches) return "mobile";
  if (window.matchMedia("(max-width: 1023.98px)").matches) return "tablet";
  return "desktop";
}

export default function StudioSiteBehavior() {
  useEffect(() => {
    let lastY = window.scrollY;
    let frame = 0;
    const apply = () => {
      frame = 0;
      const header = document.querySelector<HTMLElement>("header");
      if (!header) return;
      const y = Math.max(0, window.scrollY);
      const bp = breakpoint();
      const mode = SETTINGS[bp];
      const menuOpen = !!header.querySelector('[aria-controls][aria-expanded="true"]');
      const anyOpen = menuOpen || !!header.querySelector('[aria-expanded="true"]');
      const delta = y - lastY;
      let hidden = header.dataset.studioHidden === "true";
      if (mode !== "auto-hide" || anyOpen || y <= Math.max(header.offsetHeight, SETTINGS.threshold)) hidden = false;
      else if (delta > 4) hidden = true;
      else if (delta < -4) hidden = false;
      if (Math.abs(delta) > 4 || y === 0) lastY = y;
      const solid = mode === "sticky-solid" || (mode !== "static" && y > SETTINGS.threshold) || menuOpen;
      header.dataset.studioMode = mode;
      header.dataset.studioHidden = hidden ? "true" : "false";
      header.dataset.studioSolid = solid ? "true" : "false";
      header.dataset.studioBp = bp;
    };
    const schedule = () => { if (!frame) frame = window.requestAnimationFrame(apply); };
    apply();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    document.addEventListener("click", schedule, true);
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-expanded"] });
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      document.removeEventListener("click", schedule, true);
      observer.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return null;
}
`
}

// Build-time application of the code layer over generated source (used by build-live.mjs and the
// proposal/source-diff script). `files` is the source map at the pinned commit; `changed` the
// generator's output. Returns the full set of changed files.
export function applyCodeLayer(code, files, changed, {docFiles = new Set(), assetPath = a => a} = {}) {
 const out = {...changed}
 const prob = codeProblem(code)
 if (prob) throw Error(prob)
 for (const [path, text] of Object.entries(code?.files || {})) {
  if (docFiles.has(path) && changed[path] !== undefined) throw Error(`${path} has visual edits and a source edit; keep one of them.`)
  out[path] = text
 }
 const current = path => out[path] ?? files.get(path)
 if (code?.css?.trim()) {
  const base = current('app/globals.css')
  if (base === undefined) throw Error('The site has no app/globals.css.')
  const css = code.css.replace(/asset:([a-f0-9]{64})/g, m => assetPath(m))
  out['app/globals.css'] = `${base.replace(/\s*$/, '')}\n\n${CSS_MARK}\n${css.trim()}\n`
 }
 if (code?.header) {
  const layout = current('app/layout.tsx')
  if (!layout || layout.split('</body>').length !== 2) throw Error('Website layout drift: </body> not found exactly once in app/layout.tsx.')
  const anchor = 'import "./globals.css";'
  if (layout.split(anchor).length !== 2) throw Error('Website layout drift: globals.css import not found.')
  out[BEHAVIOR_FILE] = behaviorSource(code.header)
  out['app/layout.tsx'] = layout.replace(anchor, `${anchor}\nimport StudioSiteBehavior from "@/components/StudioSiteBehavior";`).replace('</body>', '  <StudioSiteBehavior />\n      </body>')
  const globals = current('app/globals.css')
  if (globals === undefined) throw Error('The site has no app/globals.css.')
  out['app/globals.css'] = `${globals.replace(/\s*$/, '')}\n\n${headerCss()}\n`
 }
 return out
}
