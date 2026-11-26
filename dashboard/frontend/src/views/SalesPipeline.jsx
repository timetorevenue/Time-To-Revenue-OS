import { basisLabel, stageLabel } from '../commandCentreState'

export function PipelineSummary({ summary, onOpen }) {
  if (!summary) return null
  return <div className="cc-pipeline-summary">
    <p>{summary.groups.filter(group => group.count).map(group => `${stageLabel(group.stage)}: ${group.count}`).join(' · ') || 'No opportunities recorded.'}</p>
    <p>{summary.open_values.map(value => `${value.currency} ${value.amount.toLocaleString()} (${basisLabel(value.basis)})`).join(' · ') || 'No open opportunity values recorded.'}</p>
    <small>{summary.open_without_value} open without a value. {summary.note}</small>
    {summary.recent_outcomes.length > 0 && <div><h4>Won / lost in the last 7 days</h4>{summary.recent_outcomes.map(row => <button key={row.record_id} className="cc-attention" onClick={() => onOpen(row.record_id)}><span><strong>{row.title} · {stageLabel(row.stage)}</strong><small>{row.date} · {row.reason}</small></span></button>)}</div>}
  </div>
}

export default function SalesPipeline({ data, query = '', onOpen }) {
  return <>
    <PipelineSummary summary={data.pipeline_summary} onOpen={onOpen} />
    <div className="cc-pipeline" aria-label="Sales pipeline">
      {(data.pipeline_summary?.groups || []).map(group => <section className="cc-pipeline-column" key={group.stage}>
        <h3>{stageLabel(group.stage)} <span>{group.count}</span></h3>
        {group.record_ids.map(id => data.opportunities[id]).filter(record => `${record.name} ${record.id}`.toLowerCase().includes(query.toLowerCase())).map(record => {
          const action = record.next_action && !record.next_action.completed_at ? record.next_action : null
          const overdue = data.attention.some(item => item.record_id === record.id && item.rule === 'overdue_follow_up')
          return <button className={`cc-pipeline-card ${overdue ? 'overdue' : ''}`} key={record.id} onClick={() => onOpen(record.id)}>
            <strong>{record.name}</strong>
            <span>{record.owner === 'casey' ? 'Casey' : 'Owner not recorded'} · {stageLabel(record.stage)}</span>
            <span>{record.value ? `${record.value.currency} ${record.value.amount.toLocaleString()} · ${basisLabel(record.value.basis)}` : 'Value not recorded'}</span>
            <small>Last interaction: {record.last_interaction || 'Not recorded'}</small>
            <small>{action ? `${action.text} · due ${action.due}` : record.cadence_due ? `Ledger follow-up: ${record.cadence_due}` : 'No next action'}</small>
            {(overdue || record.stalled) && <small className="cc-attention-line">{overdue ? 'Overdue follow-up' : 'Stalled · 14+ days in stage'}</small>}
          </button>
        })}
        {!group.count && <p className="source-caption">No opportunities</p>}
      </section>)}
    </div>
  </>
}
