import {useEffect,useState} from 'react'
const url='/api/website/studio/launch'
export default function ProductLaunch({openStudio}) {
 const [offer,setOffer]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const [request,setRequest]=useState('Create a TEST ONLY workflow audit offer, prepare a landing page and nurture/social drafts, and set up test Stripe checkout')
 async function call(path='',body) { const res=await fetch(url+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await res.json();if(!res.ok)throw Error(data.detail||'Launch failed');return data }
 useEffect(()=>{call().then(x=>setOffer(x.offer)).catch(e=>setError(e.message))},[])
 async function create(e){e.preventDefault();setBusy(true);setError('');try{const x=await call('',{request});setOffer(x.offer)}catch(e){setError(e.message)}finally{setBusy(false)}}
 return <section className="cc-panel" aria-label="David product launch" style={{padding:24,margin:'16px 0'}}><h2>David · Product launch</h2><p>Prepare a test offer with a local deliverable, Studio landing page, CRM and campaign drafts. Review before release.</p>
 <form onSubmit={create}><label htmlFor="launch-request">Tell David about the test offer</label><textarea id="launch-request" value={request} onChange={e=>setRequest(e.target.value)} rows={3} style={{display:'block',width:'100%',padding:12}}/><button disabled={busy}>{busy?'Preparing…':'Prepare test launch'}</button></form>
 {error&&<p role="alert">{error}</p>}{offer&&<div data-testid="launch-package"><h3>{offer.name} · Draft review</h3><p>Offer {offer.id} · Job {offer.job_id} · {offer.amount/100} {offer.currency.toUpperCase()} suggested</p><p>{offer.price_basis}</p><p role="status">{offer.stripe.provider_verified?'Stripe TEST provider verified':`Stripe blocked: ${offer.stripe.blocker}`}</p>{offer.stripe.url&&<a href={offer.stripe.url}>Hosted test checkout</a>}
 <button onClick={async()=>{setError('');try{setOffer((await call('/checkout',{})).offer)}catch(e){setError(e.message)}}}>Reconcile Stripe test checkout</button>
 <button onClick={()=>{localStorage.setItem('ttros-studio:page:default-site','test-offer');openStudio()}}>Edit landing page in Website Studio</button><p>{offer.studio?.route} · Source {offer.studio?.source} · Saved v{offer.studio?.version}</p>
 <details open><summary>Product specification and deliverable draft</summary><p>{offer.specification}</p><pre style={{whiteSpace:'pre-wrap'}}>{offer.deliverable}</pre></details>
 <details><summary>Email nurture drafts (consent required)</summary>{offer.marketing.email.map(x=><article key={x.subject}><h4>{x.subject}</h4><p>{x.body}</p></article>)}</details><details><summary>LinkedIn and Instagram campaign drafts</summary><h4>LinkedIn</h4><p>{offer.marketing.linkedin}</p><h4>Instagram</h4><p>{offer.marketing.instagram}</p></details>
 <p>{offer.leads.length} CRM leads · {offer.payments.length} payment event receipts</p>{offer.payments.map(x=><p key={x.event}>{x.event}: {x.status} · Fulfillment {x.fulfillment_job||'not queued'}</p>)}<p>Publication blocked · Email and social sending blocked · No model calls</p></div>}</section>
}
