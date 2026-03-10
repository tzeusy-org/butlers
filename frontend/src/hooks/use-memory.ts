/**
 * TanStack Query hooks for the memory API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createEntityInfo,
  deleteEntity,
  deleteEntityInfo,
  getEntities,
  getEntity,
  getEpisode,
  getEpisodes,
  getFact,
  getFacts,
  getMemoryActivity,
  getMemoryStats,
  getRule,
  getRules,
  mergeEntity,
  promoteEntity,
  revealEntitySecret,
  setEntityLinkedContact,
  unlinkEntityContact,
  updateEntity,
  updateEntityInfo,
} from "@/api/index.ts";
import type {
  CreateEntityInfoRequest,
  EntityParams,
  EpisodeParams,
  FactParams,
  RuleParams,
  UpdateEntityInfoRequest,
  UpdateEntityRequest,
} from "@/api/types.ts";

/** Fetch aggregated memory statistics. */
export function useMemoryStats() {
  return useQuery({
    queryKey: ["memory-stats"],
    queryFn: () => getMemoryStats(),
    refetchInterval: 30_000,
  });
}

/** Fetch a paginated list of episodes. */
export function useEpisodes(params?: EpisodeParams) {
  return useQuery({
    queryKey: ["memory-episodes", params],
    queryFn: () => getEpisodes(params),
    refetchInterval: 30_000,
  });
}

/** Fetch a single episode by ID. */
export function useEpisode(episodeId: string | undefined) {
  return useQuery({
    queryKey: ["memory-episode", episodeId],
    queryFn: () => getEpisode(episodeId!),
    enabled: !!episodeId,
  });
}

/** Fetch a paginated list of facts. */
export function useFacts(params?: FactParams) {
  return useQuery({
    queryKey: ["memory-facts", params],
    queryFn: () => getFacts(params),
    refetchInterval: 30_000,
  });
}

/** Fetch a single fact by ID. */
export function useFact(factId: string | null) {
  return useQuery({
    queryKey: ["memory-fact", factId],
    queryFn: () => getFact(factId!),
    enabled: !!factId,
  });
}

/** Fetch a paginated list of rules. */
export function useRules(params?: RuleParams) {
  return useQuery({
    queryKey: ["memory-rules", params],
    queryFn: () => getRules(params),
    refetchInterval: 30_000,
  });
}

/** Fetch a single rule by ID. */
export function useRule(ruleId: string | null) {
  return useQuery({
    queryKey: ["memory-rule", ruleId],
    queryFn: () => getRule(ruleId!),
    enabled: !!ruleId,
  });
}

/** Fetch recent memory activity. */
export function useMemoryActivity(limit?: number) {
  return useQuery({
    queryKey: ["memory-activity", limit],
    queryFn: () => getMemoryActivity(limit),
    refetchInterval: 15_000,
  });
}

/** Fetch a paginated list of entities. */
export function useEntities(params?: EntityParams) {
  return useQuery({
    queryKey: ["memory-entities", params],
    queryFn: () => getEntities(params),
    refetchInterval: 30_000,
  });
}

/** Fetch a single entity by ID. */
export function useEntity(entityId: string | undefined) {
  return useQuery({
    queryKey: ["memory-entity", entityId],
    queryFn: () => getEntity(entityId!),
    enabled: !!entityId,
  });
}

// ---------------------------------------------------------------------------
// Entity mutations
// ---------------------------------------------------------------------------

/** Update entity core fields (canonical_name, aliases). */
export function useUpdateEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      request,
    }: {
      entityId: string;
      request: UpdateEntityRequest;
    }) => updateEntity(entityId, request),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Merge source entity into target entity. */
export function useMergeEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      targetEntityId,
      sourceEntityId,
    }: {
      targetEntityId: string;
      sourceEntityId: string;
    }) => mergeEntity(targetEntityId, sourceEntityId),
    onSuccess: (_, { targetEntityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", targetEntityId] });
    },
  });
}

/** Soft-delete an entity. */
export function useDeleteEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => deleteEntity(entityId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Promote a transitory (unidentified) entity by clearing the unidentified flag. */
export function usePromoteEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => promoteEntity(entityId),
    onSuccess: (_, entityId) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Entity info mutations
// ---------------------------------------------------------------------------

/** Create an entity_info entry. */
export function useCreateEntityInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      request,
    }: {
      entityId: string;
      request: CreateEntityInfoRequest;
    }) => createEntityInfo(entityId, request),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Update an entity_info entry. */
export function useUpdateEntityInfo() {
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
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Delete an entity_info entry. */
export function useDeleteEntityInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, infoId }: { entityId: string; infoId: string }) =>
      deleteEntityInfo(entityId, infoId),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Reveal a secured entity_info value. */
export function useRevealEntitySecret() {
  return useMutation({
    mutationFn: ({ entityId, infoId }: { entityId: string; infoId: string }) =>
      revealEntitySecret(entityId, infoId),
  });
}

// ---------------------------------------------------------------------------
// Entity linked-contact mutations
// ---------------------------------------------------------------------------

/** Link a contact to an entity. */
export function useSetLinkedContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, contactId }: { entityId: string; contactId: string }) =>
      setEntityLinkedContact(entityId, contactId),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Unlink the contact from an entity. */
export function useUnlinkContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => unlinkEntityContact(entityId),
    onSuccess: (_, entityId) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
      void queryClient.invalidateQueries({ queryKey: ["contacts"] });
      void queryClient.invalidateQueries({ queryKey: ["contact"] });
      void queryClient.invalidateQueries({ queryKey: ["unlinked-contacts"] });
    },
  });
}
