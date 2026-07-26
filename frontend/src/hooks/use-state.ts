/**
 * TanStack Query hooks for the butler state store API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { deleteButlerState, getButlerState, setButlerState } from "@/api/index.ts";

/**
 * Primary poll interval for butler state store queries (bu-ep4ks.15).
 * No fleet-bus event type covers this domain (see
 * event-cache-registry.ts's EVENT_CACHE_REGISTRY) -- this cadence IS
 * the update path, not a reconciliation sweep.
 */
const BUTLER_STATE_POLL_MS = 30_000;

/** Fetch all state entries for a butler with auto-refresh. */
export function useButlerState(butlerName: string) {
  return useQuery({
    queryKey: ["butlers", butlerName, "state"],
    queryFn: () => getButlerState(butlerName),
    enabled: !!butlerName,
    refetchInterval: BUTLER_STATE_POLL_MS,
  });
}

/** Mutation to set (create/update) a state entry for a butler. */
export function useSetState(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) =>
      setButlerState(butlerName, key, value),
    onSuccess: (_data, variables) => {
      toast.success(`State key "${variables.key}" saved`);
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "state"] });
    },
    onError: (error, variables) => {
      toast.error(`Failed to set "${variables.key}": ${error.message}`);
    },
  });
}

/** Mutation to delete a state entry for a butler. */
export function useDeleteState(butlerName: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (key: string) => deleteButlerState(butlerName, key),
    onSuccess: (_data, key) => {
      toast.success(`State key "${key}" deleted`);
      queryClient.invalidateQueries({ queryKey: ["butlers", butlerName, "state"] });
    },
    onError: (error, key) => {
      toast.error(`Failed to delete "${key}": ${error.message}`);
    },
  });
}
