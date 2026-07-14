// ---------------------------------------------------------------------------
// SessionsKpiStrip — window-true KPI strip for the sessions page.
//
// Consumes useSessionAggregate(filterParams) — the counts are window-true
// (scoped to the active filters across ALL butlers), NEVER derived from the
// fetched page. The hook keys on the filter params only, so the strip
// recomputes on filter change but not on cursor/page change.
//
// Anatomy reuses the shared KpiStrip primitive (frontend.md §KPI strip):
// tabular-nums mega numbers, hairline dividers, no card fills, mono eyebrows.
// An honesty caption marks the numbers as scoped to the active filters.
// ---------------------------------------------------------------------------

import type { SessionParams } from "@/api/types"
import { KpiStrip } from "@/components/overview/KpiStrip"
import { KPI_EYEBROW_STYLE } from "@/components/overview/kpi-eyebrow"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { useSessionAggregate } from "@/hooks/use-sessions"

const DASH = "—"

/** Compact token formatting (e.g. "1.2K", "3.5M"). */
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

/** Render success rate as a percentage; honest dash when the denominator is 0. */
function formatRate(rate: number | null): string {
  if (rate == null) return DASH
  return `${(rate * 100).toFixed(1)}%`
}

export interface SessionsKpiStripProps {
  /** Active FILTER params (no cursor). Drives the window-true aggregate. */
  filterParams: SessionParams
}

export function SessionsKpiStrip({ filterParams }: SessionsKpiStripProps) {
  const { data, isError, refetch } = useSessionAggregate(filterParams)
  const agg = data?.data
  const top = agg?.by_butler?.[0]
  const terminal = agg ? agg.success_count + agg.failed_count : 0

  // On a first-load outage the aggregate is undefined and every cell falls to
  // "—", which reads as "still loading" indefinitely rather than "the source
  // is down". Name the outage so the dashes are honestly attributed
  // (bu-mkd5r, three-way state contract). Stale data survives a background
  // refetch error, so this only trips when nothing is cached.
  const aggUnavailable = isError && agg == null

  // Partial degradation (bu-tpudw.2): the aggregate returned, but one or more
  // butler pools dropped from its fan-out (meta.sources_degraded). Every KPI
  // below then UNDERCOUNTS, so name the missing pools rather than presenting
  // the partial totals as window-true.
  // ApiMeta is an untyped extensible bag; sources_degraded rides it per the
  // fleet-wide convention (see the aggregate endpoint's ApiMeta(...) write).
  const sourcesDegraded = (data?.meta?.sources_degraded as string[] | undefined) ?? []

  return (
    <div data-testid="sessions-kpi-strip">
      <p className="tnum uppercase" style={{ ...KPI_EYEBROW_STYLE, marginBottom: "12px" }}>
        Matching filters
      </p>
      {aggUnavailable && (
        <SourceDegradedNote
          label="Session metrics"
          detail="unavailable"
          onRetry={() => void refetch()}
          className="mb-3"
        />
      )}
      {!aggUnavailable && sourcesDegraded.length > 0 && (
        <SourceDegradedNote
          label="Session metrics"
          detail={`partial: ${sourcesDegraded.join(", ")} unreachable`}
          onRetry={() => void refetch()}
          className="mb-3"
          testId="sessions-kpi-degraded"
        />
      )}
      <KpiStrip
        cells={[
          {
            eyebrow: "SESSIONS",
            value: agg ? agg.total.toLocaleString() : DASH,
            delta: agg ? `${agg.running_count.toLocaleString()} running` : undefined,
          },
          {
            eyebrow: "SUCCESS RATE",
            value: agg ? formatRate(agg.success_rate) : DASH,
            delta: agg
              ? terminal > 0
                ? `${agg.success_count.toLocaleString()} of ${terminal.toLocaleString()} ok`
                : "no completed sessions"
              : undefined,
          },
          {
            eyebrow: "TOKENS IN / OUT",
            value: agg
              ? `${formatTokens(agg.input_tokens)} / ${formatTokens(agg.output_tokens)}`
              : DASH,
          },
          {
            eyebrow: "TOP BUTLER",
            value: top ? top.butler : DASH,
            delta: top ? `${top.count.toLocaleString()} sessions` : undefined,
          },
        ]}
      />
    </div>
  )
}
