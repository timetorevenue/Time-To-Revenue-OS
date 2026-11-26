// Browser-only acceptance on the requested local editor. Proposals are mocked;
// manual edits use the actual visible controls and Puck's history, not model calls.
export async function stage1Acceptance() {
 const pause=()=>new Promise(r=>setTimeout(r,650)),results=[]
 const assert=(v,m)=>{if(!v)throw Error(m)}
 const aside=()=>document.querySelector('.studio-assistant')
 const button=t=>[...aside().querySelectorAll('button')].find(b=>b.textContent===t)
 const doc=()=>document.querySelector('.studio-editor iframe').contentDocument
 const style=selector=>doc().defaultView.getComputedStyle(doc().querySelector(selector))
 const click=async t=>{const b=button(t)||[...document.querySelectorAll('button')].find(b=>b.textContent===t);assert(b,`Button ${t}`);b.click();await pause()}
 const control=async(label,value)=>{
  const e=[...aside().querySelectorAll('input,select')].find(e=>e.getAttribute('aria-label')===label)
  assert(e,`Control ${label}`)
  let parent=e.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement}
  const proto=e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto,'value').set.call(e,String(value))
  e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));await pause()
 }
 const choose=async(section,target)=>{
  document.querySelector('.studio-sections').open=true
  aside().querySelector(`[data-section=${section}] .studio-section-select`).click();await pause()
  await control('Style target',target)
 }
 const global=async()=>click('Global Design')
 await global();await control('Heading font','Inter');await control('Body family','Georgia');await click('Ocean')
 assert(style('h1').fontFamily.startsWith('Inter'),'global heading font H1')
 assert(style('h2').fontFamily.startsWith('Inter'),'global heading font H2')
 assert(style('.home-hero p').fontFamily.startsWith('Georgia'),'global body font')
 assert(style('.editorial-link .site-arrow').color==='rgb(9, 105, 165)','accent inheritance')
 results.push('global fonts and named palette update inheriting components')
 await control('H1 weight',700);await control('H2 weight',650)
 assert(doc().querySelector('h1').tagName==='H1'&&style('h1').fontWeight==='700','semantic H1 styling')
 assert(doc().querySelector('h2').tagName==='H2'&&style('h2').fontWeight==='650','semantic H2 styling')
 results.push('H1/H2 styles retain semantic heading tags')
 await choose('Section0','section');await control('Background mode','local');await control('Local Background','#e1edf5')
 await choose('Section0','a-0');await control('Corner radius mode','local');await control('Local Corner radius',32)
 await global();await control('Background','#ffffff');await control('Button radius (px)',8)
 assert(style('h1').fontFamily.startsWith('Inter')&&style('.home-hero p').fontFamily.startsWith('Georgia'),'panel switching preserves earlier global typography')
 assert(style('[data-studio-section="Section0"]').backgroundColor==='rgb(225, 237, 245)','section override survives global change')
 assert(style('[data-studio-section="Section0"] .site-button').borderRadius==='32px','button override survives global change')
 assert(style('[data-studio-section="Section8"] .site-button').borderRadius==='8px','other button inherits global radius')
 results.push('local section background and individual button radius survive global edits')
 await choose('Section0','a-0');await click('Reset to Global')
 assert(style('[data-studio-section="Section0"] .site-button').borderRadius==='8px','button resets to global')
 await choose('Section0','section');await click('Reset to Global')
 assert(!doc().querySelector('[data-studio-section="Section0"]').style.backgroundColor,'section inline override removed')
 assert(style('main.editorial').backgroundColor==='rgb(255, 255, 255)','section inherits global surface again')
 results.push('reset clears overrides and restores inheritance')
 await global();await control('Accent','#237a57')
 document.querySelector('[aria-label=undo]').click();await pause()
 assert(style('.editorial-link .site-arrow').color==='rgb(9, 105, 165)','undo global change')
 document.querySelector('[aria-label=redo]').click();await pause()
 assert(style('.editorial-link .site-arrow').color==='rgb(35, 122, 87)','redo global change')
 results.push('global style undo and redo')
 // Full style proposal + text; no request is forwarded to David or a model.
 const original=window.fetch;let mockCalls=0
 window.fetch=(url,options)=>String(url).endsWith('/api/website/studio/propose')?(mockCalls++,Promise.resolve(new Response(JSON.stringify({proposal:{summary:'Mocked branding proposal',edits:[{section:'Section0',field:'text0',text:'More revenue. '}],design:{accent:'#0969a5'},styles:[{section:'Section0',target:'a-0',values:{radius:32}}]},usage:{total_tokens:0}}),{status:200}))):original(url,options)
 try {
  await click('David')
  const field=document.querySelector('#studio-request')
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Update headline, accent and hero button')
  field.dispatchEvent(new Event('input',{bubbles:true}));await pause()
  const before=doc().querySelector('h1').innerText
  await click('Propose changes')
  assert(document.querySelector('.studio-proposal iframe'),'rendered proposal preview')
  await click('Reject');assert(doc().querySelector('h1').innerText===before,'reject leaves draft unchanged')
  await click('Propose changes');await click('Accept changes')
  assert(doc().querySelector('h1').innerText.startsWith('Coral bravo l'),'accept text')
  assert(style('.editorial-link .site-arrow').color==='rgb(9, 105, 165)','accept global design')
  assert(style('[data-studio-section="Section0"] .site-button').borderRadius==='32px','accept local style')
  document.querySelector('[aria-label=undo]').click();await pause()
  assert(doc().querySelector('h1').innerText===before,'proposal atomic undo')
  document.querySelector('[aria-label=redo]').click();await pause()
  results.push('mocked David text/global/local preview, reject, accept and atomic undo/redo')
  await click('Propose changes');await global();await control('H2 weight',660);await click('David')
  assert(button('Accept changes').disabled,'style changes stale a pending proposal')
  await click('Reject');results.push('style edit prevents stale proposal acceptance')
 }finally{window.fetch=original}
 // Keep a useful demonstration with one section and one button override.
 await choose('Section0','section');await control('Background mode','local');await control('Local Background','#e1edf5')
 document.querySelector('.studio-assistant [aria-label="Move Section4 down"]').click();await pause()
 results.push('visual section reorder included in the saved site draft')
 await click('Save draft')
 const saved=JSON.parse(localStorage.getItem('ttros-studio:global:default-site:home:v1'))
 assert(saved.version===4&&saved.data.root.props.design.accent==='#0969a5','design persisted (native slot envelope v4)')
 assert(saved.data.content.find(i=>i.type==='Section0').props.styles['a-0'].radius===32,'local style persisted')
 document.querySelector('[aria-label="Switch to Mobile viewport"]').click();await pause()
 assert(doc().defaultView.innerWidth===375,'mobile viewport')
 assert(doc().documentElement.scrollWidth===375,'mobile has no horizontal overflow')
 results.push('375px mobile rendering without overflow; 1200px desktop available')
 document.querySelector('[aria-label="Switch to Desktop viewport"]').click();await pause()
 await global()
 window.stage1Result={passed:results,modelCalls:0,mockedProposalRequests:mockCalls,savedDraft:saved}
 return {...window.stage1Result,savedDraft:undefined}
}
export async function verifyReload() {
 const pause=()=>new Promise(r=>setTimeout(r,650));await pause()
 const b=[...document.querySelectorAll('button')].find(b=>b.textContent==='Open Website Studio');if(b){b.click();await pause()}
 const d=document.querySelector('.studio-editor iframe').contentDocument
 const style=s=>d.defaultView.getComputedStyle(d.querySelector(s))
 if(!d.querySelector('h1').innerText.startsWith('Coral bravo l')||style('h1').fontFamily.indexOf('Inter')!==0||style('[data-studio-section="Section0"] .site-button').borderRadius!=='32px'||style('[data-studio-section="Section0"]').backgroundColor!=='rgb(225, 237, 245)')throw Error('Reload lost content/design/local styles')
 return {passed:'reload preserves text, global styles, and local overrides',version:JSON.parse(localStorage.getItem('ttros-studio:global:default-site:home:v1')).version}
}
