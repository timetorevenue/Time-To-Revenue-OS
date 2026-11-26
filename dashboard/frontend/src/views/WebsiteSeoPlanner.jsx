// Evidence-first SEO comparison strategy. Pure planning: no models, live search or publishing.
// Prepared page work is executed by the existing website executor only after readiness checks.
import { useState } from 'react'
import { planWebsiteSeo } from '../websitePreparationApi'

const today = () => new Date().toISOString().slice(0, 10)
const initial = {
  business: '', category: '', audience: '', competitors: '', buyer_problem: '',
  offer_difference: '', author: '', cta_label: 'Delta velvet yonder c',
  cta_url: '', comparison_method: '', bias_disclosure: '',
  claim: '', source_url: '', checked_on: today(), target_path: '',
}

export default function WebsiteSeoPlanner({ project, onSelect }) {
  const [step, setStep] = useState(0)
  const [fields, setFields] = useState(initial)
  const [pages, setPages] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const change = key => event => { setFields(old => ({ ...old, [key]: event.target.value })); if (key !== 'target_path') setPages([]) }
  const generate = async () => {
    setBusy(true); setError(''); setPages([])
    try {
      const competitors = fields.competitors.split('\n').map(x => x.trim()).filter(Boolean).map(name => ({ name }))
      const research = {
        business: fields.business, category: fields.category, audience: fields.audience,
        buyer_problem: fields.buyer_problem, offer_difference: fields.offer_difference,
        author: fields.author, cta_label: fields.cta_label, cta_url: fields.cta_url,
        comparison_method: fields.comparison_method, bias_disclosure: fields.bias_disclosure,
        competitors, client_scope: project?.client_scope || '',
        claims: fields.claim || fields.source_url ? [
          { claim: fields.claim, source_url: fields.source_url, checked_on: fields.checked_on },
        ] : [],
      }
      const result = await planWebsiteSeo(research)
      setPages(result.pages.map(page => ({ ...page, research })))
      setStep(3)
    } catch (e) { setError(e?.response?.data?.detail || e?.message || 'SEO planning unavailable') }
    finally { setBusy(false) }
  }
  return <section className="editorial-card cc-form seo-planner">
    <div className="card-heading"><div><h2>SEO Strategy</h2><p>Turn a buyer problem into a sourced comparison and a reviewable work order.</p></div></div>
    <ol className="seo-progress" aria-label="SEO planning steps">{['Strategy & audience', 'Competitors', 'Evidence', 'Opportunities', 'Work order'].map((label, i) => <li key={label}><button disabled={i === 4 || (i === 3 && !pages.length)} aria-current={step === i ? 'step' : undefined} onClick={() => setStep(i)}><span>{i + 1}</span>{label}</button></li>)}</ol>
    {!project && <div className="website-setup"><h3>Start your SEO plan here</h3><p>Choose a registered website above to connect client scope. You can explore the planning steps now; generating briefs and preparing work need a selected website.</p></div>}
    <p className="source-caption">Research briefs use no model or live search. Search demand stays unverified until supported by evidence; publication remains held.</p>
    <div hidden={step !== 0}><h3>1. Strategy & audience</h3><p>Define who is comparing options and the problem your offer solves.</p><div className="website-field-grid">
    <label className="cc-field"><span>Business name</span><input disabled={busy} value={fields.business} onChange={change('business')} placeholder="Glade nectar ne" /></label>
    <label className="cc-field"><span>Offer category</span><input disabled={busy} value={fields.category} onChange={change('category')} placeholder="Business process automation consulting" /></label>
    <label className="cc-field"><span>Ideal customer / use case</span><input disabled={busy} value={fields.audience} onChange={change('audience')} placeholder="BC service businesses" /></label>
    <label className="cc-field"><span>Verified buyer problem</span><textarea disabled={busy} rows={2} value={fields.buyer_problem} onChange={change('buyer_problem')} /></label>
    <label className="cc-field"><span>Defensible reason to choose us</span><textarea disabled={busy} rows={2} value={fields.offer_difference} onChange={change('offer_difference')} /></label>
    </div><button className="cc-button" onClick={() => setStep(1)}>Continue to competitors</button></div>
    <div hidden={step !== 1}><h3>2. Competitors</h3><p>Add up to ten relevant companies your buyers actually consider. Claims will need dated sources.</p>
    <label className="cc-field"><span>Competitors — one real company per line</span><textarea disabled={busy} rows={3} value={fields.competitors} onChange={change('competitors')} /></label>
    <button className="cc-button" onClick={() => setStep(2)}>Continue to evidence</button></div>
    <div hidden={step !== 2}><h3>3. Evidence</h3><p>Add a specific finding with its source. You can generate research briefs with gaps and return to complete them.</p><div className="website-field-grid">
    <label className="cc-field"><span>Comparison research method</span><input disabled={busy} value={fields.comparison_method} onChange={change('comparison_method')} placeholder="Official websites checked; hands-on trial where applicable" /></label>
    <label className="cc-field"><span>Factual comparison finding</span><input disabled={busy} value={fields.claim} onChange={change('claim')} placeholder="Specific claim; no unsupported price or performance" /></label>
    <label className="cc-field"><span>Finding source URL</span><input disabled={busy} value={fields.source_url} onChange={change('source_url')} placeholder="https://..." /></label>
    <label className="cc-field"><span>Source checked on</span><input disabled={busy} type="date" value={fields.checked_on} onChange={change('checked_on')} /></label>
    </div><details className="website-advanced"><summary>Author, disclosure and call to action</summary><p>Required before an SEO work order can pass evidence validation.</p><div className="website-field-grid">
    <label className="cc-field"><span>Real author/byline</span><input disabled={busy} value={fields.author} onChange={change('author')} /></label>
    <label className="cc-field"><span>Competitor-interest disclosure</span><textarea disabled={busy} rows={2} value={fields.bias_disclosure} onChange={change('bias_disclosure')} placeholder="We offer a competing service; judge our claims accordingly." /></label>
    <label className="cc-field"><span>Call to action label</span><input disabled={busy} value={fields.cta_label} onChange={change('cta_label')} /></label>
    <label className="cc-field"><span>Call to action URL</span><input disabled={busy} value={fields.cta_url} onChange={change('cta_url')} /></label>
    </div></details>
    <div className="cc-actions-bar"><button className="cc-button secondary" disabled={busy || !project} onClick={generate}>{busy ? 'Generating briefs…' : 'Generate SEO page briefs'}</button></div>
    {error && <p className="data-notice warning" role="alert">{error}</p>}
    </div>
    {!!pages.length && <div hidden={step !== 3} className="seo-opportunities">
      <h3>SEO opportunities ({pages.length})</h3><p className="source-caption">Generated keyword opportunities are hypotheses, not verified search demand. Start with a well-evidenced alternatives page.</p>
      <div className="website-target">    <label className="cc-field"><span>Exact repository target path (must be registered as allowed)</span><input disabled={busy} value={fields.target_path} onChange={change('target_path')} placeholder="app/comparisons/example/page.tsx" /></label>
<p className="source-caption">Use an exact registered edit path. A new comparison URL is not automatically approved; the work-order validator checks this path.</p></div>
      <div className="seo-opportunity-grid">{pages.map(page => <article className="seo-opportunity" key={`${page.kind}-${page.slug}`}>
        <div className="seo-opportunity-meta"><span>Priority {page.priority} · {page.kind}</span><strong>{page.readiness === 'editorial_review' ? 'Evidence supplied · editorial review needed' : 'Research needed'}</strong></div>
        <h4>{page.title}</h4><p><strong>Search intent:</strong> {page.keyword || page.title}</p>
        {page.research_gaps.length > 0 ? <><p><strong>Evidence to collect</strong></p><ul>{page.research_gaps.map(gap => <li key={gap}>{gap}</li>)}</ul><button className="text-link" onClick={() => setStep(2)}>Complete evidence</button></> : <p>Next: review editorial accuracy and validate the target path.</p>}
        <p className="source-caption">{page.research_gaps.length ? 'Carry this research brief into a draft. Missing evidence will still block preparation.' : 'Evidence readiness does not grant permission to edit or publish.'}</p>
        <button className="cc-button secondary" disabled={!project || !fields.target_path.trim()} onClick={() => onSelect(page, fields.target_path.trim())}>Use in website work order</button>
        {!fields.target_path.trim() && <p className="source-caption">Enter a target path above to continue.</p>}
      </article>)}</div>
    </div>}
  </section>
}
