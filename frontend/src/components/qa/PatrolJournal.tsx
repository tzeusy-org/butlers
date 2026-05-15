import type { QaJournalEvent } from "@/api/types";
import { cn } from "@/lib/utils";

interface PatrolJournalProps {
  events: QaJournalEvent[];
  patrolIntervalMinutes?: number;
  className?: string;
}

const stepClassName: Record<string, string> = {
  "cross-checked": "text-foreground",
  concluded: "text-emerald-500",
  considered: "text-muted-foreground",
  drafted: "text-foreground",
  escalated: "text-amber-500",
  flagged: "text-amber-500",
  merged: "text-emerald-500",
  opened: "text-amber-500",
  sampled: "text-foreground",
  tick: "text-muted-foreground",
  wait: "text-muted-foreground",
};

function formatJournalTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
}

export function PatrolJournal({
  events,
  patrolIntervalMinutes = 10,
  className,
}: PatrolJournalProps) {
  if (events.length === 0) return null;

  const orderedEvents = [...events].sort((a, b) => {
    const left = new Date(a.ts).getTime();
    const right = new Date(b.ts).getTime();
    if (Number.isNaN(left) || Number.isNaN(right)) return 0;
    return left - right;
  });

  return (
    <section className={cn("space-y-3", className)} aria-label="Patrol journal">
      <div className="flex flex-wrap items-baseline gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          Patrol journal · every QA decision on this case
        </p>
        <p className="ml-auto font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground tnum">
          {events.length} {events.length === 1 ? "entry" : "entries"} · patrol every{" "}
          {patrolIntervalMinutes}m
        </p>
      </div>
      <div className="divide-y divide-border/60 border-y border-border/60 font-mono text-[10px] tnum">
        {orderedEvents.map((event) => (
          <div
            key={event.id}
            className="grid grid-cols-[64px_96px_minmax(0,1fr)] gap-3 px-1 py-2"
          >
            <time className="text-muted-foreground" dateTime={event.ts}>
              {formatJournalTime(event.ts)}
            </time>
            <span
              className={cn(
                "uppercase tracking-[0.12em]",
                stepClassName[event.step] ?? "text-muted-foreground",
              )}
              data-testid={`qa-journal-step-${event.step}`}
            >
              {event.step}
            </span>
            <div className="min-w-0 space-y-1">
              <p className="whitespace-pre-wrap break-words text-foreground">{event.text}</p>
              {event.detail ? (
                <p className="whitespace-pre-wrap break-words text-muted-foreground">
                  {event.detail}
                </p>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
