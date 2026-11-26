// Verify the built Next site semantically against SAVED Studio envelopes.
// Stage 3: any mix of the homepage draft (Stage 1/2 checks, unchanged) and service-page drafts
// (real ServicePage output: sections, text, links, styles, related-link counts, title, meta
// description, canonical, Open Graph/Twitter, JSON-LD, page-scoped design CSS) plus the internal
// links between the built Studio pages.
// Usage: node scripts/studio-build-proof.mjs /tmp/fixture draft.json [draft.json ...]
import fs from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import { JSDOM } from 'jsdom'
import { validateEnvelope, envelopePage, snapshot } from '../src/studio/state.js'
import {sourceItem,styleDestination} from '../src/studio/nested.js'
import { localStyle } from '../src/studio/design.js'
import { typeOf, linkKeys, anchorMap, isOriginal, studioPages, sectionRules } from '../src/studio/sections.js'
import { pageSeo, seoProblems } from '../src/studio/seo.js'
import services from '../src/studio/services.json' with { type: 'json' }
import { verifySource } from './studio-site-fixture.mjs'
const [folder,...draftFiles]=process.argv.slice(2)
if(!folder||!draftFiles.length)throw Error('Usage: node scripts/studio-build-proof.mjs /tmp/fixture draft.json [draft.json ...]')
const built=route=>path.join(folder,'.next/server/app',route==='/'?'index.html':`${route.slice(1)}.html`)
const read=route=>new JSDOM(fs.readFileSync(built(route),'utf8')).window.document

// Every instance rendered from its real component: order, IDs, types, text, links, anchors, styles.
function verifySections(doc,data,originals,label) {
 const sections=[...doc.querySelectorAll('main.editorial > section')]
 assert.deepEqual(sections.map(s=>s.dataset.studioSection),data.content.map(i=>i.props.id),`${label}: built section order (instance IDs)`)
 assert.deepEqual(sections.map(s=>s.dataset.studioType),data.content.map(i=>i.type),`${label}: built section types`)
 const pageIds=[...doc.querySelectorAll('main [id]')].map(e=>e.id)
 assert.equal(new Set(pageIds).size,pageIds.length,`${label}: no duplicate DOM IDs on the built page`)
 for(const [index,nativeItem]of data.content.entries()) {
  const item=sourceItem(nativeItem)
  const section=sections[index],original=originals.find(s=>s.type===item.type)
  const hrefs=[...section.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))
  assert.deepEqual(hrefs,linkKeys(typeOf(item.type)).map(k=>item.props[k]),`instance links ${item.props.id}`)
  for(const [original,actual]of Object.entries(anchorMap(item)))if(!original.startsWith('workflow-'))assert(section.id===actual||section.querySelector(`[id="${actual}"]`),`anchor ${original} → ${actual} in ${item.props.id}`)
  if(!isOriginal(item))for(const id of typeOf(item.type).domIds)assert(!section.querySelector(`[id="${id}"]`)&&section.id!==id,`copy ${item.props.id} must not reuse #${id}`)
  const texts=[]
  const walk=node=>{for(const child of node.childNodes){if(child.nodeType===3&&child.textContent.trim()&&!node.closest('svg'))texts.push(child.textContent);else if(child.nodeType===1)walk(child)}}
  walk(section)
  assert.deepEqual(texts,Object.keys(original.texts).map(key=>item.props[key]),`real component text destinations ${item.type}`)
  for(const [target,values]of Object.entries(item.props.styles)) {
   const destination=styleDestination([nativeItem],item.props.id,target)
   const element=destination.item.props.id!==item.props.id?section.querySelector(`[data-studio-component-id="${destination.item.props.id}"]`):target==='section'?section:section.querySelector(`[data-studio-element="${target}"]`)
   assert(element,`built destination ${item.type}.${target}`)
   for(const [property,value]of Object.entries(localStyle(values))) {
    const actual=property.startsWith('--')?element.style.getPropertyValue(property):element.style[property]
    if(typeof value==='number')assert.equal(actual,String(value)+(property==='fontWeight'?'':'px'))
    else if(property.toLowerCase().includes('color')){
     const expected=doc.createElement('div');expected.style[property]=value;assert.equal(actual,expected.style[property])
    }else assert.equal(actual,value)
   }
  }
 }
 assert.equal(doc.querySelectorAll('main h1').length,1,`${label}: one H1`)
 assert(doc.querySelector('main h2'),`${label}: H2 present`)
}
// The page's own JSON-LD block (the one describing its WebPage); the layout adds a site-wide block.
function pageGraph(doc) {
 const blocks=[...doc.querySelectorAll('script[type="application/ld+json"]')].map(s=>JSON.parse(s.textContent)).filter(b=>(b['@graph']||[]).some(e=>e['@type']==='WebPage'||e['@type']==='AboutPage'))
 assert.equal(blocks.length,1,'exactly one page JSON-LD block')
 return blocks[0]
}
const stylesheet=doc=>[...doc.querySelectorAll('link[rel=stylesheet]')].map(e=>fs.readFileSync(path.join(folder,'.next',e.getAttribute('href').replace('/_next/','')),'utf8')).join('\n')

function verifyHome(envelope,draftFile) {
 const data=validateEnvelope(envelope,'home')
 const html=fs.readFileSync(built('/'),'utf8')
 const doc=new JSDOM(html).window.document
 verifySections(doc,data,snapshot.sections,'homepage')
 const nativeGraphic=data.content.find(i=>i.type==='Section0')?.props.workflow?.[0]
 const graphic=nativeGraphic?.props.graphic
 const figure=doc.querySelector('figure.workflow-figure')
 assert.equal(figure.dataset.studioComponent,'WorkflowFigure')
 assert.equal(figure.dataset.studioComponentId,nativeGraphic.props.id)
 if(graphic?.source==='asset') {
  const image=figure.querySelector('img.workflow-map');assert(image)
  assert.equal(image.getAttribute('src'),graphic.asset.data);assert.equal(image.alt,graphic.alt)
 } else assert(figure.querySelector('svg.workflow-map'))
 if(graphic){assert.equal(figure.style.width,graphic.width+'%');assert.equal(figure.style.justifySelf,graphic.horizontal);assert.equal(figure.style.alignSelf,graphic.vertical)}

 assert(doc.querySelector('img[alt="Harbor grove"]').getAttribute('src').startsWith('/_next/image'))
 assert.equal(doc.querySelector('link[rel=canonical]').href,'https://www.core-site.example/')
 const hero=data.content.find(i=>i.type==='Section0')
 assert.equal(doc.querySelector(`[data-studio-section="${hero.props.id}"] .site-button`).getAttribute('href'),hero.props.link0)
 const graphs=[...doc.querySelectorAll('script[type="application/ld+json"]')].flatMap(s=>JSON.parse(s.textContent)['@graph']||[])
 const faq=graphs.find(s=>s['@type']==='FAQPage')
 const faqProps=data.content.find(i=>i.type==='Section7')?.props
 assert.equal(!!faq,!!faqProps,'FAQ structured data present exactly when the FAQ section is')
 for(const [index,entry]of (faq?.mainEntity||[]).entries()){
  assert.equal(entry.name,faqProps[`text${index*2+1}`]);assert.equal(entry.acceptedAnswer.text,faqProps[`text${index*2+2}`])
 }
 assert.deepEqual(pageGraph(doc),pageSeo('home',data).jsonLd,'homepage JSON-LD equals the Studio page model')
 const css=stylesheet(doc)
 assert(css.includes(`--gold:${data.root.props.design.accent}`),'actual Next CSS contains saved accent token')
 assert(css.includes(`font-weight:${data.root.props.design.h1Weight}`),'actual Next CSS includes H1 style')
 assert(!/\.home-hero h1>span[^{]*\{color:var\(--gold\)/.test(css),'Hero emphasis line is not mapped to the Accent token (Stage 3 contrast fix)')
 const secondary=fs.readFileSync(built('/how-we-work'),'utf8')
 assert(new JSDOM(secondary).window.document.querySelector('main.editorial h1'),'existing secondary page preserved')
 const secondaryCss=[...new JSDOM(secondary).window.document.querySelectorAll('link[rel=stylesheet]')].map(e=>e.href)
 assert(secondaryCss.some(p=>doc.querySelector(`link[href="${p}"]`)),'secondary page uses shared global stylesheet')
 const counts=Object.fromEntries([...new Set(data.content.map(i=>i.type))].map(t=>[t,data.content.filter(i=>i.type===t).length]))
 return {page:'home',route:'/',instances:data.content.map(i=>({id:i.props.id,type:i.type,original:isOriginal(i)})),typeCounts:counts,passed:[`saved draft to real server-rendered Next homepage (${data.content.length} sections)`,'every instance rendered from its real component with every text destination','instance order, IDs and types','per-instance link destinations','unique DOM IDs; copies use instance-scoped anchors','local style destinations','semantic H1/H2 tags','native WorkflowFigure identity, saved graphic properties/asset and Next Image','canonical URL and CTA destination','FAQ JSON-LD matches editable FAQ','homepage JSON-LD equals the Studio page model','Hero emphasis line keeps the site colour (no Accent mapping)','global tokens in built shared CSS','existing secondary page shares global styles'],htmlSha256:crypto.createHash('sha256').update(html).digest('hex'),draftSha256:crypto.createHash('sha256').update(fs.readFileSync(draftFile)).digest('hex')}
}

function verifyService(envelope,draftFile,page) {
 const data=validateEnvelope(envelope,page),info=studioPages.pages[page],source=services.pages[page]
 const html=fs.readFileSync(built(info.route),'utf8')
 const doc=new JSDOM(html).window.document
 assert.equal(doc.querySelector('main.editorial')?.dataset.studioPage,page,`${page}: main carries the Studio page`)
 verifySections(doc,data,source.sections,page)
 const {seo,errors}=seoProblems(page,data)
 assert.deepEqual(errors,[],`${page}: SEO model valid`)
 const meta=name=>doc.querySelector(`meta[name="${name}"],meta[property="${name}"]`)?.getAttribute('content')
 assert.equal(doc.querySelector('title').textContent,seo.documentTitle,`${page}: <title>`)
 assert.equal(meta('description'),seo.description,`${page}: meta description`)
 assert.equal(doc.querySelector('link[rel=canonical]').getAttribute('href'),seo.canonical,`${page}: canonical unchanged`)
 assert.equal(seo.canonical,source.seo.canonical,`${page}: canonical equals the original`)
 assert.equal(meta('og:url'),seo.openGraph.url);assert.equal(meta('og:title'),seo.openGraph.title);assert.equal(meta('og:description'),seo.openGraph.description)
 assert.equal(meta('twitter:title'),seo.twitter.title);assert.equal(meta('twitter:description'),seo.twitter.description)
 const graph=pageGraph(doc)
 assert.deepEqual(graph,seo.jsonLd,`${page}: JSON-LD equals the Studio page model`)
 const ids=graph['@graph'].map(e=>e['@id']).filter(Boolean)
 assert.deepEqual(ids,source.seo.jsonLd['@graph'].map(e=>e['@id']).filter(Boolean),`${page}: structured-data IDs unchanged`)
 // Related-service lists keep the original structure (3, 2 and 4 entries), with the draft's links.
 const related=[...doc.querySelectorAll('ul.link-list > li > a')].map(a=>a.getAttribute('href'))
 assert.equal(related.length,source.related.length*data.content.filter(i=>i.type===source.sections[3].type).length,`${page}: related-link count preserved`)
 const css=stylesheet(doc)
 // The page's own tokens, scoped to its <main data-studio-page> (quotes may be minified away).
 const scoped=new RegExp(`:where\\(main\\[data-studio-page="?${page}"?\\]\\)\\{[^}]*--gold:${data.root.props.design.accent}[^}]*--studio-primary:${data.root.props.design.primary}`,'i')
 assert(scoped.test(css),`${page}: page-scoped design tokens in the built stylesheet`)
 return {page,route:info.route,instances:data.content.map(i=>({id:i.props.id,type:i.type,original:isOriginal(i)})),title:seo.documentTitle,description:seo.description,canonical:seo.canonical,relatedLinks:related.length,
  passed:[`saved draft to real server-rendered Next ${info.route} via components/ServicePage.tsx (${data.content.length} sections)`,'every instance rendered from its real component with every text destination','instance order, IDs, types, links, anchors and local styles','one H1','<title>, meta description, Open Graph and Twitter follow the visible service name/description','canonical URL unchanged','JSON-LD (WebPage, BreadcrumbList, Service) equals the Studio page model; @ids unchanged',`related-link list keeps ${source.related.length} entries per instance`,'page-scoped design tokens in the shared stylesheet'],
  htmlSha256:crypto.createHash('sha256').update(html).digest('hex'),draftSha256:crypto.createHash('sha256').update(fs.readFileSync(draftFile)).digest('hex')}
}

const reports=[],drafts={}
for(const file of draftFiles) {
 const envelope=JSON.parse(fs.readFileSync(file,'utf8')),page=envelopePage(envelope)
 drafts[page]=validateEnvelope(envelope,page)
 if(page==='site') {
 const h=drafts.site.content.find(i=>i.type==='SharedHeader').props,f=drafts.site.content.find(i=>i.type==='SharedFooter').props
 for(const route of ['/',...Object.values(studioPages.pages).filter(p=>p.route.startsWith('/services/')).map(p=>p.route)]) {
  const doc=read(route)
  assert.equal(doc.querySelector('header .brand-lockup span').textContent,h.name)
  for(const selector of ['header .desktop-nav','footer nav[aria-label="Explore Example Company"]'])assert.deepEqual([...doc.querySelectorAll(selector+' a')].map(a=>[a.textContent,a.getAttribute('href')]),JSON.parse(h.navigation).map(r=>[r.label,r.href]))
  assert.equal(doc.querySelector('footer a[href^="mailto:"]').getAttribute('href'),'mailto:'+f.email)
 }
 reports.push({page:'site',passed:['shared Header/Footer fields and synchronized navigation rendered on all four built pages']})
 } else reports.push(page==='home'?verifyHome(envelope,file):verifyService(envelope,file,page))
}
// Internal links between built Studio pages: every internal link on every built Studio page is a
// built route, and every same-site anchor exists on its target page.
const links=[]
// A draft-only page (the TEST offer) is built only when its draft is exported.
for(const page of Object.keys(studioPages.pages).filter(p=>p!=='site').filter(p=>!studioPages.pages[p].draftOnly||fs.existsSync(built(studioPages.pages[p].route)))) {
 const doc=read(studioPages.pages[page].route)
 for(const a of doc.querySelectorAll('main a[href^="/"], main a[href^="#"]')) {
  const href=a.getAttribute('href'),[p,anchor]=href.split('#'),route=p?(p.replace(/\/+$/,'')||'/'):studioPages.pages[page].route
  const exists=fs.existsSync(built(route))||sectionRules.links.routePrefixes.some(prefix=>route.startsWith(prefix))
  assert(exists,`${page}: ${href} resolves to a built page`)
  if(anchor&&fs.existsSync(built(route)))assert(read(route).getElementById(anchor),`${page}: ${href} anchor exists`)
  const target=Object.keys(studioPages.pages).filter(p=>p!=='site').find(id=>studioPages.pages[id].route===route)
  if(target&&target!==page)links.push({from:page,to:target,href,text:a.textContent.trim()})
 }
}
for(const page of Object.keys(studioPages.pages).filter(p=>p!=='site').filter(p=>p!=='home'&&!studioPages.pages[p].draftOnly))assert(links.some(l=>l.from==='home'&&l.to===page),`built homepage links to ${page}`)
verifySource(snapshot.source)
console.log(JSON.stringify({pages:reports,studioPageLinks:links,passed:['every internal link on the four built Studio pages resolves to a built page (anchors checked)','built homepage links to all three service pages','source manifests unchanged'],fixture:folder,base:snapshot.commit,modelCalls:0},null,2))
