// ---------------------------------------------------------------------------
// source-state-utils — bu-ig72b.22
//
// Pure helper functions for SourceStateBadgeStrip badge state classification
// and tooltip text generation. Separated from the component file to satisfy
// the react-refresh/only-export-components lint rule.
// ---------------------------------------------------------------------------

import type { ChroniclerSourceStateRow } from "@/api/types"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type BadgeState = "active" | "inactive" | "planned" | "deferred"

// ---------------------------------------------------------------------------
// getBadgeState
// ---------------------------------------------------------------------------

/**
 * Classify a source adapter row into its display badge state.
 *
 * Returns null for `not_time_bearing` rows (never shown in the strip).
 */
export function getBadgeState(row: ChroniclerSourceStateRow): BadgeState | null {
  const compat = row.chronicler_compatibility
  if (compat === "not_time_bearing") return null
  if (compat === "planned") return "planned"
  if (compat === "deferred") return "deferred"
  // compat === "supported"
  return row.active ? "active" : "inactive"
}

// ---------------------------------------------------------------------------
// buildInactiveTooltip
// ---------------------------------------------------------------------------

/**
 * Build tooltip text for an inactive source adapter.
 *
 * Combines `inactive_reason` and `last_error` when both are present.
 * Falls back to a generic message when both are null.
 */
export function buildInactiveTooltip(row: ChroniclerSourceStateRow): string {
  const parts: string[] = []
  if (row.inactive_reason) parts.push(`Reason: ${row.inactive_reason}`)
  if (row.last_error) parts.push(`Last error: ${row.last_error}`)
  return parts.length > 0 ? parts.join("\n") : "Source is inactive with no details."
}
