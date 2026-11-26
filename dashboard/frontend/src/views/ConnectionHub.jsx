import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Link2, Search, X } from 'lucide-react'
import { connectionRequest as call, hasConnectionReturn, verifyConnectionReturn } from '../connectionsApi'
import ComposioProject from './ComposioProject.jsx'
import AgentConnections from './AgentConnections.jsx'

function AppLogo({ slug, src, name }) {
  const [failed, setFailed] = useState(false)
  return <span className="app-icon" aria-hidden="true">{failed ? (name || slug).slice(0, 1).toUpperCase() :
    <img className="app-logo" src={src || `https://logos.composio.dev/api/${encodeURIComponent(slug)}`} alt="" loading="lazy" onError={() => setFailed(true)} />}</span>
}

const base = '/api/account-connections/hub'
const initial = { name: '', service: '', host: '', auth: 'none', secret_ref: '', custom_required: false }
const sample = JSON.stringify([{ name: 'status', path: '/', method: 'GET', input_schema: { type: 'object', properties: {}, additionalProperties: false }, response_schema: { type: 'object' } }], null, 2)
const PENDING = ['PENDING', 'INITIATED', 'INITIALIZING']
const SCHEME_LABEL = { OAUTH2: 'Sign in with the app', OAUTH1: 'Sign in with the app', API_KEY: 'API key', BEARER_TOKEN: 'Access token', BASIC: 'Username and password' }
const rowStatus = row => row.connection_status || row.status

export function statusLabel(row) {
  const status = rowStatus(row)
  if (PENDING.includes(status)) return 'Awaiting approval'
  return {
    ACTIVE: 'Connected', EXPIRED: 'Expired', INVALID_CREDENTIALS: 'Invalid credentials',
    SCOPE_MISMATCH: 'Wrong credential scope', UNVERIFIED: 'Unverified', PROJECT_CHANGED: 'Project changed',
    DISCONNECTED: 'Disconnected', DISABLED: 'Disabled', FAILED: 'Authorization failed',
  }[status] || 'Unverified'
}

function appName(row) {
  return row.config?.app_name || (row.kind === 'custom' ? row.name : row.service
    .split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' '))
}

function CredentialFields({ fields, busy, onSubmit, submitLabel, children }) {
  const [values, setValues] = useState(() => Object.fromEntries(fields.map(f => [f.name, f.secret || f.default == null ? '' : String(f.default)])))
  return <form className="hub-form" autoComplete="off" onSubmit={e => {
    e.preventDefault()
    const submitted = { ...values }
    setValues(current => ({ ...current, ...Object.fromEntries(fields.filter(f => f.secret).map(f => [f.name, ''])) }))
    onSubmit(submitted)
  }}>
    {children}
    {fields.map(field => {
      const props = { name: field.name, required: field.required, value: values[field.name] || '',
        onChange: e => setValues(current => ({ ...current, [field.name]: e.target.value })) }
      return <label key={field.name}>{/^(generic_)?api_key$/.test(field.name) ? 'API key' : field.label}
        {field.choices?.length ? <select {...props}><option value="">Select an option</option>
          {field.choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        </select> : <input {...props} type={field.secret ? 'password' : field.type === 'email' ? 'email' : 'text'}
          autoComplete={field.secret ? 'new-password' : 'off'} spellCheck={false} />}
        {field.description && !/^(generic_)?api_key$/.test(field.name) && <small>{field.description.split('\n')[0]}</small>}
      </label>
    })}
    <button className="connection-primary" disabled={busy}>{busy ? 'Checking…' : submitLabel}</button>
  </form>
}

function ConnectDialog({ app, row, candidates, onClose, onUpdate }) {
  const ref = useRef(null)
  const [detail, setDetail] = useState(null), [scheme, setScheme] = useState(null), [stage, setStage] = useState(null)
  const [existing, setExisting] = useState(false), [account, setAccount] = useState('')
  const [resource, setResource] = useState(row?.config?.resource || '')
  const [busy, setBusy] = useState(false), [feedback, setFeedback] = useState(null)
  const [link, setLink] = useState(null), [finished, setFinished] = useState(false)

  useEffect(() => {
    const node = ref.current
    node.showModal()
    return () => node.close()
  }, [])
  async function load() {
    setFeedback(null)
    try {
      const value = await call(base + '/apps/' + encodeURIComponent(app.slug))
      setDetail(value); setScheme(value.schemes?.[0]?.mode || null)
    } catch { setFeedback({ error: true, message: 'Could not load sign-in options. Try again.' }) }
  }
  useEffect(() => { load() }, [app.slug])

  const selected = detail?.schemes?.find(value => value.mode === scheme)
  const fields = stage?.fields || selected?.account_fields || []
  const help = detail?.resource?.help_url || stage?.help_urls?.[0] || selected?.help_urls?.[0] || detail?.help_urls?.[0]
  const title = (row ? 'Reconnect ' : 'Connect ') + (detail?.name || app.name)
  const scopeField = detail?.resource && <label>{detail.resource.label}
    <input type={detail.resource.type} required value={resource} onChange={e => setResource(e.target.value)} />
    <small>{detail.resource.instruction}</small>
  </label>

  async function submit(values) {
    setBusy(true); setFeedback(null)
    try {
      const shared = { service: app.slug, ...(row ? { replace: row.id } : {}), ...(resource ? { resource: resource.trim() } : {}) }
      const value = existing
        ? await call(base + '/project/bind', { account_id: account, ...shared })
        : await call(base + '/connect', { ...shared, ...(scheme ? { scheme } : {}),
            [stage?.stage === 'auth_config' ? 'config_fields' : 'fields']: values })
      if (value.status === 'NEEDS_INPUT') { setStage(value); return }
      if (value.url) { setLink(value.url); onUpdate(); return }
      const status = rowStatus(value)
      const error = ['INVALID_CREDENTIALS', 'EXPIRED', 'SCOPE_MISMATCH', 'FAILED', 'DISABLED'].includes(status)
      setFeedback({ error, message: value.message || (status === 'ACTIVE' ? 'Connected. Your app is ready to use.' : statusLabel(value)) })
      if (value.id) onUpdate()
      if (status === 'ACTIVE' || status === 'UNVERIFIED' || PENDING.includes(status)) setFinished(true)
    } catch (error) {
      const setup = /choose a connection|enter the address|choose a valid/i.test(error.message)
      setFeedback({ error: true, message: setup ? error.message : 'Could not connect this account. Check the details and try again.' })
    } finally { setBusy(false) }
  }

  return <dialog ref={ref} className="connect-dialog" aria-labelledby="connect-title"
    onCancel={e => { e.preventDefault(); if (!busy) onClose() }}
    onClick={e => { if (e.target === ref.current && !busy) onClose() }}>
    <section className="connect-dialog-body">
      <header className="connect-dialog-header">
        <AppLogo slug={app.slug} src={detail?.logo || app.logo} name={app.name} />
        <h2 id="connect-title">{title}</h2>
        <button className="connection-icon" type="button" aria-label="Close connection dialog" onClick={onClose} disabled={busy}><X size={19} /></button>
      </header>
      {feedback && <p className={`connection-notice ${feedback.error ? 'error' : ''}`} role={feedback.error ? 'alert' : 'status'}>{feedback.message}</p>}
      {!detail && (feedback ? <button onClick={load}>Try again</button> : <p role="status">Loading sign-in options…</p>)}
      {link && <div className="connect-dialog-section"><p>Approve access on the provider’s secure sign-in page. TTROS checks the connection when you return.</p>
        <a className="connection-primary" href={link}>Continue to provider</a></div>}
      {finished && <button className="connection-primary" onClick={onClose}>Done</button>}
      {detail && !link && !finished && <>
        {!!candidates.length && <div className="connect-choices" aria-label="Connection method">
          <button aria-pressed={!existing} onClick={() => { setExisting(false); setFeedback(null) }} disabled={busy}>{row ? 'New credentials' : 'Connect a new account'}</button>
          <button aria-pressed={existing} onClick={() => { setExisting(true); setFeedback(null) }} disabled={busy}>Use existing account</button>
        </div>}
        {!existing && detail.schemes?.length > 1 && <label className="connect-method">Sign-in method
          <select value={scheme || ''} disabled={busy} onChange={e => { setScheme(e.target.value); setStage(null); setFeedback(null) }}>
            {detail.schemes.map(option => <option key={option.mode} value={option.mode}>{SCHEME_LABEL[option.mode] || option.name}</option>)}
          </select></label>}
        <p className="connect-instructions">{existing ? 'Choose an account from your linked Platform project.' :
          stage?.stage === 'auth_config' ? 'Enter the settings for your provider app.' :
          selected?.oauth ? 'Continue to the provider to approve access.' :
          detail.no_auth ? 'This app is ready to use without signing in.' : 'Enter your credentials to verify the connection.'}
          {!existing && help && <> <a href={help} target="_blank" rel="noreferrer">Get credentials</a></>}
        </p>
        <CredentialFields key={existing ? 'existing' : `${scheme}:${stage?.stage || 'account'}`} fields={existing ? [] : fields}
          busy={busy} onSubmit={submit} submitLabel={existing ? 'Use selected account' : selected?.oauth || stage?.stage === 'auth_config' ? 'Continue' : row ? 'Verify and replace' : 'Connect securely'}>
          {existing && <label>Platform account<select required value={account} onChange={e => setAccount(e.target.value)}>
            <option value="">Select an account</option>
            {candidates.map((value, index) => <option key={value.account} value={value.account}>{value.identity || `Platform account ${index + 1}`}</option>)}
          </select></label>}
          {scopeField}
        </CredentialFields>
      </>}
    </section>
  </dialog>
}

function AccountActions({ row, name, busy, reconnect, detach, check }) {
  return <div className="account-actions">
    {!!row.enabled && PENDING.includes(rowStatus(row)) && <button onClick={check} disabled={busy}>Check status</button>}
    {row.kind === 'composio' && <button onClick={reconnect} disabled={busy}>Reconnect</button>}
    {!!row.enabled && <button onClick={() => detach(row, 'disconnect')} disabled={busy}>Disconnect</button>}
    <button className="connection-icon connection-remove" onClick={() => detach(row, 'remove')} disabled={busy}
      aria-label={`Remove ${name}${row.identity ? ' ' + row.identity : ''}`} title="Remove from TTROS"><X size={16} /></button>
  </div>
}

export default function ConnectionHub({ returning = hasConnectionReturn(location.search), initialApp = new URLSearchParams(location.search).get('app'), returnSearch = location.search }) {
  const [returnFeedback, setReturnFeedback] = useState(null)
  const [state, setState] = useState(null), [catalogue, setCatalogue] = useState(null)
  const [tab, setTab] = useState(returning ? 'accounts' : 'catalogue')
  const [query, setQuery] = useState(initialApp || ''), [catalogueBusy, setCatalogueBusy] = useState(false)
  const [catalogueError, setCatalogueError] = useState(''), [searchRetry, setSearchRetry] = useState(0)
  const [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState('')
  const [dialog, setDialog] = useState(initialApp ? { app: { slug: initialApp, name: initialApp } } : null)
  const [form, setForm] = useState(initial), [operations, setOperations] = useState(sample), [spec, setSpec] = useState(''), [editing, setEditing] = useState(null)
  const [agents, setAgents] = useState([])
  const [readArguments, setReadArguments] = useState({}), [selectedRead, setSelectedRead] = useState({})

  async function load() { setState(await call(base + '/state')) }
  function reload() { load().catch(() => setError('Could not load your accounts. Try Refresh accounts.')) }
  async function run(fn) {
    setBusy(true); setError(''); setResult(null)
    try { const value = await fn(); setResult(value); await load(); return value }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  async function checkReturn() {
    setBusy(true)
    try { setReturnFeedback(await verifyConnectionReturn(call, returnSearch)); await load() }
    catch { setReturnFeedback({ status: 'ERROR', message: 'Verification could not finish. Check the saved authorization again.' }) }
    finally { setBusy(false) }
  }
  useEffect(() => { if (returning) checkReturn(); else reload() }, [])
  useEffect(() => {
    let stale = false
    setCatalogueBusy(true); setCatalogueError('')
    const timer = setTimeout(async () => {
      try {
        const page = await call(base + '/catalogue?q=' + encodeURIComponent(query.trim()))
        if (!stale) setCatalogue(page)
      } catch { if (!stale) setCatalogueError('Could not load the app catalogue. Try again.') }
      finally { if (!stale) setCatalogueBusy(false) }
    }, query ? 220 : 0)
    return () => { stale = true; clearTimeout(timer) }
  }, [query, searchRetry])

  async function loadMore() {
    setCatalogueBusy(true)
    try {
      const value = await call(base + '/catalogue?q=' + encodeURIComponent(query.trim()) + '&cursor=' + encodeURIComponent(catalogue.next_cursor))
      setCatalogue(previous => previous !== catalogue ? previous : ({ ...value,
        items: [...previous.items, ...value.items.filter(app => !previous.items.some(p => p.slug === app.slug))] }))
    } catch { setCatalogueError('Could not load more apps. Try again.') }
    finally { setCatalogueBusy(false) }
  }
  async function loadAgents() { const value = await call(base + '/agents'); setAgents(value.agents); return null }
  function saveAgentAccess(ident, mode, allowed) {
    return run(async () => {
      const value = await call(`${base}/connections/${ident}/access`, { mode, agents: allowed }, 'PUT')
      await loadAgents()
      return { message: `${value.name || 'Account'} access saved for Agent Forge.` }
    })
  }
  const detach = (row, action) => run(() => call(`${base}/connections/${row.id}/${action}`, {}))
  const update = (key, value) => setForm(previous => ({ ...previous, [key]: value }))
  function edit(row) {
    setEditing(row.id); setForm({ name: row.name, service: row.service, ...row.config, custom_required: true })
    setOperations(JSON.stringify(row.config.operations, null, 2)); setSpec(''); setTab('custom')
  }
  const accounts = state?.connections || []
  const catalogueBySlug = new Map((catalogue?.items || []).map(app => [app.slug, app]))
  const forRow = row => catalogueBySlug.get(row.service) || { slug: row.service, name: appName(row) }
  const rowsFor = slug => accounts.filter(row => row.service === slug && row.kind === 'composio'
    && row.config?.source === 'project' && row.config?.project === state?.project?.key_fingerprint)
  const open = (app, row) => setDialog({ app, row })
  function accountLine(row, app) {
    const status = rowStatus(row)
    return <div className="integration-account" key={row.id} data-connection={row.id}>
      <div className="account-summary">
        <span className="connection-badge" data-status={status}>{statusLabel(row)}</span>
        {row.identity && <span className="connection-account" title={row.identity}>{row.identity}</span>}
      </div>
      <AccountActions row={row} name={app.name} busy={busy} reconnect={() => open(app, row)} detach={detach}
        check={() => run(async () => ({ message: statusLabel(await call(`${base}/connections/${row.id}/status`, {})) }))} />
    </div>
  }

  return <section className="connection-hub" aria-label="Connections hub">
    <div className="integration-toolbar">
      <nav className="integration-tabs" aria-label="Connection sections">
        <button aria-pressed={tab === 'catalogue'} onClick={() => setTab('catalogue')}>Browse apps</button>
        <button aria-pressed={tab === 'accounts'} onClick={() => setTab('accounts')}>My accounts <span>{accounts.length}</span></button>
      </nav>
      <button className="integration-refresh" disabled={busy} onClick={() => run(() => call(base + '/refresh', {}))}>{busy ? 'Checking…' : 'Refresh accounts'}</button>
    </div>
    {returnFeedback && <section className="connections-feedback" aria-label="Connection return verification" role={returnFeedback.status === 'ERROR' ? 'alert' : 'status'}>
      <p>{returnFeedback.message}</p>
      {returnFeedback.connections?.map(row => <p key={row.id}>{statusLabel(row)}</p>)}
      {returnFeedback.status !== 'CONNECTED' && <button disabled={busy} onClick={checkReturn}>Check saved authorization</button>}
    </section>}
    {error && <p className="connection-notice error" role="alert">{error}</p>}
    {result?.message && <p className="connection-notice" role="status">{result.message}</p>}
    {tab === 'catalogue' && <>
      <div className="integration-search">
        <Search size={20} aria-hidden="true" />
        <input aria-label="Search apps" type="search" value={query} onChange={e => setQuery(e.target.value)}
          placeholder="Search apps by name or category" maxLength={100} />
      </div>
      <div className="integration-catalogue-header"><h2>{query.trim() ? 'Search results' : 'App catalogue'}</h2>
        {catalogue && <p>{catalogue.total ?? catalogue.items.length} {(catalogue.total ?? catalogue.items.length) === 1 ? 'app' : 'apps'}</p>}</div>
      {catalogueBusy && !catalogue && <p role="status">Loading apps…</p>}
      {catalogueError && <p role="alert">{catalogueError} <button onClick={() => setSearchRetry(value => value + 1)}>Retry</button></p>}
      {!catalogueBusy && catalogue && !catalogue.items.length && <p>No apps match your search.</p>}
      <div className="app-catalogue" aria-busy={catalogueBusy}>{catalogue?.items.map(app => {
        const rows = rowsFor(app.slug)
        return <article className="app-tile" key={app.slug} data-app={app.slug} data-connected={rows.some(row => rowStatus(row) === 'ACTIVE')}>
          <header className="app-heading"><AppLogo slug={app.slug} src={app.logo} name={app.name} /><h3>{app.name}</h3>
            {!rows.length && <button className="connection-primary" aria-label={'Connect ' + app.name} onClick={() => open(app)}>Connect</button>}
          </header>
          {rows.length ? rows.map(row => accountLine(row, app)) :
            <p className="app-description">{app.description || 'Available to connect'}</p>}
        </article>
      })}</div>
      {catalogue?.next_cursor && <div className="integration-more"><button disabled={catalogueBusy} onClick={loadMore}>Load more apps</button></div>}
    </>}
    {tab === 'accounts' && <>
      <div className="integration-catalogue-header"><h2>My connected accounts</h2></div>
      {accounts.length ? <div className="app-catalogue">{accounts.map(row => {
        const app = forRow(row)
        return <article className="app-tile" key={row.id} data-connected={rowStatus(row) === 'ACTIVE'}>
          <header className="app-heading"><AppLogo slug={app.slug} src={app.logo} name={app.name} /><h3>{app.name}</h3></header>
          {accountLine(row, app)}
        </article>
      })}</div> : <div className="integration-empty"><Link2 size={24} aria-hidden="true" /><p>{state ? 'Connect an app to see it here.' : 'Loading accounts…'}</p>
        {state && <button className="connection-primary" onClick={() => setTab('catalogue')}>Browse apps</button>}</div>}
    </>}
    <details className="connections-advanced">
      <summary><ChevronDown size={15} aria-hidden="true" /> Advanced settings</summary>
      <div className="connections-actions">
        <button onClick={() => { setTab('custom'); setEditing(null); setForm(initial); setOperations(sample); setSpec('') }}>Build custom connection</button>
        <button onClick={() => { setTab('agents'); run(loadAgents) }}>Agent Forge permissions</button>
      </div>
      {tab === 'agents' && <AgentConnections state={state} agents={agents} busy={busy} save={saveAgentAccess} />}
      <ComposioProject state={state} busy={busy} run={run} call={call} base={base} onConnect={service => open({ slug: service, name: service })} />
      {accounts.map(row => <section key={row.id} className="connections-card"><h3>{forRow(row).name}</h3>
        <details><summary>Account details</summary><p>{row.kind} · {row.service} · <strong>{row.status}</strong>{!row.enabled && ' · Disabled'}</p>
        <p>Account: {row.label || row.account || row.config.host}</p>
        {row.config.verification_error && <p>{row.config.verification_error}</p>}
        {row.config.provider_account?.status_reason && <p>{row.config.provider_account.status_reason}</p>}<p>Permissions: {row.permissions.join(', ') || 'None'}</p>
        {row.kind === 'composio' && <p className="source-caption">Sign-in method: {row.config.provider_account?.auth_config?.auth_scheme || row.config.scheme || 'Managed by Composio'}. Provider scopes are approved on the provider’s screen; the runtime permission policy decides what TTROS may do.</p>}
        <p>Last backend verification: {row.verified ? new Date(row.verified * 1000).toLocaleString() : 'Never'}.</p>
        <code>{row.id}</code>
        <div className="connections-actions">
          {row.kind === 'composio' && <button disabled={busy || !row.enabled} onClick={() => run(async () => ({ message: statusLabel(await call(`${base}/connections/${row.id}/status`, {})) }))}>Check status</button>}
          {row.operations.filter(operation => !row.permissions.includes(operation + ':write') && !['tool', 'tools'].includes(operation)).map(operation => <button disabled={busy || !row.enabled} key={operation} onClick={() => run(() => call(`${base}/connections/${row.id}/execute`, { operation, arguments: JSON.parse(readArguments[row.id] || '{}') }))}>Test {operation}</button>)}
          {row.kind === 'custom' && <><button onClick={() => edit(row)}>Edit</button><button disabled={busy} onClick={() => run(() => call(`${base}/connections/${row.id}/${row.enabled ? 'disable' : 'enable'}`, {}))}>{row.enabled ? 'Disable' : 'Enable'}</button></>}
          <button disabled={busy || !row.enabled} onClick={() => run(() => call(`${base}/connections/${row.id}/disconnect`, {}))}>Disconnect TTROS access</button>
        </div><p className="source-caption">David, Agent Forge and Jobs discover this app’s tools automatically. Removing it here does not revoke the provider’s consent; do that in the provider’s security settings.</p>
        {row.config.manual_verification && <p>Run a successful read to verify this account.</p>}
        <label>Read arguments (JSON)<textarea rows={2} value={readArguments[row.id] || '{}'} onChange={e => setReadArguments(previous => ({ ...previous, [row.id]: e.target.value }))} /></label>
        {row.config.manual_verification && <div className="connections-actions">
          <button disabled={busy || !row.enabled} onClick={() => run(() => call(`${base}/connections/${row.id}/execute`, { operation: 'tools' }))}>Find app tools</button>
          <button disabled={busy || !row.enabled} onClick={() => run(() => call(`${base}/connections/${row.id}/verify`, { operation: 'tool', arguments: JSON.parse(readArguments[row.id] || '{}') }))}>Verify with a read tool</button>
        </div>}
        {!!row.operations.length && <><label>Read for David or Jobs<select value={selectedRead[row.id] || row.operations[0]} onChange={e => setSelectedRead(previous => ({ ...previous, [row.id]: e.target.value }))}>{row.operations.filter(op => !row.permissions.includes(op + ':write') && op !== 'tool').map(op => <option key={op}>{op}</option>)}</select></label><div className="connections-actions">
          <button disabled={busy || !row.enabled} onClick={() => run(() => call(`${base}/connections/${row.id}/david`, { operation: selectedRead[row.id] || row.operations[0], arguments: JSON.parse(readArguments[row.id] || '{}') }))}>Ask David to read</button>
          <button disabled={busy || !row.enabled} onClick={() => run(() => call(`${base}/connections/${row.id}/jobs`, { operation: selectedRead[row.id] || row.operations[0], arguments: JSON.parse(readArguments[row.id] || '{}') }))}>Run read as TTROS Job</button>
        </div></>}
        </details>

      </section>)}
      {result && <details><summary>Latest result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
      {!!state?.receipts?.length && <details><summary>Provider receipts</summary><pre>{JSON.stringify(state.receipts, null, 2)}</pre></details>}
    </details>
    {tab === 'custom' && <section className="editorial-card connections-card"><h2>{editing ? 'Edit Connection' : 'Build a Connection'}</h2>
      <p>Search the Composio catalogue first. Use a custom connection for unsupported APIs or a restricted endpoint. Only named HTTPS GET operations are supported.</p>
      <form className="hub-form" onSubmit={e => { e.preventDefault(); run(async () => {
        const body = { name: form.name, service: form.service, host: form.host, auth: form.auth, secret_ref: form.secret_ref, custom_required: form.custom_required, operations: JSON.parse(operations), openapi: spec ? JSON.parse(spec) : null }
        const value = await call(base + '/custom' + (editing ? '/' + editing : ''), body, editing ? 'PUT' : 'POST'); setTab('accounts'); return value
      }) }}>
        <label>Connection name<input required value={form.name} onChange={e => update('name', e.target.value)} /></label>
        <label>Service slug<input required value={form.service} onChange={e => update('service', e.target.value)} placeholder="example_service" /></label>
        <label>HTTPS API host<input required value={form.host} onChange={e => update('host', e.target.value)} placeholder="https://api.example.com" /></label>
        <label>Authentication<select value={form.auth} onChange={e => update('auth', e.target.value)}><option value="none">No authentication</option><option value="bearer">Bearer API key</option><option value="x-api-key">X-API-Key</option></select></label>
        {form.auth !== 'none' && <label>Existing encrypted secret reference<input value={form.secret_ref} onChange={e => update('secret_ref', e.target.value)} placeholder="TTROS_CONNECTOR_SECRET_EXAMPLE" /><small>Provision a named encrypted systemd credential in OS secret storage and assign it to this member and host. Enter only the reference here; key values never enter this page or connection records. The backend requires TTROS_CONNECTOR_ENCRYPTED_DIRECTORY and a scoped TTROS_CONNECTOR_SECRET_BINDINGS entry.</small></label>}
        <label>Named read operations and schemas (JSON)<textarea rows={14} value={operations} onChange={e => setOperations(e.target.value)} /></label>
        <label>Optional OpenAPI 3 specification (JSON)<textarea rows={4} value={spec} onChange={e => setSpec(e.target.value)} placeholder="Paste a reviewed OpenAPI JSON document. References and writes are not imported." /></label>
        <label><input type="checkbox" checked={form.custom_required} onChange={e => update('custom_required', e.target.checked)} /> This endpoint needs a restricted adapter even if Composio supports this service.</label>
        <p>Saving leaves the connection UNVERIFIED. Test a named operation to inspect its sanitized response and create a durable provider receipt. Redirects, private DNS/IPs, arbitrary headers and write methods are refused.</p>
        <button disabled={busy}>Save connection</button>
      </form>
    </section>}

    {dialog && <ConnectDialog app={dialog.app} row={dialog.row} onClose={() => setDialog(null)} onUpdate={reload}
      candidates={(state?.project_accounts || []).filter(account => account.service === dialog.app.slug && account.status === 'ACTIVE')} />}
  </section>
}
