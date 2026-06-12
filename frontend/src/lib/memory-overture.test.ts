// @vitest-environment node
/**
 * Unit tests for the pure /memory overture helpers (bu-2ix8d.2).
 *
 * The headline case is the Voice sentence: three EXACT templates keyed on the
 * consolidation fields, with numbers under 100 spelled as words. Also covered:
 * number-to-word spelling boundaries, write-up time formatting in the owner
 * timezone, and the LAST WRITE-UP KPI cell.
 */

import { describe, expect, it } from 'vitest'

import type { MemoryStats } from '@/api/types.ts'
import {
  composeVoiceSentence,
  formatNumeral,
  formatWriteupTime,
  numberToWords,
  writeupCell,
} from './memory-overture'

const TZ = 'Asia/Singapore'

/** A consolidation run at 06:00 SGT (= 22:00 UTC previous day). */
const SIX_AM_SGT = '2026-06-12T06:00:00+08:00'

/** Build a MemoryStats with sane defaults; override only what a test exercises. */
function makeStats(overrides: Partial<MemoryStats> = {}): MemoryStats {
  return {
    total_episodes: 1204,
    unconsolidated_episodes: 41,
    total_facts: 3182,
    active_facts: 3182,
    fading_facts: 207,
    total_rules: 58,
    candidate_rules: 10,
    established_rules: 39,
    proven_rules: 9,
    anti_pattern_rules: 0,
    last_consolidation_at: SIX_AM_SGT,
    last_consolidation_facts_produced: 12,
    dead_letter_episodes: 0,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// numberToWords
// ---------------------------------------------------------------------------

describe('numberToWords', () => {
  it('spells single digits', () => {
    expect(numberToWords(0)).toBe('zero')
    expect(numberToWords(9)).toBe('nine')
  })

  it('spells the teens', () => {
    expect(numberToWords(12)).toBe('twelve')
    expect(numberToWords(19)).toBe('nineteen')
  })

  it('spells exact tens', () => {
    expect(numberToWords(20)).toBe('twenty')
    expect(numberToWords(40)).toBe('forty')
    expect(numberToWords(90)).toBe('ninety')
  })

  it('hyphenates compound tens', () => {
    expect(numberToWords(41)).toBe('forty-one')
    expect(numberToWords(99)).toBe('ninety-nine')
  })

  it('falls back to a grouped numeral at or above 100', () => {
    expect(numberToWords(100)).toBe('100')
    expect(numberToWords(3182)).toBe('3,182')
  })

  it('falls back to a numeral for non-integer / negative input', () => {
    expect(numberToWords(-1)).toBe('-1')
    expect(numberToWords(4.5)).toBe('4.5')
  })
})

// ---------------------------------------------------------------------------
// formatNumeral
// ---------------------------------------------------------------------------

describe('formatNumeral', () => {
  it('groups thousands', () => {
    expect(formatNumeral(1204)).toBe('1,204')
    expect(formatNumeral(0)).toBe('0')
    expect(formatNumeral(41)).toBe('41')
  })
})

// ---------------------------------------------------------------------------
// formatWriteupTime
// ---------------------------------------------------------------------------

describe('formatWriteupTime', () => {
  it('renders HH:MM in the owner timezone', () => {
    expect(formatWriteupTime(SIX_AM_SGT, TZ)).toBe('06:00')
  })

  it('converts a UTC instant into the owner timezone', () => {
    // 22:00 UTC = 06:00 the next day in SGT (+08:00).
    expect(formatWriteupTime('2026-06-11T22:00:00Z', TZ)).toBe('06:00')
  })

  it('returns null for null / unparseable input', () => {
    expect(formatWriteupTime(null, TZ)).toBeNull()
    expect(formatWriteupTime(undefined, TZ)).toBeNull()
    expect(formatWriteupTime('not-a-date', TZ)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// composeVoiceSentence — the three exact templates
// ---------------------------------------------------------------------------

describe('composeVoiceSentence', () => {
  it('Template 3 (pending > 0): observations await the evening write-up', () => {
    const stats = makeStats({
      unconsolidated_episodes: 41,
      last_consolidation_facts_produced: 12,
    })
    expect(composeVoiceSentence(stats, TZ)).toBe(
      'Forty-one observations await the evening write-up; the last ran at ' +
        '06:00 and produced twelve facts.',
    )
  })

  it('Template 2 (pending == 0): the pipeline is idle', () => {
    const stats = makeStats({ unconsolidated_episodes: 0 })
    expect(composeVoiceSentence(stats, TZ)).toBe(
      'The pipeline is idle. Nothing pending since 06:00.',
    )
  })

  it('Template 1 (never run): the first write-up has not run yet', () => {
    const stats = makeStats({
      last_consolidation_at: null,
      last_consolidation_facts_produced: null,
      unconsolidated_episodes: 41,
    })
    expect(composeVoiceSentence(stats, TZ)).toBe('The first write-up has not run yet.')
  })

  it('uses singular nouns and verb agreement for a count of one', () => {
    const stats = makeStats({
      unconsolidated_episodes: 1,
      last_consolidation_facts_produced: 1,
    })
    expect(composeVoiceSentence(stats, TZ)).toBe(
      'One observation awaits the evening write-up; the last ran at ' +
        '06:00 and produced one fact.',
    )
  })

  it('treats a null facts_produced as zero in Template 3', () => {
    const stats = makeStats({
      unconsolidated_episodes: 5,
      last_consolidation_facts_produced: null,
    })
    expect(composeVoiceSentence(stats, TZ)).toContain('produced zero facts.')
  })

  it('never contains a first-person pronoun or exclamation mark', () => {
    for (const stats of [
      makeStats(),
      makeStats({ unconsolidated_episodes: 0 }),
      makeStats({ last_consolidation_at: null }),
    ]) {
      const sentence = composeVoiceSentence(stats, TZ)
      expect(sentence).not.toContain('!')
      expect(sentence).not.toMatch(/\bI\b/)
    }
  })
})

// ---------------------------------------------------------------------------
// writeupCell — the LAST WRITE-UP KPI cell
// ---------------------------------------------------------------------------

describe('writeupCell', () => {
  it('returns the HH:MM clock and a facts sub-line', () => {
    const cell = writeupCell(makeStats({ last_consolidation_facts_produced: 12 }), TZ)
    expect(cell.time).toBe('06:00')
    expect(cell.factsSub).toBe('· 12 facts')
  })

  it('returns nulls when consolidation has never run', () => {
    const cell = writeupCell(makeStats({ last_consolidation_at: null }), TZ)
    expect(cell.time).toBeNull()
    expect(cell.factsSub).toBeNull()
  })

  it('treats null facts_produced as zero', () => {
    const cell = writeupCell(makeStats({ last_consolidation_facts_produced: null }), TZ)
    expect(cell.factsSub).toBe('· 0 facts')
  })
})
