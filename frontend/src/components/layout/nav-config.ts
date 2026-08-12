// ---------------------------------------------------------------------------
// Nav item types (discriminated union)
// ---------------------------------------------------------------------------

import type { NavIconName } from './NavIcon'
import { SHELL_CAPABILITIES, type ShellCapability } from '@/lib/shell-capability'

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
// Navigation projection
// ---------------------------------------------------------------------------

function toNavItem(capability: ShellCapability): NavFlatItem {
  const placement = capability.placement
  if (!placement) throw new Error(`Navigation projection received ${capability.path} without placement`)
  return {
    path: capability.path,
    label: capability.label,
    end: placement.end,
    butler: placement.butler,
    icon: placement.icon,
    badgeKey: placement.badgeKey,
    badgeVariant: placement.badgeVariant,
    tooltip: placement.tooltip,
    chord: capability.chord,
  }
}

const NAV_SECTIONS: NavSection['title'][] = ['Main', 'Dedicated Butlers', 'Telemetry']

/** Sidebar/subnavigation projection; capability metadata remains authoritative. */
export const navSections: NavSection[] = NAV_SECTIONS.map((title) => {
  const capabilities = SHELL_CAPABILITIES
    .filter((capability) => capability.placement?.section === title)
    .sort((a, b) => (a.placement?.order ?? 0) - (b.placement?.order ?? 0))
  const items: NavItem[] = []
  const groups = new Map<string, NavGroupItem>()
  for (const capability of capabilities) {
    const placement = capability.placement
    if (!placement) continue
    if (!placement.group) {
      items.push(toNavItem(capability))
      continue
    }
    let group = groups.get(placement.group)
    if (!group) {
      group = { kind: 'group', label: placement.group, butler: placement.butler, children: [] }
      groups.set(placement.group, group)
      items.push(group)
    }
    group.children.push(toNavItem(capability))
  }
  return { title, items, defaultExpanded: title !== 'Telemetry' }
})
