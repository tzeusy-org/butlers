// ---------------------------------------------------------------------------
// Scrubber — shared time-scrubber for the Chronicles page (bu-ig72b.23)
//
// Owns the shared playhead timestamp that drives both the Gantt cursor and the
// map playhead. The scrubber position snaps to the nearest OwnTracks point
// event in the current window (no smoothing, v1 per D12).
//
// State model:
//   - scrubberMs: raw slider value in epoch ms (controlled by the range input)
//   - snappedMs:  nearest point event ms (or null if no point events exist)
//
// Both values are emitted upward via onScrub(scrubberMs, snappedMs) so that
// ChroniclesPage can pass them down to GanttSwimlane and MapWidget.
//
// Reset behavior:
//   - The parent passes `key={windowKey}` so that the component is remounted
//     when the time window changes, which resets `scrubberMs` to windowStartMs.
//
// Empty-state:
//   - The slider is always visible when the window is valid.
//   - When there are no point events, `snappedMs` is null and the map shows
//     its own empty state (no playhead marker).
//
// Update cadence:
//   - onScrub fires synchronously on every slider input change. The map
//     marker uses interpolation between trail samples, so the parent can
//     update the playhead position smoothly as scrubberMs changes; there is
//     no per-tick map repaint to debounce.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react"

import type { ChroniclerPointEvent } from "@/api/types"
import { useChroniclesTimezone } from "./use-chronicles-timezone"
import { formatScrubberLabel } from "./tz-format"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Snap `valueMs` to the nearest point event timestamp.
 * Returns null if pointEvents is empty.
 */
function snapToNearest(valueMs: number, pointEvents: ChroniclerPointEvent[]): number | null {
  if (pointEvents.length === 0) return null

  let best: ChroniclerPointEvent = pointEvents[0]
  let bestDelta = Math.abs(
    new Date(pointEvents[0].canonical_occurred_at).getTime() - valueMs,
  )

  for (let i = 1; i < pointEvents.length; i++) {
    const delta = Math.abs(
      new Date(pointEvents[i].canonical_occurred_at).getTime() - valueMs,
    )
    if (delta < bestDelta) {
      bestDelta = delta
      best = pointEvents[i]
    }
  }

  return new Date(best.canonical_occurred_at).getTime()
}


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScrubberProps {
  /** Window start (inclusive). */
  windowStart: Date
  /** Window end (inclusive). */
  windowEnd: Date
  /** Point events to snap to. May be empty (shows scrubber without snapping). */
  pointEvents: ChroniclerPointEvent[]
  /**
   * Called when the scrubber position changes (debounced 60 ms).
   * @param scrubberMs - raw slider position in epoch ms
   * @param snappedMs  - nearest point event ms, or null if no point events
   */
  onScrub: (scrubberMs: number, snappedMs: number | null) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Single time-scrubber that emits a shared timestamp for the Gantt cursor and
 * map playhead. Snaps to the nearest point event; debounces onScrub 60 ms for
 * smooth map rendering.
 *
 * Renders an HTML range input. No new npm dependencies.
 *
 * The parent MUST pass `key={windowKey}` so that the component remounts when
 * the time window changes, which resets the scrubber to the window start.
 */
export function Scrubber({ windowStart, windowEnd, pointEvents, onScrub }: ScrubberProps) {
  const windowStartMs = windowStart.getTime()
  const windowEndMs = windowEnd.getTime()
  const windowDurationMs = Math.max(1, windowEndMs - windowStartMs)

  // Owner timezone from context (default: Asia/Singapore).
  const tz = useChroniclesTimezone()

  // Initialized once per mount (parent resets via key prop when window changes).
  const [scrubberMs, setScrubberMs] = useState<number>(windowStartMs)

  // Snap to nearest point event — recomputed only when inputs change.
  const snappedMs = useMemo(
    () => snapToNearest(scrubberMs, pointEvents),
    [scrubberMs, pointEvents],
  )

  // Emit synchronously whenever scrubberMs or snappedMs changes. The map
  // marker is driven by raw scrubberMs (with interpolation) so the parent
  // wants every tick — no debouncing.
  useEffect(() => {
    onScrub(scrubberMs, snappedMs)
  }, [scrubberMs, snappedMs, onScrub])

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setScrubberMs(Number(e.target.value))
  }, [])

  const label = formatScrubberLabel(snappedMs ?? scrubberMs, windowDurationMs, tz)

  return (
    <div className="flex items-center gap-3 w-full" data-testid="scrubber">
      {/* Window start label */}
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {formatScrubberLabel(windowStartMs, windowDurationMs, tz)}
      </span>

      {/* Range input */}
      <div className="relative grow">
        <input
          type="range"
          min={windowStartMs}
          max={windowEndMs}
          step={Math.max(1, Math.floor(windowDurationMs / 1000))}
          value={scrubberMs}
          onChange={handleChange}
          aria-label="Timeline scrubber"
          className="w-full accent-primary cursor-pointer"
          data-testid="scrubber-input"
        />
      </div>

      {/* Window end label */}
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        {formatScrubberLabel(windowEndMs, windowDurationMs, tz)}
      </span>

      {/* Current position label */}
      <span
        className="shrink-0 text-xs font-medium tabular-nums min-w-[4rem] text-right"
        data-testid="scrubber-label"
        aria-live="polite"
        aria-atomic="true"
      >
        {label}
      </span>

      {/* No-events hint */}
      {pointEvents.length === 0 && (
        <span className="shrink-0 text-xs text-muted-foreground italic">
          No location points
        </span>
      )}
    </div>
  )
}
