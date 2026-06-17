/**
 * TanStack Query hooks for the /api/system/* endpoints.
 *
 * Each hook maps to one read-only system endpoint. The egress hook handles
 * HTTP 403 gracefully — a 403 means the caller is not the owner; the hook
 * returns `isForbidden: true` instead of propagating the error.
 */

import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  getBackupFacts,
  getButlerHeartbeats,
  getDatabaseFacts,
  getEgressCatalog,
  getHealth,
  getInsightDeliveryState,
  getInstanceFacts,
} from "@/api/index.ts";

/** Fetch software version, process uptime, and start timestamp. */
export function useInstanceFacts() {
  return useQuery({
    queryKey: ["system-instance"],
    queryFn: () => getInstanceFacts(),
    refetchInterval: 60_000,
  });
}

/** Fetch PostgreSQL catalog size facts: total size, per-schema breakdown, largest tables. */
export function useDatabaseFacts() {
  return useQuery({
    queryKey: ["system-database"],
    queryFn: () => getDatabaseFacts(),
    refetchInterval: 60_000,
  });
}

/** Fetch backup recency and source reachability. Always HTTP 200; degrades gracefully. */
export function useBackupFacts() {
  return useQuery({
    queryKey: ["system-backups"],
    queryFn: () => getBackupFacts(),
    refetchInterval: 120_000,
  });
}

/**
 * Fetch data-egress catalog (owner-only).
 *
 * Returns an extra `isForbidden` flag when the server responds with HTTP 403.
 * Components should render an "owner only" indicator rather than an error state
 * when `isForbidden` is true.
 */
export function useEgressFacts() {
  const query = useQuery({
    queryKey: ["system-egress"],
    queryFn: () => getEgressCatalog(),
    retry: (failureCount, error) => {
      // Never retry a 403 — it is a deliberate access-control response, not a transient failure.
      if (error instanceof ApiError && error.status === 403) return false;
      return failureCount < 3;
    },
  });

  const isForbidden =
    query.error instanceof ApiError && query.error.status === 403;

  return { ...query, isForbidden };
}

/** Fetch per-butler liveness registry snapshots and session facts. */
export function useButlerHeartbeats() {
  return useQuery({
    queryKey: ["system-butler-heartbeats"],
    queryFn: () => getButlerHeartbeats(),
    refetchInterval: 30_000,
  });
}

/**
 * Fetch the dashboard security-posture booleans from GET /api/health.
 *
 * Returns `api_key_auth_enabled` and `export_secret_insecure_default`.
 * Values are booleans only — never secret material.  The endpoint is
 * public (no X-API-Key required) so this hook always works.
 */
export function useHealthPosture() {
  return useQuery({
    queryKey: ["system-health-posture"],
    queryFn: () => getHealth(),
    // Posture is static across a process lifetime; check infrequently.
    refetchInterval: 120_000,
  });
}

/**
 * Fetch the current state of the proactive insight delivery pipeline.
 *
 * Returns queued / delivered / failed counts and the last-delivery timestamp
 * from GET /api/system/insights/delivery-state.  The endpoint degrades
 * gracefully: all-zero counts with null last_delivery_at is an honest empty
 * state (no delivery activity yet), not an error.
 */
export function useInsightDeliveryState() {
  return useQuery({
    queryKey: ["system-insight-delivery"],
    queryFn: () => getInsightDeliveryState(),
    refetchInterval: 60_000,
  });
}
