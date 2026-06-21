/**
 * Printable agenda view (bu-8yi687) — a read-only, print-friendly render mode
 * over the calendar workspace response.
 *
 * Pure frontend: it renders the entries already loaded by the workspace read
 * (`GET /api/calendar/workspace`), grouped by day in chronological order, in a
 * high-contrast layout suited to printing or saving as PDF. No new data fetch,
 * no provider write — a data-portability nicety (owner sovereignty).
 *
 * Print isolation lives in `index.css` (`@media print` keyed on
 * `[data-agenda-print-root]`): printing while the agenda is open shows just the
 * agenda. Interactive chrome (Print / Close) is marked `data-agenda-no-print`.
 */

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { groupEntriesByDay, timeLabel } from "@/lib/calendar-agenda.ts";

export interface CalendarAgendaViewProps {
  entries: UnifiedCalendarEntry[];
  /** Headline describing the range being shown (e.g. "Feb 22 – Feb 28"). */
  rangeLabel: string;
  /** Display timezone label, shown in the agenda subtitle. */
  timezone: string;
  /** Which lane the agenda was opened from (user/butler) — shown for context. */
  view: "user" | "butler";
  onClose: () => void;
}

/**
 * Full-screen, print-friendly agenda overlay. Presentational only — the caller
 * owns the entries (from the workspace read) and the open/close state.
 */
export function CalendarAgendaView({
  entries,
  rangeLabel,
  timezone,
  view,
  onClose,
}: CalendarAgendaViewProps) {
  const days = groupEntriesByDay(entries);

  return (
    <div
      data-agenda-print-root
      role="dialog"
      aria-modal="true"
      aria-label="Printable agenda"
      className="fixed inset-0 z-50 overflow-y-auto bg-[var(--bg)] p-8"
    >
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-[var(--fg)]">Agenda · {rangeLabel}</h1>
            <p className="mt-1 text-sm text-[var(--dim)]">
              {view === "butler" ? "Butler" : "User"} view · {timezone} · {entries.length}{" "}
              {entries.length === 1 ? "event" : "events"}
            </p>
          </div>
          <div data-agenda-no-print className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => window.print()}
              className="rounded border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--fg)] hover:bg-[var(--surface)]"
            >
              Print
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close agenda"
              className="rounded border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--fg)] hover:bg-[var(--surface)]"
            >
              Close
            </button>
          </div>
        </div>

        {days.length === 0 ? (
          <p className="text-sm text-[var(--dim)]">No events in this range.</p>
        ) : (
          <div className="flex flex-col gap-6">
            {days.map((day) => (
              <section key={day.key}>
                <h2 className="mb-2 border-b border-[var(--border)] pb-1 text-sm font-semibold uppercase tracking-wide text-[var(--fg)]">
                  {day.heading}
                </h2>
                <ul className="flex flex-col gap-1.5">
                  {day.entries.map((entry) => {
                    const location =
                      typeof entry.metadata?.location === "string"
                        ? entry.metadata.location
                        : null;
                    return (
                      <li
                        key={entry.entry_id}
                        className="flex items-baseline gap-3 text-sm text-[var(--fg)]"
                      >
                        <span className="w-32 shrink-0 tabular-nums text-[var(--dim)]">
                          {timeLabel(entry)}
                        </span>
                        <span className="min-w-0">
                          <span className="font-medium">{entry.title}</span>
                          {location ? (
                            <span className="text-[var(--dim)]"> · {location}</span>
                          ) : null}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
