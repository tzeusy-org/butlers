/**
 * URL-state helpers for the /memory house-ledger page.
 *
 * Mirrors the use-ingestion-url-state pattern: read/write access to the
 * memory-page query parameters without coupling route components to raw
 * URLSearchParams manipulation. Backed by React Router's useSearchParams.
 *
 * Supported query parameters (pr/overview/memory-redesign/prompts/00-foundation.md):
 * - register: 'facts' | 'rules' | 'episodes' — focused register. Default 'facts'.
 * - q:        submitted search string (NOT keystroke). Absent by default.
 * - kind:     search scope when q is set: 'all' | 'fact' | 'rule' | 'episode'. Default 'all'.
 * - validity: ledger filter: 'active' | 'fading' | 'superseded' | 'expired' | 'retracted'. Default 'active'.
 * - maturity: rules filter: 'all' | 'candidate' | 'established' | 'proven' | 'anti_pattern'. Default 'all'.
 * - status:   daybook filter: 'pending' | 'consolidated' | 'dead_letter'. Absent by default (= all).
 * - offset:   pagination offset for the focused register. Default 0.
 *
 * Default-valued params are NOT written to the URL so deep-links stay short and
 * round-trip cleanly (e.g. an absent `validity` means `active`). The search
 * text input is local component state; only the submitted value writes `q`.
 */

import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MemoryRegister = 'facts' | 'rules' | 'episodes'
export type MemorySearchKind = 'all' | 'fact' | 'rule' | 'episode'
export type MemoryValidity = 'active' | 'fading' | 'superseded' | 'expired' | 'retracted'
/**
 * Rules-register maturity filter. `all` is the unfiltered default; the other
 * four are exactly the API's maturity vocabulary
 * (MEMORY_LANGUAGE.md §3b — lowercase, no aliasing).
 */
export type MemoryMaturity = 'all' | 'candidate' | 'established' | 'proven' | 'anti_pattern'
export type MemoryEpisodeStatus = 'pending' | 'consolidated' | 'dead_letter'

export interface MemoryUrlState {
  /** Focused register. Defaults to 'facts' when absent. */
  register: MemoryRegister
  /** Submitted search query. Null when no search is active. */
  q: string | null
  /** Search scope when q is set. Defaults to 'all' when absent. */
  kind: MemorySearchKind
  /** Ledger validity filter. Defaults to 'active' when absent. */
  validity: MemoryValidity
  /** Rules-register maturity filter. Defaults to 'all' when absent. */
  maturity: MemoryMaturity
  /** Daybook status filter. Null means "all statuses". */
  status: MemoryEpisodeStatus | null
  /** Pagination offset for the focused register. Defaults to 0 when absent. */
  offset: number
}

// ---------------------------------------------------------------------------
// Vocabulary + defaults
// ---------------------------------------------------------------------------

const VALID_REGISTERS: MemoryRegister[] = ['facts', 'rules', 'episodes']
const VALID_KINDS: MemorySearchKind[] = ['all', 'fact', 'rule', 'episode']
const VALID_VALIDITIES: MemoryValidity[] = [
  'active',
  'fading',
  'superseded',
  'expired',
  'retracted',
]
const VALID_MATURITIES: MemoryMaturity[] = [
  'all',
  'candidate',
  'established',
  'proven',
  'anti_pattern',
]
const VALID_STATUSES: MemoryEpisodeStatus[] = ['pending', 'consolidated', 'dead_letter']

const DEFAULT_REGISTER: MemoryRegister = 'facts'
const DEFAULT_KIND: MemorySearchKind = 'all'
const DEFAULT_VALIDITY: MemoryValidity = 'active'
const DEFAULT_MATURITY: MemoryMaturity = 'all'
const DEFAULT_OFFSET = 0

// ---------------------------------------------------------------------------
// Parser helpers
// ---------------------------------------------------------------------------

function parseRegister(raw: string | null): MemoryRegister {
  if (raw && (VALID_REGISTERS as string[]).includes(raw)) {
    return raw as MemoryRegister
  }
  return DEFAULT_REGISTER
}

function parseKind(raw: string | null): MemorySearchKind {
  if (raw && (VALID_KINDS as string[]).includes(raw)) {
    return raw as MemorySearchKind
  }
  return DEFAULT_KIND
}

function parseValidity(raw: string | null): MemoryValidity {
  if (raw && (VALID_VALIDITIES as string[]).includes(raw)) {
    return raw as MemoryValidity
  }
  return DEFAULT_VALIDITY
}

function parseMaturity(raw: string | null): MemoryMaturity {
  if (raw && (VALID_MATURITIES as string[]).includes(raw)) {
    return raw as MemoryMaturity
  }
  return DEFAULT_MATURITY
}

function parseStatus(raw: string | null): MemoryEpisodeStatus | null {
  if (raw && (VALID_STATUSES as string[]).includes(raw)) {
    return raw as MemoryEpisodeStatus
  }
  return null
}

function parseQuery(raw: string | null): string | null {
  if (raw == null) return null
  const trimmed = raw.trim()
  return trimmed.length > 0 ? trimmed : null
}

function parseOffset(raw: string | null): number {
  if (!raw) return DEFAULT_OFFSET
  const n = Number.parseInt(raw, 10)
  if (Number.isNaN(n) || n < 0) return DEFAULT_OFFSET
  return n
}

// ---------------------------------------------------------------------------
// Serialiser helper (round-trip; default-valued params omitted)
// ---------------------------------------------------------------------------

export function serializeMemoryState(state: Partial<MemoryUrlState>): URLSearchParams {
  const params = new URLSearchParams()

  if (state.register && state.register !== DEFAULT_REGISTER) {
    params.set('register', state.register)
  }
  if (state.q) {
    params.set('q', state.q)
  }
  // kind is only meaningful alongside a query; still, only write non-default.
  if (state.kind && state.kind !== DEFAULT_KIND) {
    params.set('kind', state.kind)
  }
  if (state.validity && state.validity !== DEFAULT_VALIDITY) {
    params.set('validity', state.validity)
  }
  if (state.maturity && state.maturity !== DEFAULT_MATURITY) {
    params.set('maturity', state.maturity)
  }
  if (state.status) {
    params.set('status', state.status)
  }
  if (typeof state.offset === 'number' && state.offset !== DEFAULT_OFFSET) {
    params.set('offset', String(state.offset))
  }

  return params
}

export function parseMemoryState(searchParams: URLSearchParams): MemoryUrlState {
  return {
    register: parseRegister(searchParams.get('register')),
    q: parseQuery(searchParams.get('q')),
    kind: parseKind(searchParams.get('kind')),
    validity: parseValidity(searchParams.get('validity')),
    maturity: parseMaturity(searchParams.get('maturity')),
    status: parseStatus(searchParams.get('status')),
    offset: parseOffset(searchParams.get('offset')),
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

const URL_KEYS = ['register', 'q', 'kind', 'validity', 'maturity', 'status', 'offset'] as const

/**
 * Read and write /memory URL state.
 *
 * Returns the parsed state and a setter that merges partial updates into the
 * current query string, omitting default-valued params so URLs stay short.
 * Unrelated params (anchors, future keys) are preserved.
 *
 * Usage:
 *   const { state, setState } = useMemoryUrlState()
 *   setState({ register: 'rules' })       // switch register
 *   setState({ q: 'sleep', offset: 0 })   // submit a search, reset paging
 */
export function useMemoryUrlState() {
  const [searchParams, setSearchParams] = useSearchParams()

  const state = parseMemoryState(searchParams)

  const setState = useCallback(
    (partial: Partial<MemoryUrlState>, options?: { replace?: boolean }) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          const merged = { ...parseMemoryState(prev), ...partial }
          const serialized = serializeMemoryState(merged)

          for (const key of URL_KEYS) {
            if (serialized.has(key)) {
              next.set(key, serialized.get(key)!)
            } else {
              next.delete(key)
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
