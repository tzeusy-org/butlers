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

/** Page size for each paginated fetch request. */
const PAGE_SIZE = 200

/** Hard cap on total sessions fetched across all pages. */
export const SESSIONS_HARD_CAP = 1000

/** When total exceeds this threshold, a backpressure warning is surfaced. */
export const SESSIONS_WARN_THRESHOLD = 1000

/** Shape returned by useSessionStripeData — extends PaginatedResponse with backpressure flag. */
export interface SessionStripeResult {
  data: Array<{ butler?: string; started_at: string }>
  meta: { total: number; offset: number; limit: number; has_more: boolean }
  /** True when the backend total exceeded SESSIONS_HARD_CAP and results are truncated. */
  truncated: boolean
}

/** Compute the rolling window boundaries for the given span in hours. */
function rollingWindow(windowHours: number): { from: Date; to: Date } {
  const now = Date.now()
  return {
    from: new Date(now - windowHours * 60 * 60 * 1000),
    to: new Date(now),
  }
}

/**
 * Fetch all sessions for a rolling window of `windowHours` hours, paginating
 * across backend pages until either all sessions are collected or SESSIONS_HARD_CAP
 * is reached.
 *
 * When total sessions exceed SESSIONS_HARD_CAP, the result is truncated and
 * `truncated: true` is set so the UI can surface a backpressure warning.
 *
 * Window boundaries are recomputed inside `queryFn` on every refetch so the
 * chart always shows the *current* trailing window, not a closed interval
 * frozen at mount time.
 *
 * Pass `refetchInterval` from `useAutoRefresh()` so the user's auto-refresh
 * preference is honoured. Use `false` to disable polling entirely.
 */
async function fetchAllSessionsForWindow(windowHours: number): Promise<SessionStripeResult> {
  const w = rollingWindow(windowHours)
  const allSessions: Array<{ butler?: string; started_at: string }> = []
  let offset = 0
  let total = 0
  let lastMeta = { total: 0, offset: 0, limit: PAGE_SIZE, has_more: false }

  while (true) {
    const page = await getSessions({
      since: w.from.toISOString(),
      until: w.to.toISOString(),
      limit: PAGE_SIZE,
      offset,
    })

    total = page.meta.total
    lastMeta = page.meta
    allSessions.push(...page.data)

    // Stop if we've hit the hard cap
    if (allSessions.length >= SESSIONS_HARD_CAP) {
      return {
        data: allSessions.slice(0, SESSIONS_HARD_CAP),
        meta: { ...lastMeta, total },
        truncated: true,
      }
    }

    // Stop if there are no more pages
    if (!page.meta.has_more) {
      break
    }

    offset += PAGE_SIZE
  }

  return {
    data: allSessions,
    meta: { ...lastMeta, total },
    truncated: false,
  }
}

export function useSessionStripeData(windowHours = 24, refetchInterval: number | false = 60_000) {
  return useQuery({
    // Key on window length only; refetchInterval drives the rolling advance.
    queryKey: ["session-stripe", windowHours],
    queryFn: () => fetchAllSessionsForWindow(windowHours),
    refetchInterval,
  })
}

/** Return the current rolling window boundaries for pivot/display use. */
export function currentWindow(windowHours = 24): { from: Date; to: Date } {
  return rollingWindow(windowHours)
}
