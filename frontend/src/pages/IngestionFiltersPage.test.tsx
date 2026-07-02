// @vitest-environment jsdom
/**
 * IngestionFiltersPage — header KPI aside degraded-mode handling.
 *
 * bu-4utdw.9: the aside used to render bare em-dashes with no explanation
 * when aggregates_available=false. It must surface a "metrics unavailable"
 * note instead — never silently show zeros/dashes as if they were real
 * (see butlers CLAUDE.md "Degraded-Mode Response Envelope").
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const mockUsePipelineStats = vi.fn()

vi.mock('@/hooks/use-ingestion', () => ({
  usePipelineStats: () => mockUsePipelineStats(),
}))

import type { PipelineStats } from '@/api/types'
import { FiltersHeaderAside } from './IngestionFiltersPage'

function makeStats(overrides: Partial<PipelineStats> = {}): PipelineStats {
  return {
    window: '24h',
    aggregates_available: true,
    ingested: 1000,
    filtered: 200,
    errored: 10,
    routed_by_butler: { general: 700, health: 250 },
    spark24h: Array(24).fill(40),
    rate1h: 12,
    routed_pct: 95,
    filtered24h: 200,
    ...overrides,
  }
}

function makeRoot(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root }
}

function cleanup(root: Root, container: HTMLDivElement) {
  act(() => root.unmount())
  container.remove()
  document.body.innerHTML = ''
}

describe('FiltersHeaderAside — degraded-mode KPI handling', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => { ;({ container, root } = makeRoot()) })
  afterEach(() => cleanup(root, container))

  it('renders real values with no "metrics unavailable" note when aggregates are available', () => {
    mockUsePipelineStats.mockReturnValue({ data: makeStats() })

    act(() => { root.render(<FiltersHeaderAside />) })

    expect(
      container.querySelector('[data-testid="filters-header-metrics-unavailable"]'),
    ).toBeNull()
    expect(container.textContent).toContain('1,200')
  })

  it('shows a "metrics unavailable" note (not bare em-dashes) when degraded', () => {
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({ aggregates_available: false }),
    })

    act(() => { root.render(<FiltersHeaderAside />) })

    const note = container.querySelector('[data-testid="filters-header-metrics-unavailable"]')
    expect(note, 'metrics unavailable note missing').not.toBeNull()
    expect(note?.textContent).toContain('metrics unavailable')

    // The KPI values still render as em-dashes (never a fabricated zero),
    // but always alongside the explanatory note above.
    expect(container.textContent).toContain('—')
  })

  it('renders nothing while stats have not loaded yet', () => {
    mockUsePipelineStats.mockReturnValue({ data: undefined })

    act(() => { root.render(<FiltersHeaderAside />) })

    expect(container.textContent).toBe('')
  })
})
