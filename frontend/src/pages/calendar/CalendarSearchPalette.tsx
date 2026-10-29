/**
 * Command-palette-style full-text event search over the calendar projection.
 */

import { useEffect, useMemo, useState } from "react";

import type { CalendarWorkspaceView, UnifiedCalendarEntry } from "@/api/types.ts";
import { useCalendarWorkspaceSearch } from "@/hooks/use-calendar-workspace";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Input } from "@/components/ui/input";
import { Mono } from "@/components/ui/Mono";
import { StateDot } from "@/components/ui/StateDot";
import { Voice } from "@/components/ui/Voice";
import { formatEventTime, tzDayKey } from "@/lib/calendar-grid";

/**
 * Full-text search palette over the calendar projection.  Lists matches grouped
 * by day (ranked by trigram relevance from the backend); selecting a match (or
 * pressing Enter on the top result) calls ``onJump`` so the page can navigate
 * the grid to that day and flash the event.
 */
export function CalendarSearchPalette({
  open,
  onOpenChange,
  view,
  timezone,
  displayTimezone,
  onJump,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  view: CalendarWorkspaceView;
  /** Timezone sent to the search endpoint (relative-phrase parsing). */
  timezone: string;
  /** Workspace timezone used to render result times (not browser-local). */
  displayTimezone: string;
  onJump: (entry: UnifiedCalendarEntry) => void;
}) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  // Debounce keystrokes before hitting the search endpoint. (Internal state is
  // reset by remounting: the parent only renders this component while open.)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  const searchView: CalendarWorkspaceView =
    view === "butler" ? "butler" : "user";
  const searchQuery = useCalendarWorkspaceSearch(
    { q: debounced, view: searchView, timezone, limit: 50 },
    { enabled: open },
  );

  const entriesData = searchQuery.data?.data.entries;
  const results = entriesData ?? [];
  // Honest degraded signal: false only when every calendar schema failed to
  // respond, so an empty result means "could not search", not "no matches".
  const searchAvailable = searchQuery.data?.data.available !== false;
  const groups = useMemo(() => {
    const byDay: Array<{
      day: string;
      date: Date;
      items: UnifiedCalendarEntry[];
    }> = [];
    const idx = new Map<string, number>();
    for (const entry of entriesData ?? []) {
      const d = new Date(entry.start_at);
      const key = tzDayKey(d, displayTimezone);
      let gi = idx.get(key);
      if (gi === undefined) {
        gi = byDay.length;
        idx.set(key, gi);
        byDay.push({ day: key, date: d, items: [] });
      }
      byDay[gi].items.push(entry);
    }
    return byDay;
  }, [entriesData, displayTimezone]);

  const trimmed = debounced.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] w-[90vw] max-w-[90vw] flex-col overflow-hidden sm:w-[34rem] sm:max-w-[34rem]">
        <DialogHeader>
          <DialogTitle>Search events</DialogTitle>
          <DialogDescription>
            Find an event by title, description, or location. Press Enter to
            jump to the top match.
          </DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && results.length > 0) {
              event.preventDefault();
              onJump(results[0]);
            }
          }}
          placeholder="Search calendar…"
          aria-label="Search calendar events"
        />
        <div className="mt-2 min-h-0 flex-1 overflow-y-auto">
          {trimmed.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Type to search across all events.
            </Voice>
          ) : searchQuery.isLoading ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Searching…
            </Voice>
          ) : searchQuery.isError ? (
            <div role="alert" className="flex items-start gap-2">
              <StateDot state="error" className="mt-[7px]" />
              <p className="text-sm text-fg">
                Failed to search calendar events. {" "}
                <span className="text-[var(--mfg)]">
                  {searchQuery.error instanceof Error
                    ? searchQuery.error.message
                    : "Unknown error"}
                </span>
              </p>
            </div>
          ) : !searchAvailable ? (
            <div
              role="status"
              className="flex items-start gap-2"
              data-testid="search-unavailable"
            >
              <StateDot state="degraded" className="mt-[7px]" />
              <p className="text-sm text-fg">
                Search is unavailable right now.{" "}
                <span className="text-[var(--mfg)]">
                  The calendar index couldn&rsquo;t be reached. Results may be
                  incomplete. Try again shortly.
                </span>
              </p>
            </div>
          ) : results.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              No matching events.
            </Voice>
          ) : (
            <FetchingDim
              isFetching={searchQuery.isFetching && !searchQuery.isLoading}
            >
              {groups.map((group) => (
                <section key={group.day} className="mb-4">
                  <div className="mb-1 border-b border-[var(--border)] pb-1">
                    <Eyebrow>
                      {formatEventTime(
                        group.date,
                        displayTimezone,
                        "EEE · MMM d, yyyy",
                      )}
                    </Eyebrow>
                  </div>
                  <div role="list">
                    {group.items.map((entry) => (
                      <button
                        key={entry.entry_id}
                        type="button"
                        onClick={() => onJump(entry)}
                        className="flex w-full items-center gap-2 rounded-[3px] px-2 py-1.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                      >
                        <Mono muted className="w-14 shrink-0 tabular-nums">
                          {entry.all_day
                            ? "all day"
                            : formatEventTime(
                                entry.start_at,
                                displayTimezone,
                                "HH:mm",
                              )}
                        </Mono>
                        <span className="truncate text-sm text-fg">
                          {entry.title}
                        </span>
                        {entry.butler_name ? (
                          <Mono
                            muted
                            className="ml-auto hidden shrink-0 sm:inline"
                          >
                            {entry.butler_name}
                          </Mono>
                        ) : null}
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </FetchingDim>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
