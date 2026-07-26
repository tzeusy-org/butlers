/**
 * TanStack Query hook for the butler logs endpoint.
 *
 * GET /api/butlers/{name}/logs?level=INFO&since=ISO&limit=100
 *
 * Returns `{ lines: [{ ts, level, msg, source, request_id, metadata }] }`.
 * `level` is a minimum-severity filter (WARN returns WARN + ERROR).
 *
 * Polls every 5 s while the tab is mounted. TanStack Query's built-in
 * window-focus pause prevents background polling when the document is hidden.
 */

import { useQuery } from "@tanstack/react-query";

import { getButlerLogs } from "@/api/index.ts";
import type { ButlerLogsParams } from "@/api/index.ts";

/**
 * Primary poll interval for butler logs queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const BUTLER_LOGS_POLL_MS = 5_000;

export function useButlerLogs(
  name: string,
  params?: ButlerLogsParams,
  enabled = true,
) {
  return useQuery({
    queryKey: ["butlers", name, "logs", params],
    queryFn: () => getButlerLogs(name, params),
    enabled: !!name && enabled,
    refetchInterval: BUTLER_LOGS_POLL_MS,
    // Keep previous data visible while re-fetching to avoid flicker.
    placeholderData: (prev) => prev,
  });
}
