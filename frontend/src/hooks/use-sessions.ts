/**
 * TanStack Query hooks for the sessions API.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import type { ApiResponse, SessionDetail, SessionParams, SessionSummary } from "@/api/types.ts";
import { POLL_RUNNING_SESSION_MS } from "@/lib/poll-policy";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

import {
  getButlerSessions,
  getSession,
  getSessionAggregate,
  getSessions,
} from "@/api/index.ts";

interface SessionQueryOptions {
  refetchInterval?: number | false;
}

/**
 * refetchInterval for a single session-detail query: POLL_RUNNING_SESSION_MS
 * (the primary update path) while the session hasn't reached a terminal
 * state, `false` once it has — see POLL_RUNNING_SESSION_MS's doc comment in
 * poll-policy.ts for why a running session needs its own short poll rather
 * than leaning on the bus (no per-tool-call bus event exists).
 */
function sessionDetailRefetchInterval(query: {
  state: { data?: ApiResponse<SessionDetail> };
}): number | false {
  const session = query.state.data?.data;
  if (!session) return false;
  return session.success === null ? POLL_RUNNING_SESSION_MS : false;
}

/** Fetch a keyset-paginated list of sessions across all butlers. */
export function useSessions(params?: SessionParams, options?: SessionQueryOptions) {
  // Live path: the fleet event bus (bu-86c4c.8) invalidates ["sessions"] on
  // every session started/ended event. The default poll is a bus-aware
  // reconciliation sweep (bu-01r64.3) — a safety net, not the primary update
  // path. Callers that pass their own refetchInterval (e.g. an explicit
  // override) are unaffected.
  const busAwareInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["sessions", params],
    queryFn: () => getSessions(params),
    refetchInterval: options?.refetchInterval ?? busAwareInterval,
    // Keep the previous page/filter's rows visible while the new cursor/filter
    // combination fetches, instead of blanking to a loading skeleton
    // (JARVIS audit move 10 — never-blank lists).
    placeholderData: (prev) => prev,
  });
}

/**
 * Fetch the window-true, filter-aware session aggregate.
 *
 * The query key intentionally OMITS `cursor` so the rollup is shared across
 * pages of the same filter set: it recomputes when filters change but not when
 * the user pages forward/back. Pass only the FILTER params here (the caller
 * should strip `cursor`/`offset`).
 */
export function useSessionAggregate(params?: SessionParams, options?: SessionQueryOptions) {
  // Defensively drop pagination fields so paging never re-keys the aggregate.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- strip pagination; key only on filters
  const { cursor, offset, limit, ...filterParams } = params ?? {};
  // See useSessions above: fleet-event-bus-driven, poll is a bus-aware safety net.
  const busAwareInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["session-aggregate", filterParams],
    queryFn: () => getSessionAggregate(filterParams),
    refetchInterval: options?.refetchInterval ?? busAwareInterval,
  });
}

/** Fetch a paginated list of sessions for a single butler. */
export function useButlerSessions(name: string, params?: SessionParams) {
  // See useSessions above: fleet-event-bus-driven, poll is a bus-aware safety net.
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["butler-sessions", name, params],
    queryFn: () => getButlerSessions(name, params),
    enabled: !!name,
    refetchInterval,
    placeholderData: (prev) => prev,
  });
}

/**
 * Fetch full session detail cross-butler (GET /api/sessions/:id) — the ONE
 * query key both SessionDetailPage AND the sessions drawer use (bu-qvnce.5
 * pursuit move 5 slice 2; drawer folded on in bu-tpudw.2).
 *
 * Global-only by design: session ids are globally unique, so resolving a
 * pinned row or a deep link never needs a `?butler=` hint (the old
 * butler-scoped `useSessionDetail` could not resolve a session that wasn't on
 * the current page, and was deleted). The backend splits 404 (id unknown
 * across reachable pools) from 503 (a pool was unreachable), so a caller can
 * render a distinct pool-down state via the thrown `ApiError.status`.
 *
 * event-cache-registry.ts's sessionPatch invalidates this key on every
 * session start/end bus event; see event-cache-manifest.ts.
 */
/**
 * Turn a list row into a truthful, explicitly partial detail response while
 * the full record is in flight. The drawer renders the identifying summary
 * plus a loading body for this response; it never presents the omitted
 * transcript/tool-call fields as known-empty data.
 */
function detailSeedFromSummary(summary: SessionSummary): ApiResponse<SessionDetail> {
  return {
    data: {
      id: summary.id,
      butler: summary.butler ?? "Unknown butler",
      prompt: summary.prompt,
      trigger_source: summary.trigger_source,
      result: null,
      tool_calls: [],
      duration_ms: summary.duration_ms,
      trace_id: null,
      request_id: summary.request_id ?? null,
      cost: null,
      started_at: summary.started_at,
      completed_at: summary.completed_at,
      success: summary.success,
      error: null,
      model: summary.model ?? null,
      input_tokens: summary.input_tokens,
      output_tokens: summary.output_tokens,
      parent_session_id: null,
      complexity: summary.complexity ?? null,
      resolution_source: null,
      process_log: null,
    },
    meta: {},
  };
}

/**
 * Fetch a global session detail, optionally showing the selected list summary
 * immediately while the full dossier is fetched. `placeholderData` keeps the
 * seed out of the cache, so a partial list row can never become a durable
 * substitute for the authoritative detail response.
 */
export function useGlobalSessionDetail(id: string | null, seed?: SessionSummary) {
  return useQuery({
    queryKey: ["session-detail-global", id],
    queryFn: () => getSession(id!),
    enabled: !!id,
    refetchInterval: sessionDetailRefetchInterval,
    placeholderData: seed ? () => detailSeedFromSummary(seed) : undefined,
  });
}

/**
 * Batch-fetch full detail for a small, bounded set of session summaries —
 * used by the Sessions pinned strip (bu-ptaub) to surface an inline error
 * excerpt for each recently-failed pinned row without waiting for the user
 * to open the drawer.
 *
 * Uses `useQueries` (the same pattern as `useGoogleAccountsHealth` /
 * `useAllPendingReviews`) so the query count tracks the live `sessions` list
 * without violating React's rules of hooks. Each query is keyed identically
 * to `useGlobalSessionDetail` (`["session-detail-global", id]`) and calls the
 * same global `getSession` — the exact affordance the drawer uses — so a row
 * already rendered here is a cache hit, not a duplicate fetch, when the user
 * clicks through to the full drawer. Global-only (no `?butler=`) matches the
 * drawer's fold onto the cross-butler endpoint (bu-tpudw.2).
 */
export function useSessionErrorExcerpts(sessions: SessionSummary[]) {
  const results = useQueries({
    queries: sessions.map((s) => ({
      queryKey: ["session-detail-global", s.id],
      queryFn: () => getSession(s.id),
      refetchInterval: sessionDetailRefetchInterval,
    })),
  });

  const errorsById = new Map<string, string | null>();
  sessions.forEach((s, i) => {
    const detail = results[i]?.data?.data;
    errorsById.set(s.id, detail?.error ?? null);
  });
  return errorsById;
}
