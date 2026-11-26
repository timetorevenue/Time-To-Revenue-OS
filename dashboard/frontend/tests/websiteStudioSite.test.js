import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { execFileSync } from 'node:child_process'
import { snapshot, initialData, exportDraft } from '../src/studio/state.js'
import { verifySource, createSiteFixture } from '../scripts/studio-site-fixture.mjs'
import manifest from '../src/studio/source-manifest.json' with { type: 'json' }

test('adapter rejects malformed envelopes before creating files or reading a website',async()=>{
 await assert.rejects(createSiteFixture({version:999,data:initialData},{source:'/path-that-must-never-be-read'}),/version 999 is not supported/)
 const wrong=exportDraft(initialData);wrong.source.files['app/page.tsx']='drift'
 await assert.rejects(createSiteFixture(wrong,{source:'/path-that-must-never-be-read'}),/source\/version/)
 // A v5 label on a draft still in the c58e641 shape is not silently accepted.
 const mislabelled=JSON.parse(fs.readFileSync(new URL('./fixtures/studio-v4/home-untouched.json',import.meta.url)));mislabelled.version=5;mislabelled.source=exportDraft(initialData).source
 await assert.rejects(createSiteFixture(mislabelled,{source:'/path-that-must-never-be-read'}),/section|field|graphic|text/i)
})
test('source verifier reads the pinned commit from Git objects and rejects changed bytes or a missing commit, without modifying the source', {skip:!fs.existsSync(snapshot.source)},()=>{
 const head=execFileSync('git',['-C',snapshot.source,'rev-parse','HEAD'],{encoding:'utf8'})
 assert.doesNotThrow(()=>verifySource(snapshot.source))
 const pinned=manifest.files['components/WorkflowFigure.tsx']
 try{
  manifest.files['components/WorkflowFigure.tsx']='0'.repeat(64)
  assert.throws(()=>verifySource(snapshot.source),/source drift: components\/WorkflowFigure.tsx/)
 }finally{manifest.files['components/WorkflowFigure.tsx']=pinned}
 assert.equal(execFileSync('git',['-C',snapshot.source,'rev-parse','HEAD'],{encoding:'utf8'}),head,'the checkout HEAD is untouched')
 assert.equal(execFileSync('git',['-C',snapshot.source,'status','--porcelain'],{encoding:'utf8'}),'')
 // A small unrelated Git fixture lacks the pinned commit; no website is copied.
 const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'ttros-studio-drift-test-'))
 execFileSync('git',['init','-q',fixture])
 execFileSync('git',['-C',fixture,'-c','user.name=Studio test','-c','user.email=studio-test@localhost','commit','--allow-empty','-qm','fixture'])
 assert.throws(()=>verifySource(fixture))
})
