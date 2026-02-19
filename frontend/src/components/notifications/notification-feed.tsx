import { Link } from "react-router";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { formatDistanceToNow } from "date-fns";

import type { NotificationSummary } from "@/api/types";
import { NotificationTableSkeleton } from "@/components/skeletons";
import { Badge } from "@/components/ui/badge";
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
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map notification status to badge variant + label. */
function statusBadge(status: string) {
  switch (status) {
    case "sent":
      return (
        <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
          Sent
        </Badge>
      );
    case "failed":
      return <Badge variant="destructive">Failed</Badge>;
    case "pending":
      return (
        <Badge variant="outline" className="border-amber-500 text-amber-600">
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

/** Truncate a message to a maximum character length. */
function truncate(text: string, max = 60): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "\u2026";
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}...` : id;
}

/** Format an ISO timestamp as a relative human-readable string. */
function relativeTime(iso: string): string {
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ hasActiveFilters = false }: { hasActiveFilters?: boolean }) {
  const description = hasActiveFilters
    ? "No notifications match the current filters. Try clearing the filters to see all notifications."
    : "Notifications will appear here as butlers send messages via Telegram, email, and other channels.";

  return (
    <EmptyStateUI
      title="No notifications found"
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
}: NotificationFeedProps) {
  if (isLoading) {
    return <NotificationTableSkeleton />;
  }

  if (notifications.length === 0) {
    return <EmptyState hasActiveFilters={hasActiveFilters} />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Butler</TableHead>
          <TableHead>Channel</TableHead>
          <TableHead>Message</TableHead>
          <TableHead className="text-right">Time</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {notifications.map((n) => (
          <TableRow
            key={n.id}
            className={cn(n.status === "failed" && "bg-destructive/5")}
          >
            <TableCell>{statusBadge(n.status)}</TableCell>
            <TableCell className="font-medium">{n.source_butler}</TableCell>
            <TableCell>{channelBadge(n.channel)}</TableCell>
            <TableCell
              className="max-w-xs"
              title={n.message}
            >
              <p className="truncate text-muted-foreground">{truncate(n.message)}</p>
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
                      to={`/traces/${encodeURIComponent(n.trace_id)}`}
                    >
                      Trace {shortId(n.trace_id)}
                    </Link>
                  )}
                </div>
              )}
            </TableCell>
            <TableCell className="text-muted-foreground text-right text-xs">
              {relativeTime(n.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
