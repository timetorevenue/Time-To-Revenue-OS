// Revisit: when the Brain graph projection changes. Last touched: 2026-11-09.
import test from 'node:test'
import assert from 'node:assert/strict'
import { projectBrainGraph, filterBrainGraph, layoutBrainGraph } from '../src/brainGraph.js'
const doc = (path, connections = [], suggestions = []) => ({ path, title: path, connections, suggestions, graph_status: 'current' })
const documents = [doc('a', [{ path: 'b', direction: 'outgoing', relation: 'wiki', extractor: 'wiki' }, { activity_id: 'activity:1', title: 'Activity', direction: 'incoming', relation: 'touched' }], [{ path: 'c', relation: 'related', stale: true }]), doc('b', [{ path: 'a', direction: 'incoming', relation: 'wiki' }]), doc('c', [], [{ path: 'a', relation: 'related', stale: true }]), doc('d'), doc('unknown')]
documents[4].graph_status = 'unknown'
test('projection preserves documents, directed edges, activities and suggestions', () => {
  const graph = projectBrainGraph(documents)
  assert.equal(graph.nodes.length, 6)
  assert.equal(graph.edges.length, 3)
  assert.equal(graph.edges[0].extractor, 'wiki')
  assert.deepEqual(graph.edges.map(e => [e.source, e.target, e.suggested]), [['a', 'b', false], ['activity:1', 'a', false], ['a', 'c', true]])
  assert.equal(graph.edges[2].stale, true)
  assert.equal(graph.nodes.find(n => n.id === 'c').degree, 0)
})
test('filters retain coverage and distinguish unknown from unlinked', () => {
  const graph = projectBrainGraph(documents)
  assert.deepEqual(filterBrainGraph(graph, 'unlinked', '', true).nodes.map(n => n.id), ['c', 'd'])
  assert.equal(filterBrainGraph(graph, 'all', 'wiki', true).nodes.length, 6)
  assert.equal(filterBrainGraph(graph, 'all', 'wiki', true).edges.length, 1)
  assert.equal(filterBrainGraph(graph, 'all', '', false).edges.length, 2)
  assert.equal(filterBrainGraph(graph, 'connected', '', true).nodes.length, 3)
})
test('projection drops references outside the authorised inventory', () => {
  const graph = projectBrainGraph([doc('a', [{ path: 'private', direction: 'outgoing' }], [{ path: 'private' }])])
  assert.equal(graph.edges.length, 0)
  assert.equal(graph.nodes.length, 1)
})
test('bounded layout is deterministic and includes isolated nodes at realistic coverage', () => {
  const docs = Array.from({ length: 850 }, (_, i) => doc(String(i), i ? [{ path: String(i - 1), direction: 'outgoing', relation: 'wiki' }] : []))
  const graph = projectBrainGraph(docs), start = performance.now(), first = layoutBrainGraph(graph)
  assert.equal(first.size, 850)
  assert.ok([...first.values()].every(p => Number.isFinite(p.x) && Number.isFinite(p.y)))
  assert.ok(performance.now() - start < 5000)
  assert.deepEqual(first, layoutBrainGraph(graph))
  assert.equal(layoutBrainGraph(projectBrainGraph([])).size, 0)
})
