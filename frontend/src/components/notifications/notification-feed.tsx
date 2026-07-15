import { Link } from "react-router";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";

import type { NotificationSummary } from "@/api/types";
import { NotificationTableSkeleton } from "@/components/skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ExpandableDetail } from "@/components/ui/expandable-detail";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NotificationFeedProps {
  notifications: NotificationSummary[];
  isLoading?: boolean;
  /** When true, the empty state shows a hint that active filters may be hiding results. */
  hasActiveFilters?: boolean;
  /** Called when the user clicks "Mark read" on any unread notification row. */
  onMarkRead?: (notificationId: string) => void;
  /**
   * Called when the user clicks "Dismiss" on a notification row. Dismiss is
   * semantically identical to mark-read on the backend (both set
   * ``status = 'read'`` via PATCH /{id}/read); this prop exists so the page can
   * route it through the same mutation while keeping the affordance distinct.
   */
  onDismiss?: (notificationId: string) => void;
  /** Set of notification IDs currently being acknowledged (shows loading state). */
  pendingAckIds?: Set<string>;
  /**
   * Id of the row currently selected by j/k list-triage (bu-qvnce.11 slice
   * 4, `useListTriage` on NotificationsPage). Highlights that row and gives
   * it the `notification-row` testid + `data-notification-id` so
   * NotificationsPage can sync DOM focus to it.
   */
  selectedId?: string | null;
  /**
   * True when the notifications list response carried `source_available:
   * false` — the Switchboard notifications source was unreachable, so an empty
   * page is NOT a truthful "no notifications" result. When set, an empty feed
   * renders a named degraded note instead of the calm empty state (CLAUDE.md
   * degraded-envelope convention; bu-jad4j.2).
   */
  sourceUnavailable?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map notification status to badge variant + label. */
function statusBadge(status: string) {
  switch (status) {
    case "sent":
      return (
        <Badge className="bg-[var(--green)] text-white hover:bg-[var(--green)]/90">
          Sent
        </Badge>
      );
    case "failed":
      return <Badge variant="destructive">Failed</Badge>;
    case "retried":
      return (
        <Badge variant="outline" className="border-[var(--amber)] text-[var(--amber-text)]">
          Retried
        </Badge>
      );
    case "pending":
      return (
        <Badge variant="outline" className="border-[var(--amber)] text-[var(--amber-text)]">
          Pending
        </Badge>
      );
    default:
      return <Badge variant="secondary">{status}</Badge>;
  }
}

/** Map channel name to a styled badge. */
function channelBadge(channel: string) {
  return (
    <Badge variant="secondary" className="capitalize">
      {channel}
    </Badge>
  );
}

// Collapsed-preview clamp lengths for the message and error lines. A cell is
// "expandable" (offers the detail toggle) only when its content exceeds these.
const MESSAGE_CLAMP = 60;
const ERROR_CLAMP = 80;

/** Truncate a message to a maximum character length. */
function truncate(text: string, max = MESSAGE_CLAMP): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "\u2026";
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}...` : id;
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ hasActiveFilters = false }: { hasActiveFilters?: boolean }) {
  const description = hasActiveFilters
    ? "No notifications match the active filters."
    : "Notifications appear as butlers send messages via Telegram, email, and other channels.";

  return (
    <EmptyStateUI
      variant="page"
      title="No notifications found."
      description={description}
    />
  );
}

// ---------------------------------------------------------------------------
// NotificationFeed
// ---------------------------------------------------------------------------

export function NotificationFeed({
  notifications,
  isLoading = false,
  hasActiveFilters = false,
  onMarkRead,
  onDismiss,
  pendingAckIds,
  selectedId = null,
  sourceUnavailable = false,
}: NotificationFeedProps) {
  // Triage controls render whenever a triage handler is wired.
  const hasTriageControls = Boolean(onMarkRead || onDismiss);
  if (isLoading) {
    return <NotificationTableSkeleton />;
  }

  if (notifications.length === 0) {
    // Degraded before empty: a source_available=false page is empty because the
    // Switchboard notifications source was unreachable, not because the stream
    // is genuinely clear. Name the source instead of the calm empty state
    // (bu-jad4j.2). A reachable-but-empty source keeps the honest EmptyState.
    if (sourceUnavailable) {
      return (
        <div className="py-8">
          <SourceDegradedNote
            label="Notifications"
            detail="Switchboard source unreachable. This list may be incomplete"
            testId="notification-feed-source-unavailable"
          />
        </div>
      );
    }
    return <EmptyState hasActiveFilters={hasActiveFilters} />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Butler</TableHead>
          <TableHead>Recipient</TableHead>
          <TableHead>Channel</TableHead>
          <TableHead>Message</TableHead>
          <TableHead className="text-right">Time</TableHead>
          {hasTriageControls && <TableHead />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {notifications.map((n) => {
          const displayStatus = n.effective_status ?? n.status;
          const isPending = pendingAckIds?.has(n.id) ?? false;
          const showError = displayStatus === "failed" && Boolean(n.error);
          // The message/error is clipped in the collapsed preview and exposed in
          // full only via mouse-hover title= today; offer the keyboard-reachable
          // detail toggle when either line actually overflows its clamp (bu-x7z84).
          const messageClipped = n.message.length > MESSAGE_CLAMP;
          const errorClipped = showError && (n.error?.length ?? 0) > ERROR_CLAMP;
          const expandable = messageClipped || errorClipped;
          return (
          <TableRow
            key={n.id}
            data-testid="notification-row"
            data-notification-id={n.id}
            tabIndex={-1}
            className={cn(
              displayStatus === "failed" && "bg-destructive/5",
              selectedId === n.id && "bg-muted/60",
            )}
          >
            <TableCell>{statusBadge(displayStatus)}</TableCell>
            <TableCell className="font-medium">{n.source_butler}</TableCell>
            <TableCell className="text-muted-foreground">
              {n.recipient ? n.recipient : "—"}
            </TableCell>
            <TableCell>{channelBadge(n.channel)}</TableCell>
            <TableCell className="max-w-xs">
              <ExpandableDetail
                label="message"
                expandable={expandable}
                testId="notification-detail-toggle"
                preview={
                  <>
                    <p className="truncate text-muted-foreground" title={n.message}>
                      {truncate(n.message)}
                    </p>
                    {showError && n.error && (
                      <p className="mt-1 truncate text-xs text-destructive" title={n.error}>
                        {truncate(n.error, ERROR_CLAMP)}
                      </p>
                    )}
                  </>
                }
              >
                <p
                  className="whitespace-pre-wrap break-words text-sm text-muted-foreground"
                  data-testid="notification-detail-message"
                >
                  {n.message}
                </p>
                {showError && n.error && (
                  <p
                    className="mt-1 whitespace-pre-wrap break-words text-xs text-destructive"
                    data-testid="notification-detail-error"
                  >
                    {n.error}
                  </p>
                )}
              </ExpandableDetail>
              {(n.session_id || n.trace_id) && (
                <div className="mt-1 flex items-center gap-3 text-xs">
                  {n.session_id && (
                    <Link
                      className="text-primary underline underline-offset-2 hover:text-primary/80"
                      to={`/sessions/${encodeURIComponent(n.session_id)}?butler=${encodeURIComponent(n.source_butler)}`}
                    >
                      Session {shortId(n.session_id)}
                    </Link>
                  )}
                  {n.trace_id && (
                    <Link
                      className="text-primary underline underline-offset-2 hover:text-primary/80"
                      to={`/ingestion?trace=${encodeURIComponent(n.trace_id)}`}
                    >
                      Trace {shortId(n.trace_id)}
                    </Link>
                  )}
                </div>
              )}
            </TableCell>
            <TableCell className="text-muted-foreground text-right text-xs">
              <Time value={n.created_at} mode="relative" />
            </TableCell>
            {hasTriageControls && (
              <TableCell className="text-right">
                {/* Triage controls apply to any actionable (unread) row — both
                    sent and failed — not just failures. An already-read row has
                    nothing left to triage, so it shows no control. */}
                {displayStatus !== "read" && (
                  <div className="flex items-center justify-end gap-2">
                    {onMarkRead && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={isPending}
                        onClick={() => onMarkRead(n.id)}
                        className="text-xs"
                      >
                        {isPending ? "Acknowledging…" : "Mark read"}
                      </Button>
                    )}
                    {onDismiss && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={isPending}
                        onClick={() => onDismiss(n.id)}
                        className="text-muted-foreground text-xs"
                      >
                        Dismiss
                      </Button>
                    )}
                  </div>
                )}
              </TableCell>
            )}
          </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
