/**
 * Calendar workspace view state held in the URL (view, range, anchor, facets,
 * overlays toggle) so every layered view is shareable and survives reload.
 */

import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router";

import type {
  CalendarWorkspaceSourceFreshness,
  CalendarWorkspaceView,
} from "@/api/types.ts";
import {
  parseAnchor,
  parseRange,
  parseView,
  serializeAnchor,
  type CalendarRange,
} from "@/pages/calendar/workspace-model";

export function useCalendarUrlState() {
  const [searchParams, setSearchParams] = useSearchParams();

  const view = parseView(searchParams.get("view"));
  const range = parseRange(searchParams.get("range"));
  const anchor = parseAnchor(searchParams.get("anchor"));
  const anchorParam = serializeAnchor(anchor);
  const selectedSourceKey = searchParams.get("source") ?? "all";
  const selectedCalendarId = searchParams.get("calendar") ?? "all";
  const selectedStatus = searchParams.get("status") ?? "all";
  const selectedSourceType = searchParams.get("kind") ?? "all";
  // Cross-domain overlays are an additive, read-only context layer toggled on
  // top of the primary user/butler view (not a separate view mode). The toggle
  // is persisted in the URL so the layered view is shareable.
  const overlaysEnabled = searchParams.get("overlays") === "1";

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;

    if (next.get("view") !== view) {
      next.set("view", view);
      changed = true;
    }
    if (next.get("range") !== range) {
      next.set("range", range);
      changed = true;
    }
    if (next.get("anchor") !== anchorParam) {
      next.set("anchor", anchorParam);
      changed = true;
    }

    if (view !== "user") {
      if (next.has("source")) {
        next.delete("source");
        changed = true;
      }
      if (next.has("calendar")) {
        next.delete("calendar");
        changed = true;
      }
    }

    if (changed) {
      setSearchParams(next, { replace: true });
    }
  }, [anchorParam, range, searchParams, setSearchParams, view]);

  const updateQuery = useCallback(
    (nextValues: {
      view?: CalendarWorkspaceView;
      range?: CalendarRange;
      anchor?: Date;
      source?: string;
      calendar?: string;
      status?: string;
      kind?: string;
      overlays?: boolean;
    }) => {
      const next = new URLSearchParams(searchParams);

      if (nextValues.view) next.set("view", nextValues.view);
      if (nextValues.range) next.set("range", nextValues.range);
      if (nextValues.anchor)
        next.set("anchor", serializeAnchor(nextValues.anchor));

      if (nextValues.source !== undefined) {
        if (!nextValues.source || nextValues.source === "all") {
          next.delete("source");
        } else {
          next.set("source", nextValues.source);
        }
      }

      if (nextValues.calendar !== undefined) {
        if (!nextValues.calendar || nextValues.calendar === "all") {
          next.delete("calendar");
        } else {
          next.set("calendar", nextValues.calendar);
        }
      }

      if (nextValues.status !== undefined) {
        if (!nextValues.status || nextValues.status === "all") {
          next.delete("status");
        } else {
          next.set("status", nextValues.status);
        }
      }

      if (nextValues.kind !== undefined) {
        if (!nextValues.kind || nextValues.kind === "all") {
          next.delete("kind");
        } else {
          next.set("kind", nextValues.kind);
        }
      }

      if (nextValues.overlays !== undefined) {
        if (nextValues.overlays) {
          next.set("overlays", "1");
        } else {
          next.delete("overlays");
        }
      }

      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  return {
    view,
    range,
    anchor,
    selectedSourceKey,
    selectedCalendarId,
    selectedStatus,
    selectedSourceType,
    overlaysEnabled,
    updateQuery,
  };
}

export type CalendarUrlState = ReturnType<typeof useCalendarUrlState>;
export type UpdateCalendarQuery = CalendarUrlState["updateQuery"];

/**
 * Drop a source/calendar selection from the URL once it no longer names a
 * connected user source (or the pair stopped agreeing).
 */
export function useSourceSelectionGuard({
  view,
  selectedSourceKey,
  selectedCalendarId,
  userSources,
  updateQuery,
}: {
  view: CalendarWorkspaceView;
  selectedSourceKey: string;
  selectedCalendarId: string;
  userSources: CalendarWorkspaceSourceFreshness[];
  updateQuery: UpdateCalendarQuery;
}) {
  useEffect(() => {
    if (view !== "user") {
      return;
    }

    const sourceValid =
      selectedSourceKey === "all" ||
      userSources.some((source) => source.source_key === selectedSourceKey);
    const calendarValid =
      selectedCalendarId === "all" ||
      userSources.some((source) => source.calendar_id === selectedCalendarId);

    if (!sourceValid || !calendarValid) {
      updateQuery({
        source: sourceValid ? selectedSourceKey : "all",
        calendar: calendarValid ? selectedCalendarId : "all",
      });
      return;
    }

    if (selectedSourceKey !== "all" && selectedCalendarId !== "all") {
      const source = userSources.find(
        (item) => item.source_key === selectedSourceKey,
      );
      if (source?.calendar_id !== selectedCalendarId) {
        updateQuery({ source: "all" });
      }
    }
  }, [selectedCalendarId, selectedSourceKey, updateQuery, userSources, view]);
}
