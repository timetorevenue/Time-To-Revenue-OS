// Deterministic WCAG 2.x AA text-contrast checks for a Studio page (Stage 2.1). Browser twin of
// dashboard/backend/website_studio_contrast.py (parity-tested). No model access, no DOM access:
// every text field's colour and background are resolved from contrast-model.json (the site CSS)
// through global design tokens and local overrides, including inherited ones (a section's text or
// background override reaches the elements inside it, exactly as the canvas renders it).
// Revisit when the colour model, design tokens or local style semantics change. Last touched: 2026-11-18.
import {walkItems,migrateItem} from './nested.js'
import model from './contrast-model.json' with { type: 'json' }
import { schema } from './design.js'
import { typeOf } from './sections.js'
import { applyProposal } from './state.js'
import { designViolations } from './brand.js'

export { model as contrastModel }
export const AA = { normal: 4.5, large: 3 }
const MIN_HEADING = { h1: 48, h2: 35, h3: 20 }

function channel(c) {
  c /= 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}
export function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}
export function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}
// Displayed ratios are floored, so a value shown as 4.50:1 always passes 4.5:1.
export const shownRatio = ratio => Math.floor(ratio * 100) / 100
export const formatRatio = ratio => `${shownRatio(ratio).toFixed(2)}:1`

const varName = expr => expr.match(/^var\(--([\w-]+)\)$/)?.[1]

// Resolves a declared value on node `index` (which may be a var) to a hex colour and its origin.
function resolve(expr, index, nodes, styles, design) {
  const name = varName(expr)
  if (!name) return { hex: expr.toLowerCase(), token: null }
  if (name === 'ink' || name === 'muted') {
    for (let i = index; i !== null; i = nodes[i].parent) {
      const text = nodes[i].target && styles[nodes[i].target]?.text
      if (text) return { hex: text.toLowerCase(), token: null, inherited: nodes[i].target }
    }
  }
  const token = model.vars[name]
  return { hex: String(design[token]).toLowerCase(), token }
}

function foreground(start, nodes, styles, design) {
  for (let i = start; i !== null; i = nodes[i].parent) {
    const node = nodes[i], local = node.target ? styles[node.target] || {} : {}
    if (local.text) return { hex: local.text.toLowerCase(), kind: 'override', target: node.target, token: null }
    if (node.tag === 'a' && local.buttonVariant) return { ...resolve(local.buttonVariant === 'primary' ? 'var(--paper)' : 'var(--ink)', i, nodes, styles, design), kind: 'variant', target: node.target }
    if (node.color) return { ...resolve(node.color, i, nodes, styles, design), kind: 'css', target: node.target || null }
  }
  return { ...resolve(model.root.color, null, nodes, styles, design), kind: 'page', target: null }
}

function background(start, nodes, styles, design) {
  for (let i = start; i !== null; i = nodes[i].parent) {
    const node = nodes[i], local = node.target ? styles[node.target] || {} : {}
    if (local.background) return { hex: local.background.toLowerCase(), kind: 'override', target: node.target, token: null }
    if (node.tag === 'a' && local.buttonVariant && local.buttonVariant !== 'outline') return { ...resolve(`var(--studio-${local.buttonVariant})`, i, nodes, styles, design), kind: 'variant', target: node.target }
    if (node.tag === 'a' && local.buttonVariant === 'outline') continue
    if (node.background) return { ...resolve(node.background, i, nodes, styles, design), kind: 'css', target: node.target || null, ...(node.gradient ? { gradient: true } : {}) }
  }
  return { ...resolve(model.root.background, null, nodes, styles, design), kind: 'page', target: null }
}

function textSize(spec, nodes, styles, design) {
  const local = spec.sizeNode !== null ? styles[nodes[spec.sizeNode].target] || {} : {}
  const roleKey = spec.role === 'button' ? 'button' : spec.role === 'body' ? 'body' : spec.role
  const size = local.fontSize ?? spec.fixedSize ?? (MIN_HEADING[spec.role] ? Math.min(design[`${roleKey}Size`], MIN_HEADING[spec.role]) : design[`${roleKey}Size`])
  const weight = local.fontWeight ?? design[`${roleKey}Weight`]
  return { size, weight, large: size >= 24 || (size >= 18.66 && weight >= 700) }
}

// One check per visible text field of every instance. `id` is `<instance>:<field>`.
export function contrastChecks(data, state = 'default') {
  const design = data.root.props.design, out = []
  for (const item of walkItems(data.content.map(i=>migrateItem(i)))) {
    const source = model.sections[item.type]
    if (!source) throw Error(`Missing contrast model for ${item.type}`)
    const section = { ...source, nodes: source.states?.[state] || source.nodes }
    for (const field of Object.keys(item.props).filter(key => /^text\d+$/.test(key))) {
      if (!section.fields[field]) throw Error(`Missing contrast field ${item.type}:${field}`)
    }
    const styles = item.props.styles || {}
    for (const [field, spec] of Object.entries(section.fields)) {
      const text = String(item.props[field] ?? '').trim()
      if (!text) continue
      const fg = foreground(spec.node, section.nodes, styles, design), bg = background(spec.node, section.nodes, styles, design)
      const { size, weight, large } = textSize(spec, section.nodes, styles, design)
      const ratio = contrastRatio(fg.hex, bg.hex), required = large ? AA.large : AA.normal
      out.push({ id: `${item.props.id}:${field}`, section: item.props.id, type: item.type, field, target: spec.target, text: text.length > 48 ? `${text.slice(0, 47)}…` : text,
        foreground: fg, background: bg, ratio, required, large, size, weight, pass: ratio >= required })
    }
  }
  return out
}

export const contrastFailures = data => [...new Map(['default', 'hover'].flatMap(state => contrastChecks(data, state).filter(c => !c.pass)).map(check => [check.id, check])).values()]

// Failures present in `next` that `base` did not have (by instance and field).
export function newContrastFailures(base, next) {
  const before = new Set(contrastFailures(base).map(f => f.id))
  return contrastFailures(next).filter(f => !before.has(f.id))
}

// A stable key for the exact set of known failures (acknowledgement resets when it changes).
export const failureKey = failures => failures.map(f => `${f.id}@${shownRatio(f.ratio)}`).sort().join('|')

export function targetLabel(type, target) {
  if (target === 'section') return 'Whole section'
  const shared=typeOf(type)?.targets?.find(t=>t.id===target)
  if(shared)return shared.label
  const [tag, n] = target.split('-')
  return `${tag === 'a' ? 'Button / link' : tag.toUpperCase()} ${Number(n) + 1}`
}

export function originText(check) {
  const part = (side, s) => {
    if (s.kind === 'override') return `${side} is a local override on ${targetLabel(check.type, s.target)}`
    if (s.kind === 'variant') return `${side} comes from the button appearance`
    if (s.inherited) return `${side} inherits the local text override on ${targetLabel(check.type, s.inherited)}`
    if (s.token) return `${side} uses global ${schema.globalTokens[s.token]?.label || s.token}`
    return `${side} is the site component's fixed colour ${s.hex}${s.gradient ? ' (lightest gradient stop)' : ''}`
  }
  return `${part('Text', check.foreground)}; ${part('background', check.background)}.`
}

function mix(hex, toward, t) {
  const a = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)), b = toward === 'black' ? [0, 0, 0] : [255, 255, 255]
  return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, '0')).join('')
}
// The least change to `hex` (towards black or white) that reaches `required` against `other`.
export function nearestPassing(hex, other, required) {
  let best = null
  for (const toward of ['black', 'white']) {
    if (contrastRatio(mix(hex, toward, 1), other) < required) continue
    let lo = 0, hi = 1
    for (let i = 0; i < 24; i++) { const mid = (lo + hi) / 2; if (contrastRatio(mix(hex, toward, mid), other) >= required) hi = mid; else lo = mid }
    const candidate = mix(hex, toward, hi)
    if (contrastRatio(candidate, other) >= required && (!best || hi < best.t)) best = { hex: candidate, t: hi }
  }
  return best?.hex || null
}

const distance = (a, b) => { const x = [1, 3, 5].map(i => parseInt(a.slice(i, i + 2), 16)), y = [1, 3, 5].map(i => parseInt(b.slice(i, i + 2), 16)); return Math.hypot(...x.map((v, i) => v - y[i])) }

// Accessible alternatives for one failure. Each is a validated style operation (the same proposal
// schema as every other edit) that was applied and re-checked here: it fixes this failure and
// introduces no other failure. With approved brand rules, alternatives that would add a brand
// violation (off-palette, or a colour the rules forbid in that role) are marked and ranked last.
// Order: remove the offending override, brand colours closest to the current colour, the nearest
// passing colour, then the design's own colours.
export function contrastAlternatives(data, failure, brandRules = null, limit = 4) {
  const before = new Set(contrastFailures(data).map(f => f.id))
  const brandBefore = new Set(designViolations(data, brandRules).map(v => `${v.token}=${v.value}`))
  const brandColours = brandRules?.colours || null
  const item = data.content.find(i => i.props.id === failure.section)
  if (!item) return []
  const styles = item.props.styles || {}
  const approved = new Set((brandColours || []).map(c => c.toLowerCase()))
  const tokens = [...new Set(Object.entries(schema.globalTokens).filter(([, f]) => f.type === 'colour').map(([k]) => String(data.root.props.design[k]).toLowerCase()))]
  const candidates = []
  const push = (kind, target, values, label, colour = null) => candidates.push({ kind, target, values, label, colour })
  const fg = failure.foreground, bg = failure.background
  if (fg.kind === 'override') push('reset', fg.target, { text: null }, `Remove the text override on ${targetLabel(item.type, fg.target)}`)
  if (bg.kind === 'override') push('reset', bg.target, { background: null }, `Remove the background override on ${targetLabel(item.type, bg.target)}`)
  if (fg.kind === 'variant' || bg.kind === 'variant') push('reset', failure.target, { buttonVariant: null }, `Use the site's button appearance on ${targetLabel(item.type, failure.target)}`)
  const textTarget = failure.target, bgTarget = bg.kind === 'override' ? bg.target : failure.target
  const fgPool = [...[...approved].sort((a, b) => distance(a, fg.hex) - distance(b, fg.hex)).map(c => ['brand', c]), ['nearest', nearestPassing(fg.hex, bg.hex, failure.required)], ...tokens.map(c => ['design', c])]
  const bgPool = [...[...approved].sort((a, b) => distance(a, bg.hex) - distance(b, bg.hex)).map(c => ['brand', c]), ['nearest', nearestPassing(bg.hex, fg.hex, failure.required)], ...tokens.map(c => ['design', c])]
  for (const [kind, c] of fgPool) if (c) push(kind, textTarget, { text: c }, `Text ${c} on ${targetLabel(item.type, textTarget)}`, c)
  for (const [kind, c] of bgPool) if (c) push(kind, bgTarget, { background: c }, `Background ${c} on ${targetLabel(item.type, bgTarget)}`, c)
  // A colour that comes from a global token can also be fixed globally (it then affects every section).
  for (const [side, source, pool] of [['Text', fg, fgPool], ['Background', bg, bgPool]]) {
    if (source.kind !== 'css' && source.kind !== 'page' || !source.token) continue
    for (const [kind, c] of pool) if (c && kind !== 'design') candidates.push({ kind, target: null, design: { [source.token]: c }, colour: c,
      label: `Global ${schema.globalTokens[source.token]?.label || source.token} ${c} (every section that uses it)` })
  }
  const out = [], seen = new Set()
  for (const candidate of candidates) {
    const key = JSON.stringify([candidate.target, candidate.values, candidate.design])
    if (seen.has(key)) continue
    seen.add(key)
    if (candidate.values && Object.entries(candidate.values).some(([k, v]) => v === null && styles[candidate.target]?.[k] === undefined)) continue
    const operation = candidate.design ? { summary: candidate.label, edits: [], design: candidate.design } : { summary: candidate.label, edits: [], styles: [{ section: failure.section, target: candidate.target, values: candidate.values }] }
    let next
    try { next = applyProposal(data, operation) } catch { continue }
    const after = contrastChecks(next)
    const fixed = after.find(c => c.id === failure.id)
    if (fixed && !fixed.pass) continue
    if (after.some(c => !c.pass && !before.has(c.id))) continue
    const brandAdded = brandRules ? designViolations(next, brandRules).filter(v => !brandBefore.has(`${v.token}=${v.value}`)) : []
    out.push({ ...candidate, operation, ratio: fixed?.ratio ?? null, onBrand: brandRules ? !brandAdded.length : null, brandRule: brandAdded[0]?.rule || null })
  }
  const rank = a => (a.kind === 'reset' ? 0 : 1) + (a.onBrand === false ? 2 : 0)
  return out.map((a, i) => [a, i]).sort(([a, i], [b, j]) => rank(a) - rank(b) || i - j).slice(0, limit).map(([a]) => a)
}

// Checks for the text inside one style target of one instance (the section target covers all).
export function targetChecks(data, instance, target) {
  const item = data.content.find(i => i.props.id === instance)
  if (!item || !typeOf(item.type)) return []
  return contrastChecks({ ...data, content: [item] }).filter(c => target === 'section' || c.target === target)
}
