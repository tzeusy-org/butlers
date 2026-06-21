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
    refetchInterval: options?.refetchInterval ?? 30_000,
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
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

/** Fetch a paginated list of sessions for a single butler. */
export function useButlerSessions(name: string, params?: SessionParams) {
  return useQuery({
    queryKey: ["butler-sessions", name, params],
    queryFn: () => getButlerSessions(name, params),
    enabled: !!name,
    refetchInterval: 30_000,
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
