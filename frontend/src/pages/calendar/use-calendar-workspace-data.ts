/**
 * Workspace read model for the calendar page: source metadata, the visible
 * window in the workspace timezone, the entries query and the structures the
 * month grid, time grid, agenda and butler lanes render from.
 */

import { useMemo } from "react";
import { addDays, startOfWeek } from "date-fns";

import type {
  CalendarWorkspaceStatusFacet,
  CalendarWorkspaceView,
  UnifiedCalendarEntry,
  UnifiedCalendarSourceType,
} from "@/api/types.ts";
import { useCalendarWorkspace, useCalendarWorkspaceMeta } from "@/hooks/use-calendar-workspace";
import { tzCalendarWindow, tzDayKey } from "@/lib/calendar-grid";
import { computeFreshnessPlaque } from "@/pages/calendar/freshness";
import { useSourceControls } from "@/pages/calendar/use-source-controls";
import {
  formatLaneTitle,
  titleize,
  truncateCalendarId,
  type ButlerLaneRows,
  type CalendarRange,
} from "@/pages/calendar/workspace-model";

interface CalendarWorkspaceDataArgs {
  view: CalendarWorkspaceView;
  range: CalendarRange;
  anchor: Date;
  /** The browser timezone, sent as the request timezone. */
  timezone: string;
  selectedSourceKey: string;
  selectedCalendarId: string;
  selectedStatus: string;
  selectedSourceType: string;
}

export function useCalendarWorkspaceData({
  view,
  range,
  anchor,
  timezone,
  selectedSourceKey,
  selectedCalendarId,
  selectedStatus,
  selectedSourceType,
}: CalendarWorkspaceDataArgs) {
  const metaQuery = useCalendarWorkspaceMeta();
  const connectedSources = useMemo(
    () => metaQuery.data?.data.connected_sources ?? [],
    [metaQuery.data?.data.connected_sources],
  );
  const freshnessPlaque = useMemo(
    () =>
      computeFreshnessPlaque(
        connectedSources,
        metaQuery.isError ? "error" : metaQuery.isPending ? "pending" : "success",
        metaQuery.data?.data.sources_available !== false,
      ),
    [
      connectedSources,
      metaQuery.isError,
      metaQuery.isPending,
      metaQuery.data?.data.sources_available,
    ],
  );
  const writableCalendars = useMemo(
    () => metaQuery.data?.data.writable_calendars ?? [],
    [metaQuery.data?.data.writable_calendars],
  );
  // Only calendars that resolve to an owning butler can actually be submitted;
  // a null butler_name fails at submit with "Could not resolve owning butler".
  const submittableCalendars = useMemo(
    () => writableCalendars.filter((calendar) => Boolean(calendar.butler_name)),
    [writableCalendars],
  );
  const defaultTimezone = metaQuery.data?.data.default_timezone || timezone;
  const primaryCalendarId = metaQuery.data?.data.primary_calendar_id ?? null;

  // Visible window: derive columns/labels and the backend query bounds in the
  // *workspace* timezone so the day columns line up with how events are bucketed
  // (tzDayKey). `start`/`end` are workspace-tz calendar dates (browser-local
  // midnights of those dates); `queryStart`/`queryEnd` are the workspace-tz
  // midnight instants the backend fetch spans.
  const { start, end, queryStart, queryEnd } = useMemo(
    () => tzCalendarWindow(range, anchor, defaultTimezone),
    [range, anchor, defaultTimezone],
  );

  const userSources = useMemo(
    () => connectedSources.filter((source) => source.lane === "user"),
    [connectedSources],
  );

  const sourceControls = useSourceControls(connectedSources);
  const { hiddenSources } = sourceControls;

  const sourcesForQuery = useMemo(() => {
    const hasCalendarFilter =
      selectedSourceKey !== "all" || selectedCalendarId !== "all";
    // The read filter is driven by the view-only hidden set, NOT the persisted
    // sync toggle: hiding a calendar removes it from the grid, while disabling
    // its sync does not (its already-synced events remain visible).
    if (view !== "user" || (!hasCalendarFilter && hiddenSources.size === 0)) {
      return undefined;
    }
    const base = hasCalendarFilter
      ? userSources
          .filter(
            (source) =>
              (selectedCalendarId === "all" || source.calendar_id === selectedCalendarId) &&
              (selectedSourceKey === "all" || source.source_key === selectedSourceKey),
          )
          .map((source) => source.source_key)
      : userSources.map((source) => source.source_key);
    return base.filter((key) => !hiddenSources.has(key));
  }, [hiddenSources, selectedCalendarId, selectedSourceKey, userSources, view]);

  const workspaceQuery = useCalendarWorkspace({
    view,
    start: queryStart,
    end: queryEnd,
    timezone,
    sources: sourcesForQuery,
    status:
      selectedStatus === "all"
        ? undefined
        : (selectedStatus as CalendarWorkspaceStatusFacet),
    source_type:
      selectedSourceType === "all"
        ? undefined
        : (selectedSourceType as UnifiedCalendarSourceType),
  });

  const entries = useMemo(() => {
    const rows = workspaceQuery.data?.data.entries ?? [];
    return [...rows].sort((a, b) => a.start_at.localeCompare(b.start_at));
  }, [workspaceQuery.data?.data.entries]);

  // Linked-people resolution honesty (bu-qs64f): false only when the backend
  // reported the entity-resolution query failed for a schema.
  const peopleSourceAvailable =
    workspaceQuery.data?.data.people_source_available !== false;
  // Events fan-out honesty: false only when a butler schema's workspace query
  // failed; a short grid must not read as a complete, quiet calendar.
  const entriesSourceAvailable =
    workspaceQuery.data?.data.entries_source_available !== false;

  const lanes = useMemo(
    () =>
      workspaceQuery.data?.data.lanes.length
        ? workspaceQuery.data.data.lanes
        : (metaQuery.data?.data.lane_definitions ?? []),
    [workspaceQuery.data, metaQuery.data?.data],
  );

  const entriesByDay = useMemo(() => {
    const buckets = new Map<string, UnifiedCalendarEntry[]>();
    entries.forEach((entry) => {
      const key = tzDayKey(entry.start_at, defaultTimezone);
      const bucket = buckets.get(key) ?? [];
      bucket.push(entry);
      buckets.set(key, bucket);
    });
    return buckets;
  }, [entries, defaultTimezone]);

  const monthDays = useMemo(() => {
    if (range !== "month") return [] as Date[];
    const gridStart = startOfWeek(start, { weekStartsOn: 1 });
    return Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));
  }, [range, start]);

  const weekDays = useMemo(() => {
    if (range === "week") return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    if (range === "day") return [start];
    return [] as Date[];
  }, [range, start]);

  const calendarFilterOptions = useMemo(() => {
    const deduped = new Map<string, string>();
    userSources.forEach((source) => {
      if (!source.calendar_id || deduped.has(source.calendar_id)) return;
      const raw = truncateCalendarId(source.display_name || source.calendar_id);
      deduped.set(source.calendar_id, titleize(raw));
    });
    return Array.from(deduped.entries()).map(([calendarId, label]) => ({
      calendarId,
      label,
    }));
  }, [userSources]);

  const butlerLaneRows = useMemo(() => {
    const laneMap = new Map<string, { butlerName: string; title: string }>();
    lanes.forEach((lane) => {
      laneMap.set(lane.lane_id, { butlerName: lane.butler_name, title: lane.title });
    });
    const grouped = new Map<string, UnifiedCalendarEntry[]>();
    entries.forEach((entry) => {
      if (!entry.butler_name) return;
      if (!laneMap.has(entry.butler_name)) {
        laneMap.set(entry.butler_name, {
          butlerName: entry.butler_name,
          title: formatLaneTitle(entry.butler_name),
        });
      }
      const bucket = grouped.get(entry.butler_name) ?? [];
      bucket.push(entry);
      grouped.set(entry.butler_name, bucket);
    });
    return [...laneMap.entries()]
      .map(
        ([laneId, descriptor]): ButlerLaneRows => ({
          laneId,
          butlerName: descriptor.butlerName,
          title: descriptor.title,
          entries: [...(grouped.get(descriptor.butlerName) ?? [])].sort((a, b) =>
            a.start_at.localeCompare(b.start_at),
          ),
        }),
      )
      .sort((a, b) => a.title.localeCompare(b.title));
  }, [entries, lanes]);

  const availableButlers = useMemo(() => {
    const names = new Set<string>();
    butlerLaneRows.forEach((lane) => {
      if (lane.butlerName) names.add(lane.butlerName);
    });
    connectedSources.forEach((source) => {
      if (source.lane === "butler" && source.butler_name) names.add(source.butler_name);
    });
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [butlerLaneRows, connectedSources]);

  return {
    metaQuery,
    connectedSources,
    freshnessPlaque,
    submittableCalendars,
    defaultTimezone,
    primaryCalendarId,
    start,
    end,
    queryStart,
    queryEnd,
    userSources,
    sourceControls,
    workspaceQuery,
    entries,
    peopleSourceAvailable,
    entriesSourceAvailable,
    entriesByDay,
    monthDays,
    weekDays,
    calendarFilterOptions,
    butlerLaneRows,
    availableButlers,
  };
}
