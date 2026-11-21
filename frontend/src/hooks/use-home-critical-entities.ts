/** TanStack Query hooks for the owner's Home critical-entity registry. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCriticalEntity,
  getCriticalEntities,
  putCriticalEntity,
} from "@/api/home-critical-entities.ts";
import type { HomeCriticalEntity, HomeCriticalEntityInput } from "@/api/home-critical-entities.ts";

export const homeCriticalEntitiesKey = ["home", "critical-entities"] as const;

export function useHomeCriticalEntities() {
  return useQuery<HomeCriticalEntity[]>({
    queryKey: homeCriticalEntitiesKey,
    queryFn: () => getCriticalEntities(),
    retry: false,
  });
}

function useRegistryMutation<T>(mutationFn: (input: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: homeCriticalEntitiesKey });
    },
  });
}

export function useSaveCriticalEntity() {
  return useRegistryMutation((input: HomeCriticalEntityInput) => putCriticalEntity(input));
}

export function useDeleteCriticalEntity() {
  return useRegistryMutation((entityId: string) => deleteCriticalEntity(entityId));
}
