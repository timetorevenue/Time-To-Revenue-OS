export const ASK_DAVID_THREAD_STORAGE_KEY = 'aos.dashboard.david.thread.v1'
export const ASK_DAVID_DRAFT_STORAGE_KEY = 'aos.dashboard.david.draft.v1'

export function loadStoredThread(storage) {
  try {
    const raw = storage?.getItem(ASK_DAVID_THREAD_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function persistThread(storage, thread) {
  try {
    storage?.setItem(ASK_DAVID_THREAD_STORAGE_KEY, JSON.stringify(thread || []))
  } catch {
    // sessionStorage unavailable or full; visible history simply won't persist.
  }
}

export function loadStoredDraft(storage) {
  try {
    const raw = storage?.getItem(ASK_DAVID_DRAFT_STORAGE_KEY)
    return typeof raw === 'string' ? raw : ''
  } catch {
    return ''
  }
}

export function persistDraft(storage, draft) {
  try {
    if (draft) {
      storage?.setItem(ASK_DAVID_DRAFT_STORAGE_KEY, draft)
    } else {
      storage?.removeItem(ASK_DAVID_DRAFT_STORAGE_KEY)
    }
  } catch {
    // sessionStorage unavailable or full; the draft simply won't persist.
  }
}

const isCount = value => Number.isInteger(value) && value >= 0

// Compact provenance line built only from backend-structured fields (never the answer text).
export function replyMetaParts(entry) {
  const parts = []
  const executor = entry?.executor || entry?.workbench?.executor
  if (executor) parts.push(`Executor: ${executor}`)
  if (entry?.selected_route) parts.push(`Route: ${entry.selected_route}`)
  const usage = entry?.token_usage
  if (usage?.available && isCount(usage.total_tokens)) {
    const detail = [isCount(usage.input_tokens) ? `in ${usage.input_tokens}` : '', isCount(usage.output_tokens) ? `out ${usage.output_tokens}` : '']
      .filter(Boolean).join(' / ')
    parts.push(`Tokens: ${usage.total_tokens}${detail ? ` (${detail})` : ''}`)
  } else {
    parts.push('Tokens: unavailable')
  }
  const cost = usage?.total_cost_usd
  if (typeof cost === 'number' && Number.isFinite(cost)) parts.push(`Cost: $${cost.toFixed(4)}`)
  return parts
}


export function replyArtifactLinks(entry) {
  const receipts = Array.isArray(entry?.capability_receipts) ? entry.capability_receipts : []
  const items = new Map()
  for (const receipt of receipts) {
    if (receipt?.action !== 'execute') continue
    const candidates = receipt.capability === 'browser.session'
      ? (Array.isArray(receipt.result?.artifacts) ? receipt.result.artifacts : [])
      : ['files.write', 'files.pdf', 'files.deliver', 'computer.preview'].includes(receipt.capability)
        ? [receipt.result] : []
    for (const item of candidates) {
      if (typeof item?.id !== 'string' || !/^[a-f0-9]{32}$/.test(item.id)) continue
      if (typeof item.filename !== 'string' || !item.filename || typeof item.dashboard_download !== 'string') continue
      const prefix = `/api/david/artifacts/${item.id}?conversation_id=`
      if (!item.dashboard_download.startsWith(prefix) || !/^[a-zA-Z0-9_-]{1,160}$/.test(item.dashboard_download.slice(prefix.length))) continue
      items.set(item.id, { id: item.id, filename: item.filename, href: item.dashboard_download })
    }
  }
  return [...items.values()]
}
