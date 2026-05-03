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
