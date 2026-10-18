import { describe, expect, it } from 'vitest'

import { parseMemoryState, serializeMemoryState } from '@/hooks/use-memory-url-state'

describe('questions register URL state (bu-q7vx1q.73)', () => {
  it('parses register=questions', () => {
    expect(parseMemoryState(new URLSearchParams('register=questions')).register).toBe('questions')
  })

  it('round-trips register=questions through the serialiser', () => {
    const state = parseMemoryState(new URLSearchParams('register=questions'))
    expect(serializeMemoryState(state).get('register')).toBe('questions')
  })

  it('still falls back to facts for an unknown register', () => {
    expect(parseMemoryState(new URLSearchParams('register=question')).register).toBe('facts')
  })
})
