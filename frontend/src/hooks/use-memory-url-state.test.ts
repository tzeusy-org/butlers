// @vitest-environment node
/**
 * Unit tests for /memory URL-state helpers (parse + serialize).
 *
 * Tests the pure helpers parseMemoryState and serializeMemoryState in isolation
 * without a router environment. The hook wrapper is a thin useSearchParams
 * adapter over these helpers (same shape as use-ingestion-url-state).
 *
 * Covers:
 * - Defaults when params are absent (register=facts, kind=all, validity=active, offset=0)
 * - Valid param values parse correctly
 * - Invalid param values fall back to defaults
 * - Default-valued params are omitted from serialized output (short deep-links)
 * - Round-trip: serialize → parse preserves values
 */

import { describe, expect, it } from 'vitest'
import {
  parseMemoryState,
  serializeMemoryState,
  type MemoryUrlState,
} from './use-memory-url-state'

// ---------------------------------------------------------------------------
// parseMemoryState
// ---------------------------------------------------------------------------

describe('parseMemoryState', () => {
  it('returns defaults when URLSearchParams is empty', () => {
    const state = parseMemoryState(new URLSearchParams())
    expect(state.register).toBe('facts')
    expect(state.q).toBeNull()
    expect(state.kind).toBe('all')
    expect(state.validity).toBe('active')
    expect(state.status).toBeNull()
    expect(state.offset).toBe(0)
  })

  it('parses all valid registers', () => {
    for (const register of ['facts', 'rules', 'episodes'] as const) {
      const params = new URLSearchParams(`register=${register}`)
      expect(parseMemoryState(params).register).toBe(register)
    }
  })

  it('falls back to facts for an invalid register', () => {
    expect(parseMemoryState(new URLSearchParams('register=nope')).register).toBe('facts')
  })

  it('parses all valid search kinds', () => {
    for (const kind of ['all', 'fact', 'rule', 'episode'] as const) {
      const params = new URLSearchParams(`kind=${kind}`)
      expect(parseMemoryState(params).kind).toBe(kind)
    }
  })

  it('falls back to all for an invalid kind', () => {
    expect(parseMemoryState(new URLSearchParams('kind=garbage')).kind).toBe('all')
  })

  it('parses all valid validities', () => {
    for (const v of ['active', 'fading', 'superseded', 'expired', 'retracted'] as const) {
      const params = new URLSearchParams(`validity=${v}`)
      expect(parseMemoryState(params).validity).toBe(v)
    }
  })

  it('falls back to active for an invalid validity', () => {
    expect(parseMemoryState(new URLSearchParams('validity=zombie')).validity).toBe('active')
  })

  it('parses all valid episode statuses', () => {
    for (const s of ['pending', 'consolidated', 'dead_letter'] as const) {
      const params = new URLSearchParams(`status=${s}`)
      expect(parseMemoryState(params).status).toBe(s)
    }
  })

  it('returns null status for an invalid status', () => {
    expect(parseMemoryState(new URLSearchParams('status=bogus')).status).toBeNull()
  })

  it('parses a submitted query and trims whitespace', () => {
    expect(parseMemoryState(new URLSearchParams('q=sleep')).q).toBe('sleep')
    expect(parseMemoryState(new URLSearchParams('q=%20%20hi%20%20')).q).toBe('hi')
  })

  it('treats a blank query as no query', () => {
    expect(parseMemoryState(new URLSearchParams('q=%20%20')).q).toBeNull()
    expect(parseMemoryState(new URLSearchParams('q=')).q).toBeNull()
  })

  it('parses a numeric offset', () => {
    expect(parseMemoryState(new URLSearchParams('offset=50')).offset).toBe(50)
  })

  it('falls back to 0 for a negative or non-numeric offset', () => {
    expect(parseMemoryState(new URLSearchParams('offset=-5')).offset).toBe(0)
    expect(parseMemoryState(new URLSearchParams('offset=abc')).offset).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// serializeMemoryState (default-valued params omitted)
// ---------------------------------------------------------------------------

describe('serializeMemoryState', () => {
  it('omits register=facts (default)', () => {
    expect(serializeMemoryState({ register: 'facts' }).has('register')).toBe(false)
  })

  it('includes register when not the default', () => {
    expect(serializeMemoryState({ register: 'rules' }).get('register')).toBe('rules')
  })

  it('omits kind=all (default)', () => {
    expect(serializeMemoryState({ kind: 'all' }).has('kind')).toBe(false)
  })

  it('includes kind when not the default', () => {
    expect(serializeMemoryState({ kind: 'rule' }).get('kind')).toBe('rule')
  })

  it('omits validity=active (default)', () => {
    expect(serializeMemoryState({ validity: 'active' }).has('validity')).toBe(false)
  })

  it('includes validity when not the default', () => {
    expect(serializeMemoryState({ validity: 'fading' }).get('validity')).toBe('fading')
  })

  it('omits q when null', () => {
    expect(serializeMemoryState({ q: null }).has('q')).toBe(false)
  })

  it('includes q when set', () => {
    expect(serializeMemoryState({ q: 'sleep' }).get('q')).toBe('sleep')
  })

  it('omits status when null (= all)', () => {
    expect(serializeMemoryState({ status: null }).has('status')).toBe(false)
  })

  it('includes status when set', () => {
    expect(serializeMemoryState({ status: 'pending' }).get('status')).toBe('pending')
  })

  it('omits offset=0 (default)', () => {
    expect(serializeMemoryState({ offset: 0 }).has('offset')).toBe(false)
  })

  it('includes offset when non-zero', () => {
    expect(serializeMemoryState({ offset: 50 }).get('offset')).toBe('50')
  })

  it('produces an empty query string for the full default state', () => {
    const params = serializeMemoryState({
      register: 'facts',
      q: null,
      kind: 'all',
      validity: 'active',
      status: null,
      offset: 0,
    })
    expect(params.toString()).toBe('')
  })
})

// ---------------------------------------------------------------------------
// Round-trip: serialize then parse
// ---------------------------------------------------------------------------

describe('round-trip: serializeMemoryState → parseMemoryState', () => {
  it('round-trips a full non-default state', () => {
    const original: MemoryUrlState = {
      register: 'episodes',
      q: 'fatigue',
      kind: 'episode',
      validity: 'fading',
      status: 'consolidated',
      offset: 100,
    }
    const parsed = parseMemoryState(serializeMemoryState(original))
    expect(parsed).toEqual(original)
  })

  it('round-trips the default state (all params omitted)', () => {
    const defaults: MemoryUrlState = {
      register: 'facts',
      q: null,
      kind: 'all',
      validity: 'active',
      status: null,
      offset: 0,
    }
    const serialized = serializeMemoryState(defaults)
    expect(serialized.toString()).toBe('')
    expect(parseMemoryState(serialized)).toEqual(defaults)
  })

  it('deep-link register=rules round-trips to the rules register', () => {
    const parsed = parseMemoryState(new URLSearchParams('register=rules'))
    expect(parsed.register).toBe('rules')
    expect(parsed.validity).toBe('active')
    expect(parsed.offset).toBe(0)
  })
})
