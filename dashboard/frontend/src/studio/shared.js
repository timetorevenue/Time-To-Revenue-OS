// Shared data uses the existing deterministic reducer and independent draft/version history.
import { newContrastFailures } from './contrast.js'
import shared from './shared.json' with { type:'json' }
import { sectionRules, pageIds, pageInfo, pageAnchors, originalAnchors } from './sections.js'
import { defaultDesign, localStyle } from './design.js'
export { shared }
export const sharedFields={SharedHeader:{name:'Header wordmark',logo:'Header logo',cta:'Header button label',destination:'Header button destination'},SharedFooter:{name:'Footer wordmark',logo:'Footer logo',tagline:'Footer description',explore:'Explore heading',learn:'Learning heading',cta:'Footer contact label',destination:'Footer contact destination',email:'Footer email'}}
export const navigationFields={SharedHeader:['navigation'],SharedFooter:['learning','legal']}
export const rows=(item,key)=>JSON.parse(item.props[key])
export function sharedLinkProblem(href,drafts={}) {
 if(typeof href!=='string')return 'Invalid destination.'
 const [route,anchor,...extra]=href.split('#')
 if(extra.length || !sectionRules.links.routes.includes(route))return 'Choose an existing website route.'
 if(anchor!==undefined) {
  const page=pageIds.find(p=>pageInfo(p).route===route)
  if(!anchor || !page || !(drafts[page]?pageAnchors(drafts[page].content):originalAnchors(page)).has(anchor))return `Anchor #${anchor} does not exist on ${route}.`
 }
 return null
}
export function validateSharedContent(content,drafts={}) {
 for(const item of content) {
  const p=item.props
  for(const [key,value] of Object.entries(p)) {
   if(['id','styles',...(navigationFields[item.type]||[])].includes(key))continue
   if(typeof value!=='string' || !value.trim() || value.length>500 || /[<>\x00-\x1f]/.test(value))throw Error(`Invalid shared ${key}.`)
  }
  if(!shared.logos.includes(p.logo))throw Error('Choose a registered site logo.')
  if(sharedLinkProblem(p.destination,drafts))throw Error(sharedLinkProblem(p.destination,drafts))
  if(item.type==='SharedFooter' && !/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(p.email))throw Error('Invalid footer email.')
  for(const key of navigationFields[item.type]||[]) {
   let list;try{list=rows(item,key)}catch{throw Error('Invalid navigation rows.')}
   if(!Array.isArray(list)||list.length>12)throw Error('Navigation holds at most 12 items per group.')
   const ids=new Set(),hrefs=new Set()
   for(const row of list) {
    if(!row||Object.keys(row).sort().join()!=='href,id,label'||typeof row.id!=='string'||!/^[a-z][a-z0-9-]{1,63}$/.test(row.id)||ids.has(row.id))throw Error('Navigation needs unique stable IDs.')
    if(typeof row.label!=='string'||!row.label.trim()||row.label.length>100||/[<>\x00-\x1f]/.test(row.label))throw Error('Navigation needs a plain-text label (1–100 characters).')
    const error=sharedLinkProblem(row.href,drafts);if(error)throw Error(error)
    if(hrefs.has(row.href))throw Error('Duplicate navigation destination in this group.')
    ids.add(row.id);hrefs.add(row.href)
   }
  }
 }
 return content
}
// Old full designs retain their explicit differences. pageStyles can override back to a default.
export function effectiveDesign(data,site) {
 if(!site)return data.root.props.design
 const own=data.root.props.pageStyles ?? Object.fromEntries(Object.entries(data.root.props.design).filter(([k,v])=>v!==defaultDesign[k]))
 return {...site.root.props.design,...own}
}
export function effectiveData(data,site) {return {...data,root:{props:{...data.root.props,design:effectiveDesign(data,site)}}}}
const cssProp=k=>k.startsWith('--')?k:k.replace(/[A-Z]/g,m=>'-'+m.toLowerCase())
const unitless=new Set(['fontWeight','lineHeight','opacity'])
// Editor-canvas resets only: the canvas snapshot lacks the website's own base layer. The built site
// keeps its source CSS, so the site adapter uses {site:true} (explicit shared local styles only).
export const sharedBaseCss='.footer-contact>a:last-child{display:inline-flex;min-height:24px;align-items:center}.site-header nav ul,.site-footer nav ul{list-style:none;padding:0;margin:0}'
export function sharedCss(data,{site=false}={}) {
 return (site?'':sharedBaseCss)+data.content.map(item=>{
  const root=item.type==='SharedHeader'?'.site-header':'.site-footer'
  return shared.sections.find(s=>s.type===item.type).targets.map(t=>{
   const values=localStyle(item.props.styles[t.id]||{})
   const declarations=Object.entries(values).map(([k,v])=>`${cssProp(k)}:${typeof v==='number'&&!unitless.has(k)?v+'px':v}`).join(';')
   const selector=t.selector?t.selector.split(',').map(s=>root+' '+s).join(','):root
   return declarations?`${selector}{${declarations}}`:''
  }).join('')
 }).join('')
}
export function renameDependencies(page,name,drafts,site) {
 const route=pageInfo(page).route,out=[]
 for(const item of site.content)for(const key of navigationFields[item.type]||[])for(const row of rows(item,key))if(row.href.split('#')[0]===route && row.label!==name)out.push({scope:'site',section:item.props.id,field:key,row:row.id,before:row.label,after:name})
 return out
}

export function siteNewFailures(page,base,next,drafts,site) {
 if(page!=='site')return newContrastFailures(effectiveData(base,site),effectiveData(next,site))
 return [...newContrastFailures(base,next),...Object.entries(drafts).flatMap(([id,data])=>newContrastFailures(effectiveData(data,base),effectiveData(data,next)).map(f=>({...f,page:id})))]
}
