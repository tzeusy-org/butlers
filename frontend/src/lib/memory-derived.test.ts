// @vitest-environment node
/**
 * Unit tests for the pure memory derived-value functions.
 *
 * effectiveConfidence is the headline case: per-day decay_rate, fractional
 * days, and a [0,1] clamp. Boundaries covered: zero elapsed days, fresh
 * confirm vs. old unconfirmed, large elapsed (floor), and clamp ceiling/floor.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Episode, Fact, MemoryRule } from '@/api/types.ts'
import {
  consolidationGlyph,
  dayKeyInTimeZone,
  daysSince,
  decayArithmeticLine,
  decayDaysAgo,
  effectiveConfidence,
  formatDayStamp,
  formatEpisodeTime,
  groupEpisodesByDay,
  inspectResultToEpisode,
  inspectResultToFact,
  inspectResultToRule,
  isWriteupOverdue,
  permanenceTag,
} from './memory-derived'
import type { MemoryInspectResult } from '@/api/types.ts'

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

/** Build a MemoryRule with sane defaults; override only what a test exercises. */
function makeRule(overrides: Partial<MemoryRule> = {}): MemoryRule {
  return {
    id: 'rule-1',
    content: 'always confirm before sending',
    scope: 'global',
    maturity: 'established',
    confidence: 0.7,
    decay_rate: 0.01,
    permanence: 'standard',
    effectiveness_score: 0.8,
    applied_count: 6,
    success_count: 5,
    harmful_count: 1,
    source_episode_id: null,
    source_butler: null,
    created_at: '2026-06-12T00:00:00.000Z',
    last_applied_at: null,
    last_evaluated_at: null,
    tags: [],
    metadata: {},
    ...overrides,
  }
}

/** Build an Episode with sane defaults; override only what a test exercises. */
function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  return {
    id: 'ep-1',
    butler: 'lifestyle',
    session_id: null,
    content: 'discussed weekend plans',
    importance: 7,
    reference_count: 2,
    consolidated: true,
    consolidation_status: 'dead_letter',
    created_at: '2026-06-12T00:00:00.000Z',
    last_referenced_at: null,
    expires_at: null,
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

// ---------------------------------------------------------------------------
// Search-result adapters (§3d)
// ---------------------------------------------------------------------------

function makeInspect(overrides: Partial<MemoryInspectResult> = {}): MemoryInspectResult {
  return {
    id: 'r-1',
    kind: 'fact',
    content: 'ibuprofen, after meals',
    butler: 'lifestyle',
    created_at: '2026-06-12T00:00:00.000Z',
    metadata: {},
    ...overrides,
  }
}

describe('inspectResultToFact', () => {
  it('prefers the embedded result.fact register row (real belief data)', () => {
    const embedded = makeFact({
      id: 'fact-99',
      subject: 'Owner',
      predicate: 'preferred_pain_relief',
      confidence: 0.94,
      decay_rate: 0.008,
      permanence: 'stable',
      validity: 'fading',
      importance: 8,
    })
    const fact = inspectResultToFact(makeInspect({ fact: embedded }))
    // The embedded row is returned verbatim — identical to browse mode.
    expect(fact).toBe(embedded)
    expect(fact.subject).toBe('Owner')
    expect(fact.confidence).toBe(0.94)
    expect(fact.decay_rate).toBe(0.008)
    expect(fact.permanence).toBe('stable')
    // Real fading state flows through (no longer forced to active).
    expect(fact.validity).toBe('fading')
  })

  it('pulls subject/predicate/confidence from metadata when present', () => {
    const fact = inspectResultToFact(
      makeInspect({
        metadata: { subject: 'Owner', predicate: 'preferred_pain_relief', confidence: 0.94 },
      }),
    )
    expect(fact.subject).toBe('Owner')
    expect(fact.predicate).toBe('preferred_pain_relief')
    expect(fact.confidence).toBe(0.94)
    expect(fact.content).toBe('ibuprofen, after meals')
  })

  it('uses honest defaults when no embedded row and metadata is bare (active, no fabricated belief)', () => {
    const fact = inspectResultToFact(makeInspect({ butler: 'health', metadata: {} }))
    // No guessed fading state — never dims on a guess.
    expect(fact.validity).toBe('active')
    // No fabricated belief numeral.
    expect(fact.confidence).toBe(0)
    // Subject falls back to the source butler.
    expect(fact.subject).toBe('health')
  })
})

describe('inspectResultToRule', () => {
  it('prefers the embedded result.rule register row (real maturity/tally)', () => {
    const embedded = makeRule({
      id: 'rule-99',
      maturity: 'anti_pattern',
      confidence: 0.3,
      harmful_count: 4,
      applied_count: 10,
      success_count: 6,
      effectiveness_score: 0.6,
    })
    const rule = inspectResultToRule(makeInspect({ kind: 'rule', rule: embedded }))
    expect(rule).toBe(embedded)
    expect(rule.maturity).toBe('anti_pattern')
    expect(rule.harmful_count).toBe(4)
    expect(rule.applied_count).toBe(10)
    expect(rule.confidence).toBe(0.3)
  })

  it('defaults maturity to candidate and harm to 0 when no embedded row (zero red)', () => {
    const rule = inspectResultToRule(makeInspect({ kind: 'rule', metadata: {} }))
    expect(rule.maturity).toBe('candidate')
    expect(rule.harmful_count).toBe(0)
  })

  it('reads maturity/tally from metadata when present', () => {
    const rule = inspectResultToRule(
      makeInspect({
        kind: 'rule',
        metadata: { maturity: 'anti_pattern', harmful_count: 4, applied_count: 10 },
      }),
    )
    expect(rule.maturity).toBe('anti_pattern')
    expect(rule.harmful_count).toBe(4)
    expect(rule.applied_count).toBe(10)
  })
})

describe('inspectResultToEpisode', () => {
  it('prefers the embedded result.episode register row (real importance/status)', () => {
    const embedded = makeEpisode({
      id: 'ep-99',
      importance: 7,
      consolidation_status: 'dead_letter',
    })
    const ep = inspectResultToEpisode(makeInspect({ kind: 'episode', episode: embedded }))
    expect(ep).toBe(embedded)
    // Real status flows through — the daybook glyph can now show dead-letter.
    expect(ep.consolidation_status).toBe('dead_letter')
    expect(ep.importance).toBe(7)
  })

  it('defaults to a colorless consolidated glyph when no embedded row (never a guessed dead-letter)', () => {
    const ep = inspectResultToEpisode(makeInspect({ kind: 'episode', metadata: {} }))
    expect(ep.consolidation_status).toBe('consolidated')
    expect(ep.importance).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Attention rail: write-up overdue (§5)
// ---------------------------------------------------------------------------

describe('isWriteupOverdue', () => {
  const now = new Date('2026-06-12T00:00:00.000Z')

  it('is false when consolidation has never run', () => {
    expect(isWriteupOverdue(null, now)).toBe(false)
    expect(isWriteupOverdue(undefined, now)).toBe(false)
  })

  it('is false within 2× the daily cadence', () => {
    // 40h ago — under the 48h (2× 24h) threshold.
    const last = new Date(now.getTime() - 40 * 60 * 60 * 1000).toISOString()
    expect(isWriteupOverdue(last, now)).toBe(false)
  })

  it('is true past 2× the daily cadence', () => {
    // 50h ago — over the 48h threshold.
    const last = new Date(now.getTime() - 50 * 60 * 60 * 1000).toISOString()
    expect(isWriteupOverdue(last, now)).toBe(true)
  })

  it('is false for an unparseable timestamp', () => {
    expect(isWriteupOverdue('not-a-date', now)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// decayDaysAgo (detail-page fragment)
// ---------------------------------------------------------------------------

describe('decayDaysAgo', () => {
  it('floors fractional days since last confirmation', () => {
    const fact = makeFact({
      last_confirmed_at: '2026-05-31T06:00:00.000Z', // 11.75d before NOW
      created_at: '2026-01-01T00:00:00.000Z',
    })
    expect(decayDaysAgo(fact, NOW)).toBe(11)
  })

  it('anchors on created_at when last_confirmed_at is null', () => {
    const fact = makeFact({
      last_confirmed_at: null,
      created_at: '2026-06-02T00:00:00.000Z', // 10d before NOW
    })
    expect(decayDaysAgo(fact, NOW)).toBe(10)
  })

  it('returns null when there is no anchor timestamp', () => {
    const fact = makeFact({ last_confirmed_at: null, created_at: '' })
    expect(decayDaysAgo(fact, NOW)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// decayArithmeticLine (detail-page mono line)
// ---------------------------------------------------------------------------

describe('decayArithmeticLine', () => {
  it('matches the documented format with all fragments', () => {
    const fact = makeFact({
      confidence: 0.94,
      decay_rate: 0.002,
      last_confirmed_at: '2026-05-31T00:00:00.000Z', // 12d before NOW
      created_at: '2026-01-01T00:00:00.000Z',
    })
    const line = decayArithmeticLine(fact, NOW)
    expect(line).toBe(
      `confidence 0.94 · decays 0.002/day · last confirmed 12d ago · effective ${effectiveConfidence(
        fact,
        NOW,
      ).toFixed(2)}`,
    )
  })

  it('drops the "last confirmed" fragment when there is no anchor', () => {
    const fact = makeFact({
      confidence: 0.5,
      decay_rate: 0.01,
      last_confirmed_at: null,
      created_at: '',
    })
    const line = decayArithmeticLine(fact, NOW)
    expect(line).not.toContain('last confirmed')
    expect(line).toContain('confidence 0.50')
    expect(line).toContain('decays 0.010/day')
    expect(line).toContain('effective')
  })

  it('shows decay reducing the effective value over time', () => {
    const fact = makeFact({
      confidence: 0.94,
      decay_rate: 0.05,
      last_confirmed_at: '2026-05-13T00:00:00.000Z', // 30d before NOW
      created_at: '2026-01-01T00:00:00.000Z',
    })
    const line = decayArithmeticLine(fact, NOW)
    // effective is meaningfully below the nominal confidence after 30 days.
    const effective = effectiveConfidence(fact, NOW)
    expect(effective).toBeLessThan(0.94)
    expect(line).toContain(`effective ${effective.toFixed(2)}`)
  })
})

// ---------------------------------------------------------------------------
// Owner-timezone day bucketing (bu-bqpec)
//
// The whole memory subsystem buckets and stamps dates on the OWNER's clock
// (useTimezone(), default Asia/Singapore), never the viewer's host timezone.
// These tests vary process.env.TZ — the host clock — and prove the owner-tz
// day key / label / gutter never move with it. vitest pins TZ=UTC
// (vite.config.ts), which is exactly why the pre-fix host-local getters looked
// correct on CI while silently disagreeing on any non-UTC viewer.
// ---------------------------------------------------------------------------

/** The pre-fix host-local day key: getFullYear/getMonth/getDate on the host. */
function hostLocalDayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

describe('dayKeyInTimeZone / formatDayStamp — owner-tz day bucketing', () => {
  const HOST_ZONES = ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati', 'Asia/Singapore']
  const originalTZ = process.env.TZ

  afterEach(() => {
    process.env.TZ = originalTZ
  })

  it('resolves the day on the owner clock, not UTC', () => {
    // 2026-06-11T20:00Z is already 2026-06-12 04:00 in Singapore (+08).
    const instant = new Date('2026-06-11T20:00:00.000Z')
    expect(dayKeyInTimeZone(instant, 'Asia/Singapore')).toBe('2026-06-12')
    expect(dayKeyInTimeZone(instant, 'UTC')).toBe('2026-06-11')
  })

  it('is stable across the viewer host timezone (owner tz fixed)', () => {
    const instant = new Date('2026-06-11T20:00:00.000Z')
    const keys = new Set<string>()
    for (const tz of HOST_ZONES) {
      process.env.TZ = tz
      keys.add(dayKeyInTimeZone(instant, 'Asia/Singapore'))
    }
    expect(keys.size).toBe(1)
    expect([...keys][0]).toBe('2026-06-12')
  })

  it('matches the OLD host-local stamp when the viewer host tz == owner tz (no regression)', () => {
    // A Singapore viewer (host tz == owner tz) must see exactly what the pre-fix
    // host-local fmtDate rendered — the conversion is a no-op for owner-tz users.
    process.env.TZ = 'Asia/Singapore'
    for (const iso of [
      '2026-06-11T20:00:00.000Z', // 04:00 next day SGT — crosses UTC midnight
      '2026-06-11T23:30:00.000Z',
      '2026-06-12T01:00:00.000Z',
      '2026-01-01T15:59:00.000Z',
    ]) {
      const d = new Date(iso)
      expect(dayKeyInTimeZone(d, 'Asia/Singapore')).toBe(hostLocalDayKey(d))
    }
  })

  it('repro: the OLD host-local key crosses a date boundary the owner-tz key does not', () => {
    // Same instant, same owner timezone — the fixed owner-tz key stays on
    // 2026-06-12 for every host, but the pre-fix host-local key straddles the
    // 11th/12th boundary as the host clock moves. That divergence is the bug.
    const instant = new Date('2026-06-11T20:00:00.000Z')
    const hostKeys = new Set<string>()
    const ownerKeys = new Set<string>()
    for (const tz of HOST_ZONES) {
      process.env.TZ = tz
      hostKeys.add(hostLocalDayKey(instant))
      ownerKeys.add(dayKeyInTimeZone(instant, 'Asia/Singapore'))
    }
    // The old logic disagreed with itself across hosts...
    expect(hostKeys.has('2026-06-11')).toBe(true)
    expect(hostKeys.has('2026-06-12')).toBe(true)
    expect(hostKeys.size).toBeGreaterThan(1)
    // ...while the owner-tz key is a single, host-independent value.
    expect(ownerKeys.size).toBe(1)
    expect([...ownerKeys][0]).toBe('2026-06-12')
  })

  it('formatDayStamp returns null for missing/unparseable input', () => {
    expect(formatDayStamp(null, 'Asia/Singapore')).toBeNull()
    expect(formatDayStamp(undefined, 'Asia/Singapore')).toBeNull()
    expect(formatDayStamp('', 'Asia/Singapore')).toBeNull()
    expect(formatDayStamp('not-a-date', 'Asia/Singapore')).toBeNull()
  })

  it('formatDayStamp stamps the owner-tz calendar day', () => {
    // 23:30Z on 2026-06-11 → 2026-06-12 in Singapore.
    expect(formatDayStamp('2026-06-11T23:30:00.000Z', 'Asia/Singapore')).toBe('2026-06-12')
    expect(formatDayStamp('2026-06-11T23:30:00.000Z', 'UTC')).toBe('2026-06-11')
  })
})

describe('formatEpisodeTime — owner-tz wall clock', () => {
  const originalTZ = process.env.TZ

  afterEach(() => {
    process.env.TZ = originalTZ
  })

  it('returns --:-- for an unparseable timestamp', () => {
    expect(formatEpisodeTime('not-a-date', 'Asia/Singapore')).toBe('--:--')
  })

  it('renders the owner-tz 24-hour wall clock (h23: midnight is 00:00)', () => {
    // 16:05Z → 00:05 next day in Singapore (+08).
    expect(formatEpisodeTime('2026-06-11T16:05:00.000Z', 'Asia/Singapore')).toBe('00:05')
    expect(formatEpisodeTime('2026-06-11T16:05:00.000Z', 'UTC')).toBe('16:05')
  })

  it('is stable across the viewer host timezone', () => {
    const times = new Set<string>()
    for (const tz of ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      process.env.TZ = tz
      times.add(formatEpisodeTime('2026-06-11T20:00:00.000Z', 'Asia/Singapore'))
    }
    expect(times.size).toBe(1)
    expect([...times][0]).toBe('04:00')
  })
})

describe('groupEpisodesByDay / dayLabel — owner-tz grouping', () => {
  const originalTZ = process.env.TZ
  // NOW is 2026-06-12 20:00 SGT (12:00Z) — "today" in the owner timezone.
  const NOW_SGT = new Date('2026-06-12T12:00:00.000Z')

  afterEach(() => {
    process.env.TZ = originalTZ
  })

  it('buckets and labels on the owner clock (TODAY / YESTERDAY / dated)', () => {
    const groups = groupEpisodesByDay(
      [
        // 2026-06-12 09:00 SGT → TODAY
        makeEpisode({ id: 'today', created_at: '2026-06-12T01:00:00.000Z' }),
        // 2026-06-12 04:00 SGT but written 2026-06-11T20:00Z → still TODAY in SGT
        makeEpisode({ id: 'today-edge', created_at: '2026-06-11T20:00:00.000Z' }),
        // 2026-06-11 22:00 SGT → YESTERDAY
        makeEpisode({ id: 'yday', created_at: '2026-06-11T14:00:00.000Z' }),
        // 2026-06-09 → dated
        makeEpisode({ id: 'older', created_at: '2026-06-09T01:00:00.000Z' }),
      ],
      'Asia/Singapore',
      NOW_SGT,
    )
    expect(groups.map((g) => g.key)).toEqual([
      '2026-06-12',
      '2026-06-11',
      '2026-06-09',
    ])
    expect(groups.map((g) => g.label)).toEqual(['TODAY', 'YESTERDAY', 'TUE 9 JUN'])
    expect(groups[0]!.episodes.map((e) => e.id)).toEqual(['today', 'today-edge'])
  })

  it('groups a host-day-straddling pair under one owner-tz day', () => {
    // Both instants are the same SGT calendar day (2026-06-12) but fall on
    // different Los Angeles days — under a host-local render they would split.
    const episodes = [
      makeEpisode({ id: 'a', created_at: '2026-06-12T15:00:00.000Z' }), // 23:00 SGT · 08:00 LA (06-12)
      makeEpisode({ id: 'b', created_at: '2026-06-11T16:30:00.000Z' }), // 00:30 SGT · 09:30 LA (06-11)
    ]

    process.env.TZ = 'America/Los_Angeles'
    const grouped = groupEpisodesByDay(episodes, 'Asia/Singapore', NOW_SGT)
    expect(grouped).toHaveLength(1)
    expect(grouped[0]!.key).toBe('2026-06-12')
    expect(grouped[0]!.episodes.map((e) => e.id)).toEqual(['a', 'b'])

    // A pre-fix host-local grouping of the very same instants straddles the UTC
    // boundary in Los Angeles and would have split them into two days.
    const hostKeys = new Set(episodes.map((e) => hostLocalDayKey(new Date(e.created_at))))
    expect(hostKeys.size).toBe(2)
  })

  it('labels are stable across the viewer host timezone', () => {
    const labelsPerZone = new Set<string>()
    const episode = makeEpisode({ id: 'x', created_at: '2026-06-11T20:00:00.000Z' })
    for (const tz of ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      process.env.TZ = tz
      const [group] = groupEpisodesByDay([episode], 'Asia/Singapore', NOW_SGT)
      labelsPerZone.add(`${group!.key}|${group!.label}`)
    }
    expect(labelsPerZone.size).toBe(1)
    expect([...labelsPerZone][0]).toBe('2026-06-12|TODAY')
  })

  it('uses fake timers for the default now (labels resolve against a fixed clock)', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-06-12T12:00:00.000Z'))
    try {
      // No explicit now → defaults to new Date() under the fake clock.
      const [group] = groupEpisodesByDay(
        [makeEpisode({ id: 't', created_at: '2026-06-12T01:00:00.000Z' })],
        'Asia/Singapore',
      )
      expect(group!.label).toBe('TODAY')
    } finally {
      vi.useRealTimers()
    }
  })
})
