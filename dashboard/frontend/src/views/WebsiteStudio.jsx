// TTROS Website Studio inside Websites & Digital. Manual editing, styling, saving, preview,
// validation and source checks are deterministic and make zero model calls. David is reached
// only by the explicit "Propose changes" button; his result is applied through the same
// validated reducer as manual edits, after preview and explicit acceptance.
// Stage 2: sections are instances of the real website components with stable IDs. Add, duplicate,
// move and remove are the same validated operations as David's proposals (applyProposal), so
// they record Puck history (undo/redo), save, version and export like every other edit.
// Stage 2.1: deterministic WCAG AA text-contrast checks (contrast.js) warn while editing, block David
// proposals that introduce failures, and require an explicit acknowledgement before a draft with
// known failures is exported for the site adapter. The registered Sections palette is the only
// section-insertion interface: Puck's Blocks rail entry is replaced by a shortcut to it.
// Stage 3: real multi-page editing. A page picker opens the homepage and the three service pages
// (pages.json). Each page has a stable ID, its own browser draft, server version sequence, undo
// history, pending David proposal and acknowledgement; switching keeps unsaved work in memory and
// never carries styles or changes across pages. SEO and Links tabs show the page's deterministic
// metadata/structured data and its links to the other Studio pages (open them in the Studio).
// Revisit with the Studio draft/proposal schema or sources API. Last touched: 2026-11-18.
import { createContext, useContext, useMemo, useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createPortal } from 'react-dom'
import { Puck, Render, createUsePuck, AutoField } from '@puckeditor/core'
import '@puckeditor/core/puck.css'
import { config, Frame, PageContext } from '../studio/config'
import { SelectionContext, LocalStyleField, ContrastContext } from '../studio/fields'
import { snapshot, loadDraft, saveDraft, applyProposal, fingerprint, validateData, validateEnvelope, exportDraft, DRAFT_VERSION, initialDataFor, draftKey, migrateEnvelope } from '../studio/state'
import { typeOf, linkKeys, allocateId, newInstance, canAdd, canRemove, instanceLabel, anchorMap, isOriginal, pageTypes, pageInfo, pageIds, pageByRoute, studioPages } from '../studio/sections'
import { seoProblems, siteLinkReport, pageLinks } from '../studio/seo'
import {findItem,walkItems,isNested,nestedRules,editBefore,styleBefore} from '../studio/nested'
import { SharedPanel } from '../studio/shared-panel'
import { SiteContext } from '../studio/shared-canvas'
import { effectiveData,effectiveDesign,validateSharedContent,siteNewFailures } from '../studio/shared'
import { linkTextFields,pageSeo } from '../studio/seo'
import { studioApi } from '../studio/api'
import { ReleasesPanel } from '../studio/releases-panel'
import { designViolations, proposalViolations } from '../studio/brand'
import { contrastChecks, contrastAlternatives, failureKey, formatRatio, shownRatio, originText, targetLabel, targetChecks, AA } from '../studio/contrast'
import { LayoutList } from 'lucide-react'
import WebsiteSources, { StateBadge } from './WebsiteSources'
import './WebsiteStudio.css'
// Live website editor (the real Next.js source of the live site). The section editor below stays
// for the preserved 215679e candidate drafts.
const LiveEditor = lazy(() => import('../studio/live/LiveEditor'))

const usePuck = createUsePuck()
const StudioContext = createContext(null)
const editorUi = {leftSideBarVisible:false,rightSideBarVisible:false}
const permissions = {insert:false,delete:false,duplicate:false}
const viewports = [{width:1200,height:'auto',label:'Desktop',icon:'Monitor'},{width:375,height:'auto',label:'Mobile',icon:'Smartphone'}]
const overrides = {iframe:Frame,headerActions:Actions}
const tabs = [['shared','Shared elements'],['design','Global Design'],['sections','Sections'],['selected','Selected styles'],['brand','Brand'],['access','Accessibility'],['seo','SEO'],['links','Links'],['david','David']]
const ROOT_ZONE = 'root:default-zone'

// Static page markup (palette thumbnails, proposal previews) for one Studio page.
const staticPage=(page,data,site)=>renderToStaticMarkup(<SiteContext.Provider value={{data:page==='site'?data:(site||initialDataFor('site'))}}><PageContext.Provider value={page}><Frame><Render config={config} data={data}/></Frame></PageContext.Provider></SiteContext.Provider>)
// Palette thumbnails: each registered type of this page rendered with the current global design, static.
function thumbnail(type,design,page) {
 const data={root:{props:{design}},content:[newInstance(type,`palette-${type}`)]}
 return '<!doctype html><html><head><meta name="viewport" content="width=1200"></head><body style="margin:0">'+staticPage(page,data)+'</body></html>'
}

function SectionsPanel({data,page,operate,selectedId,focus,history,setStatus}) {
 const design=data.root.props.design
 const designKey=JSON.stringify(design)
 const sectionTypes=pageTypes(page)
 const thumbs=useMemo(()=>Object.fromEntries(sectionTypes.map(s=>[s.type,thumbnail(s.type,design,page)])),[designKey,page])
 const selected=data.content.find(i=>i.props.id===selectedId)
 const ids=data.content.map(i=>i.props.id)
 const removeBlock=selected?canRemove(data.content,selected.props.id):'Select a section first.'
 const duplicateBlock=selected?canAdd(data.content,selected.type):'Select a section first.'
 function add(type,place) {
  const id=allocateId(type,ids)
  const op=place==='end'?{op:'add',type,id,place}:{op:'add',type,id,place,ref:selected.props.id}
  const next=operate({edits:[],sections:[op]},`Added ${typeOf(type).label} (${id}). Undo is available.`)
  if(next)focus(next,id,true)
 }
 function duplicate() {
  const id=allocateId(selected.type,ids)
  const next=operate({edits:[],sections:[{op:'duplicate',source:selected.props.id,id}]},`Duplicated ${instanceLabel(data.content,selected.props.id)} as ${id}. Content and local styles were copied; edit either independently.`)
  if(next)focus(next,id,true)
 }
 function remove() {
  const label=instanceLabel(data.content,selected.props.id)
  focus(data,null) // clear Puck's selection first so it cannot slide onto the next section
  const next=operate({edits:[],sections:[{op:'remove',id:selected.props.id}]},`Removed ${label} from the draft. Undo restores it.`)
 }
 return <div className="studio-section-tools">
  <p className="studio-help">{selected?<>Selected: <strong>{instanceLabel(data.content,selected.props.id)}</strong> · ID <code data-testid="selected-section-id">{selected.props.id}</code>{isOriginal(selected)?' · original website section':''}</>:'Select a section on the page or in the list below.'}</p>
  <div className="studio-actions">
   <button onClick={duplicate} disabled={!!duplicateBlock}>Duplicate section</button>
   <button onClick={remove} disabled={!!removeBlock}>Remove section</button>
   <button onClick={()=>{history.back();setStatus('Undone.')}} disabled={!history.hasPast}>Undo</button>
   <button onClick={()=>{history.forward();setStatus('Redone.')}} disabled={!history.hasFuture}>Redo</button>
  </div>
  {selected && (duplicateBlock || removeBlock) && <p className="studio-help" role="note">{duplicateBlock?`Duplicate unavailable: ${duplicateBlock} `:''}{removeBlock?`Remove unavailable: ${removeBlock}`:''}</p>}
  <h3>Add a section</h3>
  <p className="studio-help">Real website components from the protected site. A new section starts with the site's original content and inherits the global design; override its styles independently under Selected styles.</p>
  <ul className="studio-palette" aria-label="Section palette">{sectionTypes.map(s=>{
   const block=canAdd(data.content,s.type)
   return <li key={s.type} data-section-type={s.type}>
    <div className="studio-thumb" aria-hidden="true"><iframe tabIndex={-1} title={`${s.label} preview`} sandbox="" loading="lazy" srcDoc={thumbs[s.type]}/></div>
    <strong>{s.label}</strong><small>{s.description}</small>
    {block?<small className="studio-limit" role="note">{block}</small>:<div className="studio-actions">
     <button aria-label={`Add ${s.label} above selected section`} disabled={!selected} onClick={()=>add(s.type,'before')}>Above</button>
     <button aria-label={`Add ${s.label} below selected section`} disabled={!selected} onClick={()=>add(s.type,'after')}>Below</button>
     <button aria-label={`Add ${s.label} at end of page`} onClick={()=>add(s.type,'end')}>At end</button>
    </div>}
   </li>})}</ul>
 </div>
}

// Puck's Blocks rail lists components it cannot insert (insert is disabled so every change goes
// through validated operations). The entry is replaced, by its plugin name, with a shortcut that
// opens the registered Sections palette, the only section-insertion interface.
function SectionsRail() {
 const {setTab}=useContext(StudioContext)
 const ui=usePuck(s=>s.appState.ui)
 const dispatch=usePuck(s=>s.dispatch)
 const active=ui.leftSideBarVisible && ui.plugin?.current==='blocks'
 useEffect(()=>{if(active){setTab('sections');dispatch({type:'setUi',ui:{leftSideBarVisible:false}})}},[active])
 return <div className="studio-rail-note"><p>Sections are added, duplicated and removed from the Sections palette in the Studio panel.</p><button onClick={()=>setTab('sections')}>Open Sections palette</button></div>
}
const plugins = [{name:'blocks',label:'Sections',icon:<LayoutList size={20}/>,render:SectionsRail}]

// One contrast result with its calculated ratio, origin and verified accessible alternatives.
function ContrastItem({data,check,operate,brandRules,onSelect}) {
 const alternatives=useMemo(()=>check.pass?[]:contrastAlternatives(data,check,brandRules),[data,check,brandRules])
 const label=`${instanceLabel(data.content,check.section)} · ${targetLabel(check.type,check.target)}`
 return <li className="studio-contrast-item" data-check={check.id} data-pass={check.pass}>
  <div className="studio-contrast-head"><span className="studio-contrast-sample" style={{color:check.foreground.hex,background:check.background.hex}} aria-hidden="true">Aa</span>
   <div><strong>{label}</strong><span className="studio-ratio" data-testid="contrast-ratio">{formatRatio(check.ratio)}</span> <span className={check.pass?'studio-pass':'studio-fail'}>{check.pass?`passes AA (${check.required}:1)`:`fails AA: needs ${check.required}:1`}</span>
    <small>“{check.text}” · {check.foreground.hex} on {check.background.hex} · {check.large?'large':'normal'} text ({check.size}px, weight {check.weight})</small>
    <small>{originText(check)}</small></div></div>
  {!check.pass && <div className="studio-alternatives"><span>Accessible alternatives</span>
   {alternatives.length?alternatives.map(a=><button key={a.label} onClick={()=>operate(a.operation,`Applied: ${a.label}. Contrast now ${formatRatio(a.ratio)}. Undo is available.`)}>{a.colour && <i style={{background:a.colour}} aria-hidden="true"/>}{a.label} · {formatRatio(a.ratio)}{a.onBrand===false?` · owner override: ${a.brandRule}`:''}</button>):<small>No single style change fixes this without creating another failure; adjust the colours manually.</small>}
  </div>}
  {onSelect && <button className="studio-link-button" onClick={()=>onSelect(check)}>Select on page</button>}
 </li>
}

function AccessibilityPanel({data,checks,operate,brandRules,focus,ack,setAck}) {
 const failures=checks.filter(c=>!c.pass),key=failureKey(failures)
 return <div className="studio-access-panel">
  <p>WCAG 2.2 AA text contrast: {AA.normal}:1 for normal text, {AA.large}:1 for large text (24px+, or 18.66px+ at weight 700+), at the smallest size the page renders. Checked deterministically for every text item, including colours inherited from section overrides and the global design.</p>
  <p className="studio-help" role="status" data-testid="contrast-summary">{checks.length} text items checked · {failures.length?`${failures.length} below AA`:'all pass AA'}</p>
  {!!failures.length && <>
   <ul className="studio-contrast-list">{failures.map(c=><ContrastItem key={c.id} data={data} check={c} operate={operate} brandRules={brandRules} onSelect={c=>focus(data,c.section,false,c.target)}/>)}</ul>
   <label className="studio-ack"><input type="checkbox" checked={ack===key} onChange={e=>setAck(e.target.checked?key:null)}/> I have reviewed these {failures.length} known contrast failure{failures.length===1?'':'s'}. Export may include them; they are recorded in the exported draft.</label>
   <p className="studio-help">Editing and saving drafts stay possible; every saved server version records its known failures. David cannot introduce new failures, and export for the site adapter needs this acknowledgement.</p>
  </>}
  <p className="studio-help">Not modelled: hover and focus states, text over images, and non-text contrast (icons, borders).</p>
 </div>
}

function TargetContrast({data,instance,target,operate,brandRules}) {
 const checks=targetChecks(data,instance,target)
 if(!checks.length)return null
 const failing=checks.filter(c=>!c.pass),worst=checks.reduce((a,b)=>a.ratio<=b.ratio?a:b)
 return <div className="studio-target-contrast" data-testid="target-contrast">
  <h4>Contrast</h4>
  <p className={failing.length?'studio-fail':'studio-pass'} role={failing.length?'alert':undefined}>{failing.length?`${failing.length} of ${checks.length} text item${checks.length===1?'':'s'} below WCAG AA`:`${checks.length} text item${checks.length===1?'':'s'} pass WCAG AA`} · lowest {formatRatio(worst.ratio)} (needs {worst.required}:1)</p>
  {!!failing.length && <ul className="studio-contrast-list">{failing.map(c=><ContrastItem key={c.id} data={data} check={c} operate={operate} brandRules={brandRules}/>)}</ul>}
 </div>
}

// Puck memoizes a custom field's render function by field.key, so the Selected styles field is
// keyed (remounted) per instance and design: otherwise selecting another section kept the first
// section's element list and instance (fixed in Stage 2.1).
// Link destinations of the selected instance. Applied on Enter/blur; unsafe links are refused.
// A link to another Studio page can be opened in the Studio (Stage 3).
function LinkFields({item,operate,openPage}) {
 const section=typeOf(item.type),keys=linkKeys(section)
 if(!keys.length)return null
 return <div className="studio-links"><h4>Links</h4>{keys.map((key,index)=>{
  const target=pageByRoute((item.props[key].split('#')[0].replace(/\/+$/,''))||'/')
  return <div key={`${item.props.id}-${key}-${item.props[key]}`}><label className="studio-control"><span>Link {index+1}</span>
  <input aria-label={`Link ${index+1} destination`} defaultValue={item.props[key]} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} onBlur={e=>{
   const value=e.currentTarget.value.trim()
   if(value===item.props[key])return
   if(!operate({edits:[{section:item.props.id,field:key,text:value}]},`Link ${index+1} now points to ${value}.`))e.currentTarget.value=item.props[key]
  }}/></label>{target && target!==section.page && <button className="studio-link-button" onClick={()=>openPage(target)}>Open {pageInfo(target).label} in Studio</button>}</div>})}
  <p className="studio-help">Site pages (/about), anchors on this page{Object.values(anchorMap(item)).length?` (this section: ${Object.values(anchorMap(item)).filter(a=>!a.startsWith('workflow-')).map(a=>'#'+a).join(', ')})`:''}, https:// or mailto: only.</p>
 </div>
}

function BrandPanel({data,change,openSources}) {
 const {brand}=useContext(StudioContext)
 const approved=brand?.approved
 const overridesList=useMemo(()=>designViolations(data,approved?.rules),[data,approved])
 if(!approved) return <div className="studio-brand-panel">
  <p>{brand?.proposed?'A brand configuration was extracted from the brand kit and is waiting for your approval. Until then nothing is enforced.':'The brand kit has not been read yet.'}</p>
  <button onClick={openSources}>Review in Sources & Branding</button>
 </div>
 return <div className="studio-brand-panel">
  <p>Approved {new Date(approved.approved_at).toLocaleDateString('en-CA')} from <code>{approved.source.name}</code>. David's proposals must follow it. Your own edits may override it; every override stays listed here.</p>
  <div className="studio-brand-swatches">{approved.rules.colours.map(c=><i key={c} title={c} style={{background:c}}/>)}</div>
  <p className="studio-help">Fonts {approved.rules.fonts.join(', ')} · {Object.entries(approved.rules.ranges).map(([k,[a,b]])=>`${k} ${a}–${b}px`).join(' · ')}</p>
  <button className="studio-primary" onClick={()=>change(applyProposal(data,{edits:[],design:approved.tokens}),'Approved brand applied to the draft. Undo is available.')}>Apply approved brand to draft</button>
  <h3>Owner overrides ({overridesList.length})</h3>
  {overridesList.length?<ul className="studio-overrides">{overridesList.map((v,i)=><li key={i}><strong>{v.token}</strong> {String(v.value)}<small>{v.rule}</small></li>)}</ul>:<p>The draft follows the approved brand.</p>}
 </div>
}

// The page's metadata and structured data exactly as the site adapter renders them (seo.js).
function SeoPanel({page,report,siteWarnings}) {
 const {seo,errors,warnings}=report
 const others=siteWarnings.filter(w=>w.page===page)
 return <div className="studio-seo-panel" data-testid="seo-panel">
  <p className="studio-help" role="status" data-testid="seo-summary">{errors.length?`${errors.length} SEO error${errors.length===1?'':'s'}: export blocked until fixed`:'Metadata and structured data are valid'}{warnings.length?` · ${warnings.length} warning${warnings.length===1?'':'s'}`:''}</p>
  {!!errors.length && <ul className="studio-seo-errors" role="alert">{errors.map(e=><li key={e}>{e}</li>)}</ul>}
  {!!(warnings.length||others.length) && <ul className="studio-seo-warnings">{[...warnings,...others.map(w=>w.message)].map(w=><li key={w}>{w}</li>)}</ul>}
  <dl className="studio-seo">
   <dt>URL</dt><dd><code data-testid="seo-route">{seo.route}</code> (fixed)</dd>
   <dt>Title</dt><dd data-testid="seo-title">{seo.documentTitle}</dd>
   <dt>Meta description</dt><dd data-testid="seo-description">{seo.description}</dd>
   <dt>Canonical</dt><dd><code data-testid="seo-canonical">{seo.canonical}</code></dd>
   <dt>Open Graph / Twitter</dt><dd>{seo.openGraph.title} · {seo.openGraph.url}</dd>
  </dl>
  <p className="studio-help">{page==='home'?'Homepage title, description and canonical URL are fixed in the website source; the FAQ structured data follows the visible FAQ.':'The Intro label is the page title and the Intro lead is the meta description. Open Graph, Twitter and the WebPage, Breadcrumb and Service structured data follow them, here and in the site adapter’s build; the URL and canonical address never change.'}</p>
  <details><summary>Structured data (JSON-LD)</summary><pre data-testid="seo-jsonld">{JSON.stringify(seo.jsonLd,null,1)}</pre></details>
 </div>
}

// Links on this page and the other Studio pages that link here; Studio pages open in the Studio.
function LinksPanel({page,data,report,openPage,focus,operate,drafts}) {
 const [review,setReview]=useState(null)
 const updates=pageLinks(page,data).flatMap(l=>{
  if(!l.page||l.page==='home'||!drafts[l.page])return []
  const title=pageSeo(l.page,drafts[l.page]).name,field=linkTextFields(l.type)[l.index]
  return field&&l.text!==title?[{section:l.section,field,text:title,before:data.content.find(i=>i.props.id===l.section).props[field]}]:[]
 })
 const links=pageLinks(page,data),incoming=report.pages[page]?.incoming||[]
 const kinds={studio:'Studio page',site:'Website page',anchor:'This page',external:'External',mailto:'Email'}
 return <div className="studio-links-panel" data-testid="links-panel">
  <p className="studio-help">Every link is validated: website routes from the protected site, anchors that exist on their page, https:// or mailto:. Links never change a page's URL.</p>
  {!!updates.length&&<section><h3>Page-label dependencies</h3><p>Review optional updates to this page’s destination-matching link labels. URLs and other copy stay fixed.</p><button onClick={()=>setReview({base:fingerprint(data),edits:updates})}>Preview page label updates</button></section>}
  {review&&<section className="studio-proposal"><ul>{review.edits.map((e,i)=><li key={i}><del>{e.before}</del><ins>{e.text}</ins></li>)}</ul><button disabled={review.base!==fingerprint(data)} onClick={()=>{operate({edits:review.edits.map(({before,...e})=>e)},'Accepted page label updates. Undo is available.');setReview(null)}}>Accept page label updates</button><button onClick={()=>setReview(null)}>Reject page label updates</button></section>}
  <h3>Links on this page ({links.length})</h3>
  <ul className="studio-link-list">{links.map(l=><li key={`${l.section}:${l.field}`} data-kind={l.kind} data-href={l.href}>
   <span>{l.text || l.href}</span><small>{kinds[l.kind]} · <code>{l.href}</code> · {instanceLabel(data.content,l.section)}</small>
   <span className="studio-actions"><button className="studio-link-button" onClick={()=>focus(data,l.section,false,`a-${l.index}`)}>Select</button>{l.kind==='studio' && <button className="studio-link-button" onClick={()=>openPage(l.page)}>Open {pageInfo(l.page).label} in Studio</button>}</span>
  </li>)}</ul>
  <h3>Linked from other Studio pages ({incoming.length})</h3>
  {incoming.length?<ul className="studio-link-list">{incoming.map(l=><li key={`${l.from}:${l.section}:${l.field}`} data-from={l.from}><span>{pageInfo(l.from).label}: “{l.text}”</span><small><code>{l.href}</code></small><button className="studio-link-button" onClick={()=>openPage(l.from)}>Open {pageInfo(l.from).label}</button></li>)}</ul>:<p className="studio-help">{page==='home'?'Other pages reach the homepage through the site header logo (not editable here).':'No Studio page links here.'}</p>}
  {!!report.warnings.length && <><h3>Site link checks</h3><ul className="studio-seo-warnings">{report.warnings.map(w=><li key={w.message}>{pageInfo(w.page).label}: {w.message}</li>)}</ul></>}
 </div>
}

function Actions() {
 const {sharedData,sharedSession,panel,tab,setTab,site,brand,server,setServer,openSources,setContrastMarks,page,openPage,session,remember,markSaved,otherDrafts,setLastBuild,openReleases}=useContext(StudioContext)
 const {selection,select}=useContext(SelectionContext)
 const data=usePuck(s=>s.appState.data)
 const selectedItem=usePuck(s=>s.selectedItem)
 const dispatch=usePuck(s=>s.dispatch)
 const history=usePuck(s=>s.history)
 const getSelectorForId=usePuck(s=>s.getSelectorForId)
 const getParentById=usePuck(s=>s.getParentById)
 const listRef=useRef(null)
 // The section list opens with the Sections tab; it never closes itself (the owner may keep it open).
 useEffect(()=>{if(tab==='sections'&&listRef.current)listRef.current.open=true},[tab])
 // Per-page session (Stage 3): restored when the owner returns to this page, never shared with another.
 const [request,setRequest]=useState(session.request ?? '')
 const [status,setStatus]=useState(session.status ?? session.startupError ?? '')
 const [busy,setBusy]=useState(false)
 const [proposal,setProposal]=useState(session.proposal ?? null)
 const [accepted,setAccepted]=useState(session.accepted ?? null)
 const [mobile,setMobile]=useState(false)
 const [ack,setAck]=useState(session.ack ?? null)
 const [historyVersion,setHistoryVersion]=useState(0)
 const [built,setBuilt]=useState(null)
 const [buildAck,setBuildAck]=useState(false)
 const [buildMobile,setBuildMobile]=useState(false)
 useEffect(()=>{if(built)setLastBuild?.(built)},[built,setLastBuild])
 useEffect(()=>{Object.assign(session,{request,status,proposal,accepted,ack})},[session,request,status,proposal,accepted,ack])
 // Unsaved work (data and undo history) is kept in memory for this page when the owner switches page.
 useEffect(()=>{remember(page,data,{histories:history.histories,index:history.index})},[page,data,history.histories,history.index,remember])
 useEffect(()=>{
  if(!server.available||!server.data||!server.latest||session.serverLoaded)return
  session.serverLoaded=true
  const stored=localStorage.getItem(draftKey(page))
  if(!stored && fingerprint(data)===fingerprint(initialDataFor(page))) {
   dispatch({type:'setData',data:server.data,recordHistory:true})
   markSaved(page,server.data)
   setStatus(`Loaded server version v${server.latest}.`)
  }
 },[server.available,server.latest,server.data,page])
 const resolved=page==='site'?data:effectiveData(data,sharedData)
 const checks=useMemo(()=>contrastChecks(resolved),[data,sharedData])
 const seoReport=useMemo(()=>{try{return page==='site'?{seo:{route:'All pages',documentTitle:'Shared site',description:'',canonical:''},errors:[],warnings:[]}:seoProblems(page,data)}catch(e){return {seo:null,errors:[e.message],warnings:[]}}},[page,data])
 const linkReport=useMemo(()=>siteLinkReport({...otherDrafts(page),[page]:data}),[page,data,otherDrafts])
 const failures=checks.filter(c=>!c.pass)
 const brandRules=brand?.approved?.rules || null // approved brand rules rank and label alternatives
 const marks=[...new Set(failures.map(f=>`${f.section}:${f.target}`))].sort().join('|')
 useEffect(()=>{setContrastMarks(marks)},[marks,setContrastMarks])
 useEffect(()=>{if(selectedItem?.props.id)select(isNested(selectedItem.type)?null:selectedItem.props.id,null)},[selectedItem?.props.id,select])
 const selected=findItem(data,isNested(selectedItem?.type)?selectedItem.props.id:selection?.section)
 const section=selected && typeOf(selected.type)
 const preview=proposal && applyProposal(proposal.base,proposal.value,{allowRemove:false})
 const stale=proposal && fingerprint(data)!==fingerprint(proposal.base)
 const offBrand=proposal?proposalViolations(proposal.value,brand?.approved?.rules,proposal.base):[]
 // A proposal may not introduce contrast failures; ones the page already had are listed, not hidden.
 const introduced=useMemo(()=>preview?siteNewFailures(page,proposal.base,preview,otherDrafts(page),sharedData):[],[proposal,sharedData])
 const inherited=useMemo(()=>preview?contrastChecks(preview).filter(c=>!c.pass && !introduced.some(f=>f.id===c.id)):[],[proposal,introduced])
 // One undoable history step. Puck's selection follows the selected INSTANCE, not its old index,
 // so inserting, moving or accepting sections never silently selects a different section.
 function selectorAfter(next,id) {
  // Puck's lookup describes the current tree. Root order can change in this transaction.
  const index=next.content.findIndex(i=>i.props.id===id)
  return index>=0?{index,zone:ROOT_ZONE}:findItem(next,id)?getSelectorForId(id):null
 }
 function commit(next) {
  dispatch({type:'setData',data:next,recordHistory:true})
  const activeId=selectedItem?.props.id||selection?.section
  const selector=activeId?selectorAfter(next,activeId):null
  dispatch({type:'setUi',ui:{itemSelector:selector||null}})
 }
 function change(next,message) {
  try {commit(validateData(next));if(message)setStatus(message)}
  catch(e){setStatus(`Change rejected: ${e.message}`)}
 }
 // Every manual section/link operation: the shared validated reducer, one undoable history step.
 function operate(operations,message) {
  try {
   const next=applyProposal(data,operations)
   if(page==='site')validateSharedContent(next.content,otherDrafts(page))
   commit(next);setStatus(message);return next
  } catch(e){setStatus(`Change rejected: ${e.message}`);return null}
 }
 function focus(next,id,openSections=false,target='section') {
  const selector=id?selectorAfter(next,id):null
  dispatch({type:'setUi',ui:{itemSelector:selector||null}})
  if(id&&!isNested(findItem(next,id)?.type))select(id,target)
  else if(id)setTab('selected')
  if(openSections)setTab('sections')
  setTimeout(()=>{try{document.querySelector('.studio-editor iframe')?.contentDocument?.querySelector(`[data-studio-section="${CSS.escape(id||'')}"]`)?.scrollIntoView({block:'center'})}catch{/* cross-document scroll is best effort */}},60)
 }
 // The only model request in Studio. Invoked solely by the explicit button below.
 async function propose() {
  setBusy(true);setProposal(null);setStatus('David is preparing a proposal…')
  const base=structuredClone(data)
  try {
   const result=await studioApi.requestProposal({project:site,page,base_version:server.available?server.latest:null,request,data:base,selection:isNested(selectedItem?.type)?{section:selectedItem.props.id,target:'section'}:selection})
   applyProposal(base,result.proposal,{allowRemove:false}) // David may add or duplicate, never remove
   setProposal({base,value:result.proposal,sources:result.context_sources||[],brandStatus:result.brand_status});setStatus(`Proposal ready · ${result.usage?.total_tokens ?? result.usage?.cumulative_tokens ?? 'unavailable'} tokens`)
  }catch(e){setStatus(e.message)}finally{setBusy(false)}
 }
 function move(id,target) {
  const order=data.content.map(i=>i.props.id);order.splice(order.indexOf(id),1);order.splice(target,0,id)
  change(applyProposal(data,{edits:[],order}))
 }
 function accept() {
  const next=preview
  change(next,'Proposal accepted. Undo is available.')
  setAccepted({base:fingerprint(proposal.base),result:fingerprint(next),operations:proposal.value})
  setProposal(null)
 }
 async function buildSaved(purpose='preview'){
  try {
   if(!server.available||!server.latest||fingerprint(data)!==fingerprint(server.data))throw Error('Save this page before building its exact server version.')
   const shared=await studioApi.drafts(site,'site')
   const home=await studioApi.drafts(site,'home')
   if(!shared.latest||!home.latest)throw Error('Save both the homepage and Shared site before building.')
   // Every saved service page is built from its latest server version (this page: the version on screen).
   const services=Object.keys(studioPages.pages).filter(p=>studioPages.pages[p].template==='service')
   const saved=Object.fromEntries((await Promise.all(services.filter(p=>p!==page).map(async p=>[p,(await studioApi.drafts(site,p)).latest]))).filter(([,v])=>v>0))
   if(services.includes(page))saved[page]=server.latest
   const result=await studioApi.build(site,{page,version:page==='home'?server.latest:home.latest,shared_version:page==='site'?server.latest:shared.latest,pages:saved,acknowledge_contrast:buildAck,purpose})
   setBuilt(result);setStatus(purpose==='release'?'Building a release candidate (the website\u2019s own configuration) from saved versions. Use Check build for progress.':'Building saved versions in an isolated Next.js copy. Use Check build for progress.')
  }catch(e){setStatus(`Preview not built: ${e.message}`)}
 }
 async function checkBuild(){try{const result=await studioApi.buildStatus(site,built.id);setBuilt(result);setStatus(result.error||(result.purpose==='release'&&result.validation?`Release build ${result.status} · validation ${result.validation}.`:`Real-site preview ${result.status}.`))}catch(e){setStatus(e.message)}}
 async function save(){
  try{saveDraft(localStorage,data,page);markSaved(page,data)}catch(e){setStatus(`Could not save: ${e.message}`);return}
  if(!server.available){setStatus(`Draft saved in this browser. Server versions unavailable: ${server.error || 'not connected'}`);return}
  const fromDavid=accepted && accepted.result===fingerprint(data) && !!server.data && accepted.base===fingerprint(server.data)
  try{
   const saved=await studioApi.saveDraft(site,{data,base_version:server.latest,actor:'owner',origin:fromDavid?'david':'manual',operations:fromDavid?accepted.operations:null,note:fromDavid?`Accepted David proposal: ${accepted.operations.summary||''}`.slice(0,500):'Manual Studio edits'},page)
   setServer({...server,latest:saved.version,data:structuredClone(data),last:saved,versions:[...(server.versions||[]),saved]})
   setStatus(`Draft saved in this browser and as server version v${saved.version} (${saved.origin}${saved.brand_overrides?.length?`, ${saved.brand_overrides.length} owner override(s)`:''}).${saved.contrast_failures?.length?` ${saved.contrast_failures.length} known contrast failure(s) recorded with this version.`:''}`)
  }catch(e){setStatus(`Draft saved in this browser. Server version not saved: ${e.message}`)}
 }
 const loadLatest=()=>loadVersion(server.latest)
 async function loadVersion(version){
  try{const record=await studioApi.draft(site,version,page);const {report}=migrateEnvelope(record.envelope);const next=validateEnvelope(record.envelope,page);change(next,`Loaded server version v${record.version}${report?` (schema v${report.from} migrated to v${report.to} on read: ${report.carried.length} edited value(s) carried, ${report.inserted.length} new source section(s), ${report.unsupported.length} retained)`:''}. Undo is available.`);if(version===server.latest)setServer({...server,data:next})}
  catch(e){setStatus(`Could not load server version: ${e.message}`)}
 }
 function download(){try{
  // Export feeds the real-site adapter: known contrast failures need an explicit acknowledgement.
  const sharedFailures=contrastChecks(sharedData).filter(c=>!c.pass)
  if(page!=='site' && sharedFailures.length && sharedSession.ack!==failureKey(sharedFailures)){setStatus('Export blocked: review and acknowledge shared contrast failures in Shared site → Accessibility.');return}
  if(failures.length && ack!==failureKey(failures)){setTab('access');setStatus(`Export blocked: ${failures.length} known WCAG AA contrast failure(s). Fix them, or review and acknowledge them under Accessibility.`);return}
  if(seoReport.errors.length){setTab('seo');setStatus(`Export blocked: ${seoReport.errors.length} SEO error(s). ${seoReport.errors[0]}`);return}
  const accessibility={standard:'WCAG 2.2 AA text contrast',checked:checks.length,failures:failures.map(f=>({id:f.id,target:f.target,foreground:f.foreground.hex,background:f.background.hex,ratio:shownRatio(f.ratio),required:f.required})),acknowledged:failures.length?{by:'owner',at:new Date().toISOString()}:null}
  const seo={route:seoReport.seo.route,title:seoReport.seo.documentTitle,description:seoReport.seo.description,canonical:seoReport.seo.canonical,warnings:seoReport.warnings}
  const blob=new Blob([JSON.stringify({...exportDraft(data,page),...(page==='site'?{}:{sharedSite:{...exportDraft(sharedData,'site'),accessibility:{failures:sharedFailures.map(f=>({id:f.id,ratio:shownRatio(f.ratio),required:f.required})),acknowledged:sharedFailures.length?{by:'owner',at:new Date().toISOString()}:null}}}),accessibility,seo},null,2)],{type:'application/json'})
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=page==='home'?'site-studio-draft.json':`site-studio-draft-${page}.json`;a.click();URL.revokeObjectURL(url)
  setStatus(`Validated site draft exported${failures.length?` with ${failures.length} acknowledged contrast failure(s) recorded`:''}. Use the local site adapter to build a disposable website fixture.`)
 }catch(e){setStatus(`Could not export: ${e.message}`)}}
 return <>
  <button onClick={save}>Save draft</button><button onClick={download}>Export site draft</button>
  {!!data.root?.props?.unsupported?.length&&<p className="studio-help" role="note" data-testid="studio-retained">Kept from the previous schema, not shown on the site: {data.root.props.unsupported.map(u=>u.path).join(', ')}. {data.root.props.unsupported[0].reason}</p>}
  <p className="studio-help" title="Builds the homepage, Shared site and every saved service page from saved server versions. Unsaved changes are excluded.">Preview: saved versions · {snapshot.commit.slice(0,7)} · schema v{DRAFT_VERSION}</p>
  <button onClick={()=>buildSaved('preview')} disabled={built?.status==='building'}>Build saved real-site preview</button>
  <button onClick={()=>buildSaved('release')} disabled={built?.status==='building'} title="Builds the same saved versions with the website's own configuration, validates and packages the exact build so it can become a release.">Build release candidate</button>
  <label><input type="checkbox" checked={buildAck} onChange={e=>setBuildAck(e.target.checked)}/> I reviewed known contrast failures in the saved homepage and shared site</label>
  {built&&<section aria-label="Real Next.js preview"><p>Candidate {built.base.slice(0,8)} · homepage v{built.versions.home.version} · shared v{built.versions.site.version}{Object.entries(built.versions).filter(([p])=>p!=='home'&&p!=='site').map(([p,v])=>` · ${pageInfo(p).label} v${v.version}`).join('')} · {built.status} · 0 AI calls</p><button onClick={checkBuild}>Check build</button>{built.status==='ready'&&<><p>Build <code>{built.nextBuildId}</code> · Preview expires after 30 minutes. {built.purpose==='release'?'Publication only through an immutable release below.':'Publication held.'}</p><button onClick={async()=>{try{setBuilt(await studioApi.stopBuild(site,built.id))}catch(e){setStatus(e.message)}}}>Close preview</button><button onClick={()=>setBuildMobile(false)}>Desktop preview</button><button onClick={()=>setBuildMobile(true)}>Mobile preview</button><a href={built.url} target="_blank" rel="noreferrer">Open isolated preview</a>{built.purpose==='release'?<p className="studio-help">Release builds keep the website's own frame policy, so open them in a new tab.</p>:<iframe title="Built Next.js website" src={built.url} sandbox="allow-scripts allow-same-origin" style={{width:buildMobile?375:1280,maxWidth:'100%',height:720,border:'1px solid #999'}}/>}<details><summary>Exact source diff</summary><pre style={{whiteSpace:'pre-wrap'}}>{built.sourceDiff}</pre></details></>}</section>}
  {built?.purpose==='release'&&built.validation&&<button onClick={openReleases}>Open Releases to create and publish</button>}
  {panel && createPortal(<>
   <p className="eyebrow">DESIGN WITH DAVID</p><h2>Website Studio</h2>
   <p className="studio-page-line" data-testid="studio-page-line" data-puck-selected-id={selectedItem?.props.id} data-puck-selected-type={selectedItem?.type} data-puck-selected-zone={getSelectorForId(selectedItem?.props.id)?.zone}>Editing <strong>{pageInfo(page).label}</strong> · <code>{pageInfo(page).route}</code>{seoReport.errors.length?<button className="studio-link-button" onClick={()=>setTab('seo')}> · ⚠ SEO</button>:null}</p>
   <button className="studio-contrast-chip" data-state={failures.length?'fail':'pass'} onClick={()=>setTab('access')}>{failures.length?`⚠ ${failures.length} text contrast failure${failures.length===1?'':'s'} (WCAG AA) · Review`:'✓ All text meets WCAG AA contrast'}</button>
   <nav className="studio-tabs" aria-label="Studio panels">{tabs.filter(([id])=>page==='site'?!['seo','links','sections'].includes(id):id!=='shared').map(([id,label])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{label}{id==='access' && !!failures.length && <span className="studio-badge" aria-hidden="true">{failures.length}</span>}</button>)}</nav>
   <p role="status" className="studio-status">{status}</p>
   {tab==='shared' && page==='site' && <SharedPanel data={data} operate={operate} drafts={otherDrafts(page)}/>}
   {tab==='design' && <><p className="studio-help">{page==='site'?'Global defaults for the whole site, including shared elements.':'Page styles override the global defaults only inside this page. Shared header and footer keep their own styles.'}</p>{page!=='site'&&<button onClick={()=>change({...data,root:{props:{design:sharedData.root.props.design,pageStyles:{}}}},'Page styles reset to global defaults. Local component overrides are retained.')}>Use global page styles</button>}<AutoField field={config.root.fields.design} value={effectiveDesign(data,page==='site'?null:sharedData)} onChange={design=>{const before=effectiveDesign(data,page==='site'?null:sharedData),patch=Object.fromEntries(Object.entries(design).filter(([k,v])=>v!==before[k]));operate({edits:[],design:patch},'Design updated. Undo is available.')}}/></>}
   {tab==='sections' && page!=='site' && <SectionsPanel data={data} page={page} operate={operate} selectedId={selection?.section} focus={focus} history={history} setStatus={setStatus}/>}
   {tab==='selected' && (selected&&isNested(selected.type)?<div className="studio-native-inspector" data-native-selected-id={selectedItem?.props.id} data-native-selected-type={selectedItem?.type} data-native-selected-zone={getSelectorForId(selectedItem?.props.id)?.zone}><h3>{section.label}</h3><p>{typeOf(getParentById(selected.props.id)?.type)?.label} / {section.label}</p><button onClick={()=>focus(data,getParentById(selected.props.id)?.props.id)}>Select parent {typeOf(getParentById(selected.props.id)?.type)?.label}</button><TargetContrast data={resolved} instance={selected.props.id} target="section" operate={operate} brandRules={brandRules}/><Puck.Fields/></div>:selected?<><h3>{instanceLabel(data.content,selected.props.id)}</h3><p className="studio-help">ID <code>{selected.props.id}</code></p>{selected.type==='Section0'&&<button onClick={()=>focus(data,selected.props.workflow[0].props.id)}>Select workflow graphic</button>}<LinkFields item={selected} operate={operate} openPage={openPage}/><TargetContrast data={data} instance={selected.props.id} target={selection.target||'section'} operate={operate} brandRules={brandRules}/><AutoField key={`${selected.props.id}|${JSON.stringify(data.root.props.design)}`} field={{...config.components[selected.type].fields.styles,render:props=><LocalStyleField {...props} section={section} instance={selected.props.id} design={data.root.props.design}/>}} value={selected.props.styles} onChange={styles=>change({...data,content:data.content.map(i=>i.props.id===selected.props.id?{...i,props:{...i.props,styles}}:i)})}/></>:<p>Select a section or element on the page, or use the section list below.</p>)}
   {tab==='brand' && <BrandPanel data={data} change={change} openSources={openSources}/>}
   {tab==='access' && <AccessibilityPanel data={data} checks={checks} operate={operate} brandRules={brandRules} focus={focus} ack={ack} setAck={setAck}/>}
   {tab==='seo' && seoReport.seo && <SeoPanel page={page} report={seoReport} siteWarnings={linkReport.warnings}/>}
   {tab==='seo' && !seoReport.seo && <p role="alert">{seoReport.errors[0]}</p>}
   {tab==='links' && <LinksPanel page={page} data={data} report={linkReport} openPage={openPage} focus={focus} operate={operate} drafts={{...otherDrafts(page),[page]:data}}/>}
   {tab==='david' && <>
    <p>Describe text, section order, global branding or a local style change for <strong>{pageInfo(page).label}</strong> only. David receives this page, its current revision{server.available?` (server v${server.latest})`:''}, the approved brand context, Business Brain records and site guidance. Nothing changes until you accept. Each request is one explicit model call.</p>
    <label htmlFor="studio-request">Tell David what to change</label>
    <textarea id="studio-request" value={request} onChange={e=>setRequest(e.target.value)} placeholder="Using our brand guidelines, change the homepage headline, update the accent colour, move the workflow section and round the buttons." maxLength={2000}/>
    <button disabled={busy || !request.trim()} onClick={propose}>{busy?'Preparing…':'Propose changes'}</button>
    {proposal && <section className="studio-proposal"><h3>Review proposal</h3><p>{proposal.value.summary}</p>
     <p className="studio-help">Brand: {proposal.brandStatus==='approved'?'approved rules enforced':proposal.brandStatus==='proposed_not_authoritative'?'proposed rules supplied as guidance (not yet approved)':'unavailable'} · Sources: {proposal.sources.join(', ') || 'none'}</p>
     {!!proposal.value.sections?.length && <ul>{proposal.value.sections.map((op,i)=><li key={i}><strong>{op.op==='add'?`Add ${typeOf(op.type)?.label}`:op.op==='duplicate'?`Duplicate ${instanceLabel(proposal.base.content,op.source)}`:`Remove ${instanceLabel(proposal.base.content,op.id)}`}</strong><ins>{op.op==='remove'?'removed':`new section ${op.id}${op.place&&op.place!=='end'?` ${op.place} ${instanceLabel(preview.content,op.ref??op.source)}`:op.place==='end'?' at end':''}`}</ins></li>)}</ul>}
     <ul>{proposal.value.edits.map((e,i)=><li key={i}><strong>{instanceLabel(preview.content,e.section)} · {e.field}</strong><del>{editBefore(proposal.base,e) ?? '(new section)'}</del><ins>{e.text}</ins></li>)}</ul>
     {proposal.value.design && <ul>{Object.entries(proposal.value.design).map(([k,v])=><li key={k}>{k}: <del>{String(proposal.base.root.props.design[k])}</del><ins>{String(v)}</ins></li>)}</ul>}
     {proposal.value.styles && <ul>{proposal.value.styles.map((s,i)=><li key={i}>{s.section} · {s.target}<del>{JSON.stringify(styleBefore(proposal.base,s)||{})}</del><ins>{s.values===null?'Reset to Global':JSON.stringify(s.values)}</ins></li>)}</ul>}
     {proposal.value.components && <ul>{proposal.value.components.map((c,i)=><li key={i}>Component {c.component}<del>{JSON.stringify(findItem(proposal.base,c.component)?.props.graphic)}</del><ins>{JSON.stringify(findItem(preview,c.component)?.props.graphic)}</ins></li>)}</ul>}
     {proposal.value.order && <p>Section order: {proposal.value.order.map(id=>instanceLabel(preview.content,id)).join(' → ')}</p>}
     <button onClick={()=>setMobile(!mobile)}>{mobile?'Desktop proposal':'Mobile proposal'}</button>
     <div className="studio-proposal-frame" style={{width:mobile?375:'100%'}}><iframe title="David proposed page" sandbox="" srcDoc={'<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'+staticPage(page,preview,sharedData)+'</body></html>'}/></div>
     {stale && <p role="alert">The page changed. Request a fresh proposal before accepting.</p>}
     {!!offBrand.length && <p role="alert">Off-brand proposal blocked: {offBrand.map(v=>`${v.token} ${v.value}`).join(', ')}.</p>}
     {!!introduced.length && <p role="alert" data-testid="proposal-contrast">Accessibility: proposal blocked. It introduces {introduced.length} WCAG AA contrast failure{introduced.length===1?'':'s'}: {introduced.map(f=>`${instanceLabel(preview.content,f.section)} ${targetLabel(f.type,f.target)} ${f.foreground.hex} on ${f.background.hex} ${formatRatio(f.ratio)} (needs ${f.required}:1)`).join('; ')}.</p>}
     {!!inherited.length && <p className="studio-help" data-testid="proposal-known-contrast">The page already has {inherited.length} known contrast failure{inherited.length===1?'':'s'} this proposal does not introduce (see Accessibility).</p>}
     <button disabled={stale || !!offBrand.length || !!introduced.length} onClick={accept}>Accept changes</button>
     <button onClick={()=>{setProposal(null);setStatus('Proposal rejected.')}}>Reject</button>
    </section>}
   </>}
   <details className="studio-sections" ref={listRef}><summary>Sections ({data.content.length}) · drag to reorder</summary><ol>{data.content.map((item,index)=><li key={item.props.id} data-section={item.props.id} data-type={item.type} aria-current={selection?.section===item.props.id||undefined} draggable onDragStart={e=>e.dataTransfer.setData('text/plain',item.props.id)} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();const id=e.dataTransfer.getData('text/plain');if(data.content.some(i=>i.props.id===id))move(id,index)}}><button className="studio-section-select" title={item.props.id} onClick={()=>focus(data,item.props.id)}>⠿ {instanceLabel(data.content,item.props.id)}<small>{item.props.id}</small></button><button aria-label={`Move ${item.props.id} up`} disabled={!index} onClick={()=>move(item.props.id,index-1)}>↑</button><button aria-label={`Move ${item.props.id} down`} disabled={index===data.content.length-1} onClick={()=>move(item.props.id,index+1)}>↓</button></li>)}</ol></details>
   <div className="studio-versions" data-page={page}>{server.available?<>Server versions: {server.latest?`v${server.latest} latest`:'none yet'} {server.latest>0 && <button onClick={loadLatest}>Load latest version</button>} {!!server.versions?.length&&<><label>History <select aria-label="Saved draft version" value={historyVersion||server.latest} onChange={e=>setHistoryVersion(Number(e.target.value))}>{server.versions.map(v=><option key={v.version} value={v.version}>v{v.version} · {v.origin||'manual'}</option>)}</select></label><button onClick={()=>loadVersion(historyVersion||server.latest)}>Load selected version</button></>}</>:<>Server versions unavailable: {server.error || 'checking…'}</>}</div>
   <small>Browser draft + versioned server drafts · deterministic visual edits · no publishing<br/>Site adapter base {snapshot.commit.slice(0,8)}</small>
  </>,panel)}
 </>
}

const chipOrder=[['local','Local'],['drive','Drive'],['brain','Brain'],['brand','Brand']]

const PAGE_KEY = site => `ttros-studio:page:${site}`
function rememberedPage(site) {try{const value=localStorage.getItem(PAGE_KEY(site));return pageInfo(value)?value:'home'}catch{return 'home'}}
const safeFingerprint = data => {try{return fingerprint(data)}catch{return null}}
const offline = {available:false,latest:0,data:null,error:'checking…'}

export default function WebsiteStudio({onBack,site='default-site',sites=[],onSite,initialView,onOpenCapture}) {
 const [panel,setPanel]=useState(null),[tab,setTab]=useState('david'),[selection,setSelection]=useState(null)
 const [view,setView]=useState(initialView || (site==='default-site'?'live':'editor'))
 const [sources,setSources]=useState({status:null,error:''})
 const [brand,setBrand]=useState(null)
 const [contrastMarks,setContrastMarks]=useState('')
 const failing=useMemo(()=>new Set(contrastMarks?contrastMarks.split('|'):[]),[contrastMarks])
 const [page,setPage]=useState(()=>rememberedPage(site))
 // Stage 3: one in-memory record per opened page: its working data and undo history (unsaved work
 // survives page switches), the fingerprint of its last browser save, and its session state.
 const pagesRef=useRef({})
 const [dirty,setDirty]=useState({})
 const [servers,setServers]=useState({})
 function pageState(id) {
  if(!pagesRef.current[id]) {
   let data,startupError=''
   try{
    const raw=localStorage.getItem(draftKey(id)),version=raw?JSON.parse(raw).version:null
    data=loadDraft(localStorage,id)
    if(version&&version<DRAFT_VERSION)startupError=`Saved draft migrated from version ${version} to section instances (version ${DRAFT_VERSION}); IDs, content, order and styles are unchanged. Save to keep the new format.`
   }catch(e){data=initialDataFor(id);startupError=`Saved draft unavailable: ${e.message}`}
   pagesRef.current[id]={data,history:null,saved:safeFingerprint(data),session:{startupError}}
  }
  return pagesRef.current[id]
 }
 const current=pageState(page)
 const [sharedData,setSharedData]=useState(()=>pageState('site').data)
 const remember=useCallback((id,data,history)=>{
  const state=pagesRef.current[id]
  if(!state)return
  state.data=data;state.history=history
  if(id==='site')setSharedData(data)
  const isDirty=safeFingerprint(data)!==state.saved
  setDirty(d=>d[id]===isDirty?d:{...d,[id]:isDirty})
 },[])
 const markSaved=useCallback((id,data)=>{const state=pageState(id);state.saved=safeFingerprint(data);setDirty(d=>({...d,[id]:false}))},[])
 // The other pages' current drafts (working copy, else browser draft, else the original site) for links and SEO.
 const otherDrafts=useCallback(current=>Object.fromEntries(pageIds.filter(id=>id!==current).map(id=>[id,pageState(id).data])),[])
 // Selecting opens Selected styles, except while the owner is managing sections (add above/below).
 const select=useCallback((section,target)=>{setSelection(current=>target===null&&current?.section===section?current:{section,target:target||'section'});setTab(t=>t==='sections'?t:'selected')},[])
 const selectionContext=useMemo(()=>({selection,select}),[selection,select])
 // Switching pages remounts the editor with that page's own data and history; nothing is carried across.
 const openPage=useCallback(next=>{
  if(!pageInfo(next))return
  setSelection(null);setContrastMarks('')
  setTab(next==='site'?'shared':'design')
  setPage(next)
  try{localStorage.setItem(PAGE_KEY(site),next)}catch{/* page memory is a per-browser convenience */}
 },[site])
 // Deterministic reads only: no Drive download, no model. Drive freshness is checked when asked.
 const load=useCallback(async()=>{
  const [status,brandResult]=await Promise.allSettled([studioApi.sources(site),studioApi.brand(site)])
  setSources({status:status.status==='fulfilled'?status.value:null,error:status.status==='rejected'?status.reason.message:''})
  if(brandResult.status==='fulfilled')setBrand(brandResult.value)
 },[site])
 useEffect(()=>{load()},[load])
 // Each page's server versions are read when the page is first opened (and after a reload of sources).
 const loadServer=useCallback(async id=>{
  try{
   const drafts=await studioApi.drafts(site,id)
   let data=initialDataFor(id)
   if(drafts.latest){try{data=validateEnvelope((await studioApi.draft(site,drafts.latest,id)).envelope,id)}catch{data=null}}
   setServers(all=>({...all,[id]:{available:true,latest:drafts.latest,versions:drafts.drafts,data,error:''}}))
  }catch(e){setServers(all=>({...all,[id]:{available:false,latest:0,data:initialDataFor(id),error:e.message}}))}
 },[site])
 const requested=useRef(new Set())
 useEffect(()=>{if(!requested.current.has(page)){requested.current.add(page);loadServer(page)}},[page,loadServer])
 const server=servers[page]||offline
 const setServer=useCallback(value=>setServers(all=>({...all,[page]:value})),[page])
 const refresh=useCallback(async()=>{const status=await studioApi.refresh(site);setSources({status,error:''});setBrand(await studioApi.brand(site))},[site])
 const openSources=useCallback(()=>setView('sources'),[])
 const openReleases=useCallback(()=>setView('releases'),[])
 const [lastBuild,setLastBuild]=useState(null)
 const context=useMemo(()=>({sharedSession:pageState('site').session,sharedData,panel,tab,setTab,site,brand,server,setServer,openSources,setContrastMarks,page,openPage,session:current.session,remember,markSaved,otherDrafts,setLastBuild,openReleases}),[sharedData,panel,tab,site,brand,server,setServer,openSources,page,openPage,current,remember,markSaved,otherDrafts,openReleases])
 const status=sources.status
 const label=sites.find(s=>s.id===site)?.label || status?.site?.label || 'Glade nectar ne'
 const info=pageInfo(page)
 // Puck takes data/history only when it mounts (keyed by page): computed once per page switch.
 const editor=useMemo(()=>({data:validateData(current.history?current.history.histories[current.history.index]?.state?.data ?? current.data:current.data,page),
  initialHistory:current.history?{histories:current.history.histories.map(h=>h.state?.data?{...h,state:{...h.state,data:validateData(h.state.data,page)}}:h),index:current.history.index,appendData:false}:undefined}),[page,DRAFT_VERSION])
 return <SelectionContext.Provider value={selectionContext}><ContrastContext.Provider value={failing}><StudioContext.Provider value={context}><SiteContext.Provider value={{data:sharedData,navigate:openPage}}><PageContext.Provider value={page}><section className="studio" data-site={site} data-page={page} data-live={view==='live'}>
  <header className="studio-heading"><div><p className="eyebrow">{label.toUpperCase()} / {info.label.toUpperCase()}</p><h1>TTROS Website Studio</h1></div>
   <div className="studio-site-bar">
    <label className="studio-site-select"><span>Website</span><select aria-label="Studio website" value={site} onChange={e=>onSite?.(e.target.value)}>{(sites.length?sites:[{id:site,label}]).map(s=><option key={s.id} value={s.id}>{s.label}{s.name&&s.name!==s.label?` — ${s.name}`:''}</option>)}</select></label>
    <ul className="studio-chips" aria-label="Connected sources">{chipOrder.map(([k,l])=><li key={k}><button onClick={()=>setView('sources')} title={status?.[k]?.detail||sources.error}>{l} <StateBadge state={status?.[k]?.state || (sources.error?'error':undefined)}/></button></li>)}</ul>
    <nav className="studio-views" aria-label="Studio views">{site==='default-site'&&<button aria-pressed={view==='live'} onClick={()=>setView('live')}>Live website editor</button>}<button aria-pressed={view==='editor'} onClick={()=>setView('editor')}>{site==='default-site'?'Preserved candidate':'Visual editor'}</button><button aria-pressed={view==='sources'} onClick={()=>setView('sources')}>Sources & Branding</button><button aria-pressed={view==='releases'} onClick={()=>setView('releases')}>Releases</button></nav>
    {onBack && <button onClick={onBack}>Back to Websites</button>}
   </div>
  </header>
  {view==='live' && <Suspense fallback={<p role="status">Opening the live website editor…</p>}><LiveEditor site={site} onOpenSources={()=>setView('sources')} onOpenCandidate={()=>setView('editor')} onOpenReleases={b=>{if(b?.id)setLastBuild(b);openReleases()}} onOpenCapture={onOpenCapture}/></Suspense>}
  {view!=='live'&&<nav className="studio-pages" aria-label="Studio pages">{['site',...pageIds].map(id=>{const p=pageInfo(id);return <button key={id} data-page={id} aria-pressed={page===id} aria-label={`${p.label} page${dirty[id]?' (unsaved changes)':''}`} onClick={()=>openPage(id)}><strong>{p.label}</strong><small>{p.route}</small>{dirty[id] && <span className="studio-dirty" title="Unsaved changes in this page">● unsaved</span>}</button>})}</nav>}
  {view==='releases' && <ReleasesPanel site={site} built={lastBuild}/>}
  {view==='sources' && <WebsiteSources site={site} status={status} brand={brand} error={sources.error} onRefresh={refresh} onChanged={load}/>}
  {/* Puck's hotkeys are module-global: the candidate editor must not stay mounted beside the live editor. */}
  {view!=='live' && <div className="studio-workspace" hidden={view!=='editor'}><div className="studio-editor"><Puck key={`${page}:${DRAFT_VERSION}`} config={config} data={editor.data} initialHistory={editor.initialHistory} ui={editorUi} headerTitle={info.label} headerPath="Local draft" height="860px" permissions={permissions} viewports={viewports} overrides={overrides} plugins={plugins}/></div><aside className="studio-assistant" ref={setPanel}/></div>}
 </section></PageContext.Provider></SiteContext.Provider></StudioContext.Provider></ContrastContext.Provider></SelectionContext.Provider>
}
