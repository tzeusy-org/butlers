// @vitest-environment jsdom
/**
 * Tests for IngestionTimelinePage — focusing on the LiveStatusBadge pill.
 *
 * The pill must reflect real pipeline freshness, not a wall-clock timer:
 * - "checking…"  → while TimelineTab has not yet reported freshness (undefined)
 * - "Idle"       → TimelineTab reports null (empty pipeline, no events)
 * - "Live"       → TimelineTab reports a received_at within the last 60 s
 * - "Idle"       → TimelineTab reports a received_at older than 60 s
 * - "Down"       → TimelineTab reports isDown=true (events head poll erroring),
 *                  which must win over any freshness value (bu-jad4j.5)
 *
 * TimelineTab is mocked to a stub that accepts and calls onFreshnessChange
 * (with an `isDown` flag) so we can control what freshness/health the page
 * receives.
 */

import React, { type ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'

// ---------------------------------------------------------------------------
// Mock TimelineTab so we can control onFreshnessChange calls
// ---------------------------------------------------------------------------

let capturedOnFreshnessChange: ((ra: string | null, isDown: boolean) => void) | undefined

vi.mock('@/components/ingestion/TimelineTab', () => ({
  TimelineTab: (
    props: ComponentProps<'div'> & {
      onFreshnessChange?: (ra: string | null, isDown: boolean) => void
    },
  ) => {
    capturedOnFreshnessChange = props.onFreshnessChange
    return <div data-testid="timeline-tab-stub">Timeline tab</div>
  },
}))

// This page test focuses on the LiveStatusBadge wiring. The opener's query
// composition is covered directly in IngestionVerdictOpeners.test.tsx; keeping
// it shallow here preserves the test's intentionally provider-free harness.
vi.mock('@/components/ingestion/dispatch/IngestionVerdictOpeners', () => ({
  IngestionTimelineVerdictOpener: () => (
    <div role="region" aria-label="Ingestion timeline verdict" />
  ),
}))

// Mock dispatch primitives to avoid layout complexity in tests
vi.mock('@/components/ingestion/dispatch', () => ({
  DispatchLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DispatchHeader: ({ headline, aside }: { headline: string; aside?: React.ReactNode }) => (
    <div>
      <h1>{headline}</h1>
      <div data-testid="dispatch-header-aside">{aside}</div>
    </div>
  ),
  DispatchSurface: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

// Mock IngestionSubNav to avoid router dependency
vi.mock('@/components/ingestion/IngestionSubNav', () => ({
  IngestionSubNav: () => <nav data-testid="ingestion-sub-nav" />,
}))

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function recentIso(): string {
  // 10 seconds ago — well within the 60 s freshness window
  return new Date(Date.now() - 10_000).toISOString()
}

function staleIso(): string {
  // 2 minutes ago — beyond the 60 s freshness window
  return new Date(Date.now() - 120_000).toISOString()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('IngestionTimelinePage — LiveStatusBadge', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    capturedOnFreshnessChange = undefined
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  async function renderPage() {
    const { default: IngestionTimelinePage } = await vi.importActual<{
      default: React.ComponentType
    }>('@/pages/IngestionTimelinePage')
    act(() => {
      root.render(
        <MemoryRouter>
          <IngestionTimelinePage />
        </MemoryRouter>,
      )
    })
  }

  it('shows "checking…" before TimelineTab has reported freshness', async () => {
    await renderPage()
    const aside = container.querySelector('[data-testid="dispatch-header-aside"]')
    expect(aside?.textContent).toContain('checking')
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).toBeNull()
  })

  it('shows "Live" when TimelineTab reports a recent received_at', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(recentIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).toBeNull()
  })

  it('shows "Idle" when TimelineTab reports null (empty pipeline)', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(null, false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('shows "Idle" when TimelineTab reports a stale received_at', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(staleIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('transitions from "Live" to "Idle" when freshness update brings a stale timestamp', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(recentIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()

    act(() => {
      capturedOnFreshnessChange?.(staleIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('decays from "Live" to "Idle" purely on the wall clock, with no new freshness update', async () => {
    vi.useFakeTimers()
    try {
      await renderPage()
      act(() => {
        capturedOnFreshnessChange?.(recentIso(), false)
      })
      expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()

      // No further onFreshnessChange call — advance the wall clock past the
      // 60s freshness window. The badge must decay on its own (bu-86c4c.8):
      // before the fix, `now` only advanced when latestReceivedAt changed,
      // so a quiet pipeline stayed "Live" forever.
      act(() => {
        vi.advanceTimersByTime(70_000)
      })

      expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
      expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  // A dead events poll must not decay into the same muted "Idle" dot a
  // genuinely quiet pipeline gets — the dead-API-impersonates-idle defect
  // bu-qvnce.2 fixed on /timeline, now closed on the badge's original home
  // (bu-jad4j.5). This test fails if isDown is not threaded into the badge:
  // an unwired page would render "Live" (a recent timestamp is reported).
  it('shows "Down" when TimelineTab reports isDown=true, overriding a recent received_at', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(recentIso(), true)
    })
    expect(container.querySelector('[data-testid="live-status-badge-down"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).toBeNull()
  })

  it('keeps "Idle" (not "Down") when the feed is merely quiet (isDown=false)', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(staleIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-down"]')).toBeNull()
  })

  it('recovers from "Down" to "Live" when a later report clears isDown', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(recentIso(), true)
    })
    expect(container.querySelector('[data-testid="live-status-badge-down"]')).not.toBeNull()

    act(() => {
      capturedOnFreshnessChange?.(recentIso(), false)
    })
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-down"]')).toBeNull()
  })

  it('renders the range-driven page headline, defaulting to the 24h range', async () => {
    // TimelineTab is mocked and never calls onRangeReport, so the page falls
    // back to its own default ("24h") — matching TimelineTab's own default range.
    await renderPage()
    expect(container.querySelector('h1')?.textContent).toBe('Last 24 hours, newest first.')
  })

  it('opens the ingestion surface with a labeled verdict region', async () => {
    await renderPage()

    expect(container.querySelector('[aria-label="Ingestion timeline verdict"]')).not.toBeNull()
  })
})
