/**
 * Database size formatting utilities for DbSizeTile.
 */

/**
 * Format bytes as a human-readable string with at most one decimal place.
 * Examples: 0 B, 512 B, 1.3 KB, 42.7 MB, 1.3 GB
 * Negative values are clamped to zero.
 */
export function humanizeBytes(bytes: number): string {
  const safeBytes = Math.max(0, bytes)
  const KILO = 1_024
  const MEGA = KILO * KILO
  const GIGA = MEGA * KILO

  if (safeBytes < KILO) return `${Math.floor(safeBytes)} B`
  if (safeBytes < MEGA) return `${(safeBytes / KILO).toFixed(1)} KB`
  if (safeBytes < GIGA) return `${(safeBytes / MEGA).toFixed(1)} MB`
  return `${(safeBytes / GIGA).toFixed(1)} GB`
}
