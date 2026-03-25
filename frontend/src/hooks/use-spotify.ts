/**
 * React Query hooks for the Spotify dashboard settings section.
 *
 * Provides hooks for status polling, client ID configuration, OAuth PKCE flow
 * initiation, and disconnect action.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  disconnectSpotify,
  getSpotifyStatus,
  saveSpotifyConfig,
  startSpotifyOAuth,
} from "@/api/index.ts";
import type { SpotifyConfigRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const spotifyKeys = {
  all: ["spotify"] as const,
  status: () => ["spotify", "status"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch the current Spotify connection state.
 *
 * Suitable for on-mount display; does not auto-poll by default.
 * Invalidate via `spotifyKeys.status()` after connect or disconnect.
 */
export function useSpotifyStatus() {
  return useQuery({
    queryKey: spotifyKeys.status(),
    queryFn: () => getSpotifyStatus(),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Save Spotify client_id to server-side CredentialStore.
 *
 * On success, invalidates the status query so the card reflects the updated
 * configuration state.
 */
export function useSpotifyConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: SpotifyConfigRequest) => saveSpotifyConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: spotifyKeys.all });
    },
  });
}

/**
 * Initiate the Spotify OAuth PKCE flow.
 *
 * Calls POST /api/spotify/oauth/start and returns the authorization URL.
 * The caller is responsible for redirecting the user's browser to that URL.
 */
export function useSpotifyOAuthStart() {
  return useMutation({
    mutationFn: () => startSpotifyOAuth(),
  });
}

/**
 * Disconnect the Spotify account.
 *
 * On success, invalidates all Spotify queries so the settings card
 * transitions to the not_configured state.
 */
export function useSpotifyDisconnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => disconnectSpotify(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: spotifyKeys.all });
    },
  });
}
