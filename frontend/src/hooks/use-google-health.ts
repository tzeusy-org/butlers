/**
 * Google Health connector hooks.
 *
 * Thin React Query wrappers around the dashboard API endpoints landed in
 * ``src/butlers/api/routers/google_health.py``. The status query polls
 * every 30 seconds while the page is visible and is paused automatically
 * when the browser tab is hidden (``refetchIntervalInBackground: false``).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { disconnectGoogleHealth, getGoogleHealthStatus } from "@/api/index.ts";

import { googleAccountsKeys } from "./use-secrets";

export const googleHealthKeys = {
  all: ["google-health"] as const,
  status: () => ["google-health", "status"] as const,
};

/**
 * Default poll cadence while the card is mounted and the tab is visible.
 * Matches the spec's 30-second requirement. React Query pauses refetch
 * intervals on hidden tabs by default (``refetchIntervalInBackground:
 * false``) so we don't need a manual pause hook.
 */
const STATUS_POLL_INTERVAL_MS = 30_000;

/** Fetch + poll the Google Health connector status endpoint. */
export function useGoogleHealthStatus(options?: {
  enabled?: boolean;
  pollIntervalMs?: number;
}) {
  return useQuery({
    queryKey: googleHealthKeys.status(),
    queryFn: () => getGoogleHealthStatus(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.pollIntervalMs ?? STATUS_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

/**
 * Scope-selective disconnect — strips only the three Google Health scope
 * URLs from the primary Google account's ``granted_scopes``. Calendar /
 * Drive / Gmail scopes are preserved. On success invalidates both the
 * Google accounts cache (to refresh the scope-picker state) and the
 * Google Health status cache (to reflect the new scope set).
 */
export function useDisconnectGoogleHealth() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => disconnectGoogleHealth(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: googleHealthKeys.all });
      queryClient.invalidateQueries({ queryKey: googleAccountsKeys.all });
    },
  });
}
