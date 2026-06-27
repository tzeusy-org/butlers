import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { useState } from "react";
import { Link } from "react-router";

import type { TimelineEvent } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface UnifiedTimelineProps {
  events: TimelineEvent[];
  isLoading: boolean;
  isError?: boolean;
  onRetry?: () => void;
  onLoadMore?: () => void;
  hasMore?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map event type to badge color classes. */
function eventTypeBadge(type: string) {
  switch (type) {
    case "session":
      return (
        <Badge className="bg-blue-600 text-white hover:bg-blue-600/90 text-[10px]">
          session
        </Badge>
      );
    case "error":
      return (
        <Badge variant="destructive" className="text-[10px]">
          error
        </Badge>
      );
    case "notification":
      return (
        <Badge className="bg-purple-600 text-white hover:bg-purple-600/90 text-[10px]">
          notification
        </Badge>
      );
    default:
      return (
        <Badge variant="outline" className="text-[10px]">
          {type}
        </Badge>
      );
  }
}

// ---------------------------------------------------------------------------
// Heartbeat collapsing logic
// ---------------------------------------------------------------------------

interface CollapsedGroup {
  kind: "single";
  event: TimelineEvent;
}

interface HeartbeatGroup {
  kind: "heartbeat";
  events: TimelineEvent[];
  timestamp: string; // earliest timestamp in the group
}

type DisplayEntry = CollapsedGroup | HeartbeatGroup;

function isHeartbeatEvent(event: TimelineEvent): boolean {
  const summary = event.summary.toLowerCase();
  const triggerSource = ((event.data?.trigger_source as string) ?? "").toLowerCase();
  return (
    summary.includes("heartbeat") ||
    summary.includes("tick") ||
    triggerSource.includes("heartbeat") ||
    triggerSource.includes("tick")
  );
}

function groupEvents(events: TimelineEvent[]): DisplayEntry[] {
  const entries: DisplayEntry[] = [];
  let i = 0;

  while (i < events.length) {
    const event = events[i];

    if (isHeartbeatEvent(event)) {
      // Collect consecutive heartbeat events within 10 minutes
      const group: TimelineEvent[] = [event];
      let j = i + 1;

      while (j < events.length && isHeartbeatEvent(events[j])) {
        const prevTime = new Date(events[j - 1].timestamp).getTime();
        const currTime = new Date(events[j].timestamp).getTime();
        // Events are in reverse chronological order, so prev >= curr
        if (Math.abs(prevTime - currTime) <= 10 * 60 * 1000) {
          group.push(events[j]);
          j++;
        } else {
          break;
        }
      }

      if (group.length > 1) {
        entries.push({
          kind: "heartbeat",
          events: group,
          timestamp: group[0].timestamp,
        });
      } else {
        entries.push({ kind: "single", event });
      }
      i = j;
    } else {
      entries.push({ kind: "single", event });
      i++;
    }
  }

  return entries;
}

// ---------------------------------------------------------------------------
// Single event row
// ---------------------------------------------------------------------------

/** Session-backed event types link to the session detail page. */
function isSessionEvent(event: TimelineEvent): boolean {
  return event.type === "session" || event.type === "error";
}

/** Build the cross-butler session detail href, scoping by butler when known. */
function sessionDetailHref(event: TimelineEvent): string {
  const base = `/sessions/${encodeURIComponent(event.id)}`;
  return event.butler ? `${base}?butler=${encodeURIComponent(event.butler)}` : base;
}

function EventRow({ event }: { event: TimelineEvent }) {
  const [expanded, setExpanded] = useState(false);
  const sessionLink = isSessionEvent(event) ? sessionDetailHref(event) : null;

  return (
    <li className="relative ml-4">
      <div className="absolute -left-[22px] mt-2 size-2.5 rounded-full border-2 border-background bg-muted-foreground/40" />
      <div className="flex w-full items-start gap-3 rounded-md px-2 py-2 transition-colors hover:bg-muted/50">
        <span className="text-xs text-muted-foreground shrink-0 w-[130px] pt-0.5">
          <Time value={event.timestamp} mode="absolute" precision="second" compact showTitle={false} />
        </span>
        <Badge variant="outline" className="text-[10px] shrink-0">
          {event.butler}
        </Badge>
        {eventTypeBadge(event.type)}
        {sessionLink ? (
          <Link
            to={sessionLink}
            className="text-sm truncate text-left underline-offset-4 hover:underline"
          >
            {event.summary}
          </Link>
        ) : (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="text-sm truncate text-left"
          >
            {event.summary}
          </button>
        )}
      </div>
      {expanded && !sessionLink && (
        <div className="ml-[130px] pl-2 pb-2">
          <pre className="rounded-md border bg-muted/30 p-3 text-xs whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
            {JSON.stringify(event.data, null, 2)}
          </pre>
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Heartbeat group row
// ---------------------------------------------------------------------------

function HeartbeatRow({ group }: { group: HeartbeatGroup }) {
  const [expanded, setExpanded] = useState(false);
  const count = group.events.length;
  const butlers = new Set(group.events.map((e) => e.butler));

  return (
    <li className="relative ml-4">
      <div className="absolute -left-[22px] mt-2 size-2.5 rounded-full border-2 border-background bg-muted-foreground/20" />
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-muted/50"
      >
        <span className="text-xs text-muted-foreground shrink-0 w-[130px] pt-0.5">
          <Time value={group.timestamp} mode="absolute" precision="second" compact showTitle={false} />
        </span>
        <Badge variant="outline" className="text-[10px] shrink-0 border-dashed">
          heartbeat
        </Badge>
        <span className="text-sm text-muted-foreground">
          Heartbeat: {count} butlers ticked
          {butlers.size <= 3 && (
            <span className="ml-1 text-xs">
              ({[...butlers].join(", ")})
            </span>
          )}
        </span>
      </button>
      {expanded && (
        <ul className="ml-[130px] pl-2 pb-2 space-y-1">
          {group.events.map((event) => (
            <li
              key={event.id}
              className="flex items-center gap-2 rounded px-2 py-1 text-xs text-muted-foreground"
            >
              <span className="w-[110px] shrink-0">
                <Time value={event.timestamp} mode="absolute" precision="second" compact showTitle={false} />
              </span>
              <Badge variant="outline" className="text-[10px]">
                {event.butler}
              </Badge>
              <span className="truncate">{event.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function TimelineSkeleton() {
  return (
    <div className="space-y-3 pl-6">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-4 w-[130px]" />
          <Skeleton className="h-5 w-16 rounded-full" />
          <Skeleton className="h-5 w-14 rounded-full" />
          <Skeleton className="h-4 flex-1" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <EmptyStateUI
      title="No events found."
      description="Events appear as butlers process sessions and tasks."
    />
  );
}

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div data-testid="timeline-error">
      <EmptyStateUI
        title="Could not load the timeline."
        description="The event stream failed to load. This is not the same as having no activity. Please try again."
        action={
          onRetry ? (
            <Button variant="outline" size="sm" onClick={onRetry}>
              Retry
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// UnifiedTimeline
// ---------------------------------------------------------------------------

export default function UnifiedTimeline({
  events,
  isLoading,
  isError,
  onRetry,
  onLoadMore,
  hasMore,
}: UnifiedTimelineProps) {
  if (isLoading) {
    return <TimelineSkeleton />;
  }

  // A failed fetch must be distinguishable from genuine no-activity: render a
  // dedicated error state (with retry) instead of falling through to EmptyState.
  if (isError) {
    return <ErrorState onRetry={onRetry} />;
  }

  if (events.length === 0) {
    return <EmptyState />;
  }

  const grouped = groupEvents(events);

  return (
    <div>
      <ol className={cn("relative border-l border-border/60 ml-3 space-y-0.5")}>
        {grouped.map((entry, idx) => {
          if (entry.kind === "heartbeat") {
            return <HeartbeatRow key={`hb-${idx}`} group={entry} />;
          }
          return <EventRow key={entry.event.id} event={entry.event} />;
        })}
      </ol>
      {onLoadMore && hasMore && (
        <div className="flex justify-center mt-6">
          <Button variant="outline" size="sm" onClick={onLoadMore}>
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}
