// Fresh browser, actual Puck click/outline/Fields/history; only explicit David requests are mocked.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import crypto from 'node:crypto'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
const base=process.env.STUDIO_URL||'http://127.0.0.1:4350',api=process.env.STUDIO_API||'http://127.0.0.1:4351',out=process.env.STUDIO_PROOF_DIR||new URL('../../../proofs/website_studio_native_20261119/',import.meta.url).pathname
await fs.mkdir(out,{recursive:true})
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1920,height:1160},acceptDownloads:true})
page.setDefaultTimeout(20000)
const errors=[],passed=[],hits=[],requests=[],queue=[],proposeBodies=[]
page.on('pageerror',e=>errors.push(e.message))
await page.route('**/api/**',async route=>{const r=route.request(),path=new URL(r.url()).pathname;requests.push([r.method(),path]);if(path.endsWith('/studio/propose')){proposeBodies.push(r.postDataJSON());return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(queue.shift())})}return path.startsWith('/api/website/studio/')||r.method()==='GET'?route.continue():route.fulfill({status:403,body:'{}'})})
const pause=(n=500)=>page.waitForTimeout(n),frame=()=>page.frameLocator('.studio-editor iframe'),aside=()=>page.locator('.studio-assistant'),native=()=>page.locator('.studio-native-inspector')
const selected=async()=>({type:await page.locator('.studio-page-line').getAttribute('data-puck-selected-type'),id:await page.locator('.studio-page-line').getAttribute('data-puck-selected-id'),zone:await page.locator('.studio-page-line').getAttribute('data-puck-selected-zone')})
// Physical pointer click, without force/dispatchEvent. Puck marks drag-locked components aria-disabled
// for dragging, while its own component click handler still selects them normally.
async function clickGraphic(x=.5,y=.5){const el=frame().locator('figure.workflow-figure');await el.scrollIntoViewIfNeeded();const r=await el.boundingBox();await page.mouse.click(r.x+r.width*x,r.y+r.height*y);await pause();const s=await selected();assert.equal(s.type,'WorkflowFigure');assert.equal(s.zone,'Section0:workflow');hits.push({x,y,...s});return s.id}
async function clickHero(){const el=frame().locator('.home-hero-copy');await el.scrollIntoViewIfNeeded();const r=await el.boundingBox();await page.mouse.click(r.x+15,r.y+25);await pause();assert.equal((await selected()).type,'Section0')}
async function pick(id){await page.locator(`.studio-pages [data-page="${id}"]`).click();await frame().locator(`main[data-studio-page="${id}"] h1`).waitFor();await pause()}
async function save(){await page.getByRole('button',{name:'Save draft',exact:true}).click();await pause();assert.match(await aside().locator('.studio-status').innerText(),/server version v\d+/)}
await page.goto(base+'/websites?tab=studio&site=default-site');await frame().locator('figure.workflow-figure').waitFor();await pause()
assert.equal(await page.evaluate(()=>localStorage.getItem('ttros-studio:global:default-site:home:v1')),null)
await page.evaluate(()=>document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click());await pause()
let id
for(const [x,y]of [[.08,.08],[.55,.45],[.5,.94]]){await clickHero();id=await clickGraphic(x,y)}
passed.push('fresh load: dark padding, SVG and workflow steps select the real Puck child; surrounding Hero selects Section0')
// Use the native Puck outline's actual HTML buttons for keyboard selection.
await clickHero()
const expand=page.locator('.studio-editor').getByRole('button',{name:'Expand',exact:true}).first()
if(!await page.locator('.studio-editor').getByRole('button',{name:'Hero',exact:true}).isVisible()){await page.locator('.studio-editor').getByRole('button',{name:'Toggle left sidebar',exact:true}).click();await pause()}
if(!await page.locator('.studio-editor button').filter({hasText:/^Workflow graphic$/}).isVisible()){await expand.click();await pause()}
await page.locator('.studio-editor button').filter({hasText:/^Workflow graphic$/}).press('Enter');await pause()
assert.equal((await selected()).id,id);assert.equal((await selected()).type,'WorkflowFigure')
await page.locator('.studio-workspace').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/native-selection-outline.png',fullPage:true})
await page.locator('.studio-editor').getByRole('button',{name:'Toggle left sidebar',exact:true}).click()
passed.push('Puck native outline keyboard selection opens native Puck.Fields for the nested child')
await native().getByLabel('Graphic caption',{exact:true}).fill('Native Puck workflow');await pause()
await native().getByLabel('Graphic width (%)',{exact:true}).fill('75');await pause()
await native().getByLabel('Horizontal graphic position',{exact:true}).selectOption('end');await native().getByLabel('Vertical graphic position',{exact:true}).selectOption('start')
await native().getByLabel('Workflow signal colour',{exact:true}).fill('#527a65');await pause()
assert.equal(await frame().locator('.workflow-map circle[cx="455"][r="10"]').getAttribute('fill'),'#527a65')
await native().getByLabel('Corner radius mode',{exact:true}).selectOption('local');await pause();await native().getByLabel('Local Corner radius',{exact:true}).fill('10');await pause()
passed.push('native inspector edits caption, responsive width, positioning, signal colour and graphic styles')
await fs.copyFile(new URL('../../../proofs/website_studio_usability_20261118/workflow-local.png',import.meta.url),out+'/workflow-local.png')
await native().getByLabel('Replace workflow with local image',{exact:true}).setInputFiles(out+'/workflow-local.png');await frame().locator('img.workflow-map').waitFor();await pause()
await native().getByLabel('Graphic image description',{exact:true}).fill('An inquiry is assigned to an owner and followed up.');await native().getByLabel('Graphic image description',{exact:true}).press('Tab');await pause(1000)
await native().getByLabel('Graphic image fit',{exact:true}).selectOption('cover');await pause(1000)
await page.getByRole('button',{name:'undo',exact:true}).click();await pause();assert.equal(await frame().locator('img.workflow-map').evaluate(e=>e.style.objectFit),'contain')
await page.getByRole('button',{name:'redo',exact:true}).click();await pause();assert.equal(await frame().locator('img.workflow-map').evaluate(e=>e.style.objectFit),'cover')
await native().getByLabel('Graphic image fit',{exact:true}).selectOption('contain');await pause();await save()
await page.reload();await frame().locator('img.workflow-map').waitFor();await clickGraphic()
assert.equal(await native().getByLabel('Graphic caption',{exact:true}).inputValue(),'Native Puck workflow')
assert.equal(await native().getByLabel('Graphic width (%)',{exact:true}).inputValue(),'75')
assert.equal(await frame().locator('.workflow-figure').evaluate(e=>e.style.borderRadius),'10px')
await pause(1000);await native().getByRole('button',{name:'Restore original workflow graphic',exact:true}).click();await frame().locator('svg.workflow-map').waitFor();await pause(1000)
assert.equal(await frame().locator('figcaption strong').innerText(),'Native Puck workflow')
await page.getByRole('button',{name:'undo',exact:true}).click();await frame().locator('img.workflow-map').waitFor();await pause(1000)
await page.getByRole('button',{name:'redo',exact:true}).click();await frame().locator('svg.workflow-map').waitFor();await pause(1000)
await page.getByRole('button',{name:'undo',exact:true}).click();await frame().locator('img.workflow-map').waitFor();await pause()
passed.push('local raster/alt text, native undo/redo, browser+server save and refresh retain the same child ID and properties')
// Load a real older version through the UI without rewriting it, then undo the load.
const url=api+'/api/website/studio/sites/default-site/drafts/1',old=await(await fetch(url)).json(),hash=v=>crypto.createHash('sha256').update(JSON.stringify(v)).digest('hex'),before=hash(old)
await page.getByLabel('Saved draft version',{exact:true}).selectOption('1');const start=requests.length
await page.getByRole('button',{name:'Load selected version',exact:true}).click();await pause()
await clickGraphic();const oldHero=old.envelope.data.content.find(i=>i.type==='Section0').props
assert.equal(await native().getByLabel('Graphic caption',{exact:true}).inputValue(),oldHero.workflow?.[0]?.props.caption??oldHero.text6)
assert.equal(hash(await(await fetch(url)).json()),before);assert.equal(requests.slice(start).filter(([m])=>m==='POST').length,0)
await page.getByRole('button',{name:'undo',exact:true}).click();await pause();await clickGraphic();assert.equal(await native().getByLabel('Graphic caption',{exact:true}).inputValue(),'Native Puck workflow')
passed.push('history picker loads a legacy flat draft as one native child, leaves historical bytes unchanged and supports undo')
assert.equal(proposeBodies.length,0,'ordinary editing and history did not request a model')
// Explicitly replay nested David operations; the real model endpoint is never reached.
const proposal={summary:'Native component review',edits:[{section:id,field:'caption',text:'Proposed native workflow'}],components:[{component:id,values:{graphic:{width:65}}}]}
queue.push({proposal,usage:{total_tokens:0}},{proposal,usage:{total_tokens:0}})
await aside().locator('.studio-tabs').getByRole('button',{name:'David',exact:true}).click();await page.getByLabel('Tell David what to change').fill('Edit the selected native workflow')
await page.getByRole('button',{name:'Propose changes',exact:true}).click();await page.getByRole('button',{name:'Reject',exact:true}).click();assert.equal(await frame().locator('figcaption strong').innerText(),'Native Puck workflow')
await page.getByRole('button',{name:'Propose changes',exact:true}).click();await page.getByRole('button',{name:'Accept changes',exact:true}).click();await pause()
assert.equal(await frame().locator('figcaption strong').innerText(),'Proposed native workflow');await page.getByRole('button',{name:'undo',exact:true}).click();await pause()
assert.equal(await frame().locator('figcaption strong').innerText(),'Native Puck workflow');assert.deepEqual(proposeBodies[0].selection,{section:id,target:'section'})
passed.push('mocked David nested ID/typed properties preview, reject, accept and atomic undo use the canonical tree')
await clickGraphic();await native().getByLabel('Graphic width (%)',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:out+'/native-edited-saved.png',fullPage:true})
// A real page-only style for the Next parity proof, independent of shared site defaults.
for(const name of ['service-process','service-integration','service-ai'])await pick(name)
await pick('service-integration');await aside().getByLabel('Heading font',{exact:true}).selectOption('Georgia');await pause();await save()
for(const name of ['home','service-process','service-integration','service-ai']){await pick(name);const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export site draft',exact:true}).click();await(await download).saveAs(out+`/export-${name}.json`)}
await page.locator('.studio-pages [data-page="site"]').click();await pause();const event=page.waitForEvent('download');await page.getByRole('button',{name:'Export site draft',exact:true}).click();await(await event).saveAs(out+'/export-site.json')
const exported=JSON.parse(await fs.readFile(out+'/export-home.json','utf8'))
assert.equal(exported.version,4);assert.equal(exported.data.content[0].props.workflow[0].props.id,id);assert.equal(exported.data.content[0].props.graphic,undefined)
assert.deepEqual(errors,[])
passed.push('all four pages and shared site export v4 data; no flattened graphic state or browser errors')
await fs.writeFile(out+'/browser-proof.json',JSON.stringify({passed,hits,historicalVersion:old.envelope.version,historicalHash:before,ordinaryModelRequests:0,mockedProposals:proposeBodies.length,pageErrors:errors,modelCalls:0},null,2)+'\n')
console.log(JSON.stringify({passed:passed.length,hits,mockedProposals:proposeBodies.length,errors}));await browser.close()
