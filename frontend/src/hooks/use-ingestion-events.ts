/**
 * TanStack Query hooks for the ingestion event lineage Timeline tab.
 *
 * Query key strategy:
 * - ingestionEventKeys.list(filters)          → cursor-paginated IngestionEventSummary list
 * - ingestionEventKeys.sessions(requestId)     → sessions for a given request_id
 * - ingestionEventKeys.rollup(requestId)       → cost/token rollup for a request_id
 * - ingestionEventKeys.replays(requestId)      → replay history from public.audit_log
 * - ingestionEventKeys.senderContact(requestId) → resolved contact name for sender_identity
 * - ingestionEventKeys.detail(requestId)        → full event detail with lifecycle_state/decomposition_output
 * - ingestionEventKeys.payload(requestId)      → raw inbound payload (audit-gated)
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
  getIngestionEvent,
  getIngestionEventSessions,
  getIngestionEventRollup,
  getIngestionWindowRollup,
  getIngestionEventsHistogram,
  getIngestionEventReplays,
  getIngestionEventSenderContact,
  getIngestionEventPayload,
} from "@/api/index.ts";
import type {
  CursorPaginatedResponse,
  IngestionEventsParams,
  IngestionEventSummary,
  IngestionHistogramParams,
  IngestionHistogramResponse,
  IngestionWindowRollup,
  IngestionWindowRollupParams,
} from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

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
  detail: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "detail"] as const,
  payload: (requestId: string) =>
    [...ingestionEventKeys.all, requestId, "payload"] as const,
  windowRollup: (params: IngestionWindowRollupParams) =>
    ["ingestion", "window-rollup", params] as const,
  histogram: (params: IngestionHistogramParams) =>
    ["ingestion", "events-histogram", params] as const,
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
 *
 * The fleet event bus invalidates this query-key prefix on each ingestion
 * event, so the default poll is a bus-aware reconciliation sweep. Callers can
 * still provide an explicit interval (or `false`) for focused views.
 */
export function useIngestionEvents(
  filters: IngestionEventsFilters = {},
  options?: { enabled?: boolean; refetchInterval?: number | false },
) {
  const busAwareInterval = useBusAwarePollInterval();

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
    // Refetch only the first page at the interval; infinite queries refetch all
    // loaded pages but we only need freshness from the newest (first) page.
    refetchInterval: options?.refetchInterval ?? busAwareInterval,
    enabled: options?.enabled !== false,
    // Never-blank list (JARVIS audit move 10): keep the previous filter's
    // pages visible while a filter change re-keys the query and refetches.
    placeholderData: (prev) => prev,
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

/**
 * Raw inbound payload for an ingestion event.
 *
 * Fetches from GET /api/ingestion/events/{requestId}/payload.
 * Gated by audit log — access is recorded server-side.
 * Returns 403 when the caller lacks payload-access grant; callers must
 * handle that via the error object and render the gated/unavailable state.
 *
 * Only enabled when a non-empty requestId is provided and `enabled` is true
 * (callers should not fetch until the user explicitly requests the payload tab).
 */
export function useIngestionEventPayload(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.payload(requestId),
    queryFn: () => getIngestionEventPayload(requestId),
    staleTime: 120_000, // payload rarely changes; longer stale time acceptable
    retry: false,       // don't retry 403 — the gated state is expected
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Full ingestion event detail — augments the list-row summary with lifecycle_state
 * and decomposition_output from message_inbox (joined via the switchboard pool).
 *
 * Fetches from GET /api/ingestion/events/{requestId}.
 * Both new fields are null when the switchboard pool is unavailable or the row
 * has been pruned — callers should render gracefully in either case.
 */
export function useIngestionEventDetail(
  requestId: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ingestionEventKeys.detail(requestId),
    queryFn: () => getIngestionEvent(requestId),
    staleTime: 30_000,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Aggregate event/session/cost counts for the active filter window.
 *
 * Fetches from GET /api/ingestion/rollup with the same filter params as
 * GET /api/ingestion/events.  The ``cost`` field is always null until
 * cost-per-event data is available (see follow-up bead).
 *
 * The query is disabled by default — pass `enabled: true` to activate.
 */
export function useIngestionWindowRollup(
  params: IngestionWindowRollupParams = {},
  options?: { enabled?: boolean },
) {
  return useQuery<IngestionWindowRollup>({
    queryKey: ingestionEventKeys.windowRollup(params),
    queryFn: () => getIngestionWindowRollup(params),
    staleTime: 30_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Per-minute (or coarser) ingestion event counts by status for a time window.
 *
 * Fetches from GET /api/ingestion/events/histogram — the data source for a
 * status-aware timeline hour strip (bu-4utdw.7 wires this into HourFlameStrip;
 * this hook is plumbing only). `params.from` and `params.to` are required
 * UNLESS `params.trace_id` is set — a trace-scoped query auto-widens to the
 * trace's own event bounds server-side (bu-1f81d), so the query is enabled
 * whenever either the window (`from` and `to`) or `trace_id` is present; it
 * is disabled only when neither is available, so callers never fire an
 * unbounded aggregate scan.
 *
 * The backend enforces a bucket-count guardrail and returns 422 when the
 * range/bucket combination is too wide (e.g. '1m' over >48h) — callers
 * should retry with a coarser `bucket` on error rather than treating it as a
 * generic failure. (Trace-scoped queries auto-escalate the bucket
 * server-side instead of 422ing — see the endpoint docstring.)
 */
export function useIngestionEventsHistogram(
  params: IngestionHistogramParams,
  options?: { enabled?: boolean },
) {
  return useQuery<IngestionHistogramResponse>({
    queryKey: ingestionEventKeys.histogram(params),
    queryFn: () => getIngestionEventsHistogram(params),
    staleTime: 30_000,
    enabled:
      (!!params.trace_id || (!!params.from && !!params.to)) && options?.enabled !== false,
  });
}
