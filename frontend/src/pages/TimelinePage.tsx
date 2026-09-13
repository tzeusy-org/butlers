/**
 * TimelinePage — the fleet chronicle (/timeline, bu-86c4c.10 — "One Timeline").
 *
 * Rebuilt on the ingestion dispatch ledger's component system (bu-4utdw):
 * Dispatch layout primitives, hour-grouped hairline rows, a URL-backed event
 * drawer, saved views (shared /api/timeline/saved-views backend — already
 * generic, previously consumed only by the ingestion ledger), and a real
 * live tail (WS-driven cache invalidation + an "N new events" pill) instead
 * of the old auto-refresh-toggle + manual "Load more" accumulator. Source
 * facets (sessions / notifications / errors) replace the old flat
 * event-type chip row with the same semantics.
 *
 * Replaces UnifiedTimeline.tsx (deleted) — the abandoned pre-redesign
 * version of this exact surface (see docs/redesigns/2026-07-03-jarvis-audit.md
 * §"7. One Timeline").
 */

import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { LiveStatusBadge } from "@/components/ui/live-status-badge";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { DispatchLayout, DispatchHeader, DispatchSurface } from "@/components/ingestion/dispatch";
import { NewEventsPill } from "@/components/timeline/NewEventsPill";
import { TimelineAttentionStrip } from "@/components/timeline/TimelineAttentionStrip";
import { TimelineLedger } from "@/components/timeline/TimelineLedger";
import { TimelineDensity } from "@/components/timeline/TimelineDensity";
import { useButlers } from "@/hooks/use-butlers.ts";
import { usePageActions, type PageAction } from "@/hooks/use-page-actions";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import { useTimelineAttention, useTimelineEvent, useTimelineHistogram } from "@/hooks/use-timeline";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type { TimelineSavedViewFilterSpec } from "@/api/types.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Source facets
// ---------------------------------------------------------------------------

const SOURCE_FACETS = [
  { value: "session", label: "Sessions" },
  { value: "error", label: "Errors" },
  { value: "notification", label: "Notifications" },
] as const;

// ---------------------------------------------------------------------------
// Built-in saved views
// ---------------------------------------------------------------------------

interface BuiltInView {
  id: string;
  label: string;
  event_type?: string[];
}

const BUILT_IN_VIEWS: BuiltInView[] = [
  { id: "all", label: "All" },
  { id: "errors", label: "Errors only", event_type: ["error"] },
  { id: "notifications", label: "Notifications", event_type: ["notification"] },
];

// ---------------------------------------------------------------------------
// TimelinePage
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// URL-backed facets/view/butlers (bu-qvnce.13, pursuit move 13) — the
// `?event=` drawer param was already URL-backed (useEventDrawerState); the
// source facets, butler multi-select, and active-view highlight now share
// the URL too, so the filtered timeline is shareable/reloadable. Follows the
// same comma-separated-set convention as QaOverviewPage's `?butler=`.
// ---------------------------------------------------------------------------

function parseCsvList(sp: URLSearchParams, key: string): string[] {
  return (sp.get(key) ?? "").split(",").filter(Boolean);
}

function writeCsvList(sp: URLSearchParams, key: string, values: string[]): void {
  if (values.length > 0) sp.set(key, values.join(","));
  else sp.delete(key);
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;

function iso(value: number): string {
  return new Date(value).toISOString();
}

function latestCompleteHour(now = Date.now()): { since: string; until: string } {
  const until = Math.floor(now / HOUR_MS) * HOUR_MS;
  return { since: iso(until - HOUR_MS), until: iso(until) };
}

function parseAwareTimestamp(value: string): number | null {
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

interface TimelineIntervalState {
  valid: boolean;
  explicit: boolean;
  since: string;
  until: string;
  bucketSince?: string;
  bucketUntil?: string;
}

function parseTimelineInterval(sp: URLSearchParams): TimelineIntervalState {
  const latest = latestCompleteHour();
  const sinceRaw = sp.get("since");
  const untilRaw = sp.get("until");
  const bucketSinceRaw = sp.get("bucket_since");
  const bucketUntilRaw = sp.get("bucket_until");
  const explicit = sinceRaw !== null || untilRaw !== null;
  if ((sinceRaw === null) !== (untilRaw === null)) return { ...latest, valid: false, explicit };

  const sinceMs = sinceRaw === null ? Date.parse(latest.since) : parseAwareTimestamp(sinceRaw);
  const untilMs = untilRaw === null ? Date.parse(latest.until) : parseAwareTimestamp(untilRaw);
  if (
    sinceMs === null ||
    untilMs === null ||
    sinceMs % MINUTE_MS !== 0 ||
    untilMs % MINUTE_MS !== 0 ||
    untilMs - sinceMs !== HOUR_MS
  ) {
    return { ...latest, valid: false, explicit };
  }
  const since = iso(sinceMs);
  const until = iso(untilMs);
  if ((bucketSinceRaw === null) !== (bucketUntilRaw === null)) {
    return { since, until, valid: false, explicit };
  }
  if (bucketSinceRaw === null) return { since, until, valid: true, explicit };

  const bucketSinceMs = parseAwareTimestamp(bucketSinceRaw);
  const bucketUntilMs = parseAwareTimestamp(bucketUntilRaw!);
  if (
    bucketSinceMs === null ||
    bucketUntilMs === null ||
    bucketSinceMs % MINUTE_MS !== 0 ||
    bucketUntilMs - bucketSinceMs !== MINUTE_MS ||
    bucketSinceMs < sinceMs ||
    bucketUntilMs > untilMs
  ) {
    return { since, until, valid: false, explicit };
  }
  return {
    since,
    until,
    bucketSince: iso(bucketSinceMs),
    bucketUntil: iso(bucketUntilMs),
    valid: true,
    explicit,
  };
}

export default function TimelinePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedButlers = useMemo(() => parseCsvList(searchParams, "butler"), [searchParams]);
  const selectedTypes = useMemo(() => parseCsvList(searchParams, "type"), [searchParams]);
  const trace = searchParams.get("trace")?.trim() || undefined;
  const activeViewId = searchParams.get("view") ?? "all";
  const includeInternal = searchParams.get("internal") === "1";
  const interval = useMemo(() => parseTimelineInterval(searchParams), [searchParams]);

  const {
    data: butlersResponse,
    isError: isButlerFacetsError,
    refetch: refetchButlerFacets,
  } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];

  const filters = useMemo(
    () => ({
      butler: selectedButlers.length > 0 ? selectedButlers : undefined,
      event_type: selectedTypes.length > 0 ? selectedTypes : undefined,
      trace,
      since: interval.bucketSince,
      until: interval.bucketUntil,
    }),
    [selectedButlers, selectedTypes, trace, interval.bucketSince, interval.bucketUntil],
  );

  const {
    events,
    isLoading,
    isFetching,
    isError,
    refetch,
    hasMore,
    loadMore,
    loadMoreError,
    retryLoadMore,
    isLoadingMore,
    pinned,
    newCount,
    showNewEvents,
    degradedSources,
    degradedButlers,
    heartbeatRollup,
    isLiveFeedDown,
  } = useTimelineLedger(filters, { enabled: interval.valid });

  const histogramParams = useMemo(
    () => ({
      since: interval.since,
      until: interval.until,
      butler: selectedButlers.length > 0 ? selectedButlers : undefined,
      event_type: selectedTypes.length > 0 ? selectedTypes : undefined,
      trace,
    }),
    [interval.since, interval.until, selectedButlers, selectedTypes, trace],
  );
  const histogram = useTimelineHistogram(histogramParams, interval.valid);
  const attentionParams = useMemo(
    () => ({
      butler: selectedButlers.length > 0 ? selectedButlers : undefined,
      trace,
    }),
    [selectedButlers, trace],
  );
  const attention = useTimelineAttention(attentionParams);
  const selectedEventId = searchParams.get("event");
  const selectedEventInLedger = Boolean(
    selectedEventId && events.some((event) => event.id === selectedEventId),
  );
  const selectedEvent = useTimelineEvent(
    selectedEventId,
    attentionParams,
    interval.valid && !selectedEventInLedger,
  );
  const resolvedEvent = selectedEvent.data?.data[0];
  const eventResolutionFailed = Boolean(
    selectedEventId &&
      !selectedEventInLedger &&
      (selectedEvent.isError ||
        (!resolvedEvent && Boolean(selectedEvent.data?.meta.degraded_sources.length))),
  );

  // Saved views — shared /api/timeline/saved-views backend (bu-vgj88),
  // already generic (consumed by the ingestion ledger before this page).
  const {
    data: customViewsResp,
    isError: isSavedViewsError,
    refetch: refetchSavedViews,
  } = useTimelineSavedViews();
  const customViews = customViewsResp?.data ?? [];
  const createSavedView = useCreateTimelineSavedView();
  const deleteSavedView = useDeleteTimelineSavedView();

  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");

  function applyFilterSpecToParams(sp: URLSearchParams, spec: TimelineSavedViewFilterSpec) {
    const types = Array.isArray(spec.event_type) ? (spec.event_type as string[]) : [];
    const butlers = Array.isArray(spec.butler) ? (spec.butler as string[]) : [];
    writeCsvList(sp, "type", types);
    writeCsvList(sp, "butler", butlers);
  }

  const selectBuiltInView = useCallback((view: BuiltInView) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (view.id === "all") next.delete("view");
      else next.set("view", view.id);
      writeCsvList(next, "type", view.event_type ?? []);
      writeCsvList(next, "butler", []);
      return next;
    });
  }, [setSearchParams]);

  const builtInViewCommands = useMemo<PaletteCommand[]>(
    () =>
      BUILT_IN_VIEWS.map((view) => ({
        id: `timeline-view-${view.id}`,
        label: view.label,
        keywords: ["timeline", "view", "preset", view.label],
        perform: () => selectBuiltInView(view),
      })),
    [selectBuiltInView],
  );
  useRegisterCommands(builtInViewCommands);

  function selectCustomView(id: string, spec: TimelineSavedViewFilterSpec) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("view", id);
      applyFilterSpecToParams(next, spec);
      return next;
    });
  }

  async function handleSaveView() {
    if (!saveViewName.trim() || createSavedView.isPending) return;
    const filter_spec: TimelineSavedViewFilterSpec = {
      event_type: selectedTypes,
      butler: selectedButlers,
    };
    try {
      const created = await createSavedView.mutateAsync({ name: saveViewName.trim(), filter_spec });
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("view", created.id);
        return next;
      });
      setSaveViewName("");
      setSaveDialogOpen(false);
    } catch (err) {
      console.error("Failed to save view:", err);
    }
  }

  // Toggle a single source facet — diverges from whatever view is active
  // (the "all" view remains selected visually but no longer reflects the
  // exact filter set; simple and honest rather than tracking a modified dot).
  function toggleType(type: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = parseCsvList(prev, "type");
      const updated = current.includes(type) ? current.filter((t) => t !== type) : [...current, type];
      writeCsvList(next, "type", updated);
      return next;
    });
  }

  function toggleButler(name: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const current = parseCsvList(prev, "butler");
      const updated = current.includes(name) ? current.filter((b) => b !== name) : [...current, name];
      writeCsvList(next, "butler", updated);
      return next;
    });
  }

  function toggleInternal() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (next.get("internal") === "1") next.delete("internal");
      else next.set("internal", "1");
      return next;
    });
  }

  function clearTraceScope() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("trace");
      return next;
    });
  }

  function writeChartWindow(since: string, until: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("since", since);
      next.set("until", until);
      next.delete("bucket_since");
      next.delete("bucket_until");
      next.delete("event");
      return next;
    });
  }

  function shiftChart(hours: number) {
    writeChartWindow(
      iso(Date.parse(interval.since) + hours * HOUR_MS),
      iso(Date.parse(interval.until) + hours * HOUR_MS),
    );
  }

  function selectBucket(since: string, until: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("since", interval.since);
      next.set("until", interval.until);
      next.set("bucket_since", since);
      next.set("bucket_until", until);
      next.delete("event");
      return next;
    });
  }

  function clearInterval() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("since");
      next.delete("until");
      next.delete("bucket_since");
      next.delete("bucket_until");
      return next;
    });
  }

  function clearBucketSelection() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("bucket_since");
      next.delete("bucket_until");
      return next;
    });
  }

  const jumpToLatest = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("since");
      next.delete("until");
      next.delete("bucket_since");
      next.delete("bucket_until");
      return next;
    });
    showNewEvents();
  }, [showNewEvents, setSearchParams]);

  // Live status: driven by the newest loaded event when pinned to now — the
  // same freshness convention as the ingestion ledger's LiveStatusBadge.
  const latestReceivedAt = isLoading ? undefined : (events[0]?.timestamp ?? null);

  const hasDegradedSource = degradedSources.length > 0 || degradedButlers.length > 0;
  const degradedSourceDetail = [
    degradedSources.length > 0
      ? `Partial data: ${degradedSources.join(", ")} temporarily unavailable.`
      : null,
    degradedButlers.length > 0 ? `Session data from ${degradedButlers.join(", ")} is unavailable.` : null,
    "This page may be missing some events from that source.",
  ]
    .filter(Boolean)
    .join(" ");
  const isLiveHeadRefreshing = pinned && isFetching && !isLoading && !isError;

  // Hot-loop keyboard coverage (bu-ep4ks.12): this was the densest telemetry
  // page in the fleet with zero useRegisterShortcut coverage. "r" mirrors the
  // page's own retry affordance; "n" is only registered while there is
  // something to jump to, matching the NewEventsPill's own visibility.
  const pageActions = useMemo<PageAction[]>(() => {
    if (!interval.valid) return [];
    const actions: PageAction[] = [
      {
        id: "timeline-refresh",
        label: "Refresh timeline",
        key: "r",
        display: ["r"],
        description: "Refresh timeline",
        handler: () => void refetch(),
      },
    ];
    if (newCount > 0 || interval.explicit || interval.bucketSince) {
      actions.push({
        id: "timeline-jump-latest",
        label: "Jump to latest events",
        key: "n",
        display: ["n"],
        description: "Jump to latest events",
        handler: jumpToLatest,
      });
    }
    return actions;
  }, [refetch, newCount, interval.valid, interval.explicit, interval.bucketSince, jumpToLatest]);
  usePageActions(pageActions);

  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Fleet · timeline"
        headline="Every household event, newest first."
        description="Sessions, notifications, and errors across every butler: the fleet's single chronicle."
        aside={interval.valid
          ? <LiveStatusBadge latestReceivedAt={latestReceivedAt} isDown={isLiveFeedDown} />
          : null}
      />

      <DispatchSurface className="space-y-4">
        {!interval.valid ? (
          <section className="flex items-center justify-between gap-3 rounded border border-destructive/40 px-3 py-2" role="alert">
            <span className="text-sm">The Timeline interval in this URL is invalid.</span>
            <Button type="button" variant="outline" size="xs" onClick={clearInterval}>Clear interval</Button>
          </section>
        ) : (
          <section className="space-y-3" aria-label="Timeline hour density">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1">
                <Button type="button" variant="outline" size="xs" onClick={() => shiftChart(-1)}>Previous hour</Button>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => shiftChart(1)}
                  disabled={Date.parse(interval.until) >= Date.parse(latestCompleteHour().until)}
                >
                  Next hour
                </Button>
                <Button type="button" variant="ghost" size="xs" onClick={clearInterval}>Latest hour</Button>
              </div>
              {interval.bucketSince ? (
                <Button type="button" variant="ghost" size="xs" onClick={clearBucketSelection}>Clear selection</Button>
              ) : null}
            </div>
            <TimelineDensity
              key={`${interval.since}:${interval.bucketSince ?? "live"}`}
              histogram={histogram.data}
              isLoading={histogram.isLoading}
              isError={histogram.isError}
              selectedSince={interval.bucketSince}
              onSelect={selectBucket}
              onRetry={() => void histogram.refetch()}
            />
          </section>
        )}

        <TimelineAttentionStrip
          attention={attention.data}
          isLoading={attention.isLoading}
          isError={attention.isError}
          onRetry={() => void attention.refetch()}
          selectedButlers={selectedButlers}
          trace={trace}
        />

        {interval.valid && trace && (
          <section
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border border-border rounded bg-muted/10 px-3 py-2"
            aria-label="Trace scope"
            data-testid="trace-scope-banner"
          >
            <p className="font-mono text-[11px] text-muted-foreground">
              Scoped to trace <span className="text-foreground">{trace}</span>. Matching sessions
              and trace-attributed notifications.
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={clearTraceScope}
              className="font-mono text-[11px] text-muted-foreground hover:text-foreground"
            >
              Clear trace filter
            </Button>
          </section>
        )}

        {interval.valid && hasDegradedSource && (
          <SourceDegradedNote
            label="Timeline"
            detail={degradedSourceDetail}
            testId="timeline-degraded-banner"
            className="font-mono text-[11px]"
          />
        )}

        {interval.valid && heartbeatRollup.ticks > 0 && (
          <p
            className="font-mono text-[11px] text-muted-foreground"
            data-testid="timeline-heartbeat-rollup"
          >
            This page: {heartbeatRollup.ticks} {heartbeatRollup.ticks === 1 ? "tick" : "ticks"} ·{" "}
            {heartbeatRollup.butlers} {heartbeatRollup.butlers === 1 ? "butler" : "butlers"} ticked
            {heartbeatRollup.failed > 0 && (
              <span className="text-destructive"> · {heartbeatRollup.failed} failed</span>
            )}
          </p>
        )}

        {/* Toolbar */}
        {interval.valid && <div className="space-y-3">
          {/* Saved views */}
          <div className="flex flex-wrap items-center gap-1.5">
            {BUILT_IN_VIEWS.map((view) => (
              <button key={view.id} type="button" onClick={() => selectBuiltInView(view)}>
                <Badge
                  variant={activeViewId === view.id ? "default" : "outline"}
                  className="cursor-pointer"
                  data-testid={`saved-view-${view.id}`}
                >
                  {view.label}
                </Badge>
              </button>
            ))}
            {customViews.map((view) => (
              <span key={view.id} className="inline-flex items-center gap-0.5">
                <button type="button" onClick={() => selectCustomView(view.id, view.filter_spec)}>
                  <Badge
                    variant={activeViewId === view.id ? "default" : "outline"}
                    className="cursor-pointer"
                    data-testid={`saved-view-${view.id}`}
                  >
                    {view.name}
                  </Badge>
                </button>
                <button
                  type="button"
                  aria-label={`Delete saved view ${view.name}`}
                  className="text-muted-foreground hover:text-destructive text-xs px-1"
                  onClick={() => void deleteSavedView.mutate(view.id)}
                >
                  ×
                </button>
              </span>
            ))}
            <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => setSaveDialogOpen(true)}>
              + Save view
            </Button>
            {isSavedViewsError && (
              <div
                className="flex items-center gap-2 text-xs text-[var(--amber-text)]"
                data-testid="timeline-saved-views-unavailable"
              >
                <span role="status" aria-live="polite">
                  Saved views are temporarily unavailable.
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => void refetchSavedViews()}
                  aria-label="Retry saved views"
                >
                  Retry
                </Button>
              </div>
            )}
          </div>

          {/* Source facets */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Source</p>
            <div className="flex flex-wrap gap-1.5">
              {SOURCE_FACETS.map(({ value, label }) => (
                <button key={value} type="button" onClick={() => toggleType(value)}>
                  <Badge
                    variant={selectedTypes.includes(value) ? "default" : "outline"}
                    className={cn(
                      "cursor-pointer transition-colors",
                      selectedTypes.includes(value) && "bg-primary text-primary-foreground",
                    )}
                    data-testid={`facet-${value}`}
                  >
                    {label}
                  </Badge>
                </button>
              ))}
            </div>
          </div>

          {/* Butler filter */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Butler</p>
            <div className="flex flex-wrap gap-1.5">
              {butlerNames.map((name) => (
                <button key={name} type="button" onClick={() => toggleButler(name)}>
                  <Badge
                    variant={selectedButlers.includes(name) ? "default" : "outline"}
                    className={cn(
                      "cursor-pointer transition-colors",
                      selectedButlers.includes(name) && "bg-primary text-primary-foreground",
                    )}
                  >
                    {name}
                  </Badge>
                </button>
              ))}
              {isButlerFacetsError ? (
                <div
                  className="flex items-center gap-2 text-xs text-[var(--amber-text)]"
                  data-testid="timeline-butler-facets-unavailable"
                >
                  <span role="status" aria-live="polite">
                    Butler filters are temporarily unavailable.
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    onClick={() => void refetchButlerFacets()}
                    aria-label="Retry butler filters"
                  >
                    Retry
                  </Button>
                </div>
              ) : butlerNames.length === 0 ? (
                <span className="text-xs text-muted-foreground italic">No butlers available</span>
              ) : null}
            </div>
          </div>

          {/* Successful internal maintenance is opt-in so it cannot crowd
              owner-facing history during a scheduled burst. */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Lens</p>
            <Button
              type="button"
              variant={includeInternal ? "secondary" : "outline"}
              size="xs"
              onClick={toggleInternal}
              aria-pressed={includeInternal}
              aria-label={includeInternal ? "Hide internal activity" : "Show internal activity"}
              data-testid="timeline-internal-lens"
            >
              Internal
            </Button>
          </div>
        </div>}

        {interval.valid ? (
          <>
            <NewEventsPill count={newCount} onClick={showNewEvents} />

            <FetchingDim isFetching={isLiveHeadRefreshing}>
              <TimelineLedger
                events={events}
                resolvedEvent={resolvedEvent}
                isResolvingEvent={Boolean(
                  selectedEventId && !selectedEventInLedger && selectedEvent.isLoading,
                )}
                eventResolutionFailed={eventResolutionFailed}
                onRetryEventResolution={() => void selectedEvent.refetch()}
                isLoading={isLoading}
                includeInternal={includeInternal}
                isError={isError}
                onRetry={refetch}
                hasPartialData={hasDegradedSource}
                hasMore={hasMore}
                onLoadMore={loadMore}
                loadMoreError={loadMoreError}
                onRetryLoadMore={retryLoadMore}
                isLoadingMore={isLoadingMore}
              />
            </FetchingDim>
          </>
        ) : null}
      </DispatchSurface>

      <Dialog open={interval.valid && saveDialogOpen} onOpenChange={setSaveDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save current view</DialogTitle>
            <DialogDescription>
              Saves the active source and butler filters as a named preset.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={saveViewName}
            onChange={(e) => setSaveViewName(e.target.value)}
            placeholder="View name"
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setSaveDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => void handleSaveView()}
              disabled={!saveViewName.trim() || createSavedView.isPending}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DispatchLayout>
  );
}
