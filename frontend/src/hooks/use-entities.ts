/**
 * TanStack Query hooks for relationship-scoped entity endpoints.
 *
 * These hooks target the relationship butler's entity-level APIs at
 * `/api/relationship/entities/{id}/...` — distinct from the memory butler's
 * entity hooks (use-memory.ts). The relationship-scoped surface is the
 * activity view (notes, interactions, gifts, loans, timeline).
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getEntityDates,
  getEntityGifts,
  getEntityInteractions,
  getEntityLinkedContacts,
  getEntityLoans,
  getEntityMessageThreads,
  getEntityNotes,
  getEntityTimeline,
  getRelationshipEntityQueue,
  listRelationshipEntities,
  searchRelationshipEntities,
  updateEntityDunbarTier,
} from "@/api/index.ts";
import type { RelationshipEntityListParams } from "@/api/index.ts";

/** Fetch all contacts linked to a relationship entity. */
export function useEntityLinkedContacts(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-linked-contacts", entityId],
    queryFn: () => getEntityLinkedContacts(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch notes tab data for a relationship entity. */
export function useEntityNotes(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-notes", entityId],
    queryFn: () => getEntityNotes(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch interactions tab data for a relationship entity. */
export function useEntityInteractions(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-interactions", entityId],
    queryFn: () => getEntityInteractions(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch gifts tab data for a relationship entity. */
export function useEntityGifts(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-gifts", entityId],
    queryFn: () => getEntityGifts(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch loans tab data for a relationship entity. */
export function useEntityLoans(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-loans", entityId],
    queryFn: () => getEntityLoans(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch unified timeline data for a relationship entity. */
export function useEntityTimeline(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-timeline", entityId],
    queryFn: () => getEntityTimeline(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch grouped message thread summaries for a relationship entity. */
export function useEntityMessageThreads(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-message-threads", entityId],
    queryFn: () => getEntityMessageThreads(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch important dates (birthdays, anniversaries) scoped to one entity. */
export function useEntityDates(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-dates", entityId],
    queryFn: () => getEntityDates(entityId!),
    enabled: !!entityId,
  });
}

// ---------------------------------------------------------------------------
// Entity Finder search (Cmd-K, bu-xfjwk)
// ---------------------------------------------------------------------------

const ENTITY_FINDER_DEBOUNCE_MS = 200;
const ENTITY_FINDER_MIN_QUERY_LENGTH = 1;
const ENTITY_FINDER_DEFAULT_LIMIT = 8;

/**
 * Debounced hook that fetches entity search results from
 * GET /api/butlers/relationship/entities/search.
 *
 * Enabled as soon as the query is non-empty. Returns results already ordered
 * by server-side score (prefix > contact_fact > substring > predicate).
 * Empty or whitespace queries return undefined data (hook is disabled).
 */
export function useEntityFinderSearch(
  query: string,
  options?: { limit?: number },
) {
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedQuery(query),
      ENTITY_FINDER_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [query]);

  const trimmed = debouncedQuery.trim();

  return useQuery({
    queryKey: [
      "entity-finder-search",
      trimmed,
      options?.limit ?? ENTITY_FINDER_DEFAULT_LIMIT,
    ],
    queryFn: () =>
      searchRelationshipEntities(
        trimmed,
        options?.limit ?? ENTITY_FINDER_DEFAULT_LIMIT,
      ),
    enabled: trimmed.length >= ENTITY_FINDER_MIN_QUERY_LENGTH,
    // Keep stale results visible while a new query is in-flight so the
    // Finder doesn't flash blank during fast typing.
    placeholderData: (prev) => prev,
  });
}

// ---------------------------------------------------------------------------
// Relationship entity index (§9.1 list+filter API, bu-s2bgc)
// ---------------------------------------------------------------------------

/**
 * Fetch the paginated entity list from the relationship butler (§9.1).
 *
 * Distinct from `useEntities` (use-memory.ts) which targets the memory butler.
 * This hook hits GET /api/butlers/relationship/entities and returns relationship-scoped
 * fields: tier, last_seen, contact_fact_count.
 */
export function useRelationshipEntities(params?: RelationshipEntityListParams) {
  return useQuery({
    queryKey: ["relationship-entities", params],
    queryFn: () => listRelationshipEntities(params),
  });
}

/**
 * Fetch the entity curation queue from the relationship butler (§9.5).
 *
 * Hits GET /api/butlers/relationship/entities/queue.
 * Returns three buckets: unidentified → duplicate-candidate → stale.
 */
export function useRelationshipEntityQueue(params?: {
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ["relationship-entity-queue", params],
    queryFn: () => getRelationshipEntityQueue(params),
  });
}

/** Pin or clear the Dunbar tier on an entity. */
export function useUpdateEntityDunbarTier() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, tier }: { entityId: string; tier: number | null }) =>
      updateEntityDunbarTier(entityId, tier),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["memory-entity", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["dunbar-ranking"] });
    },
  });
}
