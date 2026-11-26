// Website work-order input contract. Revisit on schema changes. Last touched: 2026-11-16.
const lines = text => text.split('\n').map(row => row.trim()).filter(Boolean)
export function preparationInput(form, project) {
  return {
    project: form.project, repository: project?.repository || '', client_scope: project?.client_scope || '',
    request: form.request, branch: form.branch, affected_paths: lines(form.affected), source_references: lines(form.sources),
    seo_strategy: form.seo_strategy || null,
    acceptance_checks: lines(form.checks).map(line => {
      const at = line.indexOf(' :: ')
      if (at < 1 || !line.slice(at + 4).trim()) throw new Error('Each acceptance check needs path :: expected text')
      return { path: line.slice(0, at).trim(), contains: line.slice(at + 4).trim() }
    }),
  }
}
