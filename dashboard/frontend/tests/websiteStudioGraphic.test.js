import test from 'node:test'
import assert from 'node:assert/strict'
import {defaultGraphic,validateGraphic,graphicStyle,graphicAttributes} from '../src/studio/workflow-graphic.js'
import {initialData,validateData,applyProposal,exportDraft,validateEnvelope} from '../src/studio/state.js'
// Schema v5: the 215679e WorkflowFigure is a text story; its design properties are bounded values.
const withGraphic=g=>{const d=structuredClone(initialData);d.content[0].props.workflow[0].props.graphic=g;return d}
test('graphic properties survive edits and exports; defaults render the source unchanged',()=>{
 const g={...defaultGraphic,width:70,horizontal:'end',accent:'#527a65',line:'#8f8676',titleScale:115,mobileOrder:'before'}
 const data=validateData(withGraphic(g))
 const edited=applyProposal(data,{edits:[{section:'Section0',field:'text7',text:'A clear workflow'}]})
 assert.deepEqual(edited.content[0].props.workflow[0].props.graphic,g)
 assert.equal(edited.content[0].props.workflow[0].props.titleLine1,'A clear workflow')
 assert.deepEqual(validateEnvelope(exportDraft(data)),data)
 assert.equal(Object.hasOwn(validateData(initialData).content[0].props,'graphic'),false)
 assert.deepEqual(graphicStyle(g),{width:'70%',justifySelf:'end',alignSelf:'center',borderTopColor:'#527a65','--studio-workflow-line':'#8f8676','--studio-workflow-title-scale':1.15})
 assert.deepEqual(graphicAttributes(g),{'data-studio-mobile-order':'before'})
 assert.deepEqual(graphicStyle(defaultGraphic),{},'the source sizing applies untouched')
 assert.deepEqual(graphicAttributes(defaultGraphic),{})
})
test('property validation rejects unbounded, active and retired version 1 values and unrelated components',()=>{
 for(const patch of [{width:0},{width:101},{width:NaN},{accent:'red'},{line:'url(x)'},{horizontal:'absolute'},{titleScale:69},{titleScale:141},{mobileOrder:'hidden'},{path:'/tmp/file'},{source:'asset'},{signal:'#527a65'}])assert.throws(()=>validateGraphic({...defaultGraphic,...patch}))
 const data=withGraphic(defaultGraphic);data.content[2].props.graphic=defaultGraphic;assert.throws(()=>validateData(data),/Unknown text field/)
})
