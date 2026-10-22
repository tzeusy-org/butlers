/**
 * Week/day time grid: column headers, all-day row and the scrollable hour
 * grid with a mono gutter. Pointer interactions (create/move/resize drags,
 * find-time ghosts) are supplied through the optional `interaction` prop.
 */

import type { MutableRefObject, PointerEvent as ReactPointerEvent, RefObject } from "react";
import {
  differenceInMinutes,
  format,
  isSameDay,
  isToday,
  parseISO,
} from "date-fns";

import type { CalendarSuggestedSlot, UnifiedCalendarEntry } from "@/api/types.ts";
import { LinkedPeopleAvatars } from "@/components/calendar/LinkedPeopleAvatars";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Tip } from "@/components/ui/tip";
import {
  AVATAR_MIN_BLOCK_HEIGHT_PX,
  formatEventTime,
  HOUR_HEIGHT_PX,
  minuteOfDayInTz,
  tzDayKey,
} from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { OverlayPill } from "@/pages/calendar/atoms";
import {
  formatMinuteLabel,
  isGridDraggable,
  type GridDragPreview,
  type MovedGhost,
} from "@/pages/calendar/grid-drag";
import { isPausedEntry, type CalendarRange } from "@/pages/calendar/workspace-model";

/** Hour labels 0–23 for the y-axis. */
const HOURS = Array.from({ length: 24 }, (_, i) => i);

type GridPointerHandler = (event: ReactPointerEvent) => void;

export interface TimeGridInteraction {
  gridDrag: GridDragPreview | null;
  movedGhost: MovedGhost | null;
  findTimeSlots: CalendarSuggestedSlot[];
  suppressClickRef: MutableRefObject<boolean>;
  openCreateDialog: (start: Date) => void;
  selectFindTimeSlot: (slot: CalendarSuggestedSlot) => void;
  undoMove: () => void;
  beginCreateDrag: (event: ReactPointerEvent, dayIndex: number) => void;
  beginMoveDrag: (event: ReactPointerEvent, entry: UnifiedCalendarEntry) => void;
  beginResizeDrag: (
    event: ReactPointerEvent,
    entry: UnifiedCalendarEntry,
    dayIndex: number,
  ) => void;
  handleGridPointerMove: GridPointerHandler;
  handleGridPointerUp: GridPointerHandler;
  handleGridPointerCancel: GridPointerHandler;
}

interface TimeGridProps {
  range: CalendarRange;
  weekDays: Date[];
  entriesByDay: Map<string, UnifiedCalendarEntry[]>;
  overlaysEnabled: boolean;
  overlaysByDayMap: Map<string, UnifiedCalendarEntry[]>;
  overlapEntryIds: Set<string>;
  defaultTimezone: string;
  timeGridRef: RefObject<HTMLDivElement>;
  gridBodyRef: RefObject<HTMLDivElement>;
  gutterRef: RefObject<HTMLDivElement>;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
  interaction?: TimeGridInteraction;
}

export function TimeGrid({
  range,
  weekDays,
  entriesByDay,
  overlaysEnabled,
  overlaysByDayMap,
  overlapEntryIds,
  defaultTimezone,
  timeGridRef,
  gridBodyRef,
  gutterRef,
  onOpenDetail,
  interaction,
}: TimeGridProps) {
  const gridDrag = interaction?.gridDrag ?? null;
  const movedGhost = interaction?.movedGhost ?? null;
  const findTimeSlots = interaction?.findTimeSlots ?? [];
  return (
    <>
      {/* Column headers */}
      <div
        className="mb-2 grid"
        style={{
          gridTemplateColumns:
            range === "week"
              ? "3.25rem repeat(7, minmax(0, 1fr))"
              : "3.25rem minmax(0, 1fr)",
        }}
      >
        <div />
        {weekDays.map((day) => (
          <div
            key={format(day, "yyyy-MM-dd")}
            className="px-2 text-center"
          >
            <Eyebrow className={cn(isToday(day) && "text-fg")}>
              {format(day, "EEE")}
            </Eyebrow>{" "}
            <span
              className={cn(
                "font-mono text-[12px] tabular-nums",
                isToday(day) ? "text-fg" : "text-[var(--mfg)]",
              )}
            >
              {format(day, "d")}
            </span>
          </div>
        ))}
      </div>

      {/* All-day row */}
      {(() => {
        const hasAllDay = weekDays.some((day) =>
          (entriesByDay.get(format(day, "yyyy-MM-dd")) ?? []).some(
            (e) => e.all_day,
          ),
        );
        const hasOverlays =
          overlaysEnabled &&
          weekDays.some(
            (day) =>
              (overlaysByDayMap.get(format(day, "yyyy-MM-dd")) ?? [])
                .length > 0,
          );
        if (!hasAllDay && !hasOverlays) return null;
        return (
          <div
            className="mb-2 grid border-b border-[var(--border)] pb-2"
            style={{
              gridTemplateColumns:
                range === "week"
                  ? "3.25rem repeat(7, minmax(0, 1fr))"
                  : "3.25rem minmax(0, 1fr)",
            }}
          >
            <div className="pr-2 pt-0.5 text-right">
              <Eyebrow>All day</Eyebrow>
            </div>
            {weekDays.map((day) => {
              const key = format(day, "yyyy-MM-dd");
              const allDayEntries = (
                entriesByDay.get(key) ?? []
              ).filter((e) => e.all_day);
              const dayOverlays = overlaysEnabled
                ? (overlaysByDayMap.get(key) ?? [])
                : [];
              return (
                <div key={key} className="space-y-1 px-1">
                  {allDayEntries.map((entry) => (
                    <Tip key={entry.entry_id} content={entry.title}>
                      <button
                        type="button"
                        data-calendar-entry-id={entry.entry_id}
                        onClick={(evt) => {
                          evt.stopPropagation();
                          onOpenDetail(entry);
                        }}
                        className="block w-full truncate rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 text-left text-[11px] text-fg transition-colors hover:bg-foreground/[0.06]"
                      >
                        {entry.title}
                      </button>
                    </Tip>
                  ))}
                  {dayOverlays.map((overlay) => (
                    <OverlayPill
                      key={overlay.entry_id}
                      entry={overlay}
                    />
                  ))}
                </div>
              );
            })}
          </div>
        );
      })()}

      {/* Scrollable time grid */}
      <div ref={timeGridRef} className="min-h-0 flex-1 overflow-y-auto">
        <div
          ref={gridBodyRef}
          className="grid h-[var(--calendar-grid-height)]"
          style={{
            gridTemplateColumns:
              range === "week"
                ? "3.25rem repeat(7, minmax(0, 1fr))"
                : "3.25rem minmax(0, 1fr)",
          }}
        >
          {/* Hour gutter */}
          <div ref={gutterRef} className="relative">
            {HOURS.map((h) => (
              <div
                key={h}
                className="absolute right-2 -translate-y-1/2 font-mono text-[10px] leading-none tabular-nums text-[var(--mfg)]"
                style={{ top: h * HOUR_HEIGHT_PX }}
              >
                {h === 0
                  ? ""
                  : format(new Date(2000, 0, 1, h), "HH:mm")}
              </div>
            ))}
          </div>

          {/* Day columns */}
          {weekDays.map((day, dayIndex) => {
            const key = format(day, "yyyy-MM-dd");
            const dayEntries = (entriesByDay.get(key) ?? []).filter(
              (e) => !e.all_day,
            );
            const ghost =
              movedGhost &&
              isSameDay(new Date(movedGhost.prevStartIso), day)
                ? movedGhost
                : null;
            return (
              <div
                key={key}
                className="relative border-l border-[var(--border)]"
              >
                {interaction ? (
                  <button
                    type="button"
                    aria-label={`Create event on ${format(day, "EEE, MMM d")}`}
                    className="absolute inset-0 z-0 cursor-pointer touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
                    onPointerDown={(evt) =>
                      interaction.beginCreateDrag(evt, dayIndex)
                    }
                    onPointerMove={interaction.handleGridPointerMove}
                    onPointerUp={interaction.handleGridPointerUp}
                    onPointerCancel={interaction.handleGridPointerCancel}
                    onClick={(evt) => {
                      // Swallow the click that trails a completed drag-create.
                      if (interaction.suppressClickRef.current) {
                        interaction.suppressClickRef.current = false;
                        return;
                      }
                      const rect =
                        evt.currentTarget.getBoundingClientRect();
                      // Keyboard activation (detail === 0) carries no pointer Y — default to the day.
                      if (evt.detail === 0) {
                        interaction.openCreateDialog(day);
                        return;
                      }
                      const yPx = evt.clientY - rect.top;
                      const snappedMin = Math.max(
                        0,
                        Math.floor(((yPx / HOUR_HEIGHT_PX) * 60) / 30) *
                          30,
                      );
                      const clickedDate = new Date(day);
                      clickedDate.setHours(
                        Math.floor(snappedMin / 60),
                        snappedMin % 60,
                        0,
                        0,
                      );
                      interaction.openCreateDialog(clickedDate);
                    }}
                  />
                ) : null}
                {/* Drag preview (create / move / resize landing in this column). */}
                {gridDrag && gridDrag.dayIndex === dayIndex ? (
                  <div
                    aria-hidden
                    data-testid="calendar-drag-preview"
                    className="pointer-events-none absolute inset-x-0.5 z-20 rounded-[3px] border border-dashed border-fg/60 bg-fg/10"
                    style={{
                      top: (gridDrag.startMin / 60) * HOUR_HEIGHT_PX,
                      height:
                        ((gridDrag.endMin - gridDrag.startMin) / 60) *
                        HOUR_HEIGHT_PX,
                      minHeight: 16,
                    }}
                  >
                    <span className="block px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-fg">
                      {formatMinuteLabel(gridDrag.startMin)}–
                      {formatMinuteLabel(gridDrag.endMin)}
                    </span>
                  </div>
                ) : null}
                {/* Find-time ghost overlays — ranked free slots from the
                    panel's latest search, drawn in place via the grid
                    geometry and synced with the list. Selecting one does
                    the same prefilled-create as the list. */}
                {findTimeSlots.map((slot, slotIndex) => {
                  const slotStart = parseISO(slot.start_at);
                  const slotEnd = parseISO(slot.end_at);
                  // Bucket + place the slot in the workspace timezone so
                  // it lines up with the grid's events under that zone.
                  if (
                    tzDayKey(slot.start_at, defaultTimezone) !==
                    format(day, "yyyy-MM-dd")
                  )
                    return null;
                  const topMin = minuteOfDayInTz(
                    slot.start_at,
                    defaultTimezone,
                  );
                  const durationMin = Math.max(
                    differenceInMinutes(slotEnd, slotStart),
                    15,
                  );
                  const slotLabel = `${formatEventTime(slot.start_at, defaultTimezone, "HH:mm")}–${formatEventTime(slot.end_at, defaultTimezone, "HH:mm")}`;
                  return (
                    <button
                      key={`find-time-${slot.start_at}-${slot.end_at}`}
                      type="button"
                      data-testid="find-time-overlay"
                      title={`Open slot ${slotIndex + 1}: ${slotLabel}`}
                      onClick={(evt) => {
                        evt.stopPropagation();
                        interaction?.selectFindTimeSlot(slot);
                      }}
                      className={cn(
                        "absolute inset-x-0.5 z-20 flex flex-col overflow-hidden rounded-[3px]",
                        "border border-dashed border-fg/50 bg-fg/[0.08]",
                        "px-1.5 py-0.5 text-left transition-colors hover:bg-fg/[0.16]",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                      )}
                      style={{
                        top: (topMin / 60) * HOUR_HEIGHT_PX,
                        height: (durationMin / 60) * HOUR_HEIGHT_PX,
                        minHeight: 16,
                      }}
                    >
                      <span className="font-mono text-[10px] leading-none tabular-nums text-fg">
                        #{slotIndex + 1} · {slotLabel}
                      </span>
                    </button>
                  );
                })}
                {/* "Moved" ghost — one-click undo of the last move/resize. */}
                {ghost ? (
                  <button
                    type="button"
                    data-testid="calendar-move-ghost"
                    title="Undo move"
                    onClick={(evt) => {
                      evt.stopPropagation();
                      interaction?.undoMove();
                    }}
                    className="absolute inset-x-0.5 z-20 flex items-center justify-center rounded-[3px] border border-dashed border-fg/40 bg-bg/70 text-[10px] font-medium text-[var(--mfg)] transition-colors hover:text-fg"
                    style={{
                      top:
                        (minuteOfDayInTz(
                          ghost.prevStartIso,
                          defaultTimezone,
                        ) /
                          60) *
                        HOUR_HEIGHT_PX,
                      height: Math.max(
                        (differenceInMinutes(
                          new Date(ghost.prevEndIso),
                          new Date(ghost.prevStartIso),
                        ) /
                          60) *
                          HOUR_HEIGHT_PX,
                        16,
                      ),
                    }}
                  >
                    Undo
                  </button>
                ) : null}
                {dayEntries.map((entry) => {
                  const s = new Date(entry.start_at);
                  const e = new Date(entry.end_at);
                  const topMin = minuteOfDayInTz(s, defaultTimezone);
                  const durationMin = Math.max(
                    differenceInMinutes(e, s),
                    15,
                  );
                  const topPx = (topMin / 60) * HOUR_HEIGHT_PX;
                  const heightPx = (durationMin / 60) * HOUR_HEIGHT_PX;
                  const paused = isPausedEntry(entry);
                  const drag =
                    interaction && isGridDraggable(entry) ? interaction : null;
                  const draggable = drag !== null;
                  const isDragSource =
                    gridDrag &&
                    gridDrag.mode !== "create" &&
                    gridDrag.entryId === entry.entry_id;
                  return (
                    <button
                      key={entry.entry_id}
                      type="button"
                      data-calendar-entry-id={entry.entry_id}
                      data-conflict-overlap={
                        overlapEntryIds.has(entry.entry_id)
                          ? "true"
                          : undefined
                      }
                      className={cn(
                        "absolute inset-x-0.5 z-10 overflow-hidden rounded-[3px] border border-[var(--border)] bg-bg px-1.5 py-0.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                        // Amber left edge marks an event caught in an overlap.
                        overlapEntryIds.has(entry.entry_id) &&
                          "border-l-2 border-l-amber-500",
                        paused && "opacity-50",
                        draggable &&
                          "cursor-grab touch-none active:cursor-grabbing",
                        isDragSource && "opacity-40",
                      )}
                      style={{
                        top: topPx,
                        height: heightPx,
                        minHeight: 16,
                      }}
                      title={`${formatEventTime(s, defaultTimezone, "HH:mm")}–${formatEventTime(e, defaultTimezone, "HH:mm")} · ${entry.title}`}
                      onPointerDown={
                        drag ? (evt) => drag.beginMoveDrag(evt, entry) : undefined
                      }
                      onPointerMove={drag?.handleGridPointerMove}
                      onPointerUp={drag?.handleGridPointerUp}
                      onPointerCancel={drag?.handleGridPointerCancel}
                      onClick={() => {
                        // Swallow the click that trails a completed move/resize drag.
                        if (interaction?.suppressClickRef.current) {
                          interaction.suppressClickRef.current = false;
                          return;
                        }
                        onOpenDetail(entry);
                      }}
                    >
                      <span className="block truncate text-[11px] font-medium leading-tight text-fg">
                        {entry.title}
                      </span>
                      {heightPx >= 32 ? (
                        <span className="mt-0.5 block truncate font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                          {formatEventTime(s, defaultTimezone, "HH:mm")}–
                          {formatEventTime(e, defaultTimezone, "HH:mm")}
                        </span>
                      ) : null}
                      {/* Linked-people avatars (bu-01qmd) — reuse the agenda/detail
                          cluster, gated by block height so short blocks don't overflow.
                          pointer-events-none keeps it transparent to the block's drag
                          handlers (no interactive elements inside the draggable block). */}
                      {heightPx >= AVATAR_MIN_BLOCK_HEIGHT_PX &&
                      entry.linked_people &&
                      entry.linked_people.length > 0 ? (
                        <LinkedPeopleAvatars
                          people={entry.linked_people}
                          className="pointer-events-none mt-0.5"
                        />
                      ) : null}
                      {drag ? (
                        <span
                          aria-hidden
                          data-testid="calendar-resize-handle"
                          className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize touch-none"
                          onPointerDown={(evt) =>
                            drag.beginResizeDrag(evt, entry, dayIndex)
                          }
                          onPointerMove={drag.handleGridPointerMove}
                          onPointerUp={drag.handleGridPointerUp}
                          onPointerCancel={drag.handleGridPointerCancel}
                        />
                      ) : null}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
