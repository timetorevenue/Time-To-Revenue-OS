// Real browser + real local HTTP, existing David/Jobs/CRM/Studio; offline Stripe fixture.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import crypto from 'node:crypto'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright')
const base=process.env.LAUNCH_URL||'http://127.0.0.1:4384'
const out=process.env.LAUNCH_PROOF_DIR||new URL('../../../proofs/product_launch_integration_20261119/',import.meta.url).pathname
await fs.mkdir(out,{recursive:true})
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[]
page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(20000)
await page.goto(base+'/websites');await page.getByRole('button',{name:'Prepare test launch',exact:true}).waitFor()
const response=page.waitForResponse(r=>r.url().endsWith('/studio/launch')&&r.request().method()==='POST')
await page.getByRole('button',{name:'Prepare test launch',exact:true}).click()
const created=await(await response).json();assert.equal(created.job.status,'done');assert.equal(created.david.job_id,created.job.id);assert.equal(created.offer.stripe.provider_verified,false)
await page.getByTestId('launch-package').waitFor()
const api='/api/website/studio/launch'
const lead=await page.request.post(base+api+'/leads',{data:{name:'Integration test lead',email:'integration@example.invalid',consent:false}});assert.equal(lead.status(),200);assert.equal((await lead.json()).nurture_allowed,false)
const event={id:'evt_integration',livemode:false,account:'acct_fixture',type:'checkout.session.completed',data:{object:{id:'cs_test_integration',livemode:false,payment_link:'plink_fixture',payment_status:'paid',amount_total:4900,currency:'cad',metadata:{site:'default-site',offer_id:created.offer.id,test_only:'true'}}}}
async function send(e){const body=JSON.stringify(e),stamp=String(Math.floor(Date.now()/1000)),sig=crypto.createHmac('sha256','whsec_fixture').update(stamp+'.'+body).digest('hex');return page.request.post(base+api+'/webhook',{data:body,headers:{'content-type':'application/json','stripe-signature':`t=${stamp},v1=${sig}`}})}
const first=await send(event);assert.equal(first.status(),200);const receipt=await first.json();assert.equal(receipt.status,'paid');assert.equal(receipt.provider_verified,false)
assert.deepEqual(await(await send(event)).json(),receipt)
const wrong=structuredClone(event);wrong.id='evt_wrong';wrong.data.object.metadata.site='wrong';assert.equal((await send(wrong)).status(),400)
const failed=structuredClone(event);failed.id='evt_failed';failed.type='checkout.session.async_payment_failed';failed.data.object.payment_status='unpaid';const failure=await(await send(failed)).json();assert.equal(failure.status,'not_paid');assert.equal(failure.fulfillment_job,undefined)
assert.equal((await page.request.post(base+api+'/publish')).status(),403);assert.equal((await page.request.post(base+api+'/send')).status(),403)
const crm=await(await page.request.get(base+'/api/crm/overview')).json();assert.equal(Object.keys(crm.offers).length,1);assert.equal(Object.keys(crm.purchases).length,1);assert.equal(Object.keys(crm.contacts).length,1);assert.equal(Object.keys(crm.opportunities).length,0)
const jobs=(await(await page.request.get(base+'/api/david/jobs')).json()).jobs;assert.equal(jobs.length,2);assert(jobs.every(j=>j.status==='done'))
await page.reload();await page.getByTestId('launch-package').waitFor();await page.screenshot({path:out+'/integrated-review.png',fullPage:true})
await page.getByRole('button',{name:'Edit landing page in Website Studio'}).click();await page.getByLabel('Tell David what to change').waitFor();const frame=page.frameLocator('.studio-editor iframe');await frame.locator('main h1').waitFor();await page.getByRole('button',{name:'Load latest version',exact:true}).click();await frame.getByRole('heading',{name:'Find the handoff that waits longest.',exact:true}).waitFor();await page.screenshot({path:out+'/integrated-studio.png',fullPage:true})
assert.deepEqual(errors,[])
await fs.writeFile(out+'/http-browser-proof.json',JSON.stringify({mode:'OFFLINE FIXTURE; NOT STRIPE PROVIDER PROOF',job:created.job.id,davidJob:created.david.job_id,fulfillment:receipt.fulfillment_job,crm:{offers:Object.keys(crm.offers),purchases:Object.keys(crm.purchases),contacts:Object.keys(crm.contacts),opportunities:0},duplicate:'same receipt',wrongSite:400,send:403,publish:403,failedPayment:failure.status,consent:false,errors,provider_verified:false},null,2)+'\n')
await browser.close()
