// Stage 3: every function takes the page it validates. `page` may be omitted: it is then the
// page the instances belong to (all must share one), so homepage callers are unchanged.
import {migrateItem,ownTexts,slotsFor,validateChildren,walkItems,editDestination,styleDestination,isNested,applyComponentPatches} from './nested.js'
import snapshot from './homepage.json' with { type: 'json' }
import source from './source-manifest.json' with { type: 'json' }
import serviceSource from './service-manifest.json' with { type: 'json' }
import { defaultDesign, schema, validateTokens, validateStyles } from './design.js'
import { ID_PATTERN, sectionRules, typeOf, linkKeys, newInstance, pageAnchors, linkProblem, limitProblem, applySectionOperation, pageTypes, pageOfContent, studioPages } from './sections.js'
import { validateSharedContent } from './shared.js'
import { migrateEnvelope, SCHEMA_VERSION } from './migrate.js'
export { snapshot, migrateEnvelope }
// Schema v5 (website 215679e). v1–v4 drafts (c58e641) migrate on read through migrate.js.
export const DRAFT_VERSION = SCHEMA_VERSION
// The original instances keep their saved IDs (Section0…Section8, HeroSignature); new ones are allocated.
// Pure: bundles that do not use it (the site adapter) drop it.
export const initialData = /* @__PURE__ */ (() => ({ root: { props: { design: { ...defaultDesign } } }, content: snapshot.sections.map(s => newInstance(s.type, s.type)) }))()
// Each page starts from the actual website: its original instances keep their type names as IDs.
export function initialDataFor(page = 'home') {
 if (page === 'home') return structuredClone(initialData)
 if (!studioPages.pages[page]) throw Error('Unknown Studio page.')
 return { root: { props: { design: { ...defaultDesign } } }, content: pageTypes(page).map(s => newInstance(s.type, s.type)) }
}
// Retain the key to migrate the existing browser draft in place, without losing it.
export const DRAFT_KEY = 'ttros-studio:global:default-site:home:v1'
export const draftKey = (page = 'home') => {
 const info = studioPages.pages[page]
 if (!info) throw Error('Unknown Studio page.')
 return info.storageKey
}
const manifests = { 'source-manifest.json': source, 'service-manifest.json': serviceSource }
export const pageSource = page => manifests[studioPages.pages[page].manifest]
// Validates and normalises a page. Drafts saved before Stage 2 have no link fields: they take
// the component's original destinations, so nothing they contained is lost or changed.
export function validateData(data, page) {
 if (!data || !Array.isArray(data.content)) throw Error('Draft must contain a list of sections.')
 if (!data.content.length || data.content.length > sectionRules.maxSections) throw Error(`A page holds 1–${sectionRules.maxSections} sections.`)
 if (data.zones && Object.keys(data.zones).length) throw Error('Nested zones are unsupported.')
 if(data.root!==undefined && (!data.root || typeof data.root!=='object' || Array.isArray(data.root)))throw Error('Invalid root.')
 const root=data.root?.props ?? {}
 if(!root || typeof root!=='object' || Array.isArray(root))throw Error('Invalid root properties.')
 if (Object.keys(root).some(k=>!['design','id','pageStyles','unsupported'].includes(k))) throw Error('Unknown root field.')
 const design=validateTokens(root.design ?? defaultDesign,schema.globalTokens)
 const pageId=page ?? pageOfContent(data.content)
 if(!studioPages.pages[pageId]) throw Error('Unknown Studio page.')
 const route=studioPages.pages[pageId].route
 const seen=new Set(),nestedCount={value:0}
 const content=data.content.map(input=>{
  const item=migrateItem(input)
  if(!item || typeof item!=='object' || !item.props || typeof item.props!=='object' || Array.isArray(item.props)) throw Error('Invalid section.')
  const section=typeOf(item.type)
  if(!section) throw Error('Unknown section type.')
  if(section.nested||section.page!==pageId) throw Error(`${section.label} (${item.type}) belongs to another page.`)
  const id=item.props.id
  if(typeof id!=='string' || !ID_PATTERN.test(id) || seen.has(id)) throw Error('Invalid or duplicate section ID.')
  seen.add(id)
  const texts=ownTexts(item.type,section.texts),links=linkKeys(section),allowed=new Set(['id','styles',...Object.keys(slotsFor(item.type)),...texts,...links])
  if(Object.keys(item.props).some(k=>!allowed.has(k))) throw Error('Unknown text field.')
  const props={id}
  for(const key of Object.keys(slotsFor(item.type)))props[key]=item.props[key]
  for(const key of texts) {
   if(typeof item.props[key]!=='string' || item.props[key].length>4000) throw Error('Invalid text.')
   props[key]=item.props[key]
  }
  links.forEach((key,index)=>{
   const value=item.props[key] ?? section.links[index]
   if(typeof value!=='string') throw Error('Invalid link.')
   props[key]=value
  })
  return validateChildren({type:item.type,props:{...props,styles:validateStyles(item.props.styles ?? {},section)}},{seen,validateStyles,count:nestedCount})
 })
 const limit=limitProblem(content,pageId)
 if(limit) throw Error(limit)
 const anchors=pageAnchors(content)
 for(const item of content) for(const [index,key] of linkKeys(typeOf(item.type)).entries()) {
  const problem=linkProblem(item.props[key],anchors,route)
  if(problem) throw Error(`${typeOf(item.type).label} link ${index+1}: ${problem}`)
 }
 if(pageId==='site')validateSharedContent(content)
 const pageStyles=root.pageStyles===undefined?{}:{pageStyles:validateTokens(root.pageStyles,schema.globalTokens,true)}
 const unsupported=root.unsupported===undefined?{}:{unsupported:validateUnsupported(root.unsupported)}
 return {root:{props:{design,...pageStyles,...unsupported}},content}
}
// One reducer for manual edits, David's proposals and workers. Section operations run first,
// so later edits and styles can address the instances they create.
export function applyProposal(data, proposal, {allowRemove=true,page}={}) {
 page=page ?? pageOfContent(data?.content)
 const next=structuredClone(validateData(data,page))
 if (!proposal || typeof proposal!=='object' || Object.keys(proposal).some(k=>!['summary','edits','order','design','styles','sections','components'].includes(k))) throw Error('Unsupported proposal operation.')
 if(!Array.isArray(proposal.edits) || proposal.edits.length>100) throw Error('Invalid proposal edits.')
 if(proposal.sections!==undefined) {
  if(!Array.isArray(proposal.sections) || proposal.sections.length>sectionRules.maxSectionOperations) throw Error('Invalid section operations.')
  for(const op of proposal.sections) next.content=applySectionOperation(next.content,op,{allowRemove})
 }
 for(const edit of proposal.edits) {
  const dest=editDestination(next.content,edit?.section,edit?.field)
  const item=dest?.item,field=dest?.field
  const section=item && typeOf(item.type)
  if(!section || !(Object.hasOwn(section.texts,field) || linkKeys(section).includes(field)) || typeof edit.text!=='string' || edit.text.length>4000) throw Error('Proposal contains an unsupported field.')
  item.props[field]=edit.text
 }
 if(proposal.order!==undefined) {
  if(!Array.isArray(proposal.order) || proposal.order.length!==next.content.length || new Set(proposal.order).size!==next.content.length || proposal.order.some(id=>!next.content.some(i=>i.props.id===id))) throw Error('Invalid section order.')
  next.content=proposal.order.map(id=>next.content.find(i=>i.props.id===id))
 }
 if(proposal.design!==undefined) {
  const values=validateTokens(proposal.design,schema.globalTokens,true)
  if(page!=='site')next.root.props.pageStyles={...(next.root.props.pageStyles??Object.fromEntries(Object.entries(next.root.props.design).filter(([k,v])=>v!==defaultDesign[k]))),...values}
  Object.assign(next.root.props.design,values)
 }
 if(proposal.styles!==undefined) {
  if(!Array.isArray(proposal.styles) || proposal.styles.length>100) throw Error('Invalid style operations.')
  for(const op of proposal.styles) {
   if(!op || Object.keys(op).sort().join()!=='section,target,values') throw Error('Invalid style operation.')
   const dest=styleDestination(next.content,op.section,op.target)
   const item=dest?.item,target=dest?.target
   const section=item && typeOf(item.type)
   if(!section) throw Error('Unknown section.')
   // null clears a complete target override; individual null values clear properties.
   if(op.values===null) {
    validateStyles({[target]:{}},section)
    delete item.props.styles[target]
   } else {
    if(!op.values || typeof op.values!=='object' || Array.isArray(op.values)) throw Error('Invalid local style values.')
    const existing={...item.props.styles[target]}
    for(const [key,value] of Object.entries(op.values)) {
     validateStyles({[target]:{[key]:value===null?schema.localTokens[key]?.default:value}},section)
     if(value===null) delete existing[key];else existing[key]=value
    }
    item.props.styles[target]=existing
   }
  }
 }
 if(proposal.components!==undefined)applyComponentPatches(next.content,proposal.components)
 return validateData(next,page)
}
export const fingerprint = data => JSON.stringify(validateData(data))
// The homepage envelope is unchanged from Stage 2 (no page key), so existing exports, server
// versions and the site adapter keep working. Other pages name their page and source manifest.
export function exportDraft(data, page) {
 page=page ?? pageOfContent(data?.content)
 const value=validateData(data,page)
 return page==='home'?{version:DRAFT_VERSION,project:'default-site',source:structuredClone(source),data:value}:{version:DRAFT_VERSION,project:'default-site',page,source:structuredClone(pageSource(page)),data:value}
}
export const envelopePage = value => value?.page ?? 'home'
// Values a migration could not place (migrate.js). Retained verbatim, never rendered.
export function validateUnsupported(list) {
 if(!Array.isArray(list) || list.length>20) throw Error('Invalid retained values.')
 return list.map(entry=>{
  const size=entry&&JSON.stringify(entry.value)?.length
  if(!entry || typeof entry!=='object' || Array.isArray(entry) || Object.keys(entry).sort().join()!=='path,reason,value' || typeof entry.path!=='string' || !entry.path || entry.path.length>200 || typeof entry.reason!=='string' || entry.reason.length>300 || size===undefined || size>400000) throw Error('Invalid retained value.')
  return structuredClone(entry)
 })
}
// v1–v4 envelopes migrate on read (migrate.js): same IDs, edited text, order and styles.
export function validateEnvelope(value, expectedPage) {
 const page=envelopePage(value)
 if(!studioPages.pages[page] || (expectedPage!==undefined && page!==expectedPage)) throw Error('Draft belongs to a different Studio page.')
 const {envelope}=migrateEnvelope(value)
 if(envelope.version!==DRAFT_VERSION || (page==='home' && envelope.page!==undefined) || envelope.project!=='default-site' || JSON.stringify(envelope.source)!==JSON.stringify(pageSource(page))) throw Error('Draft source/version does not match this website adapter.')
 return validateData(envelope.data,page)
}
export function loadDraft(storage, page='home') {
 const raw=storage.getItem(draftKey(page))
 if(!raw) {
  const data=initialDataFor(page)
  // Legacy homepage design supplied intentional global tokens before Shared site.
  if(page==='site' && storage.getItem(draftKey('home'))) {
   const home=loadDraft(storage,'home')
   if(home.root.props.pageStyles===undefined)data.root.props.design={...home.root.props.design}
  }
  return data
 }
 return validateEnvelope(JSON.parse(raw),page)
}
export function saveDraft(storage,data,page) {
 page=page ?? pageOfContent(data?.content)
 storage.setItem(draftKey(page),JSON.stringify({...exportDraft(data,page),savedAt:new Date().toISOString()}))
}
