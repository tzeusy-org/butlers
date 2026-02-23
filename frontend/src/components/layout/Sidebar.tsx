import { NavLink } from 'react-router'

const navItems = [
  { path: '/', label: 'Overview', end: true },
  { path: '/butlers', label: 'Butlers' },
  { path: '/sessions', label: 'Sessions' },
  { path: '/traces', label: 'Traces' },
  { path: '/timeline', label: 'Timeline' },
  { path: '/notifications', label: 'Notifications' },
  { path: '/issues', label: 'Issues' },
  { path: '/audit-log', label: 'Audit Log' },
  { path: '/approvals', label: 'Approvals' },
  { path: '/contacts', label: 'Contacts' },
  { path: '/calendar', label: 'Calendar' },
  { path: '/ingestion', label: 'Ingestion' },
  { path: '/groups', label: 'Groups' },
  { path: '/health/measurements', label: 'Health' },
  { path: '/collections', label: 'Collections' },
  { path: '/memory', label: 'Memory' },
  { path: '/entities', label: 'Entities' },
  { path: '/secrets', label: 'Secrets' },
  { path: '/settings', label: 'Settings' },
]

interface SidebarProps {
  collapsed?: boolean
  onToggleCollapse?: () => void
  onNavClick?: () => void
}

export default function Sidebar({ collapsed = false, onToggleCollapse, onNavClick }: SidebarProps) {
  return (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className="flex h-14 items-center border-b border-border px-4">
        <span
          className={`text-lg font-semibold transition-opacity duration-200 ${
            collapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'
          }`}
        >
          Butlers
        </span>
        {collapsed && (
          <span className="text-lg font-semibold">B</span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-3">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.end}
            onClick={onNavClick}
            className={({ isActive }) =>
              `flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                collapsed ? 'justify-center' : 'gap-3'
              } ${
                isActive
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground'
              }`
            }
            title={collapsed ? item.label : undefined}
          >
            {/* First letter as icon placeholder */}
            <span
              className={`flex size-6 shrink-0 items-center justify-center rounded text-xs font-semibold ${
                collapsed ? '' : 'bg-muted'
              }`}
            >
              {item.label[0]}
            </span>
            {!collapsed && <span>{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-border p-4">
        {!collapsed && (
          <>
            <p className="text-xs text-muted-foreground">Today&apos;s spend</p>
            <p className="text-sm font-medium">--</p>
          </>
        )}
      </div>

      {/* Collapse toggle (desktop only, rendered via parent visibility) */}
      {onToggleCollapse && (
        <div className="border-t border-border p-2">
          <button
            onClick={onToggleCollapse}
            className="flex w-full items-center justify-center rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent/50 hover:text-accent-foreground"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`transition-transform duration-200 ${collapsed ? 'rotate-180' : ''}`}
            >
              <path d="m11 17-5-5 5-5" />
              <path d="m18 17-5-5 5-5" />
            </svg>
          </button>
        </div>
      )}
    </div>
  )
}
