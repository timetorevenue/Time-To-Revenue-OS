import {nestedTypes} from './nested.js'
// Builds the static colour model used by the WCAG contrast checks (contrast.js and the Python twin
// website_studio_contrast.py). For every text field of every real website component it records the
// chain of elements from the text's container up to the section, with each element's style target
// and the colour/background the site CSS declares on it (contrast-rules.json). Pure and
// deterministic; scripts/studio-contrast-model.mjs writes the result to contrast-model.json.
// Revisit when homepage.json, homepage.css or designCss colour rules change. Last touched: 2026-11-18.
const VOID = new Set(['br', 'img', 'hr', 'input', 'meta', 'link', 'source', 'wbr'])
const EDITABLE = /^(h[123]|p|a|figure|summary)$/
const SIZED = new Set(['h1', 'h2', 'h3', 'p', 'a', 'summary'])

function parseSelector(selector) {
  const steps = []
  let combinator = ' '
  for (const token of selector.replace(/:not\([^)]*\)/g, '').trim().split(/\s+/)) {
    if (token === '>') { combinator = '>'; continue }
    const m = token.match(/^([a-z][a-z0-9-]*)?((?:\.[\w-]+)*)$/i)
    if (!m) throw Error(`Unsupported selector: ${selector}`)
    steps.push({ tag: m[1] || null, classes: m[2] ? m[2].slice(1).split('.') : [], combinator })
    combinator = ' '
  }
  return steps
}
const specificity = steps => steps.reduce((n, s) => n + s.classes.length * 100 + (s.tag ? 1 : 0), 0)
const compound = (node, step) => (!step.tag || node.tag === step.tag) && step.classes.every(c => node.classes.includes(c))
function matches(node, steps, i = steps.length - 1) {
  if (!node || !compound(node, steps[i])) return false
  if (i === 0) return true
  if (steps[i].combinator === '>') return matches(node.parentNode, steps, i - 1)
  for (let up = node.parentNode; up; up = up.parentNode) if (matches(up, steps, i - 1)) return true
  return false
}

// The Studio's section HTML is a fixed, well-formed snapshot (homepage.json); this tokenizer only
// needs elements, classes and the studio-text placeholders.
function parse(html, rootNode) {
  const nodes = [], fields = []
  let current = rootNode
  const counts = {}
  for (const m of html.matchAll(/<(\/?)([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g)) {
    const [, closing, tag, attrs, selfClosing] = m
    if (tag === 'studio-text' && closing) continue
    if (closing) { if (current.tag !== tag) throw Error(`Unbalanced </${tag}>`); current = current.parentNode; continue }
    if (tag === 'studio-text') { fields.push({ field: attrs.match(/data-field="([^"]+)"/)[1], node: current }); continue }
    const node = { tag, classes: (attrs.match(/class="([^"]*)"/)?.[1] || '').split(/\s+/).filter(Boolean), parentNode: current, index: nodes.length }
    if (!nodes.length) node.target = 'section'
    else if (EDITABLE.test(tag)) { const n = counts[tag] || 0; counts[tag] = n + 1; node.target = `${tag}-${n}` }
    nodes.push(node)
    if (!VOID.has(tag) && !selfClosing) current = node
  }
  return { nodes, fields }
}

function declared(node, rules, state = 'default') {
  const out = {}
  const ranked = rules.map((rule, order) => ({ rule, order, steps: parseSelector(rule.selector) }))
    .filter(r => (!r.rule.state || r.rule.state === state) && matches(node, r.steps)).sort((a, b) => specificity(a.steps) + (a.rule.state ? 100 : 0) - specificity(b.steps) - (b.rule.state ? 100 : 0) || a.order - b.order)
  for (const { rule } of ranked) {
    if (rule.color) out.color = rule.color
    if (rule.background) { out.background = rule.background; out.gradient = !!rule.gradient }
  }
  return out
}

function role(node) {
  for (let n = node; n && n.tag !== 'main'; n = n.parentNode) {
    if (n.tag === 'a' && (n.classes.includes('site-button') || n.classes.includes('editorial-link'))) return 'button'
    if (/^h[123]$/.test(n.tag)) return n.tag
  }
  return 'body'
}

export function buildContrastModel(sections, spec) {
  const out = { version: 1, vars: spec.vars, root: { color: spec.root.color, background: spec.root.background }, sections: {} }
  for (const section of [...sections,...nestedTypes.filter(n=>!sections.some(s=>s.type===n.type))]) {
    const main = { tag: 'main', classes: ['editorial'], parentNode: null }
    const { nodes, fields } = parse(section.html, main)
    const rows = nodes.map(node => {
      const row = { tag: node.tag, parent: node.parentNode === main ? null : node.parentNode.index }
      if (node.target) row.target = node.target
      Object.assign(row, declared(node, spec.rules))
      if (!row.gradient) delete row.gradient
      return row
    })
    const textFields = {}
    for (const { field, node } of fields) {
      let target = null, sizeNode = null
      for (let n = node; n !== main; n = n.parentNode) {
        if (target === null && n.target) target = n.target
        if (sizeNode === null && n.target && SIZED.has(n.tag)) sizeNode = n.index
      }
      const fixed = Object.entries(spec.fixedSize || {}).find(([selector]) => matches(node, parseSelector(selector)))
      textFields[field] = { node: node.index, target, role: role(node), sizeNode, ...(fixed ? { fixedSize: fixed[1] } : {}) }
    }
    const states = Object.fromEntries([...new Set(spec.rules.map(r => r.state).filter(Boolean))].map(state => [state, rows.map((row, index) => ({ ...row, ...declared(nodes[index], spec.rules, state) }))]))
    out.sections[section.type] = { nodes: rows, fields: textFields, states }
  }
  // Shared semantic targets mirror the real Header/Footer and sharedCss.
  for(const [type,defs] of Object.entries({
    SharedHeader:{name:['wordmark','var(--ink)',null],navigation:['navigation','var(--ink)',null],cta:['button','var(--paper)','var(--studio-primary)']},
    SharedFooter:{name:['wordmark','var(--paper)',null],tagline:['copy','#ccc5b8',null],explore:['copy','var(--paper)',null],learn:['copy','var(--paper)',null],email:['copy','#ccc5b8',null],learning:['navigation','#ccc5b8',null],legal:['navigation','#ccc5b8',null],cta:['button','var(--paper)',null]}
  })) {
    const nodes=[{tag:'section',parent:null,target:'section',color:type==='SharedHeader'?'var(--ink)':'var(--paper)',background:type==='SharedHeader'?'var(--paper)':'var(--ink)'}],fields={}
    for(const [field,[target,color,background]] of Object.entries(defs)) {
      const index=nodes.length
      nodes.push({tag:target==='button'||target==='navigation'?'a':'p',parent:0,target,color,...(background?{background}:{})})
      fields[field]={node:index,target,role:'body',sizeNode:index,fixedSize:14}
    }
    out.sections[type]={nodes,fields}
  }
  return out
}
