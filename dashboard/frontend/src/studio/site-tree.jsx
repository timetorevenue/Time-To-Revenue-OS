import React from 'react'
import {sourceItem,slotsFor} from './nested.js'
import {nestedRenderers} from './workflow-render.jsx'
import { snapshot, validateData } from './state.js'
import { editableTags, elementTargets, localStyle } from './design.js'
import { typeOf, anchorMap, pageTypes } from './sections.js'
import { pageSeo } from './seo.js'
import services from './services.json' with { type: 'json' }
export { pageSeo }
const ID_REFS = ['id', 'aria-labelledby', 'aria-describedby', 'aria-controls', 'htmlFor']

// Apply the supported draft to the ORIGINAL website's server React elements.
// Only the caller's explicit, synchronous server components may be expanded.
// Next Link/Image and header/footer remain owned by Next and the website.
// Stage 2: each original section is a verified TEMPLATE. Every draft instance (original, added
// or duplicated) is rendered from its type's template with its own text, links, styles and,
// for copies, instance-scoped DOM IDs, so two instances of one component can coexist.
// Schema v5 (website 215679e): a template's root is the element its snapshot starts with, so the
// hero's companion <div class="hero-signature"> is a template too; the WorkflowFigure is the native
// child bound by nestedRenderers.
// Stage 3: the same verified-template adapter for every Studio page. A service page's sections are
// the real ServicePage.tsx sections (services.json); its JSON-LD follows the visible service name and
// description through pageSeo (seo.js), the same validated page model the Studio shows.
export function adaptHomeChildren(children, input, components, objects={}) {return adaptPageChildren(children,input,components,'home',objects)}
export function adaptPageChildren(children, input, components, page='home', objects={}) {
 const data=validateData(input,page)
 const originals=page==='home'?snapshot.sections:services.pages[page].sections
 if(originals.map(s=>s.type).join()!==pageTypes(page).map(s=>s.type).join())throw Error('Studio page types drifted.')
 const allowed=new Set(components)
 // Root element (tag and class) of every snapshot template, in source order.
 const roots=originals.map(s=>{const m=s.html.match(/^<([a-z]+)\b[^>]*?(?:\sclass="([^"]*)")?[^>]*>/);return {tag:m[1],className:m[2]??''}})
 const isTemplate=node=>React.isValidElement(node)&&roots.some(r=>r.tag===node.type&&(r.tag==='section'||(node.props.className??'')===r.className))
 function expand(node) {
  if(Array.isArray(node))return node.map(expand)
  if(!React.isValidElement(node))return node
  if(typeof node.type==='function' && allowed.has(node.type)){
   const expanded=expand(node.type(node.props))
   const identity=Object.entries(objects).find(([,component])=>component===node.type)?.[0]
   return identity?React.cloneElement(expanded,{'data-studio-component':identity}):expanded
  }
  if(node.props.children===undefined)return node
  return React.cloneElement(node,{},expand(node.props.children))
 }
 function merged(nodes) {
  const result=[]
  React.Children.toArray(nodes).forEach(node=>{
   if(typeof node==='string'||typeof node==='number'){
    if(typeof result.at(-1)==='string')result[result.length-1]+=String(node)
    else result.push(String(node))
   }else result.push(node)
  })
  return result
 }
 // Adjacent text nodes form one text field (snapshot order) but keep their original segments, so an
 // unchanged field renders exactly the source's text nodes (identical markup and text shaping).
 function segments(nodes) {
  const result=[]
  React.Children.toArray(nodes).forEach(node=>{
   if(typeof node==='string'||typeof node==='number'){
    const last=result.at(-1)
    if(last?.segment){last.text+=String(node);last.parts.push(node)}
    else result.push({segment:true,text:String(node),parts:[node]})
   }else result.push(node)
  })
  return result
 }
 const sections=[],preserved=[]
 for(const node of React.Children.toArray(children)) {
  const expanded=React.isValidElement(node) && (node.type==='section'||node.type==='div'||allowed.has(node.type))?expand(node):node
  if(isTemplate(expanded))sections.push(expanded)
  else preserved.push(node)
 }
 if(sections.length!==originals.length)throw Error('Website section structure drifted; refusing Studio draft.')
 const templates=new Map()
 sections.forEach((section,index)=>{
  const source=originals[index]
  // Verify the unedited template once: every text and element destination must match the snapshot.
  const root=roots[index]
  if(section.type!==root.tag||(root.tag!=='section'&&section.props.className!==root.className))throw Error(`Website section structure drifted at ${source.type}.`)
  render(section,{type:source.type,props:{id:typeOf(source.type).originalId,...source.texts,...Object.fromEntries(typeOf(source.type).links.map((h,i)=>[`link${i}`,h])),styles:{}}})
  templates.set(source.type,section)
 })
 function render(section,nativeItem) {
  const item=sourceItem(nativeItem)
  const source=originals.find(s=>s.type===item.type),meta=typeOf(item.type),ids=anchorMap(item)
  const counts={},targets=[];let textIndex=0,linkIndex=0
  function visit(node,isRoot=false) {
   if(node?.segment) {
    if(!node.text.trim())return node.parts
    const key=`text${textIndex++}`
    if(source.texts[key]!==node.text)throw Error(`Website text drift at ${source.type}.${key}`)
    return item.props[key]===node.text?node.parts:item.props[key]
   }
   if(!React.isValidElement(node))return node
   if(node.type==='svg')return node
   const tag=typeof node.type==='string'?node.type:node.props.href?'a':null
   const target=isRoot?'section':editableTags.has(tag)?`${tag}-${counts[tag]||0}`:null
   if(target&&!isRoot)counts[tag]=(counts[tag]||0)+1
   const props={}
   for(const key of ID_REFS)if(typeof node.props[key]==='string')props[key]=node.props[key].split(/\s+/).map(v=>ids[v]??v).join(' ')
   if(node.props.href!==undefined) {
    if(node.props.href!==meta.links[linkIndex])throw Error(`Website link drift at ${source.type}.link${linkIndex}`)
    props.href=item.props[`link${linkIndex++}`]
   }
   if(target){
    targets.push(target)
    props['data-studio-element']=target
    if(source.type==='Section0'&&target==='figure-0'&&node.props['data-studio-component']!=='WorkflowFigure')throw Error('WorkflowFigure identity drift; refusing graphic edit.')
    props.style={...node.props.style,...localStyle(item.props.styles[target])}
   }
   if(isRoot){props['data-studio-section']=item.props.id;props['data-studio-type']=item.type}
   const rendered=node.props.children===undefined?React.cloneElement(node,props):React.cloneElement(node,props,...segments(node.props.children).flatMap(n=>{const v=visit(n);return Array.isArray(v)?v:[v]}))
   const identity=node.props['data-studio-component'],slot=Object.keys(slotsFor(nativeItem.type)).find(k=>slotsFor(nativeItem.type)[k].allow.includes(identity))
   // The native child binds its own fields to the SOURCE element (as the canvas does): the visit above
   // has verified that element's text and structure, and a child field may legitimately be empty.
   return slot&&nativeItem.props[slot]?nestedRenderers[identity](node.props.children===undefined?node:React.cloneElement(node,{},...merged(node.props.children)),nativeItem.props[slot][0].props):rendered
  }
  const next=visit(section,true)
  if(textIndex!==Object.keys(source.texts).length || linkIndex!==meta.links.length || targets.join()!==elementTargets({...source,type:"source-template"}).map(t=>t.id).join())throw Error(`Website destination structure drift at ${source.type}`)
  return next
 }
 if(page!=='home') {
  // Service page: the original graph must be the snapshot's (drift check); the rendered graph is
  // pageSeo's, which changes only names and descriptions derived from visible text.
  const seo=pageSeo(page,data)
  let found=0
  const retained=preserved.map(node=>{
   if(!React.isValidElement(node)||!node.props.data?.['@graph'])return node
   found++
   if(JSON.stringify(node.props.data)!==JSON.stringify(services.pages[page].seo.jsonLd))throw Error('Website structured data drifted; refusing Studio draft.')
   return React.cloneElement(node,{data:seo.jsonLd})
  })
  if(found!==1)throw Error('Website structured data drifted; refusing Studio draft.')
  return <>{retained}{data.content.map(item=>React.cloneElement(render(templates.get(item.type),item),{key:item.props.id}))}</>
 }
 // Keep FAQ JSON-LD synchronized with the visible editable FAQ. All other schema
 // and metadata remain source-owned, including canonical URLs and service data.
 const faqItem=data.content.find(i=>i.type==='Section7')
 const retained=preserved.map(node=>{
  if(!React.isValidElement(node)||!node.props.data?.['@graph'])return node
  const schema=structuredClone(node.props.data)
  // A removed FAQ takes its structured data with it; at most one FAQ is allowed (section-rules.json).
  if(!faqItem)schema['@graph']=schema['@graph'].filter(entity=>entity['@type']!=='FAQPage')
  else for(const entity of schema['@graph'])if(entity['@type']==='FAQPage')entity.mainEntity.forEach((entry,index)=>{
   entry.name=faqItem.props[`text${index*2+1}`];entry.acceptedAnswer.text=faqItem.props[`text${index*2+2}`]
  })
  return React.cloneElement(node,{data:schema})
 })
 return <>{retained}{data.content.map(item=>React.cloneElement(render(templates.get(item.type),item),{key:item.props.id}))}</>
}
