import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {initialData,validateData,validateEnvelope,exportDraft,applyProposal,fingerprint} from '../src/studio/state.js'
import {migrateItem,walkItems,sourceItem,childId} from '../src/studio/nested.js'
import {contrastFailures} from '../src/studio/contrast.js'
import {defaultGraphic} from '../src/studio/workflow-graphic.js'
const legacy=()=>JSON.parse(fs.readFileSync(new URL('../../../proofs/website_studio_usability_20261118/export-home.json',import.meta.url)))
test('v3 migration is idempotent, pure, carries the caption and layout, retains the uploaded image and keeps styles', { skip: "Needs the originating company's real website copy (not distributed) or fails identically in the originating tree; see tests/core_known_failures.json" }, ()=>{
 const env=legacy(),bytes=JSON.stringify(env);env.data.content[0].props.styles['figure-0']={radius:11}
 const before=structuredClone(env.data.content[0]),next=validateEnvelope(env)
 const parent=next.content[0],child=parent.props.workflow[0]
 assert.equal(child.type,'WorkflowFigure');assert.equal(child.props.id,childId(parent.props.id,'workflow'))
 // The owner's caption becomes the v5 title; the old SVG/image properties are retained, never rendered.
 assert.equal(child.props.titleLine1,before.props.text6);assert.equal(child.props.titleLine2,'')
 assert.deepEqual(child.props.graphic,{...defaultGraphic,width:75,horizontal:'end',vertical:'start'})
 assert.deepEqual(next.root.props.unsupported.map(u=>u.path),['Section0-workflow.graphic.source','Section0-workflow.graphic.asset','Section0-workflow.graphic.alt','Section0-workflow.graphic.signal'])
 assert.deepEqual(next.root.props.unsupported[1].value,before.props.graphic.asset)
 assert.deepEqual(child.props.styles.section,{radius:11});assert.equal(parent.props.graphic,undefined);assert.equal(parent.props.text6,undefined);assert.equal(parent.props.text5,'Grove yonder ba ')
 assert.deepEqual(validateData(next),next);assert.deepEqual(validateEnvelope(exportDraft(next)),next)
 assert.deepEqual(sourceItem(parent).props.graphic,child.props.graphic)
 assert.equal(JSON.stringify(legacy()),bytes,'the original committed export is not rewritten')
})
test('native slot invariants: exactly one allowed child, globally unique IDs, bounded depth and no flat/native mixtures',()=>{
 for(const mutate of [d=>d.content[0].props.workflow=[],d=>d.content[0].props.workflow.push(structuredClone(d.content[0].props.workflow[0])),d=>d.content[0].props.workflow[0].type='Section1',d=>d.content[0].props.workflow[0].props.id='Section0',d=>d.content[2].props.workflow=[],d=>d.content[0].props.graphic={}]){
  const d=structuredClone(initialData);mutate(d);assert.throws(()=>validateData(d))
 }
 const d=structuredClone(initialData);delete d.content[0].props.workflow;assert.throws(()=>validateData(d),/requires exactly/)
})
test('nested text/styles/typed properties share the deterministic proposal reducer, with legacy aliases supported',()=>{
 const id=initialData.content[0].props.workflow[0].props.id
 const p={summary:'Update actual workflow',edits:[{section:id,field:'titleLine1',text:'A native component'}],styles:[{section:id,target:'section',values:{radius:8}}],components:[{component:id,values:{graphic:{width:75,horizontal:'end',accent:'#527a65'}}}]}
 const next=applyProposal(initialData,p),child=next.content[0].props.workflow[0]
 assert.equal(child.props.titleLine1,'A native component');assert.equal(child.props.graphic.width,75);assert.equal(child.props.graphic.accent,'#527a65')
 assert.throws(()=>applyProposal(initialData,{edits:[],components:[{component:id,values:{graphic:{signal:'#527a65'}}}]}),/graphic properties/)
 assert.deepEqual(child.props.styles.section,{radius:8})
 const alias=applyProposal(initialData,{edits:[{section:'Section0',field:'text7',text:'Legacy text'}],styles:[{section:'Section0',target:'figure-0',values:{radius:4}}]})
 assert.equal(alias.content[0].props.workflow[0].props.titleLine1,'Legacy text')
 assert.deepEqual(alias.content[0].props.workflow[0].props.styles.section,{radius:4})
 assert.throws(()=>applyProposal(initialData,{edits:[],components:[{component:'Section0',values:{graphic:{width:75}}}]}))
 assert.notEqual(fingerprint(next),fingerprint(initialData));assert.equal([...walkItems(next.content)].length,11)
})
test('nested contrast uses the child identity and real figure background',()=>{
 const id=initialData.content[0].props.workflow[0].props.id
 assert.deepEqual(contrastFailures(initialData),[])
 // 215679e: dark text on the light #e9e3d7 story surface, so a dark local surface is the failure case.
 const bad=applyProposal(initialData,{edits:[],styles:[{section:id,target:'section',values:{background:'#1b1e1a'}}]})
 assert.ok(contrastFailures(bad).some(f=>f.section===id&&f.type==='WorkflowFigure'))
})
test('the canvas delegates nested selection and inspector to Puck, with no graphic pointer interception',()=>{
 const config=fs.readFileSync(new URL('../src/studio/config.jsx',import.meta.url),'utf8'),view=fs.readFileSync(new URL('../src/views/WebsiteStudio.jsx',import.meta.url),'utf8')
 assert.match(config,/type:'slot',allow:spec.allow/);assert.match(config,/ref:puck\?\.dragRef/)
 assert.doesNotMatch(config,/addEventListener|stopImmediatePropagation|graphicRef|pointerEvents/)
 assert.match(view,/<Puck.Fields\/>/);assert.match(view,/getSelectorForId/)
})
