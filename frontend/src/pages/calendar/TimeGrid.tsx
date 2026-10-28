/**
 * Read-only week/day time grid: column headers, all-day row and the scrollable
 * hour grid with a mono gutter. Selecting an entry opens its detail panel;
 * there is no create, drag or resize on the grid (operator view).
 */

import type { RefObject } from "react";
import { differenceInMinutes, format, isToday } from "date-fns";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { LinkedPeopleAvatars } from "@/components/calendar/LinkedPeopleAvatars";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Tip } from "@/components/ui/tip";
import {
  AVATAR_MIN_BLOCK_HEIGHT_PX,
  formatEventTime,
  HOUR_HEIGHT_PX,
  minuteOfDayInTz,
} from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { OverlayPill } from "@/pages/calendar/atoms";
import { isPausedEntry, type CalendarRange } from "@/pages/calendar/workspace-model";

/** Hour labels 0–23 for the y-axis. */
const HOURS = Array.from({ length: 24 }, (_, i) => i);

interface TimeGridProps {
  range: CalendarRange;
  weekDays: Date[];
  entriesByDay: Map<string, UnifiedCalendarEntry[]>;
  overlaysEnabled: boolean;
  overlaysByDayMap: Map<string, UnifiedCalendarEntry[]>;
  overlapEntryIds: Set<string>;
  defaultTimezone: string;
  timeGridRef: RefObject<HTMLDivElement>;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
}

const dayKey = (day: Date) => format(day, "yyyy-MM-dd");

export function TimeGrid({
  range,
  weekDays,
  entriesByDay,
  overlaysEnabled,
  overlaysByDayMap,
  overlapEntryIds,
  defaultTimezone,
  timeGridRef,
  onOpenDetail,
}: TimeGridProps) {
  const gridTemplateColumns =
    range === "week" ? "3.25rem repeat(7, minmax(0, 1fr))" : "3.25rem minmax(0, 1fr)";
  const overlaysOn = (day: Date) =>
    overlaysEnabled ? (overlaysByDayMap.get(dayKey(day)) ?? []) : [];
  const hasAllDay = weekDays.some((day) =>
    (entriesByDay.get(dayKey(day)) ?? []).some((e) => e.all_day),
  );
  const hasOverlays = weekDays.some((day) => overlaysOn(day).length > 0);

  return (
    <>
      <div className="mb-2 grid" style={{ gridTemplateColumns }}>
        <div />
        {weekDays.map((day) => (
          <div key={dayKey(day)} className="px-2 text-center">
            <Eyebrow className={cn(isToday(day) && "text-fg")}>{format(day, "EEE")}</Eyebrow>{" "}
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

      {hasAllDay || hasOverlays ? (
        <div
          className="mb-2 grid border-b border-[var(--border)] pb-2"
          style={{ gridTemplateColumns }}
        >
          <div className="pr-2 pt-0.5 text-right">
            <Eyebrow>All day</Eyebrow>
          </div>
          {weekDays.map((day) => (
            <div key={dayKey(day)} className="space-y-1 px-1">
              {(entriesByDay.get(dayKey(day)) ?? [])
                .filter((e) => e.all_day)
                .map((entry) => (
                  <Tip key={entry.entry_id} content={entry.title}>
                    <button
                      type="button"
                      data-calendar-entry-id={entry.entry_id}
                      onClick={() => onOpenDetail(entry)}
                      className="block w-full truncate rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 text-left text-[11px] text-fg transition-colors hover:bg-foreground/[0.06]"
                    >
                      {entry.title}
                    </button>
                  </Tip>
                ))}
              {overlaysOn(day).map((overlay) => (
                <OverlayPill key={overlay.entry_id} entry={overlay} />
              ))}
            </div>
          ))}
        </div>
      ) : null}

      <div ref={timeGridRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="grid h-[var(--calendar-grid-height)]" style={{ gridTemplateColumns }}>
          <div className="relative">
            {HOURS.map((h) => (
              <div
                key={h}
                className="absolute right-2 -translate-y-1/2 font-mono text-[10px] leading-none tabular-nums text-[var(--mfg)]"
                style={{ top: h * HOUR_HEIGHT_PX }}
              >
                {h === 0 ? "" : format(new Date(2000, 0, 1, h), "HH:mm")}
              </div>
            ))}
          </div>

          {weekDays.map((day) => (
            <div key={dayKey(day)} className="relative border-l border-[var(--border)]">
              {(entriesByDay.get(dayKey(day)) ?? [])
                .filter((e) => !e.all_day)
                .map((entry) => {
                  const s = new Date(entry.start_at);
                  const e = new Date(entry.end_at);
                  const durationMin = Math.max(differenceInMinutes(e, s), 15);
                  const topPx = (minuteOfDayInTz(s, defaultTimezone) / 60) * HOUR_HEIGHT_PX;
                  const heightPx = (durationMin / 60) * HOUR_HEIGHT_PX;
                  const overlaps = overlapEntryIds.has(entry.entry_id);
                  return (
                    <button
                      key={entry.entry_id}
                      type="button"
                      data-calendar-entry-id={entry.entry_id}
                      data-conflict-overlap={overlaps ? "true" : undefined}
                      className={cn(
                        "absolute inset-x-0.5 z-10 overflow-hidden rounded-[3px] border border-[var(--border)] bg-bg px-1.5 py-0.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                        // Amber left edge marks an event caught in an overlap.
                        overlaps && "border-l-2 border-l-amber-500",
                        isPausedEntry(entry) && "opacity-50",
                      )}
                      style={{ top: topPx, height: heightPx, minHeight: 16 }}
                      title={`${formatEventTime(s, defaultTimezone, "HH:mm")}–${formatEventTime(e, defaultTimezone, "HH:mm")} · ${entry.title}`}
                      onClick={() => onOpenDetail(entry)}
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
                      {/* Linked-people avatars (bu-01qmd), gated by block height
                          so short blocks don't overflow. */}
                      {heightPx >= AVATAR_MIN_BLOCK_HEIGHT_PX &&
                      entry.linked_people &&
                      entry.linked_people.length > 0 ? (
                        <LinkedPeopleAvatars
                          people={entry.linked_people}
                          className="pointer-events-none mt-0.5"
                        />
                      ) : null}
                    </button>
                  );
                })}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
