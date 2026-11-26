import { useEffect, useMemo, useState } from 'react'
import { Copy, RefreshCw, Save, Search } from 'lucide-react'
import { createQueueItem, getArtifacts, getDashboardAgents, getDashboardSystemWatch, getQueueArtifact, getSearchStatus, ingestTick, openQueueArtifactFolder, reindexSearch, searchIndex } from '../api'
import { EmptyState, PageHeader, RowButton, SourceChip, StatusChip } from '../components/DashboardKit'

const errorText = error => {
  const detail = error?.response?.data?.detail
  const value = detail?.output || detail || error?.message || 'Action failed'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function AgentsPage() {
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)
  useEffect(() => { getDashboardAgents().then(setData) }, [])
  const components = data?.components || []
  const current = selected || components[0]
  return (
    <>
      <PageHeader title="Agents" question="Local component drilldowns by owner, workbench, receipts, and token attribution." />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {components.map(component => <button key={component.id} onClick={() => setSelected(component)} className="rounded-lg border border-softgraph bg-graphite/70 p-3 text-left hover:border-champagne/50"><div className="text-sm font-semibold text-stone">{component.name}</div><div className="mt-1 text-xs text-taupe">{component.group}</div><div className="mt-2 text-xs text-champagne">{component.tokens.tokens ? `${component.tokens.tokens.toLocaleString()} tokens` : 'Token usage: unavailable from current CLI output'}</div></button>)}
      </div>
      {current && <section className="mt-4 rounded-lg border border-softgraph bg-graphite/70 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold text-ivory">{current.name}</h2><span className="text-xs text-taupe">{current.status_text}</span></div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {['queued', 'running', 'done'].map(key => <div key={key} className="rounded-lg border border-softgraph bg-ink p-3"><div className="text-xs uppercase text-taupe">{key}</div><div className="mt-1 text-2xl text-stone">{current.counts[key]}</div></div>)}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="space-y-2">{current.items.map(item => <RowButton key={item.id} title={item.title} meta={`${item.id} · ${item.owner} · ${item.workbench || 'no workbench'}`} right={<><SourceChip source={item.source} /><StatusChip status={item.honest_status || item.status} /></>} />)}{!current.items.length && <EmptyState title="No queue items" detail="Nothing matched this component." />}</div>
          <div className="space-y-2">{current.receipts.map(receipt => <div key={receipt.path} className="rounded-lg border border-softgraph bg-ink p-3 text-xs text-taupe">{receipt.path}</div>)}{!current.receipts.length && <EmptyState title="No receipts" detail="No attributable receipts found by local filename match." />}</div>
        </div>
      </section>}
    </>
  )
}

function IndexStatusStrip({ status, onReindex, onTick, busy }) {
  return (
    <div className="mb-4 grid gap-2 text-xs md:grid-cols-4">
      <div className="rounded-lg border border-softgraph bg-graphite/70 p-3"><div className="text-taupe">Files indexed</div><div className="mt-1 text-stone">{status?.files_indexed ?? 'no index yet'}</div></div>
      <div className="rounded-lg border border-softgraph bg-graphite/70 p-3"><div className="text-taupe">Last scan</div><div className="mt-1 text-stone">{status?.last_scan_time || 'no index yet'}</div></div>
      <div className="rounded-lg border border-softgraph bg-graphite/70 p-3"><div className="text-taupe">Failures</div><div className="mt-1 text-stone">{status?.failures_count ?? 0}</div></div>
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-softgraph bg-graphite/70 p-3">
        <button onClick={onReindex} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-softgraph bg-ink px-2 text-stone hover:border-champagne/50"><RefreshCw size={13} />Reindex</button>
        <button onClick={onTick} disabled={busy} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-softgraph bg-ink px-2 text-stone hover:border-champagne/50"><RefreshCw size={13} />Tick</button>
      </div>
    </div>
  )
}

function ResultRow({ item, onCreate }) {
  const copy = () => navigator.clipboard?.writeText(item.path)
  return (
    <div className="rounded-lg border border-softgraph bg-ink p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-stone">{item.title}</div>
          <div className="mt-1 break-all text-xs text-taupe">{item.source} · {item.kind} · {item.path}</div>
        </div>
        <SourceChip source={item.kind} />
      </div>
      <div className="mt-2 line-clamp-3 text-xs text-taupe">{item.snippet}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={copy} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-softgraph bg-graphite px-2 text-xs text-stone hover:border-champagne/50"><Copy size={13} />Copy path</button>
        <button onClick={() => onCreate(item)} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-softgraph bg-graphite px-2 text-xs text-stone hover:border-champagne/50"><Save size={13} />Create queue item from artifact</button>
      </div>
    </div>
  )
}

const flattenGroups = groups => Object.values(groups || {}).flat()

export function SearchPage({ initialFilters, refresh }) {
  const [query, setQuery] = useState(initialFilters?.q || '')
  const [data, setData] = useState(null)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const runSearch = async q => {
    setBusy(true)
    try {
      const result = await searchIndex({ q, limit: 50 })
      setData(result)
      setStatus(await getSearchStatus())
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => { runSearch(initialFilters?.q || '') }, [initialFilters?.q])
  const createFromArtifact = async item => {
    setBusy(true)
    try {
      const result = await createQueueItem({
        title: `Review artifact: ${item.title}`,
        owner: 'unassigned',
        priority: 'normal',
        tags: 'artifact,search',
        source: 'dashboard/artifact',
        context: `Create from indexed artifact. Review before running.\n\nArtifact: ${item.path}\nSource: ${item.source}\nKind: ${item.kind}\n\n${item.snippet || ''}`,
        source_refs: item.path,
        definition_of_done: 'Confirm the artifact has been reviewed and decide whether it should become an active work item.',
      })
      setMessage(`Saved ${result.item?.id}. It has not been run.`)
      refresh?.()
    } catch (error) {
      setMessage(errorText(error))
    } finally {
      setBusy(false)
    }
  }
  const items = flattenGroups(data?.groups)
  return (
    <>
      <PageHeader title="Search" question="Local deterministic index. Search and view actions do not call agents." />
      <IndexStatusStrip status={status} busy={busy} onReindex={async () => { setBusy(true); await reindexSearch(); await runSearch(query); setBusy(false) }} onTick={async () => { setBusy(true); await ingestTick(); await runSearch(query); setBusy(false) }} />
      <form onSubmit={event => { event.preventDefault(); runSearch(query) }} className="mb-4 flex gap-2">
        <input value={query} onChange={event => setQuery(event.target.value)} className="h-10 flex-1 rounded-lg border border-softgraph bg-ink px-3 text-sm text-stone outline-none focus:border-champagne/60" />
        <button disabled={busy} className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-softgraph bg-graphite px-3 text-sm text-stone hover:border-champagne/50"><Search size={14} />Search</button>
      </form>
      <div className="mb-3 text-xs font-mono text-champagne">{data ? `${data.count} results · ${data.latency_ms} ms · ${data.token_usage_text}` : 'no index yet'}</div>
      {message && <div className="mb-3 rounded-lg border border-champagne/50 bg-graphite p-2 text-xs text-champagne">{message}</div>}
      <div className="space-y-2">{items.map(item => <ResultRow key={item.path} item={item} onCreate={createFromArtifact} />)}{!items.length && <EmptyState title="No results" detail="Run a scan or search another local term." />}</div>
    </>
  )
}

export function ArtifactsPage({ refresh }) {
  const [data, setData] = useState(null)
  const [status, setStatus] = useState(null)
  const [filters, setFilters] = useState({ type: '', source: '', tag: '' })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [selected, setSelected] = useState(null)
  const [preview, setPreview] = useState(null)
  const load = async (next = filters) => {
    setBusy(true)
    try {
      setData(await getArtifacts({ ...next, limit: 75 }))
      setStatus(await getSearchStatus())
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => { load() }, [])
  const visible = (data?.items || []).filter(item =>
    (!filters.workflow || item.workflow === filters.workflow) &&
    (!filters.lane || item.lane === filters.lane) &&
    (!filters.type || item.kind === filters.type) &&
    (!filters.date || String(item.modified || '').slice(0, 10) === filters.date)
  )
  const openPreview = async item => {
    setSelected(item)
    try {
      setPreview(await getQueueArtifact(item.local_path))
    } catch (error) {
      setPreview({ available: false, reason: errorText(error) })
    }
  }
  const createFromArtifact = async item => {
    setBusy(true)
    try {
      const result = await createQueueItem({
        title: `Review artifact: ${item.title}`,
        owner: 'unassigned',
        priority: 'normal',
        tags: 'artifact,search',
        source: 'dashboard/artifact',
        context: `Create from indexed artifact. Review before running.\n\nArtifact: ${item.path}\nSource: ${item.source}\nKind: ${item.kind}\n\n${item.snippet || ''}`,
        source_refs: item.path,
        definition_of_done: 'Confirm the artifact has been reviewed and decide whether it should become an active work item.',
      })
      setMessage(`Saved ${result.item?.id}. It has not been run.`)
      refresh?.()
    } catch (error) {
      setMessage(errorText(error))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeader title="Artifacts" question="Indexed receipts, outputs, workflows, memory, and Business Brain references." />
      <IndexStatusStrip status={status} busy={busy} onReindex={async () => { setBusy(true); await reindexSearch(); await load(); setBusy(false) }} onTick={async () => { setBusy(true); await ingestTick(); await load(); setBusy(false) }} />
      <div className="mb-4 flex flex-wrap gap-2">
        <select value={filters.type} onChange={event => { const next = { ...filters, type: event.target.value }; setFilters(next); load(next) }} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone">
          <option value="">All types</option><option value="artifact">Artifacts</option><option value="receipt">Receipts</option><option value="workflow">Workflows</option><option value="memory">Memory</option><option value="queue_item">Queue items</option><option value="file">Files</option>
        </select>
        <select value={filters.source} onChange={event => { const next = { ...filters, source: event.target.value }; setFilters(next); load(next) }} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone">
          <option value="">All sources</option><option value="agentic_os_live">Agentic OS Live</option><option value="business_brain">Business Brain</option>
        </select>
        <input value={filters.tag} onChange={event => setFilters(current => ({ ...current, tag: event.target.value }))} onBlur={() => load(filters)} placeholder="tag" className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone outline-none" />
      </div>
      <div className="mb-3 text-xs font-mono text-champagne">{data ? `${data.items?.length || 0} indexed items · ${data.latency_ms} ms · ${data.token_usage_text}` : 'no index yet'}</div>
      {message && <div className="mb-3 rounded-lg border border-champagne/50 bg-graphite p-2 text-xs text-champagne">{message}</div>}
      <section className="mb-4 rounded-lg border border-softgraph bg-graphite/70 p-4" data-testid="new-unfiled-strip">
        <h2 className="text-sm font-semibold text-stone">New &amp; Unfiled</h2>
        <div className="mt-2 flex gap-2 overflow-x-auto">{(data?.new_unfiled || []).map(item => <button key={item.path} onClick={() => openPreview(item)} className="min-w-52 rounded-lg border border-softgraph bg-ink p-2 text-left text-xs text-taupe"><span className="block truncate font-semibold text-stone">{item.title}</span><span className="mt-1 block truncate">{item.local_path}</span></button>)}</div>
      </section>
      <div className="mb-4 grid gap-2 rounded-lg border border-softgraph bg-graphite/60 p-3 md:grid-cols-4">
        <select value={filters.workflow || ''} onChange={event => setFilters({ ...filters, workflow: event.target.value })} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone"><option value="">workflow</option>{['orchestration_acceptance', 'queue_artifacts', 'unfiled'].map(value => <option key={value}>{value}</option>)}</select>
        <select value={filters.lane || ''} onChange={event => setFilters({ ...filters, lane: event.target.value })} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone"><option value="">lane</option>{['marketing', 'revenue', 'delivery', 'operations', 'unassigned'].map(value => <option key={value}>{value}</option>)}</select>
        <select value={filters.type || ''} onChange={event => setFilters({ ...filters, type: event.target.value })} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone"><option value="">type</option>{['artifact', 'workflow', 'receipt', 'result'].map(value => <option key={value}>{value}</option>)}</select>
        <input type="date" value={filters.date || ''} onChange={event => setFilters({ ...filters, date: event.target.value })} className="h-9 rounded-lg border border-softgraph bg-ink px-2 text-xs text-stone" />
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="artifact-library">{visible.map(item => <article key={item.path} className="rounded-lg border border-softgraph bg-graphite/70 p-3" data-artifact-path={item.local_path}>
        <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h3 className="truncate text-sm font-semibold text-stone">{item.title}</h3><div className="mt-1 text-xs text-taupe">{item.workflow} · {item.source} · {item.modified || 'unknown'}</div></div><StatusChip status={item.status}>{item.status}</StatusChip></div>
        <div className="mt-2 text-xs text-taupe">Owner {item.owner} · lane {item.lane}</div>
        <div className="mt-1 truncate text-xs text-taupe">Receipt: {item.receipt || 'unavailable'}</div>
        <div className="mt-1 text-xs text-champagne">{item.token_line || 'Token usage: unavailable'}</div>
        <div className="mt-3 flex flex-wrap gap-2"><button onClick={() => openPreview(item)} className="rounded-md border border-softgraph bg-ink px-2 py-1 text-xs text-stone">Preview</button><button onClick={() => navigator.clipboard?.writeText(item.local_path)} className="rounded-md border border-softgraph bg-ink px-2 py-1 text-xs text-stone">Copy path</button><button onClick={() => openQueueArtifactFolder(item.local_path)} className="rounded-md border border-softgraph bg-ink px-2 py-1 text-xs text-stone">Open folder</button>{item.receipt && <button onClick={() => navigator.clipboard?.writeText(item.receipt)} className="rounded-md border border-softgraph bg-ink px-2 py-1 text-xs text-stone">Receipt</button>}<button onClick={() => createFromArtifact(item)} className="rounded-md border border-softgraph bg-ink px-2 py-1 text-xs text-stone">Create queue item</button></div>
      </article>)}{!visible.length && <EmptyState title="No indexed artifacts" detail="No artifact matched the current filters." />}</div>
      <div className={`${selected ? 'fixed inset-y-0 right-0 z-30 w-full max-w-xl' : 'hidden'} border-l border-softgraph bg-graphite p-4 shadow-2xl`}><button onClick={() => { setSelected(null); setPreview(null) }} className="float-right text-taupe">Close</button><h2 className="text-lg font-semibold text-stone">{selected?.title}</h2><pre className="mt-4 max-h-[80vh] overflow-auto whitespace-pre-wrap rounded-lg border border-softgraph bg-ink p-3 text-xs text-taupe">{preview?.available ? preview.content : preview?.reason || 'Loading preview'}</pre></div>
    </>
  )
}

export function MissionControl() {
  const [watch, setWatch] = useState(null)
  useEffect(() => { getDashboardSystemWatch().then(setWatch) }, [])
  const stalled = watch?.stalled || []
  const backup = watch?.backup || {}
  const latestBackup = backup?.latest
  const schedule = watch?.schedule || []
  const davidHealth = watch?.david_health || {}
  const davidDegraded = Boolean(watch?.david_needs_attention)
  return (
    <>
      <PageHeader title="Mission Control" question="Read-only system watch: backend, queue tooling, stalled runs, and log tail." />
      <div className="grid gap-3 md:grid-cols-4">
        <div className="rounded-lg border border-softgraph bg-graphite/70 p-4"><div className="text-xs text-taupe">Backend</div><div className="mt-1 text-lg text-stone">{watch?.backend?.status || 'loading'}</div></div>
        <div className="rounded-lg border border-softgraph bg-graphite/70 p-4"><div className="text-xs text-taupe">Queue Tooling</div><div className="mt-1 text-lg text-stone">{watch?.queue_tooling?.status || 'loading'}</div></div>
        <div className="rounded-lg border border-softgraph bg-graphite/70 p-4"><div className="text-xs text-taupe">Stalled Runs</div><div className="mt-1 text-lg text-stone">{stalled.length}</div></div>
        <div className={`rounded-lg border p-4 ${davidDegraded ? 'border-clay/70 bg-clay/10' : 'border-softgraph bg-graphite/70'}`} data-testid="david-health">
          <div className="text-xs text-taupe">David</div>
          <div className="mt-1 text-lg text-stone">{davidHealth.available === false ? 'no data yet' : davidDegraded ? `${davidHealth.consecutive_failures} failures in a row` : 'healthy'}</div>
          {davidDegraded && davidHealth.last_failure ? (
            <div className="mt-1 text-xs text-clay">Last: {davidHealth.last_failure.failure_class || davidHealth.last_failure.stage || 'unknown'} · {davidHealth.last_failure.timestamp || ''}</div>
          ) : null}
        </div>
      </div>
      <section className={`mt-4 rounded-lg border p-4 ${backup?.needs_attention ? 'border-clay/70 bg-clay/10' : 'border-softgraph bg-graphite/70'}`}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-stone">Automated Backup</h2>
            <div className="mt-1 text-xs text-taupe">{backup?.token_usage_text || 'Token usage: no agent invocation'}</div>
          </div>
          <StatusChip status={backup?.needs_attention ? 'Blocked' : 'Done'}>{backup?.state || 'loading'}</StatusChip>
        </div>
        <div className="mt-3 grid gap-2 text-xs text-taupe md:grid-cols-2">
          <div><span className="text-stone">Last:</span> {latestBackup?.ts || 'no backup receipts yet'}</div>
          <div><span className="text-stone">Target:</span> {latestBackup?.target || 'D:\\TTROS_Backups'}</div>
          <div className="md:col-span-2"><span className="text-stone">Snapshot:</span> {latestBackup?.snapshot_path || 'unavailable'}</div>
          <div className="md:col-span-2"><span className="text-stone">Receipt:</span> {backup?.latest_receipt_path || 'queue/receipts/backups.jsonl'}</div>
          <div className="md:col-span-2"><span className="text-stone">Log:</span> {backup?.latest_log_path || 'unavailable'}</div>
        </div>
      </section>
      <section className="mt-4 rounded-lg border border-softgraph bg-graphite/70 p-4" data-testid="schedule-view">
        <div className="mb-3">
          <h2 className="text-sm font-semibold text-stone">Read-only Schedule</h2>
          <p className="mt-1 text-xs text-taupe">Existing status and receipt evidence only. Unknown next-run times are not inferred; this view cannot trigger, edit, register, or schedule work.</p>
        </div>
        <div className="space-y-2">
          {schedule.map(row => (
            <div key={row.id} className={`grid gap-2 rounded-lg border p-3 text-xs md:grid-cols-[1fr_.8fr_1fr_1.4fr_.7fr] ${row.stale ? 'border-clay/70 bg-clay/10' : 'border-softgraph bg-ink'}`} data-schedule-row={row.id}>
              <span className="font-semibold text-stone">{row.name}</span>
              <span><strong className="text-taupe">Next:</strong> {row.next_run || 'unknown'}</span>
              <span><strong className="text-taupe">Last:</strong> {row.last_run || 'unknown'}</span>
              <span className="truncate"><strong className="text-taupe">Receipt:</strong> {row.receipt || 'unavailable'}</span>
              <StatusChip status={row.stale || row.degraded ? 'Blocked' : 'Done'}>{row.stale ? 'stale' : row.degraded ? 'degraded' : row.last_result}</StatusChip>
              <span className="md:col-span-5 text-taupe">Expected cadence: {row.expected_cadence || 'unknown'} · stale flag: {row.stale ? 'evidence-backed' : 'not asserted'}</span>
            </div>
          ))}
          {!schedule.length && <EmptyState title="Schedule unavailable" detail="No existing status evidence was returned." />}
        </div>
      </section>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-softgraph bg-graphite/70 p-4"><h2 className="text-sm font-semibold text-stone">Stalled</h2><div className="mt-3 space-y-2">{stalled.map(item => <RowButton key={item.id} title={item.title} meta={`${item.id} · ${item.stalled_minutes} min`} right={<StatusChip status="Blocked">stalled</StatusChip>} />)}{!stalled.length && <EmptyState title="No stalled runs" detail="No claimed/running queue item exceeded the configured window." />}</div></div>
        <pre className="max-h-96 overflow-auto rounded-lg border border-softgraph bg-ink p-3 text-xs text-taupe">{(watch?.error_log_tail || []).join('\n') || 'No log tail available.'}</pre>
      </div>
    </>
  )
}
