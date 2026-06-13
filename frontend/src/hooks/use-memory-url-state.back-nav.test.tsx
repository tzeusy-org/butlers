// @vitest-environment jsdom
/**
 * Browser back-navigation test for /memory URL state (bu-ezg4r).
 *
 * The sibling use-memory-url-state.test.ts covers the pure parse/serialize
 * helpers. This file exercises the live hook over a real React Router history
 * stack: each setState() push (register / filter changes) must create a
 * distinct history entry so that browser Back restores the *prior* state.
 *
 * Guards two regressions:
 *   1. setState defaulting to history.replace (which would collapse entries and
 *      make Back skip past intermediate states).
 *   2. parse/serialize drift that would fail to round-trip a popped URL back to
 *      the original state object.
 *
 * Uses the non-data <MemoryRouter> + useNavigate(-1) (the programmatic
 * equivalent of the browser Back button) so the test drives a genuine history
 * pop rather than a fresh navigation.
 */

import { render, act } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { describe, expect, it } from 'vitest'

import { useMemoryUrlState, type MemoryUrlState } from './use-memory-url-state'

type Sink = {
  state: MemoryUrlState
  setState: ReturnType<typeof useMemoryUrlState>['setState']
  back: () => void
}

/**
 * Probe component: publishes the current parsed state, the setter, and a Back
 * trigger through an onReady callback on every render so the test can drive
 * navigations and read state after each history change.
 */
function Probe({ onReady }: { onReady: (sink: Sink) => void }) {
  const { state, setState } = useMemoryUrlState()
  const navigate = useNavigate()
  useEffect(() => {
    onReady({ state, setState, back: () => navigate(-1) })
  })
  return null
}

function renderWithRouter() {
  // Holds the latest published sink; reassigned (not mutated) on every render.
  let current: Sink | null = null
  const sink = (): Sink => {
    if (current === null) throw new Error('probe not ready')
    return current
  }
  render(
    <MemoryRouter initialEntries={['/memory']}>
      <Routes>
        <Route
          path="/memory"
          element={
            <Probe
              onReady={(s) => {
                current = s
              }}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  )
  return { sink }
}

describe('useMemoryUrlState — browser back navigation', () => {
  it('Back restores the immediately-prior register/filter state', async () => {
    const { sink } = renderWithRouter()

    // Initial: all defaults.
    expect(sink().state.register).toBe('facts')
    expect(sink().state.validity).toBe('active')

    // Push state A: switch to the rules register.
    await act(async () => {
      sink().setState({ register: 'rules' })
    })
    expect(sink().state.register).toBe('rules')

    // Push state B: apply a non-default validity filter on top of A.
    await act(async () => {
      sink().setState({ validity: 'fading' })
    })
    expect(sink().state.register).toBe('rules')
    expect(sink().state.validity).toBe('fading')

    // Back → should restore state A (rules register, default validity), NOT
    // jump straight back to the initial defaults.
    await act(async () => {
      sink().back()
    })
    expect(sink().state.register).toBe('rules')
    expect(sink().state.validity).toBe('active')

    // Back again → restore the initial default state.
    await act(async () => {
      sink().back()
    })
    expect(sink().state.register).toBe('facts')
    expect(sink().state.validity).toBe('active')
  })

  it('Back round-trips a submitted search through register + filter pushes', async () => {
    const { sink } = renderWithRouter()

    // Push a search submission (q + kind), then change register.
    await act(async () => {
      sink().setState({ q: 'fatigue', kind: 'episode', offset: 0 })
    })
    expect(sink().state.q).toBe('fatigue')
    expect(sink().state.kind).toBe('episode')

    await act(async () => {
      sink().setState({ register: 'episodes', status: 'consolidated' })
    })
    expect(sink().state.register).toBe('episodes')
    expect(sink().state.status).toBe('consolidated')
    // The earlier search is still part of the merged URL state.
    expect(sink().state.q).toBe('fatigue')

    // Back → drop the register/status push, restoring just the search state.
    await act(async () => {
      sink().back()
    })
    expect(sink().state.register).toBe('facts')
    expect(sink().state.status).toBeNull()
    expect(sink().state.q).toBe('fatigue')
    expect(sink().state.kind).toBe('episode')
  })

  it('explicit replace:true does NOT create a back-navigable entry', async () => {
    const { sink } = renderWithRouter()

    // First a normal push so there is a real prior entry to land on.
    await act(async () => {
      sink().setState({ register: 'rules' })
    })
    expect(sink().state.register).toBe('rules')

    // Replace (not push) the offset — must not add a history entry.
    await act(async () => {
      sink().setState({ offset: 40 }, { replace: true })
    })
    expect(sink().state.offset).toBe(40)

    // A single Back skips past the replaced entry to the defaults, proving the
    // replace did not stack a new entry.
    await act(async () => {
      sink().back()
    })
    expect(sink().state.register).toBe('facts')
    expect(sink().state.offset).toBe(0)
  })
})
