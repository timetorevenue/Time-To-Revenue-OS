// Revisit: when Dashboard coverage contracts change. Last touched: 2026-11-09.
import { useMemo, useState } from 'react'
import { ActionButton, EmptyState } from '../components/DashboardKit'
import { memoryRecordPath } from '../shellState'
import BrainNetwork from './BrainNetwork'

const count = value => value ?? 'unknown'
const when = value => value ? new Date(value).toLocaleString() : 'unavailable'
const panel = 'mb-4 rounded-lg border border-softgraph bg-graphite/70 p-4'

export function SystemCoverage({ data }) {
  if (!data) return <EmptyState title="Coverage unavailable" detail="Refresh to read the existing system indexes." />
  const brain = data.business_brain
  return <section className={panel} data-testid="graphify-system-overview">
    <h2 className="text-lg font-semibold text-stone">System Overview</h2>
    <p className="mt-1 text-xs text-taupe">Checked {when(data.checked_at)}. Counts cover authorised global sources. Repository graphs are ingested snapshots.</p>
    {data.warnings.map(warning => <p key={warning} role="alert" className="mt-2 text-sm text-clay">{warning}</p>)}
    <div className="my-4 grid gap-3 md:grid-cols-4">
      {[["Brain documents", brain.total], ['Search indexed', brain.search_indexed], ['Graph documents', brain.graph_documents], ['Unlinked in graph', brain.unlinked]].map(([label, value]) => <div key={label} className="rounded-lg border border-softgraph bg-ink p-3"><div className="text-xs text-champagne">{label}</div><strong className="text-xl text-stone">{count(value)}</strong></div>)}
    </div>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm text-stone"><thead className="text-xs text-champagne"><tr><th className="p-2">System source</th><th>Documents</th><th>Latest document indexed</th></tr></thead><tbody>{data.sources.map(source => <tr key={source.source} className="border-t border-softgraph"><td className="p-2">{source.source}</td><td>{count(source.documents)}</td><td>{when(source.last_indexed)}</td></tr>)}</tbody></table></div>
    <p className="mt-3 text-xs text-taupe">Search: {count(brain.search_indexed == null ? null : brain.total - brain.search_indexed)} unindexed, {count(brain.search_stale)} stale, {count(brain.search_inventory_gap)} indexed rows outside the current file inventory · Graph: {count(brain.graph_documents == null ? null : brain.total - brain.graph_documents)} unindexed, {count(brain.graph_stale)} stale · Entity Index: {count(brain.entity_documents)} documents, built {when(brain.entity_built)} · Graph published {when(brain.graph_built)}</p>
    <p className="mt-2 text-xs text-taupe">{data.provenance} “Latest indexed” is a document timestamp, not proof of a complete scan. Current/stale compares each Brain file with its Search metadata and graph content hash.</p>
  </section>
}

export function RepositoryCoverage({ data }) {
  return <section className={panel} data-testid="graphify-repository-coverage">
    <h2 className="text-lg font-semibold text-stone">System repository coverage</h2>
    <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm text-stone"><thead className="text-xs text-champagne"><tr><th className="p-2">Repository</th><th>Search documents</th><th>Code graph</th><th>Document graph</th></tr></thead><tbody>{(data?.repository_coverage || []).map(repo => <tr key={repo.repository} className="border-t border-softgraph"><td className="p-2">{repo.repository}</td><td>{count(repo.search_documents)}</td><td>{repo.code_graph}{repo.graph_commit && <div className="text-xs text-taupe">{repo.graph_commit.slice(0, 10)} · {when(repo.graph_built)}</div>}</td><td>{repo.document_graph}</td></tr>)}</tbody></table></div>
    <p className="mt-3 text-xs text-taupe">System repositories are the configured TTROS and Business Brain sources. The ingested repositories below are separately published code graphs; they do not imply Search coverage of the whole system. Example Dealership and private runtime sources remain excluded.</p>
  </section>
}

export function BrainInventory({ data }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [folder, setFolder] = useState('')
  const [page, setPage] = useState(0)
  const [selectedPath, setSelectedPath] = useState('')
  const documents = data?.documents || []
  const byPath = useMemo(() => new Map(documents.map(doc => [doc.path, doc])), [documents])
  const folders = useMemo(() => [...new Set(documents.map(doc => doc.folder))].sort(), [documents])
  const rows = useMemo(() => documents.filter(doc => (!folder || doc.folder === folder)
    && `${doc.title} ${doc.path}`.toLowerCase().includes(query.toLowerCase())
    && (filter === 'all' || (filter === 'unlinked' && !doc.connections.length && doc.graph_status !== 'unknown')
      || (filter === 'unindexed' && doc.search_status === 'unindexed')
      || (filter === 'no-graph' && doc.graph_status === 'unindexed')
      || (filter === 'stale' && (doc.search_status === 'stale' || doc.graph_status === 'stale')))), [documents, query, filter, folder])
  const selected = byPath.get(selectedPath)
  const lastPage = Math.max(0, Math.ceil(rows.length / 40) - 1)
  const currentPage = Math.min(page, lastPage)
  const selectDocument = path => setSelectedPath(path)
  const connection = (link, index, suggested = false) => <li key={`${link.path || link.title}-${link.relation}-${index}`} className="rounded-lg border border-softgraph bg-ink p-2 text-xs text-taupe">
    {link.path ? <button onClick={() => selectDocument(link.path)} className="text-left font-semibold text-champagne hover:underline">{byPath.get(link.path)?.title || link.path}</button> : <strong>{link.title}</strong>}
    <div>{link.relation} · {suggested ? 'suggested · derived' : `${link.direction} · ${link.edge_kind || 'published'}`} {link.stale ? '· stale source evidence' : ''}</div>
    {!suggested && <div>{link.confidence} · {link.extractor}</div>}
    {suggested && <div>{link.reasons?.join(' · ')}</div>}
  </li>
  return <section className={panel} data-testid="graphify-brain-inventory">
    <h2 className="text-lg font-semibold text-stone">Business Brain</h2>
    <p className="mt-1 text-xs text-taupe">All authorised documents, including those absent from Search or the graph. Search titles and paths below; open a record in the existing Memory reader.</p>
    <BrainNetwork documents={documents} selectedPath={selectedPath} onSelect={selectDocument} graphAvailable={data?.graph_documents != null} />
    <div className="my-4 flex flex-wrap gap-3">
      <input aria-label="Search Business Brain inventory" placeholder="Search document titles or paths" value={query} onChange={event => { setQuery(event.target.value); setPage(0) }} className="min-w-64 flex-1 rounded-lg border border-softgraph bg-ink p-2 text-sm text-stone" />
      <select aria-label="Brain folder" value={folder} onChange={event => { setFolder(event.target.value); setPage(0) }} className="max-w-full rounded-lg border border-softgraph bg-ink p-2 text-sm text-stone"><option value="">All folders</option>{folders.map(value => <option key={value} value={value}>{value}</option>)}</select>
      <select aria-label="Brain coverage filter" value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }} className="rounded-lg border border-softgraph bg-ink p-2 text-sm text-stone">{[['all', 'All documents'], ['unlinked', 'Unlinked in graph'], ['unindexed', 'Not Search indexed'], ['no-graph', 'Not graph indexed'], ['stale', 'Stale Search or graph']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
    </div>
    <p className="mb-3 text-xs text-taupe">{rows.length} of {documents.length} documents · {count(data?.document_edges)} published document connections · {count(data?.suggested_edges)} existing suggested links. Heading containment is excluded from connection counts. Suggestions remain unconfirmed.</p>
    <div className="grid gap-4 xl:grid-cols-2">
      <div><div className="space-y-2">{rows.slice(currentPage * 40, (currentPage + 1) * 40).map(doc => <button key={doc.path} onClick={() => selectDocument(doc.path)} aria-pressed={doc.path === selectedPath} className={`w-full rounded-lg border p-3 text-left ${doc.path === selectedPath ? 'border-champagne bg-champagne/10' : 'border-softgraph bg-ink'}`} data-testid="graphify-brain-document"><strong className="text-sm text-stone">{doc.title}</strong><div className="break-all text-xs text-taupe">{doc.path}</div><div className="mt-1 text-xs text-champagne">Search: {doc.search_status} · Graph: {doc.graph_status} · {doc.connections.length} connections · {doc.suggestions.length} suggested</div></button>)}</div>
        {!rows.length && <EmptyState title="No matching documents" detail="Change the search, folder, or coverage filter." />}
        <div className="mt-3 flex items-center gap-3"><ActionButton disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</ActionButton><span className="text-xs text-taupe">Page {currentPage + 1} of {lastPage + 1}</span><ActionButton disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</ActionButton></div>
      </div>
      <div className="min-w-0 xl:sticky xl:top-4 xl:max-h-[85vh] xl:self-start xl:overflow-auto" data-testid="graphify-brain-detail">{selected ? <>
        <h3 className="font-semibold text-stone">{selected.title}</h3><p className="my-2 break-all text-xs text-taupe">{selected.path}</p>
        <a className="inline-block rounded-lg border border-softgraph bg-ink px-3 py-2 text-sm text-champagne" href={memoryRecordPath(selected.path)}>Open in Memory</a>
        <p className="my-3 text-xs text-taupe">Modified {when(selected.modified)} · Indexed {when(selected.indexed_at)} · Search {selected.search_status} · Graph {selected.graph_status} · Entity Index {selected.entity_indexed == null ? 'unknown' : selected.entity_indexed ? 'present' : 'absent'}</p>
        <h4 className="mb-2 text-sm text-stone">Published graph connections</h4><ul className="space-y-2">{selected.connections.map((link, index) => connection(link, index))}</ul>
        {!selected.connections.length && <p className="text-xs text-taupe">{selected.graph_status === 'unknown' ? 'Graph coverage unknown.' : 'No published document connections. This document is unlinked.'}</p>}
        <h4 className="mb-2 mt-4 text-sm text-stone">Existing suggested links</h4><ul className="space-y-2">{selected.suggestions.map((link, index) => connection(link, index, true))}</ul>
        {!selected.suggestions.length && <p className="text-xs text-taupe">No suggested links recorded.</p>}
      </> : <EmptyState title="Select a document" detail="Inspect its existing connections and open it in Memory." />}</div>
    </div>
  </section>
}
