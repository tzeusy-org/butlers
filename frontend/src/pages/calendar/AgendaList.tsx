/** Agenda list: entries grouped by workspace-timezone day. */

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { LinkedPeopleAvatars } from "@/components/calendar/LinkedPeopleAvatars";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { Voice } from "@/components/ui/Voice";
import { formatEventTime, tzDayKey } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { KindTag, PillButton } from "@/pages/calendar/atoms";
import { isCancelledEntry } from "@/pages/calendar/workspace-model";

interface AgendaListProps {
  entries: UnifiedCalendarEntry[];
  defaultTimezone: string;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
}

export function AgendaList({
  entries,
  defaultTimezone,
  onOpenDetail,
}: AgendaListProps) {
  if (entries.length === 0) {
    return (
      <Voice variant="italic" className="text-[var(--mfg)]">
        No events in this range.
      </Voice>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto pr-1">
      {(() => {
        const groups: Array<{
          day: string;
          date: Date;
          items: UnifiedCalendarEntry[];
        }> = [];
        const groupIndex = new Map<string, number>();
        // Group by the event's calendar day in the workspace timezone so
        // sections match the times rendered in each row.
        const todayKey = formatEventTime(
          new Date(),
          defaultTimezone,
          "yyyy-MM-dd",
        );
        for (const entry of entries) {
          const d = new Date(entry.start_at);
          const dayKey = tzDayKey(d, defaultTimezone);
          let gi = groupIndex.get(dayKey);
          if (gi === undefined) {
            gi = groups.length;
            groupIndex.set(dayKey, gi);
            groups.push({
              day: dayKey,
              date: d,
              items: [],
            });
          }
          groups[gi].items.push(entry);
        }
        return groups.map((group) => (
          <section key={group.day} className="mb-6">
            <div className="mb-1 flex items-baseline gap-2 border-b border-[var(--border)] pb-1.5">
              <Eyebrow
                className={cn(
                  group.day === todayKey && "text-fg",
                )}
              >
                {formatEventTime(
                  group.date,
                  defaultTimezone,
                  "EEE · MMM d",
                )}
              </Eyebrow>
              {group.day === todayKey ? (
                <KindTag className="text-[var(--mfg)]">today</KindTag>
              ) : null}
            </div>
            <div role="list">
              {group.items.map((entry) => {
                return (
                  <Row
                    key={entry.entry_id}
                    mark={
                      <Mono
                        muted
                        className="inline-block w-14 tabular-nums"
                      >
                        {entry.all_day
                          ? "all day"
                          : formatEventTime(
                              entry.start_at,
                              defaultTimezone,
                              "HH:mm",
                            )}
                      </Mono>
                    }
                    meta={
                      <div className="flex items-center gap-1.5">
                        <PillButton
                          onClick={() => onOpenDetail(entry)}
                        >
                          Detail
                        </PillButton>
                      </div>
                    }
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      {entry.butler_name ? (
                        <ButlerMark name={entry.butler_name} />
                      ) : null}
                      <span
                        className={cn(
                          "truncate text-sm text-fg",
                          isCancelledEntry(entry) &&
                            "text-[var(--mfg)] line-through",
                        )}
                      >
                        {entry.title}
                      </span>
                      {isCancelledEntry(entry) ? (
                        <KindTag
                          data-testid="entry-cancelled-tag"
                          className="text-[var(--red-text)]"
                        >
                          cancelled
                        </KindTag>
                      ) : null}
                      {entry.linked_people &&
                      entry.linked_people.length > 0 ? (
                        <LinkedPeopleAvatars
                          people={entry.linked_people}
                        />
                      ) : null}
                      <Mono muted className="hidden truncate sm:inline">
                        {entry.butler_name ?? entry.source_key}
                      </Mono>
                    </div>
                  </Row>
                );
              })}
            </div>
          </section>
        ));
      })()}
    </div>
  );
}
