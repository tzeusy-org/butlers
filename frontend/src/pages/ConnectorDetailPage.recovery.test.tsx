// @vitest-environment jsdom
/**
 * Connector detail recovery routing.
 *
 * Exercises the page-level recovery callback separately from the visual detail
 * layout so Passport navigation and unsupported controls cannot regress into
 * raw OAuth provider construction.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockNavigate = vi.fn()

vi.mock('react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router')>()
  return {
    ...actual,
    useParams: vi.fn(() => ({
      connectorType: 'whatsapp_user_client',
      endpointIdentity: 'owner',
    })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
    useNavigate: vi.fn(() => mockNavigate),
  }
})

vi.mock('@/hooks/use-ingestion', () => ({
  useConnectorDetail: vi.fn(),
  useConnectorStats: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useConnectorEvents: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useConnectorIncidents: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useConnectorRoutingRules: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useUpdateConnectorSettings: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

vi.mock('@/components/ingestion/connectors/ConnectorDetailView', () => ({
  ConnectorDetailView: ({ onReauth }: { onReauth?: () => void }) => (
    <div>
      {onReauth ? (
        <button type="button" data-testid="recovery-action" onClick={onReauth}>
          recover
        </button>
      ) : (
        <span data-testid="recovery-unavailable">recovery unavailable</span>
      )}
    </div>
  ),
}))

vi.mock('@/components/ingestion/IngestionSubNav', () => ({
  IngestionSubNav: () => null,
}))

vi.mock('@/components/ingestion/dispatch', () => ({
  DispatchLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DispatchSurface: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('sonner', () => ({ toast: { warning: vi.fn() } }))

import ConnectorDetailPage from '@/pages/ConnectorDetailPage'
import { useConnectorDetail } from '@/hooks/use-ingestion'
import { useParams } from 'react-router'
import type { ConnectorDetail } from '@/api/types'

const BASE_CONNECTOR: ConnectorDetail = {
  connector_type: 'whatsapp_user_client',
  endpoint_identity: 'owner',
  liveness: 'online',
  state: 'error',
  error_message: 'session expired',
  version: '1.0.0',
  uptime_s: 3600,
  last_heartbeat_at: '2026-07-23T00:00:00Z',
  first_seen_at: '2026-07-22T00:00:00Z',
  today: { messages_ingested: 0, messages_failed: 1, uptime_pct: 90 },
  hourly_events: Array(24).fill(0),
  instance_id: null,
  registered_via: 'auto',
  checkpoint: null,
  counters: null,
  settings: null,
  auth: null,
  scopes: null,
}

function setConnector(connector: ConnectorDetail) {
  vi.mocked(useConnectorDetail).mockReturnValue({
    data: { data: connector },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useConnectorDetail>)
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectorDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ConnectorDetailPage recovery routing', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(useParams).mockReturnValue({
      connectorType: 'whatsapp_user_client',
      endpointIdentity: 'owner',
    })
  })

  afterEach(() => cleanup())

  it('uses in-app Passport navigation for WhatsApp pairing', () => {
    setConnector(BASE_CONNECTOR)
    renderPage()

    fireEvent.click(screen.getByTestId('recovery-action'))

    expect(mockNavigate).toHaveBeenCalledWith('/secrets?focus=u:whatsapp')
  })

  it('does not expose an interactive recovery control for an unsupported connector', () => {
    vi.mocked(useParams).mockReturnValue({ connectorType: 'steam', endpointIdentity: 'owner' })
    setConnector({ ...BASE_CONNECTOR, connector_type: 'steam' })
    renderPage()

    expect(screen.queryByTestId('recovery-action')).toBeNull()
    expect(screen.getByTestId('recovery-unavailable')).toBeDefined()
  })
})
