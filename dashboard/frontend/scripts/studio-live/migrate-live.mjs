// Carry saved live-editor work onto a new website source commit. Zero models; deterministic.
// Usage: node migrate-live.mjs <request.json>
//   request: {from: <old snapshot dir>, to: <new snapshot dir>, docs: {docId: tree} (the version's
//   documents, as edited on the old commit), output: <result.json>}
// The old and new imports are aligned node by node (same kind/tag and source fingerprint first, then
// same kind/tag, by longest common subsequence per level). The owner's edits — changed fields,
// styles, text, moves, insertions, deletions — are replayed onto the new import: elements the new
// source added are kept where the source put them, fields the source changed but the owner did not
// take the new source value, and source references are re-bound to the new commit by their exact
// code. Anything that cannot be carried over is left as the new source and listed as a conflict.
// Revisit: when the model schema or node identity rules change. Last touched: 2026-11-22.
import fs from 'node:fs'
import path from 'node:path'
import {SLOTS, walk, validateTree} from '../../src/studio/live/model.js'

const read = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'))
const clone = v => JSON.parse(JSON.stringify(v))
const canonical = value => JSON.stringify(value, (k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(key => [key, v[key]])) : v))
const STRUCT = new Set(['id', 'kids', 'alt', 'from'])

export function migrate(oldSnap, newSnap, docs) {
 const report = {applied: [], conflicts: [], added_by_source: 0, removed_by_source: 0, documents: {}}
 const out = {}
 const codeIndex = new Map() // (doc, kind, text) → new ref
 for (const [ref, c] of Object.entries(newSnap.pristine.codes)) codeIndex.set(`${c.doc}\u0000${c.kind}\u0000${c.text}`, ref)
 const rebind = (ref, doc) => {
  const c = oldSnap.pristine.codes[ref]
  if (!c) return null
  return codeIndex.get(`${c.doc === doc ? doc : c.doc}\u0000${c.kind}\u0000${c.text}`) ?? null
 }
 for (const [docId, tree] of Object.entries(docs)) {
  const base = oldSnap.model.docs[docId]?.tree
  const fresh = newSnap.model.docs[docId]?.tree
  if (!base) { report.conflicts.push({doc: docId, detail: 'not in the old import'}); continue }
  if (canonical(tree) === canonical(base)) continue // untouched: the new source as is
  if (!fresh) { report.conflicts.push({doc: docId, detail: 'the new source no longer has this document; its edits were not carried over'}); continue }
  const result = migrateDoc(docId, base, tree, fresh, rebind, oldSnap, newSnap)
  report.documents[docId] = result.stats
  report.conflicts.push(...result.conflicts.map(detail => ({doc: docId, detail})))
  report.added_by_source += result.stats.added_by_source
  report.removed_by_source += result.stats.removed_by_source
  try {
   const codes = Object.fromEntries(Object.entries(newSnap.model.codes).map(([k, c]) => [k, c.pure ? {...c, doc: docId} : c]))
   validateTree(result.tree, {codes, doc: docId})
   out[docId] = result.tree
   report.applied.push(docId)
  } catch (e) {
   report.conflicts.push({doc: docId, detail: `the migrated document did not validate (${e.message}); kept the new source`})
  }
 }
 return {docs: out, report}
}

const sig = (item, own) => `${item.type}|${item.props.tag || item.props.name || ''}|${own ?? ''}`
// Longest common subsequence of two lists under an equality predicate → index pairs.
function lcs(a, b, eq) {
 const n = a.length, m = b.length
 const t = Array.from({length: n + 1}, () => new Int32Array(m + 1))
 for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) t[i][j] = eq(a[i], b[j]) ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1])
 const pairs = []
 for (let i = 0, j = 0; i < n && j < m;) { if (eq(a[i], b[j])) { pairs.push([i, j]); i++; j++ } else if (t[i + 1][j] >= t[i][j + 1]) i++; else j++ }
 return pairs
}

function migrateDoc(docId, base, ours, fresh, rebind, oldSnap, newSnap) {
 const conflicts = []
 const map = new Map() // old import id → new import node
 const matched = new Set() // new import nodes that have an old counterpart
 const ownOf = (snap, id) => snap.pristine.nodes[id]?.own
 // 1. Align the old and new imports.
 const align = (a, b) => {
  const strict = lcs(a, b, (x, y) => sig(x, x.__o) === sig(y, y.__o))
  const used = new Set(strict.map(p => p[1])), taken = new Set(strict.map(p => p[0]))
  const pairs = [...strict]
  // Loose second pass between strict anchors: same kind and tag.
  const ra = a.map((x, i) => [x, i]).filter(([, i]) => !taken.has(i)), rb = b.map((y, j) => [y, j]).filter(([, j]) => !used.has(j))
  for (const [i, j] of lcs(ra, rb, ([x], [y]) => sig(x) === sig(y))) pairs.push([ra[i][1], rb[j][1]])
  for (const [i, j] of pairs) {
   map.set(a[i].props.id, b[j])
   matched.add(b[j])
   for (const s of SLOTS) if (Array.isArray(a[i].props[s]) && Array.isArray(b[j].props[s])) align(a[i].props[s], b[j].props[s])
  }
 }
 const tag = (list, snap) => { for (const {item} of walk(list)) Object.defineProperty(item, '__o', {value: ownOf(snap, item.props.id), enumerable: false}) }
 const b0 = clone(base), f0 = clone(fresh)
 tag(b0, oldSnap); tag(f0, newSnap)
 align(b0, f0)
 const baseById = new Map([...walk(b0)].map(e => [e.item.props.id, e.item]))
 const stats = {mapped: map.size, added_by_source: 0, removed_by_source: 0, carried: 0}
 // 2. Rebuild the owner's structure on the new import's nodes.
 const translate = (item, path) => {
  const p = item.props
  const old = baseById.get(p.id)
  const target = old ? map.get(p.id) : null
  let props
  if (old && target) {
   props = clone(target.props)
   for (const k of new Set([...Object.keys(old.props), ...Object.keys(p)])) {
    if (STRUCT.has(k)) continue
    if (canonical(old.props[k]) === canonical(p[k])) continue // owner kept the source value: take the new one
    props[k] = clone(p[k])
    stats.carried++
   }
   props.id = target.props.id
  } else if (old && !target) {
   const edited = canonical({...old.props, kids: undefined, alt: undefined}) !== canonical({...p, kids: undefined, alt: undefined})
   stats.removed_by_source++
   if (edited) conflicts.push(`${path}: the new source removed an element you edited (${p.tag || p.name || item.type}); your edit was not carried over`)
   return null
  } else props = clone(p) // inserted or duplicated by the owner
  // Source references move to the new commit's identical code.
  for (const key of ['c', 'use']) if (typeof props[key] === 'string' && !newSnap.pristine.codes[props[key]]) {
   const ref = rebind(props[key], docId)
   if (!ref) { conflicts.push(`${path}: the source code behind ${p.tag || p.name || item.type} changed; kept the new source there`); return target ? cloneFresh(target) : null }
   props[key] = ref
  }
  if (props.x) for (const [k, r] of Object.entries(props.x)) if (!newSnap.pristine.codes[r]) {
   const ref = rebind(r, docId)
   if (ref) props.x[k] = ref
   else { conflicts.push(`${path}: the source behaviour ${k} changed; kept the new source element`); return target ? cloneFresh(target) : null }
  }
  for (const s of SLOTS) if (Array.isArray(p[s])) props[s] = p[s].map((k, i) => translate(k, `${path}/${i}`)).filter(Boolean)
  // Elements the new source added inside this node: kept after their aligned previous sibling.
  if (target) for (const s of SLOTS) if (Array.isArray(target.props[s])) {
   const list = props[s] || (props[s] = [])
   target.props[s].forEach((child, index) => {
    if (matched.has(child)) return
    stats.added_by_source++
    const prev = target.props[s].slice(0, index).reverse().find(x => list.some(y => y.props.id === x.props.id))
    const at = prev ? list.findIndex(y => y.props.id === prev.props.id) + 1 : 0
    list.splice(at, 0, cloneFresh(child))
   })
  }
  return {type: item.type, props}
 }
 const cloneFresh = node => JSON.parse(JSON.stringify(node))
 let tree = ours.map((k, i) => translate(k, `${docId}#${i}`)).filter(Boolean)
 // Roots the new source added.
 f0.forEach((child, index) => {
  if (matched.has(child)) return
  stats.added_by_source++
  const prev = f0.slice(0, index).reverse().find(x => tree.some(y => y.props.id === x.props.id))
  tree.splice(prev ? tree.findIndex(y => y.props.id === prev.props.id) + 1 : 0, 0, cloneFresh(child))
 })
 // Owner-made ids that now collide with new import ids get fresh ones.
 const seen = new Set()
 for (const {item} of walk(tree)) {
  if (seen.has(item.props.id)) item.props.id = `${item.props.id.split('_')[0]}_m${Math.random().toString(36).slice(2, 9)}`
  seen.add(item.props.id)
 }
 return {tree, conflicts, stats}
}

if (import.meta.url === `file://${process.argv[1]}`) {
 const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
 const oldSnap = {model: read(request.from, 'model.json'), pristine: read(request.from, 'pristine.json')}
 const newSnap = {model: read(request.to, 'model.json'), pristine: read(request.to, 'pristine.json')}
 const result = migrate(oldSnap, newSnap, request.docs)
 fs.writeFileSync(request.output, JSON.stringify({...result, from: oldSnap.model.commit, to: newSnap.model.commit, model_calls: 0}))
 console.log(JSON.stringify({ok: true, applied: result.report.applied.length, conflicts: result.report.conflicts.length}))
}
