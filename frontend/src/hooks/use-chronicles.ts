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
  getChroniclerBalance,
  getChroniclerCorrectionPrompts,
  getChroniclerEpisode,
  getChroniclerEpisodeCorrections,
  getChroniclerEpisodeEvents,
  getChroniclerEpisodes,
  getChroniclerEvidenceChain,
  getChroniclerEvents,
  getChroniclerRoutines,
  getChroniclerRollups,
  getChroniclerSourceState,
  getChroniclerTrends,
  getChroniclerWhoYouWereWith,
  postChroniclerEpisodeExplain,
  submitChroniclerEpisodeCorrection,
  updateChroniclerRoutine,
} from "@/api/client.ts";
import type {
  ChroniclerAggregateByCategoryParams,
  ChroniclerAggregateByDayParams,
  ChroniclerCreateRoutineRequest,
  ChroniclerBalanceParams,
  ChroniclerCorrectionPromptsParams,
  ChroniclerDayCloseParams,
  ChroniclerEpisodesParams,
  ChroniclerEventsParams,
  ChroniclerUpdateRoutineRequest,
  ChroniclerTrendsParams,
  ChroniclerRollupsParams,
  ChroniclerWhoYouWereWithParams,
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
  balance: (params: ChroniclerBalanceParams) =>
    [...chroniclesKeys.all, "balance", params] as const,
  trends: (params?: ChroniclerTrendsParams) =>
    [...chroniclesKeys.all, "trends", params] as const,
  rollups: (params: ChroniclerRollupsParams) =>
    [...chroniclesKeys.all, "rollups", params] as const,
  whoYouWereWith: (params: ChroniclerWhoYouWereWithParams) =>
    [...chroniclesKeys.all, "who-you-were-with", params] as const,
  evidenceChain: (episodeId: string) =>
    [...chroniclesKeys.all, "evidence-chain", episodeId] as const,
  correctionPrompts: (params: ChroniclerCorrectionPromptsParams) =>
    [...chroniclesKeys.all, "correction-prompts", params] as const,
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
 * Fetch the target day's per-lane balance vs the owner's rolling baseline.
 *
 * A settled past day never changes, so polling defaults to off. The response
 * carries `balance_source_error` (degraded envelope) plus per-lane
 * `unavailable` flags — consumers MUST render those as degraded, never as a
 * truthful zero.
 */
export function useChroniclesBalance(
  params: ChroniclerBalanceParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.balance(params),
    queryFn: () => getChroniclerBalance(params),
    refetchInterval: options?.refetchInterval ?? false,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fetch week/month-grained per-lane balance trends, streaks, and anomalies.
 *
 * Response carries `trends_source_error` (degraded envelope). Polling off by
 * default — a settled historical window never changes.
 */
export function useChroniclesTrends(
  params?: ChroniclerTrendsParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.trends(params),
    queryFn: () => getChroniclerTrends(params),
    refetchInterval: options?.refetchInterval ?? false,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fetch daily rollups + anomaly flags with their optional once-daily LLM
 * narrative for one local day or an inclusive range (GET /chronicler/rollups).
 *
 * Response carries `rollups_source_error` (degraded envelope). A day's
 * `narrative` / a flag's `narrative` is `null` when the labeling pass has not
 * run — a legitimate absence, never an error. Polling off by default — a
 * settled historical day never changes.
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
 * Fetch the resolved people the owner spent time with in a window.
 *
 * Response carries `who_you_were_with_source_error` (the chronicler query
 * failed → empty companions) and `companion_names_unavailable` (only the
 * relationship-butler name lookup degraded; identity/duration still trusted).
 */
export function useChroniclesWhoYouWereWith(
  params: ChroniclerWhoYouWereWithParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.whoYouWereWith(params),
    queryFn: () => getChroniclerWhoYouWereWith(params),
    refetchInterval: options?.refetchInterval ?? false,
    enabled: options?.enabled !== false,
  });
}

/**
 * Fetch the evidence chain backing an activity ("why is this counted?").
 *
 * Disabled when episodeId is falsy — callers pass null when no activity is
 * selected without triggering a fetch.
 */
export function useChroniclerEvidenceChain(
  episodeId: string | null | undefined,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.evidenceChain(episodeId ?? ""),
    queryFn: () => getChroniclerEvidenceChain(episodeId!),
    enabled: options?.enabled !== false && !!episodeId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * Fetch the window's low-confidence activities as correction prompts.
 *
 * The write path reuses the existing corrections overlay
 * ({@link useSubmitEpisodeCorrection}); once an override exists the prompt
 * drops off the list server-side.
 */
export function useChroniclesCorrectionPrompts(
  params: ChroniclerCorrectionPromptsParams,
  options?: ChroniclesHookOptions,
) {
  return useQuery({
    queryKey: chroniclesKeys.correctionPrompts(params),
    queryFn: () => getChroniclerCorrectionPrompts(params),
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
