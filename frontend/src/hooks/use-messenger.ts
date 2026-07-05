/**
 * TanStack Query hooks for the Messenger butler delivery health API.
 *
 * Four read-only hooks:
 *   useMessengerDeliveryStats  — delivery counts over a window (default 24h)
 *   useMessengerCircuitStatus  — per-channel circuit state (DB approximation)
 *   useMessengerQueueDepth     — pending/in-progress queue depth
 *   useMessengerDeadLetters    — recent dead-letter entries
 *
 * Circuit-status refreshes every 15s (live operational data, not bus-covered).
 * Dead-letters refreshes every 30s (archive, not bus-covered).
 * Delivery-stats and queue-depth are bus-covered (bu-qvnce.14 slice 3):
 * event-cache-registry.ts's notificationPatch invalidates both keys on every
 * "notification" bus event, so their refetchInterval is now a 5-minute
 * reconciliation sweep (POLL_BUS_RECONCILE_MS), not the primary update path.
 *
 * bead: bu-iuol4.34
 */

import { useQuery } from "@tanstack/react-query";

import {
  getMessengerCircuitStatus,
  getMessengerDeadLetters,
  getMessengerDeliveryStats,
  getMessengerQueueDepth,
} from "@/api/index.ts";
import type {
  MessengerDeadLettersParams,
  MessengerDeliveryStatsParams,
} from "@/api/index.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

const STALE_TIME_AGGREGATE = 30_000; // 30s — dead letters (not bus-covered)
const STALE_TIME_LIVE = 15_000; // 15s — circuit status (not bus-covered)

// ---------------------------------------------------------------------------
// useMessengerDeliveryStats
// ---------------------------------------------------------------------------

/**
 * Fetch aggregated delivery statistics over a configurable window.
 * Defaults to the last 24 hours.
 */
export function useMessengerDeliveryStats(params?: MessengerDeliveryStatsParams) {
  return useQuery({
    queryKey: ["messenger-delivery-stats", params],
    queryFn: () => getMessengerDeliveryStats(params),
    staleTime: STALE_TIME_AGGREGATE,
    refetchInterval: POLL_BUS_RECONCILE_MS,
  });
}

// ---------------------------------------------------------------------------
// useMessengerCircuitStatus
// ---------------------------------------------------------------------------

/**
 * Fetch per-channel circuit breaker state.
 *
 * The response always carries `source: "db_approximation"` — the live
 * in-memory CircuitBreaker state is not persisted to the DB. Callers should
 * surface a note when `source === "db_approximation"`.
 */
export function useMessengerCircuitStatus() {
  return useQuery({
    queryKey: ["messenger-circuit-status"],
    queryFn: () => getMessengerCircuitStatus(),
    staleTime: STALE_TIME_LIVE,
    refetchInterval: STALE_TIME_LIVE,
  });
}

// ---------------------------------------------------------------------------
// useMessengerQueueDepth
// ---------------------------------------------------------------------------

/**
 * Fetch outbound queue depth by channel and priority.
 *
 * Bus-covered (bu-qvnce.14 slice 3) -- see the file-header comment.
 */
export function useMessengerQueueDepth() {
  return useQuery({
    queryKey: ["messenger-queue-depth"],
    queryFn: () => getMessengerQueueDepth(),
    staleTime: STALE_TIME_LIVE,
    refetchInterval: POLL_BUS_RECONCILE_MS,
  });
}

// ---------------------------------------------------------------------------
// useMessengerDeadLetters
// ---------------------------------------------------------------------------

/**
 * Fetch recent dead-letter entries.
 * Defaults to returning up to 20 entries (limit is configurable).
 */
export function useMessengerDeadLetters(params?: MessengerDeadLettersParams) {
  return useQuery({
    queryKey: ["messenger-dead-letters", params],
    queryFn: () => getMessengerDeadLetters(params),
    staleTime: STALE_TIME_AGGREGATE,
    refetchInterval: STALE_TIME_AGGREGATE,
  });
}
