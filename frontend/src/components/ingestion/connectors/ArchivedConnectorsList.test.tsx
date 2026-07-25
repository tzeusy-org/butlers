// @vitest-environment jsdom
/**
 * ArchivedConnectorsList — collapsed archived-connectors section (bu-33dm2),
 * plus its unarchive UI path back (bu-ep4ks.11).
 *
 * Covers: collapsed-by-default toggle, history link, the unarchive action's
 * wiring, its pending ("unarchiving…" + disabled) state, and the error
 * surface. The mutation hook is mocked so no QueryClient/network is needed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const mutate = vi.fn()
let hookState: {
  mutate: typeof mutate
  isPending: boolean
  isError: boolean
  variables?: { connectorType: string; endpointIdentity: string }
}

vi.mock('@/hooks/use-ingestion', () => ({
  useUnarchiveConnector: vi.fn(() => hookState),
}))

import type { ConnectorSummary } from '@/api/types'
import { ArchivedConnectorsList } from './ArchivedConnectorsList'

function base(overrides: Partial<ConnectorSummary>): ConnectorSummary {
  return {
    connector_type: 'google_health',
    endpoint_identity: 'dead',
    liveness: 'offline',
    state: 'degraded',
    error_message: null,
    version: null,
    uptime_s: null,
    last_heartbeat_at: null,
    first_seen_at: '2026-01-01T00:00:00Z',
    today: { messages_ingested: 0, messages_failed: 0, uptime_pct: null },
    hourly_events: Array(24).fill(0),
    ...overrides,
  }
}

const ARCHIVED = base({ endpoint_identity: 'dead' })

function makeRoot(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  return { container, root: createRoot(container) }
}

function render(root: Root, connectors: ConnectorSummary[]) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ArchivedConnectorsList connectors={connectors} />
      </MemoryRouter>,
    )
  })
}

function expand(container: HTMLDivElement) {
  const toggle = container.querySelector('[data-testid="archived-toggle"]') as HTMLButtonElement
  act(() => toggle.click())
}

describe('ArchivedConnectorsList (bu-33dm2, unarchive: bu-ep4ks.11)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mutate.mockClear()
    hookState = { mutate, isPending: false, isError: false, variables: undefined }
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    document.body.innerHTML = ''
  })

  it('renders nothing when there are no archived connectors', () => {
    render(root, [])
    expect(container.querySelector('[data-testid="archived-section"]')).toBeNull()
  })

  it('is collapsed by default', () => {
    render(root, [ARCHIVED])
    expect(container.querySelector('[data-testid="archived-list"]')).toBeNull()
  })

  it('expands to show the archived row, linking to detail', () => {
    render(root, [ARCHIVED])
    expand(container)

    const row = container.querySelector('[data-testid="archived-row-google_health:dead"]')
    expect(row).not.toBeNull()
    const link = row?.querySelector('a')
    expect(link?.getAttribute('href')).toBe('/ingestion/connectors/google_health/dead')
  })

  it('the unarchive button fires the mutation with the identity', () => {
    render(root, [ARCHIVED])
    expand(container)

    const btn = container.querySelector(
      '[data-testid="unarchive-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())

    expect(mutate).toHaveBeenCalledWith({
      connectorType: 'google_health',
      endpointIdentity: 'dead',
    })
  })

  it('shows an "unarchiving…" disabled state on the pending row', () => {
    hookState = {
      mutate,
      isPending: true,
      isError: false,
      variables: { connectorType: 'google_health', endpointIdentity: 'dead' },
    }
    render(root, [ARCHIVED])
    expand(container)

    const btn = container.querySelector(
      '[data-testid="unarchive-action-google_health:dead"]',
    ) as HTMLButtonElement
    expect(btn.textContent).toMatch(/unarchiving/)
    expect(btn.disabled).toBe(true)
  })

  it('surfaces an error message when the unarchive fails', () => {
    hookState = { mutate, isPending: false, isError: true, variables: undefined }
    render(root, [ARCHIVED])

    expect(container.querySelector('[data-testid="archived-connectors-error"]')).not.toBeNull()
  })
})
