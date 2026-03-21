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
// Nav sections configuration
// ---------------------------------------------------------------------------

export const navSections: NavSection[] = [
  {
    title: 'Main',
    items: [
      { path: '/', label: 'Overview', end: true },
      { path: '/butlers', label: 'Butlers' },
      { path: '/sessions', label: 'Sessions' },
      { path: '/ingestion', label: 'Ingestion' },
      { path: '/approvals', label: 'Approvals' },
      { path: '/memory', label: 'Memory' },
      { path: '/entities', label: 'Entities' },
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
      { path: '/timeline', label: 'Timeline' },
      { path: '/notifications', label: 'Notifications' },
      { path: '/issues', label: 'Issues' },
      { path: '/audit-log', label: 'Audit Log' },
    ],
  },
]
