// @vitest-environment node
/**
 * Unit tests for ingestion URL-state helpers (parse + serialize).
 *
 * Tests the pure helper functions parseIngestionState and serializeIngestionState
 * in isolation without needing a router environment. The hook itself is tested
 * via integration in the IngestionDispatchConsole route tests.
 *
 * Covers:
 * - Default values when params are absent
 * - Valid param values are parsed correctly
 * - Invalid param values fall back to defaults
 * - serializeIngestionState round-trips: write then read preserves values
 * - Default values are omitted from the serialized URLSearchParams
 * - Composite round-trip: all params together
 */

import { describe, expect, it } from 'vitest'
import {
  parseIngestionState,
  serializeIngestionState,
  type IngestionUrlState,
} from './use-ingestion-url-state'

// ---------------------------------------------------------------------------
// parseIngestionState
// ---------------------------------------------------------------------------

describe('parseIngestionState', () => {
  it('returns defaults when URLSearchParams is empty', () => {
    const state = parseIngestionState(new URLSearchParams())
    expect(state.range).toBe('24h')
    expect(state.channels).toEqual([])
    expect(state.statuses).toEqual([])
    expect(state.view).toBe('all')
    expect(state.event).toBeNull()
  })

  it('parses a valid range param', () => {
    const params = new URLSearchParams('range=7d')
    expect(parseIngestionState(params).range).toBe('7d')
  })

  it('parses all valid range values', () => {
    for (const range of ['live', '1h', '24h', '7d', 'custom'] as const) {
      const params = new URLSearchParams(`range=${range}`)
      expect(parseIngestionState(params).range).toBe(range)
    }
  })

  it('falls back to 24h for an invalid range', () => {
    const params = new URLSearchParams('range=99d')
    expect(parseIngestionState(params).range).toBe('24h')
  })

  it('parses channels as a comma-separated array', () => {
    const params = new URLSearchParams('channels=email,telegram')
    expect(parseIngestionState(params).channels).toEqual(['email', 'telegram'])
  })

  it('parses a single channel', () => {
    const params = new URLSearchParams('channels=email')
    expect(parseIngestionState(params).channels).toEqual(['email'])
  })

  it('returns empty channels array when param is absent', () => {
    expect(parseIngestionState(new URLSearchParams()).channels).toEqual([])
  })

  it('parses statuses, filtering out invalid values', () => {
    const params = new URLSearchParams('statuses=ingested,invalid_status,error')
    const state = parseIngestionState(params)
    expect(state.statuses).toContain('ingested')
    expect(state.statuses).toContain('error')
    expect(state.statuses).not.toContain('invalid_status')
  })

  it('parses all valid status values', () => {
    const all = 'ingested,filtered,error,replay_pending,replay_complete,replay_failed'
    const params = new URLSearchParams(`statuses=${all}`)
    const state = parseIngestionState(params)
    expect(state.statuses).toHaveLength(6)
  })

  it('parses a valid view param', () => {
    const params = new URLSearchParams('view=errors')
    expect(parseIngestionState(params).view).toBe('errors')
  })

  it('falls back to "all" for an invalid view', () => {
    const params = new URLSearchParams('view=not_a_view')
    expect(parseIngestionState(params).view).toBe('all')
  })

  it('parses the event param', () => {
    const params = new URLSearchParams('event=evt-abc-123')
    expect(parseIngestionState(params).event).toBe('evt-abc-123')
  })

  it('returns null event when param is absent', () => {
    expect(parseIngestionState(new URLSearchParams()).event).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// serializeIngestionState
// ---------------------------------------------------------------------------

describe('serializeIngestionState', () => {
  it('omits range=24h (the default) from the output', () => {
    const params = serializeIngestionState({ range: '24h' })
    expect(params.has('range')).toBe(false)
  })

  it('includes range when not the default', () => {
    const params = serializeIngestionState({ range: '7d' })
    expect(params.get('range')).toBe('7d')
  })

  it('omits view=all (the default)', () => {
    const params = serializeIngestionState({ view: 'all' })
    expect(params.has('view')).toBe(false)
  })

  it('includes view when not the default', () => {
    const params = serializeIngestionState({ view: 'errors' })
    expect(params.get('view')).toBe('errors')
  })

  it('omits channels when empty', () => {
    const params = serializeIngestionState({ channels: [] })
    expect(params.has('channels')).toBe(false)
  })

  it('serializes channels as comma-separated string', () => {
    const params = serializeIngestionState({ channels: ['email', 'telegram'] })
    expect(params.get('channels')).toBe('email,telegram')
  })

  it('omits statuses when empty', () => {
    const params = serializeIngestionState({ statuses: [] })
    expect(params.has('statuses')).toBe(false)
  })

  it('serializes statuses as comma-separated string', () => {
    const params = serializeIngestionState({ statuses: ['ingested', 'error'] })
    expect(params.get('statuses')).toBe('ingested,error')
  })

  it('omits event when null', () => {
    const params = serializeIngestionState({ event: null })
    expect(params.has('event')).toBe(false)
  })

  it('includes event when set', () => {
    const params = serializeIngestionState({ event: 'evt-xyz' })
    expect(params.get('event')).toBe('evt-xyz')
  })
})

// ---------------------------------------------------------------------------
// Round-trip: serialize then parse
// ---------------------------------------------------------------------------

describe('round-trip: serializeIngestionState → parseIngestionState', () => {
  it('round-trips a full non-default state', () => {
    const original: IngestionUrlState = {
      range: '7d',
      channels: ['email', 'telegram'],
      statuses: ['ingested', 'error'],
      view: 'errors',
      event: 'evt-round-trip-123',
    }
    const serialized = serializeIngestionState(original)
    const parsed = parseIngestionState(serialized)
    expect(parsed).toEqual(original)
  })

  it('round-trips the default state (all omitted)', () => {
    const defaults: IngestionUrlState = {
      range: '24h',
      channels: [],
      statuses: [],
      view: 'all',
      event: null,
    }
    const serialized = serializeIngestionState(defaults)
    // Default state should produce an empty URLSearchParams
    expect(serialized.toString()).toBe('')
    const parsed = parseIngestionState(serialized)
    expect(parsed).toEqual(defaults)
  })

  it('round-trips a partial state with range only', () => {
    const state: Partial<IngestionUrlState> = { range: 'live' }
    const serialized = serializeIngestionState(state)
    const parsed = parseIngestionState(serialized)
    expect(parsed.range).toBe('live')
    expect(parsed.channels).toEqual([])
    expect(parsed.view).toBe('all')
    expect(parsed.event).toBeNull()
  })
})
