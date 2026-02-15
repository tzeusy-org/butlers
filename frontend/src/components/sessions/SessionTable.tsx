import { formatDistanceToNow, format } from "date-fns";

import type { SessionSummary } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
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

export interface SessionTableProps {
  sessions: SessionSummary[];
  isLoading: boolean;
  onSessionClick?: (session: SessionSummary) => void;
  /** Show the butler column — true for cross-butler views, false for single-butler. */
  showButlerColumn?: boolean;
  /** Optional callback when a request_id is clicked to auto-fill the filter. */
  onRequestIdClick?: (requestId: string) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format an ISO timestamp as relative ("2h ago") or absolute ("Feb 10, 2:30 PM"). */
function formatTimestamp(iso: string): { relative: string; absolute: string } {
  const date = new Date(iso);
  const relative = formatDistanceToNow(date, { addSuffix: true });
  const absolute = format(date, "MMM d, h:mm a");
  return { relative, absolute };
}

/** Format duration_ms to a human-friendly string (e.g. "1.2s", "45s", "2m 15s"). */
function formatDuration(ms: number | null): string {
  if (ms == null) return "\u2014";
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const frac = ms / 1000;
  if (totalSeconds < 60) {
    return frac % 1 === 0 ? `${totalSeconds}s` : `${frac.toFixed(1)}s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/** Truncate text to a maximum length, appending an ellipsis if needed. */
function truncate(text: string, max = 60): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "\u2026";
}

/** Truncate a UUID to show only the first 8 characters. */
function truncateUuid(uuid: string): string {
  return uuid.slice(0, 8);
}

/** Format token counts to a compact string (e.g. "1.2K", "3.5M"). */
function formatTokens(n: number | null): string {
  if (n == null) return "\u2014";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Map success field to a styled status badge. */
function statusBadge(success: boolean | null) {
  if (success === true) {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
        Success
      </Badge>
    );
  }
  if (success === false) {
    return <Badge variant="destructive">Failed</Badge>;
  }
  return (
    <Badge variant="outline" className="border-gray-400 text-gray-500">
      Running
    </Badge>
  );
}

/** Deterministic color for butler badges. */
const BUTLER_COLORS = [
  "bg-blue-600",
  "bg-violet-600",
  "bg-amber-600",
  "bg-teal-600",
  "bg-rose-600",
  "bg-indigo-600",
  "bg-cyan-600",
  "bg-orange-600",
];

function butlerBadge(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  const color = BUTLER_COLORS[Math.abs(hash) % BUTLER_COLORS.length];
  return (
    <Badge className={cn(color, "text-white hover:opacity-90")}>
      {name}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows
// ---------------------------------------------------------------------------

function SkeletonRows({
  count = 5,
  showButlerColumn,
}: {
  count?: number;
  showButlerColumn?: boolean;
}) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          {showButlerColumn && (
            <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          )}
          <TableCell><Skeleton className="h-4 w-14" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-12" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell className="text-right"><Skeleton className="h-4 w-16 ml-auto" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <EmptyStateUI
      title="No sessions found"
      description="Sessions will appear here as butlers process triggers and scheduled tasks."
    />
  );
}

// ---------------------------------------------------------------------------
// SessionTable
// ---------------------------------------------------------------------------

export function SessionTable({
  sessions,
  isLoading,
  onSessionClick,
  showButlerColumn = false,
  onRequestIdClick,
}: SessionTableProps) {
  if (!isLoading && sessions.length === 0) {
    return <EmptyState />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          {showButlerColumn && <TableHead>Butler</TableHead>}
          <TableHead>Trigger</TableHead>
          <TableHead>Request ID</TableHead>
          <TableHead>Prompt</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <SkeletonRows showButlerColumn={showButlerColumn} />
        ) : (
          sessions.map((session) => {
            const ts = formatTimestamp(session.started_at);
            return (
              <TableRow
                key={session.id}
                className={cn(
                  session.success === false && "bg-destructive/5",
                  onSessionClick && "cursor-pointer",
                )}
                onClick={() => onSessionClick?.(session)}
              >
                <TableCell
                  className="text-muted-foreground text-xs"
                  title={ts.absolute}
                >
                  {ts.relative}
                </TableCell>
                {showButlerColumn && (
                  <TableCell>
                    {session.butler ? butlerBadge(session.butler) : "\u2014"}
                  </TableCell>
                )}
                <TableCell className="text-xs text-muted-foreground">
                  {session.trigger_source}
                </TableCell>
                <TableCell
                  className="font-mono text-xs text-muted-foreground"
                  title={session.request_id ?? undefined}
                >
                  {session.request_id ? (
                    <button
                      type="button"
                      className="hover:text-foreground transition-colors underline decoration-dotted"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRequestIdClick?.(session.request_id!);
                      }}
                    >
                      {truncateUuid(session.request_id)}
                    </button>
                  ) : (
                    "\u2014"
                  )}
                </TableCell>
                <TableCell
                  className="text-muted-foreground max-w-xs"
                  title={session.prompt}
                >
                  {truncate(session.prompt)}
                </TableCell>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {formatDuration(session.duration_ms)}
                </TableCell>
                <TableCell>{statusBadge(session.success)}</TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {session.input_tokens != null || session.output_tokens != null
                    ? `${formatTokens(session.input_tokens)} / ${formatTokens(session.output_tokens)}`
                    : "\u2014"}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
