import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE)
const out=process.env.STUDIO_PROOF_DIR,fixture=process.env.FIXTURE
const site=process.env.SITE_URL||'http://127.0.0.1:4361',studio='http://127.0.0.1:4350'
const routes={home:'/', 'service-process':'/services/business-process-automation','service-integration':'/services/systems-integration','service-ai':'/services/ai-implementation'}
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1200,height:900}}),editor=await browser.newPage({viewport:{width:1440,height:1000}})
const errors=[],report={axe:{},parity:[],navigation:[],responsive:[]}
page.on('pageerror',e=>errors.push(e.message));editor.on('pageerror',e=>errors.push(e.message))
const envs=Object.fromEntries(await Promise.all(['site',...Object.keys(routes)].map(async p=>[p,JSON.parse(await fs.readFile(`${out}/export-${p}.json`,'utf8'))])))
await editor.goto(studio+'/websites');await editor.evaluate(envs=>{for(const [id,env]of Object.entries(envs))localStorage.setItem(`ttros-studio:global:default-site:${id}:v1`,JSON.stringify(env));localStorage.setItem('ttros-studio:page:default-site','home')},envs)
await editor.goto(studio+'/websites?tab=studio&site=default-site');await editor.locator('.studio-editor iframe').waitFor()
await editor.evaluate(()=>document.querySelector('[aria-label="Switch to Desktop viewport"]')?.click())
const selectors=['header','header .brand-lockup span','header .desktop-nav a','header .site-button','main h1','main .site-button','footer','footer .brand-lockup span','footer .footer-contact>a:last-child','footer nav a']
const props=['color','backgroundColor','fontFamily','fontSize','fontWeight','paddingTop','paddingRight','paddingBottom','paddingLeft','minHeight','borderRadius','letterSpacing','borderTopColor']
const measure=(doc,selectors,props)=>selectors.map(s=>{const e=doc.querySelector(s);if(!e)return null;const c=doc.defaultView.getComputedStyle(e);return Object.fromEntries(props.map(k=>[k,k==='fontFamily'?(c[k].match(/geist|inter|georgia/i)?.[0]||c[k]).toLowerCase():c[k]]))})
for(const [id,route]of Object.entries(routes)){
 await page.goto(site+route)
 if(id==='service-integration') {assert.match(await page.locator('main h1').evaluate(e=>getComputedStyle(e).fontFamily),/Georgia/);assert.equal(await page.locator('header').evaluate(e=>getComputedStyle(e).getPropertyValue('--paper')),'#f7f3ea')}
 await editor.locator(`.studio-pages button[data-page="${id}"]`).click();await editor.waitForTimeout(600)
 const a=await editor.evaluate(([f,s,p])=>new Function('return '+f)()(document.querySelector('.studio-editor iframe').contentDocument,s,p),[measure.toString(),selectors,props])
 const b=await page.evaluate(([f,s,p])=>new Function('return '+f)()(document,s,p),[measure.toString(),selectors,props])
 const diffs=[];for(let i=0;i<selectors.length;i++)for(const k of props)if(a[i]?.[k]!==b[i]?.[k])diffs.push({selector:selectors[i],property:k,editor:a[i]?.[k],built:b[i]?.[k]})
 report.parity.push({id,properties:selectors.length*props.length,diffs})
 assert.equal(await page.locator('header .brand-lockup span').innerText(),envs.site.data.content[0].props.name)
 assert.deepEqual(await page.locator('header .desktop-nav a').allTextContents(),await page.locator('footer nav[aria-label="Explore Example Company"] a').allTextContents())
 assert.ok(await page.locator('footer .footer-contact>a:last-child').evaluate(e=>e.getBoundingClientRect().height>=24))
 await page.addScriptTag({path:fixture+'/node_modules/axe-core/axe.min.js'})
 report.axe[id]=await page.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}});return r.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)}))})
}
// Exact persisted raster bytes and geometry, in both desktop and mobile layouts.
report.graphic=[]
for(const width of [1200,375]) {
 await page.setViewportSize({width,height:900});await page.goto(site+'/')
 await editor.locator('.studio-pages button[data-page="home"]').click()
 await editor.evaluate(w=>document.querySelector(`[aria-label="Switch to ${w===375?'Mobile':'Desktop'} viewport"]`)?.click(),width);await editor.waitForTimeout(500)
 const hero=envs.home.data.content[0].props,graphic=hero.workflow?.[0]?.props.graphic||hero.graphic
 const built=await page.locator('figure.workflow-figure').evaluate(e=>({component:e.dataset.studioComponent,id:e.dataset.studioComponentId,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,radius:getComputedStyle(e).borderRadius,align:getComputedStyle(e).justifySelf,src:e.querySelector('img').getAttribute('src'),alt:e.querySelector('img').alt,caption:e.querySelector('figcaption strong').textContent}))
 const canvas=await editor.frameLocator('.studio-editor iframe').locator('figure.workflow-figure').evaluate(e=>({component:e.dataset.studioComponent,id:e.dataset.studioComponentId,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,radius:getComputedStyle(e).borderRadius,align:getComputedStyle(e).justifySelf,src:e.querySelector('img').getAttribute('src'),alt:e.querySelector('img').alt,caption:e.querySelector('figcaption strong').textContent}))
 assert.equal(built.src,graphic.asset.data);assert.equal(canvas.src,built.src);assert.equal(built.alt,graphic.alt);delete canvas.src;delete built.src;assert.deepEqual(canvas,built)
 report.graphic.push({viewport:width,component:built.component,width:built.width,height:built.height,alignment:built.align,identicalAsset:true,identicalGeometry:true,caption:built.caption})
 await page.screenshot({path:out+`/next-graphic-${width}.png`,fullPage:width===375})
}
// Real client navigation: the original Header component controls its own state and active links.
await page.goto(site+'/')
for(const route of Object.values(routes).slice(1)){
 await page.locator(`main a[href="${route}"]`).first().click();await page.waitForURL(site+route)
 const paper=await page.locator('header').evaluate(e=>getComputedStyle(e).getPropertyValue('--paper'))
 assert.equal(paper,'#f7f3ea');report.navigation.push({route,headerPaper:paper})
 await page.locator('header .brand-lockup').click();await page.waitForURL(site+'/')
}
for(const width of [375,768]){
 await page.setViewportSize({width,height:900});await page.goto(site+'/services/ai-implementation')
 await page.locator('.mobile-toggle').click();assert.equal(await page.locator('.mobile-toggle').getAttribute('aria-expanded'),'true')
 await page.keyboard.press('Escape');assert.equal(await page.locator('.mobile-toggle').getAttribute('aria-expanded'),'false')
 assert.ok(await page.locator('.mobile-toggle').evaluate(e=>e===document.activeElement))
 await page.locator('.mobile-toggle').click();await page.locator('#mobile-navigation a[href="/about"]').click();await page.waitForURL(site+'/about')
 assert.equal(await page.locator('.mobile-toggle').getAttribute('aria-expanded'),'false');assert.equal(await page.locator('#mobile-navigation a[href="/about"]').getAttribute('aria-current'),'page')
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);assert.ok(overflow<=0)
 report.responsive.push({width,escapeFocus:true,activeLink:true,overflow})
}
await fs.writeFile(`${out}/site-browser-proof.json`,JSON.stringify({...report,pageErrors:errors,modelCalls:0},null,2))
assert.deepEqual(errors,[])
assert.deepEqual(Object.values(report.axe).flat(),[])
assert.deepEqual(report.parity.flatMap(p=>p.diffs),[])
console.log(JSON.stringify({pages:4,properties:report.parity.reduce((n,p)=>n+p.properties,0),axeViolations:0,responsive:report.responsive}))
await browser.close()
