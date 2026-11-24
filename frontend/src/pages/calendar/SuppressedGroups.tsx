/**
 * Counted disclosure for same-title events the workspace read held back from the grid, and a note
 * when the page cap ended the fetch with rows still behind it (bu-5ii4gj.4). A packed day must
 * never read as a quiet, complete grid.
 */

import { Time } from "@/components/ui/time";
import type { CalendarSuppressedGroup, UnifiedCalendarEntry } from "@/api/types.ts";

interface SuppressedGroupsProps {
  groups: CalendarSuppressedGroup[];
  truncated: boolean;
  onOpenDetail: (entry: UnifiedCalendarEntry) => void;
}

export function SuppressedGroups({ groups, truncated, onOpenDetail }: SuppressedGroupsProps) {
  if (groups.length === 0 && !truncated) return null;
  return (
    <div className="mb-2 flex flex-col gap-1 text-sm" data-testid="calendar-suppression">
      {truncated ? (
        <p role="status" data-testid="calendar-truncated" className="text-[var(--mfg)]">
          This window is too large to load in full: later events are not shown. Narrow the range.
        </p>
      ) : null}
      {groups.map((group) => (
        <details key={`${group.day}|${group.title}`} data-testid="calendar-suppressed-group">
          <summary className="cursor-pointer text-fg">
            {group.count} hidden: {group.title} on{" "}
            {group.entries[0] ? (
              <Time value={group.entries[0].start_at} mode="absolute" precision="short-date" />
            ) : (
              group.day
            )}
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5 pl-4">
            {group.entries.map((entry) => (
              <li key={entry.entry_id}>
                <button
                  type="button"
                  className="text-left underline-offset-2 hover:underline"
                  onClick={() => onOpenDetail(entry)}
                >
                  <Time value={entry.start_at} mode="absolute" precision="minute" /> {entry.title}
                </button>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}
