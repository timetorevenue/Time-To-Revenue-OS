// One bounded component tree shared by Puck, validation and the real website adapter.
import rules from './nested-schema.json' with {type:'json'}
import snapshot from './homepage.json' with {type:'json'}
import {defaultGraphic,validateGraphic} from './workflow-graphic.js'
export {rules as nestedRules}
export const slotsFor=type=>rules.slots[type]||{}
export const isNested=type=>Object.hasOwn(rules.components,type)
export const legacyFields=type=>Object.values(slotsFor(type)).flatMap(s=>Object.keys(s.legacy?.fields||{}))
export const ownTexts=(type,texts)=>Object.keys(texts).filter(k=>!legacyFields(type).includes(k))
export const nestedTypes=Object.entries(rules.components).map(([type,spec])=>{
 const source=snapshot.sections.find(s=>s.type===spec.sourceType)
 const mapping=Object.values(slotsFor(spec.sourceType)).find(s=>s.legacy?.type===type).legacy.fields
 let html=source.html.match(new RegExp(`<${spec.sourceTag}\\b[^>]*class="${spec.sourceClass}"[\\s\\S]*?</${spec.sourceTag}>`))[0]
 for(const [old,key] of Object.entries(mapping))html=html.replaceAll(`data-field="${old}"`,`data-field="${key}"`)
 return {type,...spec,html,texts:Object.fromEntries(Object.entries(mapping).map(([old,key])=>[key,source.texts[old]])),page:'home',nested:true,links:[],domIds:[],min:0,max:1,slug:'workflow',originalId:'Section0-workflow'}
})
// IDs are deterministic for legacy migration and retained verbatim after that first read.
export function childId(parent,slot) {
 const full=`${parent}-${slot}`;if(full.length<=64)return full
 let hash=2166136261;for(const c of full)hash=Math.imul(hash^c.charCodeAt(0),16777619)>>>0
 return `${parent.slice(0,48)}-${hash.toString(16).padStart(8,'0')}`
}
export function migrateItem(input,depth=0) {
 if(depth>rules.maxDepth)throw Error('Nested component depth exceeded.')
 if(!input||typeof input!=='object'||!input.props)throw Error('Invalid component.')
 const item=structuredClone(input)
 for(const [key,slot]of Object.entries(slotsFor(item.type))) {
  const old=slot.legacy,p=item.props
  if(old&&Object.hasOwn(p,'graphic')&&Object.hasOwn(p,key))throw Error('Mixed legacy and native graphic data.')
  if(!Object.hasOwn(p,key)&&old&&Object.keys(old.fields).every(f=>Object.hasOwn(p,f))) {
   const props={id:childId(p.id,key),...Object.fromEntries(Object.entries(old.fields).map(([f,k])=>[k,p[f]])),graphic:structuredClone(Object.hasOwn(p,'graphic')?p.graphic:defaultGraphic),styles:{...(p.styles?.[old.styleTarget]?{section:structuredClone(p.styles[old.styleTarget])}:{})}}
   p[key]=[{type:old.type,props}]
   for(const f of Object.keys(old.fields))delete p[f]
   delete p.graphic
   if(p.styles)delete p.styles[old.styleTarget]
  }
  if(Array.isArray(p[key]))p[key]=p[key].map(c=>migrateItem(c,depth+1))
 }
 return item
}
export function* walkItems(content,depth=0) {
 if(depth>rules.maxDepth)throw Error('Nested component depth exceeded.')
 for(const item of content){yield item;for(const key of Object.keys(slotsFor(item.type)))if(Array.isArray(item.props[key]))yield*walkItems(item.props[key],depth+1)}
}
export const findItem=(data,id)=>[...walkItems(data.content)].find(i=>i.props.id===id)
export function validateChildren(item,{seen,validateStyles,depth=0,count={value:0}}) {
 if(depth>rules.maxDepth||++count.value>rules.maxComponents)throw Error('Nested component budget exceeded.')
 for(const [key,slot]of Object.entries(slotsFor(item.type))) {
  const list=item.props[key]
  if(!Array.isArray(list)||list.length<slot.min||list.length>slot.max)throw Error(`${item.type}.${key} requires exactly ${slot.min} graphic component.`)
  item.props[key]=list.map(child=>{
   if(!child||!slot.allow.includes(child.type)||!isNested(child.type)||!child.props)throw Error(`Unsupported component in ${item.type}.${key}.`)
   const spec=nestedTypes.find(s=>s.type===child.type),p=child.props
   if(typeof p.id!=='string'||!/^[A-Za-z][A-Za-z0-9-]{1,63}$/.test(p.id)||seen.has(p.id))throw Error('Invalid or duplicate nested component ID.')
   seen.add(p.id)
   const keys=['id','styles',...Object.keys(spec.texts),...Object.keys(slotsFor(child.type)),...(spec.graphic?['graphic']:[])]
   if(Object.keys(p).some(k=>!keys.includes(k)))throw Error('Unknown nested component field.')
   for(const key of Object.keys(spec.texts))if(typeof p[key]!=='string'||p[key].length>4000)throw Error('Invalid nested text.')
   const value={type:child.type,props:{id:p.id,...Object.fromEntries(Object.keys(spec.texts).map(k=>[k,p[k]])),...(spec.graphic?{graphic:validateGraphic(p.graphic)}:{}),...Object.fromEntries(Object.keys(slotsFor(child.type)).map(k=>[k,p[k]])),styles:validateStyles(p.styles??{},spec)}}
   validateChildren(value,{seen,validateStyles,depth:depth+1,count});return value
  })
 }
 return item
}
// Transient source projection, never serialized: original verified React templates retain their text destinations.
export function sourceItem(input) {
 const item=structuredClone(input)
 for(const [key,slot]of Object.entries(slotsFor(item.type))) {
  const child=item.props[key]?.[0],old=slot.legacy
  if(!child||!old)continue
  for(const [f,k]of Object.entries(old.fields))item.props[f]=child.props[k]
  item.props.graphic=child.props.graphic
  item.props.styles={...item.props.styles,...(child.props.styles?.section?{[old.styleTarget]:child.props.styles.section}:{})}
 }
 return item
}
// Old proposal text/style destinations remain readable aliases, not duplicate state.
export function editDestination(content,id,field) {
 const item=[...walkItems(content)].find(i=>i.props.id===id)
 if(!item)return null
 for(const [slot,spec]of Object.entries(slotsFor(item.type)))if(Object.hasOwn(spec.legacy?.fields||{},field))return {item:item.props[slot][0],field:spec.legacy.fields[field]}
 return {item,field}
}
export function styleDestination(content,id,target) {
 const item=[...walkItems(content)].find(i=>i.props.id===id)
 if(!item)return null
 for(const [slot,spec]of Object.entries(slotsFor(item.type)))if(spec.legacy?.styleTarget===target)return {item:item.props[slot][0],target:'section'}
 return {item,target}
}
export function applyComponentPatches(content,operations) {
 if(!Array.isArray(operations)||operations.length>20)throw Error('Invalid component operations.')
 for(const op of operations) {
  if(!op||Object.keys(op).sort().join()!=='component,values'||!op.values||typeof op.values!=='object'||Array.isArray(op.values))throw Error('Invalid component operation.')
  const item=[...walkItems(content)].find(i=>i.props.id===op.component),spec=item&&rules.components[item.type]
  if(!spec||Object.keys(op.values).some(k=>k!=='graphic'||!spec.graphic))throw Error('Unknown component property.')
  if(op.values.graphic!==undefined) {
   if(!op.values.graphic||typeof op.values.graphic!=='object'||Array.isArray(op.values.graphic))throw Error('Invalid graphic patch.')
   item.props.graphic=validateGraphic({...item.props.graphic,...op.values.graphic})
  }
 }
}
export function rekeyChildren(item) {
 for(const [key]of Object.entries(slotsFor(item.type)))for(const [index,child]of(item.props[key]||[]).entries()){
  child.props.id=childId(item.props.id,index?key+'-'+index:key);rekeyChildren(child)
 }
 return item
}

export function editBefore(data,edit){const d=editDestination(data.content,edit.section,edit.field);return d?.item.props[d.field]}
export function styleBefore(data,edit){const d=styleDestination(data.content,edit.section,edit.target);return d?.item.props.styles[d.target]}
