/**
 * React Query hooks for the OwnTracks dashboard settings section.
 *
 * Provides hooks for status polling, configuration fetch, and bearer token
 * generation/regeneration flow.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  generateOwnTracksToken,
  getOwnTracksConfig,
  getOwnTracksStatus,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const ownTracksKeys = {
  all: ["owntracks"] as const,
  status: () => ["owntracks", "status"] as const,
  config: () => ["owntracks", "config"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch the current OwnTracks connection state.
 *
 * Polls every 60 seconds while the settings page is open so the event
 * counter and liveness badge stay current without hammering the backend.
 */
export function useOwnTracksStatus() {
  return useQuery({
    queryKey: ownTracksKeys.status(),
    queryFn: () => getOwnTracksStatus(),
    refetchInterval: 60_000,
    retry: false,
  });
}

/**
 * Fetch the computed webhook URL and setup metadata.
 *
 * This data is stable (URL depends only on server hostname), so we cache it
 * indefinitely for the session and do not auto-refetch.
 */
export function useOwnTracksConfig() {
  return useQuery({
    queryKey: ownTracksKeys.config(),
    queryFn: () => getOwnTracksConfig(),
    staleTime: Infinity,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Generate (or regenerate) the OwnTracks webhook bearer token.
 *
 * On success, invalidates the status query so the card reflects the updated
 * token_configured state. The token value is only available in the mutation
 * result — it is never re-fetched.
 */
export function useOwnTracksGenerateToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => generateOwnTracksToken(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ownTracksKeys.all });
    },
  });
}
