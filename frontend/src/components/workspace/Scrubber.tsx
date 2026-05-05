// ---------------------------------------------------------------------------
// Scrubber — shared time-scrubber for workspace-pattern pages (bu-ig72b.23)
//
// Owns the shared playhead timestamp. The scrubber position snaps to the
// nearest timestamp in `snapMs` (if provided). When no snap points are given
// the slider still works; `snappedMs` is null and callers decide how to handle
// that (e.g. show the raw position, or skip the snap indicator).
//
// State model:
//   - scrubberMs: raw slider value in epoch ms (controlled by the range input)
//   - snappedMs:  nearest snap point ms (or null if no snap points exist)
//
// Both values are emitted upward via onScrub(scrubberMs, snappedMs).
//
// Reset behavior:
//   - The parent passes `key={windowKey}` so that the component is remounted
//     when the time window changes, which resets `scrubberMs` to windowStartMs.
//
// Empty-state:
//   - The slider is always visible when the window is valid.
//   - When there are no snap points, `snappedMs` is null.
//
// Update cadence:
//   - onScrub fires synchronously on every slider input change.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react"

import { DEFAULT_TZ, formatScrubberLabel } from "@/components/chronicles/tz-format"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Snap `valueMs` to the nearest timestamp in the snapMs array.
 * Returns null if snapMs is empty.
 *
 * Exported for direct unit testing.
 */
export function snapToNearest(valueMs: number, snapMs: number[]): number | null {
  if (snapMs.length === 0) return null

  let best = snapMs[0]
  let bestDelta = Math.abs(snapMs[0] - valueMs)

  for (let i = 1; i < snapMs.length; i++) {
    const delta = Math.abs(snapMs[i] - valueMs)
    if (delta < bestDelta) {
      bestDelta = delta
      best = snapMs[i]
    }
  }

  return best
}


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScrubberProps {
  /** Window start (inclusive). */
  windowStart: Date
  /** Window end (inclusive). */
  windowEnd: Date
  /**
   * Epoch-millisecond timestamps to snap to. May be empty (scrubber still
   * renders, snappedMs emitted as null). Pass an empty array when there are
   * no discrete snap points (e.g. continuous cost data).
   */
  snapMs?: number[]
  /**
   * IANA timezone for label formatting. Defaults to Asia/Singapore.
   * Pass the owner's configured timezone here.
   */
  tz?: string
  /**
   * Called synchronously when the scrubber position changes.
   * @param scrubberMs - raw slider position in epoch ms
   * @param snappedMs  - nearest snap point ms, or null if no snap points
   */
  onScrub: (scrubberMs: number, snappedMs: number | null) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Single time-scrubber that emits a shared timestamp for driving domain
 * visualisations. Snaps to the nearest point in `snapMs` when provided;
 * otherwise emits the raw slider position with snappedMs=null.
 *
 * Renders an HTML range input. No new npm dependencies.
 *
 * The parent MUST pass `key={windowKey}` so that the component remounts when
 * the time window changes, which resets the scrubber to the window start.
 */
export function Scrubber({
  windowStart,
  windowEnd,
  snapMs = [],
  tz = DEFAULT_TZ,
  onScrub,
}: ScrubberProps) {
  const windowStartMs = windowStart.getTime()
  const windowEndMs = windowEnd.getTime()
  const windowDurationMs = Math.max(1, windowEndMs - windowStartMs)

  // Initialized once per mount (parent resets via key prop when window changes).
  const [scrubberMs, setScrubberMs] = useState<number>(windowStartMs)

  // Snap to nearest snap point — recomputed only when inputs change.
  const snappedMs = useMemo(
    () => snapToNearest(scrubberMs, snapMs),
    [scrubberMs, snapMs],
  )

  // Emit synchronously whenever scrubberMs or snappedMs changes.
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
    </div>
  )
}
