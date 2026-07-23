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
    failed_total: 0,
    replay_pending_total: 0,
    written_off_total: 0,
    backlog_available: true,
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

  it('shows the current active execution backlog independently of funnel metrics', () => {
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({
        aggregates_available: false,
        failed_total: 3,
        replay_pending_total: 2,
        written_off_total: 8,
        backlog_available: true,
      }),
    })

    act(() => { root.render(<FiltersHeaderAside />) })

    const backlog = container.querySelector('[data-testid="filters-header-backlog"]')
    expect(backlog, 'active backlog KPI missing').not.toBeNull()
    expect(backlog?.textContent).toContain('active backlog · current')
    expect(backlog?.textContent).toContain('5')
    expect(backlog?.textContent).toContain('active')
  })

  it('shows backlog unavailability instead of a fabricated zero', () => {
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({
        failed_total: null,
        replay_pending_total: null,
        written_off_total: null,
        backlog_available: false,
      }),
    })

    act(() => { root.render(<FiltersHeaderAside />) })

    const unavailable = container.querySelector('[data-testid="filters-header-backlog-unavailable"]')
    expect(unavailable, 'backlog unavailable note missing').not.toBeNull()
    expect(unavailable?.textContent).toContain('backlog unavailable')
    expect(container.querySelector('[data-testid="filters-header-backlog"]')).toBeNull()
  })

  it('does not label cached backlog counts current after a failed stats refresh and retries', () => {
    const retry = vi.fn()
    mockUsePipelineStats.mockReturnValue({
      data: makeStats({
        failed_total: 3,
        replay_pending_total: 2,
        written_off_total: 8,
        backlog_available: true,
      }),
      isError: true,
      error: new Error('pipeline metrics refresh failed'),
      refetch: retry,
    })

    act(() => { root.render(<FiltersHeaderAside />) })

    const unavailable = container.querySelector('[data-testid="filters-header-backlog-unavailable"]')
    expect(unavailable, 'stale header backlog must be named unavailable').not.toBeNull()
    expect(unavailable?.textContent).toContain('active backlog')
    expect(unavailable?.textContent).toContain('unavailable')
    expect(container.querySelector('[data-testid="filters-header-backlog"]')).toBeNull()
    expect(container.textContent).not.toContain('active backlog · current')

    act(() => {
      ;(unavailable?.querySelector('button') as HTMLButtonElement).click()
    })
    expect(retry).toHaveBeenCalledTimes(1)
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

  it('names a failed metrics reader and offers a scoped retry instead of vanishing', () => {
    const retry = vi.fn()
    mockUsePipelineStats.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error('pipeline metrics offline'),
      refetch: retry,
    })

    act(() => { root.render(<FiltersHeaderAside />) })

    const note = container.querySelector('[data-testid="filters-header-metrics-unavailable"]')
    expect(note).not.toBeNull()
    expect(note?.textContent).toContain('pipeline metrics')
    act(() => {
      ;(note?.querySelector('button') as HTMLButtonElement).click()
    })
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
