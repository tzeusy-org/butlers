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
import {
  AlertTriangle,
  ArrowDown,
  BookmarkPlus,
  Copy,
  Loader2,
  Plus,
  RotateCw,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DisclosureRow } from "@/components/ui/DisclosureRow";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useIngestionEvents,
  useIngestionEventsHistogram,
  useIngestionWindowRollup,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useUpdateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type {
  ConnectorSummary,
  IngestionEventSummary,
  IngestionEventStatus,
  IngestionHistogramBucket,
  IngestionHistogramBucketSize,
  TimelineSavedViewEntry,
  TimelineSavedViewFilterSpec,
} from "@/api/index.ts";
import { ApiError, bulkRetryEvents, replayIngestionEvent } from "@/api/index.ts";
import { Time } from "@/components/ui/time";
import { RowStatus, ROW_STATUS_WORDS } from "./StatusBadge";
import { isBulkEligible, bulkIneligibleReason } from "./bulkEligibility";
import { HourFlameStrip } from "./timeline/HourFlameStrip";
import { DispatchTicksCell } from "./timeline/DispatchTicksCell";
import { EventDrawer } from "./timeline/EventDrawer";
import { useEventDrawerState } from "./timeline/useEventDrawerState";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { formatCostUsdPrecise } from "@/lib/format-cost";
import { SourceDegradedNote } from "@/components/ui/query-boundary";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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


function hourGroupKey(receivedAt: string | null): string {
  if (!receivedAt) return "unknown";
  try {
    return receivedAt.slice(0, 13); // "2026-05-17T14"
  } catch {
    return "unknown";
  }
}

// bu-4utdw.7: the hour strip's histogram request must stay under the
// backend's bucket-count guardrail (1m capped at 48h; 5m up to 10 days).
// Only the 7d range exceeds the 1m cap, so it alone drops to 5m granularity.
function histogramBucketForRange(range: IngestionRange): IngestionHistogramBucketSize {
  return range === "7d" ? "5m" : "1m";
}

function histogramBucketMinutes(bucket: IngestionHistogramBucketSize): number {
  switch (bucket) {
    case "1h":
      return 60;
    case "5m":
      return 5;
    case "1m":
      return 1;
  }
}

// ---------------------------------------------------------------------------
// §2.8 Saved Views
// ---------------------------------------------------------------------------

const SAVED_VIEWS_STORAGE_KEY = "ingestion-saved-views";

/** Built-in view IDs. */
type BuiltInViewId = "all" | "errors" | "spend";

/**
 * Active view ID — either a built-in preset or a custom view's UUID string.
 * Custom UUIDs always contain a hyphen, built-in IDs never do; no collision.
 */
type ViewId = BuiltInViewId | string;

interface SavedView {
  id: BuiltInViewId;
  label: string;
  statuses: IngestionEventStatus[];
}

const BUILT_IN_VIEWS: SavedView[] = [
  {
    id: "all",
    label: "All",
    // All real traffic — noise statuses ("skipped" skip-triaged events,
    // "filtered" rule drops) stay hidden until toggled on via the status chips.
    statuses: ["ingested", "error", "failed", "replay_pending", "replay_complete", "replay_failed"],
  },
  {
    id: "errors",
    label: "Errors only",
    statuses: ["error", "failed", "replay_pending", "replay_failed"],
  },
  {
    id: "spend",
    // Lowercase — declares itself as a sort, not a filter preset (paired
    // with the down-arrow-and-$ sort indicator rendered next to the label).
    label: "spend",
    // Same statuses as "All" — cost sort applies to dispatched events.
    // Enabled by core_126: cost_usd is now denormalized onto ingestion_events.
    statuses: ["ingested", "error", "failed", "replay_pending", "replay_complete", "replay_failed"],
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
  "failed",
  "replay_pending",
  "replay_complete",
  "replay_failed",
];

// Status filter chips use the exact badge vocabulary (ROW_STATUS_WORDS,
// imported from StatusBadge.tsx) rather than a second, driftable word list —
// "ok"/"replay"/"replayed" from the old chip labels are gone. ("failed" here
// is the real routing-failure status added by bu-lkzsf.1, not the retired
// chip shorthand for replay_failed.)

// "skipped" (stored but not dispatched — e.g. home_assistant sensor streams)
// and "filtered" are noise statuses, hidden by default.
const DEFAULT_STATUSES = ALL_STATUSES.filter((s) => s !== "filtered" && s !== "skipped");

// ---------------------------------------------------------------------------
// Toolbar — range picker, search input, saved views, channel chips, status filter
// ---------------------------------------------------------------------------

export type IngestionRange = "1h" | "24h" | "7d";

const RANGE_OPTIONS: { id: IngestionRange; label: string }[] = [
  { id: "1h", label: "1h" },
  { id: "24h", label: "24h" },
  { id: "7d", label: "7d" },
];

/** One selectable entry in the "+ channel" adder popover. */
export interface ChannelOption {
  channel: string;
  /** Cheap count sourced from the already-fetched connector summaries (today's
   *  total, not window-scoped — no per-chip request is made for this). Null
   *  when unavailable. */
  count: number | null;
}

interface ToolbarProps {
  range: IngestionRange;
  onRangeChange: (r: IngestionRange) => void;
  activeViewId: ViewId;
  onViewSelect: (v: ViewId) => void;
  /** True when the active filter state (statuses/range/search/channels) has
   *  diverged from whatever the active saved view defines. */
  isViewModified: boolean;
  /** Persist the current filter state onto an existing custom view. */
  onUpdateView: (id: string) => void;
  enabledStatuses: Set<IngestionEventStatus>;
  onStatusToggle: (s: IngestionEventStatus) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  activeChannels: string[];
  /** Add or remove a channel from the active filter (toggle). */
  onChannelToggle: (channel: string) => void;
  /** Channels available in the "+ channel" adder popover. */
  channelOptions: ChannelOption[];
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
  isViewModified,
  onUpdateView,
  enabledStatuses,
  onStatusToggle,
  searchQuery,
  onSearchChange,
  activeChannels,
  onChannelToggle,
  channelOptions,
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
          {BUILT_IN_VIEWS.map((view) => {
            const active = activeViewId === view.id;
            const modified = active && isViewModified;
            return (
              <button
                key={view.id}
                type="button"
                onClick={() => onViewSelect(view.id)}
                className={[
                  "relative rounded px-2.5 py-1 font-mono text-[11px] transition-colors cursor-pointer",
                  active
                    ? "bg-foreground/10 text-foreground border border-border"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                ].join(" ")}
                title={modified ? "Filters differ from this view. Click to re-apply it" : undefined}
                data-view={view.id}
                aria-pressed={active}
              >
                <span className="inline-flex items-center gap-1">
                  {view.label}
                  {view.id === "spend" && (
                    <span className="inline-flex items-center text-current" aria-hidden>
                      <ArrowDown className="size-2.5" />$
                    </span>
                  )}
                </span>
                {modified && (
                  <span
                    className="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-[var(--amber)]"
                    data-testid={`view-modified-dot-${view.id}`}
                    aria-label="Filters differ from this saved view"
                  />
                )}
              </button>
            );
          })}

          {/* Separator between built-ins and custom views */}
          {(customViewsLoading || (customViews && customViews.length > 0)) && (
            <div className="w-px h-4 bg-border/60 mx-0.5" aria-hidden />
          )}

          {/* Custom views from backend */}
          {customViewsLoading && (
            <Skeleton className="h-6 w-16 rounded" data-testid="custom-views-loading" />
          )}
          {!customViewsLoading && customViews?.map((view) => {
            const active = activeViewId === view.id;
            const modified = active && isViewModified;
            return (
              <div key={view.id} className="flex items-center gap-1">
                <div className="relative flex items-center group">
                  <button
                    type="button"
                    onClick={() => onViewSelect(view.id)}
                    className={[
                      "relative rounded px-2.5 py-1 font-mono text-[11px] transition-colors pr-6",
                      active
                        ? "bg-foreground/10 text-foreground border border-border"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    ].join(" ")}
                    data-view={view.id}
                    data-testid={`custom-view-${view.id}`}
                    aria-pressed={active}
                    title={modified ? `Filters differ from "${view.name}". Click to re-apply it` : view.name}
                  >
                    {view.name}
                    {modified && (
                      <span
                        className="absolute top-1 right-2 size-1.5 rounded-full bg-[var(--amber)]"
                        data-testid={`view-modified-dot-${view.id}`}
                        aria-label="Filters differ from this saved view"
                      />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteCustomView(view.id);
                    }}
                    className={[
                      "absolute right-0.5 p-0.5 rounded transition-colors",
                      "text-muted-foreground/60 hover:text-destructive",
                      // Visible (not opacity-0) by default so touch users — who
                      // never get a hover state — can still discover and tap
                      // this; hover/focus just brightens it further.
                      "opacity-60 group-hover:opacity-100 focus:opacity-100 focus-visible:opacity-100",
                    ].join(" ")}
                    aria-label={`Delete saved view: ${view.name}`}
                    data-testid={`custom-view-delete-${view.id}`}
                    title={`Delete "${view.name}"`}
                  >
                    <Trash2 className="size-2.5" aria-hidden />
                  </button>
                </div>
                {modified && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onUpdateView(view.id);
                    }}
                    className={[
                      "rounded px-1.5 py-1 font-mono text-[10px] transition-colors",
                      "text-[var(--amber-text)] hover:bg-[var(--amber)]/10",
                    ].join(" ")}
                    data-testid={`update-view-${view.id}`}
                    title={`Update "${view.name}" with the current filters`}
                  >
                    update view
                  </button>
                )}
              </div>
            );
          })}

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
            save view
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
                {ROW_STATUS_WORDS[status]}
              </button>
            );
          })}
        </div>
      </div>

      {/* Channel filter chips row — active channel chips plus the "+ channel" adder */}
      {(activeChannels.length > 0 || channelOptions.length > 0) && (
        <div className="flex items-center gap-1.5 flex-wrap pb-2" data-testid="channel-chips">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            channels:
          </span>
          {activeChannels.map((channel) => (
            <button
              key={channel}
              type="button"
              onClick={() => onChannelToggle(channel)}
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
          {channelOptions.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className={[
                    "inline-flex items-center gap-1 rounded-full px-2 py-0.5",
                    "font-mono text-[11px] border border-dashed border-border/60 text-muted-foreground",
                    "hover:bg-muted hover:border-border hover:text-foreground transition-colors",
                  ].join(" ")}
                  aria-label="Add channel filter"
                  data-testid="channel-adder-button"
                >
                  <Plus className="size-2.5" aria-hidden />
                  channel
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" data-testid="channel-adder-menu">
                {channelOptions.map(({ channel, count }) => (
                  <DropdownMenuCheckboxItem
                    key={channel}
                    checked={activeChannels.includes(channel)}
                    onCheckedChange={() => onChannelToggle(channel)}
                    onSelect={(e) => e.preventDefault()}
                    className="font-mono text-[11px] gap-2"
                    data-testid={`channel-option-${channel}`}
                  >
                    <span className="flex-1 truncate">{channel}</span>
                    {count !== null && (
                      <span className="text-muted-foreground tabular-nums">{count}</span>
                    )}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
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
  /** Eligible event ids currently visible under the active filters (capped by the caller). */
  visibleEligibleIds: string[];
  onSelectAllVisible: (ids: string[]) => void;
}

function BulkActionBar({
  selectedCount,
  selectedIds,
  onClearSelection,
  onDeselectIds,
  visibleEligibleIds,
  onSelectAllVisible,
}: BulkActionBarProps) {
  const [isRetrying, setIsRetrying] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [copySuccess, setCopySuccess] = useState(false);
  // Populated from the 409 response's `unsafe_events` detail so "deselect
  // ineligible" can remove exactly the offending ids in one click, instead
  // of forcing the owner to hunt for email/replay-unsafe rows by hand.
  const [ineligibleIds, setIneligibleIds] = useState<string[]>([]);

  if (selectedCount === 0) return null;

  const overLimit = selectedCount > MAX_BULK_RETRY_BATCH;
  const disabled = overLimit || isRetrying;

  async function handleReplayAll() {
    if (disabled) return;
    setIsRetrying(true);
    setErrorMsg(null);
    setIneligibleIds([]);
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
      // 409 means the batch contains email or replay-unsafe events — surface a clear message
      // and let the owner deselect exactly the offending ids in one click.
      if (err instanceof ApiError && err.status === 409) {
        const msg = "Selection contains email or replay-unsafe events. Remove them and retry";
        setErrorMsg(msg);
        toast.error(msg);
        const detail = err.detail as { unsafe_events?: { id: string }[] } | undefined;
        setIneligibleIds(detail?.unsafe_events?.map((u) => u.id) ?? []);
      } else {
        const msg = err instanceof Error ? err.message : "Bulk replay failed";
        setErrorMsg(msg);
      }
    } finally {
      setIsRetrying(false);
    }
  }

  function handleDeselectIneligible() {
    onDeselectIds(ineligibleIds);
    setIneligibleIds([]);
    setErrorMsg(null);
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
      {visibleEligibleIds.some((id) => !selectedIds.includes(id)) && (
        <Button
          variant="ghost"
          size="sm"
          className="font-mono text-[11px] h-7 text-muted-foreground"
          onClick={() => onSelectAllVisible(visibleEligibleIds)}
          title={`Select all ${visibleEligibleIds.length} eligible visible event${visibleEligibleIds.length !== 1 ? "s" : ""} (max ${MAX_BULK_RETRY_BATCH})`}
          data-testid="bulk-select-all-visible-button"
        >
          Select all visible ({visibleEligibleIds.length})
        </Button>
      )}
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
        {copySuccess ? "Copied" : "Copy IDs"}
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
        <p className="font-mono text-[10px] text-[var(--amber-text)] ml-auto" data-testid="bulk-overlimit-msg">
          Max {MAX_BULK_RETRY_BATCH} events per batch
        </p>
      )}
      {errorMsg && (
        <div className="flex items-center gap-2 ml-auto">
          <p className="font-mono text-[10px] text-destructive" data-testid="bulk-error-msg">
            {errorMsg}
          </p>
          {ineligibleIds.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="font-mono text-[10px] h-6 px-1.5 text-destructive"
              onClick={handleDeselectIneligible}
              title="Remove the email/replay-unsafe events from the selection"
              data-testid="bulk-deselect-ineligible-button"
            >
              Deselect ineligible ({ineligibleIds.length})
            </Button>
          )}
        </div>
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
//
// bu-4utdw.4 recompose: time takes the left edge (leftmost, mono HH:mm:ss via
// the shared Time primitive); the 8-char id column is dropped (id stays
// available via the row's title attr and the drawer's copy affordance);
// token in/out columns move to the drawer only. The selection checkbox
// column is always in the grid (for alignment) but its content is visually
// demoted — see `showCheckboxColumn` on LedgerRow / LedgerColumnHeaders.
// bu-4utdw.8: a dispatch-ticks column sits between status and cost — see
// DispatchTicksCell.
// ---------------------------------------------------------------------------

const LEDGER_GRID_COLUMNS = "20px 72px 150px 1fr 112px 120px 128px 28px"

// ---------------------------------------------------------------------------
// LedgerRow — one row in the event ledger
// ---------------------------------------------------------------------------

interface LedgerRowProps {
  event: IngestionEventSummary;
  isExpanded: boolean;
  isSelected: boolean;
  /** Selection column is always visible (errors view active, or ≥1 row already selected). */
  showCheckboxColumn: boolean;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
  /** Add this row's channel to the active channel filter (click-to-filter). */
  onChannelClick: (channel: string) => void;
}

function LedgerRow({
  event,
  isExpanded,
  isSelected,
  showCheckboxColumn,
  onToggleExpand,
  onToggleSelect,
  onOptimisticUpdate,
  onChannelClick,
}: LedgerRowProps) {
  const eligible = isBulkEligible(event.status);
  const ineligibleReason = bulkIneligibleReason(event.status);
  // bu-4utdw.3: tokens/cost/sender are now list-provided fields (one grouped
  // fan-out for the whole page server-side) — no per-row hook mounts here.
  const resolvedName = event.sender_display ?? event.source_sender_identity ?? null;
  const unpricedSessionCount = event.unpriced_session_count ?? 0;
  const costEvidence = formatCostEvidence(event.cost_usd, unpricedSessionCount);

  const [isReplaying, setIsReplaying] = useState(false);

  // bu-4utdw.4: every row expands now — filtered/error rows used to be
  // excluded (their detail was tooltip-only). The drawer already renders an
  // honest reason for those statuses (EventDrawer's emptySessionsReason).
  // "failed" (routing failure after ingestion, bu-lkzsf.1) carries the same
  // error_detail shape as "error" and gets the same inline treatment.
  const reasonText =
    event.status === "filtered"
      ? event.filter_reason
      : event.status === "error" || event.status === "failed"
        ? event.error_detail
        : null;

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

  function handleRowClick(e: React.MouseEvent<HTMLDivElement>) {
    // Shift-click enters selection mode without opening the drawer; a plain
    // click anywhere else in the row is a MOUSE-ONLY convenience that
    // mirrors the sender cell's real DisclosureRow trigger below (bigger
    // click target, same action) — it carries no independent ARIA role
    // itself, so it can safely sit around several genuinely-interactive
    // children (checkbox, channel filter, sender disclosure, replay)
    // without triggering "nested interactive controls" (a real regression
    // the previous single-role-on-the-whole-row version of this component
    // had, caught by the real-page axe suite this bead adds).
    if (e.shiftKey && eligible) {
      onToggleSelect();
      return;
    }
    onToggleExpand();
  }

  const senderLabel = resolvedName ?? "Unknown sender";
  const senderDisclosureLabel = `${senderLabel}${reasonText ? `, ${reasonText}` : ""}, ${
    isExpanded ? "collapse" : "expand"
  } event details`;

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- not itself an ARIA widget (no role); this is a mouse-only "click anywhere in the row" convenience mirroring the sender cell's DisclosureRow below, which is the row's real keyboard-accessible trigger. Giving the WHOLE row a widget role instead (tried first) fails axe's nested-interactive check because the row also contains independently-focusable cells (checkbox, channel filter, replay) — a real ARIA violation, not a false positive.
    <div
      className={[
        "grid items-center gap-x-3 px-3 py-2 border-b border-border/50 text-[13px] transition-colors cursor-pointer group",
        event.status === "filtered" ? "opacity-60" : "",
        isExpanded ? "bg-muted/20" : "hover:bg-muted/10",
      ].join(" ")}
      style={{ gridTemplateColumns: LEDGER_GRID_COLUMNS }}
      onClick={handleRowClick}
      title={event.id}
      data-testid="ledger-row"
      data-event-id={event.id}
    >
      {/* Checkbox — hidden by default (opacity-0), revealed on row hover/focus
          or whenever selection mode is active (errors view, or ≥1 selected).
          A direct grid child (not wrapped) so it's the row's first element
          child, matching the row-selection convention used elsewhere. */}
      <input
        type="checkbox"
        checked={isSelected}
        disabled={!eligible}
        onChange={() => {
          if (eligible) onToggleSelect();
        }}
        onClick={(e) => e.stopPropagation()}
        tabIndex={eligible ? 0 : -1}
        className={[
          "size-3.5 rounded border-border/60 accent-foreground cursor-pointer transition-opacity justify-self-start",
          "disabled:cursor-not-allowed disabled:opacity-30",
          showCheckboxColumn ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
        ].join(" ")}
        title={eligible ? undefined : (ineligibleReason ?? undefined)}
        data-testid={eligible ? "row-checkbox" : "row-checkbox-disabled"}
        aria-disabled={eligible ? undefined : true}
        aria-label={eligible ? "Select event" : (ineligibleReason ?? "Ineligible for bulk replay")}
      />

      {/* Time — leftmost visible column, mono HH:mm:ss via the shared Time primitive */}
      {event.received_at ? (
        <Time
          value={event.received_at}
          mode="absolute"
          precision="time-seconds"
          className="font-mono tabular-nums text-[11px] text-muted-foreground"
        />
      ) : (
        <span className="font-mono tabular-nums text-[11px] text-muted-foreground">—</span>
      )}

      {/* Channel glyph + name — click-to-filter by this channel */}
      <button
        type="button"
        className={[
          "flex items-center gap-1.5 min-w-0 rounded transition-opacity",
          event.source_channel ? "hover:opacity-70 cursor-pointer" : "cursor-default",
        ].join(" ")}
        onClick={(e) => {
          e.stopPropagation();
          if (event.source_channel) onChannelClick(event.source_channel);
        }}
        disabled={!event.source_channel}
        title={event.source_channel ? `Filter by ${event.source_channel}` : undefined}
        data-testid="row-channel-filter"
      >
        <span
          className="inline-flex size-5 items-center justify-center rounded text-[10px] font-medium text-white shrink-0"
          style={{ backgroundColor: "var(--muted-foreground)" }}
          aria-hidden="true"
        >
          {(event.source_channel ?? "?").charAt(0).toUpperCase()}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground truncate">
          {event.source_channel ?? "—"}
        </span>
      </button>

      {/* Sender + inline filter/error reason (no more tooltip-only pattern).
          bu-86c4c.16: this is the row's REAL keyboard-accessible disclosure
          trigger — DisclosureRow supplies role="button", Enter+Space
          activation, and aria-expanded/aria-controls (JARVIS audit move 11,
          critical finding). It wraps only static text (no nested focusable
          descendants), so it — unlike a whole-row role — passes axe's
          nested-interactive check cleanly. stopPropagation keeps the outer
          row's own mouse-convenience onClick from double-firing the toggle. */}
      <DisclosureRow
        expanded={isExpanded}
        onToggle={onToggleExpand}
        onClick={(e) => e.stopPropagation()}
        controlsId={isExpanded ? `event-drawer-${event.id}` : undefined}
        aria-label={senderDisclosureLabel}
        title={[resolvedName, reasonText].filter(Boolean).join(" — ") || undefined}
        className="min-w-0 pr-2 flex items-baseline gap-2 rounded-sm"
        data-testid="ledger-row-trigger"
        data-event-id={event.id}
        // The drawer is URL-backed as /ingestion?event=<id>; this maps to
        // useIngestionEventDetail's exact cache key via the shared registry.
        prefetchTo={`/ingestion?event=${encodeURIComponent(event.id)}`}
      >
        <span
          className="truncate font-serif text-[13px] leading-[1.5] shrink-0 max-w-[55%]"
          aria-hidden="true"
        >
          {resolvedName ?? "—"}
        </span>
        {reasonText && (
          <span
            aria-hidden="true"
            className={[
              "truncate min-w-0 font-mono text-[11px]",
              event.status === "error" || event.status === "failed"
                ? "text-destructive"
                : "text-muted-foreground",
            ].join(" ")}
          >
            {reasonText}
          </span>
        )}
      </DisclosureRow>

      {/* Status — quiet dot + word, never a filled pill in rows */}
      <RowStatus status={event.status} />

      {/* Dispatch ticks — per-butler session micro-flame (bu-4utdw.8) */}
      <DispatchTicksCell
        sessions={event.sessions}
        sessionCount={event.session_count}
        onOpenDrawer={onToggleExpand}
      />

      {/* Cost */}
      <span
        className="text-right tabular-nums font-mono text-[11px]"
        title={unpricedSessionCount > 0 ? `${unpricedSessionCount} session cost unavailable` : undefined}
      >
        {costEvidence}
      </span>

      {/* Replay / chevron — chevron on every row now */}
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- not itself interactive; onClick only swallows bubbling so the replay button's click (which already stopPropagation()s itself) never double-fires the row's expand toggle. */}
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
        ) : (
          <span aria-hidden="true" className="font-mono text-[10px] text-muted-foreground select-none">
            {isExpanded ? "▲" : "▼"}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HourGroup — header row + events for one hour bucket
// ---------------------------------------------------------------------------

interface HourGroupProps {
  hourKey: string;
  events: IngestionEventSummary[];
  drawerEventId: string | null;
  selectedIds: Set<string>;
  showCheckboxColumn: boolean;
  onOpenDrawer: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onOptimisticUpdate: (id: string, newStatus: IngestionEventStatus) => void;
  drawerEvent: IngestionEventSummary | null;
  onCloseDrawer: () => void;
  onChannelClick: (channel: string) => void;
  /** Histogram buckets whose ts falls within this hour — the strip and header counts' data source. */
  histogramBuckets: IngestionHistogramBucket[];
  /** Bucket granularity in minutes (1 for "1m", 5 for "5m"), matches the active histogram request. */
  bucketMinutes: number;
  /** True until the histogram query's first response for the active window has arrived. */
  histogramLoading: boolean;
  /** True when the histogram reader failed and its totals cannot be trusted. */
  histogramError: boolean;
  onRetryHistogram: () => void;
  /** Minute has no loaded ledger row in view — scope the range/filters to it (URL-backed). */
  onScopeToMinute: (minuteIso: string, bucketMinutes: number) => void;
}

function HourGroup({
  hourKey,
  events,
  drawerEventId,
  selectedIds,
  showCheckboxColumn,
  onOpenDrawer,
  onToggleSelect,
  onOptimisticUpdate,
  drawerEvent,
  onCloseDrawer,
  onChannelClick,
  histogramBuckets,
  bucketMinutes,
  histogramLoading,
  histogramError,
  onRetryHistogram,
  onScopeToMinute,
}: HourGroupProps) {
  const hourStart = hourKey !== "unknown" ? hourKey + ":00:00Z" : "";

  // Honest hour totals: sourced from the histogram (same filters as the
  // ledger), not `events.length` — the loaded-page count understates hours
  // cut by a page boundary.
  const hourTotals = useMemo(() => {
    let total = 0;
    let errors = 0;
    let replays = 0;
    for (const b of histogramBuckets) {
      const c = b.counts;
      // "failed" (routing failure after ingestion, bu-lkzsf.1) counts as an
      // error for this honest-total header, same severity class as "error",
      // just a later pipeline stage. This is a deliberate SUBSET of the
      // backend's incident grouping (ingestion_connectors.py::_INCIDENT_STATUSES
      // = {error, failed, replay_failed}), not a full mirror: replay_failed
      // stays in the separate replay lane (counted below as a replay), never
      // folded into the error total.
      total +=
        c.ingested +
        c.skipped +
        c.filtered +
        c.error +
        c.failed +
        c.replay_pending +
        c.replay_complete +
        c.replay_failed;
      errors += c.error + c.failed;
      replays += c.replay_pending + c.replay_complete + c.replay_failed;
    }
    return { total, errors, replays };
  }, [histogramBuckets]);

  // Click a minute: scroll to it if a loaded ledger row falls within it,
  // otherwise scope the range/filters to that exact minute (URL-backed).
  const handleMinuteClick = useCallback(
    (minuteIso: string) => {
      const minuteMs = new Date(minuteIso).getTime();
      if (isNaN(minuteMs)) return;
      const bucketMs = bucketMinutes * 60_000;
      const match = events.find((e) => {
        if (!e.received_at) return false;
        const ts = new Date(e.received_at).getTime();
        return ts >= minuteMs && ts < minuteMs + bucketMs;
      });
      if (match) {
        document
          .querySelector(`[data-event-id="${match.id}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      } else {
        onScopeToMinute(minuteIso, bucketMinutes);
      }
    },
    [events, bucketMinutes, onScopeToMinute],
  );

  return (
    <div data-testid="hour-group" data-hour-key={hourKey}>
      {/* Hour group header */}
      <div className="flex items-center gap-3 px-3 py-1.5 bg-muted/10 border-b border-border/50">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {hourStart ? <Time value={hourStart} mode="absolute" precision="hour" compact /> : "Unknown time"}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground" data-testid="hour-group-summary">
          {histogramError ? (
            "histogram unavailable"
          ) : histogramLoading && histogramBuckets.length === 0 ? (
            "…"
          ) : (
            <>
              {hourTotals.total} {hourTotals.total === 1 ? "event" : "events"}
              {hourTotals.errors > 0 && (
                <>
                  {" "}
                  ·{" "}
                  <span className="text-destructive">
                    {hourTotals.errors} {hourTotals.errors === 1 ? "error" : "errors"}
                  </span>
                </>
              )}
              {hourTotals.replays > 0 && (
                <>
                  {" "}
                  ·{" "}
                  <span className="text-blue-600">
                    {hourTotals.replays} {hourTotals.replays === 1 ? "replay" : "replays"}
                  </span>
                </>
              )}
            </>
          )}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {histogramError ? (
            <SourceDegradedNote
              label="hour histogram"
              detail="unavailable"
              onRetry={onRetryHistogram}
              testId="hour-histogram-unavailable"
            />
          ) : (
            <HourFlameStrip
              hourStart={hourStart}
              buckets={histogramBuckets}
              bucketMinutes={bucketMinutes}
              height={16}
              onMinuteClick={handleMinuteClick}
              data-testid="hour-flame-strip"
            />
          )}
        </div>
      </div>

      {/* Event rows */}
      {events.map((event) => {
        // bu-86c4c.16: Escape (or the close button) inside the drawer should
        // return focus to the row that opened it — the real keyboard
        // equivalent of "the drawer went away, you're back where you were".
        // Looked up by data-event-id (via a plain attribute filter, not a
        // CSS-selector-escaped query — event ids are opaque strings and
        // needn't round-trip through selector syntax) rather than a ref map,
        // since rows mount/unmount freely as the ledger scrolls/paginates.
        // The trigger itself never unmounts when only the drawer toggles, so
        // this runs synchronously — no requestAnimationFrame/rAF-after-
        // unmount race to worry about.
        function closeAndReturnFocus() {
          onCloseDrawer();
          const trigger = Array.from(
            document.querySelectorAll<HTMLElement>('[data-testid="ledger-row-trigger"]'),
          ).find((el) => el.dataset.eventId === event.id);
          trigger?.focus();
        }

        return (
          <div key={event.id}>
            <LedgerRow
              event={event}
              isExpanded={drawerEventId === event.id}
              isSelected={selectedIds.has(event.id)}
              showCheckboxColumn={showCheckboxColumn}
              onToggleExpand={() =>
                drawerEventId === event.id ? onCloseDrawer() : onOpenDrawer(event.id)
              }
              onToggleSelect={() => onToggleSelect(event.id)}
              onOptimisticUpdate={onOptimisticUpdate}
              onChannelClick={onChannelClick}
            />

            {/* Inline drawer below this row when it's the focused event */}
            {drawerEventId === event.id && drawerEvent && (
              <EventDrawer
                event={drawerEvent}
                onClose={closeAndReturnFocus}
                onOptimisticUpdate={onOptimisticUpdate}
              />
            )}
          </div>
        );
      })}
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
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">time</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">channel</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">sender</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">status</span>
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">dispatch</span>
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
  unpricedSessionCount: number | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}

function formatCostEvidence(
  cost: number | null | undefined,
  unpricedSessionCount: number | undefined,
): string {
  const knownSubtotal = cost !== null && cost !== undefined ? formatCostUsdPrecise(cost) : "—";
  const unpriced = unpricedSessionCount ?? 0;
  return unpriced > 0 ? `${knownSubtotal} · ${unpriced} unpriced` : knownSubtotal;
}

function FooterRollupBand({
  events,
  sessions,
  cost,
  unpricedSessionCount,
  isLoading,
  isError,
  onRetry,
}: FooterRollupBandProps) {
  if (isError) {
    return (
      <div
        className="border-t border-border py-2 bg-muted/5"
        data-testid="footer-rollup-band"
        aria-label="Filter window aggregate counts"
      >
        <SourceDegradedNote
          label="window rollup"
          detail="unavailable"
          onRetry={onRetry}
          testId="footer-rollup-unavailable"
        />
      </div>
    );
  }

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
      {cell("cost", formatCostEvidence(cost, unpricedSessionCount))}
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
   *
   * `isDown` is true whenever the events head poll is currently erroring —
   * even if stale cached events are still on screen. Without it, a dead API
   * after the first successful paint decays to the same muted "Idle" dot a
   * genuinely quiet pipeline gets, silently impersonating a calm period
   * (bu-jad4j.5, mirroring the /timeline fix in bu-qvnce.2).
   */
  onFreshnessChange?: (latestReceivedAt: string | null, isDown: boolean) => void;
  /**
   * Called whenever the active range changes (and once on mount). The parent
   * page uses this to make the header headline range-driven instead of a
   * hardcoded "Today" string (bu-4utdw.4 honesty fix).
   */
  onRangeReport?: (range: IngestionRange) => void;
}

export function TimelineTab({
  isActive,
  defaultStatuses,
  defaultViewId,
  onFreshnessChange,
  onRangeReport,
}: TimelineTabProps) {
  const [searchParams, setSearchParams] = useSearchParams();

  // ?event=<id> — drawer URL state
  const { eventId: drawerEventId, openDrawer, closeDrawer } = useEventDrawerState();

  // ?trace=<id> — drill-down spine (bu-86c4c.3). Landed on from
  // SessionDetailDrawer's "Trace ID" link and notification-feed's "Trace"
  // link, both of which used to discard the trace on navigation. Read once
  // (search params are stable across re-renders unless the owner clears it
  // via the banner below); a full navigation to a new trace remounts this
  // component with a fresh urlTrace value.
  const urlTrace = searchParams.get("trace");

  const handleClearTrace = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("trace");
      return next;
    });
  }, [setSearchParams]);

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

  useEffect(() => {
    onRangeReport?.(range);
  }, [range, onRangeReport]);

  // Saved views
  const [activeViewId, setActiveViewId] = useState<ViewId>(
    () => defaultViewId ?? readPersistedView(),
  );
  // Guards for the "apply this view's filters on (re)selection" effects
  // below: track the view id each effect has already applied for, so a
  // later re-render caused by something unrelated (e.g. the custom-views
  // list refetching in the background) doesn't silently re-stomp filters
  // the owner has since diverged from the active view — that divergence is
  // exactly what the amber "modified" dot needs to persist until the owner
  // re-applies or updates the view.
  const appliedBuiltInViewRef = useRef<ViewId | null>(null);
  const appliedCustomViewIdRef = useRef<ViewId | null>(null);

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
  const updateSavedView = useUpdateTimelineSavedView();
  const deleteSavedView = useDeleteTimelineSavedView();

  // "Save current view" dialog state
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");

  // Built-in view statuses baseline. Custom views' statuses are handled
  // separately by applyCustomViewFilterSpec (below) as part of their full
  // filter_spec, so this only needs to cover built-ins (plus the
  // defaultStatuses test override).
  const viewStatuses = useMemo((): Set<IngestionEventStatus> => {
    if (defaultStatuses) return new Set(defaultStatuses);
    if (isBuiltInViewId(activeViewId)) {
      const view = BUILT_IN_VIEWS.find((v) => v.id === activeViewId);
      return new Set(view ? view.statuses : DEFAULT_STATUSES);
    }
    return new Set(DEFAULT_STATUSES);
  }, [activeViewId, defaultStatuses]);

  // A trace-scoped landing must show its event regardless of status — the
  // default "all" view hides "skipped"/"filtered" rows, which would silently
  // swallow the very hop the trace link promised to land on. Only applies to
  // the initial mount value; the owner can still narrow via the status chips.
  const [enabledStatuses, setEnabledStatuses] = useState<Set<IngestionEventStatus>>(
    () => (urlTrace ? new Set(ALL_STATUSES) : viewStatuses),
  );

  // Re-apply the built-in baseline only the first time a given built-in view
  // becomes active (mount, or switching from a different view) — not on
  // every render where `viewStatuses` is merely a new-but-equal Set
  // (defaultStatuses/customViews churn), which would otherwise silently
  // revert a chip toggle the owner just made. Syncing local state to the
  // "which view is active" id is the documented useEffect exception (see
  // EducationPage.tsx's identical auto-select-on-load pattern).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!isBuiltInViewId(activeViewId)) return;
    // Skip while trace-scoped — see the ALL_STATUSES initializer above; this
    // effect would otherwise immediately stomp it back to the "all" preset's
    // narrower status set. Checked BEFORE updating appliedBuiltInViewRef: if
    // the ref were marked "applied" during the trace-scoped mount, clearing
    // the trace later would short-circuit on the ref check below and never
    // revert enabledStatuses back to the view's defaults.
    if (urlTrace) return;
    if (appliedBuiltInViewRef.current === activeViewId) return;
    appliedBuiltInViewRef.current = activeViewId;
    setEnabledStatuses(viewStatuses);
  }, [activeViewId, viewStatuses, urlTrace]);
  /* eslint-enable react-hooks/set-state-in-effect */

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

  // Add or remove a single channel from the active filter (used by the chip's
  // own remove button and the "+ channel" adder popover — both just flip
  // membership).
  const handleChannelToggle = useCallback(
    (channel: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        const prevChannels = (prev.get("channels") ?? "")
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean);
        const isActive = prevChannels.includes(channel);
        const updated = isActive
          ? prevChannels.filter((c) => c !== channel)
          : [...prevChannels, channel];
        if (updated.length > 0) next.set("channels", updated.join(","));
        else next.delete("channels");
        return next;
      });
    },
    [setSearchParams],
  );

  // Row channel-cell click-to-filter: idempotent add (never removes) so
  // clicking a row that's already within the active channel filter is a
  // harmless no-op instead of surprising the owner by clearing the filter.
  const handleChannelAdd = useCallback(
    (channel: string) => {
      setSearchParams((prev) => {
        const prevChannels = (prev.get("channels") ?? "")
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean);
        if (prevChannels.includes(channel)) return prev;
        const next = new URLSearchParams(prev);
        next.set("channels", [...prevChannels, channel].join(","));
        return next;
      });
    },
    [setSearchParams],
  );

  // Hour-strip minute scoping (bu-4utdw.7): clicking a strip minute with no
  // loaded ledger row narrows the ledger's window to that exact minute,
  // URL-backed like every other filter (?scopedMinute=<iso>&scopedBucketMinutes=<n>).
  // The hour strip itself keeps showing the full picker range regardless —
  // only the ledger query and footer rollup narrow.
  const scopedMinute = searchParams.get("scopedMinute");
  const scopedBucketMinutes = Math.max(1, Number(searchParams.get("scopedBucketMinutes") ?? "1") || 1);

  const handleScopeToMinute = useCallback(
    (minuteIso: string, bucketMinutesArg: number) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("scopedMinute", minuteIso);
        next.set("scopedBucketMinutes", String(bucketMinutesArg));
        return next;
      });
    },
    [setSearchParams],
  );

  const clearMinuteScope = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("scopedMinute");
      next.delete("scopedBucketMinutes");
      return next;
    });
  }, [setSearchParams]);

  // ---------------------------------------------------------------------------
  // Custom saved views — apply filter_spec, select, update, save, delete
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

  // Covers the case where a custom view id was persisted (localStorage) and
  // is already `activeViewId` at mount, but `customViews` hasn't loaded yet —
  // once it arrives, apply that view's filter_spec exactly once. Does NOT
  // re-fire on every later `customViews` refetch (e.g. after "update view"
  // invalidates the list), which would otherwise stomp a fresh divergence.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (isBuiltInViewId(activeViewId)) return;
    if (appliedCustomViewIdRef.current === activeViewId) return;
    const customView = customViews.find((v) => v.id === activeViewId);
    if (customView) {
      appliedCustomViewIdRef.current = activeViewId;
      applyCustomViewFilterSpec(customView.filter_spec);
    }
  }, [activeViewId, customViews, applyCustomViewFilterSpec]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Select (or re-select) a view. Explicitly re-applies its filters every
  // time it's clicked — including when it's already the active view — so
  // clicking a modified view's pill is the "re-apply" action the amber dot
  // promises, not a no-op.
  const handleViewSelect = useCallback(
    (viewId: ViewId) => {
      setActiveViewId(viewId);
      persistView(viewId);

      if (isBuiltInViewId(viewId)) {
        appliedBuiltInViewRef.current = viewId;
        const view = BUILT_IN_VIEWS.find((v) => v.id === viewId);
        setEnabledStatuses(new Set(view ? view.statuses : DEFAULT_STATUSES));
        return;
      }

      appliedCustomViewIdRef.current = viewId;
      const customView = customViews.find((v) => v.id === viewId);
      if (customView) {
        applyCustomViewFilterSpec(customView.filter_spec);
      }
    },
    [customViews, applyCustomViewFilterSpec],
  );

  // True when the active filter state has diverged from whatever the active
  // saved view actually defines. Built-ins only define `statuses`; custom
  // views compare each field their filter_spec actually captured (a view
  // saved without a channel filter doesn't claim "no channels" forever).
  const isViewModified = useMemo(() => {
    if (isBuiltInViewId(activeViewId)) {
      const view = BUILT_IN_VIEWS.find((v) => v.id === activeViewId);
      if (!view) return false;
      return (
        enabledStatuses.size !== view.statuses.length ||
        view.statuses.some((s) => !enabledStatuses.has(s))
      );
    }
    const customView = customViews.find((v) => v.id === activeViewId);
    if (!customView) return false;
    const spec = customView.filter_spec;
    if (spec.statuses) {
      const specStatuses = spec.statuses as IngestionEventStatus[];
      if (
        enabledStatuses.size !== specStatuses.length ||
        specStatuses.some((s) => !enabledStatuses.has(s))
      ) {
        return true;
      }
    }
    if (spec.range && spec.range !== range) return true;
    if (typeof spec.q === "string" && spec.q !== debouncedQ) return true;
    if (typeof spec.channels === "string") {
      const specChannels = [...spec.channels.split(",").map((c) => c.trim()).filter(Boolean)].sort();
      const curChannels = [...activeChannels].sort();
      if (
        specChannels.length !== curChannels.length ||
        specChannels.some((c, i) => c !== curChannels[i])
      ) {
        return true;
      }
    }
    return false;
  }, [activeViewId, customViews, enabledStatuses, range, debouncedQ, activeChannels]);

  // Persist the current filter state onto an existing custom view ("update
  // view" — offered inline next to the amber dot instead of forcing a
  // delete-and-resave round trip).
  const handleUpdateView = useCallback(
    (id: string) => {
      const spec: TimelineSavedViewFilterSpec = {
        statuses: [...enabledStatuses],
        range,
        ...(debouncedQ ? { q: debouncedQ } : {}),
        ...(activeChannels.length > 0 ? { channels: activeChannels.join(",") } : {}),
      };
      updateSavedView.mutate(
        { id, body: { filter_spec: spec } },
        {
          onSuccess: () => toast.success("Saved view updated"),
          onError: (err) => {
            toast.error(err instanceof Error ? err.message : "Failed to update view");
          },
        },
      );
    },
    [enabledStatuses, range, debouncedQ, activeChannels, updateSavedView],
  );

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

  // Connector summaries — already fetched for the attention strip; reused
  // (same query key, no extra request) to build the "+ channel" adder's
  // option list so it stays cheap (no per-chip requests).
  const { data: connectorsResp } = useConnectorSummaries({ enabled: isActive });

  const channelOptions = useMemo((): ChannelOption[] => {
    const connectors: ConnectorSummary[] = connectorsResp?.data ?? [];
    const counts = new Map<string, number | null>();
    for (const c of connectors) {
      const today = c.today?.messages_ingested ?? null;
      const prev = counts.get(c.connector_type);
      if (prev === undefined) {
        counts.set(c.connector_type, today);
      } else if (prev !== null && today !== null) {
        counts.set(c.connector_type, prev + today);
      } else if (prev === null && today !== null) {
        counts.set(c.connector_type, today);
      }
    }
    return Array.from(counts.entries())
      .map(([channel, count]) => ({ channel, count }))
      .sort((a, b) => a.channel.localeCompare(b.channel));
  }, [connectorsResp?.data]);

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

  // When a strip minute is scoped, the ledger/rollup window collapses to that
  // exact minute (a fixed historical snapshot) instead of the live-tracking
  // range window. The hour strip itself keeps using `rangeWindow` below.
  const effectiveWindow = useMemo((): { from: string; to: string } | null => {
    if (!scopedMinute) return null;
    const startMs = new Date(scopedMinute).getTime();
    if (isNaN(startMs)) return null;
    return {
      from: scopedMinute,
      to: new Date(startMs + scopedBucketMinutes * 60_000).toISOString(),
    };
  }, [scopedMinute, scopedBucketMinutes]);

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
    ...(urlTrace ? { trace_id: urlTrace } : {}),
    // A trace-scoped landing must not be silently clipped by the range
    // picker's window — the traced event may be older than "24h" (the
    // default) and the whole point of the drill-down link is to find it
    // regardless of when it happened. Only apply the range bound when no
    // trace filter is active.
    ...(urlTrace
      ? {}
      : {
          // Only apply a lower bound on received_at so the 30 s refetch can pick
          // up events that arrived after the initial load. Including an upper
          // bound (rangeWindow.to) would freeze the query at the moment the
          // range changed, causing the refetch to silently miss new events. A
          // minute-scoped window is an intentional fixed snapshot, so it uses
          // both bounds.
          from: effectiveWindow?.from ?? rangeWindow.from,
          ...(effectiveWindow ? { to: effectiveWindow.to } : {}),
        }),
    ...(activeSort ? { sort: activeSort } : {}),
  }), [debouncedQ, activeChannels, statusesCsv, rangeWindow.from, effectiveWindow, activeSort, urlTrace]);

  const {
    data: infiniteData,
    isLoading,
    isFetching,
    isError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useIngestionEvents(eventsFilters, { enabled: isActive });

  // Hour strip data source (bu-4utdw.7): one histogram request for the whole
  // picker range/filters, sliced per hour below. Always uses `rangeWindow`
  // (not `effectiveWindow`) so the strip keeps showing full-range context
  // even while the ledger itself is minute-scoped.
  const histogramBucket = histogramBucketForRange(range);
  const histogramParams = useMemo(() => ({
    // A trace-scoped hour strip must not be silently clipped by the range
    // picker's window either — same reasoning as eventsFilters above. The
    // server auto-widens to the trace's own event bounds when `from`/`to`
    // are omitted and `trace_id` is present (bu-1f81d).
    ...(urlTrace ? {} : { from: rangeWindow.from, to: rangeWindow.to }),
    bucket: histogramBucket,
    ...(activeChannels.length > 0 ? { channels: activeChannels.join(",") } : {}),
    ...(statusesCsv ? { statuses: statusesCsv } : {}),
    ...(debouncedQ ? { q: debouncedQ } : {}),
    ...(urlTrace ? { trace_id: urlTrace } : {}),
  }), [
    rangeWindow.from,
    rangeWindow.to,
    histogramBucket,
    activeChannels,
    statusesCsv,
    debouncedQ,
    urlTrace,
  ]);

  const {
    data: histogramResp,
    isLoading: histogramLoading,
    isError: histogramError,
    refetch: refetchHistogram,
  } = useIngestionEventsHistogram(histogramParams, { enabled: isActive });

  // The server can return a coarser actual bucket after its bounded fallback
  // or trace-scoped auto-widening. That response bucket is authoritative for
  // both strip slots and the range scoped by a strip click.
  const actualHistogramBucket = histogramResp?.bucket ?? histogramBucket;
  const histogramBucketMinutesValue = histogramBucketMinutes(actualHistogramBucket);

  const histogramByHour = useMemo(() => {
    const map = new Map<string, IngestionHistogramBucket[]>();
    for (const bucket of histogramResp?.buckets ?? []) {
      const key = hourGroupKey(bucket.ts);
      const existing = map.get(key);
      if (existing) existing.push(bucket);
      else map.set(key, [bucket]);
    }
    return map;
  }, [histogramResp?.buckets]);

  // Window rollup — fires with the same filter shape plus the active window
  // (minute-scoped window when the owner has scoped to a strip minute).
  const rollupStatuses = useMemo(() => [...enabledStatuses].join(","), [enabledStatuses]);
  const rollupChannels = useMemo(() => activeChannels.join(","), [activeChannels]);

  const {
    data: rollupData,
    isLoading: rollupLoading,
    isError: rollupError,
    refetch: refetchRollup,
  } = useIngestionWindowRollup(
    {
      // A trace-scoped footer rollup must not be silently clipped by the
      // range picker's window either — same reasoning as eventsFilters
      // above. The server drops the window bound entirely when `trace_id`
      // is present, ignoring any `from`/`to` (bu-1f81d), so omit them here
      // too rather than sending a window the server will ignore anyway.
      ...(urlTrace
        ? {}
        : {
            from: effectiveWindow?.from ?? rangeWindow.from,
            to: effectiveWindow?.to ?? rangeWindow.to,
          }),
      ...(debouncedQ ? { q: debouncedQ } : {}),
      ...(rollupChannels ? { channels: rollupChannels } : {}),
      ...(rollupStatuses ? { statuses: rollupStatuses } : {}),
      ...(urlTrace ? { trace_id: urlTrace } : {}),
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
      // `isError` stays true while React Query retains the last successful
      // pages, so a head poll that starts failing after the first paint is
      // reported as down even though stale events are still rendered — the
      // badge distinguishes "Down" from a quiet "Idle" (bu-jad4j.5).
      onFreshnessChange(latestReceivedAt, !!isError);
    }
  }, [latestReceivedAt, isLoading, isError, onFreshnessChange]);

  // Evict stale overrides — syncing optimistic overrides to freshly-fetched
  // server state (external system) is the sanctioned effect use case; the
  // functional updater already no-ops (returns `prev`) when nothing needs
  // evicting.
  /* eslint-disable react-hooks/set-state-in-effect */
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
  /* eslint-enable react-hooks/set-state-in-effect */

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
    events: IngestionEventSummary[];
  }

  const hourGroups = useMemo((): HourGroup[] => {
    const groups: HourGroup[] = [];
    let currentKey: string | null = null;

    for (const event of events) {
      const hKey = hourGroupKey(event.received_at);
      if (hKey !== currentKey) {
        groups.push({ key: hKey, events: [] });
        currentKey = hKey;
      }
      groups[groups.length - 1].events.push(event);
    }
    return groups;
  }, [events]);

  // bu-4utdw.4: selection checkbox column is demoted — hidden by default,
  // shown once the errors view is active or the user has started selecting
  // rows (shift-click on a row, or the bulk bar's "select all visible").
  const showCheckboxColumn = activeViewId === "errors" || selectedIds.size > 0;

  const visibleEligibleIds = useMemo(
    () => events.filter((e) => isBulkEligible(e.status)).map((e) => e.id).slice(0, MAX_BULK_RETRY_BATCH),
    [events],
  );

  const handleSelectAllVisible = useCallback((ids: string[]) => {
    setSelectedIds(new Set(ids.slice(0, MAX_BULK_RETRY_BATCH)));
  }, []);

  return (
    <div className="space-y-3" data-testid="timeline-tab">
      {/* Toolbar */}
      <Toolbar
        range={range}
        onRangeChange={handleRangeChange}
        activeViewId={activeViewId}
        onViewSelect={handleViewSelect}
        isViewModified={isViewModified}
        onUpdateView={handleUpdateView}
        enabledStatuses={enabledStatuses}
        onStatusToggle={handleStatusToggle}
        searchQuery={searchInputValue}
        onSearchChange={handleSearchChange}
        activeChannels={activeChannels}
        onChannelToggle={handleChannelToggle}
        channelOptions={channelOptions}
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
        visibleEligibleIds={visibleEligibleIds}
        onSelectAllVisible={handleSelectAllVisible}
      />

      {/* Connector attention strip */}
      <ConnectorAttentionStrip isActive={isActive} />

      {/* Trace scope indicator (bu-86c4c.3 — drill-down spine) — honest state:
          the ledger below is narrowed to a single trace_id, ignoring the
          range picker's window entirely. Cleared by the owner, never
          silently. */}
      {urlTrace && (
        <div
          className="flex items-center gap-2 px-3 py-1.5 border border-border rounded bg-muted/10 font-mono text-[11px] text-muted-foreground"
          data-testid="trace-scope-banner"
        >
          <span className="truncate">
            Scoped to trace <span className="text-foreground">{urlTrace}</span>
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClearTrace}
            className="h-5 px-1.5 font-mono text-[11px] text-muted-foreground hover:text-foreground shrink-0"
            data-testid="trace-scope-clear"
          >
            <X className="size-3 mr-1" />
            Clear
          </Button>
        </div>
      )}

      {/* Minute scope indicator (bu-4utdw.7) — honest state: the ledger below
          is narrowed to a single hour-strip minute, not the range picker's
          window. Cleared by the owner, never silently. */}
      {effectiveWindow && (
        <div
          className="flex items-center gap-2 px-3 py-1.5 border border-border rounded bg-muted/10 font-mono text-[11px] text-muted-foreground"
          data-testid="minute-scope-banner"
        >
          <span>
            Scoped to <Time value={effectiveWindow.from} mode="absolute" precision="time" />
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={clearMinuteScope}
            className="h-5 px-1.5 font-mono text-[11px] text-muted-foreground hover:text-foreground"
            data-testid="minute-scope-clear"
          >
            <X className="size-3 mr-1" />
            Clear
          </Button>
        </div>
      )}

      {/* Ledger */}
      <FetchingDim isFetching={isFetching && !isLoading && !isError && !isFetchingNextPage}>
      <div className="border border-border rounded" data-testid="timeline-ledger">
        <LedgerColumnHeaders />

        {isError ? (
          <div className="px-6 py-4 space-y-2">
            <p className="font-serif text-[15px] leading-[1.55] text-muted-foreground italic">
              Failed to load ingestion events.
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              className="font-mono text-[11px]"
              data-testid="events-retry-button"
            >
              <RotateCw className="size-3 mr-1" />
              Retry
            </Button>
          </div>
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
                hourKey={group.key}
                events={group.events}
                drawerEventId={drawerEventId}
                selectedIds={selectedIds}
                showCheckboxColumn={showCheckboxColumn}
                onOpenDrawer={(id) => openDrawer(id)}
                onToggleSelect={handleToggleSelect}
                onOptimisticUpdate={handleOptimisticUpdate}
                drawerEvent={drawerEvent}
                onCloseDrawer={closeDrawer}
                onChannelClick={handleChannelAdd}
                histogramBuckets={histogramByHour.get(group.key) ?? []}
                bucketMinutes={histogramBucketMinutesValue}
                histogramLoading={histogramLoading}
                histogramError={histogramError}
                onRetryHistogram={() => void refetchHistogram()}
                onScopeToMinute={handleScopeToMinute}
              />
            ))}
          </>
        )}
      </div>
      </FetchingDim>

      {/* Footer rollup band — aggregate counts for the active filter window */}
      <FooterRollupBand
        events={rollupData?.events}
        sessions={rollupData?.sessions}
        cost={rollupData?.cost}
        unpricedSessionCount={rollupData?.unpriced_session_count}
        isLoading={rollupLoading}
        isError={rollupError}
        onRetry={() => void refetchRollup()}
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
