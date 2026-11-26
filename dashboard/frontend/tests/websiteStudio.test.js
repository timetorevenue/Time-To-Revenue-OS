import {test} from 'node:test'
import assert from 'node:assert/strict'
import { initialData, snapshot, validateData, applyProposal, saveDraft, loadDraft } from '../src/studio/state.js'
test('actual homepage: nine stable sections plus the hero signature, and formatted hero destinations',()=>{
 assert.equal(initialData.content.length,10)
 assert.deepEqual(initialData.content.map(i=>i.props.id),['Section0','HeroSignature','Section1','Section2','Section3','Section4','Section5','Section6','Section7','Section8'])
 assert.match(snapshot.sections[0].html,/<h1/)
 assert.match(snapshot.sections[0].html,/<span>/)
 assert.equal(initialData.content[0].props.text0,'Orchid basil indigo quartz summit kelp dune yonder jade')
 assert.equal(initialData.content[0].props.text1,'Velvet basil iris n ')
})
test('proposal replaces text only and reorders without mutating source',()=>{
 const order=initialData.content.map(i=>i.props.id).reverse()
 const next=applyProposal(initialData,{edits:[{section:'Section0',field:'text1',text:'New headline'}],order})
 assert.equal(next.content.at(-1).props.text1,'New headline')
 assert.equal(initialData.content[0].props.text1,'Velvet basil iris n ')
 assert.equal(next.content.at(-1).props.text2,'Willow grove ember')
})
test('reject unknown destinations, types, IDs, oversize text and missing sections',()=>{
 for(const edit of [{section:'client-site',field:'text0',text:'x'},{section:'Section0',field:'style',text:'red'},{section:'Section0',field:'id',text:'x'},{section:'Section0',field:'text0',text:'x'.repeat(4001)}]) assert.throws(()=>applyProposal(initialData,{edits:[edit]}))
 assert.throws(()=>applyProposal(initialData,{edits:[],order:['Section0']}))
 assert.throws(()=>validateData({...initialData,content:[]}))
})
test('draft round trip, invalid storage fails visibly, failed save propagates',()=>{
 let value;const storage={getItem:()=>value,setItem:(k,v)=>value=v}
 saveDraft(storage,initialData); assert.deepEqual(loadDraft(storage),initialData)
 value='{broken';assert.throws(()=>loadDraft(storage))
 assert.throws(()=>saveDraft({setItem(){throw Error('quota')}},initialData),/quota/)
})

// Stage 1 contract: the same validated data drives Puck, proposals and the site adapter.
import { defaultDesign, schema, designCss, localStyle, elementTargets } from '../src/studio/design.js'
import { exportDraft, validateEnvelope, DRAFT_KEY, DRAFT_VERSION } from '../src/studio/state.js'
import legacyFlat from './fixtures/studio-v4/home-v2-flat.json' with { type: 'json' }
test('v1 browser drafts migrate without losing text/order; v2 includes source provenance', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, ()=>{
 // A genuine Stage 1 draft (c58e641 schema: flat hero graphic text, no links), reversed and edited.
 const old={root:{props:{}},content:structuredClone(legacyFlat.data.content).map(({type,props:{styles,...props}})=>({type,props})).reverse()}
 old.content.at(-1).props.text0='Saved before Stage 1. '
 let raw=JSON.stringify({version:1,data:old})
 const storage={getItem:key=>{assert.equal(key,DRAFT_KEY);return raw},setItem:(key,value)=>raw=value}
 const migrated=loadDraft(storage)
 // The edited headline moved with its meaning (old text0 → v5 text1); order and IDs are kept.
 assert.equal(migrated.content.find(i=>i.type==='Section0').props.text1,'Saved before Stage 1. ')
 assert.deepEqual(migrated.content.filter(i=>i.type!=='HeroSignature').map(i=>i.props.id),old.content.map(i=>i.props.id))
 assert.deepEqual(migrated.root.props.design,defaultDesign)
 saveDraft(storage,migrated)
 assert.equal(JSON.parse(raw).version,DRAFT_VERSION) // v5 envelope; v1–v4 still load (validateEnvelope)
 assert.equal(JSON.parse(raw).source.commit,snapshot.commit)
 assert.deepEqual(loadDraft(storage),migrated)
 for(const tamper of [e=>e.version=999,e=>e.source.commit='stale',e=>e.source.files['app/page.tsx']='modified',e=>e.project='another-client']){
  const value=exportDraft(migrated);value.source=structuredClone(value.source);tamper(value)
  assert.throws(()=>validateEnvelope(value))
 }
})
test('global edits and explicit local styles share proposals, persist, and reset by property/target',()=>{
 const local=applyProposal(initialData,{edits:[],design:{accent:'#0969a5',bodyFamily:'Georgia'},styles:[{section:'Section0',target:'section',values:{background:'#e1edf5'}},{section:'Section0',target:'a-0',values:{radius:32,background:'#173f67'}}]})
 const changed=applyProposal(local,{edits:[],design:{background:'#ffffff',buttonRadius:4,accent:'#214c3b'}})
 assert.equal(changed.content[0].props.styles.section.background,'#e1edf5')
 assert.equal(changed.content[0].props.styles['a-0'].radius,32)
 assert.equal(initialData.content[0].props.styles['a-0'],undefined)
 const resetProperty=applyProposal(changed,{edits:[],styles:[{section:'Section0',target:'a-0',values:{radius:null}}]})
 assert.deepEqual(resetProperty.content[0].props.styles['a-0'],{background:'#173f67'})
 const reset=applyProposal(resetProperty,{edits:[],styles:[{section:'Section0',target:'a-0',values:null},{section:'Section0',target:'section',values:null}]})
 assert.deepEqual(reset.content[0].props.styles,{})
 assert.match(designCss(reset.root.props.design),/border-radius:4px/)
 assert.match(designCss(reset.root.props.design),/--gold:#214c3b/)
 assert.match(designCss(reset.root.props.design),/Georgia,serif/)
 assert.deepEqual(localStyle({radius:32}),{borderRadius:32})
 assert.deepEqual(validateEnvelope(exportDraft(changed)),changed)
})
test('design and destination validation rejects code, invalid values and inappropriate targets',()=>{
 for(const design of [{accent:'red;display:none'},{h1Font:'url(https://bad)'},{h1Weight:true},{bodySize:Infinity},{sectionSpacing:-1},{unknown:'x'}])assert.throws(()=>applyProposal(initialData,{edits:[],design}))
 for(const style of [{section:'other',target:'section',values:{}},{section:'Section0',target:'a-999',values:{radius:3}},{section:'Section0',target:'section',values:{buttonVariant:'primary'}},{section:'Section0',target:'a-0',values:{html:'<script>'}},{section:'Section0',target:'a-0',values:{radius:null,unknown:null}},{section:'Section0',target:'a-0',values:[]}])assert.throws(()=>applyProposal(initialData,{edits:[],styles:[style]}))
 assert.throws(()=>validateData({...initialData,root:{props:{design:{accent:'#ffffff'}}}}))
 assert.equal(elementTargets(snapshot.sections[0]).filter(t=>t.tag==='a').length,2)
 assert.equal(Object.keys(defaultDesign).length,Object.keys(schema.globalTokens).length)
})
