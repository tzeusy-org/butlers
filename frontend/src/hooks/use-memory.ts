/**
 * TanStack Query hooks for the memory API.
 */

import { useQuery } from "@tanstack/react-query";

import {
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
} from "@/api/index.ts";
import type {
  EntityParams,
  EpisodeParams,
  FactParams,
  RuleParams,
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
