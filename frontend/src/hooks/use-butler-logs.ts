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

export function useButlerLogs(
  name: string,
  params?: ButlerLogsParams,
  enabled = true,
) {
  return useQuery({
    queryKey: ["butlers", name, "logs", params],
    queryFn: () => getButlerLogs(name, params),
    enabled: !!name && enabled,
    refetchInterval: 5_000,
    // Keep previous data visible while re-fetching to avoid flicker.
    placeholderData: (prev) => prev,
  });
}
