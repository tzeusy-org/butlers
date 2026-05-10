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
 *   ACTIVE | PAUSED | AWAITING | SESSIONS 24H | SPEND TODAY | AVG LOAD
 *
 * Followed by a composition addendum: "N butlers, S staffers".
 *
 * Status-tone dots appear only when the relevant count > 0.
 * The border-top separates this band from the grid above.
 */
export function BoardFooter({ aggregates }: BoardFooterProps) {
  const {
    active,
    paused,
    awaiting,
    quarantined,
    totalSessions24h,
    totalSpendToday,
    avgLoadPct,
    butlerCount,
    stafferCount,
  } = aggregates

  const awaitingPlusQuarantined = awaiting + quarantined

  const avgLoadValue = avgLoadPct == null ? "—" : `${avgLoadPct}%`
  const spendValue = `$${totalSpendToday.toFixed(2)}`
  const sessionsValue = totalSessions24h.toLocaleString()

  return (
    <footer
      role="contentinfo"
      className="border-t border-border px-7 py-4"
    >
      <div className="grid grid-cols-6 gap-4">
        <StatCell
          label="Active"
          value={String(active)}
          dotClass="bg-emerald-500"
          showDot={active > 0}
          ariaLabel={`Active: ${active}`}
        />
        <StatCell
          label="Paused"
          value={String(paused)}
          dotClass="bg-amber-500"
          showDot={paused > 0}
          ariaLabel={`Paused: ${paused}`}
        />
        <StatCell
          label="Awaiting"
          value={String(awaitingPlusQuarantined)}
          dotClass="bg-destructive"
          showDot={awaitingPlusQuarantined > 0}
          ariaLabel={`Awaiting: ${awaitingPlusQuarantined}`}
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
    </footer>
  )
}
