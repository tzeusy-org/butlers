/** Month matrix: six-week day grid with entry chips, "+N more" and overlays. */

import { format, isSameMonth, isToday } from "date-fns";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Tip } from "@/components/ui/tip";
import { formatEventTime } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { OverlayPill } from "@/pages/calendar/atoms";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";

interface MonthGridProps {
  monthDays: Date[];
  start: Date;
  entriesByDay: Map<string, UnifiedCalendarEntry[]>;
  overlaysEnabled: boolean;
  overlaysByDayMap: Map<string, UnifiedCalendarEntry[]>;
  defaultTimezone: string;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
  updateQuery: UpdateCalendarQuery;
}

export function MonthGrid({
  monthDays,
  start,
  entriesByDay,
  overlaysEnabled,
  overlaysByDayMap,
  defaultTimezone,
  onOpenDetail,
  updateQuery,
}: MonthGridProps) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2 grid grid-cols-7">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
          (label) => (
            <Eyebrow key={label} as="div" className="px-2">
              {label}
            </Eyebrow>
          ),
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-7 grid-rows-6 overflow-y-auto border-l border-t border-[var(--border)]">
        {monthDays.map((day) => {
          const key = format(day, "yyyy-MM-dd");
          const dayEntries = entriesByDay.get(key) ?? [];
          const dayOverlays = overlaysEnabled
            ? (overlaysByDayMap.get(key) ?? [])
            : [];
          const inMonth = isSameMonth(day, start);
          const today = isToday(day);
          return (
            <div
              key={key}
              className={cn(
                "relative min-h-[5.5rem] overflow-hidden border-b border-r border-[var(--border)] p-1.5",
                !inMonth && "bg-foreground/[0.02]",
              )}
            >
              <div className="pointer-events-none relative z-10">
                <div className="mb-1 flex items-center justify-between">
                  {today ? (
                    <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-[4px] bg-fg px-1 font-mono text-[11px] tabular-nums text-bg">
                      {format(day, "d")}
                    </span>
                  ) : (
                    <span
                      className={cn(
                        "font-mono text-[11px] tabular-nums",
                        inMonth
                          ? "text-fg"
                          : "text-[var(--dim)]",
                      )}
                    >
                      {format(day, "d")}
                    </span>
                  )}
                </div>
                <div className="space-y-0.5">
                  {dayEntries.slice(0, 3).map((entry) => (
                    <Tip key={entry.entry_id} content={entry.title}>
                      <button
                        type="button"
                        data-calendar-entry-id={entry.entry_id}
                        onClick={() => onOpenDetail(entry)}
                        className="pointer-events-auto flex w-full items-center gap-1 truncate rounded-[2px] px-1 py-0.5 text-left text-[11px] text-fg transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                      >
                      {!entry.all_day ? (
                        <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                          {formatEventTime(
                            entry.start_at,
                            defaultTimezone,
                            "HH:mm",
                          )}
                        </span>
                      ) : null}
                      <span className="truncate">{entry.title}</span>
                      </button>
                    </Tip>
                  ))}
                  {dayEntries.length > 3 ? (
                    <button
                      type="button"
                      aria-label={`${dayEntries.length - 3} more on ${format(day, "MMM d")}: open day view`}
                      onClick={() =>
                        updateQuery({ range: "day", anchor: day })
                      }
                      className="pointer-events-auto block px-1 font-mono text-[10px] tabular-nums text-[var(--mfg)] transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                    >
                      +{dayEntries.length - 3} more
                    </button>
                  ) : null}
                  {dayOverlays.length > 0 ? (
                    <div className="mt-1 space-y-0.5 border-t border-dashed border-[var(--border)] pt-1">
                      {dayOverlays.slice(0, 3).map((overlay) => (
                        <OverlayPill
                          key={overlay.entry_id}
                          entry={overlay}
                        />
                      ))}
                      {dayOverlays.length > 3 ? (
                        <span className="block px-1 font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                          +{dayOverlays.length - 3} overlay
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
