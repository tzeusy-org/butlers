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
import { useQueries, useQuery } from "@tanstack/react-query"

import { getRuntimeConfig, getSessions } from "@/api/index.ts"
import { useButlers } from "@/hooks/use-butlers"
import { useRegistry } from "@/hooks/use-general"
import { useButlerHeartbeats } from "@/hooks/use-system"
import { useSpendSummary } from "@/hooks/use-spend"
import { bucketSessionsByHour } from "@/lib/session-buckets"
import { OWNER_TZ_DEFAULT } from "@/hooks/use-time-window"

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type ActivityVerb = "running" | "idle" | "paused" | "awaiting" | "quarantined"
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
  /** active_session_count / max_concurrent * 100, rounded; null when max_concurrent unknown. */
  loadPct: number | null
  /** ISO timestamp of the last session; null when no session or heartbeat unavailable. */
  lastRunISO: string | null
  /** 24 hourly session counts, oldest first (slot 0 = oldest). */
  hourlyStripe: number[]
}

/** Fleet-wide aggregates and loading state for the status board. */
export interface StatusBoardAggregates {
  total: number
  butlerCount: number
  stafferCount: number
  /** Butlers whose activity is "running". */
  active: number
  /** Butlers whose activity is "paused". */
  paused: number
  /** Butlers whose activity is "awaiting" (excludes quarantined). */
  awaiting: number
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
 *   1. status === 'degraded'                    → paused / red
 *   2. status === 'waiting'
 *      OR eligibility === 'quarantined'          → quarantined (red) or awaiting (amber)
 *   3. active_session_count > 0                 → running / green
 *   4. else                                     → idle / neutral
 */
function deriveActivity(
  status: string,
  eligibility: EligibilityState,
  activeSessionCount: number,
): { activity: ActivityVerb; cellTone: CellTone } {
  if (status === "degraded") {
    return { activity: "paused", cellTone: "red" }
  }
  if (status === "waiting" || eligibility === "quarantined") {
    if (eligibility === "quarantined") {
      return { activity: "quarantined", cellTone: "red" }
    }
    return { activity: "awaiting", cellTone: "amber" }
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

  // Fetch sessions for the past 24h for hourly bucketing.
  // The query key is stable ("sessions-24h") while queryFn recomputes the ISO
  // since timestamp on each refetch, so the rolling window advances without
  // polluting the query key or causing excess re-renders.
  const sessionsQuery = useQuery({
    queryKey: ["sessions-24h"],
    queryFn: () =>
      getSessions({
        since: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
        limit: 1000,
        offset: 0,
      }),
    refetchInterval: 60_000,
  })

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
      { last_session_at: string | null; active_session_count: number }
    > = {}
    if (!heartbeatsQuery.isError && heartbeatsQuery.data?.data) {
      for (const hb of heartbeatsQuery.data.data.butlers) {
        heartbeatMap[hb.name] = {
          last_session_at: hb.last_session_at,
          active_session_count: hb.active_session_count,
        }
      }
    }

    const byButlerCost: Record<string, number> =
      !costQuery.isError && costQuery.data?.data ? costQuery.data.data.by_butler : {}

    const sessionList = !sessionsQuery.isError && sessionsQuery.data ? sessionsQuery.data.data : []

    // Compute a single endAt for all bucketSessionsByHour calls so that all
    // per-butler hourly stripes use a consistent 24h window boundary.
    const stripeEndAt = new Date()

    const derived: StatusBoardRow[] = butlers.map((butler) => {
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
      const activeSessionCount = hb?.active_session_count ?? 0
      const lastRunISO = hb?.last_session_at ?? null

      // --- activity verb ---
      const { activity, cellTone } = deriveActivity(butler.status, eligibility, activeSessionCount)

      // --- load pct ---
      const maxConcurrent = runtimeConfigMap[butler.name] ?? null
      const loadPct = deriveLoadPct(activeSessionCount, maxConcurrent)

      // --- cost ---
      const costToday = byButlerCost[butler.name] ?? null

      // --- hourly stripe ---
      const hourlyStripe = bucketSessionsByHour(sessionList, butler.name, OWNER_TZ_DEFAULT, stripeEndAt)

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
    registryQuery.isError,
    registryQuery.data,
    heartbeatsQuery.isError,
    heartbeatsQuery.data,
    costQuery.isError,
    costQuery.data,
    sessionsQuery.isError,
    sessionsQuery.data,
    runtimeConfigMap,
  ])

  const aggregates = useMemo<StatusBoardAggregates>(() => {
    const total = rows.length
    const butlerCount = rows.filter((r) => r.type === "butler").length
    const stafferCount = rows.filter((r) => r.type === "staffer").length
    const active = rows.filter((r) => r.activity === "running").length
    const paused = rows.filter((r) => r.activity === "paused").length
    const awaiting = rows.filter((r) => r.activity === "awaiting").length
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

    return {
      total,
      butlerCount,
      stafferCount,
      active,
      paused,
      awaiting,
      quarantined,
      totalSessions24h,
      totalSpendToday,
      avgLoadPct,
      isLoading: butlersQuery.isLoading && !butlersQuery.data,
      isError: butlersQuery.isError && !butlersQuery.data,
      error: butlersQuery.error ?? null,
      refetch: butlersQuery.refetch,
    }
  }, [rows, butlersQuery.isLoading, butlersQuery.data, butlersQuery.isError, butlersQuery.error, butlersQuery.refetch])

  return { rows, aggregates }
}
