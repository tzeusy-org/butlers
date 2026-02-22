import { useEffect, useMemo } from "react";
import {
  addDays,
  addMonths,
  addWeeks,
  format,
  isSameMonth,
  isValid,
  parseISO,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { useSearchParams } from "react-router";

import type {
  CalendarWorkspaceSourceFreshness,
  CalendarWorkspaceView,
  UnifiedCalendarEntry,
} from "@/api/types.ts";
import { useCalendarWorkspace, useCalendarWorkspaceMeta } from "@/hooks/use-calendar-workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type CalendarRange = "month" | "week" | "day" | "list";

type SyncBadgeVariant = "default" | "secondary" | "destructive" | "outline";

const DEFAULT_RANGE: CalendarRange = "week";

const VIEW_OPTIONS: Array<{ value: CalendarWorkspaceView; label: string }> = [
  { value: "user", label: "User" },
  { value: "butler", label: "Butler" },
];

const RANGE_OPTIONS: Array<{ value: CalendarRange; label: string }> = [
  { value: "month", label: "Month" },
  { value: "week", label: "Week" },
  { value: "day", label: "Day" },
  { value: "list", label: "List" },
];

function parseView(raw: string | null): CalendarWorkspaceView {
  return raw === "butler" ? "butler" : "user";
}

function parseRange(raw: string | null): CalendarRange {
  if (raw === "month" || raw === "week" || raw === "day" || raw === "list") {
    return raw;
  }
  return DEFAULT_RANGE;
}

function parseAnchor(raw: string | null): Date {
  if (!raw) return new Date();
  const parsed = parseISO(raw);
  return isValid(parsed) ? parsed : new Date();
}

function serializeAnchor(value: Date): string {
  return format(value, "yyyy-MM-dd");
}

function computeWindow(range: CalendarRange, anchor: Date): { start: Date; end: Date } {
  switch (range) {
    case "month": {
      const start = startOfMonth(anchor);
      return { start, end: addMonths(start, 1) };
    }
    case "day": {
      const start = startOfDay(anchor);
      return { start, end: addDays(start, 1) };
    }
    case "list": {
      const start = startOfDay(anchor);
      return { start, end: addDays(start, 30) };
    }
    case "week":
    default: {
      const start = startOfWeek(anchor, { weekStartsOn: 1 });
      return { start, end: addWeeks(start, 1) };
    }
  }
}

function shiftAnchor(anchor: Date, range: CalendarRange, direction: -1 | 1): Date {
  switch (range) {
    case "month":
      return addMonths(anchor, direction);
    case "week":
      return addWeeks(anchor, direction);
    case "list":
      return addDays(anchor, direction * 30);
    case "day":
    default:
      return addDays(anchor, direction);
  }
}

function windowLabel(range: CalendarRange, start: Date, end: Date): string {
  if (range === "month") {
    return format(start, "MMMM yyyy");
  }
  if (range === "day") {
    return format(start, "EEE, MMM d, yyyy");
  }
  return `${format(start, "MMM d, yyyy")} – ${format(addDays(end, -1), "MMM d, yyyy")}`;
}

function formatEntryWindow(entry: UnifiedCalendarEntry): string {
  if (entry.all_day) return "All day";
  const start = new Date(entry.start_at);
  const end = new Date(entry.end_at);
  return `${format(start, "MMM d, HH:mm")} - ${format(end, "HH:mm")}`;
}

function syncBadgeVariant(syncState: string): SyncBadgeVariant {
  if (syncState === "fresh") return "secondary";
  if (syncState === "failed") return "destructive";
  if (syncState === "syncing") return "default";
  return "outline";
}

function sourceName(source: CalendarWorkspaceSourceFreshness): string {
  return source.display_name || source.calendar_id || source.source_key;
}

export default function CalendarWorkspacePage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const view = parseView(searchParams.get("view"));
  const range = parseRange(searchParams.get("range"));
  const anchor = parseAnchor(searchParams.get("anchor"));
  const anchorParam = serializeAnchor(anchor);

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const { start, end } = useMemo(() => computeWindow(range, anchor), [range, anchorParam]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;

    if (next.get("view") !== view) {
      next.set("view", view);
      changed = true;
    }
    if (next.get("range") !== range) {
      next.set("range", range);
      changed = true;
    }
    if (next.get("anchor") !== anchorParam) {
      next.set("anchor", anchorParam);
      changed = true;
    }

    if (changed) {
      setSearchParams(next, { replace: true });
    }
  }, [anchorParam, range, searchParams, setSearchParams, view]);

  const workspaceQuery = useCalendarWorkspace({
    view,
    start: start.toISOString(),
    end: end.toISOString(),
    timezone,
  });
  const metaQuery = useCalendarWorkspaceMeta();

  const entries = useMemo(() => {
    const rows = workspaceQuery.data?.data.entries ?? [];
    return [...rows].sort((a, b) => a.start_at.localeCompare(b.start_at));
  }, [workspaceQuery.data?.data.entries]);

  const sourceFreshness = workspaceQuery.data?.data.source_freshness ?? [];
  const connectedSources =
    sourceFreshness.length > 0
      ? sourceFreshness
      : (metaQuery.data?.data.connected_sources ?? []);
  const lanes =
    workspaceQuery.data?.data.lanes.length
      ? workspaceQuery.data.data.lanes
      : (metaQuery.data?.data.lane_definitions ?? []);

  const entriesByDay = useMemo(() => {
    const buckets = new Map<string, UnifiedCalendarEntry[]>();
    entries.forEach((entry) => {
      const key = format(new Date(entry.start_at), "yyyy-MM-dd");
      const bucket = buckets.get(key) ?? [];
      bucket.push(entry);
      buckets.set(key, bucket);
    });
    return buckets;
  }, [entries]);

  const monthDays = useMemo(() => {
    if (range !== "month") return [] as Date[];
    const gridStart = startOfWeek(start, { weekStartsOn: 1 });
    return Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));
  }, [range, start]);

  function updateQuery(nextValues: {
    view?: CalendarWorkspaceView;
    range?: CalendarRange;
    anchor?: Date;
  }) {
    const next = new URLSearchParams(searchParams);
    if (nextValues.view) next.set("view", nextValues.view);
    if (nextValues.range) next.set("range", nextValues.range);
    if (nextValues.anchor) next.set("anchor", serializeAnchor(nextValues.anchor));
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Calendar Workspace</h1>
          <p className="text-muted-foreground mt-1">
            Unified user and butler calendar surface backed by the workspace projection APIs.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{timezone}</Badge>
          <Badge variant="outline">{entries.length} entries</Badge>
        </div>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">View</span>
            {VIEW_OPTIONS.map((option) => (
              <Button
                key={option.value}
                type="button"
                size="sm"
                variant={view === option.value ? "default" : "outline"}
                aria-pressed={view === option.value}
                onClick={() => updateQuery({ view: option.value })}
              >
                {option.label}
              </Button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Range</span>
            {RANGE_OPTIONS.map((option) => (
              <Button
                key={option.value}
                type="button"
                size="sm"
                variant={range === option.value ? "default" : "outline"}
                aria-pressed={range === option.value}
                onClick={() => updateQuery({ range: option.value })}
              >
                {option.label}
              </Button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label="Previous range"
              onClick={() => updateQuery({ anchor: shiftAnchor(anchor, range, -1) })}
            >
              Prev
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label="Jump to today"
              onClick={() => updateQuery({ anchor: new Date() })}
            >
              Today
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-label="Next range"
              onClick={() => updateQuery({ anchor: shiftAnchor(anchor, range, 1) })}
            >
              Next
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Card>
          <CardHeader>
            <CardTitle>{windowLabel(range, start, end)}</CardTitle>
            <CardDescription>
              {view === "user" ? "User calendar view" : "Butler lane view"} • {range} mode
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {workspaceQuery.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading calendar workspace...</p>
            ) : workspaceQuery.isError ? (
              <p className="text-sm text-destructive">
                Failed to load calendar workspace. {workspaceQuery.error instanceof Error
                  ? workspaceQuery.error.message
                  : "Unknown error"}
              </p>
            ) : entries.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No events in the selected range.
              </p>
            ) : range === "month" ? (
              <div className="space-y-2">
                <div className="grid grid-cols-7 gap-2 text-xs font-medium text-muted-foreground">
                  {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label) => (
                    <div key={label} className="px-2">
                      {label}
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-7 gap-2">
                  {monthDays.map((day) => {
                    const key = format(day, "yyyy-MM-dd");
                    const dayEntries = entriesByDay.get(key) ?? [];
                    return (
                      <div
                        key={key}
                        className={cn(
                          "rounded-md border border-border p-2",
                          !isSameMonth(day, start) && "bg-muted/30 text-muted-foreground",
                        )}
                      >
                        <p className="text-xs font-medium">{format(day, "d")}</p>
                        <div className="mt-1 space-y-1">
                          {dayEntries.slice(0, 2).map((entry) => (
                            <p
                              key={entry.entry_id}
                              className="truncate rounded bg-accent/50 px-1 py-0.5 text-xs"
                              title={entry.title}
                            >
                              {entry.title}
                            </p>
                          ))}
                          {dayEntries.length > 2 ? (
                            <p className="text-[11px] text-muted-foreground">
                              +{dayEntries.length - 2} more
                            </p>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((entry) => (
                    <TableRow key={entry.entry_id}>
                      <TableCell>{formatEntryWindow(entry)}</TableCell>
                      <TableCell>{entry.title}</TableCell>
                      <TableCell>{entry.butler_name ?? entry.source_key}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{entry.status}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Sources</CardTitle>
              <CardDescription>Workspace freshness and connected calendars</CardDescription>
            </CardHeader>
            <CardContent>
              {metaQuery.isLoading && connectedSources.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading source metadata...</p>
              ) : connectedSources.length === 0 ? (
                <p className="text-sm text-muted-foreground">No connected sources reported.</p>
              ) : (
                <div className="space-y-2">
                  {connectedSources.map((source) => (
                    <div key={source.source_key} className="rounded-md border border-border p-2">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium" title={sourceName(source)}>
                          {sourceName(source)}
                        </p>
                        <Badge variant={syncBadgeVariant(source.sync_state)}>{source.sync_state}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {source.lane} • {source.provider ?? source.source_kind}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Butler Lanes</CardTitle>
              <CardDescription>Lane metadata for butler-view grouping</CardDescription>
            </CardHeader>
            <CardContent>
              {lanes.length === 0 ? (
                <p className="text-sm text-muted-foreground">No lane metadata available.</p>
              ) : (
                <div className="space-y-2">
                  {lanes.map((lane) => (
                    <div key={lane.lane_id} className="rounded-md border border-border p-2">
                      <p className="text-sm font-medium">{lane.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {lane.source_keys.length} source{lane.source_keys.length === 1 ? "" : "s"}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
