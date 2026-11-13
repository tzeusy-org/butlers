/** TanStack Query hooks for the owner presence entity settings. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPresenceOwnerEntities,
  getPresenceTransitions,
  putPresenceOwnerEntities,
} from "@/api/home-presence.ts";
import { POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy.ts";
import type { PresenceOwnerEntities, PresenceTransitions } from "@/api/home-presence.ts";

export const presenceOwnerEntitiesKey = ["home", "presence", "owner-entities"] as const;

export function usePresenceOwnerEntities() {
  return useQuery<PresenceOwnerEntities>({
    queryKey: presenceOwnerEntitiesKey,
    queryFn: () => getPresenceOwnerEntities(),
    retry: false,
  });
}

export function useSavePresenceOwnerEntities() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (save: { ownerEntities: string[]; expectedVersion: number }) =>
      putPresenceOwnerEntities(save.ownerEntities, save.expectedVersion),
    // Submitted ids are not retained in the mutation cache once the editor lets go of them.
    gcTime: 0,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: presenceOwnerEntitiesKey });
    },
  });
}

export const presenceTransitionsKey = ["home", "presence", "transitions"] as const;

export function usePresenceTransitions(cursor: string | null = null) {
  return useQuery<PresenceTransitions>({
    queryKey: [...presenceTransitionsKey, cursor],
    queryFn: () => getPresenceTransitions(20, cursor),
    retry: false,
    refetchInterval: POLL_BUS_RECONCILE_MS,
  });
}
