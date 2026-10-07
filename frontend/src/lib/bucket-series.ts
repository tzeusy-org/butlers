/** Count truth is keyed by source instants; volume never supplies listening. */
export type ListeningState = "live" | "deaf" | "unknown"
export interface CountBucket {
  bucket_start: string
  bucket_end: string
  count: number | null
  filtered?: number | null
  counts_partial?: boolean
  listening: ListeningState
}
export interface BucketWindow {
  window_start: string
  window_end: string
  bucket_width_s: number
  counts_available: boolean
}

/** Reject ambiguous keys rather than inventing a time axis from array order. */
export function orderedBuckets(input: readonly CountBucket[]): CountBucket[] {
  const seen = new Set<number>()
  const result: CountBucket[] = []
  for (const bucket of input) {
    const start = Date.parse(bucket.bucket_start)
    const end = Date.parse(bucket.bucket_end)
    if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end || seen.has(start)) return []
    if (bucket.count !== null && (!Number.isFinite(bucket.count) || bucket.count < 0)) return []
    if (bucket.filtered != null && (!Number.isFinite(bucket.filtered) || bucket.filtered < 0)) return []
    if (!["live", "deaf", "unknown"].includes(bucket.listening)) return []
    seen.add(start)
    result.push(bucket)
  }
  result.sort((a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start))
  if (result.some((bucket, index) => index > 0 &&
    Date.parse(result[index - 1].bucket_end) > Date.parse(bucket.bucket_start))) return []
  return result
}

/** Only an explicit successfully read window can authorize missing count zero. */
export function denseCountBuckets(input: readonly CountBucket[], window: BucketWindow): CountBucket[] {
  const start = Date.parse(window.window_start), end = Date.parse(window.window_end)
  const width = window.bucket_width_s * 1000
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(width > 0) || end <= start ||
      (end - start) % width !== 0 || (end - start) / width > 1000) return []
  const ordered = orderedBuckets(input)
  if (input.length && !ordered.length) return []
  const keyed = new Map(ordered.map(bucket => [Date.parse(bucket.bucket_start), bucket]))
  if (ordered.some(bucket => (Date.parse(bucket.bucket_start) - start) % width !== 0 ||
      Date.parse(bucket.bucket_start) < start || Date.parse(bucket.bucket_end) > end ||
      Date.parse(bucket.bucket_end) - Date.parse(bucket.bucket_start) !== width)) return []
  return Array.from({ length: (end - start) / width }, (_, index) => {
    const instant = start + index * width
    return keyed.get(instant) ?? {
      bucket_start: new Date(instant).toISOString(), bucket_end: new Date(instant + width).toISOString(),
      count: window.counts_available ? 0 : null, filtered: window.counts_available ? 0 : null,
      listening: "unknown",
    }
  })
}

/** Existing session analytics supply these actual keys, including a partial current hour. */
export function sessionCountBuckets(input: readonly { hour_start: string; sessions_count: number }[]): CountBucket[] {
  if (input.some(bucket => !Number.isFinite(Date.parse(bucket.hour_start)))) return []
  return orderedBuckets(input.map(bucket => ({
    bucket_start: bucket.hour_start,
    bucket_end: new Date(Date.parse(bucket.hour_start) + 3_600_000).toISOString(),
    count: bucket.sessions_count, listening: "unknown",
  })))
}

/** A list fetch pins its own explicit [from,to) window; no wall-clock fallback here. */
export function bucketSessions(
  sessions: readonly { butler?: string; started_at: string }[], butler: string,
  from: Date, to: Date, countsAvailable = true,
): CountBucket[] {
  const keyed = new Map<number, number>()
  const start = from.getTime(), end = to.getTime()
  for (const session of sessions) {
    const instant = Date.parse(session.started_at)
    if (session.butler !== butler || !Number.isFinite(instant) || instant < start || instant >= end) continue
    const key = start + Math.floor((instant - start) / 3_600_000) * 3_600_000
    keyed.set(key, (keyed.get(key) ?? 0) + 1)
  }
  return denseCountBuckets([...keyed].map(([key, count]) => ({
    bucket_start: new Date(key).toISOString(), bucket_end: new Date(key + 3_600_000).toISOString(),
    count, listening: "unknown",
  })), { window_start: from.toISOString(), window_end: to.toISOString(),
    bucket_width_s: 3600, counts_available: countsAvailable })
}
