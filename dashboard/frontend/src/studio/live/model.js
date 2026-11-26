// TTROS Website Studio — live-source element model (schema ttros.studio.live/1).
// One model for the canvas, the validators and the Next.js code generator. A document is the JSX a
// source module renders (a page, Header, Footer, HeroDiagram, a blog post body); its tree is a list
// of nodes. Node types:
//   El   an element (HTML/SVG tag, or next/link `Link`, next/image `Image`), children in `kids`
//   Txt  a text run
//   Code an opaque expression (`c` = its source reference) from the source ({children}, data-driven lists) — referenced, never authored
//   Cond a source condition ({open && …}, a ? b : c) whose branches are editable
//   Use  a functional component (FaqAccordion, Script, JsonLd) or a shared document (Header, Footer)
//   Map  one editable template repeated for every entry of a data/prop list (`c` = the source list);
//        code bound to an entry (scope = the Map id) may only move within that template
//   Rec  one record of a data module (content/blog.ts): `key` (its slug) and string fields `f`
//   Comp a React component file (components/…, the site's or one written in Code mode) placed visually;
//        it renders in the built site, the canvas shows a labelled placeholder
// Drafts may only carry literal values. Every piece of source code is referenced by id (`x`, `ref`)
// into the import snapshot, which is built server-side from the pinned Git commit, so a draft can
// move, duplicate, restyle or delete functional code but can never author it.
// Revisit: when node types, style properties or breakpoints change. Last touched: 2026-11-22 (gradients, text/backdrop/transition styles).

export const SCHEMA = 'ttros.studio.live/1'
export const BREAKPOINTS = [
 {id: 'd', label: 'Desktop', width: 1280, media: null},
 {id: 't', label: 'Tablet', width: 820, media: '(max-width: 1023.98px)'},
 {id: 'm', label: 'Mobile', width: 390, media: '(max-width: 767.98px)'},
]
export const NODE_TYPES = ['El', 'Txt', 'Code', 'Cond', 'Use', 'Map', 'Rec', 'Comp']
export const COMP_FILE = /^components\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\.(tsx|jsx)$/
export const ID = /^[a-z0-9-]{1,48}_[a-z0-9]{1,12}$/
export const DOC_KEY = /^[a-z0-9-]{1,48}$/

const SVG_TAGS = ['svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon', 'text', 'tspan', 'defs',
 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'mask', 'pattern', 'title', 'desc', 'use', 'symbol', 'marker', 'foreignObject', 'filter',
 'feGaussianBlur', 'feOffset', 'feBlend', 'feColorMatrix', 'feMerge', 'feMergeNode', 'feFlood', 'feComposite', 'feDropShadow']
const HTML_TAGS = ['div', 'section', 'header', 'footer', 'nav', 'main', 'article', 'aside', 'address', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
 'p', 'span', 'a', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'img', 'picture', 'source', 'figure', 'figcaption', 'video', 'audio', 'track',
 'button', 'strong', 'em', 'b', 'i', 'u', 's', 'small', 'sub', 'sup', 'mark', 'abbr', 'cite', 'code', 'pre', 'kbd', 'q', 'time', 'br', 'hr', 'wbr',
 'blockquote', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'details', 'summary',
 'label', 'form', 'fieldset', 'legend', 'input', 'textarea', 'select', 'option', 'iframe', 'noscript', 'del', 'ins']
export const COMPONENT_TAGS = ['Link', 'Image']
export const TAGS = new Set([...HTML_TAGS, ...SVG_TAGS, ...COMPONENT_TAGS])
export const SVG = new Set(SVG_TAGS.filter(t => t !== 'svg').concat(['svg']))
export const VOID = new Set(['img', 'br', 'hr', 'wbr', 'input', 'source', 'track', 'col', 'Image'])
// Tags the owner can insert or retag to. Frames only embed approved media hosts (validated `src`).
export const AUTHORED_TAGS = new Set(HTML_TAGS.filter(t => !['form', 'input', 'textarea', 'select', 'option', 'noscript', 'fieldset', 'legend', 'label', 'track'].includes(t)))
export const EMBED_HOSTS = ['www.youtube-nocookie.com', 'www.youtube.com', 'player.vimeo.com', 'tally.so', 'www.loom.com']

// Literal attributes the owner may set. Events, refs, inline style and raw HTML only ever come from source code refs.
const ATTRS = ['id', 'href', 'src', 'alt', 'title', 'width', 'height', 'target', 'rel', 'role', 'loading', 'decoding', 'type', 'name', 'value',
 'placeholder', 'tabIndex', 'lang', 'dir', 'hidden', 'download', 'srcSet', 'sizes', 'poster', 'preload', 'controls', 'autoPlay', 'muted', 'loop', 'playsInline',
 'allow', 'allowFullScreen', 'frameBorder', 'scrolling', 'colSpan', 'rowSpan', 'scope', 'headers', 'dateTime', 'cite', 'open', 'start', 'reversed',
 'htmlFor', 'priority', 'unoptimized', 'quality', 'fill', 'prefetch', 'scroll', 'replace', 'disabled', 'required', 'readOnly', 'checked', 'selected', 'multiple',
 'maxLength', 'minLength', 'pattern', 'autoComplete', 'inputMode', 'action', 'method', 'encType', 'noValidate', 'referrerPolicy', 'crossOrigin',
 'itemProp', 'itemScope', 'itemType', 'key', 'suppressHydrationWarning',
 // SVG presentation
 'viewBox', 'xmlns', 'd', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'points', 'stroke', 'strokeWidth', 'strokeLinecap',
 'strokeLinejoin', 'strokeDasharray', 'strokeDashoffset', 'strokeOpacity', 'fillOpacity', 'fillRule', 'clipRule', 'opacity', 'transform',
 'preserveAspectRatio', 'gradientUnits', 'gradientTransform', 'offset', 'stopColor', 'stopOpacity', 'textAnchor', 'dominantBaseline',
 'fontSize', 'fontWeight', 'fontFamily', 'letterSpacing', 'dx', 'dy', 'pathLength', 'markerEnd', 'markerStart', 'clipPath', 'mask',
 'filter', 'focusable', 'vectorEffect', 'paintOrder', 'stdDeviation', 'in', 'in2', 'result', 'mode', 'values', 'floodColor', 'floodOpacity', 'operator',
 'xlinkHref', 'shapeRendering', 'textRendering', 'imageRendering', 'patternUnits', 'maskUnits', 'clipPathUnits', 'refX', 'refY', 'markerWidth', 'markerHeight', 'orient']
export const ATTR_NAMES = new Set(ATTRS)
export const attrAllowed = name => ATTR_NAMES.has(name) || /^aria-[a-z]{2,30}$/.test(name) || /^data-(?!sid$|studio)[a-z][a-z0-9-]{0,40}$/.test(name)
const URL_ATTRS = new Set(['href', 'src', 'poster', 'action', 'cite', 'xlinkHref'])
// `alt` is only valid (and type-checks in the site) on these tags.
export const ALT_TAGS = new Set(['img', 'Image', 'area', 'input'])

// Studio style properties (per breakpoint). Written as generated classes, never inline styles.
export const STYLE_PROPS = {
 layout: ['display', 'position', 'top', 'right', 'bottom', 'left', 'zIndex', 'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
  'margin', 'marginTop', 'marginRight', 'marginBottom', 'marginLeft', 'padding', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'boxSizing', 'overflow', 'float', 'aspectRatio', 'objectFit', 'objectPosition', 'transform', 'order', 'visibility'],
 flex: ['flexDirection', 'flexWrap', 'justifyContent', 'alignItems', 'alignContent', 'alignSelf', 'justifySelf', 'gap', 'rowGap', 'columnGap',
  'flex', 'flexGrow', 'flexShrink', 'flexBasis', 'gridTemplateColumns', 'gridTemplateRows', 'gridColumn', 'gridRow', 'gridAutoFlow', 'placeItems'],
 type: ['color', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight', 'letterSpacing', 'textAlign', 'textTransform', 'textDecoration',
  'whiteSpace', 'wordBreak', 'textWrap', 'textShadow', 'textUnderlineOffset', 'fontVariant'],
 look: ['backgroundColor', 'backgroundImage', 'backgroundSize', 'backgroundPosition', 'backgroundRepeat', 'border', 'borderTop', 'borderRight',
  'borderBottom', 'borderLeft', 'borderColor', 'borderWidth', 'borderStyle', 'borderRadius', 'boxShadow', 'opacity', 'outline', 'cursor', 'filter', 'mixBlendMode', 'backdropFilter', 'transition'],
}
export const STYLE_NAMES = new Set(Object.values(STYLE_PROPS).flat())
const STYLE_VALUE = /^[#a-zA-Z0-9 .,%()\-+/*"'!:]{0,240}$/
// Studio-managed images in backgrounds: asset:<sha256> only, resolved by the canvas and the build.
const BG_ASSET = /^asset:[a-f0-9]{64}$/
// Gradients (no url()): linear/radial/conic, the characters STYLE_VALUE already allows.
export const BG_GRADIENT = /^(repeating-)?(linear|radial|conic)-gradient\([#a-zA-Z0-9 .,%()\-+/]{1,400}\)$/
export function styleProblem(name, value) {
 if (!STYLE_NAMES.has(name)) return `Unknown style property ${name}.`
 if (typeof value !== 'string' && typeof value !== 'number') return `Style ${name} must be text.`
 const text = String(value)
 if (name === 'backgroundImage') return text === 'none' || BG_ASSET.test(text) || (BG_GRADIENT.test(text) && !/url\s*\(/i.test(text)) ? null : 'Background images come from the asset library (or a CSS gradient).'
 if (!STYLE_VALUE.test(text) || /url\s*\(|expression\s*\(|@import|\\|javascript:/i.test(text) || /;|[{}<>]/.test(text)) return `Style ${name} has an unsupported value.`
 return null
}

export function urlProblem(name, value, tag) {
 if (typeof value !== 'string') return null
 const v = value.trim()
 if (!v) return null
 if (/^(javascript|vbscript|data|file):/i.test(v.replace(/[\s\u0000-\u001f]/g, ''))) {
  if (name === 'src' && /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]{1,400000}$/.test(v)) return null
  return `Unsafe ${name}.`
 }
 if (tag === 'iframe' && name === 'src') {
  let host = ''
  try { host = new URL(v).host } catch { return 'Frames need an https:// embed address.' }
  return v.startsWith('https://') && EMBED_HOSTS.includes(host) ? null : `Frames may only embed ${EMBED_HOSTS.join(', ')}.`
 }
 if (/^(https?:|mailto:|tel:|#|\/(?!\/)|\.\/|\?)/i.test(v) || /^asset:[a-f0-9]{64}$/.test(v)) return null
 if (/^[a-z0-9][a-z0-9._~%/-]*$/i.test(v)) return null // relative path
 return `Unsupported ${name} address.`
}

export const docKeyOf = id => id.split('_')[0]
export function newId(docKey, taken) {
 for (;;) {
  const id = `${docKey}_n${Math.random().toString(36).slice(2, 9)}`
  if (!taken || !taken.has(id)) return id
 }
}

// ---- tree helpers (Puck data: {root:{props}, content:[items]}; items {type, props:{id, …, kids:[…]}}) ----
export const SLOTS = ['kids', 'alt']
export function* walk(list, parent = null, slot = 'content') {
 for (let index = 0; index < (list || []).length; index++) {
  const item = list[index]
  yield {item, parent, slot, index, list}
  for (const s of SLOTS) if (Array.isArray(item.props?.[s])) yield* walk(item.props[s], item, s)
 }
}
export function findNode(list, id) {
 for (const entry of walk(list)) if (entry.item.props.id === id) return entry
 return null
}
export function allIds(list) { const s = new Set(); for (const {item} of walk(list)) s.add(item.props.id); return s }
const clone = v => (typeof structuredClone === 'function' ? structuredClone(v) : JSON.parse(JSON.stringify(v)))

// Duplicate a subtree: new ids in the owning document; source code references are kept (same scope).
export function duplicateSubtree(item, docKey, taken) {
 const copy = clone(item)
 const map = {}
 for (const {item: node} of walk([copy])) {
  const old = node.props.id
  const fresh = newId(docKeyOf(old) || docKey, taken)
  taken.add(fresh)
  map[old] = fresh
  node.props.id = fresh
  if (node.props.from === undefined && !old.includes('_n')) node.props.from = old
 }
 return {copy, map}
}

// Remove / insert / move — each returns new content (immutable) for one undoable history step.
export function removeNode(content, id) {
 const next = clone(content)
 const hit = findNode(next, id)
 if (!hit) throw Error('Element not found.')
 hit.list.splice(hit.index, 1)
 return next
}
export function insertNode(content, node, parentId, slot = 'kids', index = Infinity) {
 const next = clone(content)
 let list = next
 if (parentId) {
  const hit = findNode(next, parentId)
  if (!hit) throw Error('Target container not found.')
  if (!Array.isArray(hit.item.props[slot])) throw Error('That element cannot hold children.')
  list = hit.item.props[slot]
 }
 list.splice(Math.min(index, list.length), 0, node)
 return next
}
export function moveNode(content, id, parentId, slot = 'kids', index = Infinity) {
 const hit = findNode(content, id)
 if (!hit) throw Error('Element not found.')
 if (parentId && (parentId === id || findNode([hit.item], parentId))) throw Error('An element cannot move inside itself.')
 let without = removeNode(content, id)
 const sameList = (hit.parent?.props.id ?? null) === (parentId ?? null) && hit.slot === (parentId ? slot : 'content')
 return insertNode(without, clone(hit.item), parentId, slot, sameList && index > hit.index ? index - 1 : index)
}
export function wrapNode(content, id, wrapper) {
 const hit = findNode(content, id)
 if (!hit) throw Error('Element not found.')
 const next = clone(content)
 const again = findNode(next, id)
 const w = clone(wrapper)
 w.props.kids = [again.item]
 again.list.splice(again.index, 1, w)
 return next
}
export function unwrapNode(content, id) {
 const next = clone(content)
 const hit = findNode(next, id)
 if (!hit || !Array.isArray(hit.item.props.kids)) throw Error('Only containers can be unwrapped.')
 hit.list.splice(hit.index, 1, ...hit.item.props.kids)
 return next
}
export function updateProps(content, id, patch) {
 const next = clone(content)
 const hit = findNode(next, id)
 if (!hit) throw Error('Element not found.')
 for (const [k, v] of Object.entries(patch)) {
  if (v === undefined) delete hit.item.props[k]
  else hit.item.props[k] = v
 }
 return next
}
export function setStyle(content, id, bp, values) {
 const hit = findNode(content, id)
 if (!hit) throw Error('Element not found.')
 const st = clone(hit.item.props.st || {})
 const layer = {...(st[bp] || {})}
 for (const [k, v] of Object.entries(values)) {
  if (v === null || v === '' || v === undefined) delete layer[k]
  else { const p = styleProblem(k, v); if (p) throw Error(p); layer[k] = String(v) }
 }
 if (Object.keys(layer).length) st[bp] = layer; else delete st[bp]
 return updateProps(content, id, {st: Object.keys(st).length ? st : undefined})
}

// ---- styles → CSS (identical for the canvas and the built site) ----
const kebab = k => k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())
export const styleClass = id => 's-' + id
export function nodeCss(id, st, resolveAsset = a => a) {
 const out = []
 for (const bp of BREAKPOINTS) {
  const layer = st?.[bp.id]
  if (!layer || !Object.keys(layer).length) continue
  const body = Object.entries(layer).filter(([k, v]) => !styleProblem(k, v)).map(([k, v]) => {
   const value = k === 'backgroundImage' && String(v).startsWith('asset:') ? `url("${resolveAsset(String(v))}")` : String(v)
   return `${kebab(k)}:${value} !important`
  }).join(';')
  if (!body) continue
  const rule = `.${styleClass(id)}{${body}}`
  out.push(bp.media ? `@media ${bp.media}{${rule}}` : rule)
 }
 return out
}
export function treeCss(content, resolveAsset) {
 const rules = {d: [], t: [], m: []}
 for (const {item} of walk(content)) {
  if (!item.props.st) continue
  for (const bp of BREAKPOINTS) {
   const one = nodeCss(item.props.id, {[bp.id]: item.props.st[bp.id]}, resolveAsset)
   rules[bp.id].push(...one)
  }
 }
 // Desktop first, then tablet, then mobile, so narrower breakpoints win.
 return [...rules.d, ...rules.t, ...rules.m].join('\n')
}

const jsonData = (v, depth = 0) => v === null || ['string', 'number', 'boolean'].includes(typeof v) || (depth < 12 && typeof v === 'object' && (Array.isArray(v) ? v.every(i => jsonData(i, depth + 1)) : Object.getPrototypeOf(v) === Object.prototype && Object.values(v).every(i => jsonData(i, depth + 1))))
// ---- validation (client twin of website_studio_live.validate_tree; the server is authoritative) ----
export function validateTree(content, {codes, doc, maxNodes = 20000} = {}) {
 if (!Array.isArray(content)) throw Error('A document is a list of elements.')
 const seen = new Set()
 let count = 0
 const maps = [] // enclosing templates
 const check = (item, depth, parentTag) => {
  if (++count > maxNodes) throw Error('Too many elements in one document.')
  if (depth > 80) throw Error('Elements are nested too deeply.')
  if (!item || typeof item !== 'object' || !NODE_TYPES.includes(item.type) || !item.props || typeof item.props !== 'object') throw Error('Invalid element.')
  const p = item.props
  if (typeof p.id !== 'string' || !ID.test(p.id) || seen.has(p.id)) throw Error(`Invalid or duplicate element id ${p.id}.`)
  seen.add(p.id)
  const ref = r => {
   if (!codes) return
   const c = codes[r]
   if (!c) throw Error(`Unknown source reference ${r}.`)
   if (doc && c.doc !== doc) throw Error(`This element uses code from ${c.doc} and can only stay inside that file.`)
   if (c.scope && !maps.includes(c.scope)) throw Error('This element is bound to a repeated item and can only move inside its template.')
  }
  if (p.x) { if (typeof p.x !== 'object' || Array.isArray(p.x)) throw Error('Invalid code attributes.'); for (const [k, r] of Object.entries(p.x)) { if (typeof r !== 'string') throw Error('Invalid code reference.'); ref(r); if (p.a && Object.hasOwn(p.a, k)) throw Error(`Attribute ${k} is both literal and code.`) } }
  if (p.a) {
   if (typeof p.a !== 'object' || Array.isArray(p.a)) throw Error('Invalid attributes.')
   for (const [k, v] of Object.entries(p.a)) {
    if (!attrAllowed(k)) throw Error(`Attribute ${k} is not allowed.`)
    if (k === 'alt' && item.type === 'El' && !ALT_TAGS.has(p.tag)) throw Error(`Alternative text belongs to images, not <${p.tag}> (use title or aria-label).`)
    if (!['string', 'number', 'boolean'].includes(typeof v) || String(v).length > 4000) throw Error(`Attribute ${k} has an unsupported value.`)
    if (URL_ATTRS.has(k) || k === 'srcSet') { const u = urlProblem(k === 'srcSet' ? 'src' : k, k === 'srcSet' ? String(v).split(',')[0].trim().split(' ')[0] : v, p.tag); if (u) throw Error(u) }
   }
  }
  if (p.st !== undefined) {
   if (!p.st || typeof p.st !== 'object' || Array.isArray(p.st)) throw Error('Invalid styles.')
   for (const [bp, layer] of Object.entries(p.st)) {
    if (!BREAKPOINTS.some(b => b.id === bp) || !layer || typeof layer !== 'object') throw Error('Invalid breakpoint.')
    for (const [k, v] of Object.entries(layer)) { const prob = styleProblem(k, v); if (prob) throw Error(prob) }
   }
  }
  if (p.cls !== undefined && (typeof p.cls !== 'string' || p.cls.length > 2000 || /[<>"'`{}\\]/.test(p.cls))) throw Error('Invalid class list.')
  if (item.type === 'El') {
   if (!TAGS.has(p.tag)) throw Error(`Tag ${p.tag} is not allowed.`)
   if (p.tag === 'iframe' && !p.x?.src) { const u = urlProblem('src', p.a?.src || '', 'iframe'); if (u || !p.a?.src) throw Error(u || 'A frame needs an embed address.') }
   if (VOID.has(p.tag) ? p.kids !== undefined : !Array.isArray(p.kids)) throw Error(`${p.tag} ${VOID.has(p.tag) ? 'cannot hold' : 'needs a'} children list.`)
   if (SVG.has(p.tag) && p.tag !== 'svg' && parentTag && !SVG.has(parentTag)) throw Error('SVG shapes must stay inside a graphic.')
  } else if (item.type === 'Txt') {
   if (typeof p.text !== 'string' || p.text.length > 40000) throw Error('Invalid text.')
  } else if (item.type === 'Code') {
   if (typeof p.c !== 'string') throw Error('Invalid code element.'); ref(p.c)
  } else if (item.type === 'Cond') {
   if (typeof p.c !== 'string' || !Array.isArray(p.kids) || !Array.isArray(p.alt)) throw Error('Invalid condition.'); ref(p.c)
   if (p.show !== undefined && !['kids', 'alt', 'none'].includes(p.show)) throw Error('Invalid condition branch.')
  } else if (item.type === 'Use') {
   if (typeof p.name !== 'string' || !/^[A-Z][A-Za-z0-9]{0,60}$/.test(p.name)) throw Error('Invalid component.')
   if (p.use !== undefined) ref(p.use)
   if (p.kids !== undefined && !Array.isArray(p.kids)) throw Error('Invalid component children.')
   if (p.d !== undefined) { if (!p.d || typeof p.d !== 'object' || Array.isArray(p.d) || JSON.stringify(p.d).length > 200000 || !jsonData(p.d)) throw Error('Component data must be plain text, numbers and lists.'); if (!p.x) throw Error('Only existing component inputs can be edited.'); for (const k of Object.keys(p.d)) if (!p.x[k]) throw Error(`Component input ${k} does not exist.`) }
   if (p.tpl !== undefined && (typeof p.tpl !== 'string' || p.kids !== undefined)) throw Error('A component template is saved in its own document.')
  } else if (item.type === 'Map') {
   if (typeof p.c !== 'string' || !Array.isArray(p.kids)) throw Error('Invalid repeated template.'); ref(p.c)
  } else if (item.type === 'Comp') {
   if (typeof p.name !== 'string' || !/^[A-Z][A-Za-z0-9]{0,60}$/.test(p.name)) throw Error('A component is named in PascalCase (for example PricingCalculator).')
   if (typeof p.file !== 'string' || !COMP_FILE.test(p.file) || p.file === 'components/StudioSiteBehavior.tsx') throw Error('A placed component is a file under components/ (.tsx or .jsx).')
  } else if (item.type === 'Rec') {
   if (typeof p.key !== 'string' || !p.f || typeof p.f !== 'object' || Array.isArray(p.f)) throw Error('Invalid content record.')
   for (const [k, v] of Object.entries(p.f)) if (!/^[A-Za-z][A-Za-z0-9]{0,40}$/.test(k) || typeof v !== 'string' || v.length > 4000) throw Error(`Invalid content field ${k}.`)
  }
  if (item.type === 'Map') maps.push(p.id)
  for (const s of SLOTS) if (Array.isArray(p[s])) for (const child of p[s]) check(child, depth + 1, item.type === 'El' ? p.tag : parentTag)
  if (item.type === 'Map') maps.pop()
  const allowed = {El: ['id', 'tag', 'a', 'x', 'cls', 'st', 'kids', 'from', 'label'], Txt: ['id', 'text', 'x', 'from'], Code: ['id', 'c', 'st', 'from'],
   Cond: ['id', 'c', 'kids', 'alt', 'show', 'from'], Use: ['id', 'name', 'use', 'a', 'd', 'x', 'st', 'kids', 'doc', 'tpl', 'from', 'label'],
   Map: ['id', 'c', 'kids', 'from', 'label'], Rec: ['id', 'key', 'f'], Comp: ['id', 'name', 'file', 'st', 'from', 'label']}[item.type]
  for (const k of Object.keys(p)) if (!allowed.includes(k)) throw Error(`Unknown field ${k} on ${item.type}.`)
 }
 for (const item of content) check(item, 0, null)
 return content
}

// ---- palette: owner-insertable building blocks (El subtrees with literal values only) ----
const el = (tag, props = {}, kids) => ({type: 'El', props: {tag, ...props, ...(VOID.has(tag) ? {} : {kids: kids || []})}})
const txt = text => ({type: 'Txt', props: {text}})
export const PALETTE = [
 {id: 'heading', group: 'Text', label: 'Heading', make: () => el('h2', {cls: 'font-heading text-3xl font-semibold text-exec-graphite'}, [txt('New heading')])},
 {id: 'paragraph', group: 'Text', label: 'Paragraph', make: () => el('p', {cls: 'font-body text-base text-soft-graphite leading-relaxed'}, [txt('Write something here.')])},
 {id: 'text', group: 'Text', label: 'Inline text', make: () => txt('Text')},
 {id: 'list', group: 'Text', label: 'List', make: () => el('ul', {cls: 'list-disc pl-6 space-y-2'}, [el('li', {}, [txt('First item')]), el('li', {}, [txt('Second item')])])},
 {id: 'quote', group: 'Text', label: 'Quote', make: () => el('blockquote', {cls: 'border-l-2 border-champagne pl-5 italic'}, [txt('A short quote.')])},
 {id: 'button', group: 'Actions', label: 'Button link', make: () => el('a', {a: {href: '/free-assessment'}, cls: 'inline-flex items-center justify-center rounded-full bg-exec-graphite px-6 py-3 font-heading text-sm font-medium text-ivory'}, [txt('Book a call')])},
 {id: 'link', group: 'Actions', label: 'Text link', make: () => el('a', {a: {href: '/'}, cls: 'underline underline-offset-4'}, [txt('Link text')])},
 {id: 'image', group: 'Media', label: 'Image', make: () => el('img', {a: {src: '/images/site-logo-dark.svg', alt: 'Describe the image', loading: 'lazy'}, st: {d: {width: '320px', maxWidth: '100%', height: 'auto'}}})},
 {id: 'video', group: 'Media', label: 'Video embed', make: () => el('div', {st: {d: {position: 'relative', aspectRatio: '16 / 9', width: '100%'}}}, [el('iframe', {a: {src: 'https://www.youtube-nocookie.com/embed/', title: 'Video', allowFullScreen: true}, st: {d: {position: 'absolute', top: '0', left: '0', width: '100%', height: '100%', border: '0'}}})])},
 {id: 'icon', group: 'Media', label: 'Icon', make: () => el('svg', {a: {width: '24', height: '24', viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': 'true'}}, [el('path', {a: {d: 'M5 12h14M13 6l6 6-6 6', stroke: 'currentColor', strokeWidth: '1.8', strokeLinecap: 'round', strokeLinejoin: 'round'}})])},
 {id: 'divider', group: 'Media', label: 'Divider', make: () => el('hr', {cls: 'border-warm-stone/60 my-8'})},
 {id: 'container', group: 'Layout', label: 'Container', make: () => el('div', {st: {d: {padding: '24px', minHeight: '80px'}}})},
 {id: 'section', group: 'Layout', label: 'Section', make: () => el('section', {cls: 'px-4 sm:px-6 md:px-8 py-16'}, [el('div', {cls: 'mx-auto max-w-6xl'}, [el('h2', {cls: 'font-heading text-3xl font-semibold text-exec-graphite'}, [txt('Section heading')])])])},
 {id: 'columns2', group: 'Layout', label: '2 columns', make: () => el('div', {st: {d: {display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '24px'}, m: {gridTemplateColumns: '1fr'}}}, [el('div', {st: {d: {minHeight: '60px'}}}, [txt('Column one')]), el('div', {st: {d: {minHeight: '60px'}}}, [txt('Column two')])])},
 {id: 'columns3', group: 'Layout', label: '3 columns', make: () => el('div', {st: {d: {display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '24px'}, t: {gridTemplateColumns: 'repeat(2, minmax(0, 1fr))'}, m: {gridTemplateColumns: '1fr'}}}, [1, 2, 3].map(n => el('div', {st: {d: {minHeight: '60px'}}}, [txt(`Column ${n}`)])))},
 {id: 'grid', group: 'Layout', label: 'Grid (4)', make: () => el('div', {st: {d: {display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '16px'}, t: {gridTemplateColumns: 'repeat(2, minmax(0, 1fr))'}, m: {gridTemplateColumns: '1fr'}}}, [1, 2, 3, 4].map(n => el('div', {st: {d: {minHeight: '60px', padding: '12px'}}}, [txt(`Item ${n}`)])))},
 {id: 'row', group: 'Layout', label: 'Row (flex)', make: () => el('div', {st: {d: {display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'center'}}}, [el('div', {}, [txt('Left')]), el('div', {}, [txt('Right')])])},
 {id: 'stack', group: 'Layout', label: 'Stack (flex column)', make: () => el('div', {st: {d: {display: 'flex', flexDirection: 'column', gap: '12px'}}}, [el('div', {}, [txt('First')]), el('div', {}, [txt('Second')])])},
 {id: 'card', group: 'Layout', label: 'Card', make: () => el('div', {cls: 'rounded-2xl border border-warm-stone/60 bg-white/60 p-6'}, [el('h3', {cls: 'font-heading text-xl font-semibold text-exec-graphite mb-2'}, [txt('Card title')]), el('p', {cls: 'font-body text-soft-graphite'}, [txt('Card text.')])])},
 {id: 'background', group: 'Layout', label: 'Background block', make: () => el('div', {st: {d: {backgroundColor: '#111315', color: '#F7F3EA', padding: '48px 24px', borderRadius: '16px'}}}, [el('p', {}, [txt('Content on a background')])])},
 {id: 'free', group: 'Layout', label: 'Free-position canvas', make: () => el('div', {st: {d: {position: 'relative', minHeight: '320px', width: '100%'}}}, [el('div', {st: {d: {position: 'absolute', left: '24px', top: '24px', padding: '12px'}}}, [txt('Drag me anywhere')])])},
 {id: 'spacer', group: 'Layout', label: 'Spacer', make: () => el('div', {a: {'aria-hidden': 'true'}, st: {d: {height: '48px'}}})},
 {id: 'details', group: 'Functional', label: 'Expandable (details)', make: () => el('details', {cls: 'rounded-xl border border-warm-stone/60 p-4'}, [el('summary', {cls: 'cursor-pointer font-heading font-medium'}, [txt('Question')]), el('p', {cls: 'mt-3'}, [txt('Answer.')])])},
 {id: 'anchor', group: 'Functional', label: 'Anchor target', make: () => el('div', {a: {id: 'new-anchor'}, cls: 'scroll-mt-24'})},
 {id: 'embed', group: 'Functional', label: 'Form/booking embed (Tally)', make: () => el('iframe', {a: {src: 'https://tally.so/embed/', title: 'Form', loading: 'lazy'}, st: {d: {width: '100%', minHeight: '480px', border: '0'}}})},
 {id: 'video-file', group: 'Media', label: 'Video file', make: () => el('video', {a: {src: '/studio-video.mp4', title: 'Video', controls: true, playsInline: true, preload: 'metadata'}, st: {d: {width: '100%', height: 'auto', display: 'block', borderRadius: '12px'}}}, [])},
 {id: 'gallery', group: 'Media', label: 'Image gallery (3)', make: () => el('div', {st: {d: {display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '16px'}, m: {gridTemplateColumns: '1fr'}}}, [1, 2, 3].map(n => el('img', {a: {src: '/images/site-og.png', alt: `Gallery image ${n}`, loading: 'lazy'}, st: {d: {width: '100%', height: 'auto', aspectRatio: '4 / 3', objectFit: 'cover', borderRadius: '12px'}}})))},
 {id: 'hero', group: 'Sections', label: 'Hero with call to action', make: () => el('section', {cls: 'bg-ivory bg-grid pt-14 pb-20 md:pt-20 md:pb-28'}, [el('div', {cls: 'container-site'}, [el('div', {cls: 'max-w-3xl'}, [el('p', {cls: 'label-text mb-4'}, [txt('Eyebrow')]), el('h1', {cls: 'font-heading font-light text-exec-graphite leading-display tracking-display mb-6', st: {d: {fontSize: 'clamp(36px, 5vw, 60px)'}}}, [txt('A clear, specific promise')]), el('p', {cls: 'font-body text-soft-graphite leading-relaxed mb-8 max-w-2xl'}, [txt('One or two sentences on who this is for and what changes.')]), el('a', {a: {href: '/free-assessment'}, cls: 'btn-primary inline-flex items-center gap-2'}, [txt('Book a free 15-minute fit call')])])])])},
 {id: 'features', group: 'Sections', label: 'Feature grid (3 cards)', make: () => el('section', {cls: 'section-pad bg-mist-grey/50'}, [el('div', {cls: 'container-site'}, [el('h2', {cls: 'font-heading font-light text-exec-graphite leading-heading tracking-heading mb-8', st: {d: {fontSize: 'clamp(26px, 3vw, 40px)'}}}, [txt('What you get')]), el('div', {st: {d: {display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '24px'}, t: {gridTemplateColumns: 'repeat(2, minmax(0, 1fr))'}, m: {gridTemplateColumns: '1fr'}}}, [1, 2, 3].map(n => el('div', {cls: 'border border-warm-stone/60 rounded-xl bg-ivory px-7 py-6'}, [el('h3', {cls: 'font-heading font-medium text-exec-graphite text-xl mb-3'}, [txt(`Benefit ${n}`)]), el('p', {cls: 'font-body text-soft-graphite leading-relaxed'}, [txt('A short, concrete outcome.')])])))])])},
 {id: 'testimonial', group: 'Sections', label: 'Testimonial', make: () => el('figure', {cls: 'border-l-2 border-champagne pl-6 py-2 my-8'}, [el('blockquote', {cls: 'font-display text-exec-graphite leading-relaxed', st: {d: {fontSize: 'clamp(18px, 1.6vw, 22px)'}}}, [txt('“A specific result in the client’s own words.”')]), el('figcaption', {cls: 'font-body text-sm text-slate-taupe mt-3'}, [txt('Name, role, company')])])},
 {id: 'cta-band', group: 'Sections', label: 'Call-to-action band', make: () => el('section', {cls: 'section-pad bg-exec-graphite text-ivory'}, [el('div', {cls: 'container-site'}, [el('div', {cls: 'max-w-2xl'}, [el('h2', {cls: 'font-heading font-light text-ivory leading-heading tracking-heading mb-5', st: {d: {fontSize: 'clamp(26px, 3vw, 40px)'}}}, [txt('Ready to save hours every week?')]), el('a', {a: {href: '/free-assessment'}, cls: 'btn-gold inline-flex items-center gap-2'}, [txt('Book a free 15-minute fit call')])])])])},
]
export function instantiate(template, docKey, taken) {
 const item = clone(template)
 for (const {item: node} of walk([item])) { node.props.id = newId(docKey, taken); taken.add(node.props.id) }
 return item
}
export const labelOf = item => {
 const p = item.props
 if (item.type === 'Txt') return `“${p.text.trim().slice(0, 32)}${p.text.trim().length > 32 ? '…' : ''}”`
 if (item.type === 'Code') return p.label || 'Data-driven content'
 if (item.type === 'Cond') return 'Conditional content'
 if (item.type === 'Map') return p.label || 'Repeated item (template)'
 if (item.type === 'Rec') return `Record ${p.key}`
 if (item.type === 'Use') return p.label || p.name
 if (item.type === 'Comp') return p.label || `<${p.name} />`
 return p.label || p.tag
}
