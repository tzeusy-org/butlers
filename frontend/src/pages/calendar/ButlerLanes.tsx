/**
 * Butler lanes: one section per butler listing its scheduled tasks and
 * reminders with edit, pause/resume, snooze, dismiss and delete.
 */

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { Voice } from "@/components/ui/Voice";
import { formatEventTime } from "@/lib/calendar-grid";
import { KindTag, PillButton, SnoozeMenu } from "@/pages/calendar/atoms";
import {
  RECURRING_INSTANCE_CAP,
  capLaneEntriesByDay,
  isOverflowSentinel,
  isPausedEntry,
  type ButlerLaneRows,
} from "@/pages/calendar/workspace-model";

interface ButlerLanesProps {
  lanes: ButlerLaneRows[];
  defaultTimezone: string;
  pending: boolean;
  onAddEvent: (butlerName: string) => void;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
  onToggle: (entry: UnifiedCalendarEntry) => void;
  onSnooze: (entry: UnifiedCalendarEntry, iso: string) => void;
  onDismiss: (entry: UnifiedCalendarEntry) => void;
  onDelete: (entry: UnifiedCalendarEntry) => void;
}

export function ButlerLanes({
  lanes,
  defaultTimezone,
  pending,
  onAddEvent,
  onOpenDetail,
  onToggle,
  onSnooze,
  onDismiss,
  onDelete,
}: ButlerLanesProps) {
  if (lanes.length === 0) {
    return (
      <Voice variant="italic" className="text-[var(--mfg)]">
        No butler lanes yet.
      </Voice>
    );
  }
  return (
    <div className="min-h-0 flex-1 space-y-8 overflow-y-auto pr-1">
      {lanes.map((lane) => (
        <section key={lane.laneId}>
          <div className="mb-1 flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2">
            <div className="flex min-w-0 items-center gap-2.5">
              <ButlerMark name={lane.butlerName} tone="fill" />
              <span className="truncate text-[15px] font-medium text-fg">
                {lane.title}
              </span>
              <Mono muted className="tabular-nums">
                {lane.entries.length}{" "}
                {lane.entries.length === 1 ? "event" : "events"}
              </Mono>
            </div>
            <PillButton
              onClick={() => onAddEvent(lane.butlerName)}
              disabled={pending}
            >
              Add event
            </PillButton>
          </div>

          {lane.entries.length === 0 ? (
            <Voice
              variant="italic"
              className="py-2 text-[var(--mfg)]"
            >
              No events in this lane.
            </Voice>
          ) : (
            <div role="list">
              {capLaneEntriesByDay(
                lane.entries,
                RECURRING_INSTANCE_CAP,
                defaultTimezone,
              ).map((item) =>
                isOverflowSentinel(item) ? (
                  <div
                    key={item.sentinelKey}
                    data-testid="butler-lane-row"
                    className="border-b border-[var(--border-soft)] py-2 pl-[68px]"
                  >
                    <span className="font-serif text-[13px] italic text-[var(--mfg)]">
                      and {item.hiddenCount} more instance
                      {item.hiddenCount === 1 ? "" : "s"} of &ldquo;
                      {item.title}&rdquo;
                    </span>
                  </div>
                ) : (
                  <Row
                    key={item.entry_id}
                    data-testid="butler-lane-row"
                    mark={
                      <Mono
                        muted
                        className="inline-block w-14 tabular-nums"
                      >
                        {item.all_day
                          ? "all day"
                          : formatEventTime(
                              item.start_at,
                              defaultTimezone,
                              "HH:mm",
                            )}
                      </Mono>
                    }
                    meta={
                      <div className="flex items-center gap-1.5">
                        <PillButton
                          onClick={() => onOpenDetail(item)}
                          disabled={pending}
                        >
                          Edit
                        </PillButton>
                        <PillButton
                          onClick={() => onToggle(item)}
                          disabled={pending}
                        >
                          {isPausedEntry(item) ? "Resume" : "Pause"}
                        </PillButton>
                        <SnoozeMenu
                          disabled={pending}
                          onSnooze={(iso) =>
                            onSnooze(item, iso)
                          }
                        />
                        {item.source_type === "butler_reminder" ? (
                          <PillButton
                            data-testid="butler-dismiss-button"
                            onClick={() => onDismiss(item)}
                            disabled={pending}
                            className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
                          >
                            Dismiss
                          </PillButton>
                        ) : null}
                        <PillButton
                          onClick={() => onDelete(item)}
                          disabled={pending}
                          className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
                        >
                          Delete
                        </PillButton>
                      </div>
                    }
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm text-fg">
                        {item.title}
                      </span>
                      <KindTag>
                        {item.source_type === "scheduled_task"
                          ? "schedule"
                          : "reminder"}
                      </KindTag>
                      {isPausedEntry(item) ? (
                        <KindTag className="text-[var(--mfg)]">
                          paused
                        </KindTag>
                      ) : null}
                    </div>
                  </Row>
                ),
              )}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
