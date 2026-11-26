// Revisit: when the authorised Graphify inventory contract changes. Last touched: 2026-11-09.
// Project only supplied connections; never infer relationships from layout proximity.
export function projectBrainGraph(documents) {
  const nodes = documents.map(doc => ({ id: doc.path, title: doc.title, document: doc }))
  const byId = new Map(nodes.map(node => [node.id, node]))
  const edges = []
  for (const doc of documents) {
    for (const link of doc.connections) {
      if (link.path && link.direction === 'incoming') continue
      const target = link.path || link.activity_id
      if (!target) continue
      if (!byId.has(target) && link.activity_id) {
        const activity = { id: target, title: link.title, activity: true }
        nodes.push(activity)
        byId.set(target, activity)
      }
      if (!byId.has(target)) continue
      edges.push({ ...link, source: link.direction === 'incoming' ? target : doc.path,
        target: link.direction === 'incoming' ? doc.path : target, suggested: false })
    }
    for (const link of doc.suggestions) {
      if (!byId.has(link.path) || doc.path > link.path) continue
      edges.push({ ...link, source: doc.path, target: link.path, suggested: true })
    }
  }
  const degree = new Map(nodes.map(node => [node.id, 0]))
  for (const edge of edges) if (!edge.suggested) {
    degree.set(edge.source, degree.get(edge.source) + 1)
    degree.set(edge.target, degree.get(edge.target) + 1)
  }
  return { nodes: nodes.map(node => ({ ...node, degree: degree.get(node.id) })), edges }
}

export function filterBrainGraph(graph, coverage, relation, showSuggestions) {
  const nodes = graph.nodes.filter(node => coverage === 'all' || (coverage === 'connected' ? node.degree > 0 : !node.activity && node.degree === 0 && node.document.graph_status !== 'unknown'))
  const ids = new Set(nodes.map(node => node.id))
  return { nodes, edges: graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)
    && (!relation || edge.relation === relation) && (showSuggestions || !edge.suggested)) }
}

// Bounded deterministic layout, computed once per payload. Local grid repulsion
// avoids all-pairs work; filters/search/selection reuse these same coordinates.
export function layoutBrainGraph(graph) {
  const spacing = 34, side = Math.ceil(Math.sqrt(graph.nodes.length))
  const positions = new Map(graph.nodes.map((node, index) => [node.id,
    { x: (index % side) * spacing, y: Math.floor(index / side) * spacing }]))
  for (let step = 0; step < 110; step++) {
    const grid = new Map(), forces = new Map()
    for (const [id, p] of positions) {
      const key = `${Math.floor(p.x / 60)},${Math.floor(p.y / 60)}`
      if (!grid.has(key)) grid.set(key, [])
      grid.get(key).push(id)
      forces.set(id, { x: 0, y: 0 })
    }
    for (const [id, p] of positions) {
      const gx = Math.floor(p.x / 60), gy = Math.floor(p.y / 60), force = forces.get(id)
      for (let x = gx - 1; x <= gx + 1; x++) for (let y = gy - 1; y <= gy + 1; y++) {
        for (const other of grid.get(`${x},${y}`) || []) {
          if (id === other) continue
          const q = positions.get(other), dx = p.x - q.x, dy = p.y - q.y
          const distance = Math.max(1, Math.hypot(dx, dy))
          force.x += dx / distance * 130 / distance
          force.y += dy / distance * 130 / distance
        }
      }
    }
    for (const edge of graph.edges) {
      if (edge.suggested) continue
      const a = positions.get(edge.source), b = positions.get(edge.target)
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy))
      const strength = (distance - 65) * 0.025
      for (const [id, sign] of [[edge.source, 1], [edge.target, -1]]) {
        forces.get(id).x += sign * dx / distance * strength
        forces.get(id).y += sign * dy / distance * strength
      }
    }
    for (const [id, p] of positions) {
      const force = forces.get(id)
      p.x += Math.max(-5, Math.min(5, force.x))
      p.y += Math.max(-5, Math.min(5, force.y))
    }
  }
  return positions
}
