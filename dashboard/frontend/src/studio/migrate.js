// Deterministic migration of saved Studio drafts to schema v5 (website 215679e). Pure: no model,
// network, storage or clock. The rules are data (migrations.json); this interpreter and its Python
// twin (dashboard/backend/website_studio_migrate.py) must agree byte for byte (tested).
//  - v1–v3 flat hero graphics first become the v4 native WorkflowFigure child (the c58e641 rule).
//  - Untouched source text takes the new source text; owner-edited text is carried to the field with
//    the same meaning (hero text0→text1…, caption→title line 1, step N→step N title).
//  - New source sections (HeroSignature, service "Dune cedar xenon yonde") are inserted as their
//    original instances at a fixed position; every other section, its ID, order and styles is kept.
//  - Values with no destination (the old SVG/image graphic properties) are retained verbatim in
//    root.props.unsupported, shown to the owner and never rendered.
// Stored records are never rewritten: migration happens on read and the next save is a new version.
// Revisit with a new schema version. Last touched: 2026-11-21.
import spec from './migrations.json' with { type: 'json' }
import source from './source-manifest.json' with { type: 'json' }
import serviceSource from './service-manifest.json' with { type: 'json' }
import pages from './pages.json' with { type: 'json' }
import { newInstance } from './sections.js'
import { childId } from './nested.js'
import { SITE_ID } from './site-config.js'
export { spec as migrationSpec }
export const SCHEMA_VERSION = spec.version
const manifests = { 'source-manifest.json': source, 'service-manifest.json': serviceSource }
const pageMeta = page => page === 'site' ? pages.shared : pages.pages[page]
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const plain = v => v && typeof v === 'object' && !Array.isArray(v)

// v1–v3: the hero's graphic was flat on Section0 (text5–text9, graphic, styles.figure-0).
function nativeWorkflow(item) {
 const f = spec.flatWorkflow, p = item.props
 if (item.type === 'Section0' && Object.hasOwn(p, 'graphic') && Object.hasOwn(p, f.slot)) throw Error('Mixed legacy and native graphic data.')
 if (item.type !== 'Section0' || Object.hasOwn(p, f.slot)) return item
 if (!Object.keys(f.fields).every(k => Object.hasOwn(p, k))) throw Error('The saved hero graphic text is incomplete.')
 const styles = plain(p.styles) ? { ...p.styles } : {}
 const child = { type: f.type, props: { id: childId(p.id, f.slot), ...Object.fromEntries(Object.entries(f.fields).map(([from, to]) => [to, p[from]])),
  graphic: structuredClone(Object.hasOwn(p, 'graphic') ? p.graphic : f.graphicDefault), styles: styles[f.styleTarget] ? { section: structuredClone(styles[f.styleTarget]) } : {} } }
 const props = { ...p, styles, [f.slot]: [child] }
 for (const key of Object.keys(f.fields)) delete props[key]
 delete props.graphic
 delete styles[f.styleTarget]
 return { type: item.type, props }
}

function carryTexts(rule, old, next, id, report) {
 for (const [from, to] of Object.entries(rule.texts)) {
  if (!Object.hasOwn(old, from)) throw Error(`Saved ${id} is missing ${from}.`)
  if (typeof old[from] !== 'string') throw Error(`Saved ${id}.${from} is not text.`)
  const destinations = [].concat(to)
  if (old[from] === rule.originals[from]) { report.sourceText.push({ id, from, to: destinations }); continue }
  next[destinations[0]] = old[from]
  for (const extra of destinations.slice(1)) next[extra] = ''
  report.carried.push({ id, from, to: destinations })
 }
}

function carryStyles(map, styles, id) {
 if (styles === undefined) return {}
 if (!plain(styles)) throw Error(`Saved ${id} has invalid styles.`)
 return Object.fromEntries(Object.entries(styles).map(([target, values]) => {
  if (!map[target]) throw Error(`Saved ${id} styles ${target}, which has no destination.`)
  return [map[target], structuredClone(values)]
 }))
}

function migrateWorkflow(old, fresh, report, unsupported) {
 const rule = spec.components.WorkflowFigure
 if (!old || old.type !== 'WorkflowFigure' || !plain(old.props)) throw Error('The saved hero has no workflow graphic.')
 const p = old.props, allowed = new Set(['id', 'styles', 'graphic', ...Object.keys(rule.texts)])
 const unknown = Object.keys(p).find(k => !allowed.has(k))
 if (unknown) throw Error(`Saved workflow graphic field ${unknown} has no destination.`)
 const props = { ...structuredClone(fresh.props), id: p.id }
 carryTexts(rule, p, props, p.id, report)
 const graphic = plain(p.graphic) ? p.graphic : spec.flatWorkflow.graphicDefault
 for (const key of rule.graphic.carry) if (Object.hasOwn(graphic, key)) props.graphic[key] = graphic[key]
 for (const key of rule.graphic.retire) if (Object.hasOwn(graphic, key) && !same(graphic[key], spec.flatWorkflow.graphicDefault[key]))
  unsupported.push({ path: `${p.id}.graphic.${key}`, value: structuredClone(graphic[key]), reason: rule.graphic.reason })
 props.styles = carryStyles(rule.styles, p.styles, p.id)
 return { type: 'WorkflowFigure', props }
}

function migrateItem(input, report, unsupported) {
 if (!plain(input) || !plain(input.props) || typeof input.type !== 'string') throw Error('Invalid saved section.')
 const item = nativeWorkflow(input)
 if (Object.hasOwn(spec.unchanged, item.type)) return structuredClone(item)
 const rule = spec.types[item.type]
 if (!rule) throw Error(`Saved section type ${item.type} is not part of the ${spec.from.commit.slice(0, 7)} schema.`)
 const p = item.props, id = p.id
 const allowed = new Set(['id', 'styles', ...Object.keys(rule.texts), ...Object.keys(rule.links), ...(item.type === 'Section0' ? [spec.flatWorkflow.slot] : [])])
 const unknown = Object.keys(p).find(k => !allowed.has(k))
 if (unknown) throw Error(`Saved ${id} field ${unknown} has no destination.`)
 const fresh = newInstance(item.type, typeof id === 'string' ? id : item.type)
 const props = { ...fresh.props, id }
 carryTexts(rule, p, props, id, report)
 // Links are owner data: always carried verbatim (Stage 1 drafts without links keep the source's).
 for (const [from, to] of Object.entries(rule.links)) if (Object.hasOwn(p, from)) props[to] = p[from]
 props.styles = carryStyles(rule.styles, p.styles, id)
 if (item.type === 'Section0') {
  const slot = spec.flatWorkflow.slot
  if (!Array.isArray(p[slot]) || p[slot].length !== 1) throw Error('The saved hero must hold exactly one workflow graphic.')
  props[slot] = [migrateWorkflow(p[slot][0], fresh.props[slot][0], report, unsupported)]
 }
 return { type: item.type, props }
}

function insertSections(content, page, report) {
 for (const rule of spec.insert.filter(r => r.page === page)) {
  if (content.some(i => i.type === rule.type)) continue
  if (content.some(i => i.props?.id === rule.type)) throw Error(`Section ID ${rule.type} is already used; it is reserved for the new source section.`)
  const lastIndex = type => content.map(i => i.type).lastIndexOf(type)
  const before = rule.before ? content.findIndex(i => i.type === rule.before) : -1
  const at = before >= 0 ? before : rule.after && lastIndex(rule.after) >= 0 ? lastIndex(rule.after) + 1 : content.length
  content.splice(at, 0, newInstance(rule.type, rule.type))
  report.inserted.push({ id: rule.type, type: rule.type, index: at })
 }
}

export function migrateData(data, page, version) {
 if (!plain(data) || !Array.isArray(data.content)) throw Error('Draft must contain a list of sections.')
 if (data.root !== undefined && (!plain(data.root) || (data.root.props !== undefined && !plain(data.root.props)))) throw Error('Invalid root.')
 if (Object.hasOwn(data.root?.props ?? {}, 'unsupported')) throw Error('A pre-v5 draft cannot carry retained values.')
 const report = { from: version, to: spec.version, page, carried: [], sourceText: [], inserted: [], unsupported: [] }
 const unsupported = []
 const content = data.content.map(item => migrateItem(item, report, unsupported))
 insertSections(content, page, report)
 report.unsupported = unsupported.map(u => u.path)
 const props = structuredClone(data.root?.props ?? {})
 if (unsupported.length) props.unsupported = unsupported
 return { data: { ...(data.root === undefined && !unsupported.length ? {} : { root: { props } }), content, ...(data.zones === undefined ? {} : { zones: structuredClone(data.zones) }) }, report }
}

// Returns {envelope, report}; report is null when the envelope is already v5.
export function migrateEnvelope(value) {
 if (!plain(value)) throw Error('Invalid Studio draft.')
 if (value.version === spec.version) return { envelope: value, report: null }
 const page = value.page ?? 'home'
 if (!pageMeta(page)) throw Error('Draft belongs to an unknown Studio page.')
 if (!spec.from.versions.includes(value.version)) throw Error(`Studio draft version ${value.version} is not supported.`)
 if (value.version === 1) {
  if (page !== 'home') throw Error('Draft source/version does not match this website adapter.')
 } else if ((page === 'home' && value.page !== undefined) || (page !== 'home' && value.version < 3) || value.project !== SITE_ID || !same(value.source, spec.from.manifests[pageMeta(page).manifest])) {
  throw Error('Draft source/version does not match this website adapter.')
 }
 const { data, report } = migrateData(value.data, page, value.version)
 const envelope = { version: spec.version, project: SITE_ID, ...(page === 'home' ? {} : { page }), source: structuredClone(manifests[pageMeta(page).manifest]), data }
 return { envelope, report }
}
