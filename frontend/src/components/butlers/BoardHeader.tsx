// ---------------------------------------------------------------------------
// BoardHeader — status-board chrome: eyebrow, title, healthy/total pill,
// clock + date cluster. (bu-hb7dh.7)
//
// Props:
//   aggregates       Fleet-wide aggregates from useButlerStatusBoard().
//   refreshIntervalMs How frequently the page refetches (shown in caption).
//
// Doctrine:
//   - <Time> for all timestamps.
//   - Tailwind tokens only (no inline style, no raw oklch).
//   - No em-dashes.
//   - Title is text-2xl font-bold tracking-tight (design-language.md H1 standard).
// ---------------------------------------------------------------------------

import { Time } from "@/components/ui/time"
import type { StatusBoardAggregates } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface BoardHeaderProps {
  aggregates: StatusBoardAggregates
  refreshIntervalMs: number
}

// ---------------------------------------------------------------------------
// Pill helper
// ---------------------------------------------------------------------------

/**
 * Compute the "healthy" count: rows that are online and not quarantined.
 * healthy = total - offline - quarantined
 */
function healthyCount(aggregates: StatusBoardAggregates): number {
  return aggregates.total - aggregates.offline - aggregates.quarantined
}

/**
 * Pill dot color:
 *   green  — all butlers are healthy (healthy === total)
 *   amber  — some are healthy (healthy > 0)
 *   red    — none are healthy (healthy === 0)
 */
function pillDotClass(healthy: number, total: number): string {
  if (total === 0 || healthy === total) return "bg-green-500"
  if (healthy > 0) return "bg-amber-500"
  return "bg-red-500"
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Header strip for the /butlers/ status-board page.
 *
 * Layout (two-column grid):
 *   LEFT  — eyebrow + title row (h1 + refresh caption)
 *   RIGHT — healthy/total pill + clock+date stack
 *
 * The component carries its own border-bottom so the status-board Page shell
 * does not need to add one.
 *
 * Uses a plain `<header>` element (no role="banner") so it is valid inside
 * the `<main>` landmark rendered by Shell.tsx.
 */
export function BoardHeader({ aggregates, refreshIntervalMs }: BoardHeaderProps) {
  const healthy = healthyCount(aggregates)
  const total = aggregates.total
  const dotClass = pillDotClass(healthy, total)

  const refreshSec = Math.round(refreshIntervalMs / 1_000)
  const refreshLabel =
    refreshSec >= 60
      ? `refreshes every ${Math.round(refreshSec / 60)}m`
      : `refreshes every ${refreshSec}s`

  const now = new Date()

  return (
    <header
      className="grid grid-cols-[1fr_auto] items-baseline gap-6 border-b border-border px-7 pb-4"
    >
      {/* LEFT block: eyebrow + title row */}
      <div>
        <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          Butlers, status board
        </span>
        <div className="flex flex-wrap items-baseline gap-4">
          <h1 className="text-2xl font-bold tracking-tight">The staff, at a glance</h1>
          <span className="font-mono text-xs text-muted-foreground">
            {aggregates.butlerCount} {aggregates.butlerCount === 1 ? "butler" : "butlers"},{" "}
            {refreshLabel}
          </span>
        </div>
      </div>

      {/* RIGHT block: healthy/total pill + clock+date stack */}
      <div className="flex items-center gap-4">
        {/* Healthy/total pill */}
        <div
          className="flex items-center gap-1.5 rounded-sm border border-border px-2.5 py-1"
          aria-label={`${healthy} of ${total} reporting healthy`}
        >
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${dotClass}`} aria-hidden="true" />
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {healthy}/{total} reporting
          </span>
        </div>

        {/* Clock + date stack */}
        <div className="text-right" aria-live="off">
          <Time
            value={now}
            mode="clock-24h-mono"
            className="block text-lg font-medium tabular-nums"
          />
          <Time
            value={now}
            mode="absolute"
            precision="short-date"
            className="block font-mono text-[9.5px] uppercase tracking-wider text-muted-foreground"
          />
        </div>
      </div>
    </header>
  )
}
