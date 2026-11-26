import { useEffect, useRef, useState } from 'react'
import { Field, ActionNotice, useCrmAction } from './crmForms'
import { newRequestKey } from '../commandCentreState'

export function CreateOpportunity({ data, onCreated }) {
  const id = useRef(`opp:${newRequestKey('sales')}`)
  const [form, setForm] = useState({ name: '', relationship: '', owner: 'casey', source: 'casey:dashboard-entry' })
  const [state, submit] = useCrmAction({ id: id.current }, onCreated)
  const set = field => event => setForm(old => ({ ...old, [field]: event.target.value }))
  return <form className="cc-form" onSubmit={async event => {
    event.preventDefault()
    await submit('opportunity.create', { name: form.name, workflow: 'sales', owner: form.owner,
      ...(form.relationship.startsWith('contact:') ? { contact: form.relationship } : { organization: form.relationship }) },
    'confirmed', [form.source], 'Opportunity created.')
  }}>
    <h4>Create opportunity</h4>
    <div className="cc-form-row">
      <Field label="Opportunity name"><input value={form.name} onChange={set('name')} required maxLength={200} /></Field>
      <Field label="Existing contact or organisation"><select value={form.relationship} onChange={set('relationship')} required>
        <option value="">Choose an existing relationship…</option>
        {Object.values(data.contacts).map(record => <option key={record.id} value={record.id}>{record.name} (contact)</option>)}
        {Object.values(data.organizations).map(record => <option key={record.id} value={record.id}>{record.name} (organisation)</option>)}
      </select></Field>
      <Field label="Owner"><select value={form.owner} onChange={set('owner')}><option value="casey">Casey</option></select></Field>
      <Field label="Source"><input value={form.source} onChange={set('source')} required /></Field>
    </div>
    <p className="source-caption">Starts in Discovery. Uses the existing relationship; creates no duplicate contact.</p>
    <button className="cc-button" disabled={state.busy}>Create opportunity</button><ActionNotice state={state} />
  </form>
}

export function OpportunityEditor({ record, onSaved }) {
  const [form, setForm] = useState({ name: record.name, owner: record.owner || 'casey' })
  useEffect(() => { setForm({ name: record.name, owner: record.owner || 'casey' }) }, [record.name, record.owner])
  const [state, submit] = useCrmAction(record, onSaved)
  return <form className="cc-form" onSubmit={event => { event.preventDefault(); submit('opportunity.update', form, 'confirmed', [], 'Opportunity saved.') }}>
    <h4>Opportunity details</h4>
    <Field label="Opportunity name"><input required maxLength={200} value={form.name} onChange={e => setForm(v => ({ ...v, name: e.target.value }))} /></Field>
    <Field label="Opportunity owner"><select value={form.owner} onChange={e => setForm(v => ({ ...v, owner: e.target.value }))}><option value="casey">Casey</option></select></Field>
    <button className="cc-button" disabled={state.busy}>Save opportunity</button><ActionNotice state={state} />
  </form>
}

export function OpportunityValue({ record, onSaved }) {
  const [form, setForm] = useState({ amount: record.value?.amount ?? '', currency: record.value?.currency || 'CAD',
    basis: record.value?.basis || 'estimate', source: record.value?.sources?.[0] || 'casey:dashboard-entry', note: record.value?.note || '' })
  useEffect(() => { setForm(old => ({ ...old, amount: record.value?.amount ?? '', currency: record.value?.currency || 'CAD', basis: record.value?.basis || 'estimate', note: record.value?.note || '' })) }, [record.value?.event_id])
  const [state, submit] = useCrmAction(record, onSaved)
  const set = field => event => setForm(old => ({ ...old, [field]: event.target.value }))
  return <form className="cc-form" onSubmit={event => { event.preventDefault(); submit('value.set', { amount: form.amount, currency: form.currency, note: form.note }, form.basis, [form.source], 'Value saved.') }}>
    <h4>Recorded opportunity value</h4>
    <div className="cc-form-row">
      <Field label="Amount"><input type="number" min="0" max="1000000000000" step="0.01" required value={form.amount} onChange={set('amount')} /></Field>
      <Field label="Currency"><input required pattern="[A-Za-z]{3}" maxLength={3} value={form.currency} onChange={set('currency')} /></Field>
      <Field label="Value basis"><select value={form.basis} onChange={set('basis')}><option value="estimate">Estimate</option><option value="confirmed">Confirmed quote / agreement</option></select></Field>
    </div>
    <Field label="Value source"><input required value={form.source} onChange={set('source')} /></Field>
    <Field label="Value note"><input maxLength={200} value={form.note} onChange={set('note')} /></Field>
    <p className="source-caption">An opportunity value is not booked or guaranteed revenue. Leave it unrecorded when unknown.</p>
    <button className="cc-button" disabled={state.busy}>Save value</button><ActionNotice state={state} />
  </form>
}

export function DeliveryHandoff({ record, onSaved, renderSource, onNavigate }) {
  const [form, setForm] = useState({ confirmed_scope: record.handoff?.confirmed_scope || '', next_delivery_action: record.handoff?.next_delivery_action || '',
    owner: 'casey', source: record.handoff?.sources?.[0] || 'casey:dashboard-entry' })
  const [state, submit] = useCrmAction(record, onSaved)
  const set = field => event => setForm(old => ({ ...old, [field]: event.target.value }))
  if (record.stage !== 'won') return null
  const handoff = record.handoff
  return <section className="cc-handoff">
    <h3>Sales-to-delivery handoff</h3>
    {handoff && <div className="cc-form" data-testid="delivery-handoff">
      <h4>Ready for internal review · {handoff.at}</h4>
      <p>Client: <button className="text-link" onClick={() => onNavigate('crm', { record: handoff.client.id })}>{handoff.client.name}</button></p>
      <p><strong>Confirmed scope:</strong> {handoff.confirmed_scope}</p>
      <p><strong>Next delivery action:</strong> {handoff.next_delivery_action}</p>
      <p>Owner: Casey · Opportunity: {handoff.opportunity_name}</p>
      <div className="cc-sources">{handoff.sources.map(ref => renderSource(ref))}</div>
    </div>}
    <form className="cc-form" onSubmit={event => { event.preventDefault(); submit('handoff.prepare', { confirmed_scope: form.confirmed_scope, next_delivery_action: form.next_delivery_action, owner: form.owner }, 'confirmed', [form.source], 'Handoff saved for review.') }}>
      <h4>{handoff ? 'Revise handoff' : 'Prepare handoff'}</h4>
      <Field label="Confirmed scope"><textarea required maxLength={2000} rows={3} value={form.confirmed_scope} onChange={set('confirmed_scope')} /></Field>
      <Field label="Next delivery action"><textarea required maxLength={2000} rows={2} value={form.next_delivery_action} onChange={set('next_delivery_action')} /></Field>
      <Field label="Scope evidence"><input required value={form.source} onChange={set('source')} /></Field>
      <p className="source-caption">Internal review only. No project, contract, invoice, message or client commitment is created.</p>
      <button className="cc-button" disabled={state.busy}>Save handoff for review</button><ActionNotice state={state} />
    </form>
  </section>
}
