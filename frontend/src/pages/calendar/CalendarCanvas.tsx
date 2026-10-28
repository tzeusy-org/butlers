/**
 * The calendar canvas: the debrief card, source-degraded notes and the active
 * layout (butler lanes, month matrix, week/day time grid or agenda list).
 */

import type { ReactNode, RefObject } from "react";

import type { CalendarWorkspaceView, UnifiedCalendarEntry } from "@/api/types.ts";
import { DebriefCard } from "@/components/calendar/DebriefCard";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { StateDot } from "@/components/ui/StateDot";
import { Voice } from "@/components/ui/Voice";
import { AgendaList } from "@/pages/calendar/AgendaList";
import { ButlerLanes } from "@/pages/calendar/ButlerLanes";
import { MonthGrid } from "@/pages/calendar/MonthGrid";
import { TimeGrid } from "@/pages/calendar/TimeGrid";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";
import type { useCalendarContextData } from "@/pages/calendar/use-calendar-context-data";
import type { useCalendarWorkspaceData } from "@/pages/calendar/use-calendar-workspace-data";
import type { useButlerLaneActions } from "@/pages/calendar/use-butler-lane-actions";
import type { CalendarRange } from "@/pages/calendar/workspace-model";

type WorkspaceData = ReturnType<typeof useCalendarWorkspaceData>;
type ContextData = ReturnType<typeof useCalendarContextData>;

interface CalendarCanvasProps {
  view: CalendarWorkspaceView;
  range: CalendarRange;
  data: WorkspaceData;
  context: ContextData;
  overlaysEnabled: boolean;
  laneActions: ReturnType<typeof useButlerLaneActions>;
  butlerPending: boolean;
  /** Conflict radar rendered above the week/day time grid. */
  radar: ReactNode;
  timeGridRef: RefObject<HTMLDivElement>;
  onAddButlerEvent: (butlerName: string) => void;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
  updateQuery: UpdateCalendarQuery;
}

function DegradedNote({ testId, label, detail }: { testId: string; label: string; detail: string }) {
  return (
    <div data-testid={testId} className="pb-2">
      <SourceDegradedNote label={label} detail={detail} />
    </div>
  );
}

export function CalendarCanvas({
  view,
  range,
  data,
  context,
  overlaysEnabled,
  laneActions,
  butlerPending,
  radar,
  timeGridRef,
  onAddButlerEvent,
  onOpenDetail,
  updateQuery,
}: CalendarCanvasProps) {
  const { workspaceQuery, defaultTimezone } = data;
  const { overlaysQuery } = context;

  let layout: ReactNode;
  if (view === "butler") {
    layout = (
      <ButlerLanes
        lanes={data.butlerLaneRows}
        defaultTimezone={defaultTimezone}
        pending={butlerPending}
        onAddEvent={onAddButlerEvent}
        onOpenDetail={onOpenDetail}
        onToggle={laneActions.handleButlerToggle}
        onSnooze={laneActions.handleButlerSnooze}
        onDismiss={laneActions.handleButlerDismiss}
        onDelete={laneActions.handleButlerDelete}
      />
    );
  } else if (range === "month") {
    layout = (
      <MonthGrid
        monthDays={data.monthDays}
        start={data.start}
        entriesByDay={data.entriesByDay}
        overlaysEnabled={overlaysEnabled}
        overlaysByDayMap={context.overlaysByDayMap}
        defaultTimezone={defaultTimezone}
        onOpenDetail={onOpenDetail}
        updateQuery={updateQuery}
      />
    );
  } else if (range === "week" || range === "day") {
    layout = (
      <div className="flex min-h-0 flex-1 flex-col">
        {radar}
        <TimeGrid
          range={range}
          weekDays={data.weekDays}
          entriesByDay={data.entriesByDay}
          overlaysEnabled={overlaysEnabled}
          overlaysByDayMap={context.overlaysByDayMap}
          overlapEntryIds={context.overlapEntryIds}
          defaultTimezone={defaultTimezone}
          timeGridRef={timeGridRef}
          onOpenDetail={onOpenDetail}
        />
      </div>
    );
  } else {
    layout = (
      <AgendaList
        entries={data.entries}
        defaultTimezone={defaultTimezone}
        onOpenDetail={onOpenDetail}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col pt-5">
      <DebriefCard />
      {/* Events fan-out degraded (bu-yjfk2): a butler schema's workspace query
          failed, so the grid may be missing that schema's events. Name the
          degraded source rather than let a short grid read as "nothing scheduled". */}
      {!data.entriesSourceAvailable ? (
        <DegradedNote
          testId="entries-source-degraded"
          label="Calendar events"
          detail="some sources unavailable: this view may be missing events"
        />
      ) : null}
      {overlaysEnabled && overlaysQuery.isError ? (
        <DegradedNote
          testId="calendar-overlays-error"
          label="Cross-domain overlays"
          detail={
            overlaysQuery.error instanceof Error ? overlaysQuery.error.message : "unavailable"
          }
        />
      ) : null}
      {/* Linked-people resolution degraded (bu-qs64f): entries still render, but
          avatars may be incomplete. */}
      {!data.peopleSourceAvailable ? (
        <DegradedNote
          testId="linked-people-degraded"
          label="Linked people"
          detail="unavailable: some events may not show their people"
        />
      ) : null}
      {workspaceQuery.isLoading ? (
        <Voice variant="italic" className="text-[var(--mfg)]">
          Drawing the calendar…
        </Voice>
      ) : workspaceQuery.isError ? (
        <div role="alert" className="flex items-start gap-2 py-1">
          <StateDot state="error" className="mt-[7px]" />
          <p className="text-sm text-fg">
            The calendar workspace failed to load.{" "}
            <span className="text-[var(--mfg)]">
              {workspaceQuery.error instanceof Error
                ? workspaceQuery.error.message
                : "Unknown error"}
            </span>
          </p>
        </div>
      ) : (
        // Never-blank floor (bu-nhcp5): placeholderData on useCalendarWorkspace
        // keeps the outgoing window's entries rendered the instant navigation
        // fires a new query key; this dims them instead of replacing the grid
        // with "Drawing the calendar…" on every step.
        <FetchingDim isFetching={context.surfaceFetching}>{layout}</FetchingDim>
      )}
    </div>
  );
}
