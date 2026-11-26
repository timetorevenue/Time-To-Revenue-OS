import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {createSiteFixture,PREVIEW_CANDIDATE} from '../scripts/studio-site-fixture.mjs'
import {initialDataFor,exportDraft,applyProposal} from '../src/studio/state.js'
import {studioClient} from '../src/studio/api.js'
const source=process.env.TTROS_PREVIEW_TEST_REPOSITORY||'/home/operator/ttros-projects/default-site'

test('candidate adapter: hero, workflow and service edits render; other routes and source bytes are preserved; drift fails closed',{skip:!fs.existsSync(path.join(source,'.git'))},async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'studio-candidate-test-'))
 const head=execFileSync('git',['-C',source,'rev-parse','HEAD'],{encoding:'utf8'})
 const home=applyProposal(initialDataFor('home'),{edits:[{section:'Section0',field:'text1',text:'Grow with less admin. '},{section:'Section0-workflow',field:'step2Title',text:'Assign one owner'},{section:'HeroSignature',field:'text0',text:'Clear processes. Connected systems.'}],components:[{component:'Section0-workflow',values:{graphic:{accent:'#527a65',mobileOrder:'before'}}}]})
 const ai=applyProposal(initialDataFor('service-ai'),{edits:[{section:'ServiceAi5',field:'text1',text:'Start with one repeated task.'}]})
 const result=await createSiteFixture([exportDraft(home,'home'),exportDraft(ai,'service-ai')],{source,fixtureRoot:root,candidate:true,copyDependencies:false})
 assert.equal(result.base,PREVIEW_CANDIDATE)
 for(const file of ['app/blog/page.tsx','app/services/systems-integration/page.tsx','app/get-guide/page.tsx','components/WorkflowFigure.tsx','content/services.ts']) {
  const expected=execFileSync('git',['-C',source,'show',`${PREVIEW_CANDIDATE}:${file}`],{stdio:['ignore','pipe','ignore']})
  assert.deepEqual(fs.readFileSync(path.join(result.fixture,file)),expected,file)
 }
 assert.deepEqual(result.changes.map(c=>c.path).sort(),['app/globals.css','app/page.tsx','app/services/ai-implementation/page.tsx','components/Editorial.tsx','components/ServicePage.tsx','content/ttros-studio-draft.json','content/ttros-studio-service-ai.json','lib/ttros-studio-model.js','lib/ttros-studio.tsx','next.config.ts'])
 assert(fs.readFileSync(path.join(result.fixture,'next.config.ts'),'utf8').includes("form-action 'none'"))
 assert.match(fs.readFileSync(path.join(result.fixture,'app/services/ai-implementation/page.tsx'),'utf8'),/studioPageSeo\("service-ai"\)/)
 const saved=JSON.parse(fs.readFileSync(path.join(result.fixture,'content/ttros-studio-draft.json'),'utf8'))
 assert.equal(saved.version,5);assert.equal(saved.data.content[0].props.workflow[0].props.step2Title,'Assign one owner')
 // Genuine c58e641 drafts migrate on read into the same fixture path.
 const legacy=JSON.parse(fs.readFileSync(new URL('./fixtures/studio-v4/home-edited.json',import.meta.url)))
 const migrated=await createSiteFixture(legacy,{source,fixtureRoot:root,candidate:true,copyDependencies:false})
 const draft=JSON.parse(fs.readFileSync(path.join(migrated.fixture,'content/ttros-studio-draft.json'),'utf8'))
 assert.equal(draft.data.content[1].props.id,'HeroSignature');assert.equal(draft.data.content[0].props.text2,'Not your overtime.')
 // A source text that no longer matches the template fails closed (proved by the adapter at build time);
 // an envelope tampered after migration is rejected before any file is written.
 const tampered=exportDraft(home,'home');tampered.data.content[0].props.text9999='x'
 await assert.rejects(createSiteFixture(tampered,{source,fixtureRoot:root,candidate:true,copyDependencies:false}),/Unknown text field/)
 assert.equal(execFileSync('git',['-C',source,'rev-parse','HEAD'],{encoding:'utf8'}),head)
 assert.equal(execFileSync('git',['-C',source,'status','--porcelain'],{encoding:'utf8'}),'')
})

test('build uses exact saved versions and retains CSRF header; status never calls AI',async()=>{
 const calls=[]
 const client=studioClient(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({id:'a'})}})
 await client.build('default-site',{version:2,shared_version:3})
 await client.buildStatus('default-site','a')
 await client.stopBuild('default-site','a')
 assert.equal(calls[0].options.headers['X-TTROS-Connections'],'1')
 assert.deepEqual(JSON.parse(calls[0].options.body),{version:2,shared_version:3})
 assert.equal(calls[1].url,'/api/website/studio/sites/default-site/builds/a')
 assert.equal(calls[2].options.method,'DELETE')
 assert.equal(calls[2].options.headers['X-TTROS-Connections'],'1')
 assert(calls.every(c=>!c.url.includes('propose')))
})
