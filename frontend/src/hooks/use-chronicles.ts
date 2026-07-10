/**
 * TanStack Query hooks for the Chronicler dashboard API.
 *
 * Query-key strategy:
 * - chroniclesKeys.episodes(params)         → PaginatedResponse<ChroniclerEpisode>
 * - chroniclesKeys.episodesInfinite(params) → InfiniteData<PaginatedResponse<ChroniclerEpisode>>
 * - chroniclesKeys.byCategory(params)       → ApiResponse<ChroniclerCategoryBuckets>
 * - chroniclesKeys.byDay(params)            → ChroniclerAggregateByDayRow[]
 * - chroniclesKeys.sourceState()            → ApiResponse<ChroniclerSourceStateRow[]>
 * - chroniclesKeys.dayClose(params)         → ChroniclerDayCloseResponse
 *
 * Privacy defaults:
 * - Episodes: restricted rows are excluded by the server unless the caller
 *   passes privacy_tier explicitly. The hook does NOT inject privacy_tier —
 *   that filter is honoured at the API layer per spec §Map Render Privacy.
 * - Aggregates: the server excludes restricted by default (normal + sensitive).
 *
 * Tombstone defaults: include_tombstoned defaults to false in all hooks.
 */

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createChroniclerRoutine,
  deleteChroniclerRoutine,
  getChroniclerAggregateByCategory,
  getChroniclerAggregateByDay,
  getChroniclerDayClose,
  getChroniclerEpisode,
  getChroniclerEpisodeCorrections,
  getChroniclerEpisodeEvents,
  getChroniclerEpisodes,
  getChroniclerEvents,
  getChroniclerRollups,
  getChroniclerRoutines,
  getChroniclerSourceState,
  postChroniclerEpisodeExplain,
  submitChroniclerEpisodeCorrection,
  updateChroniclerRoutine,
} from "@/api/client.ts";
import type {
  ChroniclerAggregateByCategoryParams,
  ChroniclerAggregateByDayParams,
  ChroniclerCreateRoutineRequest,
  ChroniclerDayCloseParams,
  ChroniclerEpisodesParams,
  ChroniclerEventsParams,
  ChroniclerRollupsParams,
  ChroniclerUpdateRoutineRequest,
  SubmitCorrectionRequest,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const chroniclesKeys = {
  all: ["chronicles"] as const,
  episodes: (params?: ChroniclerEpisodesParams) =>
    [...chroniclesKeys.all, "episodes", params] as const,
  episodesInfinite: (params?: Omit<ChroniclerEpisodesParams, "offset">) =>
    [...chroniclesKeys.all, "episodes-infinite", params] as const,
  episode: (id: string) => [...chroniclesKeys.all, "episode", id] as const,
  episodeEvents: (id: string) => [...chroniclesKeys.all, "episode-events", id] as const,
  episodeCorrections: (id: string) =>
    [...chroniclesKeys.all, "episode-corrections", id] as const,
  byCategory: (params: ChroniclerAggregateByCategoryParams) =>
    [...chroniclesKeys.all, "aggregate-by-category", params] as const,
  byDay: (params: ChroniclerAggregateByDayParams) =>
    [...chroniclesKeys.all, "aggregate-by-day", params] as const,
  sourceState: () => [...chroniclesKeys.all, "source-state"] as const,
  rollups: (params: ChroniclerRollupsParams) =>
    [...chroniclesKeys.all, "rollups", params] as const,
  dayClose: (params: ChroniclerDayCloseParams) =>
    [...chroniclesKeys.all, "day-close", params] as const,
  pointEvents: (params?: ChroniclerEventsParams) =>
    [...chroniclesKeys.all, "point-events", params] as const,
  routines: (params?: { enabled_only?: boolean }) =>
    [...chroniclesKeys.all, "routines", params ?? {}] as const,
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
 * Fetch all Chronicler episodes for a window using infinite pagination.
 *
 * Uses useInfiniteQuery so that ALL loaded pages remain active and are
 * refetched on each interval — including page 0. This means newly recorded
 * episodes that fall in the first page remain visible even after the user
 * has clicked "Load more".
 *
 * pageParam is the offset (number). TanStack Query v5 requires initialPageParam.
 */
export function useChroniclesEpisodesInfinite(
  params?: Omit<ChroniclerEpisodesParams, "offset">,
  options?: ChroniclesHookOptions,
) {
  return useInfiniteQuery({
    queryKey: chroniclesKeys.episodesInfinite(params),
    queryFn: ({ pageParam }) =>
      getChroniclerEpisodes({ ...params, offset: pageParam as number }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => {
      if (!lastPage.meta.has_more) return undefined;
      return lastPage.meta.offset + lastPage.meta.limit;
    },
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
 * Fetch daily rollups + anomaly flags for one local day or an inclusive range.
 *
 * A settled past window never changes, so callers driving a fixed historical
 * range (e.g. the trend widget) should pass `refetchInterval: false`.
 */
export function useChroniclesRollups(
  params: ChroniclerRollupsParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.rollups(params),
    queryFn: () => getChroniclerRollups(params),
    refetchInterval: options?.refetchInterval ?? false,
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
 * Submit an episode correction (JARVIS audit move 6, bu-86c4c.15 —
 * "episode corrections on chronicles, a manifesto-binding promise").
 *
 * Calls the real POST /api/chronicler/episodes/{id}/corrections endpoint,
 * which the Chronicler's own read path (`v_episodes_corrected`, consumed by
 * {@link useChroniclerEpisode}) already honors, and the same table
 * {@link useChroniclerEpisodeCorrections} already reads.
 *
 * HONEST-PENDING, not optimistic: a correction is an audit-trail entry the
 * owner is asserting as fact, not a reversible toggle, so the UI shows a real
 * pending state and waits for the server round trip rather than faking
 * immediate success. On success, invalidates both the episode detail (so the
 * corrected view refreshes) and the correction-history list for that episode.
 */
export function useSubmitEpisodeCorrection() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ episodeId, body }: { episodeId: string; body: SubmitCorrectionRequest }) =>
      submitChroniclerEpisodeCorrection(episodeId, body),
    onSuccess: (_data, { episodeId }) => {
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.episode(episodeId) });
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.episodeCorrections(episodeId) });
    },
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

// ---------------------------------------------------------------------------
// Routines (bu-whhll.9 miner rows + bu-whhll.11 owner-declared schedule)
// ---------------------------------------------------------------------------

/**
 * Fetch owner-reviewable weekly routines (mined + declared).
 *
 * The settings surface must distinguish a FAILED fetch from an empty list:
 * an errored query renders a degraded note, never a calm "no schedule yet"
 * (the truth-amnesty rule, CLAUDE.md API conventions). Callers read
 * `isError`/`error` and gate their empty state on it.
 */
export function useChroniclesRoutines(
  params?: { enabled_only?: boolean },
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.routines(params),
    queryFn: () => getChroniclerRoutines(params),
    refetchInterval: options?.refetchInterval ?? false,
    enabled: options?.enabled !== false,
  });
}

/** Declare an owner work schedule (origin='declared'). Invalidates the list. */
export function useCreateChroniclesRoutine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChroniclerCreateRoutineRequest) => createChroniclerRoutine(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.all });
    },
  });
}

/** Enable/disable, rename, or re-schedule a routine. Invalidates the list. */
export function useUpdateChroniclesRoutine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      routineId,
      body,
    }: {
      routineId: string;
      body: ChroniclerUpdateRoutineRequest;
    }) => updateChroniclerRoutine(routineId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.all });
    },
  });
}

/** Delete a declared routine. Invalidates the list. */
export function useDeleteChroniclesRoutine() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (routineId: string) => deleteChroniclerRoutine(routineId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chroniclesKeys.all });
    },
  });
}
