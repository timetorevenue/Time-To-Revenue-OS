// Runs only against the isolated offline API; every proposal is mocked.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE)
const out=process.env.STUDIO_PROOF_DIR, base=process.env.STUDIO_URL||'http://127.0.0.1:4350'
await fs.mkdir(out,{recursive:true})
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true})
page.setDefaultTimeout(15000)
let mockedProposals=0
const errors=[],passed=[],requests=[],queue=[]
page.on('pageerror',e=>errors.push(e.message))
await page.route('**/api/**',async route=>{
 const r=route.request(),p=new URL(r.url()).pathname;requests.push([r.method(),p])
 if(p.endsWith('/studio/propose'))return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(queue.shift())})
 if(p.includes('/website/studio/')||r.method()==='GET')return route.continue()
 return route.fulfill({status:403,body:'{}'})
})
const pause=()=>page.waitForTimeout(500)
const pick=async id=>{console.log('page',id);await page.locator(`.studio-pages button[data-page="${id}"]`).click();await pause()}
const tab=async label=>{await page.locator('.studio-tabs').getByRole('button',{name:label,exact:true}).click();await pause()}
const field=async(label,value)=>{const e=page.getByLabel(label,{exact:true});await e.fill(value);await e.press('Tab');await pause()}
const frame=()=>page.frameLocator('.studio-editor iframe')
const doc=fn=>page.evaluate(fn)
await page.goto(base+'/websites?tab=studio&site=default-site')
await page.locator('.studio-pages button[data-page="site"]').waitFor()
await pick('site')
await field('Header wordmark','Example Company Studio')
await field('navigation services label','Studio services')
await field('Footer email','studio@example.com')
assert.equal(await frame().locator('header .brand-lockup span').innerText(),'Example Company Studio')
assert.equal(await frame().locator('footer nav[aria-label="Explore Example Company"] a').first().innerText(),'Studio services')
passed.push('shared header/footer fields and one synchronized navigation list')
await page.getByRole('button',{name:'Save draft',exact:true}).click();await pause()
assert.match(await page.locator('.studio-assistant').innerText(),/server version v\d+/)
for(const id of ['home','service-process','service-integration','service-ai']){
 await pick(id)
 assert.equal(await frame().locator('header .brand-lockup span').innerText(),'Example Company Studio')
 assert.equal(await frame().locator('footer a[href="mailto:studio@example.com"]').count(),1)
}
passed.push('one shared draft displayed on all four pages with separate page data')
await pick('site');await field('navigation services destination','javascript:bad')
assert.match(await page.locator('.studio-status').innerText(),/rejected/)
assert.equal(await page.getByLabel('navigation services destination',{exact:true}).inputValue(),'/services')
passed.push('unsafe navigation rejected without mutating the draft')
// Mock a real review response with the request fingerprint.
await tab('David')
await page.route('**/api/website/studio/propose',async route=>{
 mockedProposals++;const body=route.request().postDataJSON()
 const {fingerprint}=await import('../src/studio/state.js')
 const proposal={summary:'Shared CTA',edits:[{section:'SharedHeader',field:'cta',text:'Plan your automation'}]}
 return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({proposal,base:fingerprint(body.data)})})
})
await page.getByLabel('Tell David what to change').fill('Update shared CTA')
await page.getByRole('button',{name:'Propose changes',exact:true}).click();await pause()
await page.getByRole('button',{name:'Accept changes',exact:true}).click();await pause()
assert.match(await frame().locator('header .header-cta').innerText(),/Plan your automation/)
await page.getByRole('button',{name:'undo',exact:true}).click();await pause()
assert.match(await frame().locator('header .header-cta').innerText(),/Discuss your business/)
passed.push('mocked David shared proposal preview, accept and atomic undo')
// Renames remain optional and reviewable, including shared navigation rows.
await tab('Shared elements')
await field('navigation services destination','/services/ai-implementation')
await page.getByRole('button',{name:'Preview label updates',exact:true}).click()
assert.equal(await page.getByLabel('navigation services label',{exact:true}).inputValue(),'Studio services')
await page.getByRole('button',{name:'Reject label updates',exact:true}).click()
await page.getByRole('button',{name:'Preview label updates',exact:true}).click()
await page.getByRole('button',{name:'Accept label updates',exact:true}).click();await pause()
assert.notEqual(await page.getByLabel('navigation services label',{exact:true}).inputValue(),'Studio services')
await page.getByRole('button',{name:'undo',exact:true}).click();await pause()
await field('navigation services destination','/services')
passed.push('shared rename dependencies preview/reject/accept/undo without silent propagation')
// Store all five real browser draft envelopes, with independent page overrides for leak checks.
await page.evaluate(async()=>{
 const {initialDataFor,applyProposal,exportDraft}=await import('/src/studio/state.js')
 for(const id of ['home','service-process','service-integration','service-ai']){
 let d=initialDataFor(id)
 if(id==='service-integration')d=applyProposal(d,{edits:[],design:{headingFont:'Georgia',background:'#ffffff',text:'#123456',buttonPaddingBlock:20}})
 localStorage.setItem(`ttros-studio:global:default-site:${id}:v1`,JSON.stringify(exportDraft(d,id)))
 }
})
await page.reload();await page.locator('.studio-pages').waitFor();await pick('service-integration')
const colors=await doc(()=>{const d=document.querySelector('.studio-editor iframe').contentDocument;return ['header','main','footer'].map(s=>d.defaultView.getComputedStyle(d.querySelector(s)).getPropertyValue('--paper'))})
assert.deepEqual(colors,['#f7f3ea','#ffffff','#f7f3ea'])
assert.match(await frame().locator('main h1').evaluate(e=>getComputedStyle(e).fontFamily),/Georgia/)
assert.equal(await frame().locator('main h1').evaluate(e=>getComputedStyle(e).color),'rgb(18, 52, 86)')
passed.push('page background/text/font/button tokens remain inside main; shared shell keeps global defaults')
await pick('service-ai')
const rename=frame().locator('[data-studio-section="ServiceAi0"] p.section-label [contenteditable]').first()
await rename.hover();await rename.click();await page.keyboard.press('Control+A');await page.keyboard.type('AI Adoption');await page.locator('.studio-assistant h2').first().click();await pause()
await pick('home');await tab('Links')
await page.getByRole('button',{name:'Preview page label updates',exact:true}).click()
await page.getByRole('button',{name:'Reject page label updates',exact:true}).click()
await page.getByRole('button',{name:'Preview page label updates',exact:true}).click()
await page.getByRole('button',{name:'Accept page label updates',exact:true}).click();await pause()
assert.match(await page.locator('.studio-status').innerText(),/Accepted page label updates/)
await page.getByRole('button',{name:'undo',exact:true}).click();await pause()
passed.push('cross-page link label dependencies preview/reject/accept/undo explicitly')
await pick('service-ai');await page.getByRole('button',{name:'undo',exact:true}).click();await pause()
await pick('site');assert.equal(await page.getByLabel('Header wordmark',{exact:true}).inputValue(),'Example Company Studio')
passed.push('shared browser persistence survives reload')
// Export using the actual UI so contrast and SEO gates run.
for(const id of ['site','home','service-process','service-integration','service-ai']){
 await pick(id)
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export site draft',exact:true}).click()
 await (await download).saveAs(`${out}/export-${id}.json`)
}
assert.equal(mockedProposals,1)
assert.deepEqual(errors,[])
await page.screenshot({path:`${out}/shared-editor.png`,fullPage:true})
await fs.writeFile(`${out}/browser-proof.json`,JSON.stringify({passed,pageErrors:errors,modelCalls:0,mockedProposals:1},null,2))
console.log(JSON.stringify({passed:passed.length,errors}))
await browser.close()
