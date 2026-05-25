/**
 * IngestionFiltersPage — route component for /ingestion/filters.
 *
 * Renders the full Filters Pipeline: five-gate diagram, proportional funnel,
 * gate sections with rule rows, priority senders, channel defaults, archived
 * rules, and footer actions.
 *
 * Replaces the legacy FiltersTab card placeholder (FiltersTabContent). The
 * old card-based content is NOT rendered here (spec AC4).
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 */

import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { FiltersPipeline } from '@/components/ingestion/filters'
import { usePipelineStats } from '@/hooks/use-ingestion'

// ---------------------------------------------------------------------------
// Header aside — event count KPI strip
// ---------------------------------------------------------------------------

function FiltersHeaderAside() {
  const { data: stats } = usePipelineStats('24h')
  if (!stats) return null

  const total = stats.ingested + stats.filtered
  const dispatched = Object.values(stats.routed_by_butler).reduce((a, b) => a + b, 0)

  return (
    <div className="flex gap-8">
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
            {stats.aggregates_available ? value : '—'}
          </div>
        </div>
      ))}
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
