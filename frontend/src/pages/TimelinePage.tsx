import { useState, useCallback, useMemo } from "react";

import type { TimelineEvent } from "@/api/types.ts";
import UnifiedTimeline from "@/components/timeline/UnifiedTimeline.tsx";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useButlers } from "@/hooks/use-butlers.ts";
import { useTimeline } from "@/hooks/use-timeline.ts";
import { cn } from "@/lib/utils";
import { useAutoRefresh } from "@/hooks/use-auto-refresh";
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle";
import { Page } from "@/components/ui/page";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const EVENT_TYPES = [
  { value: "session", label: "Session" },
  { value: "notification", label: "Notification" },
  { value: "error", label: "Error" },
] as const;

// ---------------------------------------------------------------------------
// TimelinePage
// ---------------------------------------------------------------------------

export default function TimelinePage() {
  const [selectedButlers, setSelectedButlers] = useState<string[]>([]);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const autoRefreshControl = useAutoRefresh(10_000);
  const [allEvents, setAllEvents] = useState<TimelineEvent[]>([]);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [hasMore, setHasMore] = useState(false);

  // Fetch butler names for the filter
  const { data: butlersResponse } = useButlers();
  const butlerNames = butlersResponse?.data?.map((b) => b.name) ?? [];

  // Fetch timeline data
  const { data: response, isLoading, isError, refetch } = useTimeline({
    limit: PAGE_SIZE,
    butler: selectedButlers.length > 0 ? selectedButlers : undefined,
    event_type: selectedTypes.length > 0 ? selectedTypes : undefined,
    before: cursor,
  }, { refetchInterval: autoRefreshControl.refetchInterval });

  // Merge new data with accumulated events
  const currentEvents = useMemo(
    () =>
      cursor === undefined
        ? response?.data ?? []
        : [...allEvents, ...(response?.data ?? [])],
    [cursor, response, allEvents],
  );

  const currentHasMore = response?.meta?.has_more ?? hasMore;

  // Toggle butler filter
  function toggleButler(name: string) {
    setCursor(undefined);
    setAllEvents([]);
    setSelectedButlers((prev) =>
      prev.includes(name) ? prev.filter((b) => b !== name) : [...prev, name],
    );
  }

  // Toggle event type filter
  function toggleType(type: string) {
    setCursor(undefined);
    setAllEvents([]);
    setSelectedTypes((prev) =>
      prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type],
    );
  }

  // Load more via cursor pagination
  const handleLoadMore = useCallback(() => {
    if (!response?.meta?.cursor) return;
    setAllEvents(currentEvents);
    setHasMore(currentHasMore);
    setCursor(response.meta.cursor);
  }, [response, currentEvents, currentHasMore]);

  // Display events
  const displayEvents = cursor === undefined ? (response?.data ?? []) : currentEvents;

  return (
    <Page
      archetype="list"
      title="Timeline"
      description="Unified event stream across all butlers."
      actions={
        <AutoRefreshToggle
          enabled={autoRefreshControl.enabled}
          interval={autoRefreshControl.interval}
          onToggle={autoRefreshControl.setEnabled}
          onIntervalChange={autoRefreshControl.setInterval}
        />
      }
    >
      {/* Filters */}
      <Card>
        <CardContent className="py-4">
          <div className="space-y-3">
            {/* Butler filter */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">
                Filter by butler
              </p>
              <div className="flex flex-wrap gap-1.5">
                {butlerNames.length === 0 && (
                  <span className="text-xs text-muted-foreground italic">
                    No butlers available
                  </span>
                )}
                {butlerNames.map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => toggleButler(name)}
                  >
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

            {/* Event type filter */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-2">
                Filter by event type
              </p>
              <div className="flex flex-wrap gap-1.5">
                {EVENT_TYPES.map(({ value, label }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleType(value)}
                  >
                    <Badge
                      variant={selectedTypes.includes(value) ? "default" : "outline"}
                      className={cn(
                        "cursor-pointer transition-colors",
                        selectedTypes.includes(value) && "bg-primary text-primary-foreground",
                      )}
                    >
                      {label}
                    </Badge>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Timeline */}
      <Card>
        <CardContent>
          <UnifiedTimeline
            events={displayEvents}
            isLoading={isLoading && cursor === undefined}
            isError={isError && displayEvents.length === 0}
            onRetry={() => void refetch()}
            onLoadMore={handleLoadMore}
            hasMore={currentHasMore}
          />
        </CardContent>
      </Card>
    </Page>
  );
}
