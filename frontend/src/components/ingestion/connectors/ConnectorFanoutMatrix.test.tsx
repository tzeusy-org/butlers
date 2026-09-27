// @vitest-environment jsdom
/** Routing distribution state contract: measured, empty, unavailable, and stale. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/hooks/use-ingestion', () => ({
  useConnectorFanout: vi.fn(),
}))

import { useConnectorFanout } from '@/hooks/use-ingestion'
import type { ConnectorFanoutResponse } from '@/api/types'
import { ConnectorFanoutMatrix } from './ConnectorFanoutMatrix'

const refetch = vi.fn()

const MEASURED_ROUTES: ConnectorFanoutResponse = {
  data: [
    {
      connector_type: 'gmail',
      endpoint_identity: 'gmail:primary',
      target_butler: 'relationship',
      message_count: 8,
    },
    {
      connector_type: 'telegram_bot',
      endpoint_identity: 'telegram:house',
      target_butler: 'health',
      message_count: 3,
    },
  ],
  meta: { aggregates_available: true },
}

function setFanoutResult(
  result: Partial<ReturnType<typeof useConnectorFanout>>,
) {
  vi.mocked(useConnectorFanout).mockReturnValue({
    data: undefined,
    isError: false,
    isLoading: false,
    refetch,
    ...result,
  } as ReturnType<typeof useConnectorFanout>)
}

describe('ConnectorFanoutMatrix', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    refetch.mockReset()
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    document.body.innerHTML = ''
  })

  function render() {
    act(() => {
      root.render(<ConnectorFanoutMatrix />)
    })
  }

  it('renders each measured route in a semantic table', () => {
    setFanoutResult({ data: MEASURED_ROUTES })
    render()

    const table = container.querySelector('table')
    expect(table).not.toBeNull()
    expect(table?.querySelector('caption')?.textContent).toContain('last 7 days')
    expect(table?.textContent).toContain('gmail:primary')
    expect(table?.textContent).toContain('relationship')
    expect(table?.textContent).toContain('8')
    expect(container.querySelector('[data-testid="connector-fanout-empty"]')).toBeNull()
    expect(container.querySelector('[data-testid="connector-fanout-unavailable"]')).toBeNull()
  })

  it('names an explicit unavailable aggregate and hides fallback rows', () => {
    setFanoutResult({
      data: {
        ...MEASURED_ROUTES,
        meta: { aggregates_available: false },
      },
    })
    render()

    const note = container.querySelector('[data-testid="connector-fanout-unavailable"]')
    expect(note?.getAttribute('role')).toBe('alert')
    expect(note?.textContent).toContain('no routing distribution can be confirmed')
    expect(container.querySelector('table')).toBeNull()
    expect(container.querySelector('[data-testid="connector-fanout-empty"]')).toBeNull()
  })

  it('renders a successful empty vector as measured empty, not degraded', () => {
    setFanoutResult({
      data: { data: [], meta: { aggregates_available: true } },
    })
    render()

    expect(container.querySelector('[data-testid="connector-fanout-empty"]')?.textContent).toContain(
      'No routed messages recorded',
    )
    expect(container.querySelector('[data-testid="connector-fanout-unavailable"]')).toBeNull()
  })

  it('names a failed initial query and lets the operator retry', () => {
    setFanoutResult({ isError: true })
    render()

    const note = container.querySelector('[data-testid="connector-fanout-unavailable"]')
    expect(note?.textContent).toContain('latest routing distribution could not be loaded')
    const retry = note?.querySelector('button')
    act(() => {
      retry?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('keeps a last measured matrix visible when only its refresh fails', () => {
    setFanoutResult({ data: MEASURED_ROUTES, isError: true })
    render()

    expect(container.querySelector('[data-testid="connector-fanout-stale"]')?.textContent).toContain(
      'showing the last measured routing distribution',
    )
    expect(container.querySelector('table')).not.toBeNull()
  })
})
