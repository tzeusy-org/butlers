/**
 * IngestionFiltersPage — route component for /ingestion/filters.
 *
 * Renders the full Filters Pipeline: five-gate diagram, proportional funnel,
 * gate sections with rule rows, priority senders, channel defaults, archived
 * rules, and footer actions.
 *
 * Replaced the legacy FiltersTab card placeholder (FiltersTabContent, deleted
 * in bu-4utdw.2). The old card-based content is not rendered here (spec AC4).
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Filters Pipeline"
 */

import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { FiltersPipeline } from '@/components/ingestion/filters'
import { getAvailablePipelineBacklog } from '@/components/ingestion/filters/backlog-state'
import { usePipelineStats } from '@/hooks/use-ingestion'

// ---------------------------------------------------------------------------
// Header aside — event count KPI strip
// ---------------------------------------------------------------------------

export function FiltersHeaderAside() {
  const { data: stats } = usePipelineStats('24h')
  if (!stats) return null

  const total = stats.ingested + stats.filtered
  const dispatched =
    stats.routed_by_butler != null
      ? Object.values(stats.routed_by_butler).reduce((a, b) => a + b, 0)
      : 0
  const available = stats.aggregates_available
  const backlog = getAvailablePipelineBacklog(stats)

  return (
    <div className="flex flex-wrap items-baseline gap-8">
      {!available && (
        <span
          className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
          data-testid="filters-header-metrics-unavailable"
        >
          metrics unavailable
        </span>
      )}
      <div className="flex flex-wrap gap-8">
        {[
          { label: 'received · 24h', value: total.toLocaleString() },
          { label: 'dispatched', value: dispatched.toLocaleString() },
          { label: 'filtered', value: stats.filtered.toLocaleString() },
        ].map(({ label, value }) => (
          <div key={label} className="text-right">
            <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70">
              {label}
            </div>
            <div className="font-mono text-lg font-medium tabular-nums tracking-[-0.02em]">
              {available ? value : '—'}
            </div>
          </div>
        ))}
        {backlog ? (
          <div
            className="border-l border-border pl-5 text-right"
            data-testid="filters-header-backlog"
            aria-live="polite"
          >
            <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70">
              active backlog · current
            </div>
            <div className="font-mono text-lg font-medium tabular-nums tracking-[-0.02em]">
              {backlog.activeTotal.toLocaleString()} active
            </div>
          </div>
        ) : (
          <div
            className="border-l border-border pl-5 font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
            data-testid="filters-header-backlog-unavailable"
            role="status"
          >
            backlog unavailable
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// IngestionFiltersPage
// ---------------------------------------------------------------------------

export default function IngestionFiltersPage() {
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · filters"
        headline="How signals earn dispatch."
        description="Five gates between arriving and acting. Rules at each gate decide whether the system stores, drops, tiers, routes, or replays."
        aside={<FiltersHeaderAside />}
      />
      <IngestionSubNav />
      <DispatchSurface>
        <FiltersPipeline />
      </DispatchSurface>
    </DispatchLayout>
  )
}
