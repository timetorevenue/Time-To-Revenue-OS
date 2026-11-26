// A Studio page = its route document with the shared documents it renders (Header, Footer,
// HeroDiagram, a blog post body) composed in place, so the canvas shows and edits the whole page.
// split() returns every document's tree again; elements dragged between documents move with it.
// A component template (FaqAccordion) is composed into the first Use of it on the page, and the data
// records a route shows (its blog post / resource) are composed as a hidden `Data` node, so every edit
// of them is one Puck history step like any other.
import {walk} from './model.js'

const clone = v => JSON.parse(JSON.stringify(v))
export function compose(page, docs, depth = 0) {
 const tree = clone(docs[page.doc] || [])
 const templates = new Set()
 const expand = (list, level) => {
  for (const item of list) {
   const p = item.props
   if (item.type === 'Use') {
    let target = p.doc || page.bind?.[p.id]
    if (!target && p.tpl && docs[p.tpl] && !templates.has(p.tpl)) { target = p.tpl; templates.add(p.tpl) }
    if (target && docs[target] && level < 6) {
     p.kids = expand(clone(docs[target]), level + 1)
     p.shared = target
    }
   }
   for (const s of ['kids', 'alt']) if (Array.isArray(p[s]) && !(item.type === 'Use' && p.shared && s === 'kids')) expand(p[s], level)
  }
  return list
 }
 const out = expand(tree, depth)
 for (const file of Object.keys(page.data || {})) {
  if (docs[file]?.length) out.push({type: 'Data', props: {id: `${docs[file][0].props.id.split('_')[0]}_data`, shared: file, kids: clone(docs[file])}})
 }
 return out
}

// Inverse of compose: page document plus each shared document found inside it.
export function split(page, content) {
 const out = {}
 const strip = (list, doc) => list.filter(item => {
  if (item.type !== 'Data') return true
  out[item.props.shared] = strip(item.props.kids || [], item.props.shared)
  return false
 }).map(item => {
  const copy = {type: item.type, props: {...item.props}}
  const p = copy.props
  if (item.type === 'Use' && p.shared) {
   const target = p.shared
   const inner = strip(p.kids || [], target)
   if (out[target] && canonical(out[target]) !== canonical(inner)) throw Error(`${target} appears twice on this page with different edits.`)
   out[target] = inner
   delete p.kids
   delete p.shared
   return copy
  }
  for (const s of ['kids', 'alt']) if (Array.isArray(p[s])) p[s] = strip(p[s], doc)
  return copy
 })
 out[page.doc] = strip(content, page.doc)
 return out
}

// Which document each element of a composed page belongs to.
export function owners(page, content) {
 const map = {}
 const visit = (list, doc) => {
  for (const item of list) {
   map[item.props.id] = doc
   if ((item.type === 'Use' || item.type === 'Data') && item.props.shared) visit(item.props.kids || [], item.props.shared)
   else for (const s of ['kids', 'alt']) if (Array.isArray(item.props[s])) visit(item.props[s], doc)
  }
 }
 visit(content, page.doc)
 return map
}

// Order-insensitive JSON (Puck rebuilds props with its own key order).
export const canonical = value => JSON.stringify(value, (k, v) => (v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().filter(key => v[key] !== undefined).map(key => [key, v[key]])) : v))
export const changedDocs = (docs, baseline) => Object.keys(docs).filter(id => canonical(docs[id]) !== canonical(baseline[id]))
export const countNodes = tree => { let n = 0; for (const _ of walk(tree)) n++; return n }
