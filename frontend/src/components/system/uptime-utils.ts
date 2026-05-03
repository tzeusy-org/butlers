/**
 * Uptime formatting utilities for UptimeTile.
 */

/**
 * Format a duration given in seconds as "Xd Yh Zm".
 * Omits leading zero components (e.g. "2h 5m", not "0d 2h 5m").
 * Always shows at least "0m" so the field is never blank.
 */
export function formatUptimeParts(seconds: number): string {
  const totalMinutes = Math.floor(Math.max(seconds, 0) / 60)
  const d = Math.floor(totalMinutes / (60 * 24))
  const h = Math.floor((totalMinutes % (60 * 24)) / 60)
  const m = totalMinutes % 60

  const parts: string[] = []
  if (d > 0) parts.push(`${d}d`)
  if (h > 0) parts.push(`${h}h`)
  parts.push(`${m}m`)
  return parts.join(" ")
}
