import React, { createContext, useContext } from 'react'
import parse, { domToReact, attributesToProps } from 'html-react-parser'
import { defaultDesign, designCss, editableTags, localStyle } from './design'
import { SharedCanvas,SiteContext,SiteTheme } from './shared-canvas'
import {nestedRenderers} from './workflow-render'
import {slotsFor,ownTexts} from './nested'
import {WorkflowGraphicField} from './workflow-panel'
import { sectionTypes, anchorMap } from './sections'
import { GlobalDesignField, LocalStyleField, SelectionContext, ContrastContext } from './fields'
const ID_REFS = ['id', 'aria-labelledby', 'aria-describedby', 'aria-controls', 'for']
// The Studio page being edited (Stage 3). The canvas <main> carries it, exactly like the site adapter.
export const PageContext = createContext('home')
function PageMain({children}) {
 const page=useContext(PageContext)
 const site=useContext(SiteContext)?.data
 if(page==='site')return <div data-studio-page="site">{children}</div>
 return <>{site&&<SharedCanvas kind="SharedHeader" data={site} page={page}/>}<main id="main-content" className="editorial" data-studio-page={page}>{children}</main>{site&&<SharedCanvas kind="SharedFooter" data={site} page={page}/>}</>
}
// One real website section type, rendered for one instance: its own text, links, styles and
// (for added or duplicated copies) instance-scoped DOM IDs.
function Section({section,...props}) {
 const page=useContext(PageContext)
 const site=useContext(SiteContext)?.data
 const {select,selection}=useContext(SelectionContext)
 if(section.page==='site')return <SharedCanvas kind={section.type} data={{...site,content:site.content.map(i=>i.type===section.type?{type:section.type,props}:i)}} page={page} editable/>
 const failing=useContext(ContrastContext)
 const instance=props.id
 const ids=anchorMap({type:section.type,props:{id:instance}})
 const counts={}
 let link=0
 const options={replace:node=>{
  if(node.name==='studio-text')return <React.Fragment>{props[node.attribs['data-field']]}</React.Fragment>
  if(!node.name)return
  // The template root is its top-level element (a <section>, or the hero-signature <div>).
  const root=!node.parent
  const target=root?'section':editableTags.has(node.name)?`${node.name}-${counts[node.name]||0}`:null
  if(target && !root)counts[node.name]=(counts[node.name]||0)+1
  const slotEntry=Object.entries(slotsFor(section.type)).find(([,spec])=>spec.legacy?.styleTarget===target)
  if(slotEntry){const Slot=props[slotEntry[0]];return <Slot style={{display:'contents'}}/>}
  const raw={...node.attribs}
  for(const key of ID_REFS)if(raw[key])raw[key]=raw[key].split(/\s+/).map(v=>ids[v]??v).join(' ')
  if(node.name==='a' && raw.href!==undefined)raw.href=props[`link${link++}`] ?? raw.href
  const attrs=attributesToProps(raw)
  if(root){attrs['data-studio-section']=instance;attrs['data-studio-type']=section.type}
  if(target) {
   attrs['data-studio-element']=target
   attrs.style={...attrs.style,...localStyle(props.styles?.[target])}
   attrs.onClick=e=>{e.stopPropagation();if(node.name==='a')e.preventDefault();select(instance,target)}
   // Visible editor-only warning on elements whose text fails WCAG AA contrast (never in previews or the site).
   if(failing?.has(`${instance}:${target}`)){attrs['data-studio-contrast']='fail';attrs.style={...attrs.style,outline:'3px dashed #b42318',outlineOffset:'-3px'}}
   if(selection?.section===instance && selection.target===target)attrs.style={...attrs.style,outline:'2px solid #237a57',outlineOffset:'-2px'}
  }
  const children=node.children?.length?domToReact(node.children,options):undefined
  return React.createElement(node.name,attrs,children)
 }}
 return parse(section.html,options)
}
// Real Puck child: its DOM ref and selection belong entirely to Puck.
function NativeComponent({spec,puck,...props}) {
 const original=parse(spec.html,{replace:node=>node.name==='studio-text'?<>{spec.texts[node.attribs['data-field']]}</>:undefined})
 return nestedRenderers[spec.type](original,props,{ref:puck?.dragRef})
}
function Root({children,design=defaultDesign,pageStyles}) {
 const page=useContext(PageContext)
 return <><SiteTheme data={{root:{props:{design,...(pageStyles===undefined?{}:{pageStyles})}}}} page={page}/><PageMain>{children}</PageMain></>
}
export const config = {
 root: {
  fields:{design:{type:'custom',label:'Global Design',render:GlobalDesignField}},
  render:Root
 },
 components:Object.fromEntries(sectionTypes.map(s=>[s.type,s.nested?{
  label:s.label,inline:true,permissions:{drag:false,delete:false,duplicate:false,insert:false},
  fields:{...Object.fromEntries(Object.entries(s.textFields).map(([key,label])=>[key,{type:'text',label}])),graphic:{type:'custom',label:'Graphic properties',render:WorkflowGraphicField},styles:{type:'custom',label:'Graphic styles',render:props=><LocalStyleField {...props} section={s} fixedTarget="section"/>}},
  render:props=><NativeComponent spec={s} {...props}/>
 }:{
  label:s.label,
  fields:{
   ...Object.fromEntries(Object.entries(slotsFor(s.type)).map(([key,spec])=>[key,{type:'slot',allow:spec.allow}])),
   ...Object.fromEntries(ownTexts(s.type,s.texts).map(key=>[key,{type:'textarea',label:s.texts[key].slice(0,45),contentEditable:s.page!=='site'}])),
   ...Object.fromEntries(s.links.map((href,i)=>[`link${i}`,{type:'text',label:`Link ${i+1} destination (was ${href})`}])),
   styles:{type:'custom',label:'Section & element styles',render:props=><LocalStyleField {...props} section={s}/>}
  },
  render:props=><Section {...props} section={s}/>
 }]))
}
// Puck wraps inline-editable text in span[data-puck-overlay-portal]. Site rules such as
// `.outcome-list li > span` would otherwise colour those wrappers, so the canvas showed text in a
// colour the real site never uses. Wrappers carry no colour of their own (editor only; the site
// adapter and previews render no wrappers).
const wrapperCss='main.editorial [data-puck-overlay-portal]{color:inherit!important}main.editorial .bullet-list li>[data-puck-overlay-portal]{display:inline!important}.workflow-figure [data-puck-overlay-portal]{display:inline!important;font:inherit!important;letter-spacing:inherit!important;margin:0!important}'
// The site's Tailwind preflight and the two utilities ServicePage uses (mb-3, mt-3), which the
// homepage style snapshot does not carry. Scoped to service pages so the homepage canvas is unchanged.
const serviceSiteCss='main.editorial[data-studio-page^="service-"] .link-list{list-style:none;padding:0;margin:0}main.editorial[data-studio-page^="service-"] .mb-3{margin-bottom:.75rem}main.editorial[data-studio-page^="service-"] .mt-3{margin-top:.75rem}'
export function Frame({children}) {return <><link rel="stylesheet" href="/studio/homepage.css"/><style data-studio-editor="">{wrapperCss}</style><style data-studio-site="">{serviceSiteCss}</style>{children}</>}
