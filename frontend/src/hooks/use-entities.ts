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
  addEntityContact,
  archiveRelationshipEntity,
  clearEntityPreferredChannel,
  compareRelationshipEntities,
  deleteEntityContact,
  dismissRelationshipEntityPair,
  updateEntityContact,
  dismissRelationshipEntityQueueItem,
  forgetRelationshipEntity,
  getEntityActivityBins,
  getEntityConcentration,
  getEntityCoreDates,
  getEntityDates,
  getEntityDeltaFacts,
  getEntityFacts,
  getEntityGifts,
  getEntityLinkedContacts,
  getEntityLoans,
  getEntityMessageThreads,
  getEntityNeighbours,
  getEntityTimeline,
  getRelationshipEntityQueue,
  listRelationshipEntities,
  markEntityView,
  mergeRelationshipEntities,
  promoteRelationshipEntity,
  revealEntitySecret,
  searchRelationshipEntities,
  setEntityPreferredChannel,
  updateEntityDunbarTier,
} from "@/api/index.ts";
import type {
  AddEntityContactRequest,
  CompareEntitiesRequest,
  DismissEntityPairRequest,
  UpdateEntityContactRequest,
  EntityFactsParams,
  NeighboursParams,
  MergeRelationshipEntitiesRequest,
  PromoteRelationshipEntityRequest,
  RelationshipEntityListParams,
} from "@/api/index.ts";

/** Fetch all contacts linked to a relationship entity. */
export function useEntityLinkedContacts(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-linked-contacts", entityId],
    queryFn: () => getEntityLinkedContacts(entityId!),
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

/**
 * Fetch the 90-day daily activity-count series for an entity's sparkline (bu-xzh76).
 *
 * The response ``bins`` array is a dense, ascending-by-date series (one entry
 * per day including zero-count days) over ``window`` (default 90d).
 */
export function useEntityActivityBins(
  entityId: string | undefined,
  params?: { window?: string },
) {
  return useQuery({
    queryKey: ["entity-activity-bins", entityId, params?.window ?? "90d"],
    queryFn: () => getEntityActivityBins(entityId!, params),
    enabled: !!entityId,
  });
}

/**
 * Fetch facts changed since the entity's view mark — delta-since-last-visit (bu-xzh76).
 *
 * Read-only; the response is computed against the *current* mark before it
 * moves. ``marked_at`` is null on a first visit (no banner renders). The detail
 * page reads this, renders the banner, then posts the mark via
 * {@link useMarkEntityView} (spec: delta read before the mark moves). Disable
 * refetch-on-mount so the delta reflects the mark as it was on entry, not after
 * the post-render view-mark write.
 */
export function useEntityDeltaFacts(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-delta-facts", entityId],
    queryFn: () => getEntityDeltaFacts(entityId!),
    enabled: !!entityId,
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });
}

/**
 * Upsert the owner's "last viewed" mark for an entity (bu-xzh76).
 *
 * The detail page calls this *after* reading the delta facts for the current
 * load, so the next visit's delta is computed relative to this mark.
 */
export function useMarkEntityView() {
  return useMutation({
    mutationFn: (entityId: string) => markEntityView(entityId),
  });
}

/**
 * Fetch the entity's date-kind facts with their next occurrence — core dates (bu-xzh76).
 *
 * Server-side extraction (``has-birthday``, anniversaries) with next occurrence,
 * ``days_until``, and provenance per row — replaces client-side string matching.
 * Items are ordered by ``days_until`` ascending (soonest first).
 */
export function useEntityCoreDates(entityId: string | undefined) {
  return useQuery({
    queryKey: ["entity-core-dates", entityId],
    queryFn: () => getEntityCoreDates(entityId!),
    enabled: !!entityId,
  });
}

/** Fetch relational neighbours grouped by predicate for a relationship entity (§9.2).
 *
 * Pass ``params.rank = "weight"`` (with optional ``per_predicate``) to request
 * ranked truncation; the response then carries a ``remainders`` map driving the
 * "+N more" affordance for Hop / Columns.
 */
export function useEntityNeighbours(
  entityId: string | undefined,
  params?: NeighboursParams,
) {
  return useQuery({
    queryKey: ["entity-neighbours", entityId, params?.rank ?? null, params?.per_predicate ?? null],
    queryFn: () => getEntityNeighbours(entityId!, params),
    enabled: !!entityId,
  });
}

/**
 * Fetch per-fact provenance data from relationship.entity_facts (bu-mg4dk).
 *
 * Provides real provenance fields for the Workbench ProvenanceGrid (§6b Amendment 7):
 * weight, last_observed_at, object_kind, src — plus the row ``store`` label and
 * ``staleness_band``. Keyset (cursor) paginated.
 *
 * @param entityId — the entity UUID to fetch facts for.
 * @param params — optional facts-drill filters + keyset pagination params
 *   (predicate / validity / store / limit / cursor).
 */
export function useEntityFacts(
  entityId: string | undefined,
  params?: EntityFactsParams,
) {
  return useQuery({
    queryKey: [
      "entity-facts",
      entityId,
      params?.predicate ?? null,
      params?.validity ?? "active",
      params?.store ?? "identity",
      params?.limit ?? 20,
      params?.cursor ?? null,
    ],
    queryFn: () => getEntityFacts(entityId!, params),
    enabled: !!entityId,
  });
}

/**
 * Fetch concentration balance-sheet for a relational predicate (§8.4, §9.3).
 *
 * The response includes ``predicate_tabs`` so the component can render the
 * full predicate tab strip without a separate request.
 * When ``pred`` is undefined or empty, the backend defaults to ``'knows'``.
 */
export function useEntityConcentration(pred: string | undefined) {
  return useQuery({
    queryKey: ["entity-concentration", pred ?? ""],
    queryFn: () => getEntityConcentration(pred),
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

function invalidateRelationshipEntityIndex(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["relationship-entities"] });
  void queryClient.invalidateQueries({ queryKey: ["relationship-entity-queue"] });
  void queryClient.invalidateQueries({ queryKey: ["entity-finder-search"] });
}

/** Promote an existing unidentified entity through the relationship API. */
export function usePromoteRelationshipEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      canonicalName,
      entityType,
    }: {
      entityId: string;
      canonicalName: string;
      entityType: string;
    }) => {
      const request: PromoteRelationshipEntityRequest = {
        entity_id: entityId,
        canonical_name: canonicalName,
        entity_type: entityType,
      };
      return promoteRelationshipEntity(request);
    },
    onSuccess: (_, { entityId }) => {
      invalidateRelationshipEntityIndex(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["relationship-entity", entityId] });
    },
  });
}

/** Archive an entity through the relationship API. */
export function useArchiveRelationshipEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => archiveRelationshipEntity(entityId),
    onSuccess: () => invalidateRelationshipEntityIndex(queryClient),
  });
}

/** Forget an entity through the relationship API. */
export function useForgetRelationshipEntity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => forgetRelationshipEntity(entityId),
    onSuccess: (_, entityId) => {
      invalidateRelationshipEntityIndex(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["relationship-entity", entityId] });
    },
  });
}

/** Dismiss an entity from the relationship curation queue. */
export function useDismissRelationshipEntityQueueItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (entityId: string) => dismissRelationshipEntityQueueItem(entityId),
    onSuccess: () => invalidateRelationshipEntityIndex(queryClient),
  });
}

/** Merge two entities through the relationship API. */
export function useMergeRelationshipEntities() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: MergeRelationshipEntitiesRequest) =>
      mergeRelationshipEntities(request),
    onSuccess: (_, request) => {
      invalidateRelationshipEntityIndex(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["relationship-entity", request.entityA] });
      void queryClient.invalidateQueries({ queryKey: ["relationship-entity", request.entityB] });
    },
  });
}

/**
 * Compute the merge-review structural diff of two entities (relationship-merge-review).
 *
 * A mutation (not a query) because the compare view is opened on demand from an
 * entry point (queue card, Index gutter, detail-page `m` key, Workbench panel).
 * The returned data is the {@link CompareEntitiesResponse} diff: ``a`` / ``b``
 * blocks plus ``shared`` (duplicate evidence) and ``divergent`` (conflicts).
 */
export function useCompareEntities() {
  return useMutation({
    mutationFn: (request: CompareEntitiesRequest) => compareRelationshipEntities(request),
  });
}

/**
 * Dismiss a compared duplicate-candidate pair (relationship-merge-review).
 *
 * Writes a ``merge_reviews`` row with ``outcome = 'dismissed'`` and suppresses
 * the pair from the queue's duplicate-candidate bucket until new shared evidence
 * arises. Invalidates the curation queue so the dismissed pair disappears.
 */
export function useDismissEntityPair() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: DismissEntityPairRequest) => dismissRelationshipEntityPair(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["relationship-entity-queue"] });
    },
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

// ---------------------------------------------------------------------------
// Entity-contact triple mutations (§9.4, bu-u1w78 / bu-k9ylx write-path cut-over)
// ---------------------------------------------------------------------------

/**
 * Assert a contact-fact triple for an entity.
 *
 * Used by ContactChannelCard.AddChannelInfoForm after the write-path cut-over.
 * `predicate` must start with "has-" (see contact_info_type_to_predicate mapping).
 * Invalidates entity-linked-contacts, entity-facts, and relationship-entities on
 * success so the ProvenanceGrid and entity list summaries stay in sync.
 */
export function useAddEntityContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      request,
    }: {
      entityId: string;
      request: AddEntityContactRequest;
    }) => addEntityContact(entityId, request),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["entity-linked-contacts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["entity-facts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["relationship-entities"] });
    },
  });
}

/**
 * Retract an active contact-fact triple from an entity.
 *
 * Used by ContactChannelCard.ExpandedContactInfoRow after the write-path cut-over.
 * `predicate` must start with "has-". `valueHash` is SHA-256[:16] of the value
 * (matches ContactFact.value_hash). Invalidates entity-linked-contacts, entity-facts,
 * and relationship-entities on success so the ProvenanceGrid and entity list
 * summaries stay in sync.
 */
export function useDeleteEntityContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      predicate,
      valueHash,
    }: {
      entityId: string;
      predicate: string;
      valueHash: string;
    }) => deleteEntityContact(entityId, predicate, valueHash),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["entity-linked-contacts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["entity-facts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["relationship-entities"] });
    },
  });
}

/**
 * Reveal a secured entity_info value.
 *
 * Used by ContactChannelCard.SecuredChannelEntry for all secured entries
 * (all entries from list_entity_linked_contacts carry source="entity_facts"
 * since public.contact_info was dropped in bu-e2ja9).
 * Routes to GET /relationship/entities/{entityId}/secrets/{infoId}.
 */
export function useRevealEntityContactSecret() {
  return useMutation({
    mutationFn: ({ entityId, infoId }: { entityId: string; infoId: string }) =>
      revealEntitySecret(entityId, infoId),
  });
}

/**
 * Edit-in-place a contact-fact triple for an entity.
 *
 * Used by ContactChannelCard.ExpandedContactInfoRow to replace an existing
 * contact value (retract old triple + assert new triple atomically on the
 * backend). `predicate` must start with "has-". `valueHash` is SHA-256[:16]
 * of the current value (matches ContactFact.value_hash). Invalidates
 * entity-linked-contacts, entity-facts, and relationship-entities on success.
 */
export function useUpdateEntityContact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      entityId,
      predicate,
      valueHash,
      request,
    }: {
      entityId: string;
      predicate: string;
      valueHash: string;
      request: UpdateEntityContactRequest;
    }) => updateEntityContact(entityId, predicate, valueHash, request),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["entity-linked-contacts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["entity-facts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["relationship-entities"] });
    },
  });
}

/**
 * Set an entity's preferred outbound channel via the entity-keyed
 * `prefers-channel` fact (entity-keyed-preferred-channel).
 *
 * Used by ContactChannelCard.PreferredChannelSelector. Replaces the COMPAT-ONLY
 * contact-keyed `usePatchContact` write of `contacts.preferred_channel`.
 * Invalidates entity-linked-contacts (carries preferred_channel) and entity-facts
 * on success.
 */
export function useSetPreferredChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId, channel }: { entityId: string; channel: string }) =>
      setEntityPreferredChannel(entityId, { channel }),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["entity-linked-contacts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["entity-facts", entityId] });
    },
  });
}

/**
 * Clear an entity's preferred channel by retracting the active `prefers-channel`
 * fact. Idempotent. Invalidates entity-linked-contacts and entity-facts.
 */
export function useClearPreferredChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ entityId }: { entityId: string }) => clearEntityPreferredChannel(entityId),
    onSuccess: (_, { entityId }) => {
      void queryClient.invalidateQueries({ queryKey: ["entity-linked-contacts", entityId] });
      void queryClient.invalidateQueries({ queryKey: ["entity-facts", entityId] });
    },
  });
}
