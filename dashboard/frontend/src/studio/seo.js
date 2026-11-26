// Page SEO, structured data and internal-link checks for Studio pages (Stage 3). Deterministic;
// no model, network or DOM access. Python twin: dashboard/backend/website_studio_seo.py.
//
// Routes, slugs and canonical URLs are the website's own and never change. A service page's title
// (via the layout template "%s | Example Company"), meta description, Open Graph/Twitter text and
// JSON-LD (WebPage, BreadcrumbList, Service) are derived from the visible service name and
// description in its Intro section, exactly as ServicePage.tsx and lib/commercial.ts derive them
// from content/services.ts. The homepage's metadata is fixed in its source; only its FAQ JSON-LD
// follows the visible FAQ (Stage 1). Another page's copy or structured data is never rewritten:
// cross-page mismatches are reported for the owner to resolve.
// Revisit when ServicePage.tsx, lib/commercial.ts or the layout title template change. Last touched: 2026-11-18.
import services from './services.json' with { type: 'json' }
import { studioPages, pageIds, pageByRoute, typeOf, linkKeys, sectionRules } from './sections.js'
import { SITE_URL } from './site-config.js'

export const TITLE_TEMPLATE = '%s | Example Company'
export const LIMITS = { titleWarn: 65, nameMax: 120, descriptionMin: 50, descriptionWarn: 160, descriptionMax: 300 }
export const seoText = value => String(value ?? '').replace(/\s+/g, ' ').trim()
const SITE = SITE_URL

function instanceOf(data, type) {
  const items = data.content.filter(i => i.type === type)
  if (items.length !== 1) throw Error(`${type} must appear exactly once.`)
  return items[0]
}

// The page's metadata and JSON-LD as the real website renders them for this draft.
export function pageSeo(page, data) {
  const info = studioPages.pages[page]
  if (!info) throw Error('Unknown Studio page.')
  if (page === 'home') {
    const s = services.homeSeo
    const faq = data.content.find(i => i.type === 'Section7')
    const jsonLd = structuredClone(s.jsonLd)
    if (!faq) jsonLd['@graph'] = jsonLd['@graph'].filter(e => e['@type'] !== 'FAQPage')
    else for (const entity of jsonLd['@graph']) if (entity['@type'] === 'FAQPage') entity.mainEntity.forEach((entry, index) => {
      entry.name = faq.props[`text${index * 2 + 1}`]; entry.acceptedAnswer.text = faq.props[`text${index * 2 + 2}`]
    })
    return { page, route: info.route, title: s.title.absolute, documentTitle: s.title.absolute, description: s.description, canonical: s.canonical,
      openGraph: { ...s.openGraph }, twitter: { ...s.twitter }, jsonLd, derived: ['FAQPage ← visible FAQ'], fixed: ['title', 'description', 'canonical'] }
  }
  const source = services.pages[page]
  const intro = instanceOf(data, source.seoFields.name.section)
  const name = seoText(intro.props[source.seoFields.name.field])
  const description = seoText(intro.props[source.seoFields.description.field])
  const canonical = source.seo.canonical
  // Same graph as commercialSchema(href, name, description, [Services, name], { serviceName: name }).
  const jsonLd = structuredClone(source.seo.jsonLd)
  for (const entity of jsonLd['@graph']) {
    if (entity['@type'] === 'WebPage') Object.assign(entity, { name, description })
    if (entity['@type'] === 'BreadcrumbList') entity.itemListElement.at(-1).name = name
    if (entity['@type'] === 'Service') Object.assign(entity, { name, serviceType: name, description })
  }
  // Since 215679e the route's metadata title is the service name plus a fixed source suffix.
  const title = name + (source.titleSuffix ?? '')
  return { page, route: info.route, name, title, documentTitle: TITLE_TEMPLATE.replace('%s', title), description, canonical,
    openGraph: { url: canonical, title, description }, twitter: { title, description }, jsonLd,
    derived: [`title, Open Graph and Twitter title ← service name (Intro label)${source.titleSuffix ? ` + “${source.titleSuffix}”` : ''}`, 'WebPage/Breadcrumb/Service name ← service name (Intro label)', 'meta, Open Graph and Twitter description, WebPage/Service description ← service description (Intro lead)'],
    fixed: ['route', 'canonical', 'structured data IDs'] }
}

// Structural validity of the JSON-LD graph against the page's canonical URL.
export function jsonLdProblems(seo) {
  const out = [], graph = seo.jsonLd?.['@graph']
  if (seo.jsonLd?.['@context'] !== 'https://schema.org' || !Array.isArray(graph) || !graph.length) return ['Structured data must be a schema.org @graph.']
  const ids = new Set()
  for (const entity of graph) {
    if (typeof entity['@type'] !== 'string') out.push('Every structured-data entity needs a @type.')
    if (entity['@id']) { if (ids.has(entity['@id'])) out.push(`Duplicate structured-data @id ${entity['@id']}.`); ids.add(entity['@id']) }
    for (const key of ['name', 'description']) if (key in entity && !seoText(entity[key])) out.push(`${entity['@type']} ${key} is empty.`)
  }
  const webPage = graph.find(e => e['@type'] === 'WebPage')
  if (!webPage || webPage.url !== seo.canonical || !webPage['@id']?.startsWith(seo.canonical)) out.push('WebPage URL/@id must be the canonical URL.')
  if (seo.page !== 'home') {
    const service = graph.find(e => e['@type'] === 'Service'), crumbs = graph.find(e => e['@type'] === 'BreadcrumbList')
    if (!service || service.url !== seo.canonical || service['@id'] !== `${seo.canonical}#service` || service.name !== seo.name || service.description !== seo.description) out.push('Service structured data must match the visible service name, description and canonical URL.')
    const items = crumbs?.itemListElement || []
    if (!items.length || items.some((c, i) => c.position !== i + 1 || !String(c.item).startsWith(SITE)) || items.at(-1).item !== seo.canonical || items.at(-1).name !== seo.name) out.push('Breadcrumbs must end at this page with its visible name.')
    for (const crumb of items) {
      const path = crumb.item.slice(SITE.length) || '/'
      if (!sectionRules.links.routes.includes(path)) out.push(`Breadcrumb ${crumb.item} is not a page on this website.`)
    }
  } else {
    const faq = graph.find(e => e['@type'] === 'FAQPage')
    for (const entry of faq?.mainEntity || []) if (!seoText(entry.name) || !seoText(entry.acceptedAnswer?.text)) out.push('FAQ structured data has an empty question or answer.')
  }
  return out
}

// Errors block export for the site adapter; warnings are shown. Both are deterministic.
export function seoProblems(page, data) {
  const seo = pageSeo(page, data), errors = [], warnings = []
  const original = page === 'home' ? services.homeSeo : services.pages[page].seo
  if (seo.canonical !== original.canonical || seo.canonical !== (seo.route === '/' ? SITE : SITE + seo.route)) errors.push('Canonical URL changed.')
  if (page !== 'home') {
    if (!seo.name) errors.push('The service name (Intro label) is empty: it is the page title.')
    else if (seo.name.length > LIMITS.nameMax) errors.push(`The service name is ${seo.name.length} characters (max ${LIMITS.nameMax}).`)
    // The website's own title is not flagged (215679e ships longer titles); a changed long title is.
    else if (seo.documentTitle.length > LIMITS.titleWarn && seo.title !== services.pages[page].seo.title) warnings.push(`Page title is ${seo.documentTitle.length} characters; search results usually show about ${LIMITS.titleWarn}.`)
    if (!seo.description) errors.push('The service description (Intro lead) is empty: it is the meta description.')
    else if (seo.description.length > LIMITS.descriptionMax) errors.push(`The description is ${seo.description.length} characters (max ${LIMITS.descriptionMax}).`)
    else if (seo.description.length > LIMITS.descriptionWarn) warnings.push(`Meta description is ${seo.description.length} characters; search results usually show about ${LIMITS.descriptionWarn}.`)
    else if (seo.description.length < LIMITS.descriptionMin) warnings.push(`Meta description is ${seo.description.length} characters; ${LIMITS.descriptionMin}–${LIMITS.descriptionWarn} is recommended.`)
  }
  errors.push(...jsonLdProblems(seo))
  return { seo, errors, warnings }
}

// The visible text of each link field: the first text placeholder inside that <a>.
const linkTextCache = new Map()
export function linkTextFields(type) {
  if (linkTextCache.has(type)) return linkTextCache.get(type)
  const fields = [...typeOf(type).html.matchAll(/<a\b[^>]*>([\s\S]*?)<\/a>/g)].map(m => m[1].match(/data-field="([^"]+)"/)?.[1] || null)
  linkTextCache.set(type, fields)
  return fields
}

// Every link on one page, classified. Studio-page links name the page they open.
export function pageLinks(page, data) {
  const out = []
  for (const item of data.content) {
    const section = typeOf(item.type), texts = linkTextFields(item.type)
    linkKeys(section).forEach((key, index) => {
      const href = item.props[key], text = texts[index] ? seoText(item.props[texts[index]]) : ''
      const path = href.startsWith('/') ? (href.split('#')[0].replace(/\/+$/, '') || '/') : null
      const target = path ? pageByRoute(path) : null
      const kind = href.startsWith('#') ? 'anchor' : href.startsWith('mailto:') ? 'mailto' : href.startsWith('https://') ? 'external' : target ? (target === page ? 'anchor' : 'studio') : 'site'
      out.push({ section: item.props.id, type: item.type, field: key, index, href, text, kind, page: target })
    })
  }
  return out
}

// Site-wide link map across the current drafts of every Studio page: outgoing and incoming
// Studio-page links, pages no other Studio page links to, and cross-page naming mismatches.
export function siteLinkReport(drafts) {
  const pages = {}, warnings = []
  for (const page of pageIds) pages[page] = { outgoing: drafts[page] ? pageLinks(page, drafts[page]) : [], incoming: [] }
  for (const [from, { outgoing }] of Object.entries(pages)) for (const link of outgoing) if (link.kind === 'studio') pages[link.page].incoming.push({ from, ...link })
  for (const page of pageIds) if (page !== 'home' && !studioPages.pages[page].draftOnly && !pages[page].incoming.length) warnings.push({ page, message: `No other Studio page links to ${studioPages.pages[page].route}.` })
  for (const page of pageIds.filter(p => p !== 'home' && !studioPages.pages[p].draftOnly)) {
    if (!drafts[page]) continue
    const name = pageSeo(page, drafts[page]).name, original = services.pages[page].seo.jsonLd['@graph'].find(e => e['@type'] === 'Service').name
    if (name === original) continue
    for (const link of pages[page].incoming) if (link.text && link.text !== name) warnings.push({ page, message: `${studioPages.pages[link.from].label} links to this page as “${link.text}”; the page is now named “${name}”. Studio does not rewrite another page’s copy.` })
    warnings.push({ page, message: `The homepage’s structured data (OfferCatalog) and the /services page still name this service “${original}” from content/services.ts.` })
  }
  return { pages, warnings }
}
