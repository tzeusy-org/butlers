/**
 * Pure helpers for the /memory overture band (Bands 1 & 2 of the page grammar).
 *
 * These compose the templated Voice sentence and the pipeline/KPI display
 * values from `GET /api/memory/stats`. They are intentionally pure (no React,
 * no fetching) so the three Voice templates, number-to-word spelling, and the
 * write-up time formatting can be unit-tested exhaustively.
 *
 * Binding docs:
 * - (memory house-ledger redesign, graduated) prompts/01-overture.md
 * - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §2 (page grammar), §7 (Voice)
 *
 * The Voice line is TEMPLATED, never LLM-generated. Numbers under 100 spell out
 * as words *in the Voice line only*; numerals appear everywhere else (KPI strip,
 * pipeline band).
 */

import { formatInTimeZone } from 'date-fns-tz'

import type { MemoryStats } from '@/api/types.ts'

// ---------------------------------------------------------------------------
// Number → words (Voice line only, values < 100)
// ---------------------------------------------------------------------------

const ONES = [
  'zero',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
  'ten',
  'eleven',
  'twelve',
  'thirteen',
  'fourteen',
  'fifteen',
  'sixteen',
  'seventeen',
  'eighteen',
  'nineteen',
]

const TENS = [
  '', // 0 — unused
  '', // 10 — handled by ONES
  'twenty',
  'thirty',
  'forty',
  'fifty',
  'sixty',
  'seventy',
  'eighty',
  'ninety',
]

/**
 * Spell a non-negative integer below 100 as English words (lowercase).
 *
 * Used only in the Voice sentence ("Forty-one observations…"). Values of 100 or
 * more — or negative/non-finite input — fall back to the localized numeral
 * string so the sentence still reads truthfully rather than throwing.
 *
 * @example numberToWords(41) === 'forty-one'
 * @example numberToWords(12) === 'twelve'
 * @example numberToWords(120) === '120'
 */
export function numberToWords(n: number): string {
  if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n) || n >= 100) {
    return n.toLocaleString('en-US')
  }
  if (n < 20) return ONES[n]
  const tens = Math.floor(n / 10)
  const ones = n % 10
  if (ones === 0) return TENS[tens]
  return `${TENS[tens]}-${ONES[ones]}`
}

/** Capitalize the first letter of a string. */
function capitalize(s: string): string {
  return s.length > 0 ? s[0].toUpperCase() + s.slice(1) : s
}

// ---------------------------------------------------------------------------
// Write-up time formatting (HH:MM in owner timezone)
// ---------------------------------------------------------------------------

/**
 * Format a consolidation-run ISO timestamp as a 24-hour `HH:MM` clock in the
 * owner timezone. Returns `null` for missing or unparseable input.
 *
 * @param iso  ISO-8601 timestamp (e.g. `last_consolidation_at`).
 * @param tz   IANA timezone (owner timezone via useTimezone()).
 */
export function formatWriteupTime(iso: string | null | undefined, tz: string): string | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return null
  return formatInTimeZone(new Date(ms), tz, 'HH:mm')
}

// ---------------------------------------------------------------------------
// Voice sentence (three exact templates)
// ---------------------------------------------------------------------------

/**
 * Compose the templated Voice sentence narrating the consolidation pipeline.
 *
 * Three exact templates ((memory house-ledger redesign, graduated) prompts/01-overture.md §Voice):
 *
 *  1. never run (`last_consolidation_at` null):
 *     "The first write-up has not run yet."
 *  2. nothing pending (`unconsolidated_episodes === 0`):
 *     "The pipeline is idle. Nothing pending since 06:00."
 *  3. pending > 0:
 *     "Forty-one observations await the evening write-up; the last ran at
 *      06:00 and produced twelve facts."
 *
 * Past tense for events, present for state. No first person, no exclamation
 * marks. Numbers under 100 spell out as words; the write-up time renders as a
 * numeral clock.
 *
 * @param stats  Memory stats (consolidation fields drive the template choice).
 * @param tz     Owner timezone for the write-up clock.
 */
export function composeVoiceSentence(stats: MemoryStats, tz: string): string {
  const writeupTime = formatWriteupTime(stats.last_consolidation_at, tz)

  // Template 1 — the write-up has never run.
  if (stats.last_consolidation_at == null || writeupTime == null) {
    return 'The first write-up has not run yet.'
  }

  // Template 2 — nothing pending.
  if (stats.unconsolidated_episodes === 0) {
    return `The pipeline is idle. Nothing pending since ${writeupTime}.`
  }

  // Template 3 — pending observations await the evening write-up.
  const pending = stats.unconsolidated_episodes
  const subject = pending === 1 ? 'observation awaits' : 'observations await'
  const facts = stats.last_consolidation_facts_produced ?? 0
  const factWord = facts === 1 ? 'fact' : 'facts'
  return (
    `${capitalize(numberToWords(pending))} ${subject} the evening ` +
    `write-up; the last ran at ${writeupTime} and produced ${numberToWords(facts)} ${factWord}.`
  )
}

// ---------------------------------------------------------------------------
// KPI strip + pipeline band display values (numerals, tabular)
// ---------------------------------------------------------------------------

/** Format an integer with thousands separators (en-US grouping). */
export function formatNumeral(n: number): string {
  return n.toLocaleString('en-US')
}

export interface WriteupCell {
  /** `HH:MM` clock, or null when no consolidation run has happened. */
  time: string | null
  /** "· N facts" sub-line, or null when there is no last run. */
  factsSub: string | null
}

/**
 * Derive the "LAST WRITE-UP" KPI cell: an `HH:MM` clock plus a mono sub-line
 * reading `· N facts`. Both are null when consolidation has never run — the
 * cell then renders an em-dash.
 */
export function writeupCell(stats: MemoryStats, tz: string): WriteupCell {
  const time = formatWriteupTime(stats.last_consolidation_at, tz)
  if (time == null) {
    return { time: null, factsSub: null }
  }
  const facts = stats.last_consolidation_facts_produced ?? 0
  return { time, factsSub: `· ${formatNumeral(facts)} facts` }
}
