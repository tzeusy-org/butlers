/**
 * NewEventsPill — "N new events" live-tail affordance (/timeline,
 * bu-86c4c.10 — "One Timeline").
 *
 * Replaces the old auto-refresh-toggle + manual "Load more" accumulator.
 * While pinned to the live head, new events simply appear. The moment the
 * owner pages into history (Load older), new arrivals are counted here
 * instead of silently reordering the list underneath them — clicking jumps
 * back to now.
 */

import { ArrowUp } from "lucide-react";

export interface NewEventsPillProps {
  count: number;
  onClick: () => void;
}

export function NewEventsPill({ count, onClick }: NewEventsPillProps) {
  if (count <= 0) return null;

  return (
    <div className="flex justify-center py-2">
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 font-mono text-[11px] text-foreground shadow-sm hover:bg-muted/50 transition-colors"
        data-testid="new-events-pill"
      >
        <ArrowUp className="size-3" />
        {count} new {count === 1 ? "event" : "events"}
      </button>
    </div>
  );
}
