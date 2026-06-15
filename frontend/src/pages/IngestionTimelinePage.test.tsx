// @vitest-environment jsdom
/**
 * Tests for IngestionTimelinePage — focusing on the LiveStatusBadge pill.
 *
 * The pill must reflect real pipeline freshness, not a wall-clock timer:
 * - "checking…"  → while TimelineTab has not yet reported freshness (undefined)
 * - "Idle"       → TimelineTab reports null (empty pipeline, no events)
 * - "Live"       → TimelineTab reports a received_at within the last 60 s
 * - "Idle"       → TimelineTab reports a received_at older than 60 s
 *
 * TimelineTab is mocked to a stub that accepts and calls onFreshnessChange
 * so we can control what freshness value the page receives.
 */

import React, { type ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'

// ---------------------------------------------------------------------------
// Mock TimelineTab so we can control onFreshnessChange calls
// ---------------------------------------------------------------------------

let capturedOnFreshnessChange: ((ra: string | null) => void) | undefined

vi.mock('@/components/ingestion/TimelineTab', () => ({
  TimelineTab: (props: ComponentProps<'div'> & { onFreshnessChange?: (ra: string | null) => void }) => {
    capturedOnFreshnessChange = props.onFreshnessChange
    return <div data-testid="timeline-tab-stub">Timeline tab</div>
  },
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
      capturedOnFreshnessChange?.(recentIso())
    })
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).toBeNull()
  })

  it('shows "Idle" when TimelineTab reports null (empty pipeline)', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(null)
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('shows "Idle" when TimelineTab reports a stale received_at', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(staleIso())
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('transitions from "Live" to "Idle" when freshness update brings a stale timestamp', async () => {
    await renderPage()
    act(() => {
      capturedOnFreshnessChange?.(recentIso())
    })
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).not.toBeNull()

    act(() => {
      capturedOnFreshnessChange?.(staleIso())
    })
    expect(container.querySelector('[data-testid="live-status-badge-idle"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="live-status-badge-live"]')).toBeNull()
  })

  it('renders the page headline', async () => {
    await renderPage()
    expect(container.querySelector('h1')?.textContent).toBe('Today, in order of arrival.')
  })
})
