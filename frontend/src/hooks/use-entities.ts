/**
 * TanStack Query hooks for relationship-scoped entity endpoints.
 *
 * These hooks target the relationship butler's entity-level APIs at
 * `/api/relationship/entities/{id}/...` — distinct from the memory butler's
 * entity hooks (use-memory.ts). The relationship-scoped surface is the
 * activity view (notes, interactions, gifts, loans, timeline).
 */

import { useQuery } from "@tanstack/react-query";

import {
  getEntityGifts,
  getEntityInteractions,
  getEntityLinkedContacts,
  getEntityLoans,
  getEntityMessageThreads,
  getEntityNotes,
  getEntityTimeline,
} from "@/api/index.ts";

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
