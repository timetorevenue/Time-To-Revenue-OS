// Revisit: when Graphify graph interaction or theme changes. Last touched: 2026-11-09.
import { useEffect, useMemo, useRef, useState } from 'react'
import { ActionButton } from '../components/DashboardKit'
import { filterBrainGraph, layoutBrainGraph, projectBrainGraph } from '../brainGraph'

const WIDTH = 1000, HEIGHT = 560
export default function BrainNetwork({ documents, selectedPath, onSelect, graphAvailable }) {
  const graph = useMemo(() => projectBrainGraph(documents), [documents])
  const positions = useMemo(() => layoutBrainGraph(graph), [graph])
  const [coverage, setCoverage] = useState('all'), [relation, setRelation] = useState('')
  const [suggestions, setSuggestions] = useState(true), [query, setQuery] = useState('')
  const [camera, setCamera] = useState(null), [activity, setActivity] = useState(null)
  const svgRef = useRef(null), drag = useRef(null), moved = useRef(false)
  useEffect(() => { setActivity(null) }, [selectedPath])
  const visible = useMemo(() => filterBrainGraph(graph, coverage, relation, suggestions), [graph, coverage, relation, suggestions])
  const relations = useMemo(() => [...new Set(graph.edges.map(edge => edge.relation))].sort(), [graph])
  const fit = useMemo(() => {
    const points = visible.nodes.map(node => positions.get(node.id))
    if (!points.length) return { x: 0, y: 0, scale: 1 }
    const xs = points.map(p => p.x), ys = points.map(p => p.y)
    const left = Math.min(...xs), top = Math.min(...ys)
    const width = Math.max(...xs) - left, height = Math.max(...ys) - top
    const scale = Math.min(2, (WIDTH - 70) / (width + 20), (HEIGHT - 70) / (height + 20))
    return { x: WIDTH / 2 - (left + width / 2) * scale, y: HEIGHT / 2 - (top + height / 2) * scale, scale }
  }, [visible.nodes, positions])
  const overviewLabels = useMemo(() => new Set([...graph.nodes].filter(n => n.degree > 2).sort((a, b) => b.degree - a.degree).slice(0, 6).map(n => n.id)), [graph])
  const view = camera || fit
  const selectedId = activity?.id || selectedPath
  const neighbours = useMemo(() => {
    const ids = new Set([selectedId])
    for (const edge of visible.edges) if (edge.source === selectedId || edge.target === selectedId) { ids.add(edge.source); ids.add(edge.target) }
    return ids
  }, [visible.edges, selectedId])
  const matches = useMemo(() => query.trim() ? visible.nodes.filter(node => `${node.title} ${node.id}`.toLowerCase().includes(query.trim().toLowerCase())) : [], [visible.nodes, query])
  const zoom = (factor, x = WIDTH / 2, y = HEIGHT / 2) => setCamera(current => {
    const v = current || fit, scale = Math.max(0.12, Math.min(12, v.scale * factor)), ratio = scale / v.scale
    return { scale, x: x - (x - v.x) * ratio, y: y - (y - v.y) * ratio }
  })
  useEffect(() => {
    const svg = svgRef.current
    const wheel = event => {
      event.preventDefault()
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse())
      zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15, point.x, point.y)
    }
    svg.addEventListener('wheel', wheel, { passive: false })
    return () => svg.removeEventListener('wheel', wheel)
  }, [fit])
  const select = node => { if (node.document) { setActivity(null); onSelect(node.id) } else setActivity(node) }
  const fitConnections = id => {
    const ids = new Set([id])
    for (const edge of visible.edges) if (edge.source === id || edge.target === id) { ids.add(edge.source); ids.add(edge.target) }
    const points = [...ids].map(key => positions.get(key)).filter(Boolean)
    if (!points.length) return
    const xs = points.map(p => p.x), ys = points.map(p => p.y)
    const left = Math.min(...xs), top = Math.min(...ys), width = Math.max(...xs) - left, height = Math.max(...ys) - top
    const scale = Math.min(2.5, (WIDTH - 100) / (width + 30), (HEIGHT - 100) / (height + 30))
    setCamera({ scale, x: WIDTH / 2 - (left + width / 2) * scale, y: HEIGHT / 2 - (top + height / 2) * scale })
  }
  const focus = node => { fitConnections(node.id); select(node) }
  const changeFilter = setter => event => { setter(event.target.value); setCamera(null) }
  return <div className="my-4 rounded-lg border border-softgraph bg-ink p-3" data-testid="brain-network">
    <h3 className="font-semibold text-stone">Knowledge graph</h3>
    <p className="my-2 text-xs text-taupe">{visible.nodes.filter(n => n.document).length} / {documents.length} documents · {visible.nodes.filter(n => n.activity).length} activities · {visible.edges.filter(e => !e.suggested).length} published / {visible.edges.filter(e => e.suggested).length} suggested edges shown. All documents included by default; layout proximity implies no relationship.</p>
    {!graphAvailable && <p role="alert" className="text-sm text-clay">Published graph unavailable. Document coverage is unknown; existing suggestions remain unconfirmed.</p>}
    <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-stone">
      <input className="rounded-lg border border-softgraph bg-graphite p-2" aria-label="Search graph" placeholder="Find title or path" value={query} onChange={event => setQuery(event.target.value)} />
      <select className="rounded-lg border border-softgraph bg-graphite p-2" aria-label="Graph document filter" value={coverage} onChange={changeFilter(setCoverage)}><option value="all">All documents</option><option value="connected">Published connections</option><option value="unlinked">Unlinked documents</option></select>
      <select className="rounded-lg border border-softgraph bg-graphite p-2" aria-label="Graph relationship type" value={relation} onChange={changeFilter(setRelation)}><option value="">All relationship types</option>{relations.map(value => <option key={value}>{value}</option>)}</select>
      <label className="text-xs"><input type="checkbox" checked={suggestions} onChange={event => setSuggestions(event.target.checked)} /> Show suggested links</label>
      <ActionButton onClick={() => zoom(1.4)}>Zoom in</ActionButton><ActionButton onClick={() => zoom(1 / 1.4)}>Zoom out</ActionButton><ActionButton onClick={() => setCamera(null)}>Fit / reset</ActionButton><ActionButton disabled={!selectedId} onClick={() => fitConnections(selectedId)}>Fit connections</ActionButton>
    </div>
    {query.trim() && <div className="mb-2 max-h-28 overflow-auto text-xs text-champagne" aria-label="Graph search results"><p>{matches.length} matches</p>{matches.map(node => <button key={node.id} className="block p-1 text-left hover:underline" onClick={() => focus(node)}>{node.title} · {node.id}</button>)}</div>}
    <p className="mb-2 text-xs text-taupe"><span style={{ color: '#c8b896' }}>●</span> Connected document · <span style={{ color: '#8a907c' }}>●</span> Unlinked document · ◇ Activity · Solid: published · <span style={{ color: '#d59a72' }}>Dashed: unconfirmed suggestion</span>. Drag to pan; scroll to zoom. Select nodes or use search results; document details and Open in Memory appear below.</p>
    <svg ref={svgRef} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full rounded-lg border border-softgraph" style={{ height: 'min(60vh, 560px)', minHeight: 320, touchAction: 'none', background: '#151718', cursor: 'grab' }} aria-label="Business Brain knowledge graph" data-testid="brain-network-svg"

      onPointerDown={event => { if (event.button !== 0) return; const matrix = event.currentTarget.getScreenCTM().inverse(); const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix); drag.current = { x: point.x, y: point.y, view, matrix }; moved.current = false }}
      onPointerMove={event => { if (!drag.current) return; const d = drag.current, point = new DOMPoint(event.clientX, event.clientY).matrixTransform(d.matrix), dx = point.x - d.x, dy = point.y - d.y; if (Math.abs(dx) + Math.abs(dy) > 4) { moved.current = true; event.currentTarget.setPointerCapture(event.pointerId) } if (moved.current) setCamera({ ...d.view, x: d.view.x + dx, y: d.view.y + dy }) }}
      onPointerUp={event => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }} onPointerCancel={() => { drag.current = null }}>
      <g data-testid="brain-network-camera" transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
        {visible.edges.map((edge, index) => { const a = positions.get(edge.source), b = positions.get(edge.target), active = edge.source === selectedId || edge.target === selectedId
          return <line key={index} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={edge.suggested ? '#d59a72' : '#9daea8'} strokeWidth={active ? 2 : 0.8} vectorEffect="non-scaling-stroke" strokeDasharray={edge.suggested ? '5 4' : undefined} opacity={selectedId ? active ? 1 : 0.06 : edge.suggested ? 0.2 : 0.35} data-highlighted={active} data-suggested={edge.suggested}><title>{edge.relation} · {edge.suggested ? 'unconfirmed suggestion' : `published · ${edge.extractor || ''} · ${edge.confidence || ''}`} {edge.stale ? '· stale evidence' : ''}</title></line> })}
        {visible.nodes.map(node => { const p = positions.get(node.id), active = selectedId === node.id
          return <g key={node.id} transform={`translate(${p.x} ${p.y})`} opacity={selectedId && !neighbours.has(node.id) ? 0.25 : 1} data-testid="brain-network-node" data-node-id={node.id} role="button" tabIndex={0} aria-label={node.title} aria-pressed={active} onClick={() => { if (!moved.current) select(node) }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node) } }} style={{ cursor: 'pointer' }}>
            <circle r={active ? 8 : Math.min(9, 4 + Math.sqrt(node.degree) * 0.35)} fill={active || matches.includes(node) ? '#f2d59b' : node.activity ? '#9daecb' : node.degree ? '#c8b896' : '#8a907c'} stroke={active ? '#fff' : '#151718'} strokeWidth={1} />
            {node.activity && <path d="M 0 -7 L 7 0 L 0 7 L -7 0 Z" fill="none" stroke="#9daecb" />}
            <title>{node.title}\n{node.id}\n{node.degree} published connections</title>
            {(active || (!selectedId && overviewLabels.has(node.id)) || (view.scale > 1.7 && neighbours.has(node.id))) && <text x="10" y="4" fill="#eee5d5" stroke="#151718" strokeWidth="2" paintOrder="stroke" fontSize="10">{node.title.slice(0, 65)}</text>}
          </g> })}
      </g>
    </svg>
    {!visible.nodes.length && <p role="status" className="p-3 text-sm text-taupe">No nodes match these filters.</p>}
    {selectedPath && !visible.nodes.some(n => n.id === selectedPath) && <p className="mt-2 text-xs text-taupe">Selected document is outside the graph filters; its details remain below.</p>}
    {activity && <div className="mt-2 text-xs text-stone">Activity: {activity.title} · {activity.id}. Published activity references; activities have no Memory document. <button className="text-champagne" onClick={() => setActivity(null)}>Clear activity selection</button></div>}
  </div>
}
