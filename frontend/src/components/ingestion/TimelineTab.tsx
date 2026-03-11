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
 * - GET /api/ingestion/events          (event list, supports status filter)
 * - GET /api/ingestion/events/{id}/sessions  (on expand)
 * - GET /api/ingestion/events/{id}/rollup    (on expand)
 * - POST /api/ingestion/events/{id}/replay   (Replay/Retry action)
 */

import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  useIngestionEventRollup,
} from "@/hooks/use-ingestion-events";
import type {
  IngestionEventSummary,
  IngestionEventSession,
  IngestionEventStatus,
} from "@/api/index.ts";
import { replayIngestionEvent } from "@/api/index.ts";
import { StatusBadge } from "./StatusBadge";

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

/** Format a number with comma separators (e.g. 1,234,567). */
function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

/** Returns true if this status is replayable (Replay button shown). */
function isReplayable(status: IngestionEventStatus): boolean {
  return status === "filtered" || status === "error" || status === "replay_failed";
}

/** Returns true if this status is pending replay (spinner shown). */
function isReplayPending(status: IngestionEventStatus): boolean {
  return status === "replay_pending";
}

/** Returns true if this row can be expanded (filtered events cannot). */
function isExpandable(status: IngestionEventStatus): boolean {
  return status !== "filtered";
}

// ---------------------------------------------------------------------------
// Session flamegraph
// ---------------------------------------------------------------------------

/** Distinct hues for butler names — assigned in encounter order. */
const BUTLER_COLORS = [
  "bg-blue-500",
  "bg-amber-500",
  "bg-emerald-500",
  "bg-violet-500",
  "bg-rose-500",
  "bg-cyan-500",
  "bg-orange-500",
  "bg-teal-500",
];

function butlerColorMap(sessions: IngestionEventSession[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const s of sessions) {
    if (!map.has(s.butler_name)) {
      map.set(s.butler_name, BUTLER_COLORS[map.size % BUTLER_COLORS.length]);
    }
  }
  return map;
}

function SessionFlamegraph({ sessions }: { sessions: IngestionEventSession[] }) {
  const withTimes = sessions.filter((s) => s.started_at);
  if (withTimes.length === 0) return null;

  const starts = withTimes.map((s) => new Date(s.started_at!).getTime());
  const ends = withTimes.map((s) =>
    s.completed_at ? new Date(s.completed_at).getTime() : Date.now(),
  );
  const minTime = Math.min(...starts);
  const maxTime = Math.max(...ends);
  const span = maxTime - minTime || 1;

  const colors = butlerColorMap(sessions);

  // Group sessions into swim lanes by butler
  const butlers = [...colors.keys()];

  return (
    <div className="space-y-1.5">
      {/* Legend */}
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {butlers.map((b) => (
          <span key={b} className="flex items-center gap-1">
            <span className={`inline-block size-2.5 rounded-sm ${colors.get(b)}`} />
            {b}
          </span>
        ))}
      </div>

      {/* Lanes */}
      <div className="relative rounded-md border bg-muted/20 overflow-hidden">
        {butlers.map((butler) => {
          const laneSessions = withTimes.filter((s) => s.butler_name === butler);
          return (
            <div
              key={butler}
              className="relative h-7 border-b last:border-0"
            >
              {laneSessions.map((s) => {
                const sStart = new Date(s.started_at!).getTime();
                const sEnd = s.completed_at
                  ? new Date(s.completed_at).getTime()
                  : Date.now();
                const left = ((sStart - minTime) / span) * 100;
                const width = Math.max(((sEnd - sStart) / span) * 100, 1);
                const dur = formatDuration(s.started_at, s.completed_at ?? new Date().toISOString());
                const color = colors.get(s.butler_name) ?? BUTLER_COLORS[0];

                return (
                  <Link
                    key={s.id}
                    to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
                    title={`${s.butler_name} — ${dur}${s.model ? ` (${s.model})` : ""}`}
                    className={`absolute top-0.5 bottom-0.5 rounded-sm ${color} opacity-80 hover:opacity-100 transition-opacity cursor-pointer`}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  >
                    <span className="px-1 text-[10px] font-medium text-white truncate block leading-6">
                      {dur}
                    </span>
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
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
    <div className="space-y-4 px-4 pt-3 pb-4">
      {/* Flamegraph */}
      <SessionFlamegraph sessions={sessionList} />

      {/* Session list */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Butler</TableHead>
              <TableHead>Session</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Started At</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>In Tokens</TableHead>
              <TableHead>Out Tokens</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessionList.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-medium">{s.butler_name}</TableCell>
                <TableCell className="text-sm">
                  <Link
                    to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
                    className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                    title={s.id}
                  >
                    {truncateId(s.id)}
                  </Link>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {s.model ?? "—"}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatDatetime(s.started_at)}
                </TableCell>
                <TableCell className="text-sm">
                  {formatDuration(s.started_at, s.completed_at)}
                </TableCell>
                <TableCell className="text-sm tabular-nums">
                  {fmtNum(s.input_tokens)}
                </TableCell>
                <TableCell className="text-sm tabular-nums">
                  {fmtNum(s.output_tokens)}
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
// ActionCell — Replay/Retry button or spinner based on event status
// ---------------------------------------------------------------------------

interface ActionCellProps {
  event: IngestionEventSummary;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
}

function ActionCell({ event, onOptimisticUpdate }: ActionCellProps) {
  const [isPending, setIsPending] = useState(false);

  if (isReplayPending(event.status)) {
    return (
      <span
        className="flex items-center gap-1 text-xs text-muted-foreground"
        data-testid="replay-pending-spinner"
      >
        <Loader2 className="size-3 animate-spin" />
        pending
      </span>
    );
  }

  if (!isReplayable(event.status)) {
    return null;
  }

  const label = event.status === "replay_failed" ? "Retry" : "Replay";

  async function handleReplay(e: React.MouseEvent) {
    e.stopPropagation(); // Don't trigger row expand
    setIsPending(true);
    try {
      await replayIngestionEvent(event.id);
      onOptimisticUpdate(event.id, "replay_pending");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Replay request failed";
      toast.error(message);
    } finally {
      setIsPending(false);
    }
  }

  return (
    <Button
      variant="outline"
      size="xs"
      disabled={isPending}
      onClick={handleReplay}
      data-testid="replay-button"
    >
      {isPending ? <Loader2 className="size-3 animate-spin" /> : null}
      {label}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// EventRow — one row in the event list that can be expanded
// ---------------------------------------------------------------------------

interface EventRowProps {
  event: IngestionEventSummary;
  isExpanded: boolean;
  onToggle: () => void;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
}

function EventRow({ event, isExpanded, onToggle, onOptimisticUpdate }: EventRowProps) {
  const { data: rollupResp } = useIngestionEventRollup(event.id);
  const r = rollupResp?.data;

  const expandable = isExpandable(event.status);

  function handleRowClick() {
    if (expandable) onToggle();
  }

  // Total column count: Request ID, Received At, Channel, Sender, Status, Tier, Tokens In, Tokens Out, Cost, Action, expand-chevron
  const TOTAL_COLS = 11;

  return (
    <>
      <TableRow
        className={expandable ? "cursor-pointer hover:bg-muted/50" : ""}
        onClick={handleRowClick}
        aria-expanded={expandable ? isExpanded : undefined}
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
        <TableCell>
          <StatusBadge status={event.status} filterReason={event.filter_reason} />
        </TableCell>
        <TableCell className="text-sm">
          {event.policy_tier ?? event.ingestion_tier ?? "—"}
        </TableCell>
        <TableCell className="text-sm tabular-nums">
          {r ? fmtNum(r.total_input_tokens) : "—"}
        </TableCell>
        <TableCell className="text-sm tabular-nums">
          {r ? fmtNum(r.total_output_tokens) : "—"}
        </TableCell>
        <TableCell className="text-sm tabular-nums">
          {r ? formatCost(r.total_cost) : "—"}
        </TableCell>
        <TableCell onClick={(e) => e.stopPropagation()}>
          <ActionCell event={event} onOptimisticUpdate={onOptimisticUpdate} />
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">
          {expandable ? (
            <span
              className="text-xs select-none"
              aria-label={isExpanded ? "Collapse" : "Expand"}
            >
              {isExpanded ? "▲" : "▼"}
            </span>
          ) : null}
        </TableCell>
      </TableRow>

      {isExpanded && expandable && (
        <TableRow>
          <TableCell colSpan={TOTAL_COLS} className="p-0">
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
      {Array.from({ length: 11 }).map((_, i) => (
        <TableCell key={i}>
          <Skeleton className="h-4 w-full" />
        </TableCell>
      ))}
    </TableRow>
  );
}

// ---------------------------------------------------------------------------
// Status filter options
// ---------------------------------------------------------------------------

const STATUS_FILTER_OPTIONS: Array<{ value: IngestionEventStatus | "all"; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "ingested", label: "Ingested" },
  { value: "filtered", label: "Filtered" },
  { value: "error", label: "Error" },
  { value: "replay_pending", label: "Replay Pending" },
  { value: "replay_complete", label: "Replay Complete" },
  { value: "replay_failed", label: "Replay Failed" },
];

// ---------------------------------------------------------------------------
// TimelineTab
// ---------------------------------------------------------------------------

interface TimelineTabProps {
  isActive: boolean;
}

export function TimelineTab({ isActive }: TimelineTabProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const expandedId = searchParams.get("expanded");
  const statusFilter = (searchParams.get("status") ?? "all") as IngestionEventStatus | "all";

  // Optimistic overrides: map of event id → overridden status
  const [optimisticOverrides, setOptimisticOverrides] = useState<
    Map<string, IngestionEventStatus>
  >(new Map());

  const filters = statusFilter !== "all" ? { status: statusFilter } : {};
  const { data: eventsResp, isLoading, isError } = useIngestionEvents(
    filters,
    { enabled: isActive },
  );

  const rawEvents = eventsResp?.data ?? [];

  // Apply optimistic overrides so replayed events immediately show replay_pending
  const events: IngestionEventSummary[] = rawEvents.map((e) => {
    const override = optimisticOverrides.get(e.id);
    return override ? { ...e, status: override } : e;
  });

  const handleToggle = useCallback(
    (id: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (next.get("expanded") === id) {
          next.delete("expanded");
        } else {
          next.set("expanded", id);
        }
        return next;
      });
    },
    [setSearchParams],
  );

  const handleStatusFilterChange = useCallback(
    (value: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value === "all") {
          next.delete("status");
        } else {
          next.set("status", value);
        }
        next.delete("expanded"); // Clear expansion when filter changes
        return next;
      });
    },
    [setSearchParams],
  );

  const handleOptimisticUpdate = useCallback(
    (id: string, newStatus: IngestionEventStatus) => {
      setOptimisticOverrides((prev) => {
        const next = new Map(prev);
        next.set(id, newStatus);
        return next;
      });
    },
    [],
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Ingestion Events</CardTitle>
            {/* Status filter */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Status:</span>
              <Select
                value={statusFilter}
                onValueChange={handleStatusFilterChange}
              >
                <SelectTrigger size="sm" className="w-44" data-testid="status-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_FILTER_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-4 pb-4 pt-0">
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
                  <TableHead>Status</TableHead>
                  <TableHead>Tier</TableHead>
                  <TableHead>Tokens In</TableHead>
                  <TableHead>Tokens Out</TableHead>
                  <TableHead>Cost</TableHead>
                  <TableHead>Action</TableHead>
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
                    <TableCell colSpan={11}>
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
                      onOptimisticUpdate={handleOptimisticUpdate}
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
