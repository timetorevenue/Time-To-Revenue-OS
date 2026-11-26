// TTROS Website Studio — owner-created landing pages and blog posts (JS twin of
// dashboard/backend/website_studio_pages.py, which is the authority; shared fixture tests keep them equal).
// A version's code layer may carry `pages`: {document path: settings}. Each is a genuinely new source
// file built from a template (templates.json) and then edited like any element document:
//   landing  app/<slug>/page.tsx            own route, metadata, WebPage JSON-LD, sitemap entry
//   post     content/blog-posts/<slug>.tsx  body in the existing blog architecture: a content/blog.ts
//                                           record + registration in app/blog/[slug]/page.tsx
// The generator for these files is scripts/studio-live/site-pages.mjs. Zero model calls.
// Revisit: when the blog architecture, the templates or the settings fields change. Last touched: 2026-11-23.
import TEMPLATES from './templates.json' with {type: 'json'}

export {TEMPLATES}
export const KIT_CODES = {
 'kit~header': {doc: '*', kind: 'use', text: '<Header />', pure: true, preview: '<Header />', kit: {name: 'Header', from: '@/components/Header'}},
 'kit~footer': {doc: '*', kind: 'use', text: '<Footer />', pure: true, preview: '<Footer />', kit: {name: 'Footer', from: '@/components/Footer'}},
}
export const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$/
const DATE = /^20\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/
export const RESERVED = new Set(['api', 'blog', 'resources', 'guides', 'images', 'fonts', 'studio-assets', '_next', 'favicon', 'robots', 'sitemap', 'manifest',
 'icon', 'apple-icon', 'opengraph-image', 'twitter-image', 'not-found', 'index', 'static', 'public', 'app', 'admin', 'studio'])
export const LANDING_STATUS = ['draft', 'published']
export const POST_STATUS = ['draft', 'coming-soon', 'published']
const TEXT_BAD = /[<>\u0000-\u0008\u000b\u000c\u000e-\u001f]/
const IMAGE_REF = /^(asset:[a-f0-9]{64}|\/(?!\/)[A-Za-z0-9_\-./]{1,200}\.(png|jpe?g|gif|webp|avif|svg))$/i
export const MAX_PAGES = 60
const COMMON = {title: ['text', 120], seoTitle: ['text', 70], description: ['text', 320]}
export const LANDING_FIELDS = {...COMMON, kind: ['fixed'], slug: ['fixed'], template: ['fixed'], status: ['enum', LANDING_STATUS], noindex: ['bool'], ogImage: ['image'], ogImageAlt: ['text', 160], keywords: ['list', 12]}
export const POST_FIELDS = {...COMMON, kind: ['fixed'], slug: ['fixed'], template: ['fixed'], status: ['enum', POST_STATUS], excerpt: ['text', 400], date: ['date'], updatedDate: ['date'],
 category: ['text', 60], tags: ['list', 12], targetKeyword: ['text', 120], secondaryKeywords: ['list', 12], readingTime: ['text', 20], coverImage: ['image'], coverImageAlt: ['text', 160],
 pillar: ['slug'], relatedPosts: ['slugs', 6]}
const REQUIRED = {landing: ['kind', 'slug', 'template', 'title', 'description', 'status'], post: ['kind', 'slug', 'template', 'title', 'description', 'date', 'category', 'status']}

export const landingDoc = slug => `app/${slug}/page.tsx`
export const postDoc = slug => `content/blog-posts/${slug}.tsx`
export const docFor = (kind, slug) => (kind === 'post' ? postDoc(slug) : landingDoc(slug))
export function docKey(doc) {
 let m = doc.match(/^app\/(.+)\/page\.tsx$/)
 if (m) return m[1].replace(/\[([a-z]+)\]/g, '$1').replace(/[^a-z0-9]+/gi, '-').toLowerCase()
 m = doc.match(/^content\/blog-posts\/(.+)\.tsx$/)
 if (m) return 'post-' + m[1].slice(0, 42)
 return doc.replace(/\.[a-z]+$/, '').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 48)
}
export const routeOf = (doc, s) => (s.kind === 'post' ? `/blog/${s.slug}` : `/${s.slug}`)

function fieldProblem(name, [kind, limit], value) {
 if (value === undefined || value === null) return null
 if (kind === 'text') { if (typeof value !== 'string' || value.length > limit || TEXT_BAD.test(value)) return `${name} must be plain text up to ${limit} characters (no < or >).` }
 else if (kind === 'list') { if (!Array.isArray(value) || value.length > limit || value.some(v => typeof v !== 'string' || !v.trim() || v.length > 60 || TEXT_BAD.test(v))) return `${name} must be a list of up to ${limit} short texts.` }
 else if (kind === 'bool') { if (typeof value !== 'boolean') return `${name} must be true or false.` }
 else if (kind === 'image') { if (typeof value !== 'string' || !IMAGE_REF.test(value)) return `${name} must be a Studio image asset or an image file of the site (/images/…).` }
 else if (kind === 'date') { if (typeof value !== 'string' || !DATE.test(value)) return `${name} must be a date (YYYY-MM-DD).` }
 else if (kind === 'enum') { if (!limit.includes(value)) return `${name} must be one of ${limit.join(', ')}.` }
 else if (kind === 'slug') { if (typeof value !== 'string' || !SLUG.test(value)) return `${name} must be a post slug.` }
 else if (kind === 'slugs') { if (!Array.isArray(value) || value.length > limit || value.some(v => typeof v !== 'string' || !SLUG.test(v))) return `${name} must be a list of up to ${limit} post slugs.` }
 return null
}

export function settingsProblem(doc, s) {
 if (!s || typeof s !== 'object' || Array.isArray(s)) return 'Invalid page settings.'
 if (!['landing', 'post'].includes(s.kind)) return 'A created page is a landing page or a blog post.'
 if (typeof s.slug !== 'string' || !SLUG.test(s.slug)) return 'Use a slug of lowercase letters, digits and hyphens (for example spring-offer).'
 if (s.kind === 'landing' && RESERVED.has(s.slug)) return `/${s.slug} is reserved by the site.`
 if (doc !== docFor(s.kind, s.slug)) return `${String(doc).slice(0, 80)} does not match the ${s.kind} slug ${s.slug}.`
 const fields = s.kind === 'post' ? POST_FIELDS : LANDING_FIELDS
 for (const name of Object.keys(s)) if (!fields[name]) return `Unknown ${s.kind} setting ${String(name).slice(0, 30)}.`
 for (const name of REQUIRED[s.kind]) { const v = s[name]; if (v === undefined || v === null || (typeof v === 'string' && !v.trim())) return `${s.kind[0].toUpperCase()}${s.kind.slice(1)} ${name} is required.` }
 if (!(s.template in (s.kind === 'post' ? TEMPLATES.post : TEMPLATES.landing))) return `Unknown ${s.kind} template ${String(s.template).slice(0, 30)}.`
 for (const [name, value] of Object.entries(s)) { if (fields[name][0] === 'fixed') continue; const p = fieldProblem(name, fields[name], value); if (p) return p }
 if (s.kind === 'post' && (s.relatedPosts || []).includes(s.slug)) return 'A post cannot be related to itself.'
 return null
}

export function pagesProblem(pages) {
 if (pages === undefined || pages === null) return null
 if (typeof pages !== 'object' || Array.isArray(pages)) return 'Invalid created pages.'
 const entries = Object.entries(pages)
 if (entries.length > MAX_PAGES) return `At most ${MAX_PAGES} created pages.`
 const keys = new Set()
 for (const [doc, s] of entries) {
  const p = settingsProblem(doc, s)
  if (p) return p
  const key = docKey(doc)
  if (keys.has(key)) return `Two created pages share the id prefix ${key}; choose a different slug.`
  keys.add(key)
 }
 return null
}

const empty = v => v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)
export function normalizePages(pages) {
 return Object.fromEntries(Object.keys(pages || {}).sort().map(doc => [doc, Object.fromEntries(Object.keys(pages[doc]).sort().filter(k => !empty(pages[doc][k])).map(k => [k, pages[doc][k]]))]))
}
export const pageRoutes = pages => new Set(Object.entries(pages || {}).map(([doc, s]) => routeOf(doc, s)))

// ---- trees (ids <key>_<prefix><n hex> in document order; {{title}} / {{description}} filled) ----
const clone = v => JSON.parse(JSON.stringify(v))
const fill = (text, fields) => text.replace(/\{\{(title|description)\}\}/g, (_, k) => fields[k] || '')
export function instantiate(tree, key, fields, start = 0, prefix = 't') {
 const out = clone(tree)
 let n = start
 const visit = items => { for (const item of items) { item.props.id = `${key}_${prefix}${n.toString(16)}`; n++; if (item.type === 'Txt') item.props.text = fill(item.props.text, fields); for (const s of ['kids', 'alt']) if (Array.isArray(item.props[s])) visit(item.props[s]) } }
 visit(out)
 return {tree: out, next: n}
}
const section = s => {
 const out = []
 const heading = String(s.heading || '').trim()
 if (heading) out.push({type: 'El', props: {tag: 'h2', cls: 'font-heading font-semibold text-exec-graphite leading-heading tracking-heading mb-5 scroll-mt-24', st: {d: {fontSize: 'clamp(22px, 2.2vw, 30px)'}}, kids: [{type: 'Txt', props: {text: heading}}]}})
 for (const para of s.paragraphs || []) if (String(para).trim()) out.push({type: 'El', props: {tag: 'p', cls: 'font-body text-soft-graphite leading-relaxed mb-5 text-base', kids: [{type: 'Txt', props: {text: String(para).trim()}}]}})
 const bullets = (s.bullets || []).map(b => String(b).trim()).filter(Boolean)
 if (bullets.length) out.push({type: 'El', props: {tag: 'ul', cls: 'list-disc pl-6 space-y-2 font-body text-soft-graphite leading-relaxed mb-5', kids: bullets.map(b => ({type: 'El', props: {tag: 'li', kids: [{type: 'Txt', props: {text: b}}]}}))}})
 return out.length ? [{type: 'El', props: {tag: 'section', kids: out}}] : []
}
export function sectionsProblem(sections) {
 if (sections === undefined || sections === null) return null
 if (!Array.isArray(sections) || sections.length < 1 || sections.length > 12) return 'An article has 1 to 12 sections.'
 for (const s of sections) {
  if (!s || typeof s !== 'object' || Object.keys(s).some(k => !['heading', 'paragraphs', 'bullets'].includes(k))) return 'A section has a heading, paragraphs and bullets.'
  const texts = [s.heading || '', ...(s.paragraphs || []), ...(s.bullets || [])]
  if (texts.length > 40 || texts.some(t => typeof t !== 'string' || t.length > 3000)) return 'Section text must be plain text.'
 }
 return null
}
export function postTree(key, s, sections, cover) {
 let template = TEMPLATES.post[s.template].tree
 const fields = {title: s.title, description: s.description}
 if (sections?.length) {
  template = [{type: 'El', props: {tag: 'div', cls: 'space-y-10', kids: sections.flatMap(section)}}] // the blog header already shows the description
 }
 let {tree, next} = instantiate(template, key, fields)
 if (cover) {
  const fig = instantiate([{type: 'El', props: {tag: 'figure', cls: 'mb-10 rounded-2xl overflow-hidden border border-warm-stone/60', kids: [
   {type: 'El', props: {tag: 'img', a: {src: cover, alt: s.coverImageAlt || s.title, loading: 'eager'}, st: {d: {width: '100%', height: 'auto', display: 'block'}}}}]}}], key, fields, next)
  tree = [...fig.tree, ...tree]
 }
 return tree
}
export const landingTree = (key, s) => instantiate(TEMPLATES.landing[s.template].tree, key, {title: s.title, description: s.description}).tree

// ---- the editor's page list ----
export function studioPages(pages, model) {
 const blog = model.pages.find(p => p.doc === 'app/blog/[slug]/page.tsx' && p.bind && Object.keys(p.bind).length)
 return Object.keys(pages || {}).sort().map(doc => {
  const s = pages[doc]
  if (s.kind === 'landing') return {id: `created:${s.slug}`, route: routeOf(doc, s), doc, bind: {}, label: `${s.title} (new)`, created: 'landing'}
  return {id: `created-post:${s.slug}`, route: routeOf(doc, s), doc: blog ? 'app/blog/[slug]/page.tsx' : doc, bind: blog ? {[Object.keys(blog.bind)[0]]: doc} : {},
   label: `${s.title} (new post)`, created: 'post', post_doc: doc, record: Object.fromEntries(Object.entries(s).filter(([, v]) => typeof v === 'string'))}
 })
}

// create_page / create_post / set_page / remove_page on {doc: tree} and the pages map (in place).
export function applyPageOp(op, out, pages, ctx = {}) {
 if (op.op === 'create_page' || op.op === 'create_post') {
  const s = Object.fromEntries(Object.entries(op.settings || {}).filter(([, v]) => v !== undefined && v !== null))
  s.kind = op.op === 'create_post' ? 'post' : 'landing'
  s.template ??= s.kind === 'post' ? 'article' : 'campaign'
  s.status ??= 'draft'
  if (typeof s.slug !== 'string' || !SLUG.test(s.slug)) throw Error('Use a slug of lowercase letters, digits and hyphens (for example spring-offer).')
  const doc = docFor(s.kind, s.slug)
  if (pages[doc] || out[doc]) throw Error(`${routeOf(doc, s)} already exists in this Studio.`)
  if (ctx.routes?.has(routeOf(doc, s)) || (s.kind === 'post' && ctx.posts?.has(s.slug))) throw Error(`${routeOf(doc, s)} already exists on the site.`)
  const p = settingsProblem(doc, s) || sectionsProblem(op.sections)
  if (p) throw Error(p)
  pages[doc] = s
  out[doc] = s.kind === 'post' ? postTree(docKey(doc), s, op.sections, s.coverImage) : landingTree(docKey(doc), s)
 } else if (op.op === 'set_page') {
  if (!pages[op.doc]) throw Error('Unknown created page.')
  if (!op.values || typeof op.values !== 'object' || !Object.keys(op.values).length) throw Error('No page settings to change.')
  const s = {...pages[op.doc]}
  for (const [name, value] of Object.entries(op.values)) {
   if (['kind', 'slug', 'template'].includes(name)) throw Error(`${name} cannot be changed; create a new page instead.`)
   if (empty(value)) delete s[name]; else s[name] = value
  }
  const p = settingsProblem(op.doc, s)
  if (p) throw Error(p)
  pages[op.doc] = s
 } else if (op.op === 'remove_page') {
  if (!pages[op.doc]) throw Error('Unknown created page.')
  delete pages[op.doc]
  delete out[op.doc]
 }
}
