/**
 * TanStack Query hooks for calendar workspace APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptCalendarProposal,
  dismissCalendarProposal,
  findCalendarWorkspaceTime,
  getCalendarAccounts,
  getCalendarDayBriefing,
  getCalendarMeetingPrep,
  getCalendarWorkspace,
  getCalendarWorkspaceAudit,
  getCalendarWorkspaceConflicts,
  getCalendarWorkspaceDuplicates,
  getCalendarWorkspaceEntry,
  getCalendarWorkspaceMeta,
  mutateCalendarWorkspaceButlerEvent,
  mutateCalendarWorkspaceUserEvent,
  parseCalendarQuickAdd,
  patchCalendarDedupRules,
  previewCalendarWorkspaceButlerEvent,
  searchCalendarWorkspace,
  setCalendarKeepSeparate,
  setPrimaryCalendar,
  syncCalendarWorkspace,
  toggleCalendarSource,
} from "@/api/index.ts";
import type {
  ApiResponse,
  CalendarAuditParams,
  CalendarDedupRulesUpdateRequest,
  CalendarDuplicatesParams,
  ConflictScanParams,
  CalendarKeepSeparateRequest,
  CalendarProposalAcceptRequest,
  CalendarSourceToggleRequest,
  CalendarWorkspaceButlerEventPreviewRequest,
  CalendarWorkspaceButlerMutationRequest,
  CalendarWorkspaceFindTimeRequest,
  CalendarWorkspaceParams,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceSearchParams,
  CalendarWorkspaceSyncRequest,
  CalendarWorkspaceUserMutationRequest,
  QuickAddParseRequest,
  SetPrimaryCalendarRequest,
} from "@/api/types.ts";

interface CalendarWorkspaceQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/** Per-page size when walking the keyset cursor; bounded by the API (<=1000). */
const WORKSPACE_PAGE_SIZE = 500;
/** Safety cap on cursor follows so a runaway window can't loop forever. */
const WORKSPACE_MAX_PAGES = 20;

/**
 * Fetch the full workspace window by following the keyset `next_cursor` until
 * `has_more` is false, concatenating entries. The calendar grid renders a
 * bounded time window, so this keeps the view complete while consuming the
 * server's cursor-paginated contract.
 */
async function fetchAllWorkspacePages(
  params: CalendarWorkspaceParams,
): Promise<ApiResponse<CalendarWorkspaceReadResponse>> {
  const entries: CalendarWorkspaceReadResponse["entries"] = [];
  let cursor: string | undefined = params.cursor;
  let last: ApiResponse<CalendarWorkspaceReadResponse> | null = null;

  for (let page = 0; page < WORKSPACE_MAX_PAGES; page += 1) {
    const resp = await getCalendarWorkspace({
      ...params,
      limit: params.limit ?? WORKSPACE_PAGE_SIZE,
      cursor,
    });
    last = resp;
    entries.push(...resp.data.entries);
    if (!resp.data.has_more || !resp.data.next_cursor) break;
    cursor = resp.data.next_cursor;
  }

  const base = last as ApiResponse<CalendarWorkspaceReadResponse>;
  return {
    ...base,
    data: {
      ...base.data,
      entries,
      next_cursor: null,
      has_more: false,
    },
  };
}

/** Fetch normalized calendar workspace entries for the requested view/time range. */
export function useCalendarWorkspace(
  params: CalendarWorkspaceParams,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-workspace", params],
    queryFn: () => fetchAllWorkspacePages(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}

/**
 * Fetch the read-only cross-domain overlay layer (`view=overlays`) for a time
 * window. Overlays are precomputed domain-context contributions (finance bills,
 * travel legs, relationship dates, health appointments) projected onto calendar
 * days — an additive layer toggled on top of the primary user/butler view, not
 * a primary view mode. The read is fail-open server-side: a missing cached view
 * yields `entries: []` with `has_domain_context: false`, never an error.
 */
export function useCalendarOverlays(
  params: { start: string; end: string; timezone?: string },
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-overlays", params],
    queryFn: () =>
      getCalendarWorkspace({
        view: "overlays",
        start: params.start,
        end: params.end,
        timezone: params.timezone,
      }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/**
 * Fetch the structured "tomorrow at a glance" day-briefing card for a target
 * date. Reads the precomputed overlay view grouped by butler/kind — NO per-open
 * LLM call. Fail-open server-side: a missing cached view yields an honest
 * empty-state (`has_domain_context: false`), never an error.
 */
export function useCalendarDayBriefing(
  params: { date: string; timezone?: string; butlers?: string[] },
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-day-briefing", params],
    queryFn: () =>
      getCalendarDayBriefing({
        date: params.date,
        timezone: params.timezone,
        butlers: params.butlers,
      }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/**
 * Fetch the meeting-prep rail context for a selected calendar event. Reads the
 * precomputed prep view (attendees + relationship notes + last-met + per-attendee
 * message context) — NO per-open LLM call. Fail-open server-side: an event with
 * no prep contribution yields the honest empty-state (`has_prep_context: false`),
 * never an error. The query is disabled until an `eventId` is provided so the
 * rail only fetches once an event is selected.
 */
export function useCalendarMeetingPrep(
  eventId: string | null | undefined,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-meeting-prep", eventId],
    queryFn: () => getCalendarMeetingPrep(eventId as string),
    enabled: (options?.enabled ?? true) && !!eventId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/** Full-text search calendar events; disabled until the query is non-blank. */
export function useCalendarWorkspaceSearch(
  params: CalendarWorkspaceSearchParams,
  options?: { enabled?: boolean },
) {
  const trimmed = params.q.trim();
  return useQuery({
    queryKey: ["calendar-workspace-search", { ...params, q: trimmed }],
    queryFn: () => searchCalendarWorkspace({ ...params, q: trimmed }),
    enabled: (options?.enabled ?? true) && trimmed.length > 0,
    staleTime: 10_000,
  });
}

/** Fetch calendar workspace metadata (sources, lanes, writable calendars). */
export function useCalendarWorkspaceMeta(options?: CalendarWorkspaceQueryOptions) {
  return useQuery({
    queryKey: ["calendar-workspace-meta"],
    queryFn: () => getCalendarWorkspaceMeta(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/** Trigger projection/provider sync for all sources or a selected source. */
export function useSyncCalendarWorkspace() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: CalendarWorkspaceSyncRequest) => syncCalendarWorkspace(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/** Find ranked open time slots (read-only; does not mutate workspace caches). */
export function useFindCalendarWorkspaceTime() {
  return useMutation({
    mutationFn: (body: CalendarWorkspaceFindTimeRequest) => findCalendarWorkspaceTime(body),
  });
}

/** Mutate user-view provider events and refresh workspace data after success. */
export function useMutateCalendarWorkspaceUserEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarWorkspaceUserMutationRequest) =>
      mutateCalendarWorkspaceUserEvent(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/**
 * Parse a natural-language quick-add phrase into a draft event (no write).
 *
 * Parse-only: this never mutates server state, so no query caches are
 * invalidated. The returned draft is confirmed through the normal create
 * mutation ({@link useMutateCalendarWorkspaceUserEvent}) with a fresh
 * ``request_id``.
 */
export function useParseCalendarQuickAdd() {
  return useMutation({
    mutationFn: (body: QuickAddParseRequest) => parseCalendarQuickAdd(body),
  });
}

/** Execute butler-lane workspace mutations and refresh query caches. */
export function useMutateCalendarWorkspaceButlerEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarWorkspaceButlerMutationRequest) =>
      mutateCalendarWorkspaceButlerEvent(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

/**
 * Dry-run a draft butler event's recurrence expansion. Read-only preview — no
 * cache invalidation, since nothing is persisted.
 */
export function usePreviewCalendarWorkspaceButlerEvent() {
  return useMutation({
    mutationFn: (body: CalendarWorkspaceButlerEventPreviewRequest) =>
      previewCalendarWorkspaceButlerEvent(body),
  });
}

/**
 * Fetch pending calendar proposals (`view=proposals`) for a time window.
 *
 * Proposals are butler-extracted candidate events awaiting the user's
 * accept/dismiss decision. The read fails open server-side (a missing proposals
 * table yields `entries: []`, never an error). Bounded set — a single page is
 * fetched, not the keyset walk used for the main grid.
 */
export function useCalendarProposals(
  params: { start: string; end: string; timezone?: string },
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-proposals", params],
    queryFn: () =>
      getCalendarWorkspace({
        view: "proposals",
        start: params.start,
        end: params.end,
        timezone: params.timezone,
      }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/** Invalidate every workspace-derived cache after a proposal mutation. */
function invalidateWorkspaceAndProposals(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["calendar-proposals"] });
  queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
  queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
}

/**
 * Accept a calendar proposal (optionally with inline overrides). On success the
 * proposal becomes a butler event on the Butlers subcalendar; caches that could
 * reflect the new event and the now-resolved proposal are invalidated.
 */
export function useAcceptCalendarProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { proposalId: string; overrides?: CalendarProposalAcceptRequest }) =>
      acceptCalendarProposal(vars.proposalId, vars.overrides),
    onSettled: () => invalidateWorkspaceAndProposals(queryClient),
  });
}

/** Dismiss a calendar proposal (no provider write) and refresh proposal caches. */
export function useDismissCalendarProposal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { proposalId: string }) => dismissCalendarProposal(vars.proposalId),
    onSettled: () => invalidateWorkspaceAndProposals(queryClient),
  });
}

/** List connected Google accounts joined with calendar connector health. */
export function useCalendarAccounts(options?: CalendarWorkspaceQueryOptions) {
  return useQuery({
    queryKey: ["calendar-accounts"],
    queryFn: () => getCalendarAccounts(),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 60_000,
  });
}

/** Enable/disable a calendar as a sync source and refresh workspace metadata. */
export function useToggleCalendarSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarSourceToggleRequest) => toggleCalendarSource(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
      queryClient.invalidateQueries({ queryKey: ["calendar-accounts"] });
    },
  });
}

/** Set the primary calendar and refresh workspace metadata. */
export function useSetPrimaryCalendar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SetPrimaryCalendarRequest) => setPrimaryCalendar(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar-workspace-meta"] });
    },
  });
}

interface CalendarAuditQueryOptions {
  refetchInterval?: number | false;
  enabled?: boolean;
}

/** Fetch a single calendar workspace entry by instance ID. */
export function useCalendarWorkspaceEntry(
  entryId: string | null,
  options?: { enabled?: boolean; timezone?: string },
) {
  return useQuery({
    queryKey: ["calendar-workspace-entry", entryId],
    queryFn: () => getCalendarWorkspaceEntry(entryId!, options?.timezone),
    enabled: options?.enabled ?? !!entryId,
  });
}

/**
 * Fetch the cross-source duplicate clusters the read-model collapses, plus the
 * active dedup rules. Fetched only when enabled (the review panel is open). The
 * read is fail-open server-side (`available: false` on failure), never an error.
 */
export function useCalendarDuplicates(
  params: CalendarDuplicatesParams,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-duplicates", params],
    queryFn: () => getCalendarWorkspaceDuplicates(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * Scan the visible window for conflicts / overcommitment (the radar banner).
 *
 * Read-only and fail-open server-side (`issues_available: false` on failure),
 * never an error. Intended to be enabled only on the week/day views.
 */
export function useCalendarConflicts(
  params: ConflictScanParams,
  options?: CalendarWorkspaceQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-conflicts", params],
    queryFn: () => getCalendarWorkspaceConflicts(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/** Invalidate the duplicate-review + main workspace caches after a dedup change. */
function invalidateDuplicatesAndWorkspace(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["calendar-duplicates"] });
  queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
}

/**
 * Persist the cross-source dedup match-strategy / noisy-threshold settings.
 * Changing the rules re-collapses the live workspace read, so both the
 * duplicate-review and workspace caches are invalidated.
 */
export function usePatchCalendarDedupRules() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarDedupRulesUpdateRequest) => patchCalendarDedupRules(body),
    onSuccess: () => invalidateDuplicatesAndWorkspace(queryClient),
  });
}

/**
 * Pin or unpin a duplicate cluster as keep-separate. A keep-separate cluster is
 * no longer collapsed by the workspace read, so both caches are invalidated.
 */
export function useSetCalendarKeepSeparate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CalendarKeepSeparateRequest) => setCalendarKeepSeparate(body),
    onSuccess: () => invalidateDuplicatesAndWorkspace(queryClient),
  });
}

/** Fetch paginated calendar mutation audit log entries. */
export function useCalendarWorkspaceAudit(
  params?: CalendarAuditParams,
  options?: CalendarAuditQueryOptions,
) {
  return useQuery({
    queryKey: ["calendar-workspace-audit", params],
    queryFn: () => getCalendarWorkspaceAudit(params),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? 30_000,
  });
}
