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
 * Machine classification is server-side (`event.machine_class`, with the
 * legacy `is_heartbeat` fallback retained for rolling deploys) — consecutive
 * heartbeat events collapse into one row with an honest
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
import {
  isFailedMaintenanceEvent,
  isSuccessfulMaintenanceEvent,
  maintenanceRunStatus,
  timelineMachineClass,
} from "@/lib/timeline-machine-class";
import { TimelineEventDrawer } from "./TimelineEventDrawer";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface TimelineLedgerProps {
  events: TimelineEvent[];
  isLoading: boolean;
  /** Reveal reviewed internal maintenance runs instead of the owner lens. */
  includeInternal?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  /** The current snapshot excludes one or more unavailable Timeline sources. */
  hasPartialData?: boolean;
  hasMore?: boolean;
  onLoadMore?: () => void;
  /** An older-page request failed after the ledger had already rendered rows. */
  loadMoreError?: boolean;
  /** Retries the retained older-page cursor after a pagination failure. */
  onRetryLoadMore?: () => void;
  isLoadingMore?: boolean;
}

// ---------------------------------------------------------------------------
// Type badge — quiet dot + word, never a filled pill in rows (Dispatch
// visual language — matches the ingestion ledger's row status convention).
// ---------------------------------------------------------------------------

const TYPE_DOT_CLASS: Record<string, string> = {
  session: "bg-[var(--categorical-1)]",
  error: "bg-destructive",
  notification: "bg-[var(--categorical-2)]",
};

// A notification whose delivery bounced (data.status === "failed") is a
// failure impersonating health if rendered with the calm categorical dot — an
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
// Grouping — by hour, with consecutive heartbeats and opt-in maintenance
// rollups. Machine class is API-owned; this view never interprets a summary.
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

interface MaintenanceEntry {
  kind: "maintenance";
  butler: string;
  events: TimelineEvent[];
}

type LedgerEntry = SingleEntry | HeartbeatEntry | MaintenanceEntry;

function groupLedgerEntries(events: TimelineEvent[], includeInternal: boolean): LedgerEntry[] {
  const entries: LedgerEntry[] = [];
  const maintenanceByButler = new Map<string, MaintenanceEntry>();
  let previousSourceWasHeartbeat = false;

  for (const event of events) {
    const machineClass = timelineMachineClass(event);
    if (machineClass === "maintenance") {
      // A hidden or separately-rendered maintenance run is still a boundary
      // between raw heartbeat events; do not merge heartbeat groups across it.
      previousSourceWasHeartbeat = false;
      if (includeInternal) {
        const butler = event.butler || "Unassigned";
        const existingGroup = maintenanceByButler.get(butler);
        if (existingGroup) {
          existingGroup.events.push(event);
        } else {
          const group: MaintenanceEntry = { kind: "maintenance", butler, events: [event] };
          maintenanceByButler.set(butler, group);
          entries.push(group);
        }
      } else if (!isSuccessfulMaintenanceEvent(event)) {
        // Failed, running, and unknown runs remain visible in the owner lens.
        entries.push({ kind: "single", event });
      }
      continue;
    }

    if (machineClass === "heartbeat") {
      const previousEntry = entries.at(-1);
      if (previousSourceWasHeartbeat && previousEntry?.kind === "heartbeat") {
        previousEntry.events.push(event);
      } else {
        entries.push({ kind: "heartbeat", events: [event] });
      }
      previousSourceWasHeartbeat = true;
    } else {
      entries.push({ kind: "single", event });
      previousSourceWasHeartbeat = false;
    }
  }

  return entries;
}

interface HourGroup {
  hourKey: string;
  entries: LedgerEntry[];
}

function groupByHour(events: TimelineEvent[], includeInternal: boolean): HourGroup[] {
  const groups: HourGroup[] = [];
  let currentKey: string | null = null;
  let currentEvents: TimelineEvent[] = [];

  const flush = () => {
    if (currentKey !== null) {
      const entries = groupLedgerEntries(currentEvents, includeInternal);
      if (entries.length > 0) {
        groups.push({ hourKey: currentKey, entries });
      }
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

const ROW_GRID = "96px 120px 92px 1fr 24px";
const ROW_DISCLOSURE_GRID = "96px 120px 92px minmax(0, 1fr)";

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

  return (
    <div
      className={[
        "grid items-center gap-x-3 px-3 py-2 border-b border-border/50 text-[13px] transition-colors group",
        isOpen ? "bg-muted/20" : "hover:bg-muted/10",
      ].join(" ")}
      style={{ gridTemplateColumns: ROW_GRID }}
      data-testid="timeline-row"
      data-event-id={event.id}
    >
      <button
        type="button"
        className={`${sessionLink ? "col-span-4" : "col-span-5"} grid min-h-6 min-w-0 items-center gap-x-3 bg-transparent p-0 text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset`}
        style={{ gridTemplateColumns: sessionLink ? ROW_DISCLOSURE_GRID : ROW_GRID }}
        onClick={onToggle}
        aria-expanded={isOpen}
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
        {!sessionLink && (
          <span className="font-mono text-[10px] text-muted-foreground select-none">
            {isOpen ? "▲" : "▼"}
          </span>
        )}
      </button>
      {sessionLink ? (
        <Link
          to={sessionLink}
          className="inline-flex size-6 items-center justify-center font-mono text-[10px] text-muted-foreground select-none transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset"
          data-testid="row-session-link"
          aria-label="View session"
        >
          ↗
        </Link>
      ) : null}
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
// Maintenance group row — only shown in the explicit Internal lens. The
// count is intentionally limited to the currently loaded Timeline page.
// ---------------------------------------------------------------------------

function MaintenanceGroupRow({ butler, events }: { butler: string; events: TimelineEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const runs = events.length;
  const failed = events.filter(isFailedMaintenanceEvent).length;
  const runLabel = runs === 1 ? "maintenance run" : "maintenance runs";
  const summary = `${butler}: ${runs} ${runLabel}`;

  return (
    <div className="border-b border-border/50">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-3 py-1.5 text-left transition-colors hover:bg-muted/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset"
        data-testid="maintenance-group-row"
        aria-expanded={expanded}
        aria-label={`${summary}${failed > 0 ? `, ${failed} failed` : ""}. ${expanded ? "Hide" : "Show"} details`}
      >
        <span className="font-mono text-[11px] text-muted-foreground w-[96px] shrink-0">
          <Time value={events[0].timestamp} mode="absolute" precision="time-seconds" />
        </span>
        <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground shrink-0">
          <span className="size-1.5 rounded-full border border-dashed border-muted-foreground/60" />
          internal
        </span>
        <span className="text-[13px] text-muted-foreground">
          {summary}
          {failed > 0 && <span className="text-destructive"> · {failed} failed</span>}
        </span>
      </button>
      {expanded && (
        <div className="pl-[96px] pb-2 space-y-1">
          {events.map((event) => {
            const status = maintenanceRunStatus(event);
            const failedRun = status === "failed";
            return (
              <div key={event.id} className="flex items-center gap-2 px-3 text-[11px] text-muted-foreground">
                <span className="font-mono w-[100px] shrink-0">
                  <Time value={event.timestamp} mode="absolute" precision="time-seconds" />
                </span>
                <span
                  className={failedRun ? "font-mono text-destructive" : "font-mono"}
                  data-testid="maintenance-run-status"
                >
                  {status}
                </span>
                <span className="truncate" title={event.summary}>
                  {event.summary}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SingleEventEntry({
  event,
  drawerEventId,
  onOpenDrawer,
  onCloseDrawer,
}: {
  event: TimelineEvent;
  drawerEventId: string | null;
  onOpenDrawer: (id: string) => void;
  onCloseDrawer: () => void;
}) {
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
          if (entry.events.length > 1) {
            return <HeartbeatGroupRow key={`hb-${entry.events[0].id}`} events={entry.events} />;
          }
          return (
            <SingleEventEntry
              key={entry.events[0].id}
              event={entry.events[0]}
              drawerEventId={drawerEventId}
              onOpenDrawer={onOpenDrawer}
              onCloseDrawer={onCloseDrawer}
            />
          );
        }
        if (entry.kind === "maintenance") {
          return (
            <MaintenanceGroupRow
              key={`maintenance-${entry.butler}`}
              butler={entry.butler}
              events={entry.events}
            />
          );
        }
        return (
          <SingleEventEntry
            key={entry.event.id}
            event={entry.event}
            drawerEventId={drawerEventId}
            onOpenDrawer={onOpenDrawer}
            onCloseDrawer={onCloseDrawer}
          />
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

function PartialEmptyState({
  title = "Timeline data is partially unavailable.",
  description = "No events were returned by reachable sources, so this is not a complete empty result.",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div role="status" aria-live="polite" data-testid="timeline-partial-empty">
      <EmptyStateUI
        variant="page"
        title={title}
        description={description}
      />
    </div>
  );
}

function InternalMaintenanceEmptyState() {
  return (
    <EmptyStateUI
      variant="page"
      title="No owner activity in this window."
      description="Enable Internal activity to inspect scheduled maintenance runs."
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
  includeInternal = false,
  isError,
  onRetry,
  hasPartialData = false,
  hasMore,
  onLoadMore,
  loadMoreError = false,
  onRetryLoadMore,
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
        {hasPartialData ? <PartialEmptyState /> : <EmptyState />}
      </>
    );
  }

  const hourGroups = groupByHour(events, includeInternal);

  return (
    <div>
      {drawerEventMissing && drawerEventId && (
        <EventNotFoundNotice eventId={drawerEventId} onClose={closeDrawer} />
      )}
      {hourGroups.length === 0 ? (
        hasPartialData ? (
          <PartialEmptyState
            title="Owner activity is partially unavailable."
            description="Only scheduled maintenance runs were returned by reachable sources. One or more Timeline sources are unavailable, so this is not a complete owner-activity result. Enable Internal activity to inspect the maintenance runs."
          />
        ) : (
          <InternalMaintenanceEmptyState />
        )
      ) : (
        hourGroups.map((group) => (
          <HourGroupSection
            key={group.hourKey}
            group={group}
            drawerEventId={drawerEventId}
            onOpenDrawer={openDrawer}
            onCloseDrawer={closeDrawer}
          />
        ))
      )}
      {loadMoreError ? (
        <div
          className="mt-4 flex flex-wrap items-center justify-center gap-2 rounded border border-[var(--amber)]/30 bg-[var(--amber)]/5 px-3 py-2 font-mono text-[11px] text-[var(--amber-text)]"
          data-testid="timeline-load-more-error"
        >
          <span role="status" aria-live="polite">
            Older timeline events are temporarily unavailable.
          </span>
          {onRetryLoadMore && (
            <Button type="button" variant="outline" size="xs" onClick={onRetryLoadMore}>
              Retry older events
            </Button>
          )}
        </div>
      ) : (
        onLoadMore &&
        (hasMore || isLoadingMore) && (
          <div className="flex justify-center pt-4">
            <Button variant="outline" size="sm" onClick={onLoadMore} disabled={isLoadingMore}>
              {isLoadingMore ? "Loading…" : "Load older"}
            </Button>
          </div>
        )
      )}
    </div>
  );
}
