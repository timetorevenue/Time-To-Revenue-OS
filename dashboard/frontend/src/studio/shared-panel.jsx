import React,{useState} from 'react'
import { shared,sharedFields,navigationFields,rows,validateSharedContent } from './shared'
import { sectionRules,pageIds } from './sections'
import { pageSeo } from './seo'
export function SharedPanel({data,operate,drafts}) {
 const [pending,setPending]=useState(null)
 function edit(item,field,text){return operate({edits:[{section:item.props.id,field,text}]},'Shared site updated on every page. Undo is available.')}
 function update(item,key,list){const content=data.content.map(i=>i===item?{...i,props:{...i.props,[key]:JSON.stringify(list)}}:i);try{validateSharedContent(content,drafts)}catch(e){return operate({edits:[{section:item.props.id,field:key,text:JSON.stringify(list)}]},e.message)}return edit(item,key,JSON.stringify(list))}
 const dependencies=[]
 for(const item of data.content)for(const key of navigationFields[item.type])for(const row of rows(item,key)) {
  const page=pageIds.find(id=>drafts[id]&&sectionRules.links.routes.includes(row.href)&&row.href===(id==='home'?'/':drafts[id]&&pageSeo(id,drafts[id]).route))
  if(page&&page!=='home') {const title=pageSeo(page,drafts[page]).title;if(title!==row.label)dependencies.push({item,key,row,title})}
 }
 return <div className="studio-shared-panel">
  <p>One shared draft supplies every page. Main navigation appears in the desktop header, mobile menu and footer Explore list. Global Design here supplies site-wide defaults; page and element overrides take precedence.</p>
  <datalist id="studio-site-routes">{sectionRules.links.routes.map(href=><option key={href} value={href}/>)}</datalist>
  {data.content.map(item=><section key={item.props.id}><h3>{item.type==='SharedHeader'?'Header':'Footer'}</h3>
   {Object.entries(sharedFields[item.type]).map(([field,label])=><label className="studio-control" key={`${item.props.id}-${field}-${item.props[field]}`}><span>{label}</span>{field==='logo'?<select aria-label={label} value={item.props[field]} onChange={e=>edit(item,field,e.target.value)}>{shared.logos.map(src=><option key={src}>{src}</option>)}</select>:<input aria-label={label} list={field==='destination'?'studio-site-routes':undefined} defaultValue={item.props[field]} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} onBlur={e=>{if(e.target.value!==item.props[field]&&!edit(item,field,e.target.value))e.target.value=item.props[field]}}/>}</label>)}
   {navigationFields[item.type].map(key=>{const list=rows(item,key);return <div className="studio-nav-group" key={key}><h4>{key==='navigation'?'Main navigation (header + footer)':key==='learning'?'Learning links':'Legal links'}</h4><ol aria-label={`${key} items`}>{list.map((row,index)=><li key={`${row.id}-${row.label}-${row.href}`} data-nav-id={row.id}>
    <label className="studio-control"><span>Label</span><input aria-label={`${key} ${row.id} label`} defaultValue={row.label} onBlur={e=>{if(e.target.value!==row.label&&!update(item,key,list.map(r=>r.id===row.id?{...r,label:e.target.value}:r)))e.target.value=row.label}}/></label>
    <label className="studio-control"><span>Destination</span><input aria-label={`${key} ${row.id} destination`} list="studio-site-routes" defaultValue={row.href} onBlur={e=>{if(e.target.value!==row.href&&!update(item,key,list.map(r=>r.id===row.id?{...r,href:e.target.value}:r)))e.target.value=row.href}}/></label>
    <div className="studio-actions"><button aria-label={`Move ${key} ${row.id} up`} disabled={!index} onClick={()=>{const next=[...list];[next[index-1],next[index]]=[next[index],next[index-1]];update(item,key,next)}}>↑</button><button aria-label={`Move ${key} ${row.id} down`} disabled={index===list.length-1} onClick={()=>{const next=[...list];[next[index+1],next[index]]=[next[index],next[index+1]];update(item,key,next)}}>↓</button><button onClick={()=>update(item,key,list.filter(r=>r.id!==row.id))} aria-label={`Remove ${key} ${row.id}`}>Remove</button></div>
   </li>)}</ol><button disabled={list.length>=12} onClick={()=>{const href=sectionRules.links.routes.find(h=>!list.some(r=>r.href===h));let id;do{id='nav-'+crypto.randomUUID().slice(0,8)}while(list.some(r=>r.id===id));update(item,key,[...list,{id,label:'New link',href}])}}>Add {key} item</button></div>})}
  </section>)}
  {!!dependencies.length&&<section><h3>Page-label dependencies</h3><p>Destination-matching labels may be intentional. Review each proposed change before accepting. Routes, prose and source-owned /services and OfferCatalog names remain unchanged.</p><button onClick={()=>setPending({items:dependencies,base:JSON.stringify(data)})}>Preview label updates</button></section>}
  {pending&&<section className="studio-proposal"><h3>Review shared label updates</h3><ul>{pending.items.map(d=><li key={`${d.item.type}-${d.key}-${d.row.id}`}>{d.row.href}: <del>{d.row.label}</del><ins>{d.title}</ins></li>)}</ul><button disabled={pending.base!==JSON.stringify(data)} onClick={()=>{const edits=[];for(const item of data.content)for(const key of navigationFields[item.type]){const list=rows(item,key),next=list.map(r=>{const d=pending.items.find(d=>d.item.props.id===item.props.id&&d.key===key&&d.row.id===r.id);if(d&&d.row.label!==r.label)throw Error('Labels changed; preview again.');return d?{...r,label:d.title}:r});if(JSON.stringify(list)!==JSON.stringify(next))edits.push({section:item.props.id,field:key,text:JSON.stringify(next)})}operate({edits},'Accepted shared label updates. Undo is available.');setPending(null)}}>Accept label updates</button><button onClick={()=>setPending(null)}>Reject label updates</button></section>}
 </div>
}
