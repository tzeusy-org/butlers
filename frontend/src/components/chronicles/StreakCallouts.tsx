// ---------------------------------------------------------------------------
// StreakCallouts — bu-ig72b.34
//
// Renders a horizontal row of streak callouts, one per category, showing the
// longest contiguous span (no gap > 5 min) in the current episodes window.
//
// - Consumes useChroniclesEpisodes() to get raw episode data.
// - Calls findLongestStreaks() for pure frontend computation.
// - Hides entirely when no streak meets the 30 min minimum threshold.
// - Display format: "{label} streak: {hh:mm}"
// ---------------------------------------------------------------------------

import { useMemo } from "react"
import type { ChroniclerEpisodesParams } from "@/api/types"
import { useChroniclesEpisodes } from "@/hooks/use-chronicles"
import { LANE_TAXONOMY } from "./lane-taxonomy"
import { findLongestStreaks } from "./streak-utils"

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

/** Format a duration in seconds as hh:mm. */
function formatDuration(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface StreakCalloutsProps {
  /** Pass-through params for the episodes query (e.g. time window). */
  episodeParams?: ChroniclerEpisodesParams
  /** Refetch interval passed to the episodes hook (ms or false to disable). */
  refetchInterval?: number | false
}

export function StreakCallouts({ episodeParams, refetchInterval }: StreakCalloutsProps) {
  const { data } = useChroniclesEpisodes(episodeParams, { refetchInterval })
  const streaks = useMemo(() => findLongestStreaks(data?.data ?? []), [data])

  // Hide entirely when no streaks pass the 30-min threshold
  if (streaks.length === 0) return null

  return (
    <div
      className="flex flex-wrap gap-2"
      aria-label="Longest activity streaks"
      data-testid="streak-callouts"
    >
      {streaks.map((streak) => {
        const lane = LANE_TAXONOMY[streak.category]
        const label = lane?.label ?? streak.category
        const duration = formatDuration(streak.durationSeconds)
        return (
          <span
            key={streak.category}
            className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium text-white ${lane?.colour ?? "bg-slate-500"}`}
          >
            {label} streak: {duration}
          </span>
        )
      })}
    </div>
  )
}
