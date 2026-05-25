/**
 * DispatchHeader — page header band for ingestion dispatch console routes.
 *
 * Renders the eyebrow, display headline, and optional serif sub-paragraph
 * per the Dispatch visual language. Used at the top of every ingestion route
 * before the IngestionSubNav.
 *
 * Design rules (non-negotiable per DESIGN_LANGUAGE.md):
 * - Display weight is 500, never 700.
 * - Eyebrow: 10px mono uppercase, muted color, 0.14em letter-spacing.
 * - Serif voice paragraph: 16px, line-height 1.55.
 * - No card chrome, no shadow.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Dispatch Visual Language"
 */

import type { ReactNode } from 'react'

export interface DispatchHeaderProps {
  /** Section eyebrow — mono uppercase label (e.g. "Ingestion · timeline"). */
  eyebrow?: string
  /** Display headline — the page's main title. */
  headline: string
  /** Optional serif voice paragraph below the headline. */
  description?: ReactNode
  /** Optional right-rail content (e.g. KPI strip, status pill). */
  aside?: ReactNode
}

/**
 * Dispatch-language page header.
 *
 * Eyebrow and headline are always rendered. Description and aside are optional.
 * When aside is provided, the header becomes a two-column band.
 */
export function DispatchHeader({ eyebrow, headline, description, aside }: DispatchHeaderProps) {
  return (
    <div className={aside ? 'flex items-start justify-between gap-8' : undefined}>
      <div>
        {eyebrow && (
          <p className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground mb-1">
            {eyebrow}
          </p>
        )}
        <h1 className="text-2xl font-medium tracking-tight">{headline}</h1>
        {description && (
          <p className="text-base font-serif leading-[1.55] text-muted-foreground mt-1">{description}</p>
        )}
      </div>
      {aside && (
        <div className="shrink-0">
          {aside}
        </div>
      )}
    </div>
  )
}
