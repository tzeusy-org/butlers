/**
 * Timeline tab content for the /ingestion page.
 *
 * Dispatch-language ledger stream: no card chrome, hairline-divided rows,
 * hour-grouped with per-minute flame strip, URL-backed event drawer.
 *
 * Layout:
 * - Toolbar: range picker, search input, saved views, channel chips, status filter
 * - Bulk action bar
 * - Connector attention strip
 * - Ledger: hour-group headers + event rows
 * - Footer rollup band: events / sessions / cost for the active filter window
 * - Footer: pagination / load-more
 * - Drawer: slides in below the clicked row (backed by ?event=<id>)
 *
 * Data sources:
 * - GET /api/ingestion/events          (cursor-paginated; supports ?q= search)
 * - GET /api/ingestion/rollup          (window-level aggregate; bu-mxtn2)
 * - GET /api/ingestion/events/{id}/sessions  (on expand / drawer)
 * - GET /api/ingestion/events/{id}/replays   (drawer replays tab)
 * - GET /api/ingestion/events/{id}/payload   (drawer raw tab, audit-gated)
 * - POST /api/ingestion/events/{id}/replay   (replay action)
 * - POST /api/ingestion/events/retry/bulk    (bulk replay action; email/replay-unsafe events rejected with 409)
 * - GET/POST/PATCH/DELETE /api/timeline/saved-views  (custom saved views; bu-vgj88)
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline Ledger"
 * Reference: docs/redesigns/ingestion-handoff.md §1a
 *
 * §2.8 Saved Views: built-in presets + custom views persisted via backend API.
 *   Built-in active selection persisted to localStorage key `ingestion-saved-views`.
 *   Custom views stored in public.timeline_saved_views.
 * §2.9 Connector Attention Strip: highlights connectors with degraded health.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { AlertTriangle, BookmarkPlus, Copy, Loader2, RotateCw, Search, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useIngestionEvents,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionWindowRollup,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type {
  IngestionEventSummary,
  IngestionEventStatus,
  TimelineSavedViewEntry,
  TimelineSavedViewFilterSpec,
} from "@/api/index.ts";
import { ApiError, bulkRetryEvents, replayIngestionEvent } from "@/api/index.ts";
import { StatusBadge } from "./StatusBadge";
import { isBulkEligible, bulkIneligibleReason } from "./bulkEligibility";
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
  return (
    status !== "replay_pending" &&
    status !== "ingested" &&
    status !== "skipped" &&
    status !== "replay_complete"
  );
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

/** Built-in view IDs. */
type BuiltInViewId = "all" | "errors" | "priority" | "spend";

/**
 * Active view ID — either a built-in preset or a custom view's UUID string.
 * Custom UUIDs always contain a hyphen, built-in IDs never do; no collision.
 */
type ViewId = BuiltInViewId | string;

interface SavedView {
  id: BuiltInViewId;
  label: string;
  statuses: IngestionEventStatus[] | null;
  /** Tooltip shown when statuses is null (disabled/placeholder). */
  disabledTitle?: string;
}

const BUILT_IN_VIEWS: SavedView[] = [
  {
    id: "all",
    label: "All",
    // All real traffic — noise statuses ("skipped" skip-triaged events,
    // "filtered" rule drops) stay hidden until toggled on via the status chips.
    statuses: ["ingested", "error", "replay_pending", "replay_complete", "replay_failed"],
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
    disabledTitle: "Priority view available in Wave 2 (§3.3)",
  },
  {
    id: "spend",
    label: "Spend",
    // Same statuses as "All" — cost sort applies to dispatched events.
    // Enabled by core_126: cost_usd is now denormalized onto ingestion_events.
    statuses: ["ingested", "error", "replay_pending", "replay_complete", "replay_failed"],
  },
];

const BUILT_IN_IDS = new Set<string>(BUILT_IN_VIEWS.map((v) => v.id));

function isBuiltInViewId(id: string): id is BuiltInViewId {
  return BUILT_IN_IDS.has(id);
}

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
  "skipped",
  "filtered",
  "error",
  "replay_pending",
  "replay_complete",
  "replay_failed",
];

const STATUS_LABELS: Record<IngestionEventStatus, string> = {
  ingested: "ok",
  skipped: "skipped",
  filtered: "filtered",
  error: "error",
  replay_pending: "replay",
  replay_complete: "replayed",
  replay_failed: "failed",
};

// "skipped" (stored but not dispatched — e.g. home_assistant sensor streams)
// and "filtered" are noise statuses, hidden by default.
const DEFAULT_STATUSES = ALL_STATUSES.filter((s) => s !== "filtered" && s !== "skipped");

// ---------------------------------------------------------------------------
// Toolbar — range picker, search input, saved views, channel chips, status filter
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
  searchQuery: string;
  onSearchChange: (q: string) => void;
  activeChannels: string[];
  onChannelRemove: (channel: string) => void;
  /** Custom saved views from the backend (undefined = loading/unavailable). */
  customViews?: TimelineSavedViewEntry[];
  /** Whether the custom-views list is loading. */
  customViewsLoading?: boolean;
  /** Called when the user wants to save the current filter combination. */
  onSaveView: () => void;
  /** Called to delete a custom saved view by UUID. */
  onDeleteCustomView: (id: string) => void;
}

function Toolbar({
  range,
  onRangeChange,
  activeViewId,
  onViewSelect,
  enabledStatuses,
  onStatusToggle,
  searchQuery,
  onSearchChange,
  activeChannels,
  onChannelRemove,
  customViews,
  customViewsLoading,
  onSaveView,
  onDeleteCustomView,
}: ToolbarProps) {
  return (
    <div className="flex flex-col gap-0 border-b border-border" data-testid="timeline-toolbar">
      {/* Primary toolbar row */}
      <div className="flex items-center gap-3 flex-wrap py-2">
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

        {/* Search input */}
        <div className="relative flex items-center" data-testid="search-input-wrapper">
          <Search className="absolute left-2 size-3 text-muted-foreground pointer-events-none" aria-hidden />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="search events…"
            className={[
              "pl-7 pr-2 py-1 font-mono text-[11px] bg-transparent border border-border rounded",
              "text-foreground placeholder:text-muted-foreground",
              "focus:outline-none focus:ring-1 focus:ring-ring transition-colors",
              "w-44",
            ].join(" ")}
            data-testid="search-input"
            aria-label="Search events"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange("")}
              className="absolute right-1.5 p-0.5 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear search"
              data-testid="search-clear"
            >
              <X className="size-3" />
            </button>
          )}
        </div>

        {/* Saved views: built-in presets + custom views */}
        <div className="flex items-center gap-1" data-testid="saved-view-selector">
          {/* Built-in presets */}
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
              title={view.statuses === null ? view.disabledTitle : undefined}
              data-view={view.id}
              aria-pressed={activeViewId === view.id}
            >
              {view.label}
              {view.statuses === null && (
                <span className="ml-1 text-[9px] text-muted-foreground">(soon)</span>
              )}
            </button>
          ))}

          {/* Separator between built-ins and custom views */}
          {(customViewsLoading || (customViews && customViews.length > 0)) && (
            <div className="w-px h-4 bg-border/60 mx-0.5" aria-hidden />
          )}

          {/* Custom views from backend */}
          {customViewsLoading && (
            <Skeleton className="h-6 w-16 rounded" data-testid="custom-views-loading" />
          )}
          {!customViewsLoading && customViews?.map((view) => (
            <div key={view.id} className="relative flex items-center group">
              <button
                type="button"
                onClick={() => onViewSelect(view.id)}
                className={[
                  "rounded px-2.5 py-1 font-mono text-[11px] transition-colors pr-6",
                  activeViewId === view.id
                    ? "bg-foreground/10 text-foreground border border-border"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
                data-view={view.id}
                data-testid={`custom-view-${view.id}`}
                aria-pressed={activeViewId === view.id}
                title={view.name}
              >
                {view.name}
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteCustomView(view.id);
                }}
                className={[
                  "absolute right-0.5 p-0.5 rounded transition-colors",
                  "text-muted-foreground/40 hover:text-destructive",
                  "opacity-0 group-hover:opacity-100 focus:opacity-100",
                ].join(" ")}
                aria-label={`Delete saved view: ${view.name}`}
                data-testid={`custom-view-delete-${view.id}`}
                title={`Delete "${view.name}"`}
              >
                <Trash2 className="size-2.5" aria-hidden />
              </button>
            </div>
          ))}

          {/* Save current view button */}
          <button
            type="button"
            onClick={onSaveView}
            className={[
              "rounded px-2 py-1 font-mono text-[11px] transition-colors",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
              "flex items-center gap-1",
            ].join(" ")}
            aria-label="Save current view"
            data-testid="save-view-button"
            title="Save current filter combination as a named view"
          >
            <BookmarkPlus className="size-3" aria-hidden />
          </button>
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

      {/* Channel filter chips row — only rendered when channels are active */}
      {activeChannels.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pb-2" data-testid="channel-chips">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            channels:
          </span>
          {activeChannels.map((channel) => (
            <button
              key={channel}
              type="button"
              onClick={() => onChannelRemove(channel)}
              className={[
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5",
                "font-mono text-[11px] border border-border/60 bg-muted/40 text-foreground",
                "hover:bg-muted hover:border-border transition-colors",
              ].join(" ")}
              aria-label={`Remove channel filter: ${channel}`}
              data-testid={`channel-chip-${channel}`}
            >
              {channel}
              <X className="size-2.5" aria-hidden />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk action bar
// ---------------------------------------------------------------------------

const MAX_BULK_RETRY_BATCH = 100;

interface BulkActionBarProps {
  selectedCount: number;
  selectedIds: string[];
  onClearSelection: () => void;
  onDeselectIds: (ids: string[]) => void;
}

function BulkActionBar({ selectedCount, selectedIds, onClearSelection, onDeselectIds }: BulkActionBarProps) {
  const [isRetrying, setIsRetrying] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [copySuccess, setCopySuccess] = useState(false);

  if (selectedCount === 0) return null;

  const overLimit = selectedCount > MAX_BULK_RETRY_BATCH;
  const disabled = overLimit || isRetrying;

  async function handleReplayAll() {
    if (disabled) return;
    setIsRetrying(true);
    setErrorMsg(null);
    try {
      const result = await bulkRetryEvents(selectedIds);

      const succeededIds = result.results
        .filter((r) => r.status === "replay_pending")
        .map((r) => r.event_id);

      if (succeededIds.length > 0) {
        onDeselectIds(succeededIds);
        toast.success(
          `${succeededIds.length} event${succeededIds.length !== 1 ? "s" : ""} queued for replay`,
        );
      }

      if (result.failed > 0) {
        const failedMsg = `${result.failed} event${result.failed !== 1 ? "s" : ""} failed to queue`;
        setErrorMsg(failedMsg);
        toast.error(failedMsg);
      }
    } catch (err: unknown) {
      // 409 means the batch contains email or replay-unsafe events — surface a clear message.
      if (err instanceof ApiError && err.status === 409) {
        const msg = "Selection contains email or replay-unsafe events. Remove them and retry";
        setErrorMsg(msg);
        toast.error(msg);
      } else {
        const msg = err instanceof Error ? err.message : "Bulk replay failed";
        setErrorMsg(msg);
      }
    } finally {
      setIsRetrying(false);
    }
  }

  async function handleCopyIds() {
    if (!navigator.clipboard) {
      toast.error("Clipboard API not available (requires HTTPS or localhost)");
      return;
    }
    try {
      await navigator.clipboard.writeText(selectedIds.join("\n"));
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch {
      toast.error("Failed to copy IDs to clipboard");
    }
  }

  return (
    <div
      className="flex items-center gap-3 py-2 px-3 bg-muted/30 border border-border rounded text-sm"
      data-testid="bulk-action-bar"
    >
      <span className="font-mono text-[11px] text-muted-foreground">{selectedCount} selected</span>
      <Button
        variant="outline"
        size="sm"
        disabled={disabled}
        title={
          overLimit
            ? `Select at most ${MAX_BULK_RETRY_BATCH} events at once`
            : isRetrying
              ? "Replaying…"
              : "Replay selected events"
        }
        className="font-mono text-[11px] h-7"
        data-testid="bulk-retry-button"
        onClick={handleReplayAll}
      >
        {isRetrying ? (
          <Loader2 className="size-3 mr-1 animate-spin" />
        ) : (
          <RotateCw className="size-3 mr-1" />
        )}
        Replay all
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="font-mono text-[11px] h-7 text-muted-foreground"
        onClick={handleCopyIds}
        title="Copy selected event IDs to clipboard"
        data-testid="bulk-copy-ids-button"
      >
        <Copy className="size-3 mr-1" />
        {copySuccess ? "Copied!" : "Copy IDs"}
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
      {overLimit && (
        <p className="font-mono text-[10px] text-amber-600 ml-auto" data-testid="bulk-overlimit-msg">
          Max {MAX_BULK_RETRY_BATCH} events per batch
        </p>
      )}
      {errorMsg && (
        <p className="font-mono text-[10px] text-destructive ml-auto" data-testid="bulk-error-msg">
          {errorMsg}
        </p>
      )}
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
// Shared layout constant — keep LedgerRow and LedgerColumnHeaders in sync
// ---------------------------------------------------------------------------

const LEDGER_GRID_COLUMNS = "20px 80px 160px 1fr 80px 60px 60px 80px 32px"

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
  const eligible = isBulkEligible(event.status);
  const ineligibleReason = bulkIneligibleReason(event.status);
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
        "grid items-center gap-x-3 px-3 py-2 border-b border-border/50 text-[13px] transition-colors",
        canExpand ? "cursor-pointer" : "",
        isExpanded ? "bg-muted/20" : "hover:bg-muted/10",
      ].join(" ")}
      style={{ gridTemplateColumns: LEDGER_GRID_COLUMNS }}
      onClick={() => canExpand && onToggleExpand()}
      aria-expanded={canExpand ? isExpanded : undefined}
      data-testid="ledger-row"
      data-event-id={event.id}
    >
      {/* Checkbox — disabled and titled with reason for ineligible-status rows */}
      <div
        onClick={(e) => {
          e.stopPropagation();
          if (eligible) onToggleSelect();
        }}
        onKeyDown={(e) => {
          if (eligible && (e.key === " " || e.key === "Enter")) {
            e.preventDefault();
            e.stopPropagation();
            onToggleSelect();
          }
        }}
        tabIndex={eligible ? 0 : -1}
        className={["flex items-center", eligible ? "" : "cursor-not-allowed"].join(" ")}
        title={eligible ? undefined : (ineligibleReason ?? undefined)}
        data-testid={eligible ? "row-checkbox" : "row-checkbox-disabled"}
        aria-disabled={eligible ? undefined : true}
        role="checkbox"
        aria-checked={isSelected}
        aria-label={eligible ? "Select event" : (ineligibleReason ?? "Ineligible for bulk replay")}
      >
        <div className={[
          "size-4 rounded border flex items-center justify-center transition-colors",
          eligible
            ? isSelected
              ? "border-border/60 bg-foreground"
              : "border-border/60 hover:border-foreground/40"
            : "border-border/30 bg-muted/20 opacity-40",
        ].join(" ")}>
          {isSelected && eligible && <div className="size-2 rounded-sm bg-background" />}
        </div>
      </div>

      {/* Short ID */}
      <span className="font-mono text-[11px] text-muted-foreground truncate" title={event.id}>
        {truncateId(event.id)}
      </span>

      {/* Channel glyph + name */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className="inline-flex size-5 items-center justify-center rounded text-[10px] font-medium text-white shrink-0"
          style={{ backgroundColor: "var(--muted-foreground)" }}
          aria-hidden="true"
        >
          {(event.source_channel ?? "?").charAt(0).toUpperCase()}
        </span>
        <span
          className="font-mono text-[11px] text-muted-foreground truncate"
          title={event.source_channel ?? undefined}
        >
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
            onToggleExpand={() =>
              drawerEventId === event.id ? onCloseDrawer() : onOpenDrawer(event.id)
            }
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
      className="grid items-center gap-x-3 px-3 py-1 border-b border-border bg-muted/5"
      style={{ gridTemplateColumns: LEDGER_GRID_COLUMNS }}
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
// FooterRollupBand — aggregate events / sessions / cost for the active filter
// ---------------------------------------------------------------------------

interface FooterRollupBandProps {
  events: number | undefined;
  sessions: number | undefined;
  cost: number | null | undefined;
  isLoading: boolean;
}

function FooterRollupBand({ events, sessions, cost, isLoading }: FooterRollupBandProps) {
  const cell = (label: string, value: string) => (
    <div className="flex flex-col items-center gap-0.5 min-w-[80px]">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className="tabular-nums font-mono text-[13px] text-foreground">
        {isLoading ? <span className="text-muted-foreground">…</span> : value}
      </span>
    </div>
  );

  return (
    <div
      className="flex items-center justify-center gap-8 border-t border-border py-2 bg-muted/5"
      data-testid="footer-rollup-band"
      aria-label="Filter window aggregate counts"
    >
      {cell("events", events !== undefined ? events.toLocaleString() : "—")}
      <div className="w-px h-4 bg-border/60" aria-hidden />
      {cell("sessions", sessions !== undefined ? sessions.toLocaleString() : "—")}
      <div className="w-px h-4 bg-border/60" aria-hidden />
      {/* cost is populated live from /rollup when pricing is available; render em dash when null */}
      {cell("cost", cost !== null && cost !== undefined ? formatCost(cost) : "—")}
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
  /**
   * Called whenever the latest event's received_at changes.
   * The parent page uses this to drive the live-status badge honestly.
   * Passes null when no events have loaded yet.
   */
  onFreshnessChange?: (latestReceivedAt: string | null) => void;
}

export function TimelineTab({ isActive, defaultStatuses, defaultViewId, onFreshnessChange }: TimelineTabProps) {
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

  // Custom saved views from backend
  const {
    data: customViewsResp,
    isPending: customViewsLoading,
  } = useTimelineSavedViews({ enabled: isActive });
  const customViews = useMemo(
    () => customViewsResp?.data ?? [],
    [customViewsResp?.data],
  );

  const createSavedView = useCreateTimelineSavedView();
  const deleteSavedView = useDeleteTimelineSavedView();

  // "Save current view" dialog state
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");

  const viewStatuses = useMemo((): Set<IngestionEventStatus> => {
    if (defaultStatuses) return new Set(defaultStatuses);
    // Check built-in views first
    if (isBuiltInViewId(activeViewId)) {
      const view = BUILT_IN_VIEWS.find((v) => v.id === activeViewId);
      if (!view || view.statuses === null) return new Set(DEFAULT_STATUSES);
      return new Set(view.statuses);
    }
    // Custom view — apply filter_spec.statuses if present
    const customView = customViews.find((v) => v.id === activeViewId);
    if (customView?.filter_spec.statuses) {
      return new Set(customView.filter_spec.statuses as IngestionEventStatus[]);
    }
    return new Set(DEFAULT_STATUSES);
  }, [activeViewId, defaultStatuses, customViews]);

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

  const handleDeselectIds = useCallback((ids: string[]) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        next.delete(id);
      }
      return next;
    });
  }, []);

  const selectedIdsArray = useMemo(() => Array.from(selectedIds), [selectedIds]);

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

  // Search — local state drives debounced q for API
  const urlQ = searchParams.get("q") ?? "";
  const [searchInputValue, setSearchInputValue] = useState(urlQ);
  const [debouncedQ, setDebouncedQ] = useState(urlQ);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchInputValue(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        setDebouncedQ(value);
        setSearchParams((prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set("q", value);
          else next.delete("q");
          return next;
        });
      }, 300);
    },
    [setSearchParams],
  );

  // Clean up debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // Channel filter — read from URL state ("channels" param, comma-separated)
  const urlChannels = searchParams.get("channels") ?? "";
  const activeChannels: string[] = useMemo(
    () => urlChannels ? urlChannels.split(",").map((c) => c.trim()).filter(Boolean) : [],
    [urlChannels],
  );

  const handleChannelRemove = useCallback(
    (channel: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        const remaining = activeChannels.filter((c) => c !== channel);
        if (remaining.length > 0) next.set("channels", remaining.join(","));
        else next.delete("channels");
        return next;
      });
    },
    [activeChannels, setSearchParams],
  );

  // ---------------------------------------------------------------------------
  // Custom saved views — apply filter_spec, save, delete
  // All handlers are defined here so that search/channel state setters are
  // already declared above.
  // ---------------------------------------------------------------------------

  // Apply a custom view's filter_spec to the toolbar state
  const applyCustomViewFilterSpec = useCallback(
    (spec: TimelineSavedViewFilterSpec) => {
      if (spec.statuses) {
        setEnabledStatuses(new Set(spec.statuses as IngestionEventStatus[]));
      }
      if (spec.range && (["1h", "24h", "7d"] as string[]).includes(spec.range)) {
        setRange(spec.range as IngestionRange);
      }
      if (typeof spec.q === "string") {
        setSearchInputValue(spec.q);
        setDebouncedQ(spec.q);
      }
      // Batch all URL param changes into a single setSearchParams call
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (spec.range && (["1h", "24h", "7d"] as string[]).includes(spec.range)) {
          next.set("range", spec.range);
        }
        if (typeof spec.q === "string") {
          if (spec.q) next.set("q", spec.q);
          else next.delete("q");
        }
        if (typeof spec.channels === "string") {
          if (spec.channels) next.set("channels", spec.channels);
          else next.delete("channels");
        }
        return next;
      });
    },
    [setSearchParams, setEnabledStatuses, setRange, setSearchInputValue, setDebouncedQ],
  );

  // When a custom view is selected, apply its filter_spec
  useEffect(() => {
    if (isBuiltInViewId(activeViewId)) return;
    const customView = customViews.find((v) => v.id === activeViewId);
    if (customView) {
      applyCustomViewFilterSpec(customView.filter_spec);
    }
  }, [activeViewId, customViews, applyCustomViewFilterSpec]);

  const handleSaveView = useCallback(() => {
    setSaveViewName("");
    setSaveDialogOpen(true);
  }, []);

  const handleSaveViewConfirm = useCallback(() => {
    const trimmedName = saveViewName.trim();
    if (!trimmedName || createSavedView.isPending) return;

    const spec: TimelineSavedViewFilterSpec = {
      statuses: [...enabledStatuses],
      range,
      ...(debouncedQ ? { q: debouncedQ } : {}),
      ...(activeChannels.length > 0 ? { channels: activeChannels.join(",") } : {}),
    };

    createSavedView.mutate(
      { name: trimmedName, filter_spec: spec },
      {
        onSuccess: (created) => {
          setSaveDialogOpen(false);
          setSaveViewName("");
          setActiveViewId(created.id);
          persistView(created.id);
          toast.success(`Saved view "${created.name}" created`);
        },
        onError: (err) => {
          toast.error(err instanceof Error ? err.message : "Failed to save view");
        },
      },
    );
  }, [saveViewName, enabledStatuses, range, debouncedQ, activeChannels, createSavedView]);

  const handleDeleteCustomView = useCallback(
    (id: string) => {
      deleteSavedView.mutate(id, {
        onSuccess: () => {
          // If the deleted view was active, fall back to "all"
          if (activeViewId === id) {
            setActiveViewId("all");
            persistView("all");
          }
          toast.success("Saved view deleted");
        },
        onError: (err) => {
          toast.error(err instanceof Error ? err.message : "Failed to delete view");
        },
      });
    },
    [activeViewId, deleteSavedView],
  );

  // Compute ISO-8601 bounds from the range picker selection.
  // The rollup band uses these to scope its aggregate; the events list is
  // not time-bounded (it fetches newest-first and the user loads more pages).
  const rangeWindow = useMemo((): { from: string; to: string } => {
    const now = new Date();
    const to = now.toISOString();
    const hoursBack = range === "1h" ? 1 : range === "7d" ? 7 * 24 : 24;
    const from = new Date(now.getTime() - hoursBack * 60 * 60 * 1000).toISOString();
    return { from, to };
  }, [range]);

  // Events query — pass q, channels (CSV), and statuses (CSV) from toolbar state.
  // Statuses are pushed server-side so pages aren't dominated by hidden rows
  // (e.g. skipped home_assistant sensor spam); omitted when every status is
  // enabled, which is equivalent to no filter. Serialized in ALL_STATUSES
  // order so the query key is stable regardless of toggle order.
  const statusesCsv = useMemo(() => {
    if (enabledStatuses.size >= ALL_STATUSES.length) return "";
    return ALL_STATUSES.filter((s) => enabledStatuses.has(s)).join(",");
  }, [enabledStatuses]);

  // Spend view activates cost sort (core_126): sort by cost_usd DESC NULLS LAST.
  const activeSort = activeViewId === "spend" ? ("cost" as const) : undefined;

  const eventsFilters = useMemo(() => ({
    limit: PAGE_SIZE,
    ...(debouncedQ ? { q: debouncedQ } : {}),
    ...(activeChannels.length > 0 ? { channels: activeChannels.join(",") } : {}),
    ...(statusesCsv ? { statuses: statusesCsv } : {}),
    // Only apply a lower bound on received_at so the 30 s refetch can pick up
    // events that arrived after the initial load.  Including an upper bound
    // (rangeWindow.to) would freeze the query at the moment the range changed,
    // causing the refetch to silently miss new events.
    from: rangeWindow.from,
    ...(activeSort ? { sort: activeSort } : {}),
  }), [debouncedQ, activeChannels, statusesCsv, rangeWindow.from, activeSort]);

  const {
    data: infiniteData,
    isLoading,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useIngestionEvents(eventsFilters, { enabled: isActive });

  // Window rollup — fires with the same filter shape plus the active range window.
  const rollupStatuses = useMemo(() => [...enabledStatuses].join(","), [enabledStatuses]);
  const rollupChannels = useMemo(() => activeChannels.join(","), [activeChannels]);

  const {
    data: rollupData,
    isLoading: rollupLoading,
  } = useIngestionWindowRollup(
    {
      from: rangeWindow.from,
      to: rangeWindow.to,
      ...(debouncedQ ? { q: debouncedQ } : {}),
      ...(rollupChannels ? { channels: rollupChannels } : {}),
      ...(rollupStatuses ? { statuses: rollupStatuses } : {}),
    },
    { enabled: isActive },
  );

  const rawEvents = useMemo(
    () => infiniteData?.pages.flatMap((page) => page.data) ?? [],
    [infiniteData?.pages],
  );

  // Report the most-recent event's received_at to the parent for live-status.
  // We use the first page's first event (newest-first ordering) so the badge
  // reflects true pipeline freshness rather than the client-side filter view.
  const latestReceivedAt = infiniteData?.pages[0]?.data[0]?.received_at ?? null;
  useEffect(() => {
    if (!isLoading && onFreshnessChange) {
      onFreshnessChange(latestReceivedAt);
    }
  }, [latestReceivedAt, isLoading, onFreshnessChange]);

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
        searchQuery={searchInputValue}
        onSearchChange={handleSearchChange}
        activeChannels={activeChannels}
        onChannelRemove={handleChannelRemove}
        customViews={customViewsLoading ? undefined : customViews}
        customViewsLoading={customViewsLoading}
        onSaveView={handleSaveView}
        onDeleteCustomView={handleDeleteCustomView}
      />

      {/* Save view dialog */}
      <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
        <DialogContent data-testid="save-view-dialog">
          <DialogHeader>
            <DialogTitle>Save current view</DialogTitle>
            <DialogDescription>
              Name this filter combination to restore it later.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={saveViewName}
              onChange={(e) => setSaveViewName(e.target.value)}
              placeholder="View name…"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSaveViewConfirm();
              }}
              data-testid="save-view-name-input"
              autoFocus
              maxLength={100}
            />
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSaveDialogOpen(false)}
              data-testid="save-view-cancel"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSaveViewConfirm}
              disabled={!saveViewName.trim() || createSavedView.isPending}
              data-testid="save-view-confirm"
            >
              {createSavedView.isPending ? (
                <Loader2 className="size-3 mr-1 animate-spin" />
              ) : null}
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk action bar */}
      <BulkActionBar
        selectedCount={selectedIds.size}
        selectedIds={selectedIdsArray}
        onClearSelection={handleClearSelection}
        onDeselectIds={handleDeselectIds}
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

      {/* Footer rollup band — aggregate counts for the active filter window */}
      <FooterRollupBand
        events={rollupData?.events}
        sessions={rollupData?.sessions}
        cost={rollupData?.cost}
        isLoading={rollupLoading}
      />

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
