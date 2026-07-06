/**
 * TanStack Query hooks for the sessions API.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import type { ApiResponse, SessionDetail, SessionParams, SessionSummary } from "@/api/types.ts";
import { POLL_BUS_RECONCILE_MS, POLL_RUNNING_SESSION_MS } from "@/lib/poll-policy";

import {
  getButlerSession,
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
  return useQuery({
    queryKey: ["sessions", params],
    queryFn: () => getSessions(params),
    // Live path: the fleet event bus (bu-86c4c.8) invalidates ["sessions"] on
    // every session started/ended event. The default poll is now a 5-minute
    // reconciliation sweep — a safety net, not the primary update path.
    // Callers that pass their own refetchInterval (e.g. an explicit
    // auto-refresh control) are unaffected.
    refetchInterval: options?.refetchInterval ?? POLL_BUS_RECONCILE_MS,
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
  return useQuery({
    queryKey: ["session-aggregate", filterParams],
    queryFn: () => getSessionAggregate(filterParams),
    // See useSessions above: fleet-event-bus-driven, poll is now a safety net.
    refetchInterval: options?.refetchInterval ?? POLL_BUS_RECONCILE_MS,
  });
}

/** Fetch a paginated list of sessions for a single butler. */
export function useButlerSessions(name: string, params?: SessionParams) {
  return useQuery({
    queryKey: ["butler-sessions", name, params],
    queryFn: () => getButlerSessions(name, params),
    enabled: !!name,
    // See useSessions above: fleet-event-bus-driven, poll is now a safety net.
    refetchInterval: POLL_BUS_RECONCILE_MS,
    placeholderData: (prev) => prev,
  });
}

/** Fetch full session detail for a specific butler session. */
export function useSessionDetail(butler: string, id: string | null) {
  return useQuery({
    queryKey: ["session-detail", butler, id],
    queryFn: () => getButlerSession(butler, id!),
    enabled: !!butler && !!id,
    // See POLL_RUNNING_SESSION_MS: primary path for a running session's
    // streaming tool-call tail, off entirely once terminal.
    refetchInterval: sessionDetailRefetchInterval,
  });
}

/**
 * Fetch full session detail cross-butler (GET /api/session/:id) — the ONE
 * query key SessionDetailPage uses (bu-qvnce.5, pursuit move 5 slice 2).
 *
 * Previously SessionDetailPage hand-rolled this as an inline useQuery keyed
 * ["session-detail-global", id] with no refetch policy and no bus coverage —
 * the exact gap behind "the palette's own trigger->session flow lands on a
 * frozen 'Running' page" (GlobalActionsRegistrar navigates here straight
 * after triggering a butler). event-cache-registry.ts's sessionPatch now
 * invalidates this key too; see event-cache-manifest.ts.
 */
export function useGlobalSessionDetail(id: string | null) {
  return useQuery({
    queryKey: ["session-detail-global", id],
    queryFn: () => getSession(id!),
    enabled: !!id,
    refetchInterval: sessionDetailRefetchInterval,
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
 * to `useSessionDetail` (`["session-detail", butler, id]`) and calls the same
 * `getButlerSession` — the exact "existing session-detail affordance" the
 * drawer uses — so a row already rendered here is a cache hit, not a
 * duplicate fetch, when the user clicks through to the full drawer.
 *
 * Sessions with no `butler` (should not happen for a real row, but the field
 * is optional on the type) are skipped via `enabled: false`.
 */
export function useSessionErrorExcerpts(sessions: SessionSummary[]) {
  const results = useQueries({
    queries: sessions.map((s) => ({
      queryKey: ["session-detail", s.butler ?? "", s.id],
      queryFn: () => getButlerSession(s.butler!, s.id),
      enabled: !!s.butler,
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
