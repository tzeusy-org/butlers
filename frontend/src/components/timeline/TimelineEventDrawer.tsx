/**
 * TimelineEventDrawer — typed peek drawer for one fleet-chronicle row
 * (/timeline, bu-86c4c.10).
 *
 * Backed by the `?event=<id>` URL parameter (see useEventDrawerState, reused
 * from the ingestion dispatch ledger — see
 * @/components/ingestion/timeline/useEventDrawerState — it is a small,
 * fully generic useSearchParams wrapper with no ingestion-specific content).
 *
 * No raw JSON dumps: each event type gets its own typed summary with a hard
 * link to its root evidence (session transcript for session/error events;
 * the Notifications record for notification events) per the JARVIS audit's
 * "typed peek drawer for every row" move.
 *
 * Design: hairline-divided, no card chrome — matches the Dispatch visual
 * language used by the ingestion ledger this surface is rebuilt on.
 */

import { Link } from "react-router";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import type { TimelineEvent } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Build the cross-butler session detail href, scoping by butler when known. */
function sessionDetailHref(event: TimelineEvent): string {
  const base = `/sessions/${encodeURIComponent(event.id)}`;
  return event.butler ? `${base}?butler=${encodeURIComponent(event.butler)}` : base;
}

// ---------------------------------------------------------------------------
// Per-type body
// ---------------------------------------------------------------------------

function SessionDrawerBody({ event }: { event: TimelineEvent }) {
  const duration = event.data.duration_ms;
  const success = asBoolean(event.data.success);
  const triggerSource = asString(event.data.trigger_source);
  const completedAt = asString(event.data.completed_at);

  return (
    <div className="p-4 space-y-3">
      <p className="font-serif text-[15px] leading-[1.55]">{event.summary}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 font-mono text-[11px]">
        <dt className="text-muted-foreground">status</dt>
        <dd className={success === false ? "text-destructive" : undefined}>
          {success === false ? "failed" : success === true ? "succeeded" : "—"}
        </dd>
        <dt className="text-muted-foreground">trigger</dt>
        <dd>{triggerSource ?? "—"}</dd>
        <dt className="text-muted-foreground">duration</dt>
        <dd>{formatDuration(typeof duration === "number" ? duration : undefined)}</dd>
        <dt className="text-muted-foreground">completed</dt>
        <dd>
          {completedAt ? (
            <Time value={completedAt} mode="absolute" precision="second" />
          ) : (
            "—"
          )}
        </dd>
      </dl>
      <Button asChild variant="outline" size="sm">
        <Link to={sessionDetailHref(event)} data-testid="drawer-session-link">
          View session transcript
        </Link>
      </Button>
    </div>
  );
}

function NotificationDrawerBody({ event }: { event: TimelineEvent }) {
  const channel = asString(event.data.channel);
  const recipient = asString(event.data.recipient);
  const status = asString(event.data.status);

  return (
    <div className="p-4 space-y-3">
      <p className="font-serif text-[15px] leading-[1.55]">{event.summary}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 font-mono text-[11px]">
        <dt className="text-muted-foreground">channel</dt>
        <dd>{channel ?? "—"}</dd>
        <dt className="text-muted-foreground">recipient</dt>
        <dd>{recipient ?? "—"}</dd>
        <dt className="text-muted-foreground">delivery</dt>
        <dd className={status === "failed" ? "text-destructive" : undefined}>{status ?? "—"}</dd>
      </dl>
      <Button asChild variant="outline" size="sm">
        <Link to="/notifications" data-testid="drawer-notification-link">
          View in Notifications
        </Link>
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TimelineEventDrawer
// ---------------------------------------------------------------------------

export interface TimelineEventDrawerProps {
  event: TimelineEvent;
  onClose: () => void;
}

export function TimelineEventDrawer({ event, onClose }: TimelineEventDrawerProps) {
  return (
    <div
      className="border-t border-border bg-background"
      data-testid="timeline-event-drawer"
      role="complementary"
      aria-label="Event detail drawer"
    >
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <span className="font-mono text-[11px] text-muted-foreground">{event.butler}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{event.type}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          <Time value={event.timestamp} mode="absolute" precision="second" />
        </span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded p-1 hover:bg-muted transition-colors"
          aria-label="Close drawer"
          data-testid="drawer-close-button"
        >
          <X className="size-4" />
        </button>
      </div>

      {event.type === "notification" ? (
        <NotificationDrawerBody event={event} />
      ) : (
        <SessionDrawerBody event={event} />
      )}
    </div>
  );
}
