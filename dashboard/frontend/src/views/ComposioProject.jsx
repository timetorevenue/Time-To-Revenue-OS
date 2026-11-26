import { useState } from 'react'

// A workspace's Composio project: link an existing customer-owned project by its project API key,
// or connect apps through TTROS-managed Composio. Keys are write-only: verified and sealed server-side.
export default function ComposioProject({ state, busy, run, call, base, onConnect }) {
  const [apiKey, setApiKey] = useState(''), [userId, setUserId] = useState(''), [service, setService] = useState('')
  const project = state?.project
  async function link(event) {
    event.preventDefault()
    const key = apiKey.trim()
    if (!key) return
    setApiKey('') // never kept in page state after submission
    await run(() => call(base + '/project', { mode: 'customer', api_key: key, composio_user_id: userId.trim() }))
  }
  if (!project) return <section className="editorial-card connections-card" aria-label="Composio project">
    <h2>Your Composio project</h2>
    <p>Optional for technical and enterprise customers who already manage a Composio project. Ordinary app connections require no project or API key.</p>
    <div className="hub-grid">
      <form className="hub-form" aria-label="Link an existing Composio project" onSubmit={link}>
        <h3>Link an existing Composio project</h3>
        <p>Already use Composio? Paste a project API key. TTROS verifies it with Composio, stores it encrypted on the server and lets you select an existing account when you connect an app.</p>
        <label>Project API key<input type="password" autoComplete="off" spellCheck={false} required value={apiKey} onChange={e => setApiKey(e.target.value)} /></label>
        <label>Your Composio user ID in that project<input value={userId} onChange={e => setUserId(e.target.value)} placeholder="user ID you used when connecting accounts" /><small>Leave blank to start a new user for you in that project.</small></label>
        <button data-project-action="customer" disabled={busy}>Verify and link project</button>
      </form>

    </div>
  </section>
  return <section className="editorial-card connections-card" aria-label="Composio project">
    <h2>Your Composio project</h2>
    <p>{project.mode === 'customer' ? 'Your own Composio project' : 'TTROS-managed Composio'} · <strong>{project.status}</strong>{project.mode === 'customer' && <> · key …{project.key_fingerprint}</>}</p>
    <p>Your Composio user: <code>{project.composio_user_id}</code></p>
    {state.project_blocker && <p className="connections-feedback error" role="alert">{state.project_blocker}</p>}
    <h3>Your accounts in this project ({state.project_accounts?.length || 0})</h3>
    <div className="hub-grid">{(state.project_accounts || []).map(account => {
      return <article className="editorial-card connections-card" key={account.account} data-project-account={account.account}>
        <h4>{account.service}</h4><p>{account.identity} · <span className="connection-badge">{account.status}</span></p><code>{account.account}</code>
        <button disabled={busy || account.status !== 'ACTIVE'} onClick={() => onConnect(account.service)}>Choose account</button>
      </article>
    })}</div>
    <form className="connections-actions" aria-label="Connect a new app" onSubmit={event => { event.preventDefault(); onConnect(service.trim()) }}>
      <label>Connect a new app <input value={service} onChange={e => setService(e.target.value)} placeholder="gmail" maxLength={80} /></label>
      <button data-project-action="connect" disabled={busy || !service.trim()}>Open connection form</button>
    </form>
    {project.owner && <button data-project-action="unlink" disabled={busy} onClick={() => run(() => call(base + '/project', {}, 'DELETE'))}>Unlink project from TTROS</button>}
    <p className="source-caption">Unlinking removes TTROS’s access and deletes the stored key. Accounts and consent in Composio are not changed.</p>
  </section>
}
