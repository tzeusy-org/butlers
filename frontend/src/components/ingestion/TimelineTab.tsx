/**
 * Timeline tab content for the /ingestion page.
 *
 * Shows a table of recent ingestion events (request_id lineage) using
 * cursor-based infinite scroll. Events are grouped by hour with mono-eyebrow
 * headers. Expanding an event row reveals the full session lineage:
 * - Ordered list of butler sessions (started_at ASC)
 * - Per-butler breakdown (cost, tokens, success)
 * - Rollup totals (total cost, total tokens, by_butler)
 *
 * Data is fetched from:
 * - GET /api/ingestion/events          (cursor-paginated unified stream)
 * - GET /api/ingestion/events/{id}/sessions  (on expand)
 * - GET /api/ingestion/events/{id}/rollup    (on expand)
 * - POST /api/ingestion/events/{id}/replay   (Replay/Retry action)
 *
 * BREAKING (bu-1f91v.3): offset+total pagination removed; replaced with
 * cursor-based infinite scroll. The `total` field is no longer available.
 *
 * §2.8 Saved Views: client-side localStorage key `ingestion-saved-views`.
 * §2.9 Connector Attention Strip: highlights connectors with degraded health.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { AlertTriangle, Check, Copy, Loader2, RotateCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { butlerHueVar } from "@/components/ui/ButlerMark";
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
  useIngestionEventSenderContact,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
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
  return status !== "replay_pending";
}

/** Returns true if this status is pending replay (spinner shown). */
function isReplayPending(status: IngestionEventStatus): boolean {
  return status === "replay_pending";
}

/** Returns true if this row can be expanded (filtered and error events cannot). */
function isExpandable(status: IngestionEventStatus): boolean {
  return status !== "filtered" && status !== "error";
}

/**
 * Derive the hour-group label for an event's received_at timestamp.
 *
 * Returns a string like "Sat, May 17 · 14:00" that uniquely identifies
 * the one-hour bucket containing the timestamp.  Used for hour-group headers.
 */
function hourGroupLabel(receivedAt: string | null): string {
  if (!receivedAt) return "Unknown time";
  try {
    const d = new Date(receivedAt);
    // Zero out minutes/seconds/ms so we can compare timestamps for deduplication.
    const hourStart = new Date(d);
    hourStart.setMinutes(0, 0, 0);
    return hourStart.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "Unknown time";
  }
}

/**
 * Return the ISO hour string (YYYY-MM-DDTHH) used as the group key.
 * Events in the same hour share a key and a single group header.
 */
function hourGroupKey(receivedAt: string | null): string {
  if (!receivedAt) return "unknown";
  try {
    return receivedAt.slice(0, 13); // "2026-05-17T14"
  } catch {
    return "unknown";
  }
}

// ---------------------------------------------------------------------------
// CopyButton — transient "copied" label for ~900ms
// ---------------------------------------------------------------------------

function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 900);
    });
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs font-mono text-muted-foreground hover:bg-muted transition-colors"
      title={copied ? "Copied!" : "Copy to clipboard"}
      data-testid="copy-session-id"
    >
      <span className="truncate max-w-[120px]">{label ?? value}</span>
      {copied ? (
        <Check className="size-3 text-emerald-500 shrink-0" data-testid="copy-session-id-copied" />
      ) : (
        <Copy className="size-3 shrink-0" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Session flamegraph
// ---------------------------------------------------------------------------

function SessionFlamegraph({ sessions }: { sessions: IngestionEventSession[] }) {
  const withTimes = sessions.filter((s) => s.started_at);
  if (withTimes.length === 0) return null;

  const starts = withTimes.map((s) => new Date(s.started_at!).getTime());
  const ends = withTimes.map((s) =>
    // eslint-disable-next-line react-hooks/purity
    s.completed_at ? new Date(s.completed_at).getTime() : Date.now(),
  );
  const minTime = Math.min(...starts);
  const maxTime = Math.max(...ends);
  const span = maxTime - minTime || 1;

  // Collect distinct butler names in encounter order (for lane and legend ordering).
  const butlers = [...new Set(sessions.map((s) => s.butler_name))];

  return (
    <div className="space-y-1.5">
      {/* Legend + approximation notice */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {butlers.map((b) => (
          <span key={b} className="flex items-center gap-1">
            <span
              className="inline-block size-2.5 rounded-sm"
              style={{ backgroundColor: butlerHueVar(b) }}
            />
            {b}
          </span>
        ))}
      </div>

      {/* Lanes */}
      <div className="relative rounded-md border bg-muted/20 overflow-hidden">
        {butlers.map((butler) => {
          const laneSessions = withTimes.filter((s) => s.butler_name === butler);
          const laneColor = butlerHueVar(butler);
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

                return (
                  <Link
                    key={s.id}
                    to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
                    title={`${s.butler_name}: ${dur}${s.model ? ` (${s.model})` : ""}\nApproximation: bars are proportional to step duration, not actual token cost.`}
                    className="absolute top-0.5 bottom-0.5 rounded-sm opacity-80 hover:opacity-100 transition-opacity cursor-pointer"
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      backgroundColor: laneColor,
                    }}
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
      {/* Flame strip approximation note */}
      <p className="text-[10px] text-muted-foreground">
        Approximation: bars are proportional to step duration, not actual token cost.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LineageView — shows sessions and rollup for one expanded event
// ---------------------------------------------------------------------------

/** Human-readable explanation for why an event was not processed. */
function triageExplanation(decision: string | null | undefined): string | null {
  switch (decision) {
    case "metadata_only":
      return "Metadata-only ingestion: full content was not stored and no LLM session was spawned.";
    case "skip":
      return "Skipped by ingestion policy: this event matched a filter rule.";
    case "low_priority_queue":
      return "Queued as low-priority, session may be deferred.";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// SenderIdentityDisplay — shows resolved contact name or unresolved indicator
// ---------------------------------------------------------------------------

interface SenderIdentityDisplayProps {
  requestId: string;
  rawSenderIdentity: string | null | undefined;
}

function SenderIdentityDisplay({ requestId, rawSenderIdentity }: SenderIdentityDisplayProps) {
  const { data, isLoading } = useIngestionEventSenderContact(requestId, {
    enabled: !!rawSenderIdentity,
  });

  if (!rawSenderIdentity) return null;

  if (isLoading) {
    return (
      <span className="text-xs text-muted-foreground font-mono">{rawSenderIdentity}</span>
    );
  }

  const resolution = data?.data;

  if (resolution?.resolved && resolution.name) {
    return (
      <span className="text-xs" title={rawSenderIdentity ?? undefined}>
        <span className="font-medium">{resolution.name}</span>
        <span className="ml-1 text-muted-foreground">({rawSenderIdentity})</span>
      </span>
    );
  }

  return (
    <span className="text-xs flex items-center gap-1" data-testid="sender-unresolved">
      <span className="font-mono text-muted-foreground">{rawSenderIdentity}</span>
      <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 border-amber-400/60 text-amber-600 dark:text-amber-400">
        unresolved
      </Badge>
    </span>
  );
}

// ---------------------------------------------------------------------------
// LineageView — shows sessions and rollup for one expanded event
// ---------------------------------------------------------------------------

interface LineageViewProps {
  requestId: string;
  triageDecision?: string | null;
  senderIdentity?: string | null;
}

function LineageView({ requestId, triageDecision, senderIdentity }: LineageViewProps) {
  const { sessions, rollup } = useIngestionEventLineage(requestId, {
    enabled: true,
  });
  const contentRef = useRef<HTMLDivElement>(null);

  const isLoading = sessions.isLoading || rollup.isLoading;
  const sessionList = sessions.data?.data ?? [];
  const rollupData = rollup.data?.data;

  /** Smooth-scroll the content area to the session anchor. */
  function scrollToSession(sessionId: string) {
    const el = contentRef.current?.querySelector(`#session-${CSS.escape(sessionId)}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

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
    const explanation = triageExplanation(triageDecision);
    return (
      <div className="space-y-2 px-4 pt-3 pb-4">
        {/* Sender identity resolution — shown even when no sessions */}
        {senderIdentity && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium">Sender:</span>
            <SenderIdentityDisplay requestId={requestId} rawSenderIdentity={senderIdentity} />
          </div>
        )}
        <p className="text-sm text-muted-foreground">
          {explanation
            ? `Not processed: ${explanation}`
            : "No downstream sessions found for this event."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-4 px-4 pt-3 pb-4">
      {/* Left content: flamegraph + session table + rollup */}
      <div ref={contentRef} className="flex-1 min-w-0 space-y-4">
        {/* Sender identity resolution */}
        {senderIdentity && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium">Sender:</span>
            <SenderIdentityDisplay requestId={requestId} rawSenderIdentity={senderIdentity} />
          </div>
        )}

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
                <TableRow key={s.id} id={`session-${s.id}`}>
                  <TableCell className="font-medium">{s.butler_name}</TableCell>
                  <TableCell className="text-sm">
                    <div className="flex items-center gap-1">
                      <Link
                        to={`/sessions/${s.id}?butler=${encodeURIComponent(s.butler_name)}`}
                        className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                        title={s.id}
                      >
                        {truncateId(s.id)}
                      </Link>
                      <CopyButton value={s.id} label="" />
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {s.model ?? "—"}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {s.started_at ? <Time value={s.started_at} mode="absolute" precision="second" /> : "—"}
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

      {/* Right rail: session index for anchor-scroll navigation */}
      {sessionList.length > 1 && (
        <div className="w-40 shrink-0 pt-1" data-testid="session-index">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Sessions
          </p>
          <nav className="space-y-1">
            {sessionList.map((s, i) => (
              <button
                key={s.id}
                type="button"
                onClick={() => scrollToSession(s.id)}
                className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                data-testid={`session-index-item-${s.id}`}
              >
                <span
                  className="inline-block size-2 rounded-sm shrink-0"
                  style={{ backgroundColor: butlerHueVar(s.butler_name) }}
                />
                <span className="truncate">
                  #{i + 1} {s.butler_name}
                </span>
              </button>
            ))}
          </nav>
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

  const title = event.status === "replay_failed" ? "Retry" : "Replay";

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
      variant="ghost"
      size="icon"
      className="size-7"
      disabled={isPending}
      onClick={handleReplay}
      title={title}
      data-testid="replay-button"
    >
      {isPending ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <RotateCw className="size-3.5" />
      )}
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
          {event.received_at ? <Time value={event.received_at} mode="absolute" precision="second" /> : "—"}
        </TableCell>
        <TableCell className="text-sm">
          {event.source_channel ?? "—"}
        </TableCell>
        <TableCell className="max-w-[180px] truncate text-sm" title={event.source_sender_identity ?? undefined}>
          {event.source_sender_identity ?? "—"}
        </TableCell>
        <TableCell>
          <StatusBadge status={event.status} filterReason={event.filter_reason} errorDetail={event.error_detail} />
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
            <LineageView
              requestId={event.id}
              triageDecision={event.triage_decision}
              senderIdentity={event.source_sender_identity}
            />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// HourGroupHeader — mono eyebrow row separating events by hour
// ---------------------------------------------------------------------------

function HourGroupHeader({ label, colSpan }: { label: string; colSpan: number }) {
  return (
    <TableRow className="bg-muted/10 hover:bg-muted/10">
      <TableCell
        colSpan={colSpan}
        className="py-1 px-4 font-mono text-[10px] uppercase tracking-widest text-muted-foreground border-b"
      >
        {label}
      </TableCell>
    </TableRow>
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
// §2.9 ConnectorAttentionStrip
//
// Renders a compact strip of connectors whose health is degraded.
// Currently uses `state != 'healthy'` as the attention signal — the spec
// calls for `auth.status != 'ok'` but ConnectorSummary does not yet carry
// an `auth` field (it will be added in Wave 2 when the auth surface lands).
// Until then, unhealthy/offline connectors serve as a practical stand-in.
//
// Decision: extracted as a local component (not reusing the Settings Console
// AttentionStrip) because the Settings Console strip is driven by server-side
// AttentionItem objects with pre-built text and action_route fields, while the
// ingestion strip needs to derive items from ConnectorSummary data client-side.
// ---------------------------------------------------------------------------

function ConnectorAttentionStrip({ isActive }: { isActive: boolean }) {
  const { data: connectorsResp } = useConnectorSummaries({ enabled: isActive });
  const connectors = connectorsResp?.data ?? [];

  // Filter to connectors that need attention:
  // - state != 'healthy'  (degraded/error states)
  // - liveness == 'offline' (no heartbeat for 15+ min)
  //
  // TODO(Wave 2): Replace with `connector.auth?.status !== 'ok'` once the
  // auth surface is added to ConnectorSummary in §3.x.
  const attentionConnectors = connectors.filter(
    (c) => c.state !== "healthy" || c.liveness === "offline",
  );

  if (attentionConnectors.length === 0) return null;

  return (
    <div
      className="flex flex-wrap gap-2 rounded-md border border-amber-400/40 bg-amber-50/60 dark:bg-amber-950/20 px-3 py-2"
      data-testid="connector-attention-strip"
      role="alert"
      aria-label="Connectors requiring attention"
    >
      <div className="flex items-center gap-1.5 shrink-0 text-amber-700 dark:text-amber-400">
        <AlertTriangle className="size-3.5" aria-hidden />
        <span className="text-xs font-semibold">Connector issues:</span>
      </div>
      {attentionConnectors.map((c) => (
        <span
          key={`${c.connector_type}/${c.endpoint_identity}`}
          className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs bg-amber-100/80 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300"
          title={c.error_message ?? `${c.liveness} / ${c.state}`}
          data-testid="connector-attention-item"
        >
          <span className="font-medium">{c.connector_type}</span>
          <span className="text-amber-600/70 dark:text-amber-500/70">
            {c.endpoint_identity}
          </span>
          <span className="ml-0.5 text-[10px] text-amber-700 dark:text-amber-400">
            {c.state !== "healthy" ? c.state : c.liveness}
          </span>
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// §2.8 Saved Views
//
// Client-side view presets stored in localStorage under key
// `ingestion-saved-views`. Each view is a named set of enabled statuses.
//
// Built-in views:
//   All      — all statuses
//   Errors   — error + replay_pending + replay_failed
//   Priority — PLACEHOLDER: no-op until Wave 2 wires priority_contacts (§3.3)
//   Spend    — ingested + replay_complete (events that consumed LLM sessions)
// ---------------------------------------------------------------------------

const SAVED_VIEWS_STORAGE_KEY = "ingestion-saved-views";

type ViewId = "all" | "errors" | "priority" | "spend";

interface SavedView {
  id: ViewId;
  label: string;
  statuses: IngestionEventStatus[] | null; // null = placeholder (no filter applied)
}

const BUILT_IN_VIEWS: SavedView[] = [
  {
    id: "all",
    label: "All",
    statuses: ["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"],
  },
  {
    id: "errors",
    label: "Errors",
    statuses: ["error", "replay_pending", "replay_failed"],
  },
  {
    // PLACEHOLDER: Priority view will filter by priority_contacts once Wave 2
    // wires the priority_contacts table (Phase 3a §3.3). Until then, this view
    // is a no-op and renders the same as "All".
    id: "priority",
    label: "Priority",
    statuses: null,
  },
  {
    id: "spend",
    label: "Spend",
    statuses: ["ingested", "replay_complete"],
  },
];

/** Read the persisted active view ID from localStorage. */
function readPersistedView(): ViewId {
  try {
    const raw = localStorage.getItem(SAVED_VIEWS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed.activeView === "string") {
        return parsed.activeView as ViewId;
      }
    }
  } catch {
    // Malformed storage — fall through to default
  }
  return "all";
}

/** Persist the active view ID to localStorage. */
function persistView(viewId: ViewId): void {
  try {
    localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify({ activeView: viewId }));
  } catch {
    // localStorage unavailable (private browsing / quota exceeded) — ignore
  }
}

interface SavedViewSelectorProps {
  activeViewId: ViewId;
  onSelect: (viewId: ViewId) => void;
}

function SavedViewSelector({ activeViewId, onSelect }: SavedViewSelectorProps) {
  return (
    <div className="flex items-center gap-1" data-testid="saved-view-selector">
      {BUILT_IN_VIEWS.map((view) => (
        <button
          key={view.id}
          type="button"
          onClick={() => onSelect(view.id)}
          className={[
            "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            activeViewId === view.id
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
            view.statuses === null
              ? "opacity-60 cursor-default"  // placeholder view
              : "cursor-pointer",
          ].join(" ")}
          title={view.statuses === null ? "Priority view available in Wave 2 (§3.3)" : undefined}
          data-view={view.id}
          aria-pressed={activeViewId === view.id}
        >
          {view.label}
          {view.statuses === null && (
            <span className="ml-1 text-[10px] text-muted-foreground">(soon)</span>
          )}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status filter options
// ---------------------------------------------------------------------------

const ALL_STATUSES: IngestionEventStatus[] = [
  "ingested",
  "filtered",
  "error",
  "replay_pending",
  "replay_complete",
  "replay_failed",
];

const STATUS_LABELS: Record<IngestionEventStatus, string> = {
  ingested: "Ingested",
  filtered: "Filtered",
  error: "Error",
  replay_pending: "Replay Pending",
  replay_complete: "Replay Complete",
  replay_failed: "Replay Failed",
};

/** Default: all statuses except "filtered". */
const DEFAULT_STATUSES = ALL_STATUSES.filter((s) => s !== "filtered");

// ---------------------------------------------------------------------------
// TimelineTab
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

interface TimelineTabProps {
  isActive: boolean;
  /** Override the default enabled statuses (for testing). */
  defaultStatuses?: IngestionEventStatus[];
  /** Override the initial active view ID (for testing). */
  defaultViewId?: ViewId;
}

export function TimelineTab({ isActive, defaultStatuses, defaultViewId }: TimelineTabProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const expandedId = searchParams.get("expanded");

  // §2.8 Saved Views — persisted in localStorage; no server call
  const [activeViewId, setActiveViewId] = useState<ViewId>(
    () => defaultViewId ?? readPersistedView(),
  );

  // Derive the status set from the active view.
  // When defaultStatuses is provided (test override), it takes precedence.
  const viewStatuses = useMemo((): Set<IngestionEventStatus> => {
    if (defaultStatuses) return new Set(defaultStatuses);
    const view = BUILT_IN_VIEWS.find((v) => v.id === activeViewId);
    if (!view || view.statuses === null) {
      // Placeholder view (Priority) — treat as "All" until Wave 2
      return new Set(DEFAULT_STATUSES);
    }
    return new Set(view.statuses);
  }, [activeViewId, defaultStatuses]);

  // Multi-select status filter — driven by Saved View; individual checkbox
  // overrides are still available for fine-grained filtering on top of the view.
  const [enabledStatuses, setEnabledStatuses] = useState<Set<IngestionEventStatus>>(
    () => viewStatuses,
  );

  // Keep enabledStatuses in sync when the active view changes
  useEffect(() => {
    setEnabledStatuses(viewStatuses);
  }, [viewStatuses]);

  const handleViewSelect = useCallback((viewId: ViewId) => {
    setActiveViewId(viewId);
    persistView(viewId);
  }, []);

  // Optimistic overrides: map of event id → overridden status
  const [optimisticOverrides, setOptimisticOverrides] = useState<
    Map<string, IngestionEventStatus>
  >(new Map());

  // Infinite scroll query — cursor-based, no offset/total
  const {
    data: infiniteData,
    isLoading,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useIngestionEvents({ limit: PAGE_SIZE }, { enabled: isActive });

  // Flatten all pages into a single event list
  const rawEvents = useMemo(
    () => infiniteData?.pages.flatMap((page) => page.data) ?? [],
    [infiniteData?.pages],
  );

  // Evict stale optimistic overrides: once the server returns a status other
  // than replay_pending for an event we overrode, the server has caught up and
  // the override is no longer needed.
  useEffect(() => {
    setOptimisticOverrides((prev) => {
      if (prev.size === 0) return prev;
      const next = new Map(prev);
      for (const e of rawEvents) {
        if (prev.has(e.id) && e.status !== "replay_pending") {
          next.delete(e.id); // server has moved on; drop override
        }
      }
      return next.size === prev.size ? prev : next; // stable ref if no change
    });
  }, [rawEvents]);

  // Apply optimistic overrides so replayed events immediately show replay_pending
  const allEvents: IngestionEventSummary[] = rawEvents.map((e) => {
    const override = optimisticOverrides.get(e.id);
    return override ? { ...e, status: override } : e;
  });

  // Client-side status filtering
  const events = allEvents.filter((e) => enabledStatuses.has(e.status));

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

  const handleStatusToggle = useCallback(
    (status: IngestionEventStatus) => {
      setEnabledStatuses((prev) => {
        const next = new Set(prev);
        if (next.has(status)) {
          next.delete(status);
        } else {
          next.add(status);
        }
        return next;
      });
    },
    [],
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

  // Build table body: interleave hour-group headers between rows
  const TOTAL_COLS = 11;
  const tableBodyRows: React.ReactNode[] = [];
  let lastHourKey: string | null = null;

  for (const event of events) {
    const hKey = hourGroupKey(event.received_at);
    if (hKey !== lastHourKey) {
      const hLabel = hourGroupLabel(event.received_at);
      tableBodyRows.push(
        <HourGroupHeader key={`h-${hKey}`} label={hLabel} colSpan={TOTAL_COLS} />,
      );
      lastHourKey = hKey;
    }
    tableBodyRows.push(
      <EventRow
        key={event.id}
        event={event}
        isExpanded={expandedId === event.id}
        onToggle={() => handleToggle(event.id)}
        onOptimisticUpdate={handleOptimisticUpdate}
      />,
    );
  }

  return (
    <div className="space-y-4">
      {/* §2.9 Connector Attention Strip — unhealthy connectors above the table */}
      <ConnectorAttentionStrip isActive={isActive} />
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-4">
              <CardTitle>Ingestion Events</CardTitle>
              {/* §2.8 Saved Views selector */}
              <SavedViewSelector activeViewId={activeViewId} onSelect={handleViewSelect} />
            </div>
            {/* Status filter checkboxes — fine-grained override on top of Saved View */}
            <div className="flex items-center gap-3" data-testid="status-filter">
              <span className="text-sm text-muted-foreground">Status:</span>
              {ALL_STATUSES.map((status) => (
                <div key={status} className="flex items-center gap-1.5">
                  <Checkbox
                    id={`status-${status}`}
                    checked={enabledStatuses.has(status)}
                    onCheckedChange={() => handleStatusToggle(status)}
                  />
                  <Label
                    htmlFor={`status-${status}`}
                    className="text-sm font-normal cursor-pointer"
                  >
                    {STATUS_LABELS[status]}
                  </Label>
                </div>
              ))}
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
                    <TableCell colSpan={TOTAL_COLS}>
                      <EmptyState
                        title="No ingestion events."
                        description="Events appear once the system receives incoming messages."
                      />
                    </TableCell>
                  </TableRow>
                ) : (
                  tableBodyRows
                )}
              </TableBody>
            </Table>
          )}
          {/* Infinite scroll footer */}
          {events.length > 0 && (
            <div className="flex items-center justify-between border-t pt-3 mt-2 px-2">
              <span className="text-xs text-muted-foreground">
                Showing {events.length}{enabledStatuses.size < ALL_STATUSES.length ? ` (filtered from ${allEvents.length})` : ""}
              </span>
              {hasNextPage && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                >
                  {isFetchingNextPage ? <Loader2 className="size-3 animate-spin mr-1" /> : null}
                  Load more
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
