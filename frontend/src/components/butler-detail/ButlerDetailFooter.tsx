// ---------------------------------------------------------------------------
// ButlerDetailFooter — per-butler KPI band for the butler detail footer slot.
// (bu-ja5bt.4)
//
// Renders exactly four KPI cells scoped to the active butler:
//   Sessions 24h  — from useButlers() sessions_24h field
//   Spend today   — from useCostSummary("today") by_butler[name]
//   Load%         — derived from useButlerHeartbeats active_session_count
//                   and useRuntimeConfig max_concurrent
//   Last activity — from useButlerHeartbeats last_session_at via <Time>
//
// Partial-failure tolerance: if any cell's source fetch fails or returns null,
// a neutral placeholder glyph ("--") is rendered instead of crashing the band.
//
// Token constraint (non-negotiable):
//   No hex, oklch, rgb literals. Tailwind tokens only.
//   No em-dashes in prose strings. The "--" placeholder is a dash sequence, not
//   the em-dash character (U+2014).
// ---------------------------------------------------------------------------

import { KpiCell } from "@/components/butler-detail/atoms"
import { Time } from "@/components/ui/time"
import { useButlers, useRuntimeConfig } from "@/hooks/use-butlers"
import { useButlerHeartbeats } from "@/hooks/use-system"
import { useCostSummary } from "@/hooks/use-costs"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ButlerDetailFooterProps {
  /** The active butler name. Used to scope all four KPI values. */
  butler: string
}

// ---------------------------------------------------------------------------
// Placeholder glyph (not an em-dash; intentionally two hyphens)
// ---------------------------------------------------------------------------

const PLACEHOLDER = "--"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Per-butler footer KPI band.
 *
 * Scoped to the active butler only. Fleet-wide aggregates do not appear here.
 * Each cell degrades gracefully: a failed or missing data source renders the
 * neutral placeholder glyph rather than crashing the band.
 *
 * Intended to be passed as the `footer` prop on `<Page archetype="status-board">`.
 *
 * @example
 * <ButlerDetailFooter butler="relationship" />
 */
export function ButlerDetailFooter({ butler }: ButlerDetailFooterProps) {
  // --- Sessions 24h ---
  const butlersQuery = useButlers()
  const butlerRow = butlersQuery.data?.data?.find((b) => b.name === butler)
  const sessions24h: string =
    butlersQuery.isError || butlerRow == null
      ? PLACEHOLDER
      : String(butlerRow.sessions_24h)

  // --- Spend today ---
  const costQuery = useCostSummary("today", undefined, undefined, butler)
  const spendToday: string = (() => {
    if (costQuery.isError || !costQuery.data?.data) return PLACEHOLDER
    const amount = costQuery.data.data.by_butler[butler]
    if (amount == null) return PLACEHOLDER
    return `$${amount.toFixed(2)}`
  })()

  // --- Load% (active_session_count / max_concurrent * 100) ---
  const heartbeatsQuery = useButlerHeartbeats()
  const runtimeConfigQuery = useRuntimeConfig(butler)

  const heartbeat = heartbeatsQuery.data?.data?.butlers?.find((b) => b.name === butler)
  const activeSessionCount = heartbeat?.active_session_count ?? 0
  const maxConcurrent = runtimeConfigQuery.data?.max_concurrent ?? null

  const loadPct: string = (() => {
    if (heartbeatsQuery.isError || runtimeConfigQuery.isError) return PLACEHOLDER
    if (maxConcurrent == null || maxConcurrent === 0) return PLACEHOLDER
    return `${Math.round((activeSessionCount / maxConcurrent) * 100)}%`
  })()

  // --- Last activity ---
  const lastRunISO: string | null = heartbeat?.last_session_at ?? null

  const lastActivityValue =
    heartbeatsQuery.isError || lastRunISO == null ? (
      <span className="font-mono tnum text-muted-foreground">{PLACEHOLDER}</span>
    ) : (
      <Time value={lastRunISO} mode="relative" className="font-mono tnum" />
    )

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <footer
      className="border-t border-border px-7 py-4"
      aria-label={`KPI summary for ${butler}`}
    >
      <div className="grid grid-cols-4 gap-6">
        <KpiCell label="Sessions 24h" value={sessions24h} />
        <KpiCell label="Spend today" value={spendToday} />
        <KpiCell label="Load" value={loadPct} />
        <KpiCell label="Last activity" value={lastActivityValue} />
      </div>
    </footer>
  )
}
