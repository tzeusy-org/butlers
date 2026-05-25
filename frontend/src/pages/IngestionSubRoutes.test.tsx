// @vitest-environment jsdom
/**
 * Tests for the INGESTION_DISPATCH_CONSOLE sub-route scaffolding (§2.1).
 *
 * Covers:
 *   - IngestionTabRedirect: ?tab=connectors|filters|history → sub-route redirect
 *   - IngestionTabRedirect: no ?tab= → renders Timeline
 *   - IngestionTabRedirect: unknown ?tab= → redirects to /ingestion (strips tab param)
 *   - IngestionTabRedirect: filter params (period, channel, status) preserved
 *   - Sub-route pages render their page headings
 *
 * Tests import IngestionTabRedirect directly from router.tsx (it is exported).
 * The feature-flag module and IngestionTimelinePage are mocked so that importing
 * router.tsx does not evaluate createBrowserRouter side-effects or the flag.
 */

import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router'

// ---------------------------------------------------------------------------
// Mock feature-flags before importing router so the module-level flag
// evaluation does not run the real env-var read or createBrowserRouter.
// ---------------------------------------------------------------------------
vi.mock('@/lib/feature-flags', () => ({
  INGESTION_DISPATCH_CONSOLE: false,
}))

// Mock IngestionTimelinePage so the redirect component renders a testable stub
// instead of pulling in the real component and its dependencies.
vi.mock('@/pages/IngestionTimelinePage', () => ({
  default: () => <div data-testid="timeline-page">Timeline</div>,
}))

// ---------------------------------------------------------------------------
// Mock the page components used by the sub-route pages so tests don't
// need QueryClientProvider.
// ---------------------------------------------------------------------------

vi.mock('@/components/ingestion/TimelineTab', () => ({
  TimelineTab: () => <div data-testid="timeline-tab-stub">Timeline tab</div>,
}))
vi.mock('@/components/ingestion/ConnectorsTab', () => ({
  ConnectorsTab: () => <div data-testid="connectors-tab-stub">Connectors tab</div>,
}))
vi.mock('@/components/ingestion/ConnectorsListPage', () => ({
  ConnectorsListPage: () => <div data-testid="connectors-tab-stub">Connectors tab</div>,
}))
vi.mock('@/components/switchboard/FiltersTab', () => ({
  FiltersTab: () => <div data-testid="filters-tab-stub">Filters tab</div>,
}))
vi.mock('@/components/switchboard/BackfillHistoryTab', () => ({
  BackfillHistoryTab: () => <div data-testid="history-tab-stub">History tab</div>,
}))

// Import the real component after mocks are registered.
import { IngestionTabRedirect } from '@/router'

// ---------------------------------------------------------------------------
// Stub page components for sub-route destinations
// ---------------------------------------------------------------------------

function ConnectorsStub() {
  return <div data-testid="connectors-page">Connectors</div>
}

function FiltersStub() {
  return <div data-testid="filters-page">Filters</div>
}

function HistoryStub() {
  return <div data-testid="history-page">History</div>
}

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

describe('IngestionTabRedirect', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  function render(initialPath: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/connectors" element={<ConnectorsStub />} />
            <Route path="/ingestion/filters" element={<FiltersStub />} />
            <Route path="/ingestion/history" element={<HistoryStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
  }

  // --- 301-equivalent redirects ---

  it('redirects ?tab=connectors to /ingestion/connectors', () => {
    render('/ingestion?tab=connectors')
    expect(container.querySelector('[data-testid="connectors-page"]')).not.toBeNull()
  })

  it('redirects ?tab=filters to /ingestion/filters', () => {
    render('/ingestion?tab=filters')
    expect(container.querySelector('[data-testid="filters-page"]')).not.toBeNull()
  })

  it('redirects ?tab=history to /ingestion (Timeline), not to /ingestion/history', () => {
    // Spec (complete-ingestion-redesign-parity): "history SHALL map to the Timeline route …
    // it SHALL NOT remain a fourth redesigned tab." No primary /ingestion/history route.
    render('/ingestion?tab=history')
    // Must NOT land on /ingestion/history
    expect(container.querySelector('[data-testid="history-page"]')).toBeNull()
    // Must render Timeline (IngestionTabRedirect with no tab → renders IngestionTimelinePage stub)
    expect(container.querySelector('[data-testid="timeline-page"]')).not.toBeNull()
  })

  // --- Filter param preservation ---

  it('preserves period param when redirecting ?tab=connectors&period=7d', () => {
    // We test that the redirect destination contains the preserved params by
    // rendering a stub that reads search params and exposes them.
    function ConnectorsWithParams() {
      const [sp] = useSearchParams()
      return (
        <div data-testid="connectors-page" data-period={sp.get('period') ?? ''}>
          Connectors
        </div>
      )
    }

    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion?tab=connectors&period=7d']}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/connectors" element={<ConnectorsWithParams />} />
          </Routes>
        </MemoryRouter>,
      )
    })

    const el = container.querySelector('[data-testid="connectors-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-period')).toBe('7d')
  })

  it('strips ?tab= from the redirected URL', () => {
    // Verifies the 'tab' key does not leak into the sub-route query string.
    function ConnectorsWithParams() {
      const [sp] = useSearchParams()
      return (
        <div data-testid="connectors-page" data-has-tab={sp.has('tab') ? 'yes' : 'no'}>
          Connectors
        </div>
      )
    }

    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion?tab=connectors&period=7d']}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/connectors" element={<ConnectorsWithParams />} />
          </Routes>
        </MemoryRouter>,
      )
    })

    const el = container.querySelector('[data-testid="connectors-page"]')
    expect(el?.getAttribute('data-has-tab')).toBe('no')
  })

  // --- Fall-through to Timeline ---

  it('renders Timeline for /ingestion with no ?tab= param', () => {
    render('/ingestion')
    expect(container.querySelector('[data-testid="timeline-page"]')).not.toBeNull()
  })

  it('redirects to /ingestion for unrecognized ?tab=unknown (strips invalid tab param)', () => {
    // Unknown tab values redirect to /ingestion without the tab param so stale
    // bookmarks do not perpetuate an invalid ?tab= in the URL. The MemoryRouter
    // resolves /ingestion back to IngestionTabRedirect with no tab param, which
    // then renders Timeline directly (tab === null path).
    render('/ingestion?tab=unknown')
    // After the redirect resolves, Timeline is rendered (tab is null on second pass).
    expect(container.querySelector('[data-testid="timeline-page"]')).not.toBeNull()
  })

  it('does not redirect when ?tab= is absent', () => {
    render('/ingestion')
    expect(container.querySelector('[data-testid="connectors-page"]')).toBeNull()
    expect(container.querySelector('[data-testid="filters-page"]')).toBeNull()
    expect(container.querySelector('[data-testid="history-page"]')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Sub-route page components (smoke: heading and tab content rendered)
// ---------------------------------------------------------------------------

describe('IngestionTimelinePage', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('renders the Ingestion heading and timeline tab', async () => {
    const { default: IngestionTimelinePage } = await vi.importActual<{ default: React.ComponentType }>('@/pages/IngestionTimelinePage')
    act(() => {
      root.render(
        <MemoryRouter>
          <IngestionTimelinePage />
        </MemoryRouter>,
      )
    })
    expect(container.querySelector('h1')?.textContent).toBe('Ingestion')
    expect(container.querySelector('[data-testid="timeline-tab-stub"]')).not.toBeNull()
  })
})

describe('IngestionConnectorsPage', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('renders the Connectors heading and connectors tab', async () => {
    const { default: IngestionConnectorsPage } = await import('@/pages/IngestionConnectorsPage')
    act(() => {
      root.render(
        <MemoryRouter>
          <IngestionConnectorsPage />
        </MemoryRouter>,
      )
    })
    expect(container.querySelector('h1')?.textContent).toBe('Connectors')
    expect(container.querySelector('[data-testid="connectors-tab-stub"]')).not.toBeNull()
  })
})

describe('IngestionFiltersPage', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('renders the Filters heading and filters tab stub', async () => {
    const { default: IngestionFiltersPage } = await import('@/pages/IngestionFiltersPage')
    act(() => {
      root.render(
        <MemoryRouter>
          <IngestionFiltersPage />
        </MemoryRouter>,
      )
    })
    expect(container.querySelector('h1')?.textContent).toBe('Filters')
    expect(container.querySelector('[data-testid="filters-tab-stub"]')).not.toBeNull()
  })
})

describe('IngestionHistoryPage', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('renders the History heading and history tab stub', async () => {
    const { default: IngestionHistoryPage } = await import('@/pages/IngestionHistoryPage')
    act(() => {
      root.render(
        <MemoryRouter>
          <IngestionHistoryPage />
        </MemoryRouter>,
      )
    })
    expect(container.querySelector('h1')?.textContent).toBe('History')
    expect(container.querySelector('[data-testid="history-tab-stub"]')).not.toBeNull()
  })
})
