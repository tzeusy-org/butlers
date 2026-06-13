/**
 * DispatchSurface — hairline-divided content region for ingestion routes.
 *
 * The primary content container for ingestion route bodies. Provides the
 * hairline top border and vertical padding that separate the sub-nav from
 * the content below it.
 *
 * Design: no card chrome, no shadow, one elevation. Structure through
 * hairlines and rhythm, not boxes.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Dispatch Visual Language"
 * Reference: docs/redesigns/ingestion-design-language.md §3 "Layout"
 */

import type { ReactNode } from 'react'

export interface DispatchSurfaceProps {
  children: ReactNode
  /** Additional Tailwind classes. */
  className?: string
}

/**
 * Content region below the IngestionSubNav.
 *
 * Adds a hairline top border and top padding to visually separate the
 * sub-nav from the main content area without card chrome.
 */
export function DispatchSurface({ children, className }: DispatchSurfaceProps) {
  return (
    <div className={`border-t border-border pt-6 ${className ?? ''}`}>
      {children}
    </div>
  )
}
