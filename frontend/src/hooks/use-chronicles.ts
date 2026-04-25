/**
 * TanStack Query hooks for the Chronicler dashboard API.
 *
 * Query-key strategy:
 * - chroniclesKeys.episodes(params)       → PaginatedResponse<ChroniclerEpisode>
 * - chroniclesKeys.byCategory(params)     → ApiResponse<ChroniclerCategoryBuckets>
 * - chroniclesKeys.byDay(params)          → ChroniclerAggregateByDayRow[]
 * - chroniclesKeys.sourceState()          → ApiResponse<ChroniclerSourceStateRow[]>
 * - chroniclesKeys.dayClose(params)       → ChroniclerDayCloseResponse
 *
 * Privacy defaults:
 * - Episodes: restricted rows are excluded by the server unless the caller
 *   passes privacy_tier explicitly. The hook does NOT inject privacy_tier —
 *   that filter is honoured at the API layer per spec §Map Render Privacy.
 * - Aggregates: the server excludes restricted by default (normal + sensitive).
 *
 * Tombstone defaults: include_tombstoned defaults to false in all hooks.
 */

import { useQuery } from "@tanstack/react-query";

import {
  getChroniclerAggregateByCategory,
  getChroniclerAggregateByDay,
  getChroniclerDayClose,
  getChroniclerEpisodes,
  getChroniclerSourceState,
} from "@/api/client.ts";
import type {
  ChroniclerAggregateByCategoryParams,
  ChroniclerAggregateByDayParams,
  ChroniclerDayCloseParams,
  ChroniclerEpisodesParams,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const chroniclesKeys = {
  all: ["chronicles"] as const,
  episodes: (params?: ChroniclerEpisodesParams) =>
    [...chroniclesKeys.all, "episodes", params] as const,
  byCategory: (params: ChroniclerAggregateByCategoryParams) =>
    [...chroniclesKeys.all, "aggregate-by-category", params] as const,
  byDay: (params: ChroniclerAggregateByDayParams) =>
    [...chroniclesKeys.all, "aggregate-by-day", params] as const,
  sourceState: () => [...chroniclesKeys.all, "source-state"] as const,
  dayClose: (params: ChroniclerDayCloseParams) =>
    [...chroniclesKeys.all, "day-close", params] as const,
};

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

interface ChroniclesHookOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/**
 * Fetch paginated Chronicler episodes.
 *
 * Defaults: include_tombstoned=false (not injected — server default matches).
 * Restricted episodes are excluded unless the caller includes 'restricted'
 * in params via source_name / privacy_tier (passed through to the server).
 */
export function useChroniclesEpisodes(
  params?: ChroniclerEpisodesParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.episodes(params),
    queryFn: () => getChroniclerEpisodes(params),
    refetchInterval: options?.refetchInterval ?? 30_000,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fetch category and day aggregates for a time window.
 *
 * Issues two queries in one hook:
 * - GET /api/chronicler/aggregate/by-category
 * - GET /api/chronicler/aggregate/by-day
 *
 * Both use the same time window params. Day params may add a category filter.
 * Restricted episodes are excluded by default at the server layer.
 */
export function useChroniclesAggregates(
  categoryParams: ChroniclerAggregateByCategoryParams,
  dayParams: ChroniclerAggregateByDayParams,
  options?: ChroniclesHookOptions,
) {
  const byCategory = useQuery({
    queryKey: chroniclesKeys.byCategory(categoryParams),
    queryFn: () => getChroniclerAggregateByCategory(categoryParams),
    refetchInterval: options?.refetchInterval ?? 30_000,
    enabled: options?.enabled !== false,
  });

  const byDay = useQuery({
    queryKey: chroniclesKeys.byDay(dayParams),
    queryFn: () => getChroniclerAggregateByDay(dayParams),
    refetchInterval: options?.refetchInterval ?? 30_000,
    enabled: options?.enabled !== false,
  });

  return { byCategory, byDay };
}

/**
 * Fetch source adapter state joined with projection checkpoints.
 *
 * Refetched on window focus (refetchOnWindowFocus is TanStack default: true).
 * Singleton query — no params.
 */
export function useChroniclesSourceState(options?: ChroniclesHookOptions) {
  return useQuery({
    queryKey: chroniclesKeys.sourceState(),
    queryFn: () => getChroniclerSourceState(),
    refetchInterval: options?.refetchInterval ?? 30_000,
    refetchOnWindowFocus: true,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fetch the day-close cache entry for a window.
 *
 * Returns either a fresh prose response or a stale marker.
 * Throws ApiError with status 404 if no cache entry exists for the window.
 *
 * Covers both response shapes (DayCloseFreshResponse / DayCloseStaleResponse)
 * via the ChroniclerDayCloseResponse discriminated union.
 */
export function useChroniclesDayClose(
  params: ChroniclerDayCloseParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.dayClose(params),
    queryFn: () => getChroniclerDayClose(params),
    refetchInterval: options?.refetchInterval ?? false,
    enabled: options?.enabled !== false,
  });
}
