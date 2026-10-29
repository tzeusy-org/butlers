// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Link, MemoryRouter, Route, Routes } from 'react-router'
import ConnectorDetailPage from './ConnectorDetailPage'
import { ingestionKeys, useConnectorSummaries } from '@/hooks/use-ingestion'

const ID = 'owner+alerts@example.org:work'
const OTHER = 'other@example.org'
const detailPath = (id = ID) => `/api/ingestion/connectors/gmail/${encodeURIComponent(id)}`
const routePath = (id = ID) => detailPath(id).replace('/api', '')
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function payload(id: string, state: string) {
  return {
    connector_type: 'gmail', endpoint_identity: id, state,
    error_message: null, instance_id: 'test-instance', version: '1', uptime_s: 20,
    last_heartbeat_at: new Date().toISOString(), first_seen_at: '2026-10-01T00:00:00Z',
    registered_via: 'auto', counter_messages_ingested: 0, counter_messages_failed: 0,
    counter_source_api_calls: 0, counter_checkpoint_saves: 0, counter_dedupe_accepted: 0,
    today_messages_ingested: 0, today_messages_failed: 0,
    checkpoint_cursor: null, checkpoint_updated_at: null, settings: null, scopes: null,
  }
}

let states: Map<string, string>
let calls: { path: string; options: RequestInit }[]
let post: (path: string, options: RequestInit) => Promise<Response>
let read: ((id: string) => Promise<Response>) | undefined
let summaryRead: (() => Promise<Response>) | undefined
let client: QueryClient

function SummaryReader() {
  useConnectorSummaries()
  return null
}

function mount(state = 'healthy') {
  states.set(ID, state)
  client = new QueryClient({ defaultOptions: {
    queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    mutations: { retry: false },
  } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[routePath()]}>
        <Link to={routePath(OTHER)}>Other connector</Link>
        <SummaryReader />
        <Routes>
          <Route path="/ingestion/connectors/:connectorType/:endpointIdentity" element={<ConnectorDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement
const posts = () => calls.filter((c) => c.options.method === 'POST')
const reads = (id = ID) => calls.filter((c) => c.path === detailPath(id) && c.options.method !== 'POST')
const confirmed = (id = ID) => client.getQueryData<{ data: { state: string } }>(ingestionKeys.connectorDetail('gmail', id))?.data.state

describe('connector detail lifecycle writes', () => {
  beforeEach(() => {
    states = new Map([[OTHER, 'healthy']])
    calls = []
    read = undefined
    summaryRead = undefined
    post = async (path) => {
      const id = decodeURIComponent(path.split('/').at(-2)!)
      const state = path.endsWith('/pause') ? 'paused' : 'unknown'
      states.set(id, state)
      return json({ data: { connector_type: 'gmail', endpoint_identity: id, state } })
    }
    // Real apiFetch and ownerFetch execute, including the established synthetic
    // owner session and CSRF header. Only the HTTP boundary is controlled.
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
      const path = String(url)
      calls.push({ path, options })
      if (options.method === 'POST') return post(path, options)
      if (path.endsWith('/summaries')) return summaryRead ? summaryRead() : json({ data: { connectors: [] } })
      if (path.includes('/stats?')) return json({ data: [] })
      if (path.endsWith('/routing-rules')) return json({ rules: [] })
      if (path.includes('/events?')) return json({ events: [] })
      if (path.includes('/incidents?')) return json({ incidents: [] })
      const id = decodeURIComponent(path.split('/').at(-1)!)
      if (!states.has(id)) throw new Error(`Unexpected test request: ${path}`)
      return read ? read(id) : json({ data: payload(id, states.get(id)!) })
    }))
  })

  afterEach(() => {
    cleanup()
    client?.clear()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('pauses and resumes through the audited routes, using confirmed state and keyboard controls', async () => {
    mount()
    const user = userEvent.setup()
    const pause = await screen.findByRole('button', { name: 'Pause' })
    expect(button('Resume').disabled).toBe(true)
    expect(screen.getByText('Resume is available only while paused.')).toBeDefined()
    pause.focus()
    expect(document.activeElement).toBe(pause)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(confirmed()).toBe('paused'))
    expect(screen.getAllByText('paused').length).toBeGreaterThan(0)
    expect(posts().map((c) => c.path)).toEqual([`${detailPath()}/pause`])
    expect(posts()[0].options.credentials).toBe('same-origin')
    expect(new Headers(posts()[0].options.headers).has('X-CSRF-Token')).toBe(true)
    expect(posts()[0].options.body).toBeUndefined()
    button('Resume').focus()
    await user.keyboard(' ')
    await waitFor(() => expect(confirmed()).toBe('unknown'))
    expect(posts().map((c) => c.path)).toEqual([`${detailPath()}/pause`, `${detailPath()}/run-now`])
    expect(screen.getAllByText('unknown').length).toBeGreaterThan(0)
    expect(screen.getByText('Resume clears pause; state stays unknown until the next heartbeat.')).toBeDefined()
    expect(calls.filter((c) => c.path.endsWith('/summaries')).length).toBe(3)
    expect(screen.queryByText(/audit recorded|run now|poll immediately/i)).toBeNull()
  })

  it('shows a stale-paused 409 explanation without an optimistic state change and refreshes authority', async () => {
    mount('paused')
    await screen.findByRole('button', { name: 'Resume' })
    const refreshed = deferred<Response>()
    read = () => refreshed.promise
    post = async () => json({ detail: "Connector is not paused (current state: 'healthy')." }, 409)
    fireEvent.click(button('Resume'))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining("current state: 'healthy'"))
    expect(confirmed()).toBe('paused')
    expect(button('Pause').disabled).toBe(true)
    expect(button('Resume').disabled).toBe(true)
    await act(async () => refreshed.resolve(json({ data: payload(ID, 'healthy') })))
    await waitFor(() => expect(button('Pause').disabled).toBe(false))
    expect(confirmed()).toBe('healthy')
    expect(button('Resume').disabled).toBe(true)
    expect(posts()).toHaveLength(1)
  })

  it('sends one POST on rapid activation and stays disabled through refresh, with paused pause a no-op', async () => {
    mount()
    await screen.findByRole('button', { name: 'Pause' })
    const accepted = deferred<Response>()
    const refreshed = deferred<Response>()
    post = () => accepted.promise
    read = () => refreshed.promise
    const pause = button('Pause')
    act(() => { fireEvent.click(pause); fireEvent.click(pause) })
    await waitFor(() => expect(posts()).toHaveLength(1))
    expect(button('Pause').disabled).toBe(true)
    expect(button('Resume').disabled).toBe(true)
    await act(async () => accepted.resolve(json({ data: { state: 'paused' } })))
    await waitFor(() => expect(reads()).toHaveLength(2))
    expect(confirmed()).toBe('healthy')
    expect(button('Pause').disabled).toBe(true)
    await act(async () => refreshed.resolve(json({ data: payload(ID, 'paused') })))
    await waitFor(() => expect(button('Resume').disabled).toBe(false))
    fireEvent.click(button('Pause'))
    expect(posts()).toHaveLength(1)
    expect(screen.queryByRole('alert')).toBeNull()

    // A failed detail refresh must not release pending while summaries still
    // refresh. Cached confirmed data stays visible even when its GET fails.
    const summaries = deferred<Response>()
    summaryRead = () => summaries.promise
    read = async () => json({ detail: 'Reader unavailable' }, 503)
    post = async () => json({ data: { state: 'unknown' } })
    fireEvent.click(button('Resume'))
    await waitFor(() => expect(reads()).toHaveLength(3))
    await waitFor(() => expect(client.getQueryState(ingestionKeys.connectorDetail('gmail', ID))?.status).toBe('error'))
    expect(button('Resume').disabled).toBe(true)
    expect(confirmed()).toBe('paused')
    await act(async () => summaries.resolve(json({ data: { connectors: [] } })))
    await waitFor(() => expect(button('Resume').disabled).toBe(false))
    expect(screen.getAllByRole('alert').some((el) => el.textContent?.includes('status refresh failed'))).toBe(true)
    fireEvent.click(button('Resume'))
    await waitFor(() => expect(button('Resume').disabled).toBe(false))
    expect(posts()).toHaveLength(2) // The failed retry preflight cannot write.
    expect(confirmed()).toBe('paused')
  })

  it('recovers from transport and unavailable failures by reading before deliberate retry and isolates navigation', async () => {
    mount()
    await screen.findByRole('button', { name: 'Pause' })
    post = async () => { throw new TypeError('Network connection lost') }
    fireEvent.click(button('Pause'))
    await screen.findByRole('alert')
    await waitFor(() => expect(button('Pause').disabled).toBe(false))
    expect(confirmed()).toBe('healthy')
    expect(posts()).toHaveLength(1)
    expect(screen.getByRole('alert').textContent).toContain('refresh state before another write')

    const fresh = deferred<Response>()
    read = () => fresh.promise
    post = async () => json({ detail: 'Connector registry is not available' }, 503)
    fireEvent.click(button('Pause'))
    await waitFor(() => expect(reads()).toHaveLength(3))
    expect(posts()).toHaveLength(1)
    read = undefined
    await act(async () => fresh.resolve(json({ data: payload(ID, 'healthy') })))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Connector registry is not available'))
    await waitFor(() => expect(button('Pause').disabled).toBe(false))
    expect(posts()).toHaveLength(2)
    expect(confirmed()).toBe('healthy')

    post = async () => json({ detail: 'Connector not found' }, 404)
    fireEvent.click(button('Pause'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Connector not found'))
    await waitFor(() => expect(button('Pause').disabled).toBe(false))
    expect(posts()).toHaveLength(3)
    expect(confirmed()).toBe('healthy')

    // Exercise apiFetch's real finite timeout without a 15-second wall wait.
    post = (_path, options) => new Promise((_resolve, reject) => {
      options.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })
    vi.useFakeTimers()
    await act(async () => {
      fireEvent.click(button('Pause'))
      await vi.advanceTimersByTimeAsync(15_001)
    })
    vi.useRealTimers()
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Request timed out'))
    await waitFor(() => expect(button('Pause').disabled).toBe(false))
    expect(posts()).toHaveLength(4)
    expect(confirmed()).toBe('healthy')

    const oldResponse = deferred<Response>()
    post = () => oldResponse.promise
    fireEvent.click(button('Pause'))
    await waitFor(() => expect(posts()).toHaveLength(5))
    await userEvent.click(screen.getByRole('link', { name: 'Other connector' }))
    await waitFor(() => expect(confirmed(OTHER)).toBe('healthy'))
    expect(button('Pause').disabled).toBe(false)
    expect(screen.queryByRole('alert')).toBeNull()
    await act(async () => oldResponse.resolve(json({ detail: 'Old connector disappeared' }, 404)))
    expect(confirmed(OTHER)).toBe('healthy')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(posts().every((c) => c.path.startsWith(detailPath()))).toBe(true)
  })
})
