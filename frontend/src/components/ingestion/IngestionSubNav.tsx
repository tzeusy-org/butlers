/**
 * IngestionSubNav — horizontal sub-navigation strip for all /ingestion routes.
 *
 * Used by every ingestion route to provide consistent navigation between
 * Timeline, Connectors, and Filters. Highlights the active route via
 * React Router NavLink.
 *
 * Design language: Dispatch — no card chrome, no shadcn TabsList, no gradient.
 * Hairline bottom border, plain NavLink with active underline treatment.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline route replaces legacy tab landing"
 */

import { NavLink } from 'react-router'

const NAV_ITEMS = [
  { label: 'Timeline', to: '/ingestion', end: true },
  { label: 'Connectors', to: '/ingestion/connectors', end: false },
  { label: 'Filters', to: '/ingestion/filters', end: false },
] as const

interface IngestionSubNavProps {
  className?: string
}

/**
 * Sub-navigation for ingestion routes.
 *
 * Each link uses NavLink so React Router applies active state automatically.
 * The Timeline link uses `end` matching so it does not stay active on sub-routes.
 */
export function IngestionSubNav({ className }: IngestionSubNavProps) {
  return (
    <nav
      aria-label="Ingestion views"
      className={`flex gap-1 border-b border-border pb-0 ${className ?? ''}`}
    >
      {NAV_ITEMS.map(({ label, to, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            [
              'px-3 py-2 text-sm font-medium transition-colors',
              'hover:text-foreground',
              isActive
                ? 'border-b-2 border-foreground text-foreground -mb-px'
                : 'text-muted-foreground border-b-2 border-transparent -mb-px',
            ].join(' ')
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  )
}
