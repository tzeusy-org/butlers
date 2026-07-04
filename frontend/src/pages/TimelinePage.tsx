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

import { useMemo, useState } from "react";

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
import { LiveStatusBadge } from "@/components/ui/live-status-badge";
import { DispatchLayout, DispatchHeader, DispatchSurface } from "@/components/ingestion/dispatch";
import { NewEventsPill } from "@/components/timeline/NewEventsPill";
import { TimelineLedger } from "@/components/timeline/TimelineLedger";
import { useButlers } from "@/hooks/use-butlers.ts";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type { TimelineSavedViewFilterSpec } from "@/api/types.ts";
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

export default function TimelinePage() {
  const [selectedButlers, setSelectedButlers] = useState<string[]>([]);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [activeViewId, setActiveViewId] = useState<string>("all");

  const { data: butlersResponse } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];

  const filters = useMemo(
    () => ({
      butler: selectedButlers.length > 0 ? selectedButlers : undefined,
      event_type: selectedTypes.length > 0 ? selectedTypes : undefined,
    }),
    [selectedButlers, selectedTypes],
  );

  const {
    events,
    isLoading,
    isError,
    refetch,
    hasMore,
    loadMore,
    isLoadingMore,
    newCount,
    showNewEvents,
    degradedSources,
    heartbeatRollup,
  } = useTimelineLedger(filters);

  // Saved views — shared /api/timeline/saved-views backend (bu-vgj88),
  // already generic (consumed by the ingestion ledger before this page).
  const { data: customViewsResp } = useTimelineSavedViews();
  const customViews = customViewsResp?.data ?? [];
  const createSavedView = useCreateTimelineSavedView();
  const deleteSavedView = useDeleteTimelineSavedView();

  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState("");

  function applyFilterSpec(spec: TimelineSavedViewFilterSpec) {
    const types = Array.isArray(spec.event_type) ? (spec.event_type as string[]) : [];
    const butlers = Array.isArray(spec.butler) ? (spec.butler as string[]) : [];
    setSelectedTypes(types);
    setSelectedButlers(butlers);
  }

  function selectBuiltInView(view: BuiltInView) {
    setActiveViewId(view.id);
    setSelectedTypes(view.event_type ?? []);
    setSelectedButlers([]);
  }

  function selectCustomView(id: string, spec: TimelineSavedViewFilterSpec) {
    setActiveViewId(id);
    applyFilterSpec(spec);
  }

  async function handleSaveView() {
    if (!saveViewName.trim() || createSavedView.isPending) return;
    const filter_spec: TimelineSavedViewFilterSpec = {
      event_type: selectedTypes,
      butler: selectedButlers,
    };
    try {
      const created = await createSavedView.mutateAsync({ name: saveViewName.trim(), filter_spec });
      setActiveViewId(created.id);
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
    setSelectedTypes((prev) => (prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type]));
  }

  function toggleButler(name: string) {
    setSelectedButlers((prev) => (prev.includes(name) ? prev.filter((b) => b !== name) : [...prev, name]));
  }

  // Live status: driven by the newest loaded event when pinned to now — the
  // same freshness convention as the ingestion ledger's LiveStatusBadge.
  const latestReceivedAt = isLoading ? undefined : (events[0]?.timestamp ?? null);

  const hasDegradedSource = degradedSources.length > 0;

  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Fleet · timeline"
        headline="Every household event, newest first."
        description="Sessions, notifications, and errors across every butler — the fleet's single chronicle."
        aside={<LiveStatusBadge latestReceivedAt={latestReceivedAt} />}
      />

      <DispatchSurface className="space-y-4">
        {hasDegradedSource && (
          <p
            className="font-mono text-[11px] text-[var(--amber-text)] border border-[var(--amber)]/30 bg-[var(--amber)]/5 rounded px-3 py-1.5"
            data-testid="timeline-degraded-banner"
          >
            Partial data: {degradedSources.join(", ")} temporarily unavailable — this page may be
            missing some events from that source.
          </p>
        )}

        {heartbeatRollup.ticks > 0 && (
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
        <div className="space-y-3">
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
              {butlerNames.length === 0 && (
                <span className="text-xs text-muted-foreground italic">No butlers available</span>
              )}
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
            </div>
          </div>
        </div>

        <NewEventsPill count={newCount} onClick={showNewEvents} />

        <TimelineLedger
          events={events}
          isLoading={isLoading}
          isError={isError}
          onRetry={refetch}
          hasMore={hasMore}
          onLoadMore={loadMore}
          isLoadingMore={isLoadingMore}
        />
      </DispatchSurface>

      <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
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
