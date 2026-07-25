// @vitest-environment jsdom
/**
 * ArchiveCandidatesList — archive review queue (bu-u19yv).
 *
 * Covers: filtering to `archive_candidate` rows, the undo-window archive
 * wiring (bu-ep4ks.11), the pending ("archiving…" + disabled) state, and the
 * error surface. The mutation hook is mocked so no QueryClient/network is
 * needed; useUndoWindow is the real hook, driven with fake timers (mirrors
 * ButlersPage.interaction.test.tsx's own restore-undo coverage).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { toast } from 'sonner'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// sonner's real export is a CALLABLE function (toast(msg, opts)) that also
// carries .success/.error statics -- the undo-toast path calls it directly.
vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() })
  return { toast: toastFn }
})

const UNDO_WINDOW_MS = 5_000

const mutate = vi.fn()
let hookState: {
  mutate: typeof mutate
  isPending: boolean
  isError: boolean
  variables?: { connectorType: string; endpointIdentity: string }
}

vi.mock('@/hooks/use-ingestion', () => ({
  useArchiveConnector: vi.fn(() => hookState),
}))

import type { ConnectorSummary } from '@/api/types'
import { ArchiveCandidatesList } from './ArchiveCandidatesList'

function base(overrides: Partial<ConnectorSummary>): ConnectorSummary {
  return {
    connector_type: 'google_health',
    endpoint_identity: 'dead',
    liveness: 'offline',
    state: 'degraded',
    error_message: null,
    version: null,
    uptime_s: null,
    last_heartbeat_at: new Date(Date.now() - 45 * 24 * 3600_000).toISOString(),
    first_seen_at: '2026-01-01T00:00:00Z',
    today: { messages_ingested: 0, messages_failed: 0, uptime_pct: null },
    hourly_events: Array(24).fill(0),
    ...overrides,
  }
}

const CANDIDATE = base({ endpoint_identity: 'dead', archive_candidate: true })
const NON_CANDIDATE = base({ endpoint_identity: 'live', archive_candidate: false })

function makeRoot(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  return { container, root: createRoot(container) }
}

function render(root: Root, connectors: ConnectorSummary[]) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ArchiveCandidatesList connectors={connectors} />
      </MemoryRouter>,
    )
  })
}

describe('ArchiveCandidatesList (bu-u19yv)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    vi.useFakeTimers()
    ;({ container, root } = makeRoot())
    mutate.mockClear()
    hookState = { mutate, isPending: false, isError: false, variables: undefined }
  })
  afterEach(() => {
    // useUndoWindow's store is module-scoped -- flush any pending schedule
    // before the next test so it never leaks across tests in this file.
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS)
    })
    act(() => root.unmount())
    container.remove()
    document.body.innerHTML = ''
    vi.useRealTimers()
    vi.mocked(toast).mockClear()
  })

  it('renders nothing when there are no candidates', () => {
    render(root, [NON_CANDIDATE])
    expect(
      container.querySelector('[data-testid="archive-candidates-section"]'),
    ).toBeNull()
  })

  it('lists only archive_candidate rows and links each to detail', () => {
    render(root, [CANDIDATE, NON_CANDIDATE])
    const rows = container.querySelectorAll('[data-testid^="archive-candidate-row-"]')
    expect(rows.length).toBe(1)
    const link = container.querySelector('a[href="/ingestion/connectors/google_health/dead"]')
    expect(link).not.toBeNull()
  })

  it('does not fire the mutation immediately on click -- it is scheduled behind the undo window', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())
    expect(mutate).not.toHaveBeenCalled()
  })

  it('fires the mutation with the identity once the undo window elapses', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())
    act(() => vi.advanceTimersByTime(UNDO_WINDOW_MS))
    expect(mutate).toHaveBeenCalledWith({
      connectorType: 'google_health',
      endpointIdentity: 'dead',
    })
  })

  it('shows an Undo toast action immediately on click', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())

    expect(toast).toHaveBeenCalledWith(
      'Archiving dead',
      expect.objectContaining({
        action: expect.objectContaining({ label: 'Undo', onClick: expect.any(Function) }),
      }),
    )
  })

  it('cancels the archive entirely when Undo is clicked before the window elapses', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())

    const [, opts] = vi.mocked(toast).mock.calls[0]
    const action = (opts as unknown as { action: { onClick: () => void } }).action
    act(() => action.onClick())
    act(() => vi.advanceTimersByTime(UNDO_WINDOW_MS * 2))

    expect(mutate).not.toHaveBeenCalled()
  })

  it('shows an "archiving…" disabled state while scheduled, before the mutation even fires', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())

    expect(btn.textContent).toMatch(/archiving/)
    expect(btn.disabled).toBe(true)
  })

  it('shows an "archiving…" disabled state on the network-pending row', () => {
    hookState = {
      mutate,
      isPending: true,
      isError: false,
      variables: { connectorType: 'google_health', endpointIdentity: 'dead' },
    }
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    expect(btn.textContent).toMatch(/archiving/)
    expect(btn.disabled).toBe(true)
  })

  it('ignores a second click while an archive is already scheduled (no double-fire)', () => {
    render(root, [CANDIDATE])
    const btn = container.querySelector(
      '[data-testid="archive-candidate-action-google_health:dead"]',
    ) as HTMLButtonElement
    act(() => btn.click())
    act(() => btn.click())
    act(() => vi.advanceTimersByTime(UNDO_WINDOW_MS))

    expect(mutate).toHaveBeenCalledOnce()
  })

  it('surfaces an error message when the archive fails', () => {
    hookState = { mutate, isPending: false, isError: true, variables: undefined }
    render(root, [CANDIDATE])
    expect(
      container.querySelector('[data-testid="archive-candidates-error"]'),
    ).not.toBeNull()
  })
})
