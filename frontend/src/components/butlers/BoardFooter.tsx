// ---------------------------------------------------------------------------
// BoardFooter — status-board KPI aggregate band. (bu-hb7dh.7)
//
// Props:
//   aggregates  Fleet-wide aggregates from useButlerStatusBoard().
//
// Doctrine:
//   - Tailwind tokens only (no inline style, no raw oklch).
//   - No em-dashes.
//   - Status-tone dots only when count > 0 (per spec).
// ---------------------------------------------------------------------------

import { SourceDegradedNote } from "@/components/ui/query-boundary"
import type { StatusBoardAggregates } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface BoardFooterProps {
  aggregates: StatusBoardAggregates
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

interface StatCellProps {
  label: string
  value: string
  /** If set, renders a colored tone dot before the value. Only when count > 0. */
  dotClass?: string
  showDot?: boolean
  /** Accessible label for the cell container */
  ariaLabel?: string
}

function StatCell({ label, value, dotClass, showDot = false, ariaLabel }: StatCellProps) {
  return (
    <div
      role="group"
      className="flex flex-col gap-1"
      aria-label={ariaLabel ?? `${label}: ${value}`}
    >
      <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="flex items-center gap-1.5 font-mono text-base font-medium tabular-nums">
        {showDot && dotClass && (
          <span
            className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`}
            aria-hidden="true"
          />
        )}
        {value}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Footer KPI band for the /butlers/ status-board page.
 *
 * Renders six equal-width stat cells in a horizontal grid:
 *   ACTIVE | OFFLINE | QUARANTINED | SESSIONS 24H | SPEND TODAY | AVG LOAD
 *
 * Followed by a composition addendum: "N butlers, S staffers".
 *
 * Status-tone dots appear only when the relevant count > 0.
 * The border-top separates this band from the grid above.
 *
 * Uses a plain `<footer>` element (no role="contentinfo") so it is valid
 * inside the `<main>` landmark rendered by Shell.tsx.
 */
export function BoardFooter({ aggregates }: BoardFooterProps) {
  const {
    active,
    offline,
    quarantined,
    totalSessions24h,
    totalSpendToday,
    avgLoadPct,
    butlerCount,
    stafferCount,
    costSourceError,
    sessionsSourceError,
  } = aggregates

  const avgLoadValue = avgLoadPct == null ? "—" : `${avgLoadPct}%`
  // totalSpendToday is the sum of raw per-butler values formatted once — the
  // authoritative figure. Per-cell displayed values also use .toFixed(2) on
  // their own raw input, so the visual sum of cells may differ from this total
  // by at most $0.01 per cell due to rounding. This is expected and acceptable.
  //
  // When the cost source has errored, totalSpendToday is a partial sum over
  // whichever butlers still have cached cost data (possibly none) -- never a
  // confident fleet-wide total. Show "—" and name the degraded source below
  // rather than a calm "$0.00" that reads as "no spend today".
  const spendValue = costSourceError ? "—" : `$${totalSpendToday.toFixed(2)}`
  // Same doctrine for the sessions stripe: a failed hourly-activity query
  // must not read as a confident "0 sessions today".
  const sessionsValue = sessionsSourceError ? "—" : totalSessions24h.toLocaleString()

  return (
    <footer
      className="border-t border-border px-7 py-4"
    >
      <div className="grid grid-cols-6 gap-4">
        <StatCell
          label="Active"
          value={String(active)}
          dotClass="bg-[var(--green)]"
          showDot={active > 0}
          ariaLabel={`Active: ${active}`}
        />
        <StatCell
          label="Offline"
          value={String(offline)}
          dotClass="bg-destructive"
          showDot={offline > 0}
          ariaLabel={`Offline: ${offline}`}
        />
        <StatCell
          label="Quarantined"
          value={String(quarantined)}
          dotClass="bg-destructive"
          showDot={quarantined > 0}
          ariaLabel={`Quarantined: ${quarantined}`}
        />
        <StatCell
          label="Sessions·24h"
          value={sessionsValue}
          ariaLabel={`Sessions in the past 24 hours: ${sessionsValue}`}
        />
        <StatCell
          label="Spend·today"
          value={spendValue}
          ariaLabel={`Spend today: ${spendValue}`}
        />
        <StatCell
          label="Avg load"
          value={avgLoadValue}
          ariaLabel={`Average load: ${avgLoadValue}`}
        />
      </div>

      {/* Composition addendum */}
      <p className="mt-2 font-mono text-[10px] text-muted-foreground">
        {butlerCount} {butlerCount === 1 ? "butler" : "butlers"},{" "}
        {stafferCount} {stafferCount === 1 ? "staffer" : "staffers"}
      </p>

      {/* Cost source degraded note -- never let a failed spend query hide behind "$0.00". */}
      {costSourceError && (
        <SourceDegradedNote
          label="Spend today"
          detail="cost source unavailable, total above is incomplete"
          className="mt-3"
        />
      )}

      {/* Sessions source degraded note -- never let a failed stripe query hide behind "0". */}
      {sessionsSourceError && (
        <SourceDegradedNote
          label="Sessions·24h"
          detail="activity source unavailable, total above is incomplete"
          className="mt-3"
        />
      )}
    </footer>
  )
}
