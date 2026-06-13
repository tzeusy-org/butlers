/**
 * URL-state helpers for the ingestion dispatch console routes.
 *
 * Provides read/write access to ingestion-specific query parameters without
 * coupling route components to raw URLSearchParams manipulation. The helpers
 * use the existing React Router useSearchParams hook as the underlying
 * transport — same pattern used elsewhere in the dashboard.
 *
 * Supported query parameters:
 * - range: 'live' | '1h' | '24h' | '7d' | 'custom' — timeline time window
 * - channels: comma-separated channel filter (e.g. 'email,telegram')
 * - statuses: comma-separated status filter (e.g. 'ingested,replay_pending')
 * - view: saved view name ('all' | 'errors' | 'priority' | 'spend')
 * - event: open event drawer by event ID
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline URL opens an event drawer"
 * Reference: docs/redesigns/ingestion-handoff.md §4c "URL state"
 */

import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type IngestionRange = 'live' | '1h' | '24h' | '7d' | 'custom'
export type IngestionView = 'all' | 'errors' | 'priority' | 'spend'
export type IngestionStatus = 'ingested' | 'skipped' | 'filtered' | 'error' | 'replay_pending' | 'replay_complete' | 'replay_failed'

export interface IngestionUrlState {
  /** Active time range. Defaults to '24h' when absent. */
  range: IngestionRange
  /** Active channel filter — empty array means "all channels". */
  channels: string[]
  /** Active status filter — empty array means "all statuses". */
  statuses: IngestionStatus[]
  /** Active saved view. Defaults to 'all' when absent. */
  view: IngestionView
  /** Open event drawer ID. Null when no drawer is open. */
  event: string | null
}

// ---------------------------------------------------------------------------
// Parser helpers
// ---------------------------------------------------------------------------

const VALID_RANGES: IngestionRange[] = ['live', '1h', '24h', '7d', 'custom']
const VALID_VIEWS: IngestionView[] = ['all', 'errors', 'priority', 'spend']
const VALID_STATUSES: IngestionStatus[] = [
  'ingested',
  'skipped',
  'filtered',
  'error',
  'replay_pending',
  'replay_complete',
  'replay_failed',
]

function parseRange(raw: string | null): IngestionRange {
  if (raw && (VALID_RANGES as string[]).includes(raw)) {
    return raw as IngestionRange
  }
  return '24h'
}

function parseView(raw: string | null): IngestionView {
  if (raw && (VALID_VIEWS as string[]).includes(raw)) {
    return raw as IngestionView
  }
  return 'all'
}

function parseChannels(raw: string | null): string[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean)
}

function parseStatuses(raw: string | null): IngestionStatus[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s): s is IngestionStatus => (VALID_STATUSES as string[]).includes(s))
}

// ---------------------------------------------------------------------------
// Serialiser helpers (round-trip)
// ---------------------------------------------------------------------------

export function serializeIngestionState(state: Partial<IngestionUrlState>): URLSearchParams {
  const params = new URLSearchParams()

  if (state.range && state.range !== '24h') {
    params.set('range', state.range)
  }
  if (state.channels && state.channels.length > 0) {
    params.set('channels', state.channels.join(','))
  }
  if (state.statuses && state.statuses.length > 0) {
    params.set('statuses', state.statuses.join(','))
  }
  if (state.view && state.view !== 'all') {
    params.set('view', state.view)
  }
  if (state.event) {
    params.set('event', state.event)
  }

  return params
}

export function parseIngestionState(searchParams: URLSearchParams): IngestionUrlState {
  return {
    range: parseRange(searchParams.get('range')),
    channels: parseChannels(searchParams.get('channels')),
    statuses: parseStatuses(searchParams.get('statuses')),
    view: parseView(searchParams.get('view')),
    event: searchParams.get('event'),
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Read and write ingestion dispatch console URL state.
 *
 * Returns the parsed state and a setter that merges partial updates into the
 * current query string, preserving unrelated params (e.g. range stays when
 * only channels change).
 *
 * Usage:
 *   const { state, setState } = useIngestionUrlState()
 *   setState({ range: '7d' })  // only updates the range param
 */
export function useIngestionUrlState() {
  const [searchParams, setSearchParams] = useSearchParams()

  const state = parseIngestionState(searchParams)

  const setState = useCallback(
    (partial: Partial<IngestionUrlState>, options?: { replace?: boolean }) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          const merged = { ...parseIngestionState(prev), ...partial }
          const serialized = serializeIngestionState(merged)

          // Apply the serialized values, clearing keys that were reset to default
          const knownKeys: Array<keyof IngestionUrlState> = ['range', 'channels', 'statuses', 'view', 'event']
          const keyMap: Record<keyof IngestionUrlState, string> = {
            range: 'range',
            channels: 'channels',
            statuses: 'statuses',
            view: 'view',
            event: 'event',
          }
          for (const stateKey of knownKeys) {
            const urlKey = keyMap[stateKey]
            if (serialized.has(urlKey)) {
              next.set(urlKey, serialized.get(urlKey)!)
            } else {
              next.delete(urlKey)
            }
          }

          return next
        },
        { replace: options?.replace ?? false },
      )
    },
    [setSearchParams],
  )

  return { state, setState }
}
