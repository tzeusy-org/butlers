// ---------------------------------------------------------------------------
// useButlerStatusBoard — composite hook for the /butlers/ status-board page
// (bu-hb7dh.5)
//
// Joins six data sources:
//   useButlers           → name, status, type, description, sessions_24h
//   useRegistry          → eligibility_state per butler
//   useButlerHeartbeats  → last_session_at, active_session_count, heartbeat_age_seconds
//   useSpendSummary      → cost today per butler
//   useQuery (sessions)  → raw session list for 24h hourly bucketing (stable key, rolling ISO since)
//   useQueries (runtime) → max_concurrent per butler via per-butler runtime-config queries
//
// Partial-failure tolerance: if any non-butlers source errors, rows are still
// emitted with explicit fallback values. Only the butlers list error propagates
// to aggregates.isError.
// ---------------------------------------------------------------------------

import { useMemo } from "react"
import { useQueries } from "@tanstack/react-query"

import { getRuntimeConfig, getButlerHourlyActivity } from "@/api/index.ts"
import { useButlers } from "@/hooks/use-butlers"
import { useRegistry } from "@/hooks/use-general"
import { useButlerHeartbeats } from "@/hooks/use-system"
import { useSpendSummary } from "@/hooks/use-spend"

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type ActivityVerb = "running" | "idle" | "offline" | "quarantined"
export type CellTone = "neutral" | "green" | "amber" | "red"
export type EligibilityState = "active" | "quarantined" | "stale" | "unavailable"

/** Per-butler row for the status board table. */
export interface StatusBoardRow {
  name: string
  /** Agent type: "butler" (user-facing) or "staffer" (infrastructure). */
  type: "butler" | "staffer"
  description: string | null
  /** Raw API status string (e.g. "healthy", "degraded", "waiting"). */
  status: string
  /** Derived activity state — see derivation rules in the issue spec. */
  activity: ActivityVerb
  /** Visual tone for the status rail cell. */
  cellTone: CellTone
  /** Derived eligibility from the switchboard registry. */
  eligibility: EligibilityState
  /** Sessions started in the last 24 hours (from the butlers list). */
  sessions24h: number
  /** Cost in USD today; null when data is unavailable or all sessions are free/unpriced. */
  costToday: number | null
  /** active_session_count / max_concurrent * 100, rounded; null when max_concurrent or heartbeat unavailable. */
  loadPct: number | null
  /** ISO timestamp of the last session; null when no session or heartbeat unavailable. */
  lastRunISO: string | null
  /** 24 hourly session counts, oldest first (slot 0 = oldest). */
  hourlyStripe: number[]
  /** Sum of hourlyStripe buckets — shown as the SESS·24H KPI (agrees with stripe total). */
  hourlyTotal: number
  /** True while the per-butler hourly-activity endpoint is loading. */
  hourlyStripeLoading: boolean
  /** True when the per-butler hourly-activity endpoint has errored. */
  hourlyStripeError: boolean
  /** True when the backend reported schema_unreachable for this butler's heartbeat entry. */
  schemaUnreachable: boolean
  /** True when heartbeat data is unavailable for this butler (fleet-wide error or per-entry schema_unreachable). */
  heartbeatUnavailable: boolean
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
  totalSessions24h: number
  totalSpendToday: number
  /** Mean loadPct across rows that have a known load; null when no row has a known load. */
  avgLoadPct: number | null
  /** True only while the primary butlers list is loading and has no cached data. */
  isLoading: boolean
  /** True only when the butlers list itself has errored and no cached data exists. */
  isError: boolean
  error: Error | null
  refetch: () => void
  /** True when the heartbeat source has errored fleet-wide. */
  heartbeatSourceError: boolean
  /** True when the registry source has errored fleet-wide. */
  registrySourceError: boolean
  /** Count of butlers with eligibility='unavailable' (unregistered or registry source error). */
  eligibilityUnavailable: number
  /** True when at least one row has a per-entry schema_unreachable error from the backend. */
  hasPerEntryErrors: boolean
  /** True when any secondary source (heartbeat, registry, per-entry errors) has degraded. */
  sourcesPartiallyDegraded: boolean
}

/** Return value of useButlerStatusBoard. */
export interface StatusBoardResult {
  rows: StatusBoardRow[]
  aggregates: StatusBoardAggregates
}

// ---------------------------------------------------------------------------
// Activity verb derivation
// ---------------------------------------------------------------------------

/**
 * Derive the activity verb and cell tone for one butler row.
 *
 * Rules applied IN ORDER (first match wins):
 *   1. status === 'down'                        → offline / red
 *   2. eligibility === 'quarantined'            → quarantined / red
 *   3. active_session_count > 0                 → running / green
 *   4. else                                     → idle / neutral
 *
 * Backend _probe_butler emits only 'ok' and 'down'; no 'degraded' or 'waiting'
 * state exists in the current implementation.
 */
function deriveActivity(
  status: string,
  eligibility: EligibilityState,
  activeSessionCount: number,
): { activity: ActivityVerb; cellTone: CellTone } {
  if (status === "down") {
    return { activity: "offline", cellTone: "red" }
  }
  if (eligibility === "quarantined") {
    return { activity: "quarantined", cellTone: "red" }
  }
  if (activeSessionCount > 0) {
    return { activity: "running", cellTone: "green" }
  }
  return { activity: "idle", cellTone: "neutral" }
}

// ---------------------------------------------------------------------------
// loadPct helper
// ---------------------------------------------------------------------------

function deriveLoadPct(activeCount: number, maxConcurrent: number | null | undefined): number | null {
  if (maxConcurrent == null || maxConcurrent === 0) return null
  return Math.round((activeCount / maxConcurrent) * 100)
}

// ---------------------------------------------------------------------------
// Main hook
// ---------------------------------------------------------------------------

/**
 * Composite hook powering the /butlers/ status-board page.
 *
 * Returns derived rows sorted by sessions24h desc (name asc for ties) and
 * fleet-wide aggregates. Partial failures in secondary sources (cost, heartbeat,
 * sessions, registry, runtime-config) produce rows with fallback values; they
 * never suppress the row entirely.
 */
export function useButlerStatusBoard(): StatusBoardResult {
  const butlersQuery = useButlers()
  const registryQuery = useRegistry()
  const heartbeatsQuery = useButlerHeartbeats()
  const costQuery = useSpendSummary("today")

  // Stable reference: prevents `butlers` from appearing as a new array on every
  // render, which would otherwise trigger unnecessary useMemo re-runs.
  // useButlers returns ApiResponse<ButlerSummary[]>; unwrap with .data.
  const butlers = useMemo(() => butlersQuery.data?.data ?? [], [butlersQuery.data])

  // Per-butler runtime-config queries using useQueries so query count tracks
  // the live butler list without violating React's rules of hooks.
  const runtimeConfigResults = useQueries({
    queries: butlers.map((b) => ({
      queryKey: ["butlers", b.name, "runtime-config"],
      queryFn: () => getRuntimeConfig(b.name),
      // Tolerate failures — a failed config just means loadPct=null for that row.
      retry: 1,
    })),
  })

  // Per-butler hourly-activity queries so the stripe and SESS·24H KPI draw from
  // one authoritative server-side source (same SQL, same window).  This also
  // removes the getSessions limit:1000 silent-drop hazard that existed when the
  // fleet exceeded 1000 sessions/24h.
  const hourlyActivityResults = useQueries({
    queries: butlers.map((b) => ({
      queryKey: ["butlers", b.name, "analytics", "hourly-activity", 24],
      queryFn: () => getButlerHourlyActivity(b.name, { window_hours: 24 }),
      staleTime: 60_000,
      refetchInterval: 60_000,
      retry: 1,
    })),
  })

  // Build a stable name→max_concurrent map from the runtime config results.
  const runtimeConfigMap = useMemo(() => {
    const map: Record<string, number | null> = {}
    butlers.forEach((b, i) => {
      const result = runtimeConfigResults[i]
      const maxConcurrent = result?.data?.max_concurrent
      map[b.name] = maxConcurrent != null && maxConcurrent > 0 ? maxConcurrent : null
    })
    return map
  }, [butlers, runtimeConfigResults])

  const rows = useMemo(() => {
    if (butlers.length === 0) return []

    // Build lookup maps for secondary sources (all tolerate undefined/null)
    const registryMap: Record<string, string> = {}
    if (!registryQuery.isError && registryQuery.data?.data) {
      for (const entry of registryQuery.data.data) {
        registryMap[entry.name] = entry.eligibility_state
      }
    }

    const heartbeatMap: Record<
      string,
      { last_session_at: string | null; active_session_count: number; error: string | null }
    > = {}
    if (!heartbeatsQuery.isError && heartbeatsQuery.data?.data) {
      for (const hb of heartbeatsQuery.data.data.butlers) {
        heartbeatMap[hb.name] = {
          last_session_at: hb.last_session_at,
          active_session_count: hb.active_session_count,
          error: hb.error ?? null,
        }
      }
    }

    const byButlerCost: Record<string, number> =
      !costQuery.isError && costQuery.data?.data ? costQuery.data.data.by_butler : {}

    const derived: StatusBoardRow[] = butlers.map((butler, butlerIndex) => {
      // --- eligibility ---
      const rawEligibility = registryMap[butler.name]
      let eligibility: EligibilityState
      if (rawEligibility === "active") {
        eligibility = "active"
      } else if (rawEligibility === "quarantined") {
        eligibility = "quarantined"
      } else if (rawEligibility === "stale") {
        eligibility = "stale"
      } else {
        eligibility = "unavailable"
      }

      // --- heartbeat data ---
      const hb = heartbeatMap[butler.name]
      // Consume the backend per-entry error field: schema_unreachable means this
      // butler's session DB was unreachable when the heartbeat endpoint ran.
      const schemaUnreachable = hb?.error === "schema_unreachable"
      // heartbeatUnavailable is true for fleet-wide source failure, pending load, OR per-entry error.
      const heartbeatUnavailable = heartbeatsQuery.isError || heartbeatsQuery.isPending || schemaUnreachable
      // Only use active_session_count when the heartbeat data is reliable.
      const activeSessionCount = heartbeatUnavailable ? 0 : (hb?.active_session_count ?? 0)
      const lastRunISO = hb?.last_session_at ?? null

      // --- activity verb ---
      const { activity, cellTone } = deriveActivity(butler.status, eligibility, activeSessionCount)

      // --- load pct ---
      // null when heartbeat is unavailable so the LOAD KPI shows '—' not '0%'.
      const maxConcurrent = runtimeConfigMap[butler.name] ?? null
      const loadPct = heartbeatUnavailable ? null : deriveLoadPct(activeSessionCount, maxConcurrent)

      // --- cost ---
      const costToday = byButlerCost[butler.name] ?? null

      // --- hourly stripe (from server hourly-activity endpoint) ---
      // The stripe and SESS·24H KPI both derive from the same per-butler
      // server query so they always display the same window.
      // API returns buckets newest-first (hour_index 0 = current hour);
      // convert to oldest-first (slot 0 = oldest) for ActivityStripe.
      const hourlyResult = hourlyActivityResults[butlerIndex]
      const buckets = hourlyResult?.data?.data?.buckets ?? []
      const hourlyStripe = new Array<number>(24).fill(0)
      for (const bucket of buckets) {
        const slot = 23 - bucket.hour_index
        if (slot >= 0 && slot < 24) hourlyStripe[slot] = bucket.sessions_count
      }
      const hourlyTotal = hourlyStripe.reduce((s, n) => s + n, 0)
      const hourlyStripeLoading = hourlyResult?.isLoading ?? false
      const hourlyStripeError = !hourlyResult?.isLoading && (hourlyResult?.isError ?? false)

      return {
        name: butler.name,
        type: butler.type,
        description: butler.description ?? null,
        status: butler.status,
        activity,
        cellTone,
        eligibility,
        sessions24h: butler.sessions_24h,
        costToday,
        loadPct,
        lastRunISO,
        hourlyStripe,
        hourlyTotal,
        hourlyStripeLoading,
        hourlyStripeError,
        schemaUnreachable,
        heartbeatUnavailable,
      }
    })

    // Sort: sessions24h desc, name asc for ties
    derived.sort((a, b) => {
      if (b.sessions24h !== a.sessions24h) return b.sessions24h - a.sessions24h
      return a.name.localeCompare(b.name)
    })

    return derived
  }, [
    butlers,
    registryQuery,
    heartbeatsQuery,
    costQuery,
    runtimeConfigMap,
    hourlyActivityResults,
  ])

  const aggregates = useMemo<StatusBoardAggregates>(() => {
    const total = rows.length
    const butlerCount = rows.filter((r) => r.type === "butler").length
    const stafferCount = rows.filter((r) => r.type === "staffer").length
    const active = rows.filter((r) => r.activity === "running").length
    const offline = rows.filter((r) => r.activity === "offline").length
    const quarantined = rows.filter((r) => r.activity === "quarantined").length
    const totalSessions24h = rows.reduce((sum, r) => sum + r.sessions24h, 0)
    const totalSpendToday = rows.reduce((sum, r) => sum + (r.costToday ?? 0), 0)

    const knownLoadRows = rows.filter((r) => r.loadPct !== null)
    const avgLoadPct =
      knownLoadRows.length > 0
        ? Math.round(
            knownLoadRows.reduce((sum, r) => sum + (r.loadPct as number), 0) /
              knownLoadRows.length,
          )
        : null

    const heartbeatSourceError = heartbeatsQuery.isError
    const registrySourceError = registryQuery.isError
    const eligibilityUnavailable = rows.filter((r) => r.eligibility === "unavailable").length
    const hasPerEntryErrors = rows.some((r) => r.schemaUnreachable)
    const sourcesPartiallyDegraded = heartbeatSourceError || registrySourceError || hasPerEntryErrors

    return {
      total,
      butlerCount,
      stafferCount,
      active,
      offline,
      quarantined,
      totalSessions24h,
      totalSpendToday,
      avgLoadPct,
      isLoading: butlersQuery.isLoading && !butlersQuery.data,
      isError: butlersQuery.isError && !butlersQuery.data,
      error: butlersQuery.error ?? null,
      refetch: butlersQuery.refetch,
      heartbeatSourceError,
      registrySourceError,
      eligibilityUnavailable,
      hasPerEntryErrors,
      sourcesPartiallyDegraded,
    }
  }, [rows, butlersQuery.isLoading, butlersQuery.data, butlersQuery.isError, butlersQuery.error, butlersQuery.refetch,
      heartbeatsQuery.isError, registryQuery.isError])

  return { rows, aggregates }
}
