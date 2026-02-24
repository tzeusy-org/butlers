import { useState } from 'react'
import { NavLink, useLocation } from 'react-router'
import { useButlers } from '@/hooks/use-butlers'

// ---------------------------------------------------------------------------
// Nav item types (discriminated union)
// ---------------------------------------------------------------------------

/** A flat navigation link. */
export interface NavFlatItem {
  kind?: 'flat'
  path: string
  label: string
  end?: boolean
  /** If set, only show when this butler is present in the roster. */
  butler?: string
}

/** A collapsible group of navigation links. */
export interface NavGroupItem {
  kind: 'group'
  label: string
  /** If set, only show when this butler is present in the roster. */
  butler?: string
  children: NavFlatItem[]
}

export type NavItem = NavFlatItem | NavGroupItem

// ---------------------------------------------------------------------------
// Type guard
// ---------------------------------------------------------------------------

function isGroup(item: NavItem): item is NavGroupItem {
  return item.kind === 'group'
}

// ---------------------------------------------------------------------------
// Nav items configuration
// ---------------------------------------------------------------------------

const navItems: NavItem[] = [
  { path: '/', label: 'Overview', end: true },
  { path: '/butlers', label: 'Butlers' },
  { path: '/sessions', label: 'Sessions' },
  { path: '/traces', label: 'Traces' },
  { path: '/timeline', label: 'Timeline' },
  { path: '/notifications', label: 'Notifications' },
  { path: '/issues', label: 'Issues' },
  { path: '/audit-log', label: 'Audit Log' },
  { path: '/approvals', label: 'Approvals' },
  {
    kind: 'group',
    label: 'Relationships',
    butler: 'relationship',
    children: [
      { path: '/contacts', label: 'Contacts' },
      { path: '/groups', label: 'Groups' },
    ],
  },
  { path: '/calendar', label: 'Calendar' },
  { path: '/ingestion', label: 'Ingestion' },
  { path: '/health/measurements', label: 'Health' },
  { path: '/memory', label: 'Memory' },
  { path: '/secrets', label: 'Secrets' },
  { path: '/settings', label: 'Settings' },
]

// ---------------------------------------------------------------------------
// Helper: check if a path matches the current location
// ---------------------------------------------------------------------------

function isPathActive(pathname: string, itemPath: string, end?: boolean): boolean {
  if (end) {
    return pathname === itemPath
  }
  return pathname === itemPath || pathname.startsWith(itemPath + '/')
}

// ---------------------------------------------------------------------------
// Butler-aware filtering
// ---------------------------------------------------------------------------

function useFilteredNavItems(items: NavItem[]): NavItem[] {
  const { data: response, isLoading, isError } = useButlers()

  // While loading or on error, show all items (graceful degradation)
  if (isLoading || isError || !response) {
    return items
  }

  const butlerNames = new Set(response.data.map((b) => b.name))

  return items.filter((item) => {
    const butlerField = item.butler
    if (!butlerField) return true
    return butlerNames.has(butlerField)
  })
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Shared link styles for flat items (used by both top-level and group children). */
function navLinkClassName(
  isActive: boolean,
  collapsed: boolean,
  indented: boolean,
): string {
  return [
    'flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors',
    collapsed ? 'justify-center' : 'gap-3',
    indented && !collapsed ? 'pl-9' : null,
    isActive
      ? 'bg-accent text-accent-foreground'
      : 'text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground',
  ].filter(Boolean).join(' ')
}

function FlatNavLink({
  item,
  collapsed,
  indented = false,
  onNavClick,
}: {
  item: NavFlatItem
  collapsed: boolean
  indented?: boolean
  onNavClick?: () => void
}) {
  return (
    <NavLink
      to={item.path}
      end={item.end}
      onClick={onNavClick}
      className={({ isActive }) => navLinkClassName(isActive, collapsed, indented)}
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
  )
}

function NavGroup({
  item,
  collapsed,
  onNavClick,
}: {
  item: NavGroupItem
  collapsed: boolean
  onNavClick?: () => void
}) {
  const location = useLocation()

  // Determine if any child route is active
  const hasActiveChild = item.children.some((child) =>
    isPathActive(location.pathname, child.path, child.end),
  )

  // User can manually toggle when no child is active. When a child route is
  // active the group always stays expanded (auto-expand requirement).
  const [userExpanded, setUserExpanded] = useState(false)
  const expanded = hasActiveChild || userExpanded

  // Collapsed sidebar: show first letter, clicking navigates to first child
  if (collapsed) {
    return (
      <NavLink
        to={item.children[0]?.path ?? '/'}
        onClick={onNavClick}
        className={({ isActive }) => navLinkClassName(isActive || hasActiveChild, true, false)}
        title={item.label}
      >
        <span className="flex size-6 shrink-0 items-center justify-center rounded text-xs font-semibold">
          {item.label[0]}
        </span>
      </NavLink>
    )
  }

  return (
    <div>
      {/* Group header */}
      <button
        onClick={() => setUserExpanded((prev) => !prev)}
        className={[
          'flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
          hasActiveChild
            ? 'text-accent-foreground'
            : 'text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground',
        ].join(' ')}
        aria-expanded={expanded}
      >
        {/* First letter icon */}
        <span className="flex size-6 shrink-0 items-center justify-center rounded bg-muted text-xs font-semibold">
          {item.label[0]}
        </span>
        <span className="flex-1 text-left">{item.label}</span>
        {/* Chevron */}
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
          className={`shrink-0 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
      </button>

      {/* Children (with smooth height transition) */}
      <div
        aria-hidden={!expanded}
        className={`overflow-hidden transition-all duration-200 ${
          expanded ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        {item.children.map((child) => (
          <FlatNavLink
            key={child.path}
            item={child}
            collapsed={false}
            indented
            onNavClick={onNavClick}
          />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Sidebar component
// ---------------------------------------------------------------------------

interface SidebarProps {
  collapsed?: boolean
  onToggleCollapse?: () => void
  onNavClick?: () => void
}

export default function Sidebar({ collapsed = false, onToggleCollapse, onNavClick }: SidebarProps) {
  const filteredItems = useFilteredNavItems(navItems)

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
        {filteredItems.map((item) =>
          isGroup(item) ? (
            <NavGroup
              key={item.label}
              item={item}
              collapsed={collapsed}
              onNavClick={onNavClick}
            />
          ) : (
            <FlatNavLink
              key={item.path}
              item={item}
              collapsed={collapsed}
              onNavClick={onNavClick}
            />
          ),
        )}
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
