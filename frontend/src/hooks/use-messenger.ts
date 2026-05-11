/**
 * TanStack Query hooks for the Messenger butler delivery health API.
 *
 * Four read-only hooks:
 *   useMessengerDeliveryStats  — delivery counts over a window (default 24h)
 *   useMessengerCircuitStatus  — per-channel circuit state (DB approximation)
 *   useMessengerQueueDepth     — pending/in-progress queue depth
 *   useMessengerDeadLetters    — recent dead-letter entries
 *
 * Circuit-status and queue-depth refresh every 15s (live operational data).
 * Delivery-stats and dead-letters refresh every 30s (aggregate / archive).
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

const STALE_TIME_AGGREGATE = 30_000; // 30s — delivery stats, dead letters
const STALE_TIME_LIVE = 15_000; // 15s — circuit status, queue depth

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
    refetchInterval: STALE_TIME_AGGREGATE,
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
 * Refreshes frequently as the queue changes in real time.
 */
export function useMessengerQueueDepth() {
  return useQuery({
    queryKey: ["messenger-queue-depth"],
    queryFn: () => getMessengerQueueDepth(),
    staleTime: STALE_TIME_LIVE,
    refetchInterval: STALE_TIME_LIVE,
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
