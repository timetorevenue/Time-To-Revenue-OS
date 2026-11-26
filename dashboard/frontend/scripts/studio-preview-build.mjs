// Explicit saved-draft build; no model, provider, publish or source writes.
// purpose 'release' keeps the website's own next.config.ts (no preview CSP/iframe changes), so the
// build is the one a release may carry; 'preview' (default) is the embeddable isolated preview.
import fs from 'node:fs'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {createSiteFixture,PREVIEW_CANDIDATE} from './studio-site-fixture.mjs'
const [requestFile]=process.argv.slice(2)
const request=JSON.parse(fs.readFileSync(requestFile,'utf8'))
const receipt=await createSiteFixture(request.envelopes,{source:request.repository,fixtureRoot:request.output,candidate:request.purpose!=='release',copyDependencies:false})
const folder=receipt.fixture
execFileSync('npm',['ci','--include=dev','--no-audit','--no-fund','--prefer-offline'],{cwd:folder,stdio:['ignore',2,2],timeout:300000})
execFileSync('npm',['run','build'],{cwd:folder,stdio:['ignore',2,2],timeout:600000})
let diff=''
for(const change of [...receipt.changes,...['tsconfig.json','next-env.d.ts'].filter(p=>!receipt.changes.some(c=>c.path===p)).map(path=>({path}))]) {
 let before=''
 try{before=execFileSync('git',['-C',request.repository,'show',`${PREVIEW_CANDIDATE}:${change.path}`],{encoding:'utf8',stdio:['ignore','pipe','ignore']})}catch{}
 if(before===fs.readFileSync(path.join(folder,change.path),'utf8'))continue
 const original=path.join(request.output,`before-${diff.length}.txt`)
 fs.writeFileSync(original,before)
 const result=(()=>{try{return execFileSync('git',['diff','--no-index','--',original,path.join(folder,change.path)],{encoding:'utf8'})}catch(e){if(e.status!==1)throw e;return e.stdout}})()
 diff+=`\nFile: ${change.path}\n${result}`
}
fs.writeFileSync(path.join(request.output,'source.diff'),diff)
const routes=JSON.parse(fs.readFileSync(path.join(folder,'.next/server/app-paths-manifest.json'),'utf8'))
const result={...receipt,purpose:request.purpose==='release'?'release':'preview',build:'passed',nextBuildId:fs.readFileSync(path.join(folder,'.next/BUILD_ID'),'utf8').trim(),routes:Object.keys(routes),versions:request.versions,sourceDiff:diff}
fs.writeFileSync(path.join(request.output,'build.json'),JSON.stringify(result,null,2))
