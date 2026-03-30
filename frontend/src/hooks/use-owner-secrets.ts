/**
 * TanStack Query hooks for the owner entity_info (User secrets tab).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createEntityInfo,
  deleteEntityInfo,
  getOwnerEntityInfo,
  revealEntitySecret,
  updateEntityInfo,
} from "@/api/index.ts";
import type {
  CreateEntityInfoRequest,
  UpdateEntityInfoRequest,
} from "@/api/types.ts";

export const ownerSecretsKeys = {
  all: ["owner-entity-info"] as const,
  list: () => ["owner-entity-info", "list"] as const,
};

/** Fetch all entity_info entries for the owner entity. */
export function useOwnerEntityInfo() {
  return useQuery({
    queryKey: ownerSecretsKeys.list(),
    queryFn: () => getOwnerEntityInfo(),
    retry: false,
  });
}

/** Create an entity_info entry on the owner entity. */
export function useCreateOwnerEntityInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      request,
    }: {
      entityId: string;
      request: CreateEntityInfoRequest;
    }) => createEntityInfo(entityId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ownerSecretsKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Update an entity_info entry on the owner entity. */
export function useUpdateOwnerEntityInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      infoId,
      request,
    }: {
      entityId: string;
      infoId: string;
      request: UpdateEntityInfoRequest;
    }) => updateEntityInfo(entityId, infoId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ownerSecretsKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Delete an entity_info entry on the owner entity. */
export function useDeleteOwnerEntityInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, infoId }: { entityId: string; infoId: string }) =>
      deleteEntityInfo(entityId, infoId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ownerSecretsKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Reveal a secured entity_info value on the owner entity. */
export function useRevealOwnerEntitySecret() {
  return useMutation({
    mutationFn: ({ entityId, infoId }: { entityId: string; infoId: string }) =>
      revealEntitySecret(entityId, infoId),
  });
}
