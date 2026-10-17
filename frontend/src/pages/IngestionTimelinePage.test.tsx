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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { waitFor } from '@testing-library/react'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { transferableAbortController } from 'node:util'

// ---------------------------------------------------------------------------
// Mock TimelineTab so we can control onFreshnessChange calls
// ---------------------------------------------------------------------------

let capturedOnRangeReport: ((range: '1h' | '24h' | '7d') => void) | undefined
let capturedOnFreshnessChange: ((ra: string | null, isDown: boolean) => void) | undefined

vi.mock('@/components/ingestion/TimelineTab', () => ({
  TimelineTab: (
    props: ComponentProps<'div'> & {
      onRangeReport?: (range: '1h' | '24h' | '7d') => void
      onFreshnessChange?: (ra: string | null, isDown: boolean) => void
    },
  ) => {
    capturedOnFreshnessChange = props.onFreshnessChange
    capturedOnRangeReport = props.onRangeReport
    return <div data-testid="timeline-tab-stub">Timeline tab</div>
  },
}))

// This page test focuses on the LiveStatusBadge wiring. The opener's query
// composition is covered directly in IngestionVerdictOpeners.test.tsx; keeping
// it shallow here isolates freshness from verdict queries. The scrub projection
// below uses its real query/client through a fixture HTTP transport.
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
  let queryClient: QueryClient
  const actualFetch = globalThis.fetch

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    capturedOnFreshnessChange = undefined
    capturedOnRangeReport = undefined
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      bearer_scrub_available: false, bearer_scrub_counts: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  })

  afterEach(() => {
    act(() => root.unmount())
    queryClient.clear()
    vi.unstubAllGlobals()
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
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <IngestionTimelinePage />
          </MemoryRouter>
        </QueryClientProvider>,
      )
    })
  }

  it('reads scrub estimates through the real client and follows all selected ranges', async () => {
    const paths: string[] = []
    // A test-owned HTTP fixture serves the real fetch/client/query path.
    const server = createServer((request, response) => {
      paths.push(request.url ?? '')
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({
        bearer_scrub_available: true,
        bearer_scrub_counts: request.url?.endsWith('window=1h')
          ? { auth_code: 0, reset_link: 0, magic_link: 0 }
          : { auth_code: 1.25, reset_link: 0, magic_link: 3 },
      }))
    })
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    const fixtureOrigin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      // Bridge jsdom's signal into native fetch's realm, preserving cancellation.
      const controller = transferableAbortController()
      const abort = () => controller.abort()
      if (init?.signal?.aborted) abort()
      init?.signal?.addEventListener('abort', abort, { once: true })
      return actualFetch(new URL(String(input), fixtureOrigin).href, { ...init, signal: controller.signal })
        .finally(() => init?.signal?.removeEventListener('abort', abort))
    })
    try {
      await renderPage()
      await waitFor(() => {
        const state = queryClient.getQueryState(['ingestion', 'pipeline-stats', '24h'])
        expect(state?.error).toBeNull()
        expect(state?.status).toBe('success')
      })
      await waitFor(() => expect(container.querySelector('[aria-label="Bearer scrub estimates"]')?.textContent).toContain('1.25'))
      const section = container.querySelector('[aria-label="Bearer scrub estimates"]')!
      expect(section.textContent).toContain('Auth codes')
      expect(section.textContent).toContain('Reset links')
      expect(section.textContent).toContain('Magic links')
      expect(section.textContent).toContain('Prometheus')
      expect(section.textContent).toContain('attempts')
      expect(Array.from(section.querySelectorAll('dd')).map(node => node.textContent)).toEqual(['1.25', '0', '3'])
      for (const range of ['1h', '7d'] as const) {
        act(() => capturedOnRangeReport?.(range))
        await waitFor(() => expect(paths).toContain(`/api/ingestion/pipeline?window=${range}`))
        await waitFor(() => expect(Array.from(section.querySelectorAll('dd')).map(node => node.textContent))
          .toEqual(range === '1h' ? ['0', '0', '0'] : ['1.25', '0', '3']))
      }
      expect(paths).toEqual([
        '/api/ingestion/pipeline?window=24h', '/api/ingestion/pipeline?window=1h', '/api/ingestion/pipeline?window=7d',
      ])
      expect(container.querySelector('[data-testid="timeline-tab-stub"]')).not.toBeNull()
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
    }
  })

  it.each(['loading', 'unavailable', 'false-with-zero', 'legacy', 'malformed', 'negative', 'request-error', 'stale-error'] as const)(
    'keeps the Timeline usable and scrub counts honest for %s', async (state) => {
      let resolveFetch: ((response: Response) => void) | undefined
      const healthy = { bearer_scrub_available: true, bearer_scrub_counts: { auth_code: 8, reset_link: 9, magic_link: 10 } }
      const key = ['ingestion', 'pipeline-stats', '24h']
      if (state === 'stale-error') queryClient.setQueryData(key, healthy)
      vi.stubGlobal('fetch', vi.fn(async () => {
        if (state === 'loading') return new Promise<Response>(resolve => { resolveFetch = resolve })
        if (state === 'request-error' || state === 'stale-error') throw new Error('fixture unavailable')
        const payload = state === 'legacy' ? { ingested: 42, aggregates_available: true }
          : state === 'malformed' ? { ...healthy, bearer_scrub_counts: { auth_code: 0 } }
            : state === 'negative' ? { ...healthy, bearer_scrub_counts: { auth_code: -1, reset_link: 0, magic_link: 0 } }
              : state === 'false-with-zero' ? { bearer_scrub_available: false, bearer_scrub_counts: { auth_code: 0, reset_link: 0, magic_link: 0 } }
                : { bearer_scrub_available: false, bearer_scrub_counts: null }
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }))
      await renderPage()
      const section = container.querySelector('[aria-label="Bearer scrub estimates"]')!
      if (state === 'loading') {
        expect(section.querySelector('[role="status"]')?.textContent).toContain('Loading')
        act(() => resolveFetch?.(new Response(JSON.stringify({}), { status: 200 })))
      }
      await waitFor(() => expect(section.querySelector('[role="alert"]')?.textContent).toContain('Prometheus'))
      expect(section.querySelector('[role="alert"]')?.textContent).toContain('unavailable')
      expect(section.querySelectorAll('dd')).toHaveLength(0)
      expect(container.querySelector('[data-testid="timeline-tab-stub"]')).not.toBeNull()
      expect(container.querySelector('[aria-label="Ingestion timeline verdict"]')).not.toBeNull()
    },
  )

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
