import { useState, useEffect } from 'react'
import Sidebar, { MobileNav } from './components/Sidebar'
import TopBar from './components/TopBar'
import CaptureBox from './components/CaptureBox'
import AskDavid from './components/AskDavid'
import CompanyHome from './views/CompanyHome'
import CrmSales from './views/CrmSales'
import Websites from './views/Websites'
import Connections from './views/Connections'
import useDavidJobs from './useDavidJobs'
import MemoryBrain from './views/MemoryBrain'
import MemoryIntake from './views/MemoryIntake'
import RemotePC from './views/RemotePC'
import { GraphifyPage, RepoIngest } from './views/DashboardV1'
import DavidJobs, { DavidStatus } from './views/DavidJobs'
import { getHealth } from './api'
import { destinationForView, openSessionTab, restoreShellSession, shellPathForView, shellRouteFromPath, shellSessionSnapshot, shellViewForNavigation } from './shellState'
// Keep saved links readable while routing retained surfaces to their current APIs.
const RetiredView = () => <p role="status">This legacy view is retired. Use David, Jobs, CRM, Websites, Memory, Ingest, Graphify or Remote PC.</p>
const VIEWS = {
  'company-home': CompanyHome, cockpit: AskDavid, 'message-board': AskDavid,
  crm: CrmSales, websites: Websites, settings: Connections, 'connections-spine': Connections,
  'work-queue': DavidJobs, queue: DavidJobs, results: DavidJobs,
  'results-receipts': DavidJobs, artifacts: DavidJobs,
  search: MemoryBrain, 'memory-board': MemoryBrain, 'memory-intake': MemoryIntake,
  graphify: GraphifyPage, 'repo-ingest': RepoIngest,
  system: DavidStatus, 'mission-control': DavidStatus,
  'remote-pc': RemotePC,
}

export default function App() {
  const [restoredShell] = useState(() => {
    const raw = window.sessionStorage?.getItem('aos.dashboard.shell.v1')
    return restoreShellSession(raw, !raw && window.location.pathname === '/' ? '/home' : window.location.pathname, window.location.search)
  })
  const jobsState = useDavidJobs()
  const [captureOpen, setCaptureOpen] = useState(false)
  const [view, setView] = useState(restoredShell.view)
  const [viewParams, setViewParams] = useState(restoredShell.viewParams)
  const [sessionTabs, setSessionTabs] = useState(restoredShell.sessionTabs)
  const [backendOk, setBackendOk] = useState(null)
  const [cockpit, setCockpit] = useState(null)
  const [navCollapsed, setNavCollapsed] = useState(() => { try { return localStorage.getItem('aos.dashboard.nav-collapsed.v1') === '1' } catch { return false } })
  const toggleNavigation = () => setNavCollapsed(value => {
    try { localStorage.setItem('aos.dashboard.nav-collapsed.v1', value ? '0' : '1') } catch { /* optional preference */ }
    return !value
  })

  const applyShellView = (nextView, params = {}) => {
    setSessionTabs(current => openSessionTab(current, nextView, params))
    setView(nextView)
    setViewParams(params)
  }

  const syncShellRoute = (nextView, params = {}, replace = false) => {
    const path = shellPathForView(nextView, params)
    const state = { ...(window.history.state || {}), aosShell: { view: nextView, viewParams: params } }
    if (replace || window.location.pathname + window.location.search === path) window.history.replaceState(state, '', path)
    else window.history.pushState(state, '', path)
  }

  const navigate = (nextView, params = {}) => {
    const routedView = shellViewForNavigation(nextView, params)
    applyShellView(routedView, params)
    syncShellRoute(routedView, params)
  }

  const updateActiveViewParams = params => {
    const next = params || {}
    setViewParams(next)
    setSessionTabs(current => current.map(tab => tab.id === view ? { ...tab, params: next } : tab))
    syncShellRoute(view, next, true)
  }

  // Opening/closing a Memory record is a real history step (Back closes it).
  const pushActiveViewParams = params => {
    const next = params || {}
    setViewParams(next)
    setSessionTabs(current => current.map(tab => tab.id === view ? { ...tab, params: next } : tab))
    syncShellRoute(view, next, false)
  }

  const refreshCockpit = () => getHealth()
    .then(data => { setBackendOk(true); setCockpit(data) })
    .catch(() => { setBackendOk(false); setCockpit(null) })

  useEffect(() => {
    refreshCockpit()
    const poll = window.setInterval(refreshCockpit, 15000)
    return () => window.clearInterval(poll)
  }, [])

  useEffect(() => {
    document.getElementById('aos-main-scroll')?.scrollTo(0, 0)
  }, [view])

  useEffect(() => {
    window.sessionStorage?.setItem('aos.dashboard.shell.v1', shellSessionSnapshot(view, viewParams, sessionTabs))
  }, [view, viewParams, sessionTabs])

  useEffect(() => {
    const path = shellPathForView(restoredShell.view, restoredShell.viewParams)
    window.history.replaceState({ ...(window.history.state || {}), aosShell: { view: restoredShell.view, viewParams: restoredShell.viewParams } }, '', path)
    const onPopState = event => {
      const route = shellRouteFromPath(window.location.pathname, window.location.search)
      const saved = event.state?.aosShell
      if (route) applyShellView(route.view, route.viewParams)
      else if (saved && VIEWS[saved.view]) applyShellView(saved.view, saved.viewParams || {})
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    const isTypingTarget = target => {
      const tag = target?.tagName
      return target?.isContentEditable || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    }
    const onKeyDown = event => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return
      event.preventDefault()
      navigate('search')
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const ViewComponent = VIEWS[view] || RetiredView
  const activeDestination = destinationForView(view)

  return (
    <div className="workbench-shell" data-testid="workbench-shell">
      <a className="skip-link" href="#aos-main-scroll">Skip to content</a>
      <Sidebar collapsed={navCollapsed} onToggle={toggleNavigation} activeDestination={activeDestination} currentView={view} onNavigate={navigate} counts={cockpit?.counts || {}} needsCount={Array.isArray(cockpit?.needs_me) ? cockpit.needs_me.length : undefined} />
      <div className="workspace-content">
        <TopBar currentView={view} activeDestination={activeDestination} backendOk={backendOk} cockpit={cockpit} onNavigate={navigate} onRefresh={() => { refreshCockpit(); jobsState.refresh() }} onCapture={() => setCaptureOpen(value => !value)} />
        {captureOpen && <CaptureBox />}
        <main id="aos-main-scroll" className="main-scroll" tabIndex={-1}>
          <div className="page-content">
            {view !== 'cockpit' && view !== 'message-board' && <ViewComponent jobsState={jobsState} resultsMode={['results', 'results-receipts', 'artifacts'].includes(view)} cockpit={cockpit} initialFilters={viewParams} onViewParamsChange={updateActiveViewParams} onViewParamsPush={pushActiveViewParams} onNavigate={navigate} refresh={refreshCockpit} onRefresh={refreshCockpit} />}
            <div hidden={view !== 'cockpit' && view !== 'message-board'}><AskDavid initialFilters={view === 'cockpit' ? viewParams : {}} onNavigate={navigate} refresh={refreshCockpit} /></div>
          </div>
        </main>
      </div>
      <MobileNav currentView={view} activeDestination={activeDestination} onNavigate={navigate} counts={cockpit?.counts || {}} needsCount={Array.isArray(cockpit?.needs_me) ? cockpit.needs_me.length : undefined} />
    </div>
  )
}
