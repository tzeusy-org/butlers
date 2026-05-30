/**
 * deriveMinuteCounts — compute per-minute event density from timestamps.
 *
 * Used by HourFlameStrip to build the 60-bar per-minute density view for
 * an hour group in the Timeline ledger.
 */

/**
 * Compute 60-element minute-bucket counts from an array of received_at ISO strings.
 *
 * All timestamps should fall within the same hour (caller's responsibility).
 * Timestamps outside [hourStart, hourStart+60min) are silently ignored.
 *
 * @param timestamps - Array of ISO-format timestamps (may contain null/undefined).
 * @param hourStart  - ISO string for the start of the hour bucket (e.g. "2026-05-17T14:00:00Z").
 * @returns          - 60-element array where index = minute offset within the hour.
 */
export function deriveMinuteCounts(
  timestamps: (string | null | undefined)[],
  hourStart: string,
): number[] {
  const counts: number[] = Array(60).fill(0)
  if (!hourStart) return counts

  const hourStartMs = new Date(hourStart).getTime()
  if (isNaN(hourStartMs)) return counts

  for (const ts of timestamps) {
    if (!ts) continue
    try {
      const ms = new Date(ts).getTime()
      if (isNaN(ms)) continue
      const minuteOffset = Math.floor((ms - hourStartMs) / 60_000)
      if (minuteOffset >= 0 && minuteOffset < 60) {
        counts[minuteOffset]++
      }
    } catch {
      // malformed timestamp — skip
    }
  }
  return counts
}
