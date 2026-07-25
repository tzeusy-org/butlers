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
  /**
   * Single-letter `g`-then-key chord that navigates here (e.g. `'h'` for
   * `g h` → this path). Declared directly on the route it targets so the
   * chord can never drift from where it actually points (bu-86c4c.7 — the
   * command/route registry is the single source of truth for the sidebar,
   * the command menu's Pages group, g-chords, and the '?' help sheet).
   * Consumed by `src/lib/route-registry.ts`.
   */
  chord?: string
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
      { path: '/', label: 'Overview', end: true, icon: 'overview', chord: 'o' },
      { path: '/butlers', label: 'Butlers', icon: 'butlers', chord: 'b' },
      { path: '/qa', label: 'QA', butler: 'qa', badgeKey: 'qa-escalations', badgeVariant: 'red', icon: 'qa' },
      { path: '/ingestion', label: 'Ingestion', icon: 'ingestion', chord: 'e' },
      // g-chords added (bu-ep4ks.12): these two badged, high-traffic pages
      // had no chord while several lower-traffic pages did.
      { path: '/approvals', label: 'Approvals', badgeKey: 'approvals-pending', badgeVariant: 'amber', icon: 'approvals', chord: 'p' },
      { path: '/decisions', label: 'Decisions', badgeKey: 'decisions-open', badgeVariant: 'amber', icon: 'decisions', chord: 'd' },
      { path: '/memory', label: 'Memory', icon: 'memory', chord: 'm' },
      { path: '/entities', label: 'Entities', icon: 'entities' },
      { path: '/secrets', label: 'Secrets', icon: 'secrets' },
      { path: '/settings', label: 'Settings', icon: 'settings' },
    ],
  },
  {
    title: 'Dedicated Butlers',
    items: [
      { path: '/education', label: 'Education', butler: 'education' },
      // chord: 'h' — fixed bu-86c4c.7 drift (used to point g-h at the
      // pre-redesign /health/measurements route; now points at the actual
      // Health overview page it is declared on). The child routes are the
      // stable Health ledger surfaces exposed from that overview.
      {
        kind: 'group',
        label: 'Health',
        butler: 'health',
        children: [
          { path: '/health', label: 'Overview', end: true, chord: 'h' },
          { path: '/health/measurements', label: 'Measurements' },
          { path: '/health/medications', label: 'Medications' },
          { path: '/health/conditions', label: 'Conditions' },
          { path: '/health/symptoms', label: 'Symptoms' },
          { path: '/health/meals', label: 'Meals' },
          { path: '/health/research', label: 'Research' },
        ],
      },
      { path: '/calendar', label: 'Calendar' },
      { path: '/chronicles', label: 'Chronicles', butler: 'chronicler', tooltip: 'Retrospective lived-time reconstruction' },
    ],
  },
  {
    title: 'Telemetry',
    defaultExpanded: false,
    items: [
      { path: '/timeline', label: 'Timeline', icon: 'timeline', chord: 't' },
      { path: '/notifications', label: 'Notifications', icon: 'notifications', chord: 'n' },
      { path: '/issues', label: 'Issues', icon: 'issues', chord: 'i' },
      { path: '/sessions', label: 'Sessions', icon: 'sessions', chord: 's' },
      // JARVIS audit move 8 (bu-86c4c.11): the merged /costs +
      // /settings/spend surface, now nav-visible in both sidebar and
      // command palette (was reachable from neither).
      { path: '/spend', label: 'Spend', icon: 'spend' },
      { path: '/audit-log', label: 'Audit Log', icon: 'audit', chord: 'a' },
      { path: '/system', label: 'System', icon: 'system', tooltip: 'Instance ownership and runtime facts' },
    ],
  },
]
