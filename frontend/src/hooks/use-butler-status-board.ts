// ---------------------------------------------------------------------------
// useButlerStatusBoard — composite hook for the /butlers/ status-board page
// (bu-hb7dh.5, consolidated onto GET /api/butlers/board in bu-86c4c.17)
//
// Single-request adapter: fetches the consolidated board payload (rows +
// aggregates, joined server-side against the scheduler's cron expectations
// and probed via MCP) and maps its snake_case wire shape onto the camelCase
// StatusBoardRow/StatusBoardAggregates contract the board UI already
// consumes. All derivation (canonical liveness verdict, cron-expectation
// cadence status, load%, cost) happens server-side now -- see
// GET /api/butlers/board (src/butlers/api/routers/butlers.py) for the rules.
//
// Row order is exactly the server's roster order: never re-sorted
// client-side, so the grid never reshuffles position on poll.
// ---------------------------------------------------------------------------

import { useMemo } from "react"

import { useButlersBoard } from "@/hooks/use-butlers.ts"
import type { BoardRow } from "@/api/types"

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type ActivityVerb = "running" | "idle" | "overdue" | "offline" | "quarantined" | "unknown"
export type CellTone = "neutral" | "green" | "amber" | "red"
export type EligibilityState = "active" | "quarantined" | "stale" | "unavailable"
export type CadenceStatus = "on_schedule" | "overdue" | "unknown"
export type CadenceLabel = "hourly" | "daily" | "weekly" | "custom" | null

/** Per-butler row for the status board table. */
export interface StatusBoardRow {
  name: string
  /** Agent type: "butler" (user-facing) or "staffer" (infrastructure). */
  type: "butler" | "staffer"
  description: string | null
  /** Raw API status string: 'ok' (healthy) or 'down'. Backend emits no other values. */
  status: string
  /** Canonical liveness verb -- computed server-side, shared by every consumer. */
  activity: ActivityVerb
  /** Visual tone for the status rail cell. */
  cellTone: CellTone
  /** Derived eligibility from the switchboard registry. */
  eligibility: EligibilityState
  /** Why this butler is quarantined, when eligibility === 'quarantined'. */
  quarantineReason: string | null
  quarantinedAt: string | null
  /** Sessions started in the last 24 hours (from the butlers list). */
  sessions24h: number
  /** Cost in USD today; null when data is unavailable or all sessions are free/unpriced. */
  costToday: number | null
  /** active_session_count / max_concurrent * 100, rounded; null when max_concurrent or heartbeat unavailable. */
  loadPct: number | null
  /** 0 whenever heartbeatUnavailable is true -- never a stale confident count during an outage. */
  activeSessionCount: number
  /** ISO timestamp of the last session; null when no session or heartbeat unavailable. */
  lastRunISO: string | null
  /** ISO timestamp of the last registry heartbeat; null when never seen. */
  lastHeartbeatISO: string | null
  /** Seconds since the last registry heartbeat; null when never seen. */
  heartbeatAgeSeconds: number | null
  /** 24 hourly session counts, oldest first (slot 0 = oldest). */
  hourlyStripe: number[]
  /** Sum of hourlyStripe buckets — shown as the SESS·24H KPI (agrees with stripe total). */
  hourlyTotal: number
  /** Always false: the board loads in one round trip, no per-row stagger. */
  hourlyStripeLoading: boolean
  /** True when this butler's own schema was unreachable when the board was computed. */
  hourlyStripeError: boolean
  /** True when the backend reported schema_unreachable for this butler's heartbeat entry. */
  schemaUnreachable: boolean
  /** True when heartbeat data is unavailable for this butler (fleet-wide error or per-entry schema_unreachable). */
  heartbeatUnavailable: boolean
  /** Shortest interval (seconds) across this butler's own enabled cron schedules; null when none. */
  cadenceSeconds: number | null
  /** Human bucket for cadenceSeconds ("hourly" | "daily" | "weekly" | "custom"); null when no schedule. */
  cadenceLabel: CadenceLabel
  /** Seconds since the butler's last known activity (session or heartbeat); null when never seen. */
  silenceSeconds: number | null
  /** "overdue" when silence exceeds the butler's own cadence expectation (or 5 min with no cadence). */
  cadenceStatus: CadenceStatus
}

/** Fleet-wide aggregates and loading state for the status board. */
export interface StatusBoardAggregates {
  total: number
  butlerCount: number
  stafferCount: number
  /** Butlers whose activity is "running". */
  active: number
  /** Butlers whose activity is "offline" (status === 'down'). */
  offline: number
  /** Butlers whose activity is "quarantined". */
  quarantined: number
  /** Butlers silent longer than their own cron cadence expects. */
  overdue: number
  /** Butlers whose canonical server-derived liveness verdict is "unknown". */
  unknown: number
  totalSessions24h: number
  totalSpendToday: number
  /** Mean loadPct across rows that have a known load; null when no row has a known load. */
  avgLoadPct: number | null
  /** True only while the board query is loading and has no cached data. */
  isLoading: boolean
  /** True only when the board query has errored and no cached data exists. */
  isError: boolean
  error: Error | null
  refetch: () => void
  /** True when the heartbeat/registry source has errored fleet-wide. */
  heartbeatSourceError: boolean
  /** True when the registry source has errored fleet-wide. */
  registrySourceError: boolean
  /** Count of butlers with eligibility='unavailable' (unregistered or registry source error). */
  eligibilityUnavailable: number
  /** True when at least one row has a per-entry schema_unreachable error from the backend. */
  hasPerEntryErrors: boolean
  /**
   * True when the cost source has errored for at least one butler.
   * When true, `totalSpendToday` is a partial sum over rows with known cost, NOT
   * a confident fleet-wide total -- it must never be shown as a bare "$0.00".
   */
  costSourceError: boolean
  /**
   * True when at least one butler's hourly-activity query has errored.
   * When true, `totalSessions24h` is a partial sum over rows with a known
   * stripe, NOT a confident fleet-wide total.
   */
  sessionsSourceError: boolean
  /** True when any secondary source (heartbeat, registry, cost, sessions, per-entry errors) has degraded. */
  sourcesPartiallyDegraded: boolean
}

/** Return value of useButlerStatusBoard. */
export interface StatusBoardResult {
  rows: StatusBoardRow[]
  aggregates: StatusBoardAggregates
  /**
   * Rows that need the owner's attention (offline, quarantined, or overdue),
   * in the same stable roster order as `rows`. Renders as the board's
   * needs-you strip; empty when the fleet is fully healthy.
   */
  needsYou: StatusBoardRow[]
}

// ---------------------------------------------------------------------------
// Row mapping
// ---------------------------------------------------------------------------

function mapRow(row: BoardRow): StatusBoardRow {
  return {
    name: row.name,
    type: row.type,
    description: row.description,
    status: row.status,
    activity: row.activity,
    cellTone: row.cell_tone,
    eligibility: row.eligibility,
    quarantineReason: row.quarantine_reason,
    quarantinedAt: row.quarantined_at,
    sessions24h: row.sessions_24h,
    costToday: row.cost_today,
    loadPct: row.load_pct,
    activeSessionCount: row.active_session_count,
    lastRunISO: row.last_session_at,
    lastHeartbeatISO: row.last_heartbeat_at,
    heartbeatAgeSeconds: row.heartbeat_age_seconds,
    hourlyStripe: row.hourly_stripe,
    hourlyTotal: row.hourly_total,
    hourlyStripeLoading: false,
    hourlyStripeError: row.schema_unreachable || (row.stripe_source_error ?? false),
    schemaUnreachable: row.schema_unreachable,
    heartbeatUnavailable: row.heartbeat_unavailable,
    cadenceSeconds: row.cadence_seconds,
    cadenceLabel: row.cadence_label,
    silenceSeconds: row.silence_seconds,
    cadenceStatus: row.cadence_status,
  }
}

// "unknown" (heartbeat_unavailable) belongs here too -- an unknowable
// liveness must never be folded into a calm "All N healthy" line (bu-qvnce.1).
//
// Exported so every consumer of the canonical board verdict -- this page's
// needsYou strip AND the Overview's KPI/attention-list derivation
// (components/overview/model.ts, bu-qvnce.4) -- classifies "needs attention"
// from the exact same set. Two independently-maintained copies of this list
// is exactly the "one instrument built by one hand" defect this move fixes.
export const NEEDS_YOU_ACTIVITIES: ReadonlySet<ActivityVerb> = new Set([
  "offline",
  "quarantined",
  "overdue",
  "unknown",
])

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------

/**
 * Composite hook powering the /butlers/ status-board page.
 *
 * Fetches GET /api/butlers/board in a single round trip and maps it onto the
 * board UI's existing camelCase contract. Row order is the server's stable
 * roster order -- never re-sorted here.
 */
export function useButlerStatusBoard(): StatusBoardResult {
  // Same query useButlersBoard() exposes to the Overview page (bu-qvnce.4) --
  // one query definition, not two independently-maintained copies of the
  // same queryKey/queryFn/refetchInterval.
  const boardQuery = useButlersBoard()

  const rows = useMemo(() => {
    const data = boardQuery.data?.data
    if (!data) return []
    return data.rows.map(mapRow)
  }, [boardQuery.data])

  const needsYou = useMemo(
    () => rows.filter((r) => NEEDS_YOU_ACTIVITIES.has(r.activity)),
    [rows],
  )

  const aggregates = useMemo<StatusBoardAggregates>(() => {
    const agg = boardQuery.data?.data.aggregates
    const eligibilityUnavailable = rows.filter((r) => r.eligibility === "unavailable").length
    const unknown = rows.filter((r) => r.activity === "unknown").length
    const hasPerEntryErrors = agg?.has_per_entry_errors ?? false

    return {
      total: agg?.total ?? 0,
      butlerCount: agg?.butler_count ?? 0,
      stafferCount: agg?.staffer_count ?? 0,
      active: agg?.active ?? 0,
      offline: agg?.offline ?? 0,
      quarantined: agg?.quarantined ?? 0,
      overdue: agg?.overdue ?? 0,
      unknown,
      totalSessions24h: agg?.total_sessions_24h ?? 0,
      totalSpendToday: agg?.total_spend_today ?? 0,
      avgLoadPct: agg?.avg_load_pct ?? null,
      isLoading: boardQuery.isLoading && !boardQuery.data,
      isError: boardQuery.isError && !boardQuery.data,
      error: boardQuery.error ?? null,
      refetch: boardQuery.refetch,
      heartbeatSourceError: agg?.heartbeat_source_error ?? false,
      registrySourceError: agg?.registry_source_error ?? false,
      eligibilityUnavailable,
      hasPerEntryErrors,
      costSourceError: agg?.cost_source_error ?? false,
      sessionsSourceError: agg?.sessions_source_error ?? false,
      sourcesPartiallyDegraded: agg?.sources_partially_degraded ?? false,
    }
  }, [
    boardQuery.data,
    boardQuery.isLoading,
    boardQuery.isError,
    boardQuery.error,
    boardQuery.refetch,
    rows,
  ])

  return { rows, aggregates, needsYou }
}
