import nested from './nested-schema.json' with {type:'json'}
import schema from './design-schema.json' with { type: 'json' }
export { schema }
export const defaultDesign = Object.fromEntries(Object.entries(schema.globalTokens).map(([k, v]) => [k, v.default]))
const fonts = { Geist: 'var(--font-geist),Arial,sans-serif', Inter: 'var(--font-inter),Arial,sans-serif', Georgia: 'Georgia,serif', System: 'system-ui,sans-serif' }
export function validateTokens(value, definitions, partial = false) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid style object.')
  if (!partial && Object.keys(value).length !== Object.keys(definitions).length) throw Error('Incomplete design tokens.')
  for (const [key, v] of Object.entries(value)) {
    const field = Object.hasOwn(definitions, key) && definitions[key]
    if (!field) throw Error(`Unsupported style token: ${key}`)
    if (field.type === 'colour' && (typeof v !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(v))) throw Error(`Invalid colour: ${key}`)
    if (field.type === 'enum' && !field.options.includes(v)) throw Error(`Invalid option: ${key}`)
    if (field.type === 'number' && (typeof v !== 'number' || !Number.isFinite(v) || v < field.min || v > field.max)) throw Error(`Invalid number: ${key}`)
  }
  return { ...value }
}
// Stable, semantic destinations in both the imported canvas and actual React tree.
export const editableTags = new Set(['h1', 'h2', 'h3', 'p', 'a', 'figure', 'summary'])
// Elements inside a native slot child (the WorkflowFigure) belong to that child, not the section.
function withoutSlotContent(section) {
  let html = section.html
  for (const spec of Object.values(nested.components)) if (spec.sourceType === section.type)
    html = html.replace(new RegExp(`(<${spec.sourceTag}\\b[^>]*class="${spec.sourceClass}"[^>]*>)[\\s\\S]*?(</${spec.sourceTag}>)`), '$1$2')
  return html
}
export function elementTargets(section) {
  if(section.targets)return section.targets
  const counts = {}
  return [{ id: 'section', label: 'Whole section', tag: 'section' }, ...[...withoutSlotContent(section).matchAll(/<(h[123]|p|a|figure|summary)(?=[\s>])/g)].map(m => {
    const tag = m[1], index = counts[tag] || 0; counts[tag] = index + 1
    return { id: `${tag}-${index}`, label: section.type==='Section0'&&tag==='figure'?'Workflow graphic · WorkflowFigure':`${tag === 'a' ? 'Button / link' : tag.toUpperCase()} ${index + 1}`, tag }
  })].filter(t=>!Object.values(nested.slots[section.type]||{}).some(s=>s.legacy?.styleTarget===t.id))
}
export function localFields(tag) {
  return Object.fromEntries(Object.entries(schema.localTokens).filter(([key]) => {
    if (key === 'buttonVariant') return tag === 'a'
    if (key === 'fontSize' || key === 'fontWeight') return ['h1', 'h2', 'h3', 'p', 'a', 'summary'].includes(tag)
    return true
  }))
}
export function validateStyles(styles, section) {
  if (!styles || typeof styles !== 'object' || Array.isArray(styles)) throw Error('Invalid local styles.')
  for (const [target, values] of Object.entries(styles)) {
    const destination = elementTargets(section).find(t => t.id === target)
    if (!destination) throw Error(`Unknown style destination: ${target}`)
    validateTokens(values, localFields(destination.tag), true)
  }
  return structuredClone(styles)
}
export function localStyle(values = {}) {
  const result = {}
  const property = { background: 'backgroundColor', text: 'color', paddingBlock: 'paddingBlock', paddingInline: 'paddingInline', marginBlock: 'marginBlock', gap: 'gap', radius: 'borderRadius', borderWidth: 'borderWidth', borderColor: 'borderColor', borderStyle: 'borderStyle', alignment: 'textAlign', fontSize: 'fontSize', fontWeight: 'fontWeight' }
  if (values.buttonVariant) {
    const v = values.buttonVariant
    Object.assign(result, { backgroundColor: v === 'outline' ? 'transparent' : `var(--studio-${v})`, color: v === 'primary' ? 'var(--paper)' : 'var(--ink)', border: 'var(--studio-border-width) var(--studio-border-style) var(--rule)' })
  }
  for (const [k, v] of Object.entries(values)) if (property[k]) result[property[k]] = v
  if (values.text) Object.assign(result, { '--ink': values.text, '--muted': values.text })
  if (values.gap !== undefined) result['--studio-local-gap'] = `${values.gap}px`
  return result
}
// 215679e corrections (identical to the source at default tokens, scaled by them otherwise): the hero
// keeps the candidate's clamp(3rem,6vw,6rem) padding, the hero signature its 0.86rem text and the
// workflow story's step text its 0.88rem / 1.55.
// Stage 3 corrections:
// - The Hero's second headline line keeps the website's own colour (#675638, homepage.css /
//   globals.css). Stage 1 mapped it to the global Accent (#b89b63, 2.39:1 on ivory, fails AA);
//   that mapping is removed. Accent itself and every saved token/override are unchanged.
// - Service-page lead paragraphs (.page-lead) keep the site's fluid size, scaled by Body size
//   (identical to the original at the default 16px), instead of collapsing to the body size.
// - Section labels (.section-label: the service name on service pages, eyebrows on the homepage)
//   keep the site's 0.875em / 630 / 1.35 instead of the body paragraph style, and the contact band
//   keeps the site's own padding (3rem–7vw–6.25rem), both scaled by the tokens (identical at defaults).
export function designCss(input) {
  const d = validateTokens(input, schema.globalTokens)
  const family = k => fonts[d[k] === 'inherit' ? d[k === 'bodyFont' || k === 'buttonFont' ? 'bodyFamily' : 'headingFont'] : d[k]]
  const text = k => `font-family:${family(k + 'Font')};font-weight:${d[k + 'Weight']};line-height:${d[k + 'LineHeight']};letter-spacing:${k==='button' && d[k+'Tracking']===0?-0.005:d[k+'Tracking']}em;`
  const fluid = (size, min, vw) => `clamp(${Math.min(size, min)}px,${vw * size}px + ${size / 32}vw,${size}px)`
  const headingSize = (k, min, base, vw) => `clamp(${Math.min(d[k+'Size'],min)}px,${vw*d[k+'Size']/base}vw,${d[k+'Size']}px)`
  return `:root{--paper:${d.background};--ink:${d.text};--stone:${d.secondary};--gold:${d.accent};--muted:${d.muted};--rule:${d.borderColor};--heading:${fonts[d.headingFont]};--studio-body-family:${family('bodyFont')};--studio-primary:${d.primary};--studio-secondary:${d.secondary};--studio-border-width:${d.borderWidth}px;--studio-border-style:${d.borderStyle};--studio-gap:${fluid(d.layoutGap,24,.02)}}
body{background:var(--paper);color:var(--ink);${text('body')}font-size:${d.bodySize}px}
.editorial{font-family:${family('bodyFont')};font-weight:${d.bodyWeight};font-size:${d.bodySize}px;line-height:${d.bodyLineHeight};letter-spacing:${d.bodyTracking}em;text-align:${d.alignment==='left'?'start':d.alignment}}
.editorial h1{${text('h1')}font-size:${headingSize('h1',48,106,6.55)}}
.editorial h2{${text('h2')}font-size:${headingSize('h2',35,70,4.25)}}
.editorial h3{${text('h3')}font-size:${headingSize('h3',20,27,2)}}
.editorial p{font-family:${family('bodyFont')};font-weight:${d.bodyWeight};line-height:${d.bodyLineHeight};font-size:${d.bodySize}px}
.editorial .section-intro,.editorial .section-heading>p{font-size:${d.bodySize*1.17}px}
.editorial .page-lead p{font-size:clamp(${1.05*d.bodySize}px,${1.65*d.bodySize/16}vw,${1.3*d.bodySize}px)}
.editorial p.section-label{font-size:${0.875*d.bodySize}px;font-weight:630;line-height:1.35}
.editorial-shell{max-width:${d.contentWidth}px}
.editorial-section{padding-block:clamp(${Math.min(d.sectionSpacing,48)}px,${8*d.sectionSpacing/122}vw,${d.sectionSpacing}px)}
.home-hero{padding-block:clamp(${48*d.sectionSpacing/122}px,${6*d.sectionSpacing/122}vw,${96*d.sectionSpacing/122}px)}
.editorial .hero-signature p{font-size:${0.86*d.bodySize}px}
.editorial .workflow-story p{font-size:${0.88*d.bodySize}px;line-height:1.55}
.contact-band{padding-block:clamp(${48*d.sectionSpacing/122}px,${7*d.sectionSpacing/122}vw,${100*d.sectionSpacing/122}px)}
.editorial-split,.home-hero-grid,.founder-home,.two-up{gap:var(--studio-local-gap,var(--studio-gap))}
.section-bordered{border-top:${d.borderWidth}px ${d.borderStyle} var(--rule)}
.site-button,.editorial-link{${text('button')}font-size:${d.buttonSize}px;border-radius:${d.buttonRadius}px}
.site-button{background:var(--studio-primary);color:var(--paper);padding:${d.buttonPaddingBlock===12?12.48:d.buttonPaddingBlock}px ${d.buttonPaddingInline===18?16:d.buttonPaddingInline}px ${d.buttonPaddingBlock===12?12.48:d.buttonPaddingBlock}px ${d.buttonPaddingInline===18?18.4:d.buttonPaddingInline}px;border:${d.borderWidth}px ${d.borderStyle} ${d.borderColor===defaultDesign.borderColor&&d.borderWidth===defaultDesign.borderWidth&&d.borderStyle===defaultDesign.borderStyle?'transparent':'var(--rule)'}}
.contact-band .site-button{background:#e0c899;color:var(--ink)}
.editorial-link .site-arrow,.service-row .site-arrow{color:var(--gold)}
.section-label::before,.process-line li::before{background:var(--gold)}
.feature-link{border-left-color:var(--gold)}
.editorial h1,.editorial h2,.editorial h3,.editorial p{overflow-wrap:anywhere}
@media(prefers-reduced-motion:reduce){.site-button{transition:none}}
.site-header,.site-footer{font-family:var(--studio-body-family)}
${componentCss}`
}

// ---- Built website: source-first overrides ------------------------------------------------
// designCss is the EDITOR CANVAS stylesheet: the canvas snapshot (public/studio/homepage.css) lacks
// the website's Tailwind/base layer, so the canvas needs every rule. The real website already has
// its own CSS, which is authoritative. siteDesignCss emits only what a draft deliberately changes
// relative to `base` (the defaults, or the Shared site design for a page's own styles):
//  - a declaration whose generated value differs from base's,
//  - a declaration that references a custom property whose value changed (e.g. Accent → arrows),
//  - a declaration in BINDINGS whose Studio token meaning differs from the source's binding,
// plus componentCss (WorkflowFigure property hooks, identical to the source at defaults). Unchanged
// Studio-private --studio-* properties are added only when a draft's local button appearance
// (buttonVariant, the one inline style that reads them) needs them: privateVars.
// An unchanged draft therefore adds nothing that changes the website.
// Revisit when designCss gains rules or the source rebinds a token. Last touched: 2026-11-21.
// The source fills .site-button with var(--ink); Studio's Primary token owns the fill, so the fill
// follows Primary whenever Primary or Text differs from base.
export const BINDINGS = [{ selector: '.site-button', property: 'background', tokens: ['primary', 'text'] }]
function declarations(css) {
  const out = []
  let media = ''
  for (const m of css.matchAll(/(@media[^{]*)\{|([^{}]+)\{([^{}]*)\}|\}/g)) {
    if (m[1]) { media = m[1].trim(); continue }
    if (!m[2]) { media = ''; continue }
    const selector = m[2].trim()
    for (const part of m[3].split(';')) {
      const at = part.indexOf(':')
      if (at > 0) out.push({ media, selector, property: part.slice(0, at).trim(), value: part.slice(at + 1).trim() })
    }
  }
  return out
}
function serialise(list) {
  let css = '', open = null
  for (const d of list) {
    const key = `${d.media}\u0000${d.selector}`
    if (open?.key !== key) {
      if (open) css += `}${open.media ? '}' : ''}\n`
      css += `${d.media ? d.media + '{' : ''}${d.selector}{`
      open = { key, media: d.media }
    } else css += ';'
    css += `${d.property}:${d.value}`
  }
  return open ? css + `}${open.media ? '}' : ''}\n` : ''
}
// page: a page's own layer over the Shared site layer, which already carries the hooks and --studio-*.
export function siteDesignCss(input, base = defaultDesign, { page = false, privateVars = false } = {}) {
  const d = validateTokens(input, schema.globalTokens), b = validateTokens(base, schema.globalTokens)
  const mine = declarations(designCss(d).replace(componentCss, '')), theirs = declarations(designCss(b).replace(componentCss, ''))
  const key = x => `${x.media}\u0000${x.selector}\u0000${x.property}`
  const before = new Map(theirs.map(x => [key(x), x.value]))
  const changedVars = new Set(mine.filter(x => x.property.startsWith('--') && before.get(key(x)) !== x.value).map(x => x.property))
  const changedTokens = new Set(Object.keys(d).filter(k => d[k] !== b[k]))
  const keep = mine.filter(x => {
    if (!page && privateVars && x.selector === ':root' && x.property.startsWith('--studio-')) return true
    if (before.get(key(x)) !== x.value) return true
    if ([...x.value.matchAll(/var\((--[\w-]+)/g)].some(m => changedVars.has(m[1]))) return true
    return BINDINGS.some(r => r.selector === x.selector && r.property === x.property && r.tokens.some(t => changedTokens.has(t)))
  })
  return serialise(keep) + (page ? '' : componentCss)
}
// WorkflowFigure design properties (workflow-graphic.js) reach the source's pseudo-elements through
// custom properties. Fallbacks are the website's own values (globals.css at 215679e), so a figure
// without overrides renders exactly as the source.
export const componentCss = `.workflow-story li:not(:last-child)::before{background:var(--studio-workflow-line,#b7aa91)}
.workflow-story li>span{border-color:var(--studio-workflow-line,#b7aa91)}
.workflow-figure .workflow-after{border-top-color:var(--studio-workflow-line,#b7aa91)}
.workflow-figure figcaption strong{font-size:calc(clamp(1.9rem,3vw,2.7rem)*var(--studio-workflow-title-scale,1))}
@media(max-width:47.99rem){.workflow-figure[data-studio-mobile-order="before"]{order:-1}}
`

// The site adapter puts every Studio page's design in the one shared stylesheet. Each non-home
// page's rules are scoped to that page's <main data-studio-page> without raising specificity
// (:where), so the cascade inside the page is unchanged and nothing leaks onto other pages.
export function scopeCss(css, page) {
  if (!/^[a-z][a-z0-9-]{1,40}$/.test(page)) throw Error('Invalid page scope.')
  const marker = `main[data-studio-page="${page}"]`
  const scope = selector => selector.split(',').map(part => {
    const s = part.trim()
    if (s === ':root' || s === 'body') return `:where(${marker})`

    return s === '.editorial' || s.startsWith('.editorial ') ? `:where(${marker})${s}` : `:where(${marker}) ${s}`
  }).join(',')
  return css.replace(/(^|[{}])([^{}@]+)\{/g, (_, before, selector) => `${before}${selector.match(/^\s*/)[0]}${scope(selector)}{`)
}
