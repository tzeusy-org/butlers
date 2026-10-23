/**
 * Masthead for the calendar workspace: eyebrow, period headline, event count
 * and the page-level actions (sync, search, agenda, sources, create butler event).
 */

import { format } from "date-fns";

import type { CalendarWorkspaceView } from "@/api/types.ts";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { CommitButton, PillButton } from "@/pages/calendar/atoms";
import type { CalendarSync } from "@/pages/calendar/use-calendar-sync";
import { headlineLabel, type CalendarRange } from "@/pages/calendar/workspace-model";

interface CalendarHeaderProps {
  view: CalendarWorkspaceView;
  range: CalendarRange;
  timezone: string;
  anchor: Date;
  start: Date;
  end: Date;
  entryCount: number;
  sync: CalendarSync;
  hiddenSourceCount: number;
  butlerPending: boolean;
  onSearch: () => void;
  onAgenda: () => void;
  onSources: () => void;
  onCreateButlerEvent: () => void;
}

export function CalendarHeader({
  view,
  range,
  timezone,
  anchor,
  start,
  end,
  entryCount,
  sync,
  hiddenSourceCount,
  butlerPending,
  onSearch,
  onAgenda,
  onSources,
  onCreateButlerEvent,
}: CalendarHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 pb-5">
      <div className="min-w-0">
        <Eyebrow as="div" className="mb-2.5">
          Calendar · {view === "user" ? "User" : "Butler"} view · {timezone} ·{" "}
          {format(anchor, "yyyy")}
        </Eyebrow>
        <Display>{headlineLabel(range, start, end)}</Display>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Mono muted className="mr-1 tabular-nums">
          {entryCount} {entryCount === 1 ? "event" : "events"}
        </Mono>
        <PillButton
          onClick={sync.handleSyncAll}
          disabled={sync.syncMutation.isPending}
          aria-label="Sync all sources now"
        >
          {sync.syncButtonLabel}
        </PillButton>
        <PillButton onClick={onSearch} aria-label="Search events">
          Search
        </PillButton>
        <PillButton onClick={onAgenda} aria-label="Open printable agenda">
          Agenda
        </PillButton>
        <PillButton onClick={onSources} aria-label="Configure sources">
          Sources
          {hiddenSourceCount > 0 ? (
            <span className="tabular-nums text-[var(--dim)]">
              · {hiddenSourceCount} hidden
            </span>
          ) : null}
        </PillButton>
        {view === "butler" ? (
          <CommitButton onClick={onCreateButlerEvent} disabled={butlerPending}>
            Create butler event
          </CommitButton>
        ) : null}
      </div>
    </header>
  );
}
