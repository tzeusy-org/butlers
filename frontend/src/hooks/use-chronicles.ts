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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getChroniclerAggregateByCategory,
  getChroniclerAggregateByDay,
  getChroniclerDayClose,
  getChroniclerEpisode,
  getChroniclerEpisodeCorrections,
  getChroniclerEpisodeEvents,
  getChroniclerEpisodes,
  getChroniclerEvents,
  getChroniclerSourceState,
  postChroniclerEpisodeExplain,
} from "@/api/client.ts";
import type {
  ChroniclerAggregateByCategoryParams,
  ChroniclerAggregateByDayParams,
  ChroniclerDayCloseParams,
  ChroniclerEpisodesParams,
  ChroniclerEventsParams,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const chroniclesKeys = {
  all: ["chronicles"] as const,
  episodes: (params?: ChroniclerEpisodesParams) =>
    [...chroniclesKeys.all, "episodes", params] as const,
  episode: (id: string) => [...chroniclesKeys.all, "episode", id] as const,
  episodeEvents: (id: string) => [...chroniclesKeys.all, "episode-events", id] as const,
  episodeCorrections: (id: string) =>
    [...chroniclesKeys.all, "episode-corrections", id] as const,
  byCategory: (params: ChroniclerAggregateByCategoryParams) =>
    [...chroniclesKeys.all, "aggregate-by-category", params] as const,
  byDay: (params: ChroniclerAggregateByDayParams) =>
    [...chroniclesKeys.all, "aggregate-by-day", params] as const,
  sourceState: () => [...chroniclesKeys.all, "source-state"] as const,
  dayClose: (params: ChroniclerDayCloseParams) =>
    [...chroniclesKeys.all, "day-close", params] as const,
  pointEvents: (params?: ChroniclerEventsParams) =>
    [...chroniclesKeys.all, "point-events", params] as const,
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
 * Fetch category aggregates only for a time window.
 *
 * Use this when you only need the by-category breakdown and do not need
 * the by-day series. Avoids the extra /aggregate/by-day request.
 */
export function useChroniclesByCategory(
  params: ChroniclerAggregateByCategoryParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.byCategory(params),
    queryFn: () => getChroniclerAggregateByCategory(params),
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

/**
 * Fetch a single Chronicler episode by ID (corrected view).
 *
 * Returns undefined while loading. Throws ApiError with status 404
 * if the episode is not found.
 *
 * Disabled when episodeId is falsy — callers may pass null/undefined when
 * no episode is selected without triggering a fetch.
 */
export function useChroniclerEpisode(
  episodeId: string | null | undefined,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.episode(episodeId ?? ""),
    queryFn: () => getChroniclerEpisode(episodeId!),
    enabled: options?.enabled !== false && !!episodeId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * Fetch point events linked to an episode.
 *
 * Returns an empty array when there are no linked events.
 * Disabled when episodeId is falsy.
 */
export function useChroniclerEpisodeEvents(
  episodeId: string | null | undefined,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.episodeEvents(episodeId ?? ""),
    queryFn: () => getChroniclerEpisodeEvents(episodeId!),
    enabled: options?.enabled !== false && !!episodeId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * Fetch the correction history for an episode (sorted by created_at DESC).
 *
 * Returns an empty array when there are no corrections.
 * Disabled when episodeId is falsy.
 */
export function useChroniclerEpisodeCorrections(
  episodeId: string | null | undefined,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.episodeCorrections(episodeId ?? ""),
    queryFn: () => getChroniclerEpisodeCorrections(episodeId!),
    enabled: options?.enabled !== false && !!episodeId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * Mutation hook for the Tier-2 "Explain this episode" per-episode drilldown.
 *
 * This calls POST /api/chronicler/episodes/{id}/explain, which assembles a
 * token-bounded bundle (episode detail + linked events + correction history)
 * and invokes the LLM. It is the proper per-episode Tier-2 path per RFC 0014 §D5.
 *
 * Constraints:
 *   - Explicit-click triggered only (never automatic).
 *   - Rate-limited by the backend (1 per 24 h per episode).
 *   - UI disabled while the rate-limit window is active.
 *   - Sensitive/restricted episodes return 403 — the ExplainButton is hidden for those.
 *
 * Rate-limit detection: when the mutation fails with ApiError status 429
 * and code "episode_explain_rate_limited", surface retry_after_seconds from the
 * error details and disable the button.
 *
 * On success, invalidates the chronicles query cache so any adjacent widget
 * picks up fresh data automatically.
 */
export function useChroniclerExplain() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (episodeId: string) => postChroniclerEpisodeExplain(episodeId),
    onSuccess: () => {
      // Invalidate all chronicles queries so any adjacent prose widget refreshes.
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.all });
    },
  });
}

/**
 * Fetch point events for the scrubber.
 *
 * Fetches up to 500 point events in a time window. Used by the Scrubber to
 * snap the playhead to the nearest known event timestamp (D12).
 *
 * Privacy: sensitive point events (e.g. OwnTracks location) are included
 * because their coordinates are needed for map rendering. The caller is
 * responsible for privacy-appropriate display.
 */
export function useChroniclesPointEvents(
  params?: ChroniclerEventsParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.pointEvents(params),
    queryFn: () => getChroniclerEvents(params),
    refetchInterval: options?.refetchInterval ?? 30_000,
    enabled: options?.enabled !== false,
  });
}
