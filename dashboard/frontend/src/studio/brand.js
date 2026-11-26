import {walkItems} from './nested.js'
// Browser twin of website_brand.py compliance checks. Deterministic; no model access.
// Approved rules come from the server; nothing here decides what the brand is.
import { schema, elementTargets } from './design.js'
import { applySectionOperation, typeOf } from './sections.js'

const fontTokens = Object.entries(schema.globalTokens).filter(([, v]) => v.type === 'enum' && v.options.includes('Geist')).map(([k]) => k)
const localSize = { h1: 'h1Size', h2: 'h2Size', h3: 'h3Size', p: 'bodySize' }

function excepted(rules, token, value) {
  return (rules.exceptions || []).some(e => e.token === token && String(e.value).toUpperCase() === String(value).toUpperCase())
}

export function tokenViolations(design, rules, prefix = '') {
  const out = []
  if (!rules) return out
  for (const [token, value] of Object.entries(design || {})) {
    const field = schema.globalTokens[token]
    if (!field || excepted(rules, token, value)) continue
    if (field.type === 'colour') {
      const v = String(value).toUpperCase()
      if (!rules.colours.includes(v)) out.push({ token: prefix + token, value, rule: 'Colour is not in the approved brand palette.' })
      else if (rules.no_fill.tokens.includes(token) && rules.no_fill.colours.includes(v)) out.push({ token: prefix + token, value, rule: rules.no_fill.reason })
      else if (rules.not_core.tokens.includes(token) && rules.not_core.colours.includes(v)) out.push({ token: prefix + token, value, rule: rules.not_core.reason })
    } else if (fontTokens.includes(token) && value !== 'inherit' && rules.fonts.length && !rules.fonts.includes(value)) {
      out.push({ token: prefix + token, value, rule: `Brand fonts are ${rules.fonts.join(', ')}.` })
    } else if (rules.ranges[token]) {
      const [low, high] = rules.ranges[token]
      if (value < low || value > high) out.push({ token: prefix + token, value, rule: `Brand range is ${low}–${high}px.` })
    }
  }
  return out
}

function tagOf(type, target) {
  const section = typeOf(type) // any page's section type (Stage 3)
  return section ? elementTargets(section).find(t => t.id === target)?.tag || '' : ''
}

// sectionId labels the violation (an instance ID); type selects the component's element tags.
export function styleViolations(sectionId, target, values, rules, type = sectionId) {
  const tag = tagOf(type, target), mapped = {}
  for (const [key, value] of Object.entries(values || {})) {
    if (value === null || value === undefined) continue
    if (['background', 'text', 'borderColor'].includes(key)) mapped[key] = value
    else if (key === 'radius' && tag === 'a') mapped.buttonRadius = value
    else if (key === 'fontSize' && localSize[tag]) mapped[localSize[tag]] = value
  }
  return tokenViolations(mapped, rules, `${sectionId}.${target}.`)
}

// Instance types come from the page the proposal applies to, including instances it adds.
// A duplicate introduces copies of its source's local styles, so those are checked too.
export function proposalViolations(proposal, rules, data = null) {
  if (!rules) return []
  const out = tokenViolations(proposal?.design || {}, rules)
  for(const op of proposal?.components||[])if(op.values?.graphic?.signal)out.push(...tokenViolations({accent:op.values.graphic.signal},rules,op.component+'.graphic.'))
  let content = data?.content || []
  const types = () => Object.fromEntries([...walkItems(content)].map(i => [i.props.id, i.type]))
  for (const op of proposal?.sections || []) {
    try { content = applySectionOperation(content, op) } catch { continue }
    if (op.op === 'duplicate') {
      const copy = content.find(i => i.props.id === op.id)
      for (const [target, values] of Object.entries(copy?.props.styles || {})) out.push(...styleViolations(op.id, target, values, rules, copy.type))
    }
  }
  const typeById = types()
  for (const op of proposal?.styles || []) if (op.values) out.push(...styleViolations(op.section, op.target, op.values, rules, typeById[op.section] ?? op.section))
  return out
}

// Every current deviation from the approved brand: rendered as visible owner overrides.
export function designViolations(data, rules) {
  if (!rules) return []
  const out = tokenViolations(data.root.props.design, rules)
  for (const item of walkItems(data.content)) for (const [target, values] of Object.entries(item.props.styles || {})) out.push(...styleViolations(item.props.id, target, values, rules, item.type))
  return out
}
