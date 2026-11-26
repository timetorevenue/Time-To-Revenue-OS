// TTROS Website Studio live-source adapter: the real Next.js source (pinned Git commit) <-> the
// Studio element model (src/studio/live/model.js). Deterministic, offline, zero model calls.
//
// importSource(files) parses every page, shared component and blog post body with Babel and turns
// each rendered JSX element into a model node. Source code is never copied into drafts: attribute
// expressions, event handlers, data-driven lists and conditions become references into `codes`.
// Static values are evaluated in a sandbox (node:vm; no Date, randomness, timers, I/O or imports
// beyond the pinned source) so lists over local constants (navigation, cards, steps) and small local
// wrapper components (<P>, <SectionHeading>) are unrolled into individually editable elements.
//
// generate(snapshot, docs) writes source back. Anything still identical to the import is emitted
// as the original source text (byte for byte); only edited elements are regenerated, as literal
// JSX. A document with no changes is not rewritten at all. mode 'instrument' regenerates
// everything and tags elements with data-sid for the import capture and parity proof.
// Repeated parts whose entries are not static (component props such as FAQ items, per-route data
// lists) import as one editable template (`Map`): editing it changes every entry; its data bindings
// are scoped to the template and cannot leave it. Components that take props (FaqAccordion) import
// as template documents edited through the pages that use them. Data modules (content/blog.ts,
// content/resources.ts) import as records (`Rec`) whose string fields are edited in place.
// Revisit: when the website's source structure or the model schema changes. Last touched: 2026-11-22.
import {parse} from '@babel/parser'
import traverseModule from '@babel/traverse'
import vm from 'node:vm'
import crypto from 'node:crypto'
import {transformSync} from 'esbuild'
import {JSDOM} from 'jsdom'
import {SCHEMA, TAGS, VOID, attrAllowed, styleClass, treeCss, walk} from '../../src/studio/live/model.js'
import {KIT_CODES} from '../../src/studio/live/pages.js'
import {landingSource, postSource} from './site-pages.mjs'

const traverse = traverseModule.default || traverseModule
const sha = text => crypto.createHash('sha256').update(text).digest('hex')
const canonical = value => JSON.stringify(value, (k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(key => [key, v[key]])) : v))
const OWN_SKIP = new Set(['kids', 'alt', 'from', 'label', 'show'])
export const ownFingerprint = item => sha(canonical({type: item.type, props: Object.fromEntries(Object.entries(item.props).filter(([k, v]) => !OWN_SKIP.has(k) && v !== undefined))}))

// ---------------------------------------------------------------- documents and routes
export function docKey(file) {
 if (file === 'app/page.tsx') return 'home'
 if (file === 'app/not-found.tsx') return 'not-found'
 let m = file.match(/^app\/(.+)\/page\.tsx$/)
 if (m) return m[1].replace(/\[([a-z]+)\]/g, '$1').replace(/[^a-z0-9]+/gi, '-').toLowerCase()
 m = file.match(/^components\/(.+)\.tsx$/)
 if (m) return 'c-' + m[1].replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase()
 m = file.match(/^content\/blog-posts\/(.+)\.tsx$/)
 if (m) return 'post-' + m[1].slice(0, 42)
 return file.replace(/\.[a-z]+$/, '').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 48)
}
const routeOf = file => file === 'app/page.tsx' ? '/' : file === 'app/not-found.tsx' ? '/_not-found' : '/' + file.replace(/^app\//, '').replace(/\/page\.tsx$/, '')
const resolveImport = (from, source, files) => {
 let base = null
 if (source.startsWith('@/')) base = source.slice(2)
 else if (source.startsWith('.')) { const parts = from.split('/').slice(0, -1); for (const p of source.split('/')) { if (p === '..') parts.pop(); else if (p !== '.') parts.push(p) } base = parts.join('/') }
 if (base === null) return null
 for (const ext of ['', '.tsx', '.ts', '.jsx', '.js', '/index.tsx', '/index.ts']) if (files.has(base + ext)) return base + ext
 return null
}

// ---------------------------------------------------------------- sandboxed static evaluation
const quietMath = Object.freeze(Object.fromEntries(Object.getOwnPropertyNames(Math).filter(k => k !== 'random').map(k => [k, Math[k]])))
const JSX_MARK = Symbol.for('ttros.studio.jsx')
function sandbox() {
 const context = vm.createContext({Math: quietMath, JSON, String, Number, Boolean, Array, Object, RegExp, Map, Set, Symbol, Error, TypeError,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent, encodeURI, decodeURI, Infinity, NaN, undefined,
  console: {log() {}, warn() {}, error() {}, info() {}}})
 vm.runInContext('globalThis.__h=function(t,p){var c=[].slice.call(arguments,2);return {$$jsx:true,t:t,p:p,c:c}};globalThis.__F="__fragment"', context)
 return context
}
function stripTs(code) {
 return transformSync(code, {loader: 'tsx', jsx: 'transform', jsxFactory: '__h', jsxFragment: '__F', format: 'esm', target: 'es2020', minifyWhitespace: false}).code
}
const isJsxValue = v => v && typeof v === 'object' && v.$$jsx === true
export function plainData(value, depth = 0) {
 if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return true
 if (depth > 12 || typeof value !== 'object' || isJsxValue(value)) return false
 if (Array.isArray(value)) return value.length <= 2000 && value.every(v => plainData(v, depth + 1))
 const proto = Object.getPrototypeOf(value)
 if (proto !== null && proto.constructor?.name !== 'Object') return false
 return Object.values(value).every(v => v === undefined || plainData(v, depth + 1))
}
class Evaluator {
 constructor() { this.context = sandbox(); this.cache = new Map() }
 compile(expr) {
  if (!this.cache.has(expr)) {
   let fn = null
   try {
    const js = stripTs(`(${expr});`).trim().replace(/;$/, '')
    fn = vm.runInContext(`(function(__s,__e){with(__s){with(__e){return (${js});}}})`, this.context, {timeout: 200})
   } catch { fn = null }
   this.cache.set(expr, fn)
  }
  return this.cache.get(expr)
 }
 // Evaluate a TS expression with the given scopes; {ok, value}. Never throws.
 run(expr, scope, env = {}) {
  const fn = this.compile(expr)
  if (!fn) return {ok: false}
  try {
   const script = new vm.Script('__run()')
   this.context.__run = () => fn(scope, env)
   const value = script.runInContext(this.context, {timeout: 200})
   return {ok: true, value}
  } catch { return {ok: false} } finally { delete this.context.__run }
 }
 // Bind function parameters (with destructuring/defaults) to argument values.
 bind(paramsSource, names, args, scope) {
  const fn = this.compile(`((${paramsSource}) => ({${names.join(',')}}))`)
  if (!fn) return null
  try {
   const make = fn(scope, {})
   this.context.__args = args
   this.context.__make = make
   return vm.runInContext('__make.apply(null, __args)', this.context, {timeout: 200})
  } catch { return null } finally { delete this.context.__args; delete this.context.__make }
 }
}

// Module scopes: each file's top-level constants and functions, evaluated independently (a failing
// declaration simply stays unknown). Imports bind to other pinned files' scopes; packages do not.
function moduleScope(file, files, ev, cache, stack = []) {
 if (cache.has(file)) return cache.get(file)
 const result = {values: {}, local: new Set(), imported: new Map(), exportsDefault: null}
 cache.set(file, result)
 if (stack.includes(file)) return result
 const text = files.get(file)
 let ast
 try { ast = parse(text, {sourceType: 'module', plugins: ['typescript', 'jsx']}) } catch { return result }
 for (const node of ast.program.body) {
  if (node.type !== 'ImportDeclaration' || node.importKind === 'type') continue
  const target = resolveImport(file, node.source.value, files)
  const other = target && /\.(tsx?|jsx?)$/.test(target) ? moduleScope(target, files, ev, cache, [...stack, file]) : null
  for (const spec of node.specifiers) {
   const local = spec.local.name
   const imported = spec.type === 'ImportDefaultSpecifier' ? 'default' : spec.type === 'ImportNamespaceSpecifier' ? '*' : spec.imported.name || spec.imported.value
   result.imported.set(local, {source: node.source.value, file: target, imported})
   if (!other) continue
   if (imported === '*') { result.values[local] = {...other.values}; continue }
   const name = imported === 'default' ? other.exportsDefault : imported
   if (name && Object.hasOwn(other.values, name)) result.values[local] = other.values[name]
  }
 }
 const declarations = []
 for (const raw of ast.program.body) {
  let node = raw
  if (node.type === 'ExportNamedDeclaration' && node.declaration) node = node.declaration
  if (node.type === 'ExportDefaultDeclaration') {
   if (node.declaration.type === 'FunctionDeclaration' && node.declaration.id) { result.exportsDefault = node.declaration.id.name; node = node.declaration }
   else if (node.declaration.type === 'Identifier') { result.exportsDefault = node.declaration.name; continue } else continue
  }
  if (node.type === 'FunctionDeclaration' && node.id) { result.local.add(node.id.name); declarations.push({kind: 'fn', name: node.id.name, code: text.slice(node.start, node.end)}) }
  if (node.type === 'VariableDeclaration') for (const d of node.declarations) {
   if (d.id.type !== 'Identifier') continue
   result.local.add(d.id.name)
   if (d.init) declarations.push({kind: 'var', name: d.id.name, code: text.slice(d.init.start, d.init.end)})
  }
 }
 // Functions first (hoisted), then constants in source order; each sees what succeeded before it.
 for (const d of declarations.filter(d => d.kind === 'fn')) {
  try {
   const js = stripTs(d.code)
   const fn = vm.runInContext(`(function(__s){with(__s){ ${js}; return ${d.name}; }})`, ev.context, {timeout: 200})
   result.values[d.name] = fn(new Proxy(result.values, {has: (t, k) => Object.hasOwn(t, k), get: (t, k) => t[k]}))
  } catch { /* stays unknown */ }
 }
 for (const d of declarations.filter(d => d.kind === 'var')) {
  const r = ev.run(d.code, result.values)
  if (r.ok && r.value !== undefined) result.values[d.name] = r.value
 }
 return result
}

// ---------------------------------------------------------------- JSX → model
function cleanJsxText(value) {
 const lines = value.split(/\r\n|\n|\r/)
 let last = 0
 lines.forEach((line, i) => { if (/[^ \t]/.test(line)) last = i })
 let out = ''
 lines.forEach((line, i) => {
  let s = line.replace(/\t/g, ' ')
  if (i !== 0) s = s.replace(/^[ ]+/, '')
  if (i !== lines.length - 1) s = s.replace(/[ ]+$/, '')
  if (s) { if (i !== last) s += ' '; out += s }
 })
 return out
}
const ATTR_RENAME = {'xlink:href': 'xlinkHref', 'xml:space': 'xmlSpace', class: 'className', for: 'htmlFor'}
const unwrapParens = path => { let p = path; while (p.node && (p.node.type === 'ParenthesizedExpression' || p.node.type === 'TSAsExpression' || p.node.type === 'TSNonNullExpression')) p = p.get('expression'); return p }
const CLASS_OK = /^[^<>"'`{}\\]{0,2000}$/
const isJsx = node => !!node && ['JSXElement', 'JSXFragment'].includes(node.type)
const jsxLike = path => { const p = unwrapParens(path); return isJsx(p.node) || p.node?.type === 'NullLiteral' || (p.node?.type === 'Identifier' && p.node.name === 'undefined') }
const textLike = path => { const p = unwrapParens(path); return p.node?.type === 'StringLiteral' || (p.node?.type === 'TemplateLiteral' && !p.node.expressions.length) }
// A condition branch: JSX, text, or a nested condition of those (a ? X : b ? Y : Z).
const condLike = path => { const p = unwrapParens(path); return jsxLike(p) || textLike(p) || (p.isConditionalExpression() && condLike(p.get('consequent')) && condLike(p.get('alternate'))) || (p.isLogicalExpression({operator: '&&'}) && condLike(p.get('right'))) }
const textOf = node => node.type === 'StringLiteral' ? node.value : node.quasis[0].value.cooked
// `if (test) return null` (or an empty return) inside a list callback.
const skipsEntry = s => s.isIfStatement() && !s.node.alternate && (() => {
 const c = s.node.consequent
 const ret = c.type === 'BlockStatement' ? (c.body.length === 1 ? c.body[0] : null) : c
 return ret?.type === 'ReturnStatement' && (!ret.argument || ret.argument.type === 'NullLiteral')
})()
// a.b.c on an identifier → ['a', 'b', 'c'] (data paths shown per entry in the canvas).
const memberPath = path => {
 const out = []
 let p = unwrapParens(path)
 if (p.isJSXExpressionContainer?.()) p = unwrapParens(p.get('expression'))
 while (p.isMemberExpression() && !p.node.computed) { out.unshift(p.node.property.name); p = p.get('object') }
 return p.isIdentifier() ? [p.node.name, ...out] : null
}

class DocImporter {
 constructor(file, text, ast, scope, ctx) {
  Object.assign(this, {file, text, ast, scope, ctx})
  this.key = docKey(file)
  this.n = 0
  this.c = 0
  this.uses = new Set()
 }
 id() { return `${this.key}_${(this.n++).toString(36)}` }
 code(text, kind, extra = {}) {
  const ref = `${this.key}~${(this.c++).toString(36)}`
  this.ctx.codes[ref] = {doc: this.file, text, kind, ...extra}
  return ref
 }
 src(node) { return this.text.slice(node.start, node.end) }
 // Identifiers an expression reads that are bound inside `within` (callback params, component props).
 dependsOn(path, within) {
  if (!within) return false
  let hit = false
  const check = p => {
   const binding = p.scope.getBinding(p.node.name)
   if (binding && within.some(w => binding.path === w || binding.path.isDescendant(w))) hit = true
  }
  if (path.isIdentifier()) check(path)
  path.traverse({ReferencedIdentifier: check, JSXIdentifier(p) { if (p.parentPath.isJSXOpeningElement() || p.parentPath.isJSXClosingElement() || p.parentPath.isJSXMemberExpression()) { const b = p.scope.getBinding(p.node.name); if (b && within.some(w => b.path === w || b.path.isDescendant(w))) hit = true } }})
  return hit
 }
 env(frame) { return frame?.env || {} }
 evaluate(path, frame) {
  const src = this.src(path.node)
  return this.ctx.ev.run(src, this.scope.values, {...this.ctx.locals, ...this.env(frame)})
 }
 pristine(item, path, extra = {}) {
  this.ctx.nodes[item.props.id] = {doc: this.file, own: ownFingerprint(item), ...extra, ...(path && !extra.lit ? {span: [path.node.start, path.node.end]} : {})}
 }
 // `frame` = {within:[paths], env, lit:true} while unrolling a list or expanding a local component.
 children(paths, frame) {
  const out = []
  for (const p of paths) out.push(...this.child(p, frame))
  return out
 }
 child(path, frame) {
  const node = path.node
  if (node.type === 'JSXText') {
   const text = cleanJsxText(node.value)
   if (!text) return []
   const item = {type: 'Txt', props: {id: this.id(), text}}
   this.pristine(item, path, frame ? {lit: true} : {})
   return [item]
  }
  if (node.type === 'JSXFragment') return this.children(path.get('children'), frame)
  if (node.type === 'JSXElement') return this.element(path, frame)
  if (node.type === 'JSXSpreadChild') return this.opaque(path, frame)
  if (node.type !== 'JSXExpressionContainer') return []
  const expr = unwrapParens(path.get('expression'))
  if (expr.node.type === 'JSXEmptyExpression') return []
  if (isJsx(expr.node)) return this.child(expr, frame)
  if (expr.node.type === 'StringLiteral' || (expr.node.type === 'TemplateLiteral' && !expr.node.expressions.length)) {
   const text = expr.node.type === 'StringLiteral' ? expr.node.value : expr.node.quasis[0].value.cooked
   const item = {type: 'Txt', props: {id: this.id(), text}}
   this.pristine(item, path, frame ? {lit: true} : {})
   return [item]
  }
  if (expr.isCallExpression()) { const unrolled = this.unroll(path, expr, frame); if (unrolled) return unrolled }
  if (expr.isLogicalExpression({operator: '&&'}) && jsxLike(expr.get('right'))) return this.condition(path, expr.get('left'), expr.get('right'), null, frame)
  if (expr.isConditionalExpression() && (jsxLike(expr.get('consequent')) || jsxLike(expr.get('alternate')) || (textLike(expr.get('consequent')) && textLike(expr.get('alternate'))))) return this.condition(path, expr.get('test'), expr.get('consequent'), expr.get('alternate'), frame)
  const dependent = this.dependsOn(expr, frame?.within)
  const r = this.evaluate(expr, frame)
  if (r.ok && (typeof r.value === 'string' || typeof r.value === 'number')) {
   const props = {id: this.id(), text: String(r.value)}
   if (!dependent) props.x = {text: this.code(this.src(expr.node), 'text', {value: String(r.value)})}
   const item = {type: 'Txt', props}
   this.pristine(item, path, frame ? {lit: true} : {})
   return [item]
  }
  if (dependent) {
   if (r.ok && (r.value === null || r.value === undefined || typeof r.value === 'boolean')) return []
   if (r.ok && frame?.children && r.value === frame.children.token) return frame.children.nodes
   if (frame?.template) return this.opaque(path, frame, {scope: frame.template, ...(memberPath(expr) ? {path: memberPath(expr)} : {})})
   throw new Unsupported('dependent expression')
  }
  const bind = !frame && this.dataBinding(expr)
  return this.opaque(path, frame, bind ? {bind} : {})
 }
 opaque(path, frame, extra = {}) {
  const item = {type: 'Code', props: {id: this.id(), c: this.code(this.src(path.node), 'child', extra)}}
  this.pristine(item, path, frame ? {lit: true} : {})
  return [item]
 }
 textItem(node) {
  const item = {type: 'Txt', props: {id: this.id(), text: textOf(node)}}
  this.pristine(item, null, {lit: true})
  return [item]
 }
 branchOf(b, frame) {
  if (!b) return []
  const u = unwrapParens(b)
  if (isJsx(u.node)) return this.child(u, frame)
  if (textLike(u)) return this.textItem(u.node)
  if (u.isConditionalExpression() && condLike(u)) return this.condition(u, u.get('test'), u.get('consequent'), u.get('alternate'), frame, true)
  if (u.isLogicalExpression({operator: '&&'}) && condLike(u.get('right'))) return this.condition(u, u.get('left'), u.get('right'), null, frame, true)
  return []
 }
 condition(path, test, yes, no, frame, nested = false) {
  let scoped = false
  if (this.dependsOn(test, frame?.within)) {
   const r = this.evaluate(test, frame)
   if (!r.ok && !frame?.template) throw new Unsupported('dependent condition')
   if (r.ok) {
    return this.branchOf(r.value ? yes : no, frame)
   }
   scoped = true
  }
  const branch = b => this.branchOf(b, frame)
  const r = this.evaluate(test, frame)
  const item = {type: 'Cond', props: {id: this.id(), c: this.code(this.src(test.node), 'test', {op: no ? '?' : '&&', ...(scoped ? {scope: frame.template} : {}), ...(r.ok && plainData(r.value) ? {value: !!r.value} : {})}), kids: [], alt: []}}
  item.props.kids = branch(yes)
  item.props.alt = branch(no)
  if (r.ok) item.props.show = r.value ? 'kids' : 'alt' // state-dependent: set from the import render
  this.pristine(item, path, frame || nested ? {lit: true} : {}) // a nested condition is re-emitted in braces
  return [item]
 }
 // {LIST.map((item, i) => <JSX/>)} over a list declared in this file → one editable copy per entry;
 // over anything else (component props, per-route data) → one editable template (`Map`).
 unroll(container, call, frame) {
  const callee = unwrapParens(call.get('callee'))
  if (!callee.isMemberExpression() || callee.node.computed || callee.node.property.name !== 'map') return null
  const args = call.get('arguments')
  if (args.length !== 1 || !(args[0].isArrowFunctionExpression() || args[0].isFunctionExpression())) return null
  const fn = args[0]
  const object = unwrapParens(callee.get('object'))
  let body = fn.get('body')
  const before = []
  if (body.isBlockStatement()) {
   const statements = body.get('body')
   const last = statements.at(-1)
   if (!last?.isReturnStatement() || !last.node.argument) return null
   for (const s of statements.slice(0, -1)) { if (!s.isVariableDeclaration() && !skipsEntry(s)) return null; before.push(s) }
   body = unwrapParens(last.get('argument'))
  } else body = unwrapParens(body)
  if (!isJsx(body.node)) return null
  let local = object.isArrayExpression()
  if (object.isIdentifier()) {
   const binding = object.scope.getBinding(object.node.name)
   local = (!!binding && binding.kind !== 'module' && binding.path.isVariableDeclarator()) || (!!frame?.env && Object.hasOwn(frame.env, object.node.name))
  }
  const list = local ? this.evaluate(object, frame) : {ok: false}
  if (local && list.ok && Array.isArray(list.value) && list.value.length <= 400 && plainData(list.value)) {
   const out = this.unrollList(container, fn, body, before, list.value, frame)
   if (out) return out
  }
  return this.template(container, fn, object, body, frame)
 }
 unrollList(container, fn, body, before, entries, frame) {
  const names = Object.keys(fn.scope.bindings).filter(n => fn.scope.bindings[n].kind === 'param')
  const params = fn.node.params.map(p => this.src(p)).join(', ')
  const within = [fn, ...(frame?.within || [])]
  const out = []
  const snapshot = {n: this.n, c: this.c, nodes: {...this.ctx.nodes}, codes: {...this.ctx.codes}, groups: {...this.ctx.groups}}
  try {
   entries.forEach((entry, index) => {
    const bound = this.ctx.ev.bind(params, names, [entry, index, entries], this.scope.values)
    if (!bound) throw new Unsupported('params')
    const env = {...this.env(frame), ...bound}
    for (const s of before) {
     if (s.isIfStatement()) {
      const r = this.ctx.ev.run(this.src(s.node.test), this.scope.values, {...this.ctx.locals, ...env})
      if (!r.ok) throw new Unsupported('skip test')
      if (r.value) return
      continue
     }
     for (const d of s.get('declarations')) {
      if (!d.get('id').isIdentifier()) throw new Unsupported('pattern')
      const r = this.ctx.ev.run(this.src(d.node.init), this.scope.values, {...this.ctx.locals, ...env})
      if (!r.ok) throw new Unsupported('local')
      env[d.node.id.name] = r.value
     }
    }
    out.push(...this.child(body, {within, env, lit: true, children: frame?.children, template: frame?.template}))
   })
  } catch (e) {
   if (!(e instanceof Unsupported)) throw e
   Object.assign(this, {n: snapshot.n, c: snapshot.c}); this.ctx.nodes = snapshot.nodes; this.ctx.codes = snapshot.codes; this.ctx.groups = snapshot.groups
   return null
  }
  if (out.length && !frame) this.ctx.groups[out[0].props.id] = {doc: this.file, kind: 'list', ids: out.map(i => i.props.id), code: this.code(this.src(container.node), 'child')}
  return out
 }
 // One editable template for every entry. head/tail = the source around the entry's JSX.
 template(container, fn, object, body, frame) {
  const snapshot = {n: this.n, c: this.c, nodes: {...this.ctx.nodes}, codes: {...this.ctx.codes}, groups: {...this.ctx.groups}}
  try {
   const id = this.id()
   let list
   if (object.isIdentifier() && this.rootParams?.has(object.node.name) && object.scope.getBinding(object.node.name)?.kind === 'param') list = {prop: object.node.name}
   // The entry's React key (source expression), for templates the owner gives several roots.
   const keyAttr = body.node.type === 'JSXElement' ? body.node.openingElement.attributes.find(a => a.type === 'JSXAttribute' && a.name.name === 'key') : null
   const key = !keyAttr?.value ? null : keyAttr.value.type === 'StringLiteral' ? JSON.stringify(keyAttr.value.value) : this.src(keyAttr.value.expression)
   const c = this.code(this.src(container.node), 'map', {head: this.text.slice(container.node.start, body.node.start), tail: this.text.slice(body.node.end, container.node.end), ...(key ? {key} : {}),
    ...(frame?.template ? {scope: frame.template} : {}), ...(list ? {list} : {}), names: fn.node.params.map(p => p.type === 'Identifier' ? p.name : '')})
   const kids = this.child(body, {within: [fn, ...(frame?.within || [])], env: {...this.env(frame)}, lit: true, template: id, children: frame?.children})
   const item = {type: 'Map', props: {id, c, kids}}
   this.pristine(item, container, frame ? {lit: true} : {})
   return [item]
  } catch (e) {
   if (!(e instanceof Unsupported)) throw e
   Object.assign(this, {n: snapshot.n, c: snapshot.c}); this.ctx.nodes = snapshot.nodes; this.ctx.codes = snapshot.codes; this.ctx.groups = snapshot.groups
   return null
  }
 }
 // `post.title` where `post` is a page local found in an imported data module (route-dependent).
 dataBinding(expr) {
  const path = memberPath(expr)
  if (!path || path.length !== 2) return null
  const binding = expr.scope.getBinding(path[0])
  if (!binding?.path.isVariableDeclarator() || !binding.path.node.init) return null
  // Only the record the route itself selects (its dynamic segment, e.g. [slug]) is bound to the page.
  const segment = (this.file.match(/\[([a-z]+)\]/) || [])[1]
  if (!segment) return null
  let found = null, byRoute = false
  binding.path.get('init').traverse({Identifier: p => {
   const info = this.scope.imported.get(p.node.name)
   if (info?.file && this.ctx.dataSets[info.file]) found = info.file
   if (p.node.name === segment && p.scope.getBinding(segment)?.kind !== 'module') byRoute = true
  }})
  if (!found || !byRoute || !this.ctx.dataSets[found].fields.includes(path[1])) return null
  return {file: found, field: path[1]}
 }
 attributes(path, frame) {
  const a = {}, x = {}, order = []
  let cls
  for (const attr of path.get('openingElement.attributes')) {
   if (attr.isJSXSpreadAttribute()) {
    order.push(`...${Object.keys(x).length}`)
    const spreadDependent = this.dependsOn(attr.get('argument'), frame?.within)
    if (spreadDependent && !frame?.template) throw new Unsupported('spread')
    x[`...${Object.keys(x).length}`] = this.code(this.src(attr.node.argument), 'spread', spreadDependent ? {scope: frame.template} : {})
    continue
   }
   const raw = attr.node.name.type === 'JSXNamespacedName' ? `${attr.node.name.namespace.name}:${attr.node.name.name.name}` : attr.node.name.name
   const name = ATTR_RENAME[raw] || raw
   order.push(name)
   const value = attr.node.value
   const pure = v => { x[name] = this.code(JSON.stringify(v), 'attr', {pure: true, value: v}) }
   if (value === null) { if (attrAllowed(name)) a[name] = true; else pure(true); continue }
   if (value.type === 'StringLiteral') {
    if (name === 'className') { if (CLASS_OK.test(value.value)) cls = value.value; else pure(value.value) }
    else if (attrAllowed(name)) a[name] = value.value; else pure(value.value)
    continue
   }
   const expr = unwrapParens(attr.get('value.expression'))
   if (expr.node.type === 'JSXEmptyExpression') continue
   const dependent = this.dependsOn(expr, frame?.within)
   const r = this.evaluate(expr, frame)
   const literal = r.ok && ['string', 'number', 'boolean'].includes(typeof r.value)
   if (dependent) {
    // Values that depend on a list entry or component prop become literals (or trusted literal code).
    if (frame?.template && (!r.ok || (r.value !== undefined && r.value !== null && !literal && !plainData(r.value)))) { x[name] = this.code(this.src(expr.node), 'attr', {scope: frame.template}); continue }
    if (!r.ok || (r.value !== undefined && r.value !== null && !literal && !plainData(r.value))) throw new Unsupported('dependent attribute')
    if (r.value === undefined || r.value === null) continue
    if (name === 'className' && typeof r.value === 'string' && CLASS_OK.test(r.value)) { cls = r.value; continue }
    if (literal && attrAllowed(name)) { a[name] = r.value; continue }
    x[name] = this.code(JSON.stringify(r.value), 'attr', {pure: true, value: literal ? r.value : undefined, data: plainData(r.value) ? r.value : undefined})
    continue
   }
   x[name] = this.code(this.src(expr.node), 'attr', {...(literal ? {value: r.value} : {}), ...(r.ok && plainData(r.value) && !literal ? {data: r.value} : {})})
  }
  return {a, x, cls, order}
 }
 element(path, frame) {
  const opening = path.node.openingElement
  const nameNode = opening.name
  const children = () => this.children(path.get('children'), frame)
  const finish = (item, extra = {}) => {
   const opener = path.get('openingElement')
   const nameEnd = opener.node.name.end
   const attrsEnd = opener.node.end - (opener.node.selfClosing ? 2 : 1)
   this.pristine(item, path, frame ? {lit: true, ...(extra.order ? {order: extra.order} : {})} : {attrs: this.text.slice(nameEnd, attrsEnd), selfClosing: opener.node.selfClosing,
    ...(path.node.closingElement ? {inner: [opener.node.end, path.node.closingElement.start]} : {}), ...extra})
   return [item]
  }
  if (nameNode.type === 'JSXIdentifier' && /^[a-z]/.test(nameNode.name)) {
   if (!TAGS.has(nameNode.name)) return this.opaque(path, frame)
   const {a, x, cls, order} = this.attributes(path, frame)
   const props = {id: this.id(), tag: nameNode.name}
   if (Object.keys(a).length) props.a = a
   if (Object.keys(x).length) props.x = x
   if (cls !== undefined) props.cls = cls
   if (!VOID.has(nameNode.name)) props.kids = children()
   return finish({type: 'El', props}, {order})
  }
  if (nameNode.type !== 'JSXIdentifier') return this.opaque(path, frame)
  const name = nameNode.name
  const binding = path.scope.getBinding(name)
  const imported = this.scope.imported.get(name)
  if (imported && ['next/link', 'next/image'].includes(imported.source) && imported.imported === 'default') {
   const tag = imported.source === 'next/link' ? 'Link' : 'Image'
   const {a, x, cls, order} = this.attributes(path, frame)
   const props = {id: this.id(), tag}
   if (Object.keys(a).length) props.a = a
   if (Object.keys(x).length) props.x = x
   if (cls !== undefined) props.cls = cls
   if (tag === 'Link') props.kids = children()
   this.uses.add(tag)
   return finish({type: 'El', props}, {order})
  }
  // A small local wrapper component (function in this file returning JSX) is expanded in place.
  if (binding && binding.kind !== 'module' && (binding.path.isFunctionDeclaration() || (binding.path.isVariableDeclarator() && ['ArrowFunctionExpression', 'FunctionExpression'].includes(binding.path.node.init?.type)))) {
   const expanded = this.expand(path, binding, frame)
   if (expanded) return expanded
  }
  if (frame && (this.dependsOn(path, frame.within))) {
   if (frame.template) return this.opaque(path, frame, {scope: frame.template})
   throw new Unsupported('dependent component')
  }
  // Imported components: shared documents (Header, Footer, HeroDiagram) are edited in their own file;
  // functional ones (FaqAccordion, Script, JsonLd) keep their behaviour and expose literal props.
  const props = {id: this.id(), name}
  if (imported?.file && this.ctx.staticDocs.has(imported.file) && imported.imported === 'default' && !opening.attributes.length && !path.node.children.length) {
   props.doc = imported.file
   props.use = this.code(this.src(path.node), 'use')
   this.ctx.docUses.push({from: this.file, id: props.id, doc: imported.file})
   return finish({type: 'Use', props})
  }
  if (!imported && binding?.path.isVariableDeclarator()) {
   // const ContentComponent = publishedComponents[slug] — bound per route to that post's document.
   const init = binding.path.get('init')
   const member = unwrapParens(init.isLogicalExpression() ? init.get('left') : init)
   if (member.isMemberExpression() && member.node.computed && member.get('object').isIdentifier()) {
    const objBinding = member.scope.getBinding(member.node.object.name)
    const objInit = objBinding?.path.isVariableDeclarator() && objBinding.path.get('init')
    if (objInit && objInit.isObjectExpression()) {
     const table = {}
     for (const prop of objInit.node.properties) {
      if (prop.type !== 'ObjectProperty' || prop.value.type !== 'Identifier') continue
      const key = prop.key.type === 'StringLiteral' ? prop.key.value : prop.key.name
      const target = this.scope.imported.get(prop.value.name)
      if (target?.file && this.ctx.staticDocs.has(target.file)) table[key] = target.file
     }
     if (Object.keys(table).length) {
      props.use = this.code(this.src(path.node), 'use')
      this.ctx.bindings.push({from: this.file, id: props.id, table})
      return finish({type: 'Use', props})
     }
    }
   }
  }
  const {a, x, order} = this.attributes(path, frame)
  props.use = this.code(this.src(path.node), 'use')
  // A component whose markup is a template document (FaqAccordion): edited in place on this page.
  if (imported?.file && this.ctx.templateDocs.has(imported.file) && imported.imported === 'default') props.tpl = imported.file
  if (Object.keys(a).length) props.a = a
  if (Object.keys(x).length) props.x = x
  return finish({type: 'Use', props}, {order})
 }
 expand(path, binding, frame) {
  const fnPath = binding.path.isFunctionDeclaration() ? binding.path : binding.path.get('init')
  if (fnPath.node.params.length > 1 || fnPath.node.async) return null
  let body = fnPath.get('body')
  if (body.isBlockStatement()) {
   const statements = body.get('body')
   if (statements.length !== 1 || !statements[0].isReturnStatement() || !statements[0].node.argument) return null
   body = unwrapParens(statements[0].get('argument'))
  } else body = unwrapParens(body)
  if (!isJsx(body.node)) return null
  const snapshot = {n: this.n, c: this.c, nodes: {...this.ctx.nodes}, codes: {...this.ctx.codes}, groups: {...this.ctx.groups}}
  try {
   const props = {}
   for (const attr of path.get('openingElement.attributes')) {
    if (!attr.isJSXAttribute() || attr.node.name.type !== 'JSXIdentifier') throw new Unsupported('props')
    const value = attr.node.value
    if (value === null) props[attr.node.name.name] = true
    else if (value.type === 'StringLiteral') props[attr.node.name.name] = value.value
    else {
     const r = this.evaluate(attr.get('value.expression'), frame)
     if (!r.ok || !plainData(r.value)) throw new Unsupported('prop')
     props[attr.node.name.name] = r.value
    }
   }
   const token = {$$children: true}
   const kids = this.children(path.get('children'), frame)
   props.children = token
   const names = Object.keys(fnPath.scope.bindings).filter(n => fnPath.scope.bindings[n].kind === 'param')
   const params = fnPath.node.params.map(p => this.src(p)).join(', ')
   const bound = params ? this.ctx.ev.bind(params, names, [props], this.scope.values) : {}
   if (!bound) throw new Unsupported('params')
   const out = this.child(body, {within: [fnPath, ...(frame?.within || [])], env: {...this.env(frame), ...bound}, lit: true, children: {token, nodes: kids}})
   if (out.length && !frame) this.ctx.groups[out[0].props.id] = {doc: this.file, kind: 'component', name: binding.identifier.name, ids: out.map(i => i.props.id), code: this.code(this.src(path.node), 'use')}
   return out
  } catch (e) {
   if (!(e instanceof Unsupported)) throw e
   Object.assign(this, {n: snapshot.n, c: snapshot.c}); this.ctx.nodes = snapshot.nodes; this.ctx.codes = snapshot.codes; this.ctx.groups = snapshot.groups
   return null
  }
 }
}
class Unsupported extends Error {}

// A data module (content/blog.ts): one exported array of object literals typed by an exported type
// alias, each with a string `slug`. Its plain string fields (by the type, never the slug) are editable.
const STRUCTURAL = new Set(['slug', 'pillar', 'contentPath']) // keys and links between records stay code-owned
function dataSet(file, text) {
 let ast
 try { ast = parse(text, {sourceType: 'module', plugins: ['typescript']}) } catch { return null }
 const types = {}
 for (const node of ast.program.body) {
  const decl = node.type === 'ExportNamedDeclaration' ? node.declaration : node
  if (decl?.type === 'TSTypeAliasDeclaration' && decl.typeAnnotation.type === 'TSTypeLiteral') types[decl.id.name] = decl.typeAnnotation.members
 }
 for (const node of ast.program.body) {
  const decl = node.type === 'ExportNamedDeclaration' ? node.declaration : null
  if (decl?.type !== 'VariableDeclaration') continue
  for (const d of decl.declarations) {
   if (d.id.type !== 'Identifier' || d.init?.type !== 'ArrayExpression') continue
   const ann = d.id.typeAnnotation?.typeAnnotation
   const typeName = ann?.type === 'TSArrayType' && ann.elementType.type === 'TSTypeReference' && ann.elementType.typeName.type === 'Identifier' ? ann.elementType.typeName.name : null
   const members = types[typeName]
   if (!members) continue
   const fields = members.filter(m => m.type === 'TSPropertySignature' && m.key.type === 'Identifier' && m.typeAnnotation?.typeAnnotation.type === 'TSStringKeyword' && !STRUCTURAL.has(m.key.name)).map(m => m.key.name)
   const records = []
   for (const el of d.init.elements) {
    if (el?.type !== 'ObjectExpression') return null
    const row = {key: null, fields: {}}
    for (const prop of el.properties) {
     if (prop.type !== 'ObjectProperty' || prop.computed) continue
     const name = prop.key.type === 'Identifier' ? prop.key.name : prop.key.value
     const v = prop.value
     const value = v.type === 'StringLiteral' ? v.value : v.type === 'TemplateLiteral' && !v.expressions.length ? v.quasis[0].value.cooked : null
     if (name === 'slug' && value !== null) row.key = value
     else if (value !== null && fields.includes(name)) row.fields[name] = {value, span: [v.start, v.end]}
    }
    if (!row.key || records.some(r => r.key === row.key)) return null
    records.push(row)
   }
   if (records.length) return {export: d.id.name, type: typeName, fields, records}
  }
 }
 return null
}

// ---------------------------------------------------------------- static HTML guides
// public/guides/<name>/index.html (served at /guides/<name>): the body's elements become El/Txt nodes
// (literal attributes; anything else is kept as a source reference), the head (its own stylesheet)
// is kept verbatim. Byte-identical sibling files (guide.html) are written as mirrors.
const HTML_DOC = /^public\/guides\/[a-z0-9-]+\/index\.html$/
const HTML_RENAME = {class: 'cls', for: 'htmlFor'}
function importHtml(file, text, ctx, files) {
 const name = file.split('/')[2]
 const key = ('guide-' + name).slice(0, 48)
 const open = text.match(/<body\b[^>]*>/i), close = text.toLowerCase().lastIndexOf('</body>')
 if (!open || close < 0) return null
 const dom = new JSDOM(text)
 // The body's own source only (the parser would fold text after </body> into it).
 const inner = new JSDOM(`<!doctype html><html><head></head><body>${text.slice(open.index + open[0].length, close)}</body></html>`)
 let n = 0, c = 0
 const code = (raw, kind, extra = {}) => { const ref = `${key}~${(c++).toString(36)}`; ctx.codes[ref] = {doc: file, text: raw, kind, ...extra}; return ref }
 const node = el => {
  if (el.nodeType === 3) { const item = {type: 'Txt', props: {id: `${key}_${(n++).toString(36)}`, text: el.data}}; ctx.nodes[item.props.id] = {doc: file, own: ownFingerprint(item), lit: true}; return item }
  if (el.nodeType !== 1) return null
  const tag = el.localName
  const id = `${key}_${(n++).toString(36)}`
  if (!TAGS.has(tag) || ['Link', 'Image'].includes(tag)) { const item = {type: 'Code', props: {id, c: code(el.outerHTML, 'html')}}; ctx.nodes[id] = {doc: file, own: ownFingerprint(item), lit: true}; return item }
  const props = {id, tag}, a = {}, x = {}
  for (const attr of el.attributes) {
   const k = HTML_RENAME[attr.name] || attr.name
   if (k === 'cls' && CLASS_OK.test(attr.value)) props.cls = attr.value
   else if (k !== 'cls' && attrAllowed(k)) a[k] = attr.value === '' && attr.name !== 'alt' && attr.name !== 'value' ? true : attr.value
   else x[attr.name] = code(JSON.stringify(attr.value), 'attr', {pure: true, value: attr.value})
  }
  if (Object.keys(a).length) props.a = a
  if (Object.keys(x).length) props.x = x
  if (!VOID.has(tag)) props.kids = [...el.childNodes].map(node).filter(Boolean)
  const item = {type: 'El', props}
  ctx.nodes[id] = {doc: file, own: ownFingerprint(item), lit: true, order: [...el.attributes].map(x => x.name)}
  return item
 }
 const tree = [...inner.window.document.body.childNodes].map(node).filter(Boolean)
 const css = [...dom.window.document.head.querySelectorAll('style')].map(s => s.textContent).join('\n')
 const dir = file.replace(/index\.html$/, '')
 const mirrors = [...files.keys()].filter(f => f !== file && f.startsWith(dir) && f.endsWith('.html') && files.get(f) === text)
 const title = dom.window.document.title || name
 return {tree, key, head: [0, open.index + open[0].length], tail: [close, text.length], css, mirrors, title, name}
}

// The default export's rendered JSX (its last top-level return), with the locals evaluated before it.
function renderRoot(ast) {
 let found = null
 traverse(ast, {ExportDefaultDeclaration(path) {
  let fn = path.get('declaration')
  if (fn.isIdentifier()) { const b = path.scope.getBinding(fn.node.name); fn = b?.path }
  if (!fn || !(fn.isFunctionDeclaration() || fn.isArrowFunctionExpression() || fn.isFunctionExpression())) return
  const body = fn.get('body')
  if (!body.isBlockStatement()) { const u = unwrapParens(body); if (isJsx(u.node)) found = {fn, ret: u, statements: []}; return }
  const statements = body.get('body')
  const ret = [...statements].reverse().find(s => s.isReturnStatement() && s.node.argument && isJsx(unwrapParens(s.get('argument')).node))
  if (ret) found = {fn, ret: unwrapParens(ret.get('argument')), statements: statements.slice(0, statements.indexOf(ret))}
 }})
 return found
}

export function importSource(files, {commit = null} = {}) {
 const ev = new Evaluator()
 const scopes = new Map()
 const ctx = {ev, codes: {}, nodes: {}, groups: {}, docUses: [], bindings: [], staticDocs: new Set(), templateDocs: new Set(), dataSets: {}, locals: {}}
 const candidates = [...files.keys()].filter(f => /^(app\/(.+\/)?page\.tsx|app\/not-found\.tsx|components\/[A-Za-z]+\.tsx|content\/blog-posts\/[a-z0-9-]+\.tsx)$/.test(f)).sort()
 const parsed = new Map()
 for (const file of candidates) {
  const ast = parse(files.get(file), {sourceType: 'module', plugins: ['typescript', 'jsx']})
  const root = renderRoot(ast)
  if (!root) continue
  parsed.set(file, {ast, root})
  // Static documents: rendered without props (Header, Footer, HeroDiagram, post bodies) — editable in place.
  if (!file.startsWith('app/') && root.fn.node.params.length === 0) ctx.staticDocs.add(file)
  // Template documents: components with props whose root is a site element (FaqAccordion).
  else if (file.startsWith('components/') && root.ret.node.type === 'JSXElement' && root.ret.node.openingElement.name.type === 'JSXIdentifier' && TAGS.has(root.ret.node.openingElement.name.name)) ctx.templateDocs.add(file)
 }
 for (const file of [...files.keys()].filter(f => /^content\/[a-z0-9-]+\.ts$/.test(f)).sort()) {
  const set = dataSet(file, files.get(file))
  if (set) ctx.dataSets[file] = set
 }
 const docs = {}
 const unsupported = []
 for (const [file, {ast, root}] of parsed) {
  if (!file.startsWith('app/') && !ctx.staticDocs.has(file) && !ctx.templateDocs.has(file)) continue // functional components (JsonLd)
  const text = files.get(file)
  const scope = moduleScope(file, files, ev, scopes)
  const importer = new DocImporter(file, text, ast, scope, ctx)
  importer.rootParams = new Set(Object.keys(root.fn.scope.bindings).filter(n => root.fn.scope.bindings[n].kind === 'param'))
  // Locals evaluated before the return (pure constants only: hooks, params and requests stay unknown).
  ctx.locals = {}
  for (const s of root.statements) {
   if (!s.isVariableDeclaration()) continue
   for (const d of s.get('declarations')) {
    if (!d.node.init) continue
    // const [open, setOpen] = useState(initial): the value the site renders on load.
    const init = unwrapParens(d.get('init'))
    const first = d.node.id.type === 'ArrayPattern' ? d.node.id.elements[0] : null
    if (first?.type === 'Identifier' && init.isCallExpression() && /^(React\.)?useState$/.test(text.slice(init.node.callee.start, init.node.callee.end))) {
     const arg = init.node.arguments[0]
     const r = !arg ? {ok: true, value: undefined} : /Function/.test(arg.type) ? {ok: false} : ev.run(text.slice(arg.start, arg.end), scope.values, ctx.locals)
     if (r.ok && plainData(r.value ?? null)) ctx.locals[first.name] = r.value
     continue
    }
    if (!d.get('id').isIdentifier()) continue
    const r = ev.run(text.slice(d.node.init.start, d.node.init.end), scope.values, ctx.locals)
    if (r.ok && r.value !== undefined && !isJsxValue(r.value)) ctx.locals[d.node.id.name] = r.value
   }
  }
  let tree
  try { tree = importer.child(root.ret, null) } catch (e) { if (e instanceof Unsupported) { unsupported.push({file, reason: e.message}); continue } throw e }
  const fragment = root.ret.node.type === 'JSXFragment'
  const imports = {}
  for (const [local, info] of scope.imported) imports[local] = info.source
  docs[file] = {id: file, key: importer.key, file, kind: file.startsWith('app/') ? 'page' : file.startsWith('content/') ? 'content' : ctx.templateDocs.has(file) ? 'template' : 'component',
   client: /^\s*["']use client["']/.test(text), fragment, tree, imports, uses: [...importer.uses],
   ret: [root.ret.node.start, root.ret.node.end], sha256: sha(text), unsupported: []}
 }
 for (const file of [...files.keys()].filter(f => HTML_DOC.test(f)).sort()) {
  const html = importHtml(file, files.get(file), ctx, files)
  if (!html) continue
  docs[file] = {id: file, key: html.key, file, kind: 'html', client: false, fragment: false, tree: html.tree, imports: {}, uses: [], ret: [0, 0], sha256: sha(files.get(file)), unsupported: [],
   html: {head: html.head, tail: html.tail, mirrors: html.mirrors, css: html.css, title: html.title, name: html.name}}
 }
 // Data records: each data module becomes a document of `Rec` nodes (one per entry, keyed by slug).
 for (const [file, set] of Object.entries(ctx.dataSets)) {
  const key = docKey(file)
  const tree = set.records.map((r, i) => ({type: 'Rec', props: {id: `${key}_${i.toString(36)}`, key: r.key, f: Object.fromEntries(Object.entries(r.fields).map(([k, v]) => [k, v.value]))}}))
  docs[file] = {id: file, key, file, kind: 'data', client: false, fragment: false, tree, imports: {}, uses: [], ret: [0, 0], sha256: sha(files.get(file)), unsupported: [],
   data: {export: set.export, type: set.type, fields: set.fields, spans: Object.fromEntries(set.records.map(r => [r.key, Object.fromEntries(Object.entries(r.fields).map(([k, v]) => [k, v.span]))])),
    values: Object.fromEntries(set.records.map(r => [r.key, Object.fromEntries(Object.entries(r.fields).map(([k, v]) => [k, v.value]))]))}}
 }
 // Routes: each page (dynamic routes expand per parameter in the capture) with the documents it shows.
 const pages = []
 for (const doc of Object.values(docs).filter(d => d.kind === 'page')) {
  const route = routeOf(doc.file)
  const binding = ctx.bindings.find(b => b.from === doc.file)
  if (binding && /\[[a-z]+\]/.test(route)) {
   for (const [slug, target] of Object.entries(binding.table)) pages.push({id: `${doc.key}:${slug}`, route: route.replace(/\[[a-z]+\]/, slug), doc: doc.file, bind: {[binding.id]: target}, label: `${route.split('/')[1]}: ${slug}`})
  } else if (!/\[[a-z]+\]/.test(route)) pages.push({id: doc.key, route, doc: doc.file, bind: {}, label: route === '/' ? 'Homepage' : route})
  // Dynamic templates without per-path documents are listed per prerendered path by the import.
 }
 for (const doc of Object.values(docs).filter(d => d.kind === 'html')) pages.push({id: doc.key, route: `/guides/${doc.html.name}`, doc: doc.file, bind: {}, label: `Guide: ${doc.html.title}`, css: `${doc.key}.css`})
 pages.sort((a, b) => (a.route === '/' ? -1 : b.route === '/' ? 1 : a.route.localeCompare(b.route)))
 dataPages(pages, docs, ctx.codes)
 const fileHashes = Object.fromEntries([...files.entries()].map(([f, t]) => [f, sha(t)]))
 const model = {schema: SCHEMA, commit, docs: Object.fromEntries(Object.entries(docs).map(([id, d]) => [id, {id, key: d.key, file: d.file, kind: d.kind, client: d.client, tree: d.tree, ...(d.data ? {data: {export: d.data.export, type: d.data.type, fields: d.data.fields}} : {}), ...(d.html ? {html: {mirrors: d.html.mirrors, title: d.html.title}} : {})}])),
  pages, codes: Object.fromEntries(Object.entries(ctx.codes).map(([ref, c]) => [ref, {doc: c.doc, kind: c.kind, ...(c.value !== undefined ? {value: c.value} : {}), ...(c.data !== undefined ? {data: c.data} : {}), ...(c.op ? {op: c.op} : {}), ...(c.pure ? {pure: true} : {}),
   ...(c.scope ? {scope: c.scope} : {}), ...(c.path ? {path: c.path} : {}), ...(c.bind ? {bind: c.bind} : {}), ...(c.list ? {list: c.list} : {}), ...(c.names ? {names: c.names} : {}), preview: c.text.length > 160 ? c.text.slice(0, 157) + '…' : c.text}])),
  uses: ctx.docUses}
 const pristine = {schema: SCHEMA, commit, files: fileHashes, docs: Object.fromEntries(Object.entries(docs).map(([id, d]) => [id, {fragment: d.fragment, ret: d.ret, sha256: d.sha256, imports: d.imports, uses: d.uses, roots: d.tree.map(i => i.props.id), ...(d.data ? {data: {spans: d.data.spans, values: d.data.values}} : {}), ...(d.html ? {html: {head: d.html.head, tail: d.html.tail, mirrors: d.html.mirrors, css: d.html.css}} : {})}])),
  codes: ctx.codes, nodes: ctx.nodes, groups: ctx.groups, unsupported}
 // Each node's child ids (for structural pristine checks).
 for (const d of Object.values(docs)) for (const {item} of walk(d.tree)) {
  const row = pristine.nodes[item.props.id]
  if (!row) continue
  if (Array.isArray(item.props.kids)) row.kids = item.props.kids.map(k => k.props.id)
  if (Array.isArray(item.props.alt)) row.alt = item.props.alt.map(k => k.props.id)
 }
 return {model, pristine}
}

// Pages that show data-bound values get the data module and the record their route shows.
export function dataPages(pages, docs, codes) {
 for (const page of pages) {
  const files = new Set()
  const visit = list => { for (const {item} of walk(list)) { const c = item.type === 'Code' ? codes[item.props.c] : null; if (c?.bind) files.add(c.bind.file) } }
  visit(docs[page.doc]?.tree || [])
  if (!files.size) continue
  const slug = page.route.split('/').at(-1)
  page.data = Object.fromEntries([...files].filter(f => docs[f]?.tree.some(r => r.props.key === slug)).map(f => [f, slug]))
  if (!Object.keys(page.data).length) delete page.data
 }
}

// ---------------------------------------------------------------- model → source
const JSX_SAFE = /^[^"&{}<>\\\n\r]*$/
function attrText(name, value) {
 if (value === true) return name
 if (typeof value === 'number' || value === false) return `${name}={${JSON.stringify(value)}}`
 return JSX_SAFE.test(value) ? `${name}="${value}"` : `${name}={${JSON.stringify(value)}}`
}
// `pages`: owner-created documents (code layer `pages`, pages.js); each is emitted as a whole new file.
export function generate(pristine, files, docs, {mode = 'minimal', assetPath = a => a, cssFile = 'app/ttros-studio.css', pages = {}} = {}) {
 const instrument = mode === 'instrument'
 const codes = {...pristine.codes, ...KIT_CODES}
 const out = {}
 const problems = []
 const memo = new Map()
 const snap = id => pristine.nodes[id]
 const ownSame = item => { const s = snap(item.props.id); return !!s && s.own === ownFingerprint(item) }
 let current = null // the document being generated
 const fullyPristine = item => {
  if (instrument) return false
  const id = item.props.id
  const key = `${current}|${id}`
  if (memo.has(key)) return memo.get(key)
  const s = snap(id)
  let ok = !!s && s.doc === current && ownSame(item) && !item.props.st
  for (const slot of ['kids', 'alt']) if (ok && Array.isArray(item.props[slot])) ok = canonical(item.props[slot].map(k => k.props.id)) === canonical(s[slot] || []) && item.props[slot].every(fullyPristine)
  memo.set(key, ok)
  return ok
 }
 const scopes = [] // templates (Map ids) enclosing the element being generated
 const codeText = (ref, doc) => {
  const c = codes[ref]
  if (!c) throw Error(`Unknown source reference ${ref}`)
  if (c.doc !== doc && !c.pure) throw Error(`An element using code from ${c.doc} was moved into ${doc}; move it back or remove that behaviour first.`)
  if (c.scope && !scopes.includes(c.scope)) throw Error('An element bound to a repeated item was moved out of its template; move it back inside the repeated item.')
  return c.text
 }
 const resolve = v => typeof v === 'string' && v.startsWith('asset:') ? assetPath(v) : v
 const escText = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
 const escAttr = t => String(t).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
 const HTML_NAMES = {htmlFor: 'for'}
 const htmlNode = item => {
  const p = item.props
  if (item.type === 'Txt') return escText(p.text)
  if (item.type === 'Code') { const raw = codeText(p.c, current); return p.st ? `<div class="${styleClass(p.id)}">${raw}</div>` : raw }
  if (item.type !== 'El') throw Error(`${item.type} elements cannot be used in a static HTML page`)
  const tag = p.tag === 'Link' ? 'a' : p.tag === 'Image' ? 'img' : p.tag
  const entries = []
  for (const [k, v] of Object.entries(p.a || {})) { if (v === false || v === undefined) continue; const nm = HTML_NAMES[k] || k; entries.push([nm, v === true ? nm : `${nm}="${escAttr(resolve(v))}"`]) }
  for (const [k, ref] of Object.entries(p.x || {})) { const c = codes[ref]; codeText(ref, current); if (c?.value !== undefined) entries.push([k, `${k}="${escAttr(c.value)}"`]) }
  const cls = [p.cls, p.st ? styleClass(p.id) : null].filter(Boolean).join(' ')
  if (cls) entries.push(['class', `class="${escAttr(cls)}"`])
  const order = pristine.nodes[p.id]?.doc === current ? pristine.nodes[p.id].order || [] : []
  const rank = k => { const i = order.indexOf(k); return i < 0 ? order.length : i }
  const attrs = entries.map((e, i) => [rank(e[0]), i, e[1]]).sort((x, y) => x[0] - y[0] || x[1] - y[1]).map(e => e[2])
  if (instrument) attrs.push(`data-sid="${p.id}"`)
  const open = `<${tag}${attrs.length ? ' ' + attrs.join(' ') : ''}>`
  return VOID.has(tag) ? open : `${open}${(p.kids || []).map(htmlNode).join('')}</${tag}>`
 }
 for (const [docId, tree] of Object.entries(docs)) {
  const created = pages[docId]
  const meta = created ? {created: true, imports: {}, roots: [], fragment: true} : pristine.docs[docId]
  if (!meta) { problems.push(`Unknown document ${docId}`); continue }
  if (created && files.has(docId)) { problems.push(`${docId} already exists in the website source; choose another slug.`); continue }
  current = docId
  const text = created ? '' : files.get(docId)
  if (!created && sha(text) !== meta.sha256) throw Error(`Website source drift: ${docId} no longer matches the import.`)
  if (meta.data) {
   // Data records: only string field values change, written into their literal positions.
   if (instrument) continue
   const edits = []
   if (canonical(tree.map(r => r.props.id)) !== canonical(meta.roots)) { problems.push(`${docId}: records cannot be added, removed or reordered in the editor.`); continue }
   for (const rec of tree) {
    const spans = meta.data.spans[rec.props.key], before = meta.data.values[rec.props.key]
    if (!spans) { problems.push(`${docId}: unknown record ${rec.props.key}`); continue }
    for (const [field, value] of Object.entries(rec.props.f || {})) {
     if (!spans[field]) { problems.push(`${docId}: ${field} is not an editable field`); continue }
     if (value !== before[field]) edits.push([spans[field][0], spans[field][1], JSON.stringify(value)])
    }
   }
   if (!edits.length) continue
   let next = text
   for (const [a, b, v] of edits.sort((x, y) => y[0] - x[0])) next = next.slice(0, a) + v + next.slice(b)
   out[docId] = next
   continue
  }
  const needed = new Set()
  const list = (items, parentId) => {
   const parts = []
   for (let i = 0; i < items.length;) {
    const group = !instrument && pristine.groups[items[i].props.id]
    if (group && group.doc === docId && group.ids.every((id, k) => items[i + k]?.props.id === id && fullyPristine(items[i + k]))) { parts.push(codeText(group.code, docId)); i += group.ids.length; continue }
    parts.push(node(items[i], parentId)); i++
   }
   return parts.join('\n')
  }
  const attrs = item => {
   const p = item.props, s = snap(p.id)?.doc === docId ? snap(p.id) : null
   const studio = p.st ? styleClass(p.id) : null
   if (!instrument && s && !s.lit && s.attrs !== undefined && ownSame(item) && !studio) return s.attrs
   const entries = []
   for (const [k, v] of Object.entries(p.a || {})) entries.push([k, attrText(k, resolve(v))])
   for (const [k, ref] of Object.entries(p.x || {})) {
    if (k === 'className') continue
    entries.push([k, k.startsWith('...') ? `{...${codeText(ref, docId)}}` : `${k}={${codeText(ref, docId)}}`])
   }
   const classCode = p.x?.className ? codeText(p.x.className, docId) : null
   const literal = [p.cls, studio].filter(Boolean).join(' ')
   if (classCode) entries.push(['className', literal && literal !== p.cls ? `className={[(${classCode}), ${JSON.stringify(literal)}].join(" ")}` : `className={${classCode}}`])
   else if (literal || p.cls !== undefined) entries.push(['className', attrText('className', literal)])
   const order = s?.order || []
   const rank = k => { const i = order.indexOf(k); return i < 0 ? order.length : i }
   const parts = entries.map((e, i) => [rank(e[0]), i, e[1]]).sort((x, y) => x[0] - y[0] || x[1] - y[1]).map(e => e[2])
   if (instrument) parts.push(`data-sid="${p.id}"`)
   return parts.length ? ' ' + parts.join(' ') : ''
  }
  const mark = (id, inner) => instrument ? `<studio-mark data-sid="${id}">${inner}</studio-mark>` : inner
  const node = (item, parentId) => {
   // Source text is only ever re-emitted inside the document it came from.
   const p = item.props, s = snap(p.id)?.doc === docId ? snap(p.id) : null
   if (s && !s.lit && s.span && fullyPristine(item)) return text.slice(s.span[0], s.span[1])
   if (item.type === 'Txt') return p.x?.text && codes[p.x.text]?.value === p.text ? `{${codeText(p.x.text, docId)}}` : `{${JSON.stringify(p.text)}}`
   if (item.type === 'Code') {
    const code = codeText(p.c, docId)
    const inner = mark(p.id, code)
    return p.st ? `<div className="${styleClass(p.id)}">${inner}</div>` : inner
   }
   if (item.type === 'Map') {
    const c = codes[p.c]
    codeText(p.c, docId)
    scopes.push(p.id)
    try {
     // One keyed root is emitted as is; otherwise the entry is a keyed Fragment (lint: react/jsx-key).
     const only = p.kids.length === 1 && p.kids[0].type === 'El' ? p.kids[0] : null
     const keyed = only && (only.props.x?.key || only.props.a?.key !== undefined)
     let entry
     if (keyed) entry = node(only, p.id)
     else if (c.key) { needed.add('Fragment'); entry = `<Fragment key={${c.key}}>\n${list(p.kids, p.id)}\n</Fragment>` }
     else entry = `<>\n${list(p.kids, p.id)}\n</>`
     return c.head + entry + c.tail
    } finally { scopes.pop() }
   }
   if (item.type === 'Cond') {
    const c = codes[p.c]
    const test = codeText(p.c, docId)
    const yes = p.kids.length ? `<>\n${list(p.kids, p.id)}\n</>` : 'null'
    const no = p.alt.length ? `<>\n${list(p.alt, p.id)}\n</>` : 'null'
    return c.op === '&&' && !p.alt.length ? `{(${test}) && (${yes})}` : `{(${test}) ? (${yes}) : (${no})}`
   }
   if (item.type === 'Comp') {
    // A React component of the site (or written in Code mode) placed visually; it renders in the build.
    needed.add(`comp:${p.name}:@/${p.file.replace(/\.(tsx|ts|jsx|js)$/, '')}`)
    const usage = instrument ? mark(p.id, `<${p.name} />`) : `<${p.name} />`
    return p.st ? `<div className="${styleClass(p.id)}">${usage}</div>` : usage
   }
   if (item.type === 'Use') {
    const code = codeText(p.use, docId)
    if (codes[p.use]?.kit) needed.add(`kit:${codes[p.use].kit.name}`)
    if (p.doc && p.name && !p.a && !p.x) {
     const usage = instrument ? mark(p.id, code) : code
     return p.st ? `<div className="${styleClass(p.id)}">${usage}</div>` : usage
    }
    let usage = code
    if (!ownSame(item) && !p.doc) {
     // Edited functional component props: literal props replace source values; behaviour stays.
     const parts = []
     for (const [k, v] of Object.entries(p.a || {})) parts.push(attrText(k, resolve(v)))
     for (const [k, v] of Object.entries(p.d || {})) parts.push(`${k}={${JSON.stringify(v)}}`)
     for (const [k, ref] of Object.entries(p.x || {})) if (!(p.d && Object.hasOwn(p.d, k))) parts.push(k.startsWith('...') ? `{...${codeText(ref, docId)}}` : `${k}={${codeText(ref, docId)}}`)
     const original = code.match(/^<([A-Za-z0-9_.]+)/)
     if (!original) throw Error('Unsupported component usage')
     const inner = Array.isArray(p.kids) && p.kids.length ? `>\n${list(p.kids, p.id)}\n</${original[1]}>` : ' />'
     usage = `<${original[1]}${parts.length ? ' ' + parts.join(' ') : ''}${inner}`
    }
    usage = instrument ? mark(p.id, usage) : usage
    return p.st ? `<div className="${styleClass(p.id)}">${usage}</div>` : usage
   }
   const tag = p.tag
   if (tag === 'Link' || tag === 'Image') needed.add(tag)
   const open = `<${tag}${attrs(item)}`
   if (VOID.has(tag) || !p.kids?.length) return `${open} />`
   if (!instrument && s && !s.lit && s.inner && canonical(p.kids.map(k => k.props.id)) === canonical(s.kids || []) && p.kids.every(fullyPristine)) return `${open}>${text.slice(s.inner[0], s.inner[1])}</${tag}>`
   return `${open}>\n${list(p.kids, p.id)}\n</${tag}>`
  }
  const rootIds = tree.map(i => i.props.id)
  if (!created && !instrument && canonical(rootIds) === canonical(meta.roots) && tree.every(fullyPristine)) continue
  if (meta.html) {
   // Static HTML guide: the body is serialized from the model; the head (with its stylesheet) is kept,
   // owner styles are added in their own <style>, and identical mirror files get the same bytes.
   let body
   try { body = tree.map(htmlNode).join('') } catch (e) { problems.push(`${docId}: ${e.message}`); continue }
   const owner = treeCss(tree, assetPath)
   let head = text.slice(meta.html.head[0], meta.html.head[1])
   if (owner) { const at = head.toLowerCase().lastIndexOf('</head>'); head = head.slice(0, at) + `<style data-ttros-studio="">\n${owner}\n</style>\n` + head.slice(at) }
   const next = head + body + text.slice(meta.html.tail[0], meta.html.tail[1])
   out[docId] = next
   for (const mirror of meta.html.mirrors || []) out[mirror] = next
   continue
  }
  let jsx
  try {
   jsx = created ? list(tree, null) : tree.length === 1 && tree[0].type === 'El' && !meta.fragment ? node(tree[0], null) : `<>\n${list(tree, null)}\n</>`
  } catch (e) { problems.push(`${docId}: ${e.message}`); continue }
  if (created) {
   out[docId] = created.kind === 'post' ? postSource(docId, created, jsx, needed) : landingSource(docId, created, jsx, needed, {assetPath})
   continue
  }
  let next = text.slice(0, meta.ret[0]) + '(\n' + jsx + '\n)' + text.slice(meta.ret[1])
  const imports = []
  if (needed.has('Link') && !Object.values(meta.imports).includes('next/link') && !meta.imports.Link) imports.push('import Link from "next/link";')
  if (needed.has('Image') && !Object.values(meta.imports).includes('next/image') && !meta.imports.Image) imports.push('import Image from "next/image";')
  if (needed.has('Fragment') && !meta.imports.Fragment) imports.push('import { Fragment } from "react";')
  for (const item of [...needed].filter(n => n.startsWith('kit:') || n.startsWith('comp:')).sort()) {
   const [kind, name, from] = item.split(':')
   const source = kind === 'kit' ? Object.values(KIT_CODES).find(c => c.kit.name === name).kit.from : from
   if (meta.imports[name] === source) continue
   if (meta.imports[name]) { problems.push(`${docId}: ${name} is already imported from ${meta.imports[name]}; rename the component.`); continue }
   imports.push(`import ${name} from "${source}";`)
  }
  if (imports.length) {
   const directive = next.match(/^\s*["']use client["'];?\s*\n/)
   next = directive ? directive[0] + imports.join('\n') + '\n' + next.slice(directive[0].length) : imports.join('\n') + '\n' + next
  }
  out[docId] = next
 }
 if (problems.length) throw Error(problems.join(' · '))
 const css = treeCss(Object.values(docs).flat(), assetPath)
 if (css) {
  out[cssFile] = `/* Generated by TTROS Website Studio: owner styles for edited elements. */\n${css}\n`
  const layout = files.get('app/layout.tsx')
  const anchor = 'import "./globals.css";'
  if (!layout || layout.split(anchor).length !== 2) throw Error('Website layout drift: globals.css import not found.')
  out['app/layout.tsx'] = layout.replace(anchor, `${anchor}\nimport "./${cssFile.replace(/^app\//, '')}";`)
 }
 return out
}
