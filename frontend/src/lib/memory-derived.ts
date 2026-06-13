/**
 * Pure derived values for the /memory house-ledger redesign.
 *
 * These are the client-side display computations the memory registers and
 * detail pages share. They are intentionally pure (no React, no fetching) so
 * they can be unit-tested exhaustively and reused across bands.
 *
 * Binding docs:
 * - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §4 "Belief typography"
 * - (memory house-ledger redesign, graduated) prompts/00-foundation.md §"Derived values"
 *
 * Source of truth caveats:
 * - `effectiveConfidence` is the *display* value (belief column, detail-page
 *   arithmetic line). It does NOT drive dimming — rows dim when the server
 *   reports `validity === 'fading'`, because the server owns the threshold.
 */

import type { Episode, Fact, MemoryInspectResult, MemoryRule } from '@/api/types.ts'

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

/**
 * Whole days elapsed since a fact's last confirmation (or creation), for the
 * detail-page decay-arithmetic line. Floors fractional days so the line reads
 * `12d ago`, not `12.4d ago`. Returns null when there is no timestamp to
 * anchor on (the line then omits the "last confirmed" fragment).
 */
export function decayDaysAgo(fact: Fact, now: Date = new Date()): number | null {
  const anchor = fact.last_confirmed_at ?? fact.created_at
  if (!anchor) return null
  return Math.floor(daysSince(anchor, now))
}

/**
 * The honest decay-arithmetic line for a fact's detail page, one mono string:
 *
 *   confidence 0.94 · decays 0.002/day · last confirmed 12d ago · effective 0.92
 *
 * The "last confirmed" fragment is dropped when there is no confirmation/creation
 * timestamp to anchor on; every other fragment always renders. `effective` is
 * the same clamped effectiveConfidence() the ledger belief column shows.
 * MEMORY_LANGUAGE.md §4; prompts/06-detail-pages.md "Fact".
 */
export function decayArithmeticLine(fact: Fact, now: Date = new Date()): string {
  const confidence = fact.confidence.toFixed(2)
  const rate = fact.decay_rate.toFixed(3)
  const effective = effectiveConfidence(fact, now).toFixed(2)
  const daysAgo = decayDaysAgo(fact, now)

  const parts = [`confidence ${confidence}`, `decays ${rate}/day`]
  if (daysAgo != null) parts.push(`last confirmed ${daysAgo}d ago`)
  parts.push(`effective ${effective}`)
  return parts.join(' · ')
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

// ---------------------------------------------------------------------------
// Search-result adapters (MEMORY_LANGUAGE.md §3d)
//
// The unified search hits GET /api/memory/inspect, which returns a uniform
// MemoryInspectResult whose flat id · kind · content · butler · created_at
// fields identify the result, PLUS exactly one full register-shaped row
// (`fact` / `rule` / `episode`, matching `kind`) carrying the complete field
// set — subject/predicate/confidence for facts, maturity/tally for rules,
// importance/consolidation for episodes (see #2199 / bu-by2n0).
//
// Each adapter therefore PREFERS the embedded register row, so a search result
// renders belief / maturity / importance data IDENTICAL to browse mode. The
// `metadata`-with-honest-defaults path remains only as a safety fallback for
// results that predate the embedded row (never a fabricated belief numeral or
// harm count — color discipline §6 holds).
//
// These are pure (testable) and frontend-only: they reuse the existing register
// row components verbatim, so a fact row in browse mode and results mode share
// the exact same grid, typography, and hairlines.
// ---------------------------------------------------------------------------

/** Read a string field from inspect metadata, or null when absent/non-string. */
function metaString(meta: Record<string, unknown>, key: string): string | null {
  const v = meta[key]
  return typeof v === 'string' ? v : null
}

/** Read a finite number field from inspect metadata, or null when absent. */
function metaNumber(meta: Record<string, unknown>, key: string): number | null {
  const v = meta[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/**
 * Adapt an inspect result (kind === 'fact') into a Fact for the ledger row.
 *
 * Prefers the full `result.fact` register row (#2199) so the ledger renders
 * real belief / permanence / validity data identical to browse mode. Falls
 * back to `metadata`-with-honest-defaults only when no embedded row is present:
 * the subject falls back to the source butler (or "—"), the belief column
 * carries no numeral (confidence 0 → effective 0, rendered `0.00`), and
 * validity defaults to `active` so the row never dims on a guessed fading state.
 */
export function inspectResultToFact(result: MemoryInspectResult): Fact {
  if (result.fact) return result.fact
  const meta = result.metadata ?? {}
  return {
    id: result.id,
    subject: metaString(meta, 'subject') ?? result.butler ?? '—',
    predicate: metaString(meta, 'predicate') ?? '',
    content: result.content,
    importance: metaNumber(meta, 'importance') ?? 0,
    confidence: metaNumber(meta, 'confidence') ?? 0,
    decay_rate: 0,
    permanence: metaString(meta, 'permanence') ?? 'standard',
    source_butler: result.butler,
    source_episode_id: null,
    session_id: null,
    supersedes_id: null,
    entity_id: null,
    entity_name: null,
    object_entity_id: null,
    object_entity_name: null,
    validity: metaString(meta, 'validity') ?? 'active',
    scope: result.butler ?? '',
    reference_count: 0,
    created_at: result.created_at,
    last_referenced_at: null,
    last_confirmed_at: result.created_at,
    tags: [],
    metadata: meta,
  }
}

/**
 * Adapt an inspect result (kind === 'rule') into a MemoryRule for the directive
 * row. Prefers the full `result.rule` register row (#2199) so maturity /
 * confidence / tally render identical to browse mode. Falls back to
 * `metadata`-with-honest-defaults only when no embedded row is present:
 * maturity defaults to `candidate` and counts to 0 (zero harm → zero red).
 */
export function inspectResultToRule(result: MemoryInspectResult): MemoryRule {
  if (result.rule) return result.rule
  const meta = result.metadata ?? {}
  return {
    id: result.id,
    content: result.content,
    scope: result.butler ?? '',
    maturity: metaString(meta, 'maturity') ?? 'candidate',
    confidence: metaNumber(meta, 'confidence') ?? 0,
    decay_rate: 0,
    permanence: metaString(meta, 'permanence') ?? 'standard',
    effectiveness_score: metaNumber(meta, 'effectiveness_score') ?? 0,
    applied_count: metaNumber(meta, 'applied_count') ?? 0,
    success_count: metaNumber(meta, 'success_count') ?? 0,
    harmful_count: metaNumber(meta, 'harmful_count') ?? 0,
    source_episode_id: null,
    source_butler: result.butler,
    created_at: result.created_at,
    last_applied_at: null,
    last_evaluated_at: null,
    tags: [],
    metadata: meta,
  }
}

/**
 * Adapt an inspect result (kind === 'episode') into an Episode for the daybook
 * row. Prefers the full `result.episode` register row (#2199) so importance and
 * the consolidation glyph render identical to browse mode. Falls back to
 * `metadata`-with-honest-defaults only when no embedded row is present:
 * importance is 0 (muted time) and status defaults to `consolidated` (a filled,
 * colorless glyph — never a guessed dead-letter `✕`).
 */
export function inspectResultToEpisode(result: MemoryInspectResult): Episode {
  if (result.episode) return result.episode
  const meta = result.metadata ?? {}
  const status = metaString(meta, 'consolidation_status') ?? 'consolidated'
  return {
    id: result.id,
    butler: result.butler ?? '',
    session_id: null,
    content: result.content,
    importance: metaNumber(meta, 'importance') ?? 0,
    reference_count: 0,
    consolidated: status === 'consolidated',
    consolidation_status: status,
    created_at: result.created_at,
    last_referenced_at: null,
    expires_at: null,
    metadata: meta,
  }
}

// ---------------------------------------------------------------------------
// Attention rail derivations (MEMORY_LANGUAGE.md §5, prompt 05)
// ---------------------------------------------------------------------------

/**
 * Default consolidation cadence in hours. The house runs a single daily evening
 * write-up (MEMORY_LANGUAGE.md §1, §7 — "the evening write-up"), so the cadence
 * is 24h and "overdue" means the last run is older than 2× that window.
 */
export const CONSOLIDATION_CADENCE_HOURS = 24

/** Importance threshold (inclusive) at which a fading fact is "important". */
export const IMPORTANT_FACT_THRESHOLD = 8

/**
 * Whether the evening write-up is overdue: now − last_consolidation_at exceeds
 * 2× the cadence. Returns false when consolidation has never run (there is no
 * "overdue" without a prior run — the Voice line narrates "not run yet").
 *
 * @param lastConsolidationAt  ISO timestamp of the last run, or null.
 * @param now                  Reference instant (injectable for tests).
 * @param cadenceHours         Cadence in hours (default 24h daily write-up).
 */
export function isWriteupOverdue(
  lastConsolidationAt: string | null | undefined,
  now: Date = new Date(),
  cadenceHours: number = CONSOLIDATION_CADENCE_HOURS,
): boolean {
  if (!lastConsolidationAt) return false
  const lastMs = Date.parse(lastConsolidationAt)
  if (Number.isNaN(lastMs)) return false
  const elapsedHours = (now.getTime() - lastMs) / (1000 * 60 * 60)
  return elapsedHours > 2 * cadenceHours
}
