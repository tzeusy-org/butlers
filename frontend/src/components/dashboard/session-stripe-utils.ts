// ---------------------------------------------------------------------------
// session-stripe-utils.ts — pure helpers and data hook for SessionStripeChart
// ---------------------------------------------------------------------------

import { useQuery } from "@tanstack/react-query"
import { getSessions } from "@/api/index.ts"
import type { KeysetMeta, SessionParams } from "@/api/types.ts"

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

/** Maximum sessions fetched in a single request — also the hard cap. */
export const SESSIONS_HARD_CAP = 1000

/** Shape returned by useSessionStripeData — keyset list plus a backpressure flag. */
export interface SessionStripeResult {
  data: Array<{ butler?: string; started_at: string }>
  meta: KeysetMeta
  /** True when more rows exist beyond SESSIONS_HARD_CAP (results are truncated). */
  truncated: boolean
}

/** Categorical filter fields the stripe chart forwards to the session list. */
export type StripeFilterParams = Pick<
  SessionParams,
  "butler" | "trigger_source" | "status" | "request_id" | "since" | "until"
>

/** Parse a filter date string ("YYYY-MM-DD" or ISO) into a Date, or null. */
function parseFilterDate(raw: string | undefined, endOfDay: boolean): Date | null {
  if (!raw) return null
  // Date-only inputs anchor to start/end of the UTC day so the pivot window
  // covers the whole calendar day the user selected.
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(raw)
  const d = new Date(dateOnly ? `${raw}T${endOfDay ? "23:59:59.999" : "00:00:00.000"}Z` : raw)
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * Compute the window boundaries for the stripe chart.
 *
 * When the active filters carry `since`/`until`, those bound the window so the
 * chart matches the table's date filter. Otherwise it falls back to a rolling
 * trailing window of `windowHours` hours ending now.
 */
function resolveWindow(
  windowHours: number,
  filterParams?: StripeFilterParams,
): { from: Date; to: Date } {
  const now = Date.now()
  const since = parseFilterDate(filterParams?.since, false)
  const until = parseFilterDate(filterParams?.until, true)
  const to = until ?? new Date(now)
  const from = since ?? new Date(to.getTime() - windowHours * 60 * 60 * 1000)
  return { from, to }
}

/**
 * Fetch sessions for the chart window in a single request capped at
 * SESSIONS_HARD_CAP rows.
 *
 * When more rows exist beyond the cap, the result is marked `truncated: true`
 * (derived from keyset `meta.has_more`) so the UI can surface a backpressure
 * warning. Using a single request avoids repeated fan-out on the backend.
 *
 * Window boundaries are recomputed inside `queryFn` on every refetch so the
 * chart always shows the *current* window (rolling, or the filtered range),
 * not a closed interval frozen at mount time. Active categorical filters
 * (butler/trigger/status/request_id) are forwarded so the chart matches the
 * page's filter set.
 *
 * Pass `refetchInterval` from `useAutoRefresh()` so the user's auto-refresh
 * preference is honoured. Use `false` to disable polling entirely.
 */
async function fetchAllSessionsForWindow(
  windowHours: number,
  filterParams?: StripeFilterParams,
): Promise<SessionStripeResult> {
  const w = resolveWindow(windowHours, filterParams)
  const page = await getSessions({
    ...filterParams,
    since: w.from.toISOString(),
    until: w.to.toISOString(),
    limit: SESSIONS_HARD_CAP,
  })

  return {
    data: page.data,
    meta: page.meta,
    // has_more is true only when more rows exist beyond SESSIONS_HARD_CAP
    truncated: page.meta.has_more,
  }
}

export function useSessionStripeData(
  windowHours = 24,
  refetchInterval: number | false = 60_000,
  filterParams?: StripeFilterParams,
) {
  return useQuery({
    // Key on window length + filters; refetchInterval drives the rolling advance.
    queryKey: ["session-stripe", windowHours, filterParams ?? null],
    queryFn: () => fetchAllSessionsForWindow(windowHours, filterParams),
    refetchInterval,
  })
}

/** Return the current window boundaries for pivot/display use. */
export function currentWindow(
  windowHours = 24,
  filterParams?: StripeFilterParams,
): { from: Date; to: Date } {
  return resolveWindow(windowHours, filterParams)
}
