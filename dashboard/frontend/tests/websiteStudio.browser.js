// Run in the local /websites Studio tab via preview_evaluate. Replays the separately
// verified live David response; never spends a second model call during UI checks.
export async function studioAcceptance() {
 const pause=()=>new Promise(r=>setTimeout(r,250))
 const assert=(v,m)=>{if(!v)throw Error(m)}
 const button=t=>[...document.querySelectorAll('button')].find(b=>b.textContent===t)
 const doc=()=>document.querySelector('iframe').contentDocument
 const headline=()=>doc().querySelector('h1').innerText
 const results=[]
 const fetchOriginal=window.fetch
 window.fetch=(url,opts)=>String(url).endsWith('/api/website/studio/propose')?Promise.resolve(new Response(JSON.stringify({proposal:{summary:'Live proposal replay for UI acceptance',edits:[{section:'Section0',field:'text0',text:'Grow with clarity. '}]},usage:{cumulative_tokens:9674}}),{status:200})):fetchOriginal(url,opts)
 try {
  button('David').click();await pause()
  const field=document.querySelector('#studio-request')
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Improve the headline')
  field.dispatchEvent(new Event('input',{bubbles:true}));await pause()
  const before=headline();button('Propose changes').click();await pause()
  assert(!!document.querySelector('.studio-proposal'),'proposal visible')
  assert(headline()===before,'proposal has not changed draft')
  button('Reject').click();await pause();assert(headline()===before,'reject leaves draft untouched');results.push('proposal preview and reject')
  button('Propose changes').click();await pause();button('Accept changes').click();await pause()
  assert(headline().startsWith('Grow with clarity.'),'accept updates text')
  assert(!document.querySelector('[aria-label=undo]').disabled,'accept is undoable')
  document.querySelector('[aria-label=undo]').click();await pause();assert(headline()===before,'undo restores draft');results.push('accept and undo')
  document.querySelector('[aria-label="Switch to Mobile viewport"]').click();await pause()
  assert(doc().defaultView.innerWidth===375,'375px mobile viewport');assert(doc().documentElement.scrollWidth===375,'no mobile content overflow');results.push('mobile preview')
  document.querySelector('[aria-label="Switch to Desktop viewport"]').click();await pause()
  assert(doc().defaultView.innerWidth===1200,'desktop viewport');results.push('desktop preview')
  button('Propose changes').click();await pause()
  const sectionDetails=document.querySelector('.studio-sections');sectionDetails.open=true
  document.querySelector('[aria-label="Move Section1 up"]').click();await pause()
  assert(button('Accept changes').disabled,'stale proposal cannot overwrite newer draft');results.push('stale proposal blocked')
  button('Reject').click();document.querySelector('[aria-label=undo]').click();await pause()
  const source=document.querySelector('[data-section=Section2]'),target=document.querySelector('[data-section=Section1]')
  const transfer=new DataTransfer();source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));target.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer}));await pause()
  button('Save draft').click();await pause()
  const draft=JSON.parse(localStorage.getItem('ttros-studio:global:default-site:home:v1'))
  assert(draft.data.content[1].type==='Section2','drag reorders draft');results.push('section drag and save')
  document.querySelector('[aria-label=undo]').click();await pause()
  assert([...document.querySelectorAll('.studio-sections li')][1].dataset.section==='Section1','undo restores order');results.push('reorder undo')
  const text=doc().querySelector('h1 [contenteditable]'),style=doc().defaultView.getComputedStyle(text).fontSize
  text.innerText='A clearer headline. ';text.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));await pause()
  button('Save draft').click();await pause()
  const edited=JSON.parse(localStorage.getItem('ttros-studio:global:default-site:home:v1'))
  assert(edited.data.content[0].props.text0==='A clearer headline. ','inline text persists')
  assert(edited.data.content[0].props.text1==='Willow grove ember','second line retained')
  assert(doc().defaultView.getComputedStyle(text).fontSize===style,'destination font preserved');results.push('inline replacement preserves formatting and saves')
  document.querySelector('[aria-label=undo]').click();await pause();button('Save draft').click()
  return {passed:results,modelCalls:0,proposalSource:'replay of live-david.json',viewport:doc().defaultView.innerWidth}
 } finally {window.fetch=fetchOriginal}
}
