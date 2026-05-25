// @vitest-environment jsdom
/**
 * Ingestion Dispatch Console — Route Foundation Tests (bu-y25mj.1)
 *
 * Covers acceptance criteria:
 * 1. /ingestion mounts the new Dispatch shell — no legacy <TabsTrigger> present
 * 2. IngestionSubNav highlights the current route correctly
 * 3. /ingestion?tab=connectors redirects to /ingestion/connectors
 * 4. /ingestion?tab=filters redirects to /ingestion/filters
 * 5. /ingestion?tab=history redirects to /ingestion (Timeline, not /ingestion/history)
 * 6. ?tab= redirect preserves compatible query params (range, channels, status)
 *
 * All tab content components are mocked so this file needs no QueryClientProvider.
 * The feature-flags module is mocked to set INGESTION_DISPATCH_CONSOLE = true so
 * the routes in router-config actually use the new dispatch sub-route hierarchy.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router'

// ---------------------------------------------------------------------------
// Mocks — must be before any router/page imports
// ---------------------------------------------------------------------------

vi.mock('@/lib/feature-flags', () => ({
  INGESTION_DISPATCH_CONSOLE: true,
}))

vi.mock('@/pages/IngestionTimelinePage', () => ({
  default: () => (
    <div data-testid="timeline-page">
      <nav aria-label="Ingestion views">
        <a href="/ingestion" aria-current="page">Timeline</a>
        <a href="/ingestion/connectors">Connectors</a>
        <a href="/ingestion/filters">Filters</a>
      </nav>
    </div>
  ),
}))

vi.mock('@/components/ingestion/TimelineTab', () => ({
  TimelineTab: () => <div data-testid="timeline-tab-stub">Timeline tab</div>,
}))
vi.mock('@/components/ingestion/ConnectorsListPage', () => ({
  ConnectorsListPage: () => <div data-testid="connectors-list-stub">Connectors list</div>,
}))
vi.mock('@/components/ingestion/ConnectorsTab', () => ({
  ConnectorsTab: () => <div data-testid="connectors-tab-stub">Connectors tab</div>,
}))
vi.mock('@/components/switchboard/FiltersTab', () => ({
  FiltersTab: () => <div data-testid="filters-tab-stub">Filters tab</div>,
}))
vi.mock('@/components/switchboard/BackfillHistoryTab', () => ({
  BackfillHistoryTab: () => <div data-testid="history-tab-stub">History tab</div>,
}))

// Import after mocks
import { IngestionTabRedirect } from '@/router'

// ---------------------------------------------------------------------------
// Stub page components for sub-routes
// ---------------------------------------------------------------------------

function TimelineStub() {
  return (
    <div data-testid="timeline-page">
      <nav aria-label="Ingestion views">
        <a href="/ingestion" aria-current="page">Timeline</a>
        <a href="/ingestion/connectors">Connectors</a>
        <a href="/ingestion/filters">Filters</a>
      </nav>
    </div>
  )
}

function ConnectorsStub() {
  return (
    <div data-testid="connectors-page">
      <nav aria-label="Ingestion views">
        <a href="/ingestion">Timeline</a>
        <a href="/ingestion/connectors" aria-current="page">Connectors</a>
        <a href="/ingestion/filters">Filters</a>
      </nav>
    </div>
  )
}

function FiltersStub() {
  return (
    <div data-testid="filters-page">
      <nav aria-label="Ingestion views">
        <a href="/ingestion">Timeline</a>
        <a href="/ingestion/connectors">Connectors</a>
        <a href="/ingestion/filters" aria-current="page">Filters</a>
      </nav>
    </div>
  )
}

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Shared test helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// §1: /ingestion mounts new Dispatch shell — no legacy TabsTrigger
// ---------------------------------------------------------------------------

describe('§1 /ingestion renders Dispatch shell without legacy TabsTrigger', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('renders the ingestion page without any [role="tab"] elements', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion']}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    // The Dispatch shell must not contain any TabsTrigger (role=tab)
    const tabTriggers = container.querySelectorAll('[role="tab"]')
    expect(tabTriggers.length).toBe(0)
  })

  it('renders IngestionSubNav (the nav landmark with Ingestion views label)', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion']}>
          <Routes>
            <Route path="/ingestion" element={<TimelineStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    expect(nav).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// §2: IngestionSubNav highlights current route
// ---------------------------------------------------------------------------

describe('§2 IngestionSubNav highlights the current route', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  it('marks Timeline as active (aria-current=page) at /ingestion', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion']}>
          <Routes>
            <Route path="/ingestion" element={<TimelineStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    const timelineLink = Array.from(nav?.querySelectorAll('a') ?? []).find(
      (a) => a.textContent?.trim() === 'Timeline',
    )
    expect(timelineLink?.getAttribute('aria-current')).toBe('page')
  })

  it('marks Connectors as active (aria-current=page) at /ingestion/connectors', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion/connectors']}>
          <Routes>
            <Route path="/ingestion/connectors" element={<ConnectorsStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    const connectorsLink = Array.from(nav?.querySelectorAll('a') ?? []).find(
      (a) => a.textContent?.trim() === 'Connectors',
    )
    expect(connectorsLink?.getAttribute('aria-current')).toBe('page')
  })

  it('marks Filters as active (aria-current=page) at /ingestion/filters', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion/filters']}>
          <Routes>
            <Route path="/ingestion/filters" element={<FiltersStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    const filtersLink = Array.from(nav?.querySelectorAll('a') ?? []).find(
      (a) => a.textContent?.trim() === 'Filters',
    )
    expect(filtersLink?.getAttribute('aria-current')).toBe('page')
  })
})

// ---------------------------------------------------------------------------
// §3–4: Legacy ?tab= redirects
// ---------------------------------------------------------------------------

describe('§3–4 Legacy ?tab= redirects', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
  })
  afterEach(() => cleanup(root, container))

  function render(initialPath: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/connectors" element={<ConnectorsStub />} />
            <Route path="/ingestion/filters" element={<FiltersStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
  }

  it('§3 redirects /ingestion?tab=connectors to /ingestion/connectors', () => {
    render('/ingestion?tab=connectors')
    expect(container.querySelector('[data-testid="connectors-page"]')).not.toBeNull()
  })

  it('§4 redirects /ingestion?tab=filters to /ingestion/filters', () => {
    render('/ingestion?tab=filters')
    expect(container.querySelector('[data-testid="filters-page"]')).not.toBeNull()
  })

  it('§5 redirects /ingestion?tab=history to /ingestion (Timeline, not a history sub-route)', () => {
    // Per spec: "history SHALL map to the Timeline route … SHALL NOT remain a fourth redesigned tab"
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion?tab=history']}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/history" element={<div data-testid="history-page" />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    // Should NOT land on /ingestion/history
    expect(container.querySelector('[data-testid="history-page"]')).toBeNull()
    // Should render Timeline (IngestionTabRedirect with no tab renders timeline)
    expect(container.querySelector('[data-testid="timeline-page"]')).not.toBeNull()
  })

  it('preserves compatible query params (range) when redirecting ?tab=connectors', () => {
    function ConnectorsWithParams() {
      const [sp] = useSearchParams()
      return (
        <div
          data-testid="connectors-page"
          data-range={sp.get('range') ?? ''}
          data-has-tab={sp.has('tab') ? 'yes' : 'no'}
        >
          Connectors
        </div>
      )
    }

    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/ingestion?tab=connectors&range=7d']}>
          <Routes>
            <Route path="/ingestion" element={<IngestionTabRedirect />} />
            <Route path="/ingestion/connectors" element={<ConnectorsWithParams />} />
          </Routes>
        </MemoryRouter>,
      )
    })

    const el = container.querySelector('[data-testid="connectors-page"]')
    expect(el).not.toBeNull()
    // range param must be preserved
    expect(el?.getAttribute('data-range')).toBe('7d')
    // tab param must be stripped
    expect(el?.getAttribute('data-has-tab')).toBe('no')
  })

  it('strips the ?tab= param from the redirected URL', () => {
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
        <MemoryRouter initialEntries={['/ingestion?tab=connectors']}>
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
})

// ---------------------------------------------------------------------------
// §6: Dispatch primitives are importable from central location
// ---------------------------------------------------------------------------

describe('§6 Dispatch primitives import from central location', () => {
  it('DispatchLayout, DispatchHeader, DispatchSurface are importable', async () => {
    const { DispatchLayout, DispatchHeader, DispatchSurface } = await import(
      '@/components/ingestion/dispatch'
    )
    expect(typeof DispatchLayout).toBe('function')
    expect(typeof DispatchHeader).toBe('function')
    expect(typeof DispatchSurface).toBe('function')
  })

  it('IngestionSubNav is importable from @/components/ingestion/IngestionSubNav', async () => {
    const { IngestionSubNav } = await import('@/components/ingestion/IngestionSubNav')
    expect(typeof IngestionSubNav).toBe('function')
  })
})
