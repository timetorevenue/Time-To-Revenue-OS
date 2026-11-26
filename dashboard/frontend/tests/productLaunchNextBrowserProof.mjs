import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
const base=process.env.NEXT_URL||'http://127.0.0.1:4382'
const out=process.env.LAUNCH_PROOF_DIR||new URL('../../../proofs/product_launch_integration_20261119/',import.meta.url).pathname
const b=await chromium.launch({headless:true}),p=await b.newPage(),checks=[]
for(const width of [1440,375]){
 await p.setViewportSize({width,height:1000})
 for(const route of ['/','/services/business-process-automation','/services/systems-integration','/services/ai-implementation','/offers/test-workflow-audit']){
  const r=await p.goto(base+route);assert.equal(r.status(),200);await p.locator('main h1').waitFor()
  assert.equal(await p.locator('main h1').count(),1)
  checks.push({width,route,title:await p.title(),heading:await p.locator('main h1').innerText()})
  if(route.startsWith('/offers/')){assert.match(await p.locator('main').innerText(),/TEST ONLY/);await p.screenshot({path:out+`/next-offer-${width}.png`,fullPage:true})}
 }
}
await fs.writeFile(out+'/next-browser.json',JSON.stringify({checks,source:'Actual EXCO Next components via the existing Studio adapter',publication:'held'},null,2)+'\n');await b.close()
