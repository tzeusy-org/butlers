/**
 * Read-only context lanes laid over the workspace window: the conflict radar,
 * projected invitations, cross-domain overlays and the day-briefing card.
 */

import { useMemo } from "react";
import { addDays, format, startOfDay } from "date-fns";

import {
  useCalendarConflicts,
  useCalendarDayBriefing,
  useCalendarInvitations,
  useCalendarOverlays,
} from "@/hooks/use-calendar-workspace";
import { overlaysByDay } from "@/lib/calendar-overlays";
import type { CalendarRange } from "@/pages/calendar/workspace-model";

interface CalendarContextDataArgs {
  range: CalendarRange;
  queryStart: string;
  queryEnd: string;
  timezone: string;
  overlaysEnabled: boolean;
  workspaceFetching: boolean;
}

export function useCalendarContextData({
  range,
  queryStart,
  queryEnd,
  timezone,
  overlaysEnabled,
  workspaceFetching,
}: CalendarContextDataArgs) {
  // Conflict & overcommitment radar (bu-q8o90x): scan the visible window for
  // overlaps / dense days only on the time-grid views. Fail-open server-side, so
  // a degraded scan (`issues_available: false`) leaves the banner silent.
  const radarEnabled = range === "week" || range === "day";
  const conflictsQuery = useCalendarConflicts(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: radarEnabled },
  );
  const conflictScan = conflictsQuery.data?.data;
  const conflictIssues = useMemo(
    () => (radarEnabled && !conflictsQuery.isError ? (conflictScan?.issues ?? []) : []),
    [conflictsQuery.isError, radarEnabled, conflictScan?.issues],
  );
  const conflictsAvailable =
    radarEnabled && !conflictsQuery.isError && (conflictScan?.issues_available ?? false);
  // entry_ids appearing in any overlap issue get the amber grid edge.
  const overlapEntryIds = useMemo(() => {
    const ids = new Set<string>();
    if (!conflictsAvailable) return ids;
    for (const issue of conflictIssues) {
      if (issue.kind !== "overlap") continue;
      for (const event of issue.events) ids.add(event.entry_id);
    }
    return ids;
  }, [conflictsAvailable, conflictIssues]);

  const invitationsQuery = useCalendarInvitations({
    start: queryStart,
    end: queryEnd,
    timezone,
  });
  const invitationPages = invitationsQuery.data?.pages;
  const invitations = invitationPages
    ? [
        ...new Map(
          invitationPages
            .flatMap((page) => page.data.entries)
            .map((entry) => [entry.entry_id, entry] as const),
        ).values(),
      ]
    : undefined;
  const hasInvitationPages = invitationPages !== undefined && invitationPages.length > 0;
  const invitationsAvailable =
    hasInvitationPages && invitationPages.every((page) => page.data.issues_available);
  const invitationConflictsAvailable =
    hasInvitationPages && invitationPages.every((page) => page.data.conflicts_available);
  const invitationSourcesDegraded = invitationPages
    ? [...new Set(invitationPages.flatMap((page) => page.data.sources_degraded))]
    : undefined;

  const overlaysQuery = useCalendarOverlays(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: overlaysEnabled },
  );
  const overlayEntries = useMemo(
    () =>
      overlaysEnabled && !overlaysQuery.isError
        ? (overlaysQuery.data?.data.entries ?? [])
        : [],
    [overlaysEnabled, overlaysQuery.isError, overlaysQuery.data?.data],
  );
  const hasDomainContext =
    !overlaysQuery.isError && (overlaysQuery.data?.data.has_domain_context ?? false);
  const overlaysByDayMap = useMemo(() => overlaysByDay(overlayEntries), [overlayEntries]);

  // Day-briefing card ("tomorrow at a glance"): the precomputed overlay
  // contributions for tomorrow, grouped by butler/kind. Reads the same cached
  // view as the overlays lane (no per-open LLM); only fetched while overlays are
  // toggled on, so it shares that opt-in surface.
  const briefingDate = useMemo(() => addDays(startOfDay(new Date()), 1), []);
  const briefingDateParam = useMemo(() => format(briefingDate, "yyyy-MM-dd"), [briefingDate]);
  const dayBriefingQuery = useCalendarDayBriefing(
    { date: briefingDateParam, timezone },
    { enabled: overlaysEnabled },
  );

  const surfaceFetching =
    workspaceFetching ||
    (overlaysEnabled &&
      !overlaysQuery.isLoading &&
      !overlaysQuery.isError &&
      overlaysQuery.isFetching) ||
    (radarEnabled &&
      !conflictsQuery.isLoading &&
      !conflictsQuery.isError &&
      conflictsQuery.isFetching);

  return {
    radarEnabled,
    conflictsQuery,
    conflictIssues,
    conflictsAvailable,
    overlapEntryIds,
    invitationsQuery,
    invitations,
    invitationsAvailable,
    invitationConflictsAvailable,
    invitationSourcesDegraded,
    overlaysQuery,
    hasDomainContext,
    overlaysByDayMap,
    briefingDate,
    dayBriefingQuery,
    surfaceFetching,
  };
}
