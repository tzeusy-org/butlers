/**
 * TanStack Query hooks for the sessions API.
 */

import { useQuery } from "@tanstack/react-query";
import type { SessionParams } from "@/api/types.ts";

import {
  getButlerSession,
  getButlerSessions,
  getSessionAggregate,
  getSessions,
} from "@/api/index.ts";

interface SessionQueryOptions {
  refetchInterval?: number | false;
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
    refetchInterval: options?.refetchInterval ?? 5 * 60_000,
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
    refetchInterval: options?.refetchInterval ?? 5 * 60_000,
  });
}

/** Fetch a paginated list of sessions for a single butler. */
export function useButlerSessions(name: string, params?: SessionParams) {
  return useQuery({
    queryKey: ["butler-sessions", name, params],
    queryFn: () => getButlerSessions(name, params),
    enabled: !!name,
    // See useSessions above: fleet-event-bus-driven, poll is now a safety net.
    refetchInterval: 5 * 60_000,
    placeholderData: (prev) => prev,
  });
}

/** Fetch full session detail for a specific butler session. */
export function useSessionDetail(butler: string, id: string | null) {
  return useQuery({
    queryKey: ["session-detail", butler, id],
    queryFn: () => getButlerSession(butler, id!),
    enabled: !!butler && !!id,
  });
}
