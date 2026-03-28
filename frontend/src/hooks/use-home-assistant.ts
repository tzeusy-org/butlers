/**
 * React Query hooks for the Home Assistant dashboard settings section.
 *
 * Provides hooks for status polling, credential configuration, and
 * credential deletion.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  configureHomeAssistant,
  deleteHomeAssistantConfig,
  getHomeAssistantStatus,
} from "@/api/index.ts";
import type { HomeAssistantConfigRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const homeAssistantKeys = {
  all: ["home-assistant"] as const,
  status: () => ["home-assistant", "status"] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch the current Home Assistant connection state.
 *
 * Suitable for on-mount display; does not auto-poll by default.
 * Invalidate via `homeAssistantKeys.status()` after configure or delete.
 */
export function useHomeAssistantStatus() {
  return useQuery({
    queryKey: homeAssistantKeys.status(),
    queryFn: () => getHomeAssistantStatus(),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Validate and save Home Assistant URL + access token.
 *
 * On success, invalidates the status query so the card reflects the updated
 * connection state.
 */
export function useConfigureHomeAssistant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: HomeAssistantConfigRequest) => configureHomeAssistant(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: homeAssistantKeys.all });
    },
  });
}

/**
 * Remove stored Home Assistant credentials.
 *
 * On success, invalidates all Home Assistant queries so the settings card
 * transitions to the not_configured state.
 */
export function useDeleteHomeAssistantConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => deleteHomeAssistantConfig(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: homeAssistantKeys.all });
    },
  });
}
