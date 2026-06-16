// ---------------------------------------------------------------------------
// session-buckets.ts — shared bucketing utilities for per-butler hourly stripes
//
// Extracted from session-stripe-utils.ts so that both SessionStripeChart and
// useButlerStatusBoard share one source of truth for the bucketing logic.
// ---------------------------------------------------------------------------

/** Session shape required by bucket utilities. */
export interface BucketableSession {
  butler?: string | null
  started_at: string
}

// ---------------------------------------------------------------------------
// Core bucketing helpers (timezone-aware)
// ---------------------------------------------------------------------------

/**
 * Truncate a Date to its UTC-hour floor and return as epoch ms.
 *
 * Bucketing is done in UTC because session timestamps stored by the server are
 * UTC ISO strings.
 */
function utcHourFloor(d: Date): number {
  return Math.floor(d.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000)
}

/**
 * Bucket sessions by started_at into 24 hourly slots, oldest first.
 *
 * The 24 slots represent the trailing 24 hours ending *now* (or at the
 * provided `endAt`). Slot 0 is the oldest hour bucket; slot 23 is the
 * most-recent hour bucket.
 *
 * Sessions that fall outside the 24-hour window are silently ignored.
 * Sessions with unparseable started_at are silently ignored.
 *
 * @param sessions - Array of sessions (any butler, any time)
 * @param butlerName - Only sessions where session.butler === butlerName are counted
 * @param endAt - Window end reference (defaults to now). The actual window end is
 *   aligned to the next UTC-hour boundary: `utcHourFloor(endAt) + 1h`. The window
 *   therefore covers `[windowEnd - 24h, windowEnd)` where `windowEnd` is that
 *   hour-aligned boundary.
 */
export function bucketSessionsByHour(
  sessions: BucketableSession[],
  butlerName: string,
  endAt: Date = new Date(),
): number[] {
  const stripe = new Array<number>(24).fill(0)
  const windowEndMs = utcHourFloor(endAt) + 60 * 60 * 1000 // inclusive current hour
  const windowStartMs = windowEndMs - 24 * 60 * 60 * 1000 // 24 h before

  for (const session of sessions) {
    if (!session.butler || session.butler !== butlerName) continue
    const d = new Date(session.started_at)
    if (isNaN(d.getTime())) continue
    const bucketMs = utcHourFloor(d)
    if (bucketMs < windowStartMs || bucketMs >= windowEndMs) continue
    const slotIndex = Math.floor((bucketMs - windowStartMs) / (60 * 60 * 1000))
    if (slotIndex >= 0 && slotIndex < 24) {
      stripe[slotIndex]++
    }
  }

  return stripe
}
