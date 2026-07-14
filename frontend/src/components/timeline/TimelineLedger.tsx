/**
 * TimelineLedger — the fleet chronicle's event stream (/timeline,
 * bu-86c4c.10 — "One Timeline").
 *
 * Rebuilt on the same Dispatch-language ledger system as the ingestion
 * timeline (bu-4utdw): hour-grouped, hairline-divided rows, a URL-backed
 * event drawer (`?event=<id>`, via the ingestion ledger's
 * useEventDrawerState — a small, fully generic hook with no
 * ingestion-specific content). Replaces UnifiedTimeline.tsx, whose vertical
 * dotted-timeline layout and client-side heartbeat sniff were the abandoned
 * pre-redesign version of this exact surface.
 *
 * Heartbeat classification is server-side (`event.is_heartbeat`, bu-86c4c.9)
 * — consecutive heartbeat events collapse into one row with an honest
 * "{ticks} ticks · {butlers} butlers ticked" line computed from the group's
 * own members (the old UnifiedTimeline bug rendered the tick count where a
 * distinct-butler count belonged).
 *
 * Spec: docs/redesigns/2026-07-03-jarvis-audit.md §"7. One Timeline"
 */

import { useState } from "react";
import { Link } from "react-router";

import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { ErrorState as ErrorStateUI } from "@/components/ui/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useEventDrawerState } from "@/components/ingestion/timeline/useEventDrawerState";
import type { TimelineEvent } from "@/api/types.ts";
import { TimelineEventDrawer } from "./TimelineEventDrawer";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface TimelineLedgerProps {
  events: TimelineEvent[];
  isLoading: boolean;
  isError?: boolean;
  onRetry?: () => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
  isLoadingMore?: boolean;
}

// ---------------------------------------------------------------------------
// Type badge — quiet dot + word, never a filled pill in rows (Dispatch
// visual language — matches the ingestion ledger's row status convention).
// ---------------------------------------------------------------------------

const TYPE_DOT_CLASS: Record<string, string> = {
  session: "bg-blue-500",
  error: "bg-destructive",
  notification: "bg-purple-500",
};

// A notification whose delivery bounced (data.status === "failed") is a
// failure impersonating health if rendered with the calm purple dot — an
// hours-long bounced-alert outage read as routine. Give it the destructive
// mark so the row is legible as an error at a glance, matching the Errors lens
// that now also selects these deliveries server-side (bu-hmdqz.14).
function isFailedNotification(event: TimelineEvent): boolean {
  return event.type === "notification" && event.data?.status === "failed";
}

function TypeBadge({ type, failed = false }: { type: string; failed?: boolean }) {
  const dotClass = failed ? "bg-destructive" : (TYPE_DOT_CLASS[type] ?? "bg-muted-foreground/50");
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-mono text-[11px] shrink-0 ${
        failed ? "text-destructive" : "text-muted-foreground"
      }`}
    >
      <span className={`size-1.5 rounded-full ${dotClass}`} />
      {failed ? "failed" : type}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Session-detail link (for the row's session/error affordance)
// ---------------------------------------------------------------------------

function isSessionEvent(event: TimelineEvent): boolean {
  return event.type === "session" || event.type === "error";
}

function sessionDetailHref(event: TimelineEvent): string {
  const base = `/sessions/${encodeURIComponent(event.id)}`;
  return event.butler ? `${base}?butler=${encodeURIComponent(event.butler)}` : base;
}

// ---------------------------------------------------------------------------
// Grouping — by hour, with consecutive heartbeats collapsed
// ---------------------------------------------------------------------------

function hourGroupKey(timestamp: string): string {
  // "2026-07-04T14:32:10Z" -> "2026-07-04T14"
  return timestamp.length >= 13 ? timestamp.slice(0, 13) : "unknown";
}

interface SingleEntry {
  kind: "single";
  event: TimelineEvent;
}

interface HeartbeatEntry {
  kind: "heartbeat";
  events: TimelineEvent[];
}

type LedgerEntry = SingleEntry | HeartbeatEntry;

function groupHeartbeats(events: TimelineEvent[]): LedgerEntry[] {
  const entries: LedgerEntry[] = [];
  let i = 0;
  while (i < events.length) {
    const event = events[i];
    if (event.is_heartbeat) {
      const group: TimelineEvent[] = [event];
      let j = i + 1;
      while (j < events.length && events[j].is_heartbeat) {
        group.push(events[j]);
        j++;
      }
      entries.push(group.length > 1 ? { kind: "heartbeat", events: group } : { kind: "single", event });
      i = j;
    } else {
      entries.push({ kind: "single", event });
      i++;
    }
  }
  return entries;
}

interface HourGroup {
  hourKey: string;
  entries: LedgerEntry[];
}

function groupByHour(events: TimelineEvent[]): HourGroup[] {
  const groups: HourGroup[] = [];
  let currentKey: string | null = null;
  let currentEvents: TimelineEvent[] = [];

  const flush = () => {
    if (currentKey !== null) {
      groups.push({ hourKey: currentKey, entries: groupHeartbeats(currentEvents) });
    }
  };

  for (const event of events) {
    const key = hourGroupKey(event.timestamp);
    if (key !== currentKey) {
      flush();
      currentKey = key;
      currentEvents = [event];
    } else {
      currentEvents.push(event);
    }
  }
  flush();
  return groups;
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

const ROW_GRID = "96px 120px 92px 1fr 18px";

function EventRow({
  event,
  isOpen,
  onToggle,
}: {
  event: TimelineEvent;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const sessionLink = isSessionEvent(event) ? sessionDetailHref(event) : null;

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle();
    }
  }

  return (
    <div
      className={[
        "grid items-center gap-x-3 px-3 py-2 border-b border-border/50 text-[13px] transition-colors cursor-pointer group",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset",
        isOpen ? "bg-muted/20" : "hover:bg-muted/10",
      ].join(" ")}
      style={{ gridTemplateColumns: ROW_GRID }}
      onClick={onToggle}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="button"
      aria-expanded={isOpen}
      data-testid="timeline-row"
      data-event-id={event.id}
    >
      <Time
        value={event.timestamp}
        mode="absolute"
        precision="time-seconds"
        className="font-mono tabular-nums text-[11px] text-muted-foreground"
      />
      <span className="font-mono text-[11px] text-muted-foreground truncate">{event.butler}</span>
      <TypeBadge type={event.type} failed={isFailedNotification(event)} />
      <span className="truncate font-serif text-[13px] leading-[1.5]" title={event.summary}>
        {event.summary}
      </span>
      {sessionLink ? (
        <Link
          to={sessionLink}
          onClick={(e) => e.stopPropagation()}
          className="font-mono text-[10px] text-muted-foreground select-none hover:text-foreground transition-colors"
          data-testid="row-session-link"
          aria-label="View session"
        >
          ↗
        </Link>
      ) : (
        <span className="font-mono text-[10px] text-muted-foreground select-none">
          {isOpen ? "▲" : "▼"}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Heartbeat group row — honest rollup computed from this group's own events,
// not the event-count-mislabeled-as-butler-count bug UnifiedTimeline had.
// ---------------------------------------------------------------------------

function HeartbeatGroupRow({ events }: { events: TimelineEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const ticks = events.length;
  const butlers = new Set(events.map((e) => e.butler));
  const failed = events.filter((e) => e.data?.success === false).length;

  return (
    <div className="border-b border-border/50">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-muted/10"
        data-testid="heartbeat-group-row"
        aria-expanded={expanded}
      >
        <span className="font-mono text-[11px] text-muted-foreground w-[96px] shrink-0">
          <Time value={events[0].timestamp} mode="absolute" precision="time-seconds" />
        </span>
        <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground shrink-0">
          <span className="size-1.5 rounded-full border border-dashed border-muted-foreground/60" />
          heartbeat
        </span>
        <span className="text-[13px] text-muted-foreground">
          {ticks} {ticks === 1 ? "tick" : "ticks"} · {butlers.size} {butlers.size === 1 ? "butler" : "butlers"} ticked
          {failed > 0 && <span className="text-destructive"> · {failed} failed</span>}
        </span>
      </button>
      {expanded && (
        <div className="pl-[96px] pb-2 space-y-1">
          {events.map((event) => (
            <div key={event.id} className="flex items-center gap-2 px-3 text-[11px] text-muted-foreground">
              <span className="font-mono w-[100px] shrink-0">
                <Time value={event.timestamp} mode="absolute" precision="time-seconds" />
              </span>
              <span className="font-mono truncate">{event.butler}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hour group header + body
// ---------------------------------------------------------------------------

function HourGroupSection({
  group,
  drawerEventId,
  onOpenDrawer,
  onCloseDrawer,
}: {
  group: HourGroup;
  drawerEventId: string | null;
  onOpenDrawer: (id: string) => void;
  onCloseDrawer: () => void;
}) {
  const hourStart = group.hourKey !== "unknown" ? `${group.hourKey}:00:00Z` : "";

  return (
    <div data-testid="hour-group" data-hour-key={group.hourKey}>
      <div className="flex items-center gap-3 px-3 py-1.5 bg-muted/10 border-b border-border/50">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {hourStart ? <Time value={hourStart} mode="absolute" precision="hour" compact /> : "Unknown time"}
        </span>
      </div>

      {group.entries.map((entry) => {
        if (entry.kind === "heartbeat") {
          return <HeartbeatGroupRow key={`hb-${entry.events[0].id}`} events={entry.events} />;
        }
        const event = entry.event;
        const isOpen = drawerEventId === event.id;
        return (
          <div key={event.id}>
            <EventRow
              event={event}
              isOpen={isOpen}
              onToggle={() => (isOpen ? onCloseDrawer() : onOpenDrawer(event.id))}
            />
            {isOpen && <TimelineEventDrawer event={event} onClose={onCloseDrawer} />}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading / empty / error states
// ---------------------------------------------------------------------------

function LedgerSkeleton() {
  return (
    <div className="space-y-2 pt-2">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex items-center gap-3 px-3">
          <Skeleton className="h-4 w-[96px]" />
          <Skeleton className="h-4 w-[110px]" />
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 flex-1" />
        </div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      variant="page"
      title="No events found."
      description="Events appear as butlers process sessions and deliver notifications."
    />
  );
}

// ---------------------------------------------------------------------------
// Off-page ?event= resolution (bu-qvnce.13, pursuit move 13) — a deep link
// to an event outside the currently-loaded window previously matched no row
// and silently rendered nothing (the drawer just never opened). This makes
// that honest: the owner sees why the drawer isn't open, with a way to clear
// the stale link, instead of a page that looks like the event doesn't exist.
// ---------------------------------------------------------------------------

function EventNotFoundNotice({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  return (
    <div
      className="mb-2 flex items-center justify-between gap-3 rounded border border-[var(--amber)]/30 bg-[var(--amber)]/5 px-3 py-2 font-mono text-[11px] text-[var(--amber-text)]"
      data-testid="timeline-event-not-found"
    >
      <span>
        Event <span className="font-medium">{eventId}</span> isn&apos;t in the currently loaded
        window. It may be older than what&apos;s shown here, or no longer in the live feed.
      </span>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 shrink-0 px-2"
        onClick={onClose}
        data-testid="timeline-event-not-found-dismiss"
      >
        Clear link
      </Button>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div data-testid="timeline-error">
      <ErrorStateUI
        title="Could not load the timeline."
        description="The event stream failed to load. This is not the same as having no activity."
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
// TimelineLedger
// ---------------------------------------------------------------------------

export function TimelineLedger({
  events,
  isLoading,
  isError,
  onRetry,
  hasMore,
  onLoadMore,
  isLoadingMore,
}: TimelineLedgerProps) {
  const { eventId: drawerEventId, openDrawer, closeDrawer } = useEventDrawerState();

  if (isLoading) {
    return <LedgerSkeleton />;
  }

  if (isError) {
    return <ErrorState onRetry={onRetry} />;
  }

  // A ?event= deep link whose id isn't in the currently-loaded window (e.g.
  // it scrolled out after "Load older", or the id is simply stale/wrong) —
  // resolve it honestly instead of a drawer that silently never opens.
  const drawerEventMissing = drawerEventId !== null && !events.some((e) => e.id === drawerEventId);

  if (events.length === 0) {
    return (
      <>
        {drawerEventMissing && drawerEventId && (
          <EventNotFoundNotice eventId={drawerEventId} onClose={closeDrawer} />
        )}
        <EmptyState />
      </>
    );
  }

  const hourGroups = groupByHour(events);

  return (
    <div>
      {drawerEventMissing && drawerEventId && (
        <EventNotFoundNotice eventId={drawerEventId} onClose={closeDrawer} />
      )}
      {hourGroups.map((group) => (
        <HourGroupSection
          key={group.hourKey}
          group={group}
          drawerEventId={drawerEventId}
          onOpenDrawer={openDrawer}
          onCloseDrawer={closeDrawer}
        />
      ))}
      {onLoadMore && (hasMore || isLoadingMore) && (
        <div className="flex justify-center pt-4">
          <Button variant="outline" size="sm" onClick={onLoadMore} disabled={isLoadingMore}>
            {isLoadingMore ? "Loading…" : "Load older"}
          </Button>
        </div>
      )}
    </div>
  );
}
