import { useEffect, useState } from 'react'

function AccountAccess({ row, agents, selected, busy, save }) {
  const [mode, setMode] = useState(row.access?.mode || 'Automatic')
  const [rules, setRules] = useState(row.access?.agents || {})
  const [addRef, setAddRef] = useState('')
  useEffect(() => {
    setMode(row.access?.mode || 'Automatic')
    setRules(row.access?.agents || {})
  }, [row.access])
  const declared = selected?.grants.filter(grant => grant === `connector.read:${row.service}` || grant.startsWith(`connector.read:${row.service}:`)) || []
  const usable = selected?.connections.resolved || {}
  const reason = !row.enabled ? 'This binding is disconnected.' : row.access?.mode === 'Disabled' ? 'Account access is disabled.' : row.status !== 'ACTIVE' && !row.config?.no_auth ? 'Provider status is not ACTIVE.' : null
  const assign = (ref, operation, checked) => setRules(previous => {
    const next = { ...previous }, ops = new Set(next[ref] || [])
    if (checked) ops.add(operation); else ops.delete(operation)
    if (ops.size) next[ref] = [...ops].sort(); else delete next[ref]
    return next
  })
  return <article className="editorial-card connections-card" data-agent-account={row.id}>
    <h4>{row.name} · {row.service}</h4>
    <p>Status: {row.enabled ? row.status : 'DISCONNECTED'} · Supported operations: {row.operations.join(', ') || 'None'}</p>
    {reason && <p role="status">Unavailable: {reason}</p>}
    <p>Selected version declares: {declared.join(', ') || 'No capability for this app'}</p>
    {!!declared.length && <ul>{declared.map(grant => <li key={grant}>{grant}: {usable[grant]?.some(match => match.connection === row.id) ? 'Discoverable' : selected.connections.missing_reasons?.[grant] || 'Unavailable for this agent version on this account'}</li>)}</ul>}
    <label>Access mode <select aria-label={`Access mode · ${row.name}`} value={mode} onChange={event => setMode(event.target.value)} disabled={busy || !row.enabled}>
      <option>Automatic</option><option>Restricted</option><option>Disabled</option>
    </select></label>
    {mode === 'Restricted' && <fieldset><legend>Allowed agent versions and operations</legend>
      <p>Select exact installed versions. Only checked reads can be used by those versions.</p>
      <label>Add agent version <select aria-label={`Add agent version · ${row.name}`} value={addRef} onChange={event => { setAddRef(event.target.value); if (event.target.value) setRules(previous => ({ ...previous, [event.target.value]: previous[event.target.value] || [] })) }}>
        <option value="">Choose an agent version</option>{agents.filter(agent => !(agent.ref in rules)).map(agent => <option key={agent.ref} value={agent.ref}>{agent.ref}</option>)}
      </select></label>
      {Object.keys(rules).map(ref => <div key={ref} className="connections-restriction"><strong>{ref}</strong>
        {row.operations.map(operation => <label key={operation}><input type="checkbox" aria-label={`${ref} · ${operation} · ${row.name}`} checked={(rules[ref] || []).includes(operation)} onChange={event => assign(ref, operation, event.target.checked)} /> {operation}</label>)}
        <button type="button" onClick={() => setRules(previous => { const next = { ...previous }; delete next[ref]; return next })}>Remove {ref}</button>
      </div>)}
      {!Object.values(rules).some(ops => ops.length) && <p role="status">Select an agent version and at least one operation to save Restricted access.</p>}
    </fieldset>}
    <button type="button" disabled={busy || !row.enabled || (mode === 'Restricted' && !Object.values(rules).some(ops => ops.length))}
      onClick={() => save(row.id, mode, mode === 'Restricted' ? Object.fromEntries(Object.entries(rules).filter(([, ops]) => ops.length)) : {})}>Save access</button>
  </article>
}

export default function AgentConnections({ state, agents, busy, save }) {
  const [ref, setRef] = useState('')
  const selected = agents.find(agent => agent.ref === ref) || agents[0]
  return <section aria-label="Agent Forge Connections and Permissions">
    <h2>Agent Forge · Connections & Permissions</h2>
    <p>Choose a version to inspect its declared capabilities and missing requirements. Account access updates take effect without rebuilding the agent. Manifest capability changes still require Factory validation.</p>
    <label>Selected agent version <select aria-label="Selected agent version" value={selected?.ref || ''} onChange={event => setRef(event.target.value)}>
      {!agents.length && <option value="">No validated installed agents</option>}
      {agents.map(agent => <option key={agent.ref} value={agent.ref}>{agent.ref}</option>)}
    </select></label>
    {selected && <><p>Declared capabilities: {selected.grants.join(', ') || 'None'}</p>
      {selected.connections.missing_required.length > 0 && <div role="status"><h3>Missing requirements</h3><ul>{selected.connections.missing_required.map(grant => <li key={grant}>{grant}: {selected.connections.missing_reasons?.[grant]}</li>)}</ul></div>}</>}
    <div className="hub-grid">{state?.connections?.map(row => <AccountAccess key={row.id} row={row} agents={agents} selected={selected} busy={busy} save={save} />)}</div>
    {!state?.connections?.length && <p>Connect an account to set agent access.</p>}
    <p className="source-caption">Discovery is advisory. Execution also requires authenticated membership, the package grant, exact task permission, live account status and runtime policy.</p>
  </section>
}
