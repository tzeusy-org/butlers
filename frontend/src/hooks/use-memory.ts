/**
 * TanStack Query hooks for the memory API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveEntity,
  confirmFact,
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
  getMemoryCompactionLog,
  getMemoryRetentionPolicies,
  getMemoryStats,
  getRule,
  getRules,
  inspectMemory,
  promoteEntity,
  retractFact,
  revealEntitySecret,
  setEntityLinkedContact,
  unarchiveEntity,
  unlinkEntityContact,
  updateEntity,
  updateEntityInfo,
  updateMemoryRetentionPolicies,
  getDunbarRanking,
  forgetRelationshipEntity,
} from "@/api/index.ts";
import type {
  CreateEntityInfoRequest,
  EntityDetailParams,
  EntityParams,
  EpisodeParams,
  Fact,
  FactParams,
  MemoryInspectParams,
  RuleParams,
  UpdateEntityInfoRequest,
  UpdateEntityRequest,
  UpdateRetentionPoliciesRequest,
} from "@/api/types.ts";

/** Fetch aggregated memory statistics. */
export function useMemoryStats() {
  return useQuery({
    queryKey: ["memory-stats"],
    queryFn: () => getMemoryStats(),
    refetchInterval: 30_000,
  });
}

/**
 * Fetch recent memory writes for a specific butler.
 *
 * Calls GET /api/memory/episodes?butler={name}&limit={limit} ordered by
 * created_at desc (server default). The backend applies the butler filter
 * in the SQL WHERE clause — results are scoped to the given butler.
 */
export function useMemoryRecentWrites(butler: string, limit = 10) {
  return useQuery({
    queryKey: ["memory-recent-writes", butler, limit],
    queryFn: () => getEpisodes({ butler, limit }),
    refetchInterval: 15_000,
    enabled: butler.length > 0,
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

/**
 * Re-ink a fact (POST /facts/:id/confirm). On success, invalidates the
 * single-fact and facts-list caches so the detail page reflects the reset
 * decay timer immediately, plus the memory-stats cache so the KPI counts
 * (active vs fading facts) do not go stale. bu-awo8k.3, bu-3mxat.
 */
export function useConfirmFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (factId: string) => confirmFact(factId),
    onSuccess: (_, factId) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-fact", factId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-facts"] });
      void queryClient.invalidateQueries({ queryKey: ["memory-stats"] });
    },
  });
}

/**
 * Retract a fact (POST /facts/:id/retract). On success, invalidates the
 * single-fact and facts-list caches so the detail page reflects the retracted
 * validity immediately, plus the memory-stats cache so the KPI counts (active
 * facts drop, the headline numbers stay honest). bu-awo8k.4, bu-3mxat.
 */
export function useRetractFact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (factId: string) => retractFact(factId),
    onSuccess: (_, factId) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-fact", factId] });
      void queryClient.invalidateQueries({ queryKey: ["memory-facts"] });
      void queryClient.invalidateQueries({ queryKey: ["memory-stats"] });
    },
  });
}

/**
 * Fetch the facts derived from a given episode (GET /facts?source_episode_id).
 * Powers the episode detail page's "facts derived from this episode" section.
 * bu-awo8k.6. Disabled when no episodeId is provided.
 */
export function useFactsByEpisode(episodeId: string | undefined, limit = 50) {
  return useQuery({
    queryKey: ["memory-facts", "by-episode", episodeId, limit],
    queryFn: () => getFacts({ source_episode_id: episodeId!, limit }),
    enabled: !!episodeId,
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
export function useEntity(entityId: string | undefined, params?: EntityDetailParams) {
  return useQuery({
    queryKey: ["memory-entity", entityId, params],
    queryFn: () => getEntity(entityId!, params),
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

/** Soft-delete an entity. Pass retireFacts to auto-retire active facts. */
export function useDeleteEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (opts: { entityId: string; retireFacts?: boolean }) =>
      deleteEntity(opts.entityId, { retireFacts: opts.retireFacts }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Hard-delete (forget with tombstone) a relationship entity.
 *
 * Calls DELETE /api/butlers/relationship/entities/{id}.
 * Retracts all active facts and tombstones the entity. Irreversible.
 */
export function useForgetRelationshipEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => forgetRelationshipEntity(entityId),
    onSuccess: (_, entityId) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
      void queryClient.invalidateQueries({ queryKey: ["relationship-entities"] });
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
    },
  });
}

/** Archive an entity (hide from default views). */
export function useArchiveEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => archiveEntity(entityId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entities"] });
    },
  });
}

/** Restore an archived entity. */
export function useUnarchiveEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => unarchiveEntity(entityId),
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

/** Fetch retention policies. */
export function useMemoryRetentionPolicies() {
  return useQuery({
    queryKey: ["memory-retention-policies"],
    queryFn: () => getMemoryRetentionPolicies(),
    staleTime: 60_000,
  });
}

/** Bulk-update retention policies. */
export function useUpdateMemoryRetentionPolicies() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateRetentionPoliciesRequest) =>
      updateMemoryRetentionPolicies(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory-retention-policies"] });
    },
  });
}

/** Fetch compaction log entries. */
export function useMemoryCompactionLog(limit?: number) {
  return useQuery({
    queryKey: ["memory-compaction-log", limit],
    queryFn: () => getMemoryCompactionLog(limit),
    refetchInterval: 60_000,
  });
}

/** Inspect memory (search across tiers). */
export function useMemoryInspect(params?: MemoryInspectParams) {
  return useQuery({
    queryKey: ["memory-inspect", params],
    queryFn: () => inspectMemory(params),
    enabled: (params?.q != null && params.q.length > 0) || !!params?.kind,
  });
}

/** Fetch the Dunbar tier ranking for the social map visualization. */
export function useDunbarRanking(enabled: boolean = false) {
  return useQuery({
    queryKey: ["dunbar-ranking"],
    queryFn: () => getDunbarRanking(),
    enabled,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Lifestyle memory hooks
// ---------------------------------------------------------------------------

/**
 * Fetch up to `limit` active facts for a butler/subject pair with a single
 * stable cache key. Callers supply a `select` function to derive a
 * panel-specific slice from the shared cache entry — React Query caches the
 * full network response once and applies each subscriber's `select`
 * independently, so multiple calls with different `select` functions share the
 * same network request.
 *
 * Cache key: ["memory-butler-facts", butler, subject, limit]
 * Endpoint:  GET /memory/facts?subject=<subject>&scope=<butler>&validity=active&limit=<limit>
 *
 * @param butler  Butler name (e.g. "lifestyle"). Partitions the cache per butler.
 * @param subject Fact subject (e.g. "user").
 * @param select  Optional filter returning a subset of Fact[]. When omitted,
 *                returns all facts unchanged.
 * @param limit   Maximum facts to fetch (default 200).
 */
export function useButlerFacts({
  butler,
  subject,
  select,
  limit = 200,
}: {
  butler: string;
  subject: string;
  select?: (facts: Fact[]) => Fact[];
  limit?: number;
}) {
  return useQuery({
    queryKey: ["memory-butler-facts", butler, subject, limit],
    queryFn: async () => {
      const res = await getFacts({ subject, scope: butler, validity: "active", limit });
      return res.data ?? [];
    },
    select,
    refetchInterval: 60_000,
  });
}
