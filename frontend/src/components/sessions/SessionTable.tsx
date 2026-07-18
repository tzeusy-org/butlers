import type { ReactNode } from "react";

import { Time } from "@/components/ui/time";

import type { SessionSummary } from "@/api/types";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { ComplexityBadge } from "@/components/general/ComplexityBadge";
import { StatusBadge } from "@/components/sessions/StatusBadge";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { usePrefetchOnIntent } from "@/hooks/use-prefetch-on-intent";
import { formatCostUsd } from "@/lib/format-cost";
import { formatDurationMs } from "@/lib/format-duration";
import { truncate } from "@/lib/truncate";
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
  /**
   * Id of the currently-selected row (mirrors ?selected= in the URL,
   * bu-qvnce.5 pursuit move 5 slice 4). Highlights the row and marks it
   * aria-selected; also carries data-session-id for the page's j/k roving
   * focus to sync native DOM focus onto.
   */
  selectedId?: string | null;
  /**
   * Butler pools dropped from the list fan-out that produced these rows
   * (KeysetMeta.sources_degraded). A non-empty list means the page is a
   * PARTIAL view: rows are missing. Rendered as a named note above the table,
   * and — critically — it GATES the calm "No sessions found" empty state, so a
   * degraded zero never reads as a truthful absence (bu-hmdqz.12, fleet-wide
   * degraded-envelope convention). Defaults to empty for callers on the
   * butler-scoped `PaginatedResponse` (no per-pool fan-out).
   */
  sourcesDegraded?: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

interface SessionTableRowProps {
  session: SessionSummary;
  interactive: boolean;
  selected: boolean;
  onSessionClick?: (session: SessionSummary) => void;
  children: ReactNode;
}

/**
 * A table row that warms the same global-detail cache slot the drawer reads.
 * The intent timer makes a deliberate hover or keyboard focus feel instant
 * without turning a pointer sweep across the session ledger into a fetch fan-out.
 */
function SessionTableRow({
  session,
  interactive,
  selected,
  onSessionClick,
  children,
}: SessionTableRowProps) {
  const prefetch = usePrefetchOnIntent(
    interactive ? `/sessions/${encodeURIComponent(session.id)}` : null,
  );

  return (
    <TableRow
      data-testid="session-row"
      data-session-id={session.id}
      aria-selected={interactive ? selected : undefined}
      className={cn(
        session.success === false && "bg-destructive/5",
        selected && "bg-muted",
        interactive &&
          "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      onClick={() => onSessionClick?.(session)}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={
        interactive
          ? `Open session detail for ${session.butler ?? "session"}: ${truncate(session.prompt, 80)}`
          : undefined
      }
      onKeyDown={
        interactive
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSessionClick?.(session);
              }
            }
          : undefined
      }
      onPointerEnter={interactive ? prefetch.onPointerEnter : undefined}
      onPointerLeave={interactive ? prefetch.onPointerLeave : undefined}
      onFocus={interactive ? prefetch.onFocus : undefined}
      onBlur={interactive ? prefetch.onBlur : undefined}
    >
      {children}
    </TableRow>
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
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-12" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell className="text-right"><Skeleton className="h-4 w-16 ml-auto" /></TableCell>
          <TableCell className="text-right"><Skeleton className="h-4 w-12 ml-auto" /></TableCell>
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
      variant="page"
      title="No sessions found."
      description="Sessions appear as butlers process triggers and scheduled tasks."
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
  selectedId = null,
  sourcesDegraded = [],
}: SessionTableProps) {
  const degradedNote =
    sourcesDegraded.length > 0 ? (
      <SourceDegradedNote
        label="Session list"
        detail={`partial: ${sourcesDegraded.join(", ")} unreachable`}
        className="mb-3"
        testId="sessions-list-degraded"
      />
    ) : null;

  if (!isLoading && sessions.length === 0) {
    // A degraded zero must not render calm absence: name the dropped pool(s)
    // instead of the "No sessions found" all-clear (bu-hmdqz.12).
    return degradedNote ?? <EmptyState />;
  }

  return (
    <>
      {degradedNote}
      <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          {showButlerColumn && <TableHead>Butler</TableHead>}
          <TableHead>Trigger</TableHead>
          <TableHead>Request ID</TableHead>
          <TableHead>Prompt</TableHead>
          <TableHead>Model</TableHead>
          <TableHead>Complexity</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <SkeletonRows showButlerColumn={showButlerColumn} />
        ) : (
          sessions.map((session) => {
            const interactive = Boolean(onSessionClick);
            const selected = selectedId != null && session.id === selectedId;
            return (
              <SessionTableRow
                key={session.id}
                session={session}
                interactive={interactive}
                selected={selected}
                onSessionClick={onSessionClick}
              >
                <TableCell className="text-muted-foreground text-xs">
                  <Time value={session.started_at} mode="smart" />
                </TableCell>
                {showButlerColumn && (
                  <TableCell>
                    {session.butler ? (
                      <span className="inline-flex items-center gap-2 text-foreground">
                        <ButlerMark name={session.butler} tone="neutral" />
                        {session.butler}
                      </span>
                    ) : (
                      "\u2014"
                    )}
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
                <TableCell
                  className="font-mono text-xs text-muted-foreground max-w-[120px] truncate"
                  title={session.model ?? undefined}
                >
                  {session.model ?? "\u2014"}
                </TableCell>
                <TableCell>
                  {session.complexity ? (
                    <ComplexityBadge tier={session.complexity} />
                  ) : (
                    <span className="text-xs text-muted-foreground">&mdash;</span>
                  )}
                </TableCell>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {formatDurationMs(session.duration_ms)}
                </TableCell>
                <TableCell><StatusBadge success={session.success} /></TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {session.input_tokens != null || session.output_tokens != null
                    ? `${formatTokens(session.input_tokens)} / ${formatTokens(session.output_tokens)}`
                    : "\u2014"}
                </TableCell>
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  {session.cost_usd != null ? formatCostUsd(session.cost_usd) : "\u2014"}
                </TableCell>
              </SessionTableRow>
            );
          })
        )}
      </TableBody>
    </Table>
    </>
  );
}
