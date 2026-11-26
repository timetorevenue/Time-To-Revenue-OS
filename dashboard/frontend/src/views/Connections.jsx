import { useEffect, useRef, useState } from 'react'
import { connectionRequest as call, hasConnectionReturn } from '../connectionsApi'
import './Connections.css'
import ConnectionHub from './ConnectionHub'

function Feedback({ value, pending }) {
  const ref = useRef(null)
  useEffect(() => {
    if (value && !pending) { ref.current?.focus({ preventScroll: true }); ref.current?.scrollIntoView?.({ block: 'nearest', behavior: 'auto' }) }
  }, [value, pending])
  if (pending) return <p className="connections-feedback" role="status">{pending}… Contacting the TTROS backend.</p>
  if (!value) return null
  return <div ref={ref} tabIndex={-1} className={`connections-feedback ${value.error ? 'error' : ''}`} role={value.error ? 'alert' : 'status'}>
    <strong>{value.title}</strong><p>{value.message}</p>
    {value.detail?.instructions && <p>{value.detail.instructions}</p>}
    {!!value.result?.missing_configuration?.length && <><p>Missing configuration:</p><ul>{value.result.missing_configuration.map(item => <li key={item}>{item}</li>)}</ul></>}
    {value.result?.identity_blocker && <p>{value.result.identity_blocker}</p>}
    {value.result?.status_source && <p>{value.result.status_source}</p>}
    {value.result?.checked_at && <p>Checked: {new Date(value.result.checked_at).toLocaleString()}</p>}
    {value.result?.upstream_revocation && <p>{value.result.upstream_revocation}</p>}
  </div>
}

export default function Connections({ onNavigate }) {
  // Capture before the Dashboard shell normalizes the URL while session loads.
  const [startSearch] = useState(() => location.search)
  const returning = hasConnectionReturn(startSearch)
  const initialApp = new URLSearchParams(startSearch).get('app')
  const [settings, setSettings] = useState(false)
  const [session, setSession] = useState(null), [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(null), [feedback, setFeedback] = useState(null), [receipt, setReceipt] = useState(null), [setup, setSetup] = useState(null)
  const setupRef = useRef(null)
  async function load() {
    const auth = await call('/api/auth/session'); setSession(auth)
    const operator = auth.identity?.workspace === 'ttros-operator' || auth.identity?.method === 'local_preview'
    const connections = operator ? await call('/api/account-connections') : null
    setStatus(connections)
    return { auth, connections }
  }
  useEffect(() => { load().catch(error => setFeedback({ target: 'page', title: 'Connections could not load', error: true, message: error.message })) }, [])
  useEffect(() => { if (setup) { setupRef.current?.focus({ preventScroll: true }); setupRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' }) } }, [setup])
  async function action(target, title, fn, reload = true) {
    setBusy({ target, title }); setFeedback(null)
    try {
      const result = await fn()
      if (result?.url) {
        const url = new URL(result.url)
        const allowed = target === 'google' ? url.origin === 'https://accounts.google.com' : url.hostname === 'composio.dev' || url.hostname.endsWith('.composio.dev')
        if (url.protocol !== 'https:' || !allowed) throw Error('The backend returned an invalid authorization link. No redirect was performed.')
        setFeedback({ target, title, message: 'Authorization started. Opening the provider sign-in page…' })
        window.location.assign(url.href)
        return
      }
      setFeedback({ target, title, message: result?.message || 'Request completed.', result })
      if (reload) {
        try { await load() } catch (error) { setFeedback({ target, title, message: `${result?.message || 'Action completed.'} The follow-up refresh failed: ${error.message}`, error: true }) }
      }
    } catch (error) {
      setFeedback({ target, title: `${title} · Needs attention${error.status ? ` (HTTP ${error.status})` : ''}`, error: true, message: target === 'google' && !operator && error.status === 503 ? 'Google sign-in is temporarily unavailable. Please contact TTROS support.' : error.message, detail: operator ? error.detail : undefined })
    } finally { setBusy(null) }
  }
  const local = session?.identity?.method === 'local_preview'
  const operator = local || session?.identity?.workspace === 'ttros-operator'
  const notice = target => <Feedback value={feedback?.target === target ? feedback : null} pending={busy?.target === target ? busy.title : null} />
  const providerAction = (provider, name, label) => action(provider.id, `${provider.label} · ${label}`, () => call(`/api/account-connections/${provider.id}/${name}`, {}))
  const openSetup = id => {
    setSetup(id)
    if (setup === id) { setupRef.current?.focus(); setupRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' }) }
  }
  const setupProvider = status?.providers.find(p => p.id === setup)
  return <div className="cc-page connections-page">
    <div className="page-heading integration-page-heading">
      <div><h1>Connections</h1><p>Connect the apps you use with David, Agent Forge and Jobs.</p></div>
      {session?.identity && <button aria-pressed={settings} onClick={() => setSettings(value => !value)}>{settings ? 'Back to connections' : 'Sign-in settings'}</button>}
    </div>
    {notice('page')}
    {!session && feedback?.error && <button onClick={() => action('page', 'Configuration checked', load, false)}>Retry</button>}
    {session && !session.identity && returning && <p role="status">Sign in to the same TTROS account to verify your app connection.</p>}
    {(settings || (session && !session.identity)) && <section className="editorial-card connections-card" data-provider="google">
      <h2>TTROS sign-in</h2>
      <p>{session?.identity ? `Signed in as ${session.identity.email || 'local preview operator'}` : 'Sign in to manage app connections.'}</p>
      <div className="connections-actions">
        {session?.identity?.method !== 'google_oidc' && <button data-action="sign-in" disabled={!!busy} onClick={() => action('google', 'Google sign-in', () => call('/api/auth/google/start', {}), false)}>Sign in with Google</button>}
        {session?.identity && !local && session.identity.method !== 'google_oidc' && <button data-action="link" disabled={!!busy} onClick={() => action('google', 'Link Google to existing user', () => call('/api/auth/google/start?link=true', {}), false)}>Link Google to this existing user</button>}
        {session?.identity?.method === 'google_oidc' && <button data-action="logout" disabled={!!busy} onClick={() => action('google', 'Signed out', async () => { await call('/api/auth/logout', {}); setReceipt(null); return { message: 'Your Dashboard session was signed out. Provider accounts were preserved.' } })}>Sign out</button>}
        {operator && <button data-action="setup" onClick={() => openSetup('google')}>Google setup</button>}
      </div>
      {!session?.google_configured && <p>Google sign-in is temporarily unavailable. Please contact TTROS support.</p>}
      {notice('google')}
      {operator && session?.identity && <p className="source-caption">Workspace: {session.identity.workspace} · Member: {session.identity.user}</p>}
    </section>}
    {session?.identity && <div hidden={settings}><ConnectionHub returning={returning} initialApp={initialApp} returnSearch={startSearch} /></div>}
    {operator && <details className="connections-advanced" open={settings && setup === 'google' ? true : undefined}><summary>Operator configuration</summary>
    <button data-action="refresh" disabled={!!busy} onClick={() => action('page', 'Configuration checked', async () => {
      const { auth, connections } = await load()
      return { message: `Google sign-in ${auth.google_configured ? 'configured' : 'requires setup'}. ${connections?.providers.filter(p => p.configured).length || 0} of ${connections?.providers.length || 0} provider configurations ready.` }
    }, false)}>Check configuration</button>
    {status && <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 16 }}>
        {status.providers.map(provider => <section className="editorial-card connections-card" data-provider={provider.id} key={provider.id}>
          <h2>{provider.label}</h2><p>Last recorded state: <strong>{provider.status.replaceAll('_', ' ')}</strong> · {provider.configured ? 'Configuration ready' : 'Setup required'}</p>
          <p className="connections-state">{provider.status_source}</p><p>{provider.setup}</p>
          <div className="connections-actions">
            {['connect', 'reconnect', 'status', 'disconnect'].map(name => {
              const label = name === 'status' ? 'Check status' : name[0].toUpperCase() + name.slice(1)
              return <button data-action={name} aria-label={`${label} · ${provider.label}`} key={name} disabled={!!busy} onClick={() => providerAction(provider, name, label)}>{label}</button>
            })}
            <button data-action="setup" onClick={() => openSetup(provider.id)}>Setup instructions</button>
          </div>
          {notice(provider.id)}
          {provider.permissions.map(permission => <label key={permission} style={{ display: 'block', marginTop: 12 }}><input type="checkbox" checked={provider.granted.includes(permission)} disabled={!!busy || ['NOT_CONNECTED', 'DISCONNECTED'].includes(provider.status)} onChange={e => action(provider.id, 'David permissions updated', async () => {
            const result = await call(`/api/account-connections/${provider.id}/permissions`, { permissions: e.target.checked ? [...provider.granted, permission] : provider.granted.filter(x => x !== permission) }, 'PUT')
            return { ...result, message: 'This member’s provider read permissions were updated.' }
          })} /> Allow David: {permission}</label>)}
          <p className="source-caption">Disconnect removes this member’s TTROS binding. It preserves the upstream provider account.</p>
          {provider.id === 'vercel' && <button data-action="verify" disabled={!!busy} onClick={() => action(provider.id, 'Vercel deployment verification', async () => {
            const result = await call('/api/account-connections/vercel/read/deployment', {}); setReceipt(result)
            return { ...result, message: result.matches_registered ? 'Vercel metadata matches the registered deployment, project and source.' : 'Vercel responded, but the metadata does not match the registered deployment. Review the result below.' }
          })}>Verify current deployment (read only)</button>}
        </section>)}
      </div>
      <section className="editorial-card connections-card" data-provider="stripe" style={{ marginTop: 18 }}><h2>Stripe · TEST only</h2>
        <p>{status.stripe.configured ? 'Runtime credentials present · Provider operations still need verification' : 'Setup required · Test credentials are not injected'}</p><p>{status.stripe.proof}</p><p>{status.stripe.customer_owned}</p>
        <div className="connections-actions"><button data-action="verify" disabled={!!busy} onClick={() => action('stripe', 'Stripe TEST connection check', async () => {
          const result = await call('/api/account-connections/stripe-test/connection/verify', {}); setReceipt(result)
          return { ...result, message: 'Stripe TEST account read verified. Product, checkout and webhook operations still need separate verification.' }
        })}>Verify Stripe TEST connection (read only)</button>
          <button data-action="launch" onClick={() => onNavigate('websites')}>Open existing product-launch workflow</button><button data-action="setup" onClick={() => openSetup('stripe')}>Stripe TEST setup</button></div>
        {notice('stripe')}<p>Live Stripe keys are refused. Product, price, hosted test checkout and signed webhook operations remain in the existing launch workflow.</p>
      </section>
      <p className="source-caption">{status.existing_connectors}</p>
      <button data-action="audit" disabled={!!busy} onClick={() => action('audit', 'Audit receipts loaded', async () => {
        const result = await call('/api/account-connections/audit'); setReceipt(result)
        return { message: result.receipts.length ? `Loaded ${result.receipts.length} audit receipts for this workspace member.` : 'No audit receipts exist yet for this workspace member.' }
      }, false)}>View my audit receipts</button>
      {notice('audit')}
    </>}
    {receipt && <pre className="editorial-card" aria-label="Connection result">{JSON.stringify(receipt, null, 2)}</pre>}
    {setup && <section className="editorial-card connections-setup" ref={setupRef} tabIndex={-1} id="connections-setup" aria-label="Connection setup instructions">
      <h2>{setup === 'google' ? 'Google sign-in setup' : setup === 'stripe' ? 'Stripe TEST setup' : `${setupProvider?.label || setup} setup`}</h2>
      {setup === 'google' ? <>
        <p>Use the existing Google OAuth Web application client when available. Use this exact authorized redirect URI:</p><p><code>{session?.google_setup?.callback_url || `${location.origin}/api/auth/google/callback`}</code></p>
        <p>Missing settings: {session?.google_setup?.missing_configuration?.join('; ') || 'None reported. Check the OAuth client’s authorized callback and consent screen test users.'}</p>
        <ol><li><a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Open Google OAuth credentials</a> and register the callback above. Add your Google account as a consent-screen test user if the app is in Testing.</li>
          <li>Use the runtime secret manager to inject TTROS_GOOGLE_CLIENT_ID and TTROS_GOOGLE_CLIENT_SECRET into this isolated backend. Set TTROS_DASHBOARD_ORIGIN to this Dashboard’s exact origin. Use HTTPS outside localhost. Do not paste secrets into chat, Git or this UI.</li>
          <li>After the isolated server loads the settings, use Check configuration again, then Sign in with Google. To link an existing TTROS user, enter via verified Access/Tailscale identity and select Link Google to this existing user. Matching email alone never links accounts.</li></ol>
      </> : setup === 'stripe' ? <>
        <ol><li>In Stripe TEST mode obtain a restricted test key and exact acct_ account ID. Grant account read and Products, Prices and Payment Links write permissions in test mode only.</li>
          <li>Securely inject TTROS_STRIPE_TEST_KEY, TTROS_STRIPE_TEST_ACCOUNT and TTROS_STRIPE_TEST_WEBHOOK_SECRET into the isolated backend. After loading them, use Check configuration again and Verify Stripe TEST connection.</li>
          <li>Use the existing product-launch workflow for real test Product, Price and checkout operations. For signed events run <code>stripe listen --forward-to http://127.0.0.1:4391/api/website/studio/launch/webhook</code> and inject that listener’s signing secret. Complete the actual hosted test checkout. Fixture tests are not provider proof.</li></ol>
        <a href="https://dashboard.stripe.com/test/apikeys" target="_blank" rel="noreferrer">Open Stripe TEST API keys</a>
      </> : <>
        <p>{setupProvider?.setup}</p><p>Missing settings:</p><ul>{setupProvider?.configuration?.missing_configuration?.map(item => <li key={item}><code>{item}</code></li>)}</ul>
        {setupProvider?.configuration?.identity_blocker && <p>{setupProvider.configuration.identity_blocker}</p>}
        <ol><li>Provision the application key through runtime secret injection. Provider tokens stay at Composio.</li><li>In context/account_connections.json, set the provider’s reviewed auth_config_id and enable read_only_reviewed only after reviewing its actual read scopes.</li><li>Sign in through configured Google OIDC or the existing verified operator identity, then Connect. Complete provider consent. Check status queries the saved attempt and verifies its owner. Reconnect opens a new supported authorization flow; it does not delete the previous upstream account.</li></ol>
        {setup === 'vercel' && <a href="https://vercel.com/login" target="_blank" rel="noreferrer">Open Vercel sign-in · Continue with Google</a>}
        {['googledrive', 'gmail'].includes(setup) && <p>Google sign-in grants no Gmail or Drive data access. Complete this separate provider consent.</p>}
        <p><a href="https://platform.composio.dev" target="_blank" rel="noreferrer">Open Composio provider setup</a></p>
      </>}
      <p>Keep all production services and existing accounts unchanged. These instructions apply to the isolated Dashboard.</p>
    </section>}
    </details>}
  </div>
}
