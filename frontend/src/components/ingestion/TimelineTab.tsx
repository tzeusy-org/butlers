/**
 * Timeline tab content for the /ingestion page.
 *
 * Dispatch-language ledger stream: no card chrome, hairline-divided rows,
 * hour-grouped with per-minute flame strip, URL-backed event drawer.
 *
 * Layout:
 * - Toolbar: range picker, saved views, status filter (multi-select chips)
 * - Bulk action bar (placeholder — no backend support yet)
 * - Connector attention strip
 * - Ledger: hour-group headers + event rows
 * - Footer: pagination / load-more
 * - Drawer: slides in below the clicked row (backed by ?event=<id>)
 *
 * Data sources:
 * - GET /api/ingestion/events          (cursor-paginated)
 * - GET /api/ingestion/events/{id}/sessions  (on expand / drawer)
 * - GET /api/ingestion/events/{id}/replays   (drawer replays tab)
 * - GET /api/ingestion/events/{id}/payload   (drawer raw tab, audit-gated)
 * - POST /api/ingestion/events/{id}/replay   (replay action)
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline Ledger"
 * Reference: pr/overview/ingestion-redesign/INGESTION_HANDOFF.md §1a
 *
 * §2.8 Saved Views: client-side localStorage key `ingestion-saved-views`.
 * §2.9 Connector Attention Strip: highlights connectors with degraded health.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { AlertTriangle, Loader2, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useIngestionEvents,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import type {
  IngestionEventSummary,
  IngestionEventStatus,
} from "@/api/index.ts";
import { replayIngestionEvent } from "@/api/index.ts";
import { StatusBadge } from "./StatusBadge";
import { HourFlameStrip } from "./timeline/HourFlameStrip";
import { deriveMinuteCounts } from "./timeline/deriveMinuteCounts";
import { EventDrawer } from "./timeline/EventDrawer";
import { useEventDrawerState } from "./timeline/useEventDrawerState";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncateId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) + "…" : id;
}

function formatCost(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.001) return "<$0.001";
  return `$${usd.toFixed(4)}`;
}

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString();
}

function isReplayable(status: IngestionEventStatus): boolean {
  return status !== "replay_pending" && status !== "ingested" && status !== "replay_complete";
}

function isReplayPending(status: IngestionEventStatus): boolean {
  return status === "replay_pending";
}

function hourGroupLabel(receivedAt: string | null): string {
  if (!receivedAt) return "Unknown time";
  try {
    const d = new Date(receivedAt);
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

function hourGroupKey(receivedAt: string | null): string {
  if (!receivedAt) return "unknown";
  try {
    return receivedAt.slice(0, 13); // "2026-05-17T14"
  } catch {
    return "unknown";
  }
}

// ---------------------------------------------------------------------------
// §2.8 Saved Views
// ---------------------------------------------------------------------------

const SAVED_VIEWS_STORAGE_KEY = "ingestion-saved-views";

type ViewId = "all" | "errors" | "priority" | "spend";

interface SavedView {
  id: ViewId;
  label: string;
  statuses: IngestionEventStatus[] | null;
}

const BUILT_IN_VIEWS: SavedView[] = [
  {
    id: "all",
    label: "All",
    statuses: ["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"],
  },
  {
    id: "errors",
    label: "Errors only",
    statuses: ["error", "replay_pending", "replay_failed"],
  },
  {
    id: "priority",
    label: "Priority",
    statuses: null, // placeholder — no backend priority_contacts yet
  },
  {
    id: "spend",
    label: "Spend",
    statuses: ["ingested", "replay_complete"],
  },
];

function readPersistedView(): ViewId {
  try {
    const raw = localStorage.getItem(SAVED_VIEWS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed.activeView === "string") return parsed.activeView as ViewId;
    }
  } catch {
    // Malformed — fall through
  }
  return "all";
}

function persistView(viewId: ViewId): void {
  try {
    localStorage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify({ activeView: viewId }));
  } catch {
    // localStorage unavailable — ignore
  }
}

// ---------------------------------------------------------------------------
// Status constants
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
  ingested: "ok",
  filtered: "filtered",
  error: "error",
  replay_pending: "replay",
  replay_complete: "replayed",
  replay_failed: "failed",
};

const DEFAULT_STATUSES = ALL_STATUSES.filter((s) => s !== "filtered");

// ---------------------------------------------------------------------------
// Toolbar — range picker, saved views, status filter
// ---------------------------------------------------------------------------

type IngestionRange = "1h" | "24h" | "7d";

const RANGE_OPTIONS: { id: IngestionRange; label: string }[] = [
  { id: "1h", label: "1h" },
  { id: "24h", label: "24h" },
  { id: "7d", label: "7d" },
];

interface ToolbarProps {
  range: IngestionRange;
  onRangeChange: (r: IngestionRange) => void;
  activeViewId: ViewId;
  onViewSelect: (v: ViewId) => void;
  enabledStatuses: Set<IngestionEventStatus>;
  onStatusToggle: (s: IngestionEventStatus) => void;
}

function Toolbar({
  range,
  onRangeChange,
  activeViewId,
  onViewSelect,
  enabledStatuses,
  onStatusToggle,
}: ToolbarProps) {
  return (
    <div className="flex items-center gap-3 flex-wrap py-2 border-b border-border" data-testid="timeline-toolbar">
      {/* Range picker */}
      <div className="flex items-center gap-0 border border-border rounded overflow-hidden" data-testid="range-picker">
        {RANGE_OPTIONS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => onRangeChange(id)}
            className={[
              "px-3 py-1 font-mono text-[11px] tracking-[0.01em] border-r border-border last:border-r-0 transition-colors",
              range === id
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            ].join(" ")}
            data-testid={`range-${id}`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Saved views */}
      <div className="flex items-center gap-1" data-testid="saved-view-selector">
        {BUILT_IN_VIEWS.map((view) => (
          <button
            key={view.id}
            type="button"
            onClick={() => {
              if (view.statuses !== null) onViewSelect(view.id);
            }}
            className={[
              "rounded px-2.5 py-1 font-mono text-[11px] transition-colors",
              activeViewId === view.id
                ? "bg-foreground/10 text-foreground border border-border"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
              view.statuses === null ? "opacity-50 cursor-default" : "cursor-pointer",
            ].join(" ")}
            title={view.statuses === null ? "Priority view available in Wave 2 (§3.3)" : undefined}
            data-view={view.id}
            aria-pressed={activeViewId === view.id}
          >
            {view.label}
            {view.statuses === null && (
              <span className="ml-1 text-[9px] text-muted-foreground">(soon)</span>
            )}
          </button>
        ))}
      </div>

      {/* Status filter chips */}
      <div className="flex items-center gap-1 ml-auto flex-wrap" data-testid="status-filter">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground mr-1">
          status:
        </span>
        {ALL_STATUSES.map((status) => {
          const active = enabledStatuses.has(status);
          return (
            <button
              key={status}
              type="button"
              onClick={() => onStatusToggle(status)}
              className={[
                "rounded px-2 py-0.5 font-mono text-[11px] border transition-colors",
                active
                  ? "border-foreground/30 bg-muted text-foreground"
                  : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
              ].join(" ")}
              data-testid={`status-filter-${status}`}
              aria-pressed={active}
            >
              {STATUS_LABELS[status]}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk action bar placeholder
// ---------------------------------------------------------------------------

interface BulkActionBarProps {
  selectedCount: number;
  onClearSelection: () => void;
}

function BulkActionBar({ selectedCount, onClearSelection }: BulkActionBarProps) {
  if (selectedCount === 0) return null;
  return (
    <div
      className="flex items-center gap-3 py-2 px-3 bg-muted/30 border border-border rounded text-sm"
      data-testid="bulk-action-bar"
    >
      <span className="font-mono text-[11px] text-muted-foreground">{selectedCount} selected</span>
      <Button
        variant="outline"
        size="sm"
        disabled
        title="Bulk retry requires backend support (filed as follow-up)"
        className="font-mono text-[11px] h-7"
        data-testid="bulk-retry-button"
      >
        <RotateCw className="size-3 mr-1" />
        Replay all
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="font-mono text-[11px] h-7 text-muted-foreground"
        onClick={onClearSelection}
        data-testid="bulk-clear-button"
      >
        Clear
      </Button>
      <p className="font-mono text-[10px] text-muted-foreground ml-auto">
        Bulk retry: no backend endpoint yet — filed as follow-up bead.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// §2.9 ConnectorAttentionStrip
// ---------------------------------------------------------------------------

function ConnectorAttentionStrip({ isActive }: { isActive: boolean }) {
  const { data: connectorsResp } = useConnectorSummaries({ enabled: isActive });
  const connectors = connectorsResp?.data ?? [];
  const attentionConnectors = connectors.filter(
    (c) => c.state !== "healthy" || c.liveness === "offline",
  );

  if (attentionConnectors.length === 0) return null;

  return (
    <div
      className="flex flex-wrap gap-2 px-3 py-2 border-b border-border"
      data-testid="connector-attention-strip"
      role="alert"
      aria-label="Connectors requiring attention"
    >
      <div className="flex items-center gap-1.5 shrink-0 text-muted-foreground">
        <AlertTriangle className="size-3.5" aria-hidden />
        <span className="font-mono text-[10px] uppercase tracking-[0.14em]">Connector issues:</span>
      </div>
      {attentionConnectors.map((c) => (
        <span
          key={`${c.connector_type}/${c.endpoint_identity}`}
          className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground underline"
          title={c.error_message ?? `${c.liveness} / ${c.state}`}
          data-testid="connector-attention-item"
        >
          {c.connector_type}/{c.endpoint_identity} · {c.state !== "healthy" ? c.state : c.liveness}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// LedgerRow — one row in the event ledger
// ---------------------------------------------------------------------------

interface LedgerRowProps {
  event: IngestionEventSummary;
  isExpanded: boolean;
  isSelected: boolean;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
}

function LedgerRow({
  event,
  isExpanded,
  isSelected,
  onToggleExpand,
  onToggleSelect,
  onOptimisticUpdate,
}: LedgerRowProps) {
  const { data: rollupResp } = useIngestionEventRollup(event.id);
  const r = rollupResp?.data;
  const { data: senderResp } = useIngestionEventSenderContact(event.id, {
    enabled: !!event.source_sender_identity,
  });
  const resolvedName = senderResp?.data?.resolved ? senderResp.data.name : null;

  const [isReplaying, setIsReplaying] = useState(false);
  const canExpand = event.status !== "filtered" && event.status !== "error";

  async function handleReplay(e: React.MouseEvent) {
    e.stopPropagation();
    setIsReplaying(true);
    try {
      await replayIngestionEvent(event.id);
      onOptimisticUpdate(event.id, "replay_pending");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Replay request failed");
    } finally {
      setIsReplaying(false);
    }
  }

  return (
    <div
      className={[
        "grid items-center px-3 py-2 border-b border-border/50 text-[13px] transition-colors",
        "grid-cols-[20px_80px_auto_1fr_80px_60px_60px_80px_32px]",
        canExpand ? "cursor-pointer" : "",
        isExpanded ? "bg-muted/20" : "hover:bg-muted/10",
      ].join(" ")}
      style={{ gridTemplateColumns: "20px 80px auto 1fr 80px 60px 60px 80px 32px" }}
      onClick={() => canExpand && onToggleExpand()}
      aria-expanded={canExpand ? isExpanded : undefined}
      data-testid="ledger-row"
      data-event-id={event.id}
    >
      {/* Checkbox */}
      <div onClick={(e) => { e.stopPropagation(); onToggleSelect(); }} className="flex items-center">
        <div className={[
          "size-4 rounded border border-border/60 flex items-center justify-center transition-colors",
          isSelected ? "bg-foreground" : "hover:border-foreground/40",
        ].join(" ")}>
          {isSelected && <div className="size-2 rounded-sm bg-background" />}
        </div>
      </div>

      {/* Short ID */}
      <span className="font-mono text-[11px] text-muted-foreground truncate" title={event.id}>
        {truncateId(event.id)}
      </span>

      {/* Channel glyph + name */}
      <div className="flex items-center gap-1.5">
        <span
          className="inline-flex size-5 items-center justify-center rounded text-[10px] font-medium text-white shrink-0"
          style={{ backgroundColor: "var(--muted-foreground)" }}
          aria-hidden="true"
        >
          {(event.source_channel ?? "?").charAt(0).toUpperCase()}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {event.source_channel ?? "—"}
        </span>
      </div>

      {/* Sender + summary */}
      <div className="min-w-0 pr-2">
        <span className="truncate block font-serif text-[13px] leading-[1.5]">
          {resolvedName ?? event.source_sender_identity ?? "—"}
        </span>
      </div>

      {/* Status */}
      <div>
        <StatusBadge
          status={event.status}
          filterReason={event.filter_reason}
          errorDetail={event.error_detail}
        />
      </div>

      {/* Tokens in */}
      <span className="text-right tabular-nums font-mono text-[11px] text-muted-foreground">
        {r ? fmtNum(r.total_input_tokens) : "—"}
      </span>

      {/* Tokens out */}
      <span className="text-right tabular-nums font-mono text-[11px] text-muted-foreground">
        {r ? fmtNum(r.total_output_tokens) : "—"}
      </span>

      {/* Cost */}
      <span className="text-right tabular-nums font-mono text-[11px]">
        {r ? formatCost(r.total_cost) : "—"}
      </span>

      {/* Replay / chevron */}
      <div className="flex items-center justify-end gap-0" onClick={(e) => e.stopPropagation()}>
        {isReplayPending(event.status) ? (
          <Loader2 className="size-3 animate-spin text-muted-foreground" data-testid="replay-pending-spinner" />
        ) : isReplayable(event.status) ? (
          <button
            type="button"
            onClick={handleReplay}
            disabled={isReplaying}
            className="rounded p-1 hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            title={event.status === "replay_failed" ? "Retry" : "Replay"}
            data-testid="replay-button"
          >
            {isReplaying ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RotateCw className="size-3" />
            )}
          </button>
        ) : canExpand ? (
          <span className="font-mono text-[10px] text-muted-foreground select-none">
            {isExpanded ? "▲" : "▼"}
          </span>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HourGroup — header row + events for one hour bucket
// ---------------------------------------------------------------------------

interface HourGroupProps {
  label: string;
  hourKey: string;
  events: IngestionEventSummary[];
  drawerEventId: string | null;
  selectedIds: Set<string>;
  onOpenDrawer: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
  drawerEvent: IngestionEventSummary | null;
  onCloseDrawer: () => void;
}

function HourGroup({
  label,
  hourKey,
  events,
  drawerEventId,
  selectedIds,
  onOpenDrawer,
  onToggleSelect,
  onOptimisticUpdate,
  drawerEvent,
  onCloseDrawer,
}: HourGroupProps) {
  const hourStart = hourKey !== "unknown" ? hourKey + ":00:00Z" : "";
  const minuteCounts = useMemo(
    () => deriveMinuteCounts(events.map((e) => e.received_at), hourStart),
    [events, hourStart],
  );

  return (
    <div data-testid="hour-group" data-hour-key={hourKey}>
      {/* Hour group header */}
      <div className="flex items-center gap-3 px-3 py-1.5 bg-muted/10 border-b border-border/50">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">
          {events.length} {events.length === 1 ? "event" : "events"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <HourFlameStrip minuteCounts={minuteCounts} height={16} data-testid="hour-flame-strip" />
        </div>
      </div>

      {/* Event rows */}
      {events.map((event) => (
        <div key={event.id}>
          <LedgerRow
            event={event}
            isExpanded={drawerEventId === event.id}
            isSelected={selectedIds.has(event.id)}
            onToggleExpand={() => onOpenDrawer(event.id)}
            onToggleSelect={() => onToggleSelect(event.id)}
            onOptimisticUpdate={onOptimisticUpdate}
          />

          {/* Inline drawer below this row when it's the focused event */}
          {drawerEventId === event.id && drawerEvent && (
            <EventDrawer
              event={drawerEvent}
              onClose={onCloseDrawer}
              onOptimisticUpdate={onOptimisticUpdate}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// LedgerSkeleton
// ---------------------------------------------------------------------------

function LedgerSkeleton() {
  return (
    <div className="space-y-1 p-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full rounded" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column headers
// ---------------------------------------------------------------------------

function LedgerColumnHeaders() {
  return (
    <div
      className="grid items-center px-3 py-1 border-b border-border bg-muted/5"
      style={{ gridTemplateColumns: "20px 80px auto 1fr 80px 60px 60px 80px 32px" }}
    >
      <div />
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">id</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">channel</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">sender</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">status</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground text-right">in</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground text-right">out</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground text-right">cost</span>
      <div />
    </div>
  );
}

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

  // ?event=<id> — drawer URL state
  const { eventId: drawerEventId, openDrawer, closeDrawer } = useEventDrawerState();

  // Range state (writes to URL)
  const urlRange = searchParams.get("range") as IngestionRange | null;
  const [range, setRange] = useState<IngestionRange>(
    urlRange && ["1h", "24h", "7d"].includes(urlRange) ? (urlRange as IngestionRange) : "24h",
  );

  const handleRangeChange = useCallback(
    (r: IngestionRange) => {
      setRange(r);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("range", r);
        return next;
      });
    },
    [setSearchParams],
  );

  // Saved views
  const [activeViewId, setActiveViewId] = useState<ViewId>(
    () => defaultViewId ?? readPersistedView(),
  );

  const viewStatuses = useMemo((): Set<IngestionEventStatus> => {
    if (defaultStatuses) return new Set(defaultStatuses);
    const view = BUILT_IN_VIEWS.find((v) => v.id === activeViewId);
    if (!view || view.statuses === null) return new Set(DEFAULT_STATUSES);
    return new Set(view.statuses);
  }, [activeViewId, defaultStatuses]);

  const [enabledStatuses, setEnabledStatuses] = useState<Set<IngestionEventStatus>>(
    () => viewStatuses,
  );

  useEffect(() => {
    setEnabledStatuses(viewStatuses);
  }, [viewStatuses]);

  const handleViewSelect = useCallback((viewId: ViewId) => {
    setActiveViewId(viewId);
    persistView(viewId);
  }, []);

  const handleStatusToggle = useCallback((status: IngestionEventStatus) => {
    setEnabledStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  }, []);

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const handleToggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleClearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // Optimistic overrides
  const [optimisticOverrides, setOptimisticOverrides] = useState<Map<string, IngestionEventStatus>>(
    new Map(),
  );

  const handleOptimisticUpdate = useCallback((id: string, newStatus: IngestionEventStatus) => {
    setOptimisticOverrides((prev) => {
      const next = new Map(prev);
      next.set(id, newStatus);
      return next;
    });
  }, []);

  // Events query
  const {
    data: infiniteData,
    isLoading,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useIngestionEvents({ limit: PAGE_SIZE }, { enabled: isActive });

  const rawEvents = useMemo(
    () => infiniteData?.pages.flatMap((page) => page.data) ?? [],
    [infiniteData?.pages],
  );

  // Evict stale overrides
  useEffect(() => {
    setOptimisticOverrides((prev) => {
      if (prev.size === 0) return prev;
      const next = new Map(prev);
      for (const e of rawEvents) {
        if (prev.has(e.id) && e.status !== "replay_pending") next.delete(e.id);
      }
      return next.size === prev.size ? prev : next;
    });
  }, [rawEvents]);

  const allEvents: IngestionEventSummary[] = rawEvents.map((e) => {
    const override = optimisticOverrides.get(e.id);
    return override ? { ...e, status: override } : e;
  });

  const events = allEvents.filter((e) => enabledStatuses.has(e.status));

  // Find the drawer event in the current event list
  const drawerEvent = drawerEventId
    ? events.find((e) => e.id === drawerEventId) ?? null
    : null;

  // Group events by hour
  interface HourGroup {
    key: string;
    label: string;
    events: IngestionEventSummary[];
  }

  const hourGroups = useMemo((): HourGroup[] => {
    const groups: HourGroup[] = [];
    let currentKey: string | null = null;

    for (const event of events) {
      const hKey = hourGroupKey(event.received_at);
      if (hKey !== currentKey) {
        groups.push({ key: hKey, label: hourGroupLabel(event.received_at), events: [] });
        currentKey = hKey;
      }
      groups[groups.length - 1].events.push(event);
    }
    return groups;
  }, [events]);

  return (
    <div className="space-y-3" data-testid="timeline-tab">
      {/* Toolbar */}
      <Toolbar
        range={range}
        onRangeChange={handleRangeChange}
        activeViewId={activeViewId}
        onViewSelect={handleViewSelect}
        enabledStatuses={enabledStatuses}
        onStatusToggle={handleStatusToggle}
      />

      {/* Bulk action bar */}
      <BulkActionBar
        selectedCount={selectedIds.size}
        onClearSelection={handleClearSelection}
      />

      {/* Connector attention strip */}
      <ConnectorAttentionStrip isActive={isActive} />

      {/* Ledger */}
      <div className="border border-border rounded" data-testid="timeline-ledger">
        <LedgerColumnHeaders />

        {isError ? (
          <p className="px-6 py-4 font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
            Failed to load ingestion events.
          </p>
        ) : isLoading ? (
          <LedgerSkeleton />
        ) : events.length === 0 ? (
          <div className="px-6 py-8">
            <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
              No events match the current filters.
            </p>
          </div>
        ) : (
          <>
            {hourGroups.map((group) => (
              <HourGroup
                key={group.key}
                label={group.label}
                hourKey={group.key}
                events={group.events}
                drawerEventId={drawerEventId}
                selectedIds={selectedIds}
                onOpenDrawer={(id) => openDrawer(id)}
                onToggleSelect={handleToggleSelect}
                onOptimisticUpdate={handleOptimisticUpdate}
                drawerEvent={drawerEvent}
                onCloseDrawer={closeDrawer}
              />
            ))}
          </>
        )}
      </div>

      {/* Load more footer */}
      {events.length > 0 && (
        <div className="flex items-center justify-between pt-1 px-1">
          <span className="font-mono text-[11px] text-muted-foreground">
            Showing {events.length}
            {enabledStatuses.size < ALL_STATUSES.length
              ? ` (filtered from ${allEvents.length})`
              : ""}
          </span>
          {hasNextPage && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => fetchNextPage()}
              disabled={isFetchingNextPage}
              className="font-mono text-[11px]"
            >
              {isFetchingNextPage ? <Loader2 className="size-3 animate-spin mr-1" /> : null}
              Load more
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
