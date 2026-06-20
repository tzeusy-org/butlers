// ---------------------------------------------------------------------------
// Nav item types (discriminated union)
// ---------------------------------------------------------------------------

import type { NavIconName } from './NavIcon'

/** A flat navigation link. */
export interface NavFlatItem {
  kind?: 'flat'
  path: string
  label: string
  end?: boolean
  /** If set, only show when this butler is present in the roster. */
  butler?: string
  /**
   * Hairline SVG glyph for the rail. When omitted, the rail falls back to
   * a first-letter glyph. Items in the "Dedicated Butlers" section with a
   * `butler` field render `ButlerMark` instead and ignore this field.
   */
  icon?: NavIconName
  /** If set, a React key used to look up a live badge count from the badge registry. */
  badgeKey?: string
  /**
   * If set, controls badge color:
   *   'red'   — reauth / critical counts
   *   'amber' — approval / warning counts
   * Defaults to primary (blue) if omitted.
   */
  badgeVariant?: 'red' | 'amber'
  /** If set, used as the tooltip text instead of the label (e.g. for items needing disambiguation). */
  tooltip?: string
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
// Nav sections configuration
// ---------------------------------------------------------------------------

export const navSections: NavSection[] = [
  {
    title: 'Main',
    items: [
      { path: '/', label: 'Overview', end: true, icon: 'overview' },
      { path: '/butlers', label: 'Butlers', icon: 'butlers' },
      { path: '/qa', label: 'QA', butler: 'qa', badgeKey: 'qa-escalations', badgeVariant: 'red', icon: 'qa' },
      { path: '/ingestion', label: 'Ingestion', icon: 'ingestion' },
      { path: '/approvals', label: 'Approvals', badgeKey: 'approvals-pending', badgeVariant: 'amber', icon: 'approvals' },
      { path: '/memory', label: 'Memory', icon: 'memory' },
      { path: '/entities', label: 'Entities', icon: 'entities' },
      { path: '/secrets', label: 'Secrets', icon: 'secrets' },
      { path: '/settings', label: 'Settings', icon: 'settings' },
    ],
  },
  {
    title: 'Dedicated Butlers',
    items: [
      { path: '/education', label: 'Education', butler: 'education' },
      { path: '/health', label: 'Health', butler: 'health' },
      { path: '/calendar', label: 'Calendar' },
      { path: '/chronicles', label: 'Chronicles', butler: 'chronicler', tooltip: 'Retrospective lived-time reconstruction' },
    ],
  },
  {
    title: 'Telemetry',
    defaultExpanded: false,
    items: [
      { path: '/timeline', label: 'Timeline', icon: 'timeline' },
      { path: '/notifications', label: 'Notifications', icon: 'notifications' },
      { path: '/issues', label: 'Issues', icon: 'issues' },
      { path: '/sessions', label: 'Sessions', icon: 'sessions' },
      { path: '/audit-log', label: 'Audit Log', icon: 'audit' },
      { path: '/system', label: 'System', icon: 'system', tooltip: 'Instance ownership and runtime facts' },
    ],
  },
]
