import test from 'node:test'
import assert from 'node:assert/strict'
import { initialDataFor,loadDraft,applyProposal,exportDraft,validateEnvelope } from '../src/studio/state.js'
import { siteNewFailures,effectiveDesign,sharedLinkProblem,validateSharedContent,sharedCss } from '../src/studio/shared.js'
import { contrastFailures,newContrastFailures } from '../src/studio/contrast.js'
import { defaultDesign,scopeCss,designCss } from '../src/studio/design.js'
const site=()=>initialDataFor('site')
test('shared envelope round trips independently; required elements cannot be removed or duplicated',()=>{
 const d=site();assert.deepEqual(validateEnvelope(exportDraft(d,'site'),'site'),d)
 for(const sections of [[{op:'remove',id:'SharedHeader'}],[{op:'duplicate',source:'SharedFooter',id:'footer-copy'}]])assert.throws(()=>applyProposal(d,{edits:[],sections}))
 assert.throws(()=>validateEnvelope(exportDraft(d,'site'),'home'))
})
test('shared destinations, logos, navigation IDs and emails are bounded',()=>{
 const edit=(field,text,section='SharedHeader')=>applyProposal(site(),{edits:[{section,field,text}]})
 for(const [field,text] of [['logo','https://evil/logo'],['destination','javascript:evil'],['navigation','[]bad'],['navigation',JSON.stringify([{id:'x',label:'X',href:'/about'}])]])assert.throws(()=>edit(field,text))
 assert.throws(()=>edit('email','x@bad','SharedFooter'))
 const rows=[{id:'stable-id',label:'Automation',href:'/services/business-process-automation'}]
 assert.equal(JSON.parse(edit('navigation',JSON.stringify(rows)).content[0].props.navigation)[0].id,'stable-id')
 assert.throws(()=>edit('navigation',JSON.stringify([...rows,...rows])))
 assert.equal(sharedLinkProblem('/#solutions'),null);assert.match(sharedLinkProblem('/#missing'),/does not exist/)
})
test('shared global defaults preserve old page overrides and explicit resets to a default',()=>{
 const global=applyProposal(site(),{edits:[],design:{accent:'#123456',h2Size:55}})
 const page=initialDataFor('service-ai');page.root.props.design.headingFont='Georgia'
 assert.equal(effectiveDesign(page,global).headingFont,'Georgia');assert.equal(effectiveDesign(page,global).accent,'#123456')
 const edited=applyProposal(page,{edits:[],design:{h2Size:defaultDesign.h2Size}})
 assert.equal(effectiveDesign(edited,global).h2Size,defaultDesign.h2Size)
})
test('page CSS cannot target body, root, header or footer; shared CSS has email target sizing',()=>{
 const css=scopeCss(designCss(defaultDesign),'service-ai')
 assert.doesNotMatch(css,/:has\(|:root|\bbody\s*[{.:]/)
 assert.match(css,/:where\(main\[data-studio-page="service-ai"\]\)\.editorial h1/)
 assert.match(sharedCss(site()),/min-height:24px/)
})
test('shared contrast gates detect global and selected-element regressions',()=>{
 assert.deepEqual(contrastFailures(site()),[])
 const bad=applyProposal(site(),{edits:[],styles:[{section:'SharedHeader',target:'navigation',values:{text:'#ffffff'}}]})
 assert.ok(newContrastFailures(site(),bad).some(f=>f.section==='SharedHeader'))
})

test('legacy homepage global tokens seed the first shared draft without rewriting history',()=>{
 const home=initialDataFor('home');home.root.props.design.accent='#123456'
 const raw=JSON.stringify(exportDraft(home,'home'))
 const storage={getItem:k=>k.includes(':home:')?raw:null}
 assert.equal(loadDraft(storage,'site').root.props.design.accent,'#123456')
 assert.equal(storage.getItem('x:home:x'),raw)
})

test('default buttons retain website padding, tracking, transparent border and contact colour',()=>{
 const css=designCss(defaultDesign)
 assert.match(css,/padding:12.48px 16px 12.48px 18.4px/)
 assert.match(css,/border:1px solid transparent/)
 assert.match(css,/letter-spacing:-0.005em/)
 assert.match(css,/contact-band \.site-button\{background:#e0c899/)
 assert.doesNotMatch(css,/filter:brightness/)
 assert.match(designCss({...defaultDesign,borderColor:'#123456'}),/border:1px solid var\(--rule\)/)
})

test('shared proposals cannot introduce contrast failures on inheriting pages',()=>{
 const next=applyProposal(site(),{edits:[],design:{muted:'#ffffff'}})
 assert.deepEqual(contrastFailures(next),[])
 assert.ok(siteNewFailures('site',site(),next,{home:initialDataFor('home')},site()).length>0)
})
