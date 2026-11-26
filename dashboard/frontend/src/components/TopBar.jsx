import { MoreHorizontal, RefreshCw, Search } from 'lucide-react'
import { useState } from 'react'
import { launcherPrompt } from '../launcherPrompts'
import { VIEW_META } from '../shellState'
export default function TopBar({
  currentView,
  backendOk,
  onNavigate,
  onRefresh,
  onCapture,
}) {
  const [copied, setCopied] = useState('')
  const copyPrompt = async (target) => {
    try {
      await navigator.clipboard.writeText(launcherPrompt(target))
      setCopied(`Copied ${target}`)
    } catch {
      setCopied('Clipboard unavailable')
    }
  }
  const labels = {
    cockpit: 'Ask David',
    search: 'Search & Memory',
    results: 'Results & Receipts',
  }
  return (
    <header className="utility-bar">
      <div className="breadcrumb">
        <span>Workspace</span>
        <span>/</span>
        <strong>
          {labels[currentView] ||
            VIEW_META[currentView]?.label ||
            'Company Home'}
        </strong>
      </div>
      <div className="utility-actions">
        <button
          className="utility-search"
          aria-label="Search your business"
          onClick={() => onNavigate('search')}
        >
          <Search size={14} />
          <span>Search your business</span>
          <kbd>/</kbd>
        </button>
        <span className="utility-date">
          {new Intl.DateTimeFormat('en-CA', {
            timeZone: 'America/Vancouver',
            weekday: 'short',
            month: 'short',
            day: 'numeric',
          }).format(new Date())}
        </span>
        <button
          className="utility-refresh"
          onClick={onRefresh}
          aria-label="Refresh dashboard"
          title={
            backendOk === true
              ? 'API reachable'
              : backendOk === false
                ? 'API unavailable'
                : 'Checking API'
          }
        >
          <RefreshCw size={15} />
        </button>
        <button className="capture-toggle" onClick={onCapture}>
          Capture note
        </button>
        <details className="workspace-tools">
          <summary aria-label="Workspace tools">
            <MoreHorizontal size={17} />
          </summary>
          <div>
            <button onClick={onCapture}>Capture a note</button>
            <button onClick={() => copyPrompt('codex')}>
              Copy Codex prompt
            </button>
            <button onClick={() => copyPrompt('claude-code')}>
              Copy Claude Code prompt
            </button>
            <p role="status">
              {copied ||
                (backendOk === true
                  ? 'API reachable'
                  : backendOk === false
                    ? 'API unavailable'
                    : 'Checking API…')}
            </p>
          </div>
        </details>
        <span className="operator-avatar" aria-label="Casey workspace">
          LD
        </span>
      </div>
    </header>
  )
}
