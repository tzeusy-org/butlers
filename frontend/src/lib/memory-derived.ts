/**
 * Pure derived values for the /memory house-ledger redesign.
 *
 * These are the client-side display computations the memory registers and
 * detail pages share. They are intentionally pure (no React, no fetching) so
 * they can be unit-tested exhaustively and reused across bands.
 *
 * Binding docs:
 * - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §4 "Belief typography"
 * - pr/overview/memory-redesign/prompts/00-foundation.md §"Derived values"
 *
 * Source of truth caveats:
 * - `effectiveConfidence` is the *display* value (belief column, detail-page
 *   arithmetic line). It does NOT drive dimming — rows dim when the server
 *   reports `validity === 'fading'`, because the server owns the threshold.
 */

import type { Fact } from '@/api/types.ts'

const MS_PER_DAY = 1000 * 60 * 60 * 24

/** Clamp a value into the inclusive [0, 1] range. NaN clamps to 0. */
function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0
  if (value < 0) return 0
  if (value > 1) return 1
  return value
}

/**
 * Fractional days elapsed between an ISO timestamp and a reference instant.
 *
 * Returns 0 for missing/unparseable input and never returns a negative value
 * (a future `from` timestamp clamps to 0 elapsed days), so decay can only ever
 * reduce confidence, never amplify it.
 */
export function daysSince(fromIso: string | null | undefined, now: Date = new Date()): number {
  if (!fromIso) return 0
  const fromMs = Date.parse(fromIso)
  if (Number.isNaN(fromMs)) return 0
  const elapsed = (now.getTime() - fromMs) / MS_PER_DAY
  return elapsed > 0 ? elapsed : 0
}

/**
 * Effective (decayed) confidence for a fact.
 *
 *   effective = confidence * exp(-decay_rate * daysSince(last_confirmed_at ?? created_at))
 *
 * `decay_rate` is a per-day rate. Days elapsed are fractional. The result is
 * clamped to [0, 1] so display never shows an out-of-range belief value even
 * if the stored `confidence` is itself slightly out of range.
 *
 * Dimming does NOT use this value — dim on server `validity === 'fading'`.
 *
 * @param fact  The fact to evaluate (uses confidence, decay_rate, timestamps).
 * @param now   Reference instant (injectable for deterministic tests).
 */
export function effectiveConfidence(fact: Fact, now: Date = new Date()): number {
  const elapsedDays = daysSince(fact.last_confirmed_at ?? fact.created_at, now)
  const decayed = fact.confidence * Math.exp(-fact.decay_rate * elapsedDays)
  return clamp01(decayed)
}

/**
 * Two-letter mono permanence tag for the belief column.
 *
 * permanent→pm · stable→st · standard→sd · volatile→vo · ephemeral→ep.
 * Unknown permanence values fall back to the raw string so the surface fails
 * visibly rather than silently mapping a new backend value to a wrong tag.
 */
export function permanenceTag(permanence: string): string {
  switch (permanence) {
    case 'permanent':
      return 'pm'
    case 'stable':
      return 'st'
    case 'standard':
      return 'sd'
    case 'volatile':
      return 'vo'
    case 'ephemeral':
      return 'ep'
    default:
      return permanence
  }
}

/** Consolidation status values that map to a glyph. */
export type ConsolidationStatus = 'pending' | 'consolidated' | 'dead_letter' | 'failed'

/** The {◦ • ✕} glyph set used in the daybook and elsewhere. */
export const CONSOLIDATION_GLYPHS = {
  pending: '◦',
  consolidated: '•',
  dead_letter: '✕',
} as const

/**
 * Map an episode consolidation status to its glyph.
 *
 *   pending → '◦' (hollow) · consolidated → '•' (filled) ·
 *   dead_letter / failed → '✕' (the only `--red` glyph).
 *
 * Accepts a bare status string or any object carrying a `status` field (e.g. an
 * Episode). Unknown statuses fall back to the pending hollow glyph rather than
 * rendering nothing, so a row is never glyph-less.
 */
export function consolidationGlyph(input: string | { status?: string | null }): string {
  const status = typeof input === 'string' ? input : (input.status ?? '')
  switch (status) {
    case 'consolidated':
      return CONSOLIDATION_GLYPHS.consolidated
    case 'dead_letter':
    case 'failed':
      return CONSOLIDATION_GLYPHS.dead_letter
    case 'pending':
    default:
      return CONSOLIDATION_GLYPHS.pending
  }
}
