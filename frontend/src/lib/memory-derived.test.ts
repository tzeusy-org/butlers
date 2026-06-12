// @vitest-environment node
/**
 * Unit tests for the pure memory derived-value functions.
 *
 * effectiveConfidence is the headline case: per-day decay_rate, fractional
 * days, and a [0,1] clamp. Boundaries covered: zero elapsed days, fresh
 * confirm vs. old unconfirmed, large elapsed (floor), and clamp ceiling/floor.
 */

import { describe, expect, it } from 'vitest'
import type { Fact } from '@/api/types.ts'
import {
  consolidationGlyph,
  daysSince,
  effectiveConfidence,
  permanenceTag,
} from './memory-derived'

const NOW = new Date('2026-06-12T00:00:00.000Z')

/** Build a Fact with sane defaults; override only the fields a test exercises. */
function makeFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: 'fact-1',
    subject: 'owner',
    predicate: 'prefers',
    content: 'tea over coffee',
    importance: 5,
    confidence: 0.9,
    decay_rate: 0.0,
    permanence: 'standard',
    source_butler: null,
    source_episode_id: null,
    session_id: null,
    supersedes_id: null,
    entity_id: null,
    entity_name: null,
    object_entity_id: null,
    object_entity_name: null,
    validity: 'active',
    scope: 'global',
    reference_count: 0,
    created_at: '2026-06-12T00:00:00.000Z',
    last_referenced_at: null,
    last_confirmed_at: null,
    tags: [],
    metadata: {},
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// daysSince
// ---------------------------------------------------------------------------

describe('daysSince', () => {
  it('returns 0 for a null/undefined/empty timestamp', () => {
    expect(daysSince(null, NOW)).toBe(0)
    expect(daysSince(undefined, NOW)).toBe(0)
    expect(daysSince('', NOW)).toBe(0)
  })

  it('returns 0 for an unparseable timestamp', () => {
    expect(daysSince('not-a-date', NOW)).toBe(0)
  })

  it('returns 0 for the same instant', () => {
    expect(daysSince('2026-06-12T00:00:00.000Z', NOW)).toBe(0)
  })

  it('clamps a future timestamp to 0 elapsed days', () => {
    expect(daysSince('2026-06-20T00:00:00.000Z', NOW)).toBe(0)
  })

  it('computes whole days elapsed', () => {
    expect(daysSince('2026-06-02T00:00:00.000Z', NOW)).toBeCloseTo(10, 6)
  })

  it('computes fractional days elapsed', () => {
    // 12 hours = 0.5 days
    expect(daysSince('2026-06-11T12:00:00.000Z', NOW)).toBeCloseTo(0.5, 6)
  })
})

// ---------------------------------------------------------------------------
// effectiveConfidence
// ---------------------------------------------------------------------------

describe('effectiveConfidence', () => {
  it('equals stored confidence when decay_rate is 0', () => {
    const fact = makeFact({ confidence: 0.84, decay_rate: 0, created_at: '2026-01-01T00:00:00Z' })
    expect(effectiveConfidence(fact, NOW)).toBeCloseTo(0.84, 6)
  })

  it('equals stored confidence at zero elapsed days (just created)', () => {
    const fact = makeFact({ confidence: 0.77, decay_rate: 0.05, created_at: NOW.toISOString() })
    expect(effectiveConfidence(fact, NOW)).toBeCloseTo(0.77, 6)
  })

  it('prefers last_confirmed_at over created_at (fresh confirm resets decay)', () => {
    const fact = makeFact({
      confidence: 0.9,
      decay_rate: 0.1,
      created_at: '2026-01-01T00:00:00Z', // long ago
      last_confirmed_at: NOW.toISOString(), // confirmed now
    })
    expect(effectiveConfidence(fact, NOW)).toBeCloseTo(0.9, 6)
  })

  it('decays an old unconfirmed fact by exp(-rate * days)', () => {
    // 10 days, rate 0.002 → 0.94 * exp(-0.02) ≈ 0.92139
    const fact = makeFact({
      confidence: 0.94,
      decay_rate: 0.002,
      created_at: '2026-06-02T00:00:00.000Z',
      last_confirmed_at: null,
    })
    expect(effectiveConfidence(fact, NOW)).toBeCloseTo(0.94 * Math.exp(-0.002 * 10), 6)
  })

  it('floors toward 0 for very large elapsed time', () => {
    const fact = makeFact({
      confidence: 1,
      decay_rate: 0.5,
      created_at: '2000-01-01T00:00:00Z',
    })
    const v = effectiveConfidence(fact, NOW)
    expect(v).toBeGreaterThanOrEqual(0)
    expect(v).toBeLessThan(1e-6)
  })

  it('clamps the ceiling to 1 when stored confidence exceeds 1', () => {
    const fact = makeFact({ confidence: 1.5, decay_rate: 0, created_at: NOW.toISOString() })
    expect(effectiveConfidence(fact, NOW)).toBe(1)
  })

  it('clamps the floor to 0 when stored confidence is negative', () => {
    const fact = makeFact({ confidence: -0.3, decay_rate: 0, created_at: NOW.toISOString() })
    expect(effectiveConfidence(fact, NOW)).toBe(0)
  })

  it('never returns NaN', () => {
    const fact = makeFact({ confidence: 0.5, decay_rate: 0.01, last_confirmed_at: null, created_at: '' })
    expect(Number.isNaN(effectiveConfidence(fact, NOW))).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// permanenceTag
// ---------------------------------------------------------------------------

describe('permanenceTag', () => {
  it('maps the five known permanence values to two-letter tags', () => {
    expect(permanenceTag('permanent')).toBe('pm')
    expect(permanenceTag('stable')).toBe('st')
    expect(permanenceTag('standard')).toBe('sd')
    expect(permanenceTag('volatile')).toBe('vo')
    expect(permanenceTag('ephemeral')).toBe('ep')
  })

  it('falls back to the raw value for an unknown permanence', () => {
    expect(permanenceTag('mythical')).toBe('mythical')
    expect(permanenceTag('')).toBe('')
  })
})

// ---------------------------------------------------------------------------
// consolidationGlyph
// ---------------------------------------------------------------------------

describe('consolidationGlyph', () => {
  it('maps pending to a hollow circle', () => {
    expect(consolidationGlyph('pending')).toBe('◦')
  })

  it('maps consolidated to a filled dot', () => {
    expect(consolidationGlyph('consolidated')).toBe('•')
  })

  it('maps dead_letter and failed to a red cross', () => {
    expect(consolidationGlyph('dead_letter')).toBe('✕')
    expect(consolidationGlyph('failed')).toBe('✕')
  })

  it('accepts an object carrying a status field', () => {
    expect(consolidationGlyph({ status: 'consolidated' })).toBe('•')
    expect(consolidationGlyph({ status: 'dead_letter' })).toBe('✕')
  })

  it('falls back to the hollow pending glyph for unknown/missing status', () => {
    expect(consolidationGlyph('whatever')).toBe('◦')
    expect(consolidationGlyph({})).toBe('◦')
    expect(consolidationGlyph({ status: null })).toBe('◦')
  })
})
