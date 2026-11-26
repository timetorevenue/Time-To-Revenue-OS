import { useRef } from 'react'
import {
  BookOpen,
  FileCheck2,
  Github,
  Globe,
  Handshake,
  House,
  Layers3,
  ListChecks,
  MessageCircle,
  Monitor,
  Network,
  Settings2,
  UploadCloud,
  X,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
const WORKSPACE = [
  { id: 'company-home', label: 'Company Home', icon: House },
  { id: 'cockpit', label: 'Ask David', icon: MessageCircle },
  { id: 'work-queue', label: 'Jobs', icon: ListChecks },
]
// Departments with a working slice; the other five are reached from Company Home.
const DEPARTMENTS = [
  { id: 'crm', label: 'CRM & Sales', icon: Handshake },
  { id: 'websites', label: 'Websites & Digital', icon: Globe },
]
const KNOWLEDGE = [
  { id: 'search', label: 'Search & Memory', icon: BookOpen },
  { id: 'graphify', label: 'Graphify', icon: Network },
  { id: 'results', label: 'Results & Receipts', icon: FileCheck2 },
]
const TOOLS = [
  { id: 'settings', label: 'Settings · Connections', icon: Settings2 },
  { id: 'memory-intake', label: 'Ingest / Add to Memory', icon: UploadCloud },
  { id: 'repo-ingest', label: 'GitHub', icon: Github },
  { id: 'system', label: 'System status', icon: Settings2 },
  { id: 'remote-pc', label: 'Remote PC', icon: Monitor },
]
export const NAV_ITEMS = [...WORKSPACE, ...DEPARTMENTS, ...KNOWLEDGE, ...TOOLS]
function Brand() {
  return (
    <div className="ttros-brand">
      <span className="brand-mark">
        <Layers3 size={22} strokeWidth={1.4} />
      </span>
      <div>
        <strong>TTROS</strong>
        <small>EXAMPLE COMPANY</small>
      </div>
    </div>
  )
}
function Navigation({ currentView, activeDestination, onNavigate }) {
  return (
    <nav aria-label="Workspace navigation">
      {[
        ['WORKSPACE', WORKSPACE],
        ['DEPARTMENTS', DEPARTMENTS],
        ['KNOWLEDGE', KNOWLEDGE],
        ['TOOLS & SYSTEM', TOOLS],
      ].map(([heading, items]) => (
        <div className="nav-group" key={heading}>
          <p>{heading}</p>
          {items.map(({ id, label, icon: Icon }) => {
            const active =
              currentView === id ||
              (!NAV_ITEMS.some((item) => item.id === currentView) &&
                activeDestination === id)
            return (
              <button
                key={id}
                aria-label={label}
                title={label}
                aria-current={active ? 'page' : undefined}
                onClick={() => onNavigate(id)}
                data-nav-destination={id}
              >
                <Icon size={16} strokeWidth={1.6} />
                <span>{label}</span>
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
export default function Sidebar(props) {
  return (
    <aside className="ttros-sidebar" data-testid="sidebar" data-collapsed={!!props.collapsed}>
      <Brand />
      <button className="sidebar-toggle" aria-label={props.collapsed ? 'Expand Dashboard navigation' : 'Collapse Dashboard navigation'} aria-expanded={!props.collapsed} onClick={props.onToggle} title={props.collapsed ? 'Expand Dashboard navigation' : 'Collapse Dashboard navigation'}>
        {props.collapsed ? <PanelLeftOpen size={18}/> : <><PanelLeftClose size={18}/><span>Collapse navigation</span></>}
      </button>
      <Navigation {...props} />
      <div className="sidebar-footer">
        <span className="status-dot" />
        Local workspace
        <small>
          Decide with context.
          <br />
          Close with evidence.
        </small>
      </div>
    </aside>
  )
}
export function MobileNav(props) {
  const dialog = useRef(null)
  return (
    <>
      <nav
        className="mobile-navigation"
        aria-label="Mobile navigation"
        data-testid="mobile-nav"
      >
        {WORKSPACE.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            aria-current={props.activeDestination === id ? 'page' : undefined}
            onClick={() => props.onNavigate(id)}
          >
            <Icon size={19} />
            <span>{id === 'company-home' ? 'Home' : label}</span>
          </button>
        ))}
        <button
          onClick={() => dialog.current.showModal()}
          aria-label="All navigation"
        >
          <Menu size={19} />
          <span>More</span>
        </button>
      </nav>
      <dialog className="navigation-dialog" ref={dialog}>
        <div className="dialog-top">
          <Brand />
          <button
            aria-label="Close navigation"
            onClick={() => dialog.current.close()}
          >
            <X size={22} />
          </button>
        </div>
        <Navigation
          {...props}
          onNavigate={(id) => {
            dialog.current.close()
            props.onNavigate(id)
          }}
        />
      </dialog>
    </>
  )
}
