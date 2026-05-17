/**
 * TanStack Query hooks for the ingestion event lineage Timeline tab.
 *
 * Query key strategy:
 * - ingestionEventKeys.list(filters)          → cursor-paginated IngestionEventSummary list
 * - ingestionEventKeys.sessions(requestId)     → sessions for a given request_id
 * - ingestionEventKeys.rollup(requestId)       → cost/token rollup for a request_id
 * - ingestionEventKeys.replays(requestId)      → replay history from public.audit_log
 * - ingestionEventKeys.senderContact(requestId) → resolved contact name for sender_identity
 *
 * Stale time of 30s matches the spec for Timeline tab data freshness.
 *
 * BREAKING (bu-1f91v.3): useIngestionEvents now uses useInfiniteQuery.
 * Contract: { pages, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError }
 * The old { data: { data, meta: { total, offset, limit } } } shape is removed.
 */

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  listIngestionEvents,
  getIngestionEventSessions,
  getIngestionEventRollup,
  getIngestionEventReplays,
  getIngestionEventSenderContact,
} from "@/api/index.ts";
import type { CursorPaginatedResponse, IngestionEventsParams, IngestionEventSummary } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

/** Filters used as the infinite-scroll query key (cursor is NOT part of the key). */
export type IngestionEventsFilters = Omit<IngestionEventsParams, "cursor">;

export const ingestionEventKeys = {
  all: ["ingestion", "events"] as const,
  list: (filters: IngestionEventsFilters) =>
    [...ingestionEventKeys.all, "list", filters] as const,
  sessions: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "sessions"] as const,
  rollup: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "rollup"] as const,
  replays: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "replays"] as const,
  senderContact: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "sender-contact"] as const,
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Cursor-paginated list of ingestion events, newest first.
 *
 * Fetches from GET /api/ingestion/events using keyset cursor pagination.
 * Exposes infinite scroll semantics: call fetchNextPage() to load more.
 *
 * Contract (BREAKING from offset+total shape):
 * - pages: CursorPaginatedResponse<IngestionEventSummary>[]
 * - fetchNextPage: () => void
 * - hasNextPage: boolean
 * - isFetchingNextPage: boolean
 * - isLoading / isError / error
 *
 * total is NOT available — the API no longer returns a count.
 */
export function useIngestionEvents(
  filters: IngestionEventsFilters = {},
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery<
    CursorPaginatedResponse<IngestionEventSummary>,
    Error,
    { pages: CursorPaginatedResponse<IngestionEventSummary>[]; pageParams: (string | null)[] },
    ReturnType<typeof ingestionEventKeys.list>,
    string | null
  >({
    queryKey: ingestionEventKeys.list(filters),
    queryFn: ({ pageParam }) =>
      listIngestionEvents({ ...filters, cursor: pageParam ?? undefined }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.meta.has_more ? (lastPage.meta.next_cursor ?? null) : null,
    staleTime: 30_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fan-out sessions for a single ingestion event request_id.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/sessions.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventSessions(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.sessions(requestId),
    queryFn: () => getIngestionEventSessions(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Cost/token rollup for a single ingestion event request_id.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/rollup.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventRollup(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.rollup(requestId),
    queryFn: () => getIngestionEventRollup(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Parallel fetch of sessions + rollup for a single ingestion event.
 *
 * Both queries share the same requestId and run concurrently.
 * Only fires when requestId is non-empty.
 */
export function useIngestionEventLineage(
  requestId: string,
  options?: { enabled?: boolean },
) {
  const enabled = !!requestId && options?.enabled !== false;
  const sessions = useIngestionEventSessions(requestId, { enabled });
  const rollup = useIngestionEventRollup(requestId, { enabled });
  return { sessions, rollup };
}

/**
 * Replay attempt history for a single ingestion event.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/replays.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventReplays(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.replays(requestId),
    queryFn: () => getIngestionEventReplays(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Resolved contact name for the sender_identity of an ingestion event.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/sender-contact.
 * Returns resolved=false on miss — always 200 from the backend.
 * Only enabled when a non-empty requestId is provided.
 */
export function useIngestionEventSenderContact(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.senderContact(requestId),
    queryFn: () => getIngestionEventSenderContact(requestId),
    staleTime: 60_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}
