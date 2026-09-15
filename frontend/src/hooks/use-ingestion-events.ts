/**
 * TanStack Query hooks for the ingestion event lineage Timeline tab.
 *
 * Query key strategy:
 * - ingestionEventKeys.list(filters)          → cursor-paginated IngestionEventSummary list
 * - ingestionEventKeys.sessions(requestId)     → sessions for a given request_id
 * - ingestionEventKeys.replays(requestId)      → replay history from public.audit_log
 * - ingestionEventKeys.senderContact(requestId) → resolved contact name for sender_identity
 * - ingestionEventKeys.detail(requestId)        → full event detail with lifecycle_state/decomposition_output
 * - ingestionEventKeys.payload(requestId)      → raw inbound payload (audit-gated)
 *
 * Stale time of 30s matches the spec for Timeline tab data freshness.
 *
 * The list preserves the cursor-page result shape while polling only its live
 * head. Loaded history stays fixed until the owner returns to the live head.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  listIngestionEvents,
  getIngestionEvent,
  getIngestionEventSessions,
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
  IngestionHistogramBucketSize,
  IngestionHistogramParams,
  IngestionHistogramResponse,
  IngestionWindowRollup,
  IngestionWindowRollupParams,
} from "@/api/index.ts";

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

/**
 * Reconciliation fallback for active ingestion reads. Bus events provide
 * earlier invalidation, while this also covers a disconnected bus or an
 * event coalesced into an in-flight read whose snapshot predates that event.
 * Historical pages and audit-gated payloads never poll.
 */
const INGESTION_EVENTS_POLL_DEFAULT_MS = 30_000;

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
 * Auto-refetches every 30s so the ledger and live-status badge stay honest.
 */
export function useIngestionEvents(
  filters: IngestionEventsFilters = {},
  options?: { enabled?: boolean; refetchInterval?: number | false },
) {
  type Page = CursorPaginatedResponse<IngestionEventSummary>;
  const enabled = options?.enabled !== false;
  const scope = JSON.stringify(filters);
  const [history, setHistory] = useState<{
    scope: string;
    pages: Page[];
    pageParams: (string | null)[];
  } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [olderError, setOlderError] = useState(false);
  const olderRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    setHistory(null);
  }, [scope]);
  useEffect(() => {
    setLoadingMore(false);
    setOlderError(false);
    return () => {
      olderRequest.current?.abort();
      olderRequest.current = null;
    };
  }, [scope, enabled]);

  const head = useQuery({
    queryKey: ingestionEventKeys.list(filters),
    queryFn: ({ signal }) => listIngestionEvents(filters, signal),
    staleTime: 30_000,
    refetchInterval:
      options?.refetchInterval !== undefined
        ? options.refetchInterval
        : INGESTION_EVENTS_POLL_DEFAULT_MS,
    enabled,
    // Never-blank list (JARVIS audit move 10): keep the previous filter's
    // pages visible while a filter change re-keys the query and refetches.
    placeholderData: (prev) => prev,
  });
  useEffect(() => {
    if (head.isPlaceholderData || !head.data) return;
    const refreshed = new Map(head.data.data.map(event => [event.id, event]));
    // Persist observed updates so an event cannot revert to its original
    // snapshot state when newer arrivals push it out of the live head.
    setHistory(previous => {
      if (!previous || previous.scope !== scope) return previous;
      let changed = false;
      const pages = previous.pages.map(page => {
        const data = page.data.map(event => {
          const latest = refreshed.get(event.id);
          if (!latest || latest === event) return event;
          changed = true;
          return latest;
        });
        return { ...page, data };
      });
      return changed ? { ...previous, pages } : previous;
    });
  }, [head.data, head.isPlaceholderData, scope]);
  const snapshot = history?.scope === scope ? history : null;
  const pages = useMemo(
    () => snapshot?.pages ?? (head.data ? [head.data] : []),
    [snapshot, head.data],
  );
  const pageParams = snapshot?.pageParams ?? [null];
  const lastPage = pages.at(-1);
  const cursor = lastPage?.meta.has_more ? lastPage.meta.next_cursor : null;

  async function fetchNextPage() {
    if (!enabled || head.isPlaceholderData || !cursor || olderRequest.current) return;
    const controller = new AbortController();
    olderRequest.current = controller;
    // Commit before the fetch: refreshes cannot move the reader or change
    // the retained cursor if this older-page request fails.
    setHistory({ scope, pages, pageParams });
    setLoadingMore(true);
    setOlderError(false);
    try {
      const page = await listIngestionEvents({ ...filters, cursor }, controller.signal);
      if (controller.signal.aborted) return;
      // A head refresh may have reconciled the retained pages while this
      // request was in flight. Append to that state, not the captured pages.
      setHistory(previous => previous?.scope === scope ? {
        ...previous,
        pages: [...previous.pages, page],
        pageParams: [...previous.pageParams, cursor],
      } : previous);
    } catch {
      if (!controller.signal.aborted) setOlderError(true);
    } finally {
      if (olderRequest.current === controller) {
        olderRequest.current = null;
        setLoadingMore(false);
      }
    }
  }

  const committedIds = snapshot && new Set(snapshot.pages.flatMap(page => page.data.map(event => event.id)));
  const newCount = enabled && committedIds
    ? (head.data?.data.filter(event => !committedIds.has(event.id)).length ?? 0)
    : 0;
  function showNewEvents() {
    olderRequest.current?.abort();
    olderRequest.current = null;
    setHistory(null);
    setLoadingMore(false);
    setOlderError(false);
  }

  return {
    ...head,
    data: pages.length ? { pages, pageParams } : undefined,
    hasNextPage: enabled && !!cursor && !head.isPlaceholderData,
    isFetchingNextPage: enabled && loadingMore,
    isFetchNextPageError: enabled && olderError,
    fetchNextPage,
    newCount,
    isFollowingLive: snapshot === null,
    latestReceivedAt: head.data?.data[0]?.received_at ?? null,
    showNewEvents,
  };
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
    queryFn: ({ signal }) => getIngestionEventSessions(requestId, signal),
    staleTime: 30_000,
    refetchInterval: INGESTION_EVENTS_POLL_DEFAULT_MS,
    enabled: !!requestId && options?.enabled !== false,
  });
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
    queryFn: ({ signal }) => getIngestionEventReplays(requestId, signal),
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
    queryFn: ({ signal }) => getIngestionEventSenderContact(requestId, signal),
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
    queryFn: ({ signal }) => getIngestionEventPayload(requestId, signal),
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
    queryFn: ({ signal }) => getIngestionEvent(requestId, signal),
    staleTime: 30_000,
    refetchInterval: INGESTION_EVENTS_POLL_DEFAULT_MS,
    enabled: !!requestId && options?.enabled !== false,
  });
}

/**
 * Aggregate event/session/cost counts for the active filter window.
 *
 * Fetches from GET /api/ingestion/rollup with the same filter params as
 * GET /api/ingestion/events. ``cost`` is a known-priced subtotal when pricing
 * is available; ``unpriced_session_count`` makes omitted session coverage
 * explicit.
 *
 * The query is disabled by default — pass `enabled: true` to activate.
 */
export function useIngestionWindowRollup(
  params: IngestionWindowRollupParams = {},
  options?: { enabled?: boolean },
) {
  return useQuery<IngestionWindowRollup>({
    queryKey: ingestionEventKeys.windowRollup(params),
    queryFn: ({ signal }) => getIngestionWindowRollup(params, signal),
    staleTime: 30_000,
    refetchInterval: INGESTION_EVENTS_POLL_DEFAULT_MS,
    enabled: options?.enabled !== false,
  });
}

const NEXT_HISTOGRAM_BUCKET: Record<IngestionHistogramBucketSize, IngestionHistogramBucketSize | null> = {
  "1m": "5m",
  "5m": "1h",
  "1h": null,
};

function isHistogramRangeError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    (error as { status?: unknown }).status === 422
  );
}

function coarserHistogramParams(
  params: IngestionHistogramParams,
): IngestionHistogramParams | null {
  const bucket = params.bucket ?? "1m";
  const nextBucket = NEXT_HISTOGRAM_BUCKET[bucket];
  return nextBucket ? { ...params, bucket: nextBucket } : null;
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
 * range/bucket combination is too wide (e.g. '1m' over >48h). The hook makes
 * one bounded retry at the next coarser bucket, then surfaces the failure
 * honestly if that fallback also fails. (Trace-scoped queries auto-escalate
 * the bucket server-side instead of 422ing — see the endpoint docstring.)
 */
export function useIngestionEventsHistogram(
  params: IngestionHistogramParams,
  options?: { enabled?: boolean },
) {
  return useQuery<IngestionHistogramResponse>({
    queryKey: ingestionEventKeys.histogram(params),
    queryFn: async ({ signal }) => {
      try {
        return await getIngestionEventsHistogram(params, signal);
      } catch (error) {
        const fallbackParams = isHistogramRangeError(error)
          ? coarserHistogramParams(params)
          : null;
        if (signal.aborted || !fallbackParams) throw error;

        // Intentionally one request only: a second 422 must remain an
        // unavailable histogram, not fan out through progressively coarser
        // guesses or React Query's generic retry loop.
        return getIngestionEventsHistogram(fallbackParams, signal);
      }
    },
    staleTime: 30_000,
    retry: false,
    refetchInterval: INGESTION_EVENTS_POLL_DEFAULT_MS,
    enabled:
      (!!params.trace_id || (!!params.from && !!params.to)) && options?.enabled !== false,
  });
}
