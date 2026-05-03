// ---------------------------------------------------------------------------
// session-stripe-utils.ts — pure helpers and data hook for SessionStripeChart
// ---------------------------------------------------------------------------

import { useQuery } from "@tanstack/react-query"
import { getSessions } from "@/api/index.ts"

// ---------------------------------------------------------------------------
// Row type
// ---------------------------------------------------------------------------

export interface SessionBucketRow {
  bucket: string
  [butlerName: string]: string | number
}

// ---------------------------------------------------------------------------
// Time bucketing (UTC-based throughout)
// ---------------------------------------------------------------------------

/** Infer whether to use hourly or daily buckets based on window span. */
export function bucketUnit(from: Date, to: Date): "hour" | "day" {
  const spanMs = to.getTime() - from.getTime()
  const spanHours = spanMs / (1000 * 60 * 60)
  return spanHours <= 48 ? "hour" : "day"
}

/** Truncate a Date to its bucket floor in UTC (hour or day). */
function bucketFloor(d: Date, unit: "hour" | "day"): Date {
  const ms = d.getTime()
  if (unit === "hour") {
    return new Date(Math.floor(ms / (60 * 60 * 1000)) * (60 * 60 * 1000))
  }
  return new Date(Math.floor(ms / (24 * 60 * 60 * 1000)) * (24 * 60 * 60 * 1000))
}

/** ISO string key for a bucket (YYYY-MM-DDTHH for hours, YYYY-MM-DD for days). All UTC. */
export function bucketKey(d: Date, unit: "hour" | "day"): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  const year = d.getUTCFullYear()
  const month = pad(d.getUTCMonth() + 1)
  const day = pad(d.getUTCDate())
  if (unit === "hour") {
    return `${year}-${month}-${day}T${pad(d.getUTCHours())}`
  }
  return `${year}-${month}-${day}`
}

/** Generate the complete ordered list of bucket keys for the window. */
function generateBuckets(from: Date, to: Date, unit: "hour" | "day"): string[] {
  const keys: string[] = []
  const step = unit === "hour" ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000
  let cursor = bucketFloor(from, unit).getTime()
  const end = to.getTime()

  while (cursor <= end) {
    keys.push(bucketKey(new Date(cursor), unit))
    cursor += step
  }
  return keys
}

/** Human-readable X-axis label for a UTC bucket key. */
export function formatBucketKey(key: string, unit: "hour" | "day"): string {
  if (unit === "hour") {
    // key format: YYYY-MM-DDTHH (UTC)
    const [, time] = key.split("T")
    if (!time) return key
    const hour = parseInt(time, 10)
    const suffix = hour >= 12 ? "pm" : "am"
    const display = hour % 12 === 0 ? 12 : hour % 12
    return `${display}${suffix}`
  }
  // key format: YYYY-MM-DD (UTC noon to avoid offset edge-cases)
  const [year, month, day] = key.split("-").map(Number)
  const d = new Date(Date.UTC(year, month - 1, day, 12, 0, 0))
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
}

// ---------------------------------------------------------------------------
// Pivot function
// ---------------------------------------------------------------------------

/** Aggregate raw session list into per-bucket per-butler counts. */
export function pivotSessionsIntoRows(
  sessions: Array<{ butler?: string; started_at: string }>,
  from: Date,
  to: Date,
  unit: "hour" | "day",
): SessionBucketRow[] {
  const keys = generateBuckets(from, to, unit)
  const rowMap: Record<string, SessionBucketRow> = {}
  for (const key of keys) {
    rowMap[key] = { bucket: key }
  }

  for (const session of sessions) {
    if (!session.butler) continue
    const d = new Date(session.started_at)
    if (isNaN(d.getTime())) continue
    const key = bucketKey(bucketFloor(d, unit), unit)
    if (!(key in rowMap)) continue
    const row = rowMap[key]
    const prev = typeof row[session.butler] === "number" ? (row[session.butler] as number) : 0
    row[session.butler] = prev + 1
  }

  return keys.map((k) => rowMap[k])
}

// ---------------------------------------------------------------------------
// Data fetch hook
// ---------------------------------------------------------------------------

/** Maximum sessions fetched per request — capped at 200 (backend Query constraint). */
const MAX_SESSIONS_LIMIT = 200

/** Compute the rolling window boundaries for the given span in hours. */
function rollingWindow(windowHours: number): { from: Date; to: Date } {
  const now = Date.now()
  return {
    from: new Date(now - windowHours * 60 * 60 * 1000),
    to: new Date(now),
  }
}

/**
 * Fetch sessions for a rolling window of `windowHours` hours, with
 * client-side aggregation.
 *
 * Window boundaries are recomputed inside `queryFn` on every refetch so the
 * chart always shows the *current* trailing window, not a closed interval
 * frozen at mount time.
 *
 * Pass `refetchInterval` from `useAutoRefresh()` so the user's auto-refresh
 * preference is honoured. Use `false` to disable polling entirely.
 */
export function useSessionStripeData(windowHours = 24, refetchInterval: number | false = 60_000) {
  return useQuery({
    // Key on window length only; refetchInterval drives the rolling advance.
    queryKey: ["session-stripe", windowHours],
    queryFn: () => {
      const w = rollingWindow(windowHours)
      return getSessions({
        since: w.from.toISOString(),
        until: w.to.toISOString(),
        limit: MAX_SESSIONS_LIMIT,
        offset: 0,
      })
    },
    refetchInterval,
  })
}

/** Return the current rolling window boundaries for pivot/display use. */
export function currentWindow(windowHours = 24): { from: Date; to: Date } {
  return rollingWindow(windowHours)
}
