/**
 * DispatchLayout — root layout wrapper for all ingestion dispatch console routes.
 *
 * Provides the consistent outer container used by Timeline, Connectors, and
 * Filters route shells. Applies max-width, padding, and vertical rhythm per
 * the Dispatch design language.
 *
 * Design: one elevation, no card chrome, hairline rules for structure.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Dispatch Visual Language"
 * Reference: pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md §3a "Page shell"
 */

import type { ReactNode } from 'react'

interface DispatchLayoutProps {
  children: ReactNode
  className?: string
}

/**
 * Outer layout container for ingestion dispatch console pages.
 *
 * Sets vertical rhythm via space-y-6. Content should not add its own
 * outer padding — the dashboard shell provides horizontal gutters.
 */
export function DispatchLayout({ children, className }: DispatchLayoutProps) {
  return (
    <div className={`space-y-6 ${className ?? ''}`}>
      {children}
    </div>
  )
}
