import { useState } from 'react'
import { NavLink, useLocation } from 'react-router'
import { useButlers } from '@/hooks/use-butlers'
import { useCostSummary } from '@/hooks/use-costs'

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

/** A labelled section grouping multiple nav items under a heading. */
export interface NavSection {
  title: string
  items: NavItem[]
  /** Whether this section starts expanded (default: true). */
  defaultExpanded?: boolean
}

// ---------------------------------------------------------------------------
// Type guard
// ---------------------------------------------------------------------------

function isGroup(item: NavItem): item is NavGroupItem {
  return item.kind === 'group'
}

// ---------------------------------------------------------------------------
// Nav sections configuration
// ---------------------------------------------------------------------------

const navSections: NavSection[] = [
  {
    title: 'Main',
    items: [
      { path: '/', label: 'Overview', end: true },
      { path: '/butlers', label: 'Butlers' },
      { path: '/sessions', label: 'Sessions' },
      { path: '/ingestion', label: 'Ingestion' },
      { path: '/approvals', label: 'Approvals' },
      { path: '/memory', label: 'Memory' },
      { path: '/secrets', label: 'Secrets' },
      { path: '/settings', label: 'Settings' },
    ],
  },
  {
    title: 'Dedicated Butlers',
    items: [
      {
        kind: 'group',
        label: 'Relationships',
        butler: 'relationship',
        children: [
          { path: '/contacts', label: 'Contacts' },
          { path: '/groups', label: 'Groups' },
        ],
      },
      { path: '/education', label: 'Education', butler: 'education' },
      { path: '/health/measurements', label: 'Health' },
      { path: '/calendar', label: 'Calendar' },
    ],
  },
  {
    title: 'Telemetry',
    defaultExpanded: false,
    items: [
      { path: '/traces', label: 'Traces' },
      { path: '/timeline', label: 'Timeline' },
      { path: '/notifications', label: 'Notifications' },
      { path: '/issues', label: 'Issues' },
      { path: '/audit-log', label: 'Audit Log' },
    ],
  },
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

function useFilteredNavSections(sections: NavSection[]): NavSection[] {
  const { data: response, isLoading, isError } = useButlers()

  // While loading or on error, show all items (graceful degradation)
  if (isLoading || isError || !response) {
    return sections
  }

  const butlerNames = new Set(response.data.map((b) => b.name))

  return sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        const butlerField = item.butler
        if (!butlerField) return true
        return butlerNames.has(butlerField)
      }),
    }))
    .filter((section) => section.items.length > 0)
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
// Collapsible section wrapper
// ---------------------------------------------------------------------------

/** Collect all paths from a list of nav items (including group children). */
function allPaths(items: NavItem[]): { path: string; end?: boolean }[] {
  const result: { path: string; end?: boolean }[] = []
  for (const item of items) {
    if (isGroup(item)) {
      for (const child of item.children) {
        result.push({ path: child.path, end: child.end })
      }
    } else {
      result.push({ path: item.path, end: item.end })
    }
  }
  return result
}

function NavSectionGroup({
  section,
  collapsed,
  isFirst,
  onNavClick,
}: {
  section: NavSection
  collapsed: boolean
  isFirst: boolean
  onNavClick?: () => void
}) {
  const location = useLocation()

  // Auto-expand when any item in the section is active
  const hasActiveItem = allPaths(section.items).some((p) =>
    isPathActive(location.pathname, p.path, p.end),
  )

  const [userExpanded, setUserExpanded] = useState(section.defaultExpanded !== false)
  const expanded = hasActiveItem || userExpanded

  return (
    <div className={!isFirst ? 'mt-2' : ''}>
      {/* Section header — clickable toggle when expanded sidebar */}
      {!collapsed ? (
        <button
          onClick={() => setUserExpanded((prev) => !prev)}
          className="flex w-full items-center gap-1 px-3 py-1"
          aria-expanded={expanded}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`shrink-0 text-muted-foreground/40 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
          >
            <path d="m9 18 6-6-6-6" />
          </svg>
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
            {section.title}
          </h3>
        </button>
      ) : (
        !isFirst && <div className="mx-2 mb-2 border-t border-border" />
      )}
      {/* Items */}
      <div
        aria-hidden={!collapsed && !expanded}
        className={
          collapsed
            ? 'space-y-1'
            : `overflow-hidden transition-all duration-200 space-y-1 ${
                expanded ? 'max-h-[500px] opacity-100' : 'max-h-0 opacity-0'
              }`
        }
      >
        {section.items.map((item) =>
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
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sidebar footer with live cost data
// ---------------------------------------------------------------------------

function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const { data: costResponse, isLoading } = useCostSummary('today')
  const cost = costResponse?.data.total_cost_usd

  return (
    <div className="border-t border-border p-4">
      {!collapsed && (
        <>
          <p className="text-xs text-muted-foreground">Today&apos;s spend</p>
          <p className="text-sm font-medium">
            {isLoading || cost == null ? '--' : `$${cost.toFixed(2)}`}
          </p>
        </>
      )}
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
  const filteredSections = useFilteredNavSections(navSections)

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
      <nav className="flex-1 overflow-y-auto p-3">
        {filteredSections.map((section, idx) => (
          <NavSectionGroup
            key={section.title}
            section={section}
            collapsed={collapsed}
            isFirst={idx === 0}
            onNavClick={onNavClick}
          />
        ))}
      </nav>

      {/* Footer */}
      <SidebarFooter collapsed={collapsed} />

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
