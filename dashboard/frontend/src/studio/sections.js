// Section instances for TTROS Website Studio (Stage 2). Deterministic; no model access.
// A section TYPE is one of the real website components in homepage.json. A section INSTANCE
// has a stable ID that is independent of its type and position: the nine original sections
// keep their saved IDs so history, exports and accepted proposals keep resolving; every added
// or duplicated instance gets a fresh ID. Python twin: dashboard/backend/website_studio_sections.py.
// Stage 3: section types belong to a PAGE (pages.json). The homepage's nine types are unchanged;
// each service page's five types are the real ServicePage sections snapshotted in services.json.
// A page's content may only hold its own types, so styles and changes never cross pages.
// Revisit when homepage.json/services.json is re-snapshotted or section-rules.json changes. Last touched: 2026-11-18.
import {nestedTypes,migrateItem,rekeyChildren,walkItems} from './nested.js'
import snapshot from './homepage.json' with { type: 'json' }
import services from './services.json' with { type: 'json' }
import pages from './pages.json' with { type: 'json' }
import shared from './shared.json' with { type: 'json' }
import rules from './section-rules.json' with { type: 'json' }

export { rules as sectionRules }
export const studioPages={...pages,pages:{...pages.pages,site:pages.shared}}
export const ID_PATTERN = new RegExp(rules.idPattern)
const decode = value => value.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')

// Link destinations and DOM IDs come from the real component markup, in document order.
const withMeta = (section, page, meta = rules.types[section.type]) => {
  if (!meta) throw Error(`Section type ${section.type} has no Studio rules.`)
  return {
    ...section, ...meta, page,
    min: meta.min ?? 0, max: meta.max ?? rules.maxSections,
    links: [...section.html.matchAll(/<a\b[^>]*?\shref="([^"]*)"/g)].map(m => decode(m[1])),
    domIds: [...section.html.matchAll(/\sid="([^"]+)"/g)].map(m => decode(m[1])),
  }
}
export const sectionTypes = [
  ...nestedTypes,
  ...shared.sections.map(s=>withMeta(s,'site',s)),
  ...snapshot.sections.map(s => withMeta(s, 'home')),
  // A service section's rules are those of its permanent index (the type name's suffix), not its position.
  ...Object.entries(services.pages).flatMap(([page, p]) => p.sections.map(s => withMeta(s, page, { ...rules.serviceTypes[Number(s.type.match(/(\d+)$/)[1])], originalId: s.type }))),
]
export const typeOf = type => sectionTypes.find(s => s.type === type)
export const pageIds = Object.keys(pages.pages)
export const pageInfo = page => studioPages.pages[page] ? { id: page, ...studioPages.pages[page] } : null
export const pageTypes = page => sectionTypes.filter(s => s.page === page&&!s.nested)
export const pageByRoute = route => pageIds.find(id => pages.pages[id].route === route) || null
// The page a list of instances belongs to (all instances must share it).
export function pageOfContent(content) {
  const page = typeOf(content?.[0]?.type)?.page
  if (!page) throw Error('Unknown section type.')
  return page
}
// Anchors a page has in its original website form (used to check links into another Studio page).
export const originalAnchors = page => new Set([...rules.pageAnchors, ...pageTypes(page).flatMap(s => s.domIds)])
export const linkKeys = section => section.links.map((_, index) => `link${index}`)

export function newInstance(type, id) {
  const section = typeOf(type)
  if (!section) throw Error('Unknown section type.')
  return migrateItem({ type, props: { id, ...section.texts, ...Object.fromEntries(section.links.map((href, i) => [`link${i}`, href])), styles: {} } })
}

// Fresh instance ID: readable type slug plus a random suffix, never reused within the page.
export function allocateId(type, existing, random = n => crypto.getRandomValues(new Uint8Array(n))) {
  const section = typeOf(type)
  if (!section) throw Error('Unknown section type.')
  const taken = new Set(existing)
  for (let attempt = 0; attempt < 50; attempt++) {
    const suffix = [...random(6)].map(b => 'abcdefghijkmnpqrstuvwxyz23456789'[b % 32]).join('')
    const id = `${section.slug}-${suffix}`
    if (!taken.has(id) && ID_PATTERN.test(id)) return id
  }
  throw Error('Could not allocate a unique section ID.')
}

// The original website instance keeps the component's anchors (e.g. #how-it-works). Added and
// duplicated instances get IDs scoped by their instance ID, so the page never has duplicate IDs
// and ARIA references inside a copy point at the copy.
export const isOriginal = item => item.props.id === typeOf(item.type)?.originalId
export function anchorMap(item) {
  const section = typeOf(item.type)
  return Object.fromEntries(section.domIds.map(id => [id, isOriginal(item) ? id : `${id}-${item.props.id}`]))
}
export function pageAnchors(content) {
  return new Set([...rules.pageAnchors, ...content.flatMap(item => Object.values(anchorMap(item)))])
}

const patterns = Object.fromEntries(['https', 'mailto', 'internal', 'slugPattern'].map(k => [k, new RegExp(rules.links[k])]))
// Returns null when safe, otherwise the reason. Same-page anchors must exist on this draft; an
// anchor on another Studio page must exist on that page's original form. `route` is this page's.
export function linkProblem(href, anchors, route = '/') {
  if (typeof href !== 'string' || !href || href.length > rules.links.maxLength) return 'Links must be 1–500 characters.'
  if (patterns.https.test(href)) {
    try { const url = new URL(href); return url.username || url.password ? 'Links may not contain credentials.' : null } catch { return 'Invalid web address.' }
  }
  if (patterns.mailto.test(href)) return null
  const match = href.match(patterns.internal)
  if (!match || (!match[1] && !match[2]) || match[1]?.includes('//')) return 'Use a site page (/about), an anchor on this page (#how-it-works), https:// or mailto:.'
  const path = match[1] ? (match[1].replace(/\/+$/, '') || '/') : null
  if (path) {
    const prefix = rules.links.routePrefixes.find(p => path.startsWith(p))
    const known = rules.links.routes.includes(path) || (prefix && patterns.slugPattern.test(path.slice(prefix.length)))
    if (!known) return `${path} is not a page on this website.`
  }
  const anchor = match[2]?.slice(1)
  if (anchor && (!path || path === route) && !anchors.has(anchor)) return `#${anchor} is not on this page.`
  const other = anchor && path && path !== route ? pageByRoute(path) : null
  if (other && !originalAnchors(other).has(anchor)) return `#${anchor} is not on ${path}.`
  return null
}

// Insert, duplicate or remove one instance. Pure; the caller validates the resulting page.
const OP_KEYS = { add: ['id', 'op', 'place', 'ref', 'type'], duplicate: ['id', 'op', 'place', 'ref', 'source'], remove: ['id', 'op'] }
export function applySectionOperation(content, op, { allowRemove = true } = {}) {
  if (!op || typeof op !== 'object' || Array.isArray(op)) throw Error('Invalid section operation.')
  const keys = OP_KEYS[op.op]
  if (!keys || Object.keys(op).some(k => !keys.includes(k))) throw Error('Unsupported section operation.')
  if (op.op === 'remove') {
    if (!allowRemove) throw Error('Section removal is a manual action.')
    if (!content.some(i => i.props.id === op.id)) throw Error('Unknown section.')
    return content.filter(i => i.props.id !== op.id)
  }
  if (typeof op.id !== 'string' || !ID_PATTERN.test(op.id) || content.some(i => i.props.id === op.id)) throw Error('A new section needs a new, unique ID.')
  let item
  if (op.op === 'add') item = newInstance(op.type, op.id)
  else {
    const source = content.find(i => i.props.id === op.source)
    if (!source) throw Error('Unknown section to duplicate.')
    item = structuredClone(source)
    item.props.id = op.id
    rekeyChildren(item)
  }
  const place = op.place ?? (op.op === 'duplicate' ? 'after' : 'end')
  const ref = op.ref ?? (op.op === 'duplicate' ? op.source : undefined)
  let index
  if (place === 'end') {
    if (op.ref !== undefined) throw Error('An end insertion has no reference section.')
    index = content.length
  } else if (place === 'before' || place === 'after') {
    const at = content.findIndex(i => i.props.id === ref)
    if (at < 0) throw Error('Unknown insertion point.')
    index = place === 'before' ? at : at + 1
  } else throw Error('Invalid insertion place.')
  return [...content.slice(0, index), item, ...content.slice(index)]
}

// Count rules per type of the page, with the reason shown to the owner.
export function limitProblem(content, page = pageOfContent(content)) {
  for (const section of pageTypes(page)) {
    const count = content.filter(i => i.type === section.type).length
    if (count < section.min) return `${section.label} is required${section.limitReason ? `: ${section.limitReason}` : '.'}`
    if (count > section.max) return `${section.label} allows ${section.max}${section.limitReason ? `: ${section.limitReason}` : '.'}`
  }
  return null
}
export function canAdd(content, type) {
  const section = typeOf(type)
  if (!section) return 'Unknown section type.'
  if (content.length && section.page !== pageOfContent(content)) return `${section.label} belongs to another page.`
  if (content.length >= rules.maxSections) return `The page holds at most ${rules.maxSections} sections.`
  if (content.filter(i => i.type === type).length >= section.max) return section.limitReason || `${section.label} allows ${section.max}.`
  return null
}
export function canRemove(content, id) {
  const item = content.find(i => i.props.id === id)
  if (!item) return 'Select a section first.'
  const section = typeOf(item.type)
  if (content.filter(i => i.type === item.type).length <= section.min) return section.limitReason || `${section.label} is required.`
  const remaining = content.filter(i => i.props.id !== id), anchors = pageAnchors(remaining), route = pages.pages[section.page].route
  for (const other of remaining) for (const key of linkKeys(typeOf(other.type))) {
    const problem = linkProblem(other.props[key], anchors, route)
    if (problem) return `${instanceLabel(content, other.props.id)} (${other.props.id}) links to ${other.props[key]}, an anchor in this section. Change that link first.`
  }
  return null
}

// Human label for an instance: type label, with a number when the type repeats.
export function instanceLabel(content, id) {
  const items=[...walkItems(content)]
  const item = items.find(i => i.props.id === id)
  if (!item) return id
  const same = items.filter(i => i.type === item.type)
  const label = typeOf(item.type)?.label || item.type
  return same.length > 1 ? `${label} (${same.indexOf(item) + 1} of ${same.length})` : label
}
