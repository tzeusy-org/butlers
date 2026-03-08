/**
 * TanStack Query hooks for thread affinity APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteThreadAffinityOverride,
  getThreadAffinitySettings,
  listThreadAffinityOverrides,
  updateThreadAffinitySettings,
  upsertThreadAffinityOverride,
} from "@/api/index.ts";
import type {
  ThreadAffinitySettingsUpdate,
  ThreadOverrideUpsert,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key families
// ---------------------------------------------------------------------------

export const threadAffinityKeys = {
  settings: () => ["thread-affinity-settings"] as const,
  overrides: () => ["thread-affinity-overrides"] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch global thread-affinity settings. Staleness: 60s per spec. */
export function useThreadAffinitySettings() {
  return useQuery({
    queryKey: threadAffinityKeys.settings(),
    queryFn: () => getThreadAffinitySettings(),
    staleTime: 60_000,
  });
}

/** Fetch per-thread affinity overrides. */
export function useThreadAffinityOverrides() {
  return useQuery({
    queryKey: threadAffinityKeys.overrides(),
    queryFn: () => listThreadAffinityOverrides(),
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Update global thread-affinity settings. */
export function useUpdateThreadAffinitySettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ThreadAffinitySettingsUpdate) => updateThreadAffinitySettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: threadAffinityKeys.settings() });
    },
  });
}

/**
 * Upsert a per-thread affinity override.
 */
export function useUpsertThreadAffinityOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, body }: { threadId: string; body: ThreadOverrideUpsert }) =>
      upsertThreadAffinityOverride(threadId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: threadAffinityKeys.settings() });
      queryClient.invalidateQueries({ queryKey: threadAffinityKeys.overrides() });
    },
  });
}

/**
 * Delete a per-thread affinity override.
 */
export function useDeleteThreadAffinityOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (threadId: string) => deleteThreadAffinityOverride(threadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: threadAffinityKeys.settings() });
      queryClient.invalidateQueries({ queryKey: threadAffinityKeys.overrides() });
    },
  });
}
