// The read-only source SSR snapshot keeps the real markup/SVG/classes; fields are escaped React text.
import React,{createContext,useContext,useState} from 'react'
import parse,{domToReact,attributesToProps} from 'html-react-parser'
import templates from './shared-templates.json'
import { rows, sharedCss, effectiveDesign } from './shared'
import { designCss,scopeCss } from './design'
import { pageInfo, pageByRoute } from './sections'
import { SelectionContext } from './fields'
export const SiteContext=createContext(null)
const cls=(n,c)=>n?.attribs?.class?.split(' ').includes(c)
const inside=(n,c)=>{for(let p=n;p;p=p.parent)if(cls(p,c))return true;return false}
export function SharedCanvas({kind,data,page='home',editable=false}) {
 const {select}=useContext(SelectionContext)
 const context=useContext(SiteContext)
 const [open,setOpen]=useState(false)
 const item=data.content.find(i=>i.type===kind),p=item.props,header=kind==='SharedHeader'
 const route=pageInfo(page)?.route||'/'
 const nav=(list,cta=false)=><ul className={header&&cta?'editorial-shell':undefined}>{list.map(row=><li key={row.id}><a href={row.href} aria-current={route===row.href?'page':route.startsWith(row.href+'/')?'true':undefined} onClick={e=>{e.preventDefault();setOpen(false);editable?select(item.props.id,'navigation'):context?.navigate?.(pageByRoute(row.href.split('#')[0]))}}>{row.label}</a></li>)}{cta&&<li><a href={p.destination} onClick={e=>{e.preventDefault();setOpen(false);if(editable)select(item.props.id,'button')}}>{p.cta}</a></li>}</ul>
 const options={replace:node=>{
  if(!node.name)return
  const attrs=attributesToProps(node.attribs||{})
  let target=null,children
  if(node.name===(header?'header':'footer'))target='section'
  if(node.name==='img'&&inside(node,'brand-lockup')){attrs.src=p.logo;delete attrs.srcSet;delete attrs.loading;target='logo'}
  if(node.name==='a'&&cls(node,'brand-lockup'))attrs['aria-label']=`${p.name} home`
  if(node.name==='span'&&inside(node,'brand-lockup')){children=p.name;target='wordmark'}
  if(node.name==='nav') {
   const label=node.attribs['aria-label']
   if(header){children=nav(rows(item,'navigation'),cls(node,'mobile-nav'));if(cls(node,'mobile-nav'))attrs.hidden=!open}
   else if(label==='Explore Example Company')children=<><p className="footer-label">{p.explore}</p>{nav(rows(data.content.find(i=>i.type==='SharedHeader'),'navigation'))}</>
   else if(label==='Learning and pricing')children=<><p className="footer-label">{p.learn}</p>{nav(rows(item,'learning'))}</>
   else if(label==='Legal')children=rows(item,'legal').map(row=><a key={row.id} href={row.href} onClick={e=>{e.preventDefault();if(editable)select(item.props.id,'navigation')}}>{row.label}</a>)
   target='navigation'
  }
  if(node.name==='p'&&inside(node,'footer-brand')){children=p.tagline;target='copy'}
  if(node.name==='p'&&inside(node,'footer-base')){children=`© ${new Date().getFullYear()} ${p.name}`;target='copy'}
  if(node.name==='a'&&(cls(node,'header-cta')||cls(node,'editorial-link')&&inside(node,'footer-contact'))){attrs.href=p.destination;children=<>{p.cta} {domToReact(node.children.filter(n=>n.name==='svg'),options)}</>;target='button'}
  if(node.name==='a'&&node.attribs.href?.startsWith('mailto:')){attrs.href=`mailto:${p.email}`;children=p.email;target='copy'}
  if(node.name==='button'&&cls(node,'mobile-toggle')) {
   attrs['aria-expanded']=open;attrs.onClick=()=>setOpen(v=>!v)
   children=<>{open?'Close':'Menu'}<span className={open?'menu-mark is-open':'menu-mark'} aria-hidden="true"/></>
  }
  if(target){
   attrs['data-studio-element']=target
   if(target==='section'){attrs['data-studio-section']=item.props.id;attrs['data-studio-type']=item.type}
   if(editable)attrs.onClick=e=>{if(node.name==='button')return;e.stopPropagation();e.preventDefault();select(item.props.id,target)}
  }
  if(node.name==='a'&&!attrs.onClick)attrs.onClick=e=>{e.preventDefault();if(!editable)context?.navigate?.(pageByRoute(attrs.href?.split('#')[0]))}
  if(node.name==='header')attrs.onKeyDown=e=>{if(e.key==='Escape'){setOpen(false);e.currentTarget.querySelector('.mobile-toggle')?.focus()}}
  return React.createElement(node.name,attrs,children??(node.children?.length?domToReact(node.children,options):undefined))
 }}
 return parse(header?templates.header:templates.footer,options)
}
export function SiteTheme({data,page}) {
 const site=useContext(SiteContext)?.data
 if(!site)return <style data-studio-theme="">{designCss(data.root.props.design)}</style>
 return <><style data-studio-global="">{designCss(site.root.props.design)}</style><style data-studio-theme="">{page==='site'?'':scopeCss(designCss(effectiveDesign(data,site)),page)}</style><style data-studio-shared="">{sharedCss(site)}</style></>
}
