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

import type { Episode, Fact } from '@/api/types.ts'

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

// ---------------------------------------------------------------------------
// Daybook (episodes register) day grouping
// ---------------------------------------------------------------------------

/** A day-bucket of episodes for the daybook (register=episodes). */
export interface DayGroup {
  /** Local-day key `YYYY-MM-DD` — stable across the group. */
  key: string
  /** Rendered header label: `TODAY` · `YESTERDAY` · `THU 12 JUN`. */
  label: string
  /** Episodes in this day, preserving the API's (reverse-chronological) order. */
  episodes: Episode[]
}

/** Local-day key `YYYY-MM-DD` for an instant (NOT UTC — grouping is local). */
function localDayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const WEEKDAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const
const MONTHS = [
  'JAN',
  'FEB',
  'MAR',
  'APR',
  'MAY',
  'JUN',
  'JUL',
  'AUG',
  'SEP',
  'OCT',
  'NOV',
  'DEC',
] as const

/**
 * Day header label for a day relative to `now`:
 *   today → "TODAY" · yesterday → "YESTERDAY" · older → "THU 12 JUN".
 */
function dayLabel(day: Date, now: Date): string {
  const dayKey = localDayKey(day)
  if (dayKey === localDayKey(now)) return 'TODAY'

  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (dayKey === localDayKey(yesterday)) return 'YESTERDAY'

  return `${WEEKDAYS[day.getDay()]} ${day.getDate()} ${MONTHS[day.getMonth()]}`
}

/**
 * Group episodes into days by local `created_at`, preserving the incoming
 * (reverse-chronological) order both across and within groups. Pure — operates
 * on a copy and never sorts; the API owns ordering. A day may legitimately
 * split across pages; this returns whatever the current page holds, and the
 * caller repeats the header at the top of the next page (re-grouping each page
 * yields exactly that). Adjacent same-day episodes share a group; a day that
 * reappears non-adjacently (page boundary) starts a fresh group.
 *
 * @param episodes  Episodes in API order (created_at desc).
 * @param now       Reference instant for TODAY/YESTERDAY labels (injectable).
 */
export function groupEpisodesByDay(episodes: Episode[], now: Date = new Date()): DayGroup[] {
  const groups: DayGroup[] = []
  let current: DayGroup | null = null

  for (const ep of episodes) {
    const d = new Date(ep.created_at)
    const valid = !Number.isNaN(d.getTime())
    const key = valid ? localDayKey(d) : 'unknown'

    if (!current || current.key !== key) {
      current = { key, label: valid ? dayLabel(d, now) : 'UNDATED', episodes: [] }
      groups.push(current)
    }
    current.episodes.push(ep)
  }

  return groups
}

/** `HH:MM` local wall-clock time, or `--:--` for an unparseable timestamp. */
export function formatEpisodeTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--'
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
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
