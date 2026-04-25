// ---------------------------------------------------------------------------
// useTimeWindow — bu-ig72b.20
//
// Manages the active Chronicles time window and syncs it to URL params
// (?from=YYYY-MM-DD&to=YYYY-MM-DD).  Defaults to today.
//
// pollingDisabled is true when the window ends >= 24 h before now.
// Consumers should pass this flag to useAutoRefresh (bu-C5) when that
// hook is wired up; no further refactoring is needed.
// ---------------------------------------------------------------------------

import { useCallback, useMemo } from "react"
import { useSearchParams } from "react-router"
import { endOfDay, format, isValid, parseISO, startOfDay, subDays } from "date-fns"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PresetKey = "today" | "week" | "custom"

/** The resolved time window that all child widgets consume. */
export interface TimeWindow {
  from: Date
  to: Date
  /**
   * True when the window ends more than 24 hours before now.
   * Consumers should disable polling when this is true.
   * Wired to useAutoRefresh by bu-C5 — this flag is the only hook-in needed.
   */
  pollingDisabled: boolean
  /** Which preset is active, or "custom" for a hand-typed range. */
  preset: PresetKey
}

export interface UseTimeWindowResult extends TimeWindow {
  setPreset: (preset: "today" | "week") => void
  setCustomRange: (from: Date, to: Date) => void
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

const DATE_FMT = "yyyy-MM-dd"

/** Format a date as a URL-safe YYYY-MM-DD string. */
export function formatWindowDate(d: Date): string {
  return format(d, DATE_FMT)
}

/**
 * Returns true when the window ends more than 24 hours before now.
 * A window whose `to` is within the last 24 h is still considered "recent"
 * and eligible for polling.
 */
export function isPollingDisabled(to: Date): boolean {
  return Date.now() - to.getTime() >= 24 * 60 * 60 * 1000
}

function todayWindow(): { from: Date; to: Date } {
  const now = new Date()
  return { from: startOfDay(now), to: endOfDay(now) }
}

function weekWindow(): { from: Date; to: Date } {
  const now = new Date()
  return { from: startOfDay(subDays(now, 6)), to: endOfDay(now) }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

const PARAM_FROM = "from"
const PARAM_TO = "to"

export function useTimeWindow(): UseTimeWindowResult {
  const [searchParams, setSearchParams] = useSearchParams()

  const fromParam = searchParams.get(PARAM_FROM)
  const toParam = searchParams.get(PARAM_TO)

  // Memoize the parsed window so child widgets get stable Date references and
  // don't trigger redundant re-renders / useEffect re-runs when the parent
  // re-renders without the URL params actually changing.
  const window = useMemo((): TimeWindow => {
    if (fromParam && toParam) {
      const parsedFrom = parseISO(fromParam)
      const parsedTo = parseISO(toParam)
      if (isValid(parsedFrom) && isValid(parsedTo) && parsedFrom <= parsedTo) {
        const from = startOfDay(parsedFrom)
        const to = endOfDay(parsedTo)
        // Detect named presets so the buttons stay highlighted.
        const wk = weekWindow()
        const td = todayWindow()
        let preset: PresetKey
        if (
          formatWindowDate(from) === formatWindowDate(wk.from) &&
          formatWindowDate(to) === formatWindowDate(wk.to)
        ) {
          preset = "week"
        } else if (
          formatWindowDate(from) === formatWindowDate(td.from) &&
          formatWindowDate(to) === formatWindowDate(td.to)
        ) {
          preset = "today"
        } else {
          preset = "custom"
        }
        return { from, to, preset, pollingDisabled: isPollingDisabled(to) }
      }
    }
    // No params or invalid params — fall back to today
    const td = todayWindow()
    return { from: td.from, to: td.to, preset: "today", pollingDisabled: isPollingDisabled(td.to) }
  }, [fromParam, toParam])

  const setPreset = useCallback(
    (p: "today" | "week") => {
      const w = p === "today" ? todayWindow() : weekWindow()
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set(PARAM_FROM, formatWindowDate(w.from))
          next.set(PARAM_TO, formatWindowDate(w.to))
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const setCustomRange = useCallback(
    (f: Date, t: Date) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set(PARAM_FROM, formatWindowDate(f))
          next.set(PARAM_TO, formatWindowDate(t))
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  return { ...window, setPreset, setCustomRange }
}
