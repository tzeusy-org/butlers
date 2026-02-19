/**
 * TanStack Query hooks for the butler state store API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { deleteButlerState, getButlerState, setButlerState } from "@/api/index.ts";

/** Fetch all state entries for a butler with auto-refresh. */
export function useButlerState(butlerName: string) {
  return useQuery({
    queryKey: ["butlers", butlerName, "state"],
    queryFn: () => getButlerState(butlerName),
    enabled: !!butlerName,
    refetchInterval: 30_000,
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
