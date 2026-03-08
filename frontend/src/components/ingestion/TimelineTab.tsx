/**
 * Timeline tab content for the /ingestion page.
 *
 * Shows a table of recent ingestion events (request_id lineage).
 * Expanding an event row reveals the full session lineage:
 * - Ordered list of butler sessions (started_at ASC)
 * - Per-butler breakdown (cost, tokens, success)
 * - Rollup totals (total cost, total tokens, by_butler)
 *
 * Data is fetched from:
 * - GET /api/switchboard/ingestion/events          (event list)
 * - GET /api/switchboard/ingestion/events/{id}/sessions  (on expand)
 * - GET /api/switchboard/ingestion/events/{id}/rollup    (on expand)
 */

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useIngestionEvents,
  useIngestionEventLineage,
} from "@/hooks/use-ingestion-events";
import type { IngestionEventSummary } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Truncate a UUID-style string to first 8 chars for display. */
function truncateId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) + "…" : id;
}

/** Format an ISO datetime string as a short human-readable date+time. */
function formatDatetime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Format duration between two ISO timestamps (ms → human-readable). */
function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt || !completedAt) return "—";
  try {
    const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime();
    if (ms < 0) return "—";
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60_000).toFixed(1)}m`;
  } catch {
    return "—";
  }
}

/** Format a cost value in USD. */
function formatCost(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.001) return `<$0.001`;
  return `$${usd.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// LineageView — shows sessions and rollup for one expanded event
// ---------------------------------------------------------------------------

interface LineageViewProps {
  requestId: string;
}

function LineageView({ requestId }: LineageViewProps) {
  const { sessions, rollup } = useIngestionEventLineage(requestId, {
    enabled: true,
  });

  const isLoading = sessions.isLoading || rollup.isLoading;
  const sessionList = sessions.data?.data ?? [];
  const rollupData = rollup.data?.data;

  if (isLoading) {
    return (
      <div className="space-y-2 px-4 pb-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (sessions.isError || rollup.isError) {
    return (
      <p className="px-4 pb-4 text-sm text-destructive">
        Failed to load session lineage details. Please try again.
      </p>
    );
  }

  if (sessionList.length === 0) {
    return (
      <p className="px-4 pb-4 text-sm text-muted-foreground">
        No downstream sessions found for this event.
      </p>
    );
  }

  return (
    <div className="space-y-4 px-4 pb-4">
      {/* Session list */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Butler</TableHead>
              <TableHead>Started At</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>In Tokens</TableHead>
              <TableHead>Out Tokens</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Trace</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessionList.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-medium">{s.butler_name}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatDatetime(s.started_at)}
                </TableCell>
                <TableCell className="text-sm">
                  {formatDuration(s.started_at, s.completed_at)}
                </TableCell>
                <TableCell className="text-sm tabular-nums">
                  {s.input_tokens ?? "—"}
                </TableCell>
                <TableCell className="text-sm tabular-nums">
                  {s.output_tokens ?? "—"}
                </TableCell>
                <TableCell>
                  {s.success === true ? (
                    <Badge variant="default">ok</Badge>
                  ) : s.success === false ? (
                    <Badge variant="destructive">fail</Badge>
                  ) : (
                    <Badge variant="outline">unknown</Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm">
                  {s.trace_id ? (
                    <a
                      href={`/traces/${s.trace_id}`}
                      className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                      target="_blank"
                      rel="noopener noreferrer"
                      title={s.trace_id}
                    >
                      {truncateId(s.trace_id)}
                    </a>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Rollup summary */}
      {rollupData && (
        <div className="rounded-md border bg-muted/30 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Rollup
          </p>
          <div className="flex flex-wrap gap-4 text-sm">
            <span>
              <span className="text-muted-foreground">Sessions: </span>
              <span className="font-medium">{rollupData.total_sessions}</span>
            </span>
            <span>
              <span className="text-muted-foreground">Input tokens: </span>
              <span className="font-medium tabular-nums">
                {rollupData.total_input_tokens.toLocaleString()}
              </span>
            </span>
            <span>
              <span className="text-muted-foreground">Output tokens: </span>
              <span className="font-medium tabular-nums">
                {rollupData.total_output_tokens.toLocaleString()}
              </span>
            </span>
            <span>
              <span className="text-muted-foreground">Total cost: </span>
              <span className="font-medium">{formatCost(rollupData.total_cost)}</span>
            </span>
          </div>
          {Object.keys(rollupData.by_butler).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-3">
              {Object.entries(rollupData.by_butler).map(([butler, entry]) => (
                <span key={butler} className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">{butler}</span>
                  {": "}
                  {entry.sessions} sess / {entry.input_tokens + entry.output_tokens} tok /{" "}
                  {formatCost(entry.cost)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EventRow — one row in the event list that can be expanded
// ---------------------------------------------------------------------------

interface EventRowProps {
  event: IngestionEventSummary;
  isExpanded: boolean;
  onToggle: () => void;
}

function EventRow({ event, isExpanded, onToggle }: EventRowProps) {
  return (
    <>
      <TableRow
        className="cursor-pointer hover:bg-muted/50"
        onClick={onToggle}
        aria-expanded={isExpanded}
      >
        <TableCell className="font-mono text-xs" title={event.id}>
          {truncateId(event.id)}
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">
          {formatDatetime(event.received_at)}
        </TableCell>
        <TableCell className="text-sm">
          {event.source_channel ?? "—"}
        </TableCell>
        <TableCell className="max-w-[180px] truncate text-sm" title={event.source_sender_identity ?? undefined}>
          {event.source_sender_identity ?? "—"}
        </TableCell>
        <TableCell className="text-sm">
          {event.policy_tier ?? event.ingestion_tier ?? "—"}
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">
          <span
            className="text-xs select-none"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            {isExpanded ? "▲" : "▼"}
          </span>
        </TableCell>
      </TableRow>

      {isExpanded && (
        <TableRow>
          <TableCell colSpan={6} className="p-0">
            <LineageView requestId={event.id} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows for loading state
// ---------------------------------------------------------------------------

function EventRowSkeleton() {
  return (
    <TableRow>
      {Array.from({ length: 6 }).map((_, i) => (
        <TableCell key={i}>
          <Skeleton className="h-4 w-full" />
        </TableCell>
      ))}
    </TableRow>
  );
}

// ---------------------------------------------------------------------------
// TimelineTab
// ---------------------------------------------------------------------------

interface TimelineTabProps {
  isActive: boolean;
}

export function TimelineTab({ isActive }: TimelineTabProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: eventsResp, isLoading, isError } = useIngestionEvents(
    {},
    { enabled: isActive },
  );

  const events = eventsResp?.data ?? [];

  function handleToggle(id: string) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Ingestion Events</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isError ? (
            <p className="px-6 py-4 text-sm text-destructive">
              Failed to load ingestion events.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Request ID</TableHead>
                  <TableHead>Received At</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead>Sender</TableHead>
                  <TableHead>Tier</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <EventRowSkeleton key={i} />
                  ))
                ) : events.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6}>
                      <EmptyState
                        title="No ingestion events"
                        description="Events will appear here once the system receives incoming messages."
                      />
                    </TableCell>
                  </TableRow>
                ) : (
                  events.map((event) => (
                    <EventRow
                      key={event.id}
                      event={event}
                      isExpanded={expandedId === event.id}
                      onToggle={() => handleToggle(event.id)}
                    />
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
