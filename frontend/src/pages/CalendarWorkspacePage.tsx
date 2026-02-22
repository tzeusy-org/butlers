import { useEffect, useMemo, useState } from "react";
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
import { toast } from "sonner";
import { useSearchParams } from "react-router";

import type {
  CalendarWorkspaceSourceFreshness,
  CalendarWorkspaceUserMutationAction,
  CalendarWorkspaceView,
  CalendarWorkspaceWritableCalendar,
  UnifiedCalendarEntry,
} from "@/api/types.ts";
import {
  useCalendarWorkspace,
  useCalendarWorkspaceMeta,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  useSyncCalendarWorkspace,
} from "@/hooks/use-calendar-workspace";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type CalendarRange = "month" | "week" | "day" | "list";

type SyncBadgeVariant = "default" | "secondary" | "destructive" | "outline";
type ButlerEventKind = "scheduled_task" | "butler_reminder";
type RecurrenceFrequency = "NONE" | "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";

type UserEventDialogMode = "create" | "edit";
type ButlerEventDialogMode = "create" | "edit";

interface UserEventFormState {
  sourceKey: string;
  title: string;
  startAtLocal: string;
  endAtLocal: string;
  timezone: string;
  description: string;
  location: string;
}

interface ButlerEventDraft {
  butlerName: string;
  eventKind: ButlerEventKind;
  title: string;
  startAtLocal: string;
  endAtLocal: string;
  timezone: string;
  recurrenceFrequency: RecurrenceFrequency;
  hasUntilAt: boolean;
  untilAtLocal: string;
  cron: string;
}

interface ButlerLaneRows {
  laneId: string;
  butlerName: string;
  title: string;
  entries: UnifiedCalendarEntry[];
}

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

function formatLaneTitle(butlerName: string): string {
  return butlerName.replace(/[_-]/g, " ").replace(/\b\w/g, (s) => s.toUpperCase());
}

function formatLocalDateTimeInput(value: Date): string {
  return format(value, "yyyy-MM-dd'T'HH:mm");
}

function parseLocalDateTimeInput(value: string): Date | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    return null;
  }
  return parsed;
}

function toRRuleUntilToken(value: Date): string {
  const compact = value.toISOString().replace(/[-:]/g, "").replace(".000", "");
  return `${compact.slice(0, 15)}Z`;
}

function parseRRuleUntilToken(token: string): Date | null {
  const dateTimeMatch = token.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (dateTimeMatch) {
    const [, y, m, d, hh, mm, ss] = dateTimeMatch;
    return new Date(Date.UTC(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm), Number(ss)));
  }

  const dateOnlyMatch = token.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (dateOnlyMatch) {
    const [, y, m, d] = dateOnlyMatch;
    return new Date(Date.UTC(Number(y), Number(m) - 1, Number(d), 0, 0, 0));
  }

  return null;
}

function buildRRule(
  frequency: RecurrenceFrequency,
  untilAt: Date | null,
): string | null {
  if (frequency === "NONE") {
    return null;
  }
  const parts = [`FREQ=${frequency}`];
  if (untilAt !== null) {
    parts.push(`UNTIL=${toRRuleUntilToken(untilAt)}`);
  }
  return `RRULE:${parts.join(";")}`;
}

function parseRRule(
  rule: string | null,
  untilAtIso: string | null,
): { frequency: RecurrenceFrequency; untilAtLocal: string; hasUntilAt: boolean } {
  if (!rule) {
    if (untilAtIso) {
      const until = new Date(untilAtIso);
      if (Number.isFinite(until.getTime())) {
        return {
          frequency: "NONE",
          untilAtLocal: formatLocalDateTimeInput(until),
          hasUntilAt: true,
        };
      }
    }
    return { frequency: "NONE", untilAtLocal: "", hasUntilAt: false };
  }

  const upper = rule.toUpperCase();
  const freqMatch = upper.match(/FREQ=([A-Z]+)/);
  const untilMatch = upper.match(/UNTIL=([0-9TZ]+)/);

  let frequency: RecurrenceFrequency = "NONE";
  if (
    freqMatch?.[1] === "DAILY" ||
    freqMatch?.[1] === "WEEKLY" ||
    freqMatch?.[1] === "MONTHLY" ||
    freqMatch?.[1] === "YEARLY"
  ) {
    frequency = freqMatch[1];
  }

  const untilCandidate = untilMatch?.[1]
    ? parseRRuleUntilToken(untilMatch[1])
    : untilAtIso
      ? new Date(untilAtIso)
      : null;

  if (untilCandidate && Number.isFinite(untilCandidate.getTime())) {
    return {
      frequency,
      untilAtLocal: formatLocalDateTimeInput(untilCandidate),
      hasUntilAt: true,
    };
  }

  return {
    frequency,
    untilAtLocal: "",
    hasUntilAt: false,
  };
}

function formatStaleness(stalenessMs: number | null): string {
  if (stalenessMs == null) {
    return "staleness unknown";
  }
  if (stalenessMs < 1_000) {
    return "fresh";
  }
  const seconds = Math.floor(stalenessMs / 1_000);
  if (seconds < 60) {
    return `${seconds}s stale`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m stale`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h stale`;
  }
  return `${Math.floor(hours / 24)}d stale`;
}

function formatOptionalTimestamp(value: string | null): string | null {
  if (!value) return null;
  const parsed = parseISO(value);
  if (!isValid(parsed)) return null;
  return format(parsed, "MMM d, HH:mm");
}

function toLocalDateTimeValue(value: string): string {
  const parsed = parseISO(value);
  if (!isValid(parsed)) {
    return "";
  }
  return format(parsed, "yyyy-MM-dd'T'HH:mm");
}

function toIsoFromLocalDateTime(value: string): string | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toISOString();
}

function maybeText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function buildRequestId(action: CalendarWorkspaceUserMutationAction): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `calendar-${action}-${crypto.randomUUID()}`;
  }
  return `calendar-${action}-${Date.now()}`;
}

function defaultFormWindow(anchor: Date): { startAtLocal: string; endAtLocal: string } {
  const start = new Date(anchor);
  start.setMinutes(0, 0, 0);
  if (start.getTime() < Date.now()) {
    start.setHours(start.getHours() + 1);
  }
  const end = new Date(start.getTime() + 30 * 60 * 1_000);
  return {
    startAtLocal: format(start, "yyyy-MM-dd'T'HH:mm"),
    endAtLocal: format(end, "yyyy-MM-dd'T'HH:mm"),
  };
}

function createDefaultButlerDraft(timezone: string, butlerName: string): ButlerEventDraft {
  const base = new Date();
  base.setSeconds(0, 0);
  base.setMinutes(0);
  base.setHours(base.getHours() + 1);
  const end = addDays(base, 0);
  end.setMinutes(base.getMinutes() + 15);

  return {
    butlerName,
    eventKind: "butler_reminder",
    title: "",
    startAtLocal: formatLocalDateTimeInput(base),
    endAtLocal: formatLocalDateTimeInput(end),
    timezone,
    recurrenceFrequency: "NONE",
    hasUntilAt: false,
    untilAtLocal: "",
    cron: "",
  };
}

function inferEventKind(entry: UnifiedCalendarEntry): ButlerEventKind {
  return entry.source_type === "scheduled_task" ? "scheduled_task" : "butler_reminder";
}

function resolveButlerEventTarget(
  entry: UnifiedCalendarEntry,
): { eventId: string; sourceHint: ButlerEventKind } | null {
  if (entry.source_type === "scheduled_task" && entry.schedule_id) {
    return {
      eventId: entry.schedule_id,
      sourceHint: "scheduled_task",
    };
  }

  if (entry.source_type === "butler_reminder" && entry.reminder_id) {
    return {
      eventId: entry.reminder_id,
      sourceHint: "butler_reminder",
    };
  }

  const metadata = entry.metadata ?? {};
  const originRef =
    typeof metadata.origin_ref === "string" && metadata.origin_ref.trim().length > 0
      ? metadata.origin_ref.trim()
      : null;
  if (!originRef) {
    return null;
  }

  return {
    eventId: originRef,
    sourceHint: entry.source_type === "scheduled_task" ? "scheduled_task" : "butler_reminder",
  };
}

function isPausedEntry(entry: UnifiedCalendarEntry): boolean {
  const normalized = entry.status.toLowerCase();
  return normalized === "paused" || normalized === "inactive";
}

function newButlerRequestId(prefix: string): string {
  return `calendar-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function CalendarWorkspacePage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const view = parseView(searchParams.get("view"));
  const range = parseRange(searchParams.get("range"));
  const anchor = parseAnchor(searchParams.get("anchor"));
  const anchorParam = serializeAnchor(anchor);
  const selectedSourceKey = searchParams.get("source") ?? "all";
  const selectedCalendarId = searchParams.get("calendar") ?? "all";

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const { start, end } = useMemo(() => computeWindow(range, anchor), [range, anchorParam]);

  const metaQuery = useCalendarWorkspaceMeta();
  const connectedSources = metaQuery.data?.data.connected_sources ?? [];
  const writableCalendars = metaQuery.data?.data.writable_calendars ?? [];
  const defaultTimezone = metaQuery.data?.data.default_timezone || timezone;

  const userSources = useMemo(
    () => connectedSources.filter((source) => source.lane === "user"),
    [connectedSources],
  );

  const sourceFilters = useMemo(() => {
    let filtered = userSources;
    if (selectedCalendarId !== "all") {
      filtered = filtered.filter((source) => source.calendar_id === selectedCalendarId);
    }
    if (selectedSourceKey !== "all") {
      filtered = filtered.filter((source) => source.source_key === selectedSourceKey);
    }
    return filtered.map((source) => source.source_key);
  }, [selectedCalendarId, selectedSourceKey, userSources]);

  const sourcesForQuery =
    view === "user" && (selectedSourceKey !== "all" || selectedCalendarId !== "all")
      ? sourceFilters
      : undefined;

  const workspaceQuery = useCalendarWorkspace({
    view,
    start: start.toISOString(),
    end: end.toISOString(),
    timezone,
    sources: sourcesForQuery,
  });

  const syncMutation = useSyncCalendarWorkspace();
  const butlerMutation = useMutateCalendarWorkspaceButlerEvent();
  const userEventMutation = useMutateCalendarWorkspaceUserEvent();

  const [syncingSourceKey, setSyncingSourceKey] = useState<string | null>(null);
  const [userEventDialogOpen, setUserEventDialogOpen] = useState(false);
  const [userEventDialogMode, setUserEventDialogMode] = useState<UserEventDialogMode>("create");
  const [activeUserEntry, setActiveUserEntry] = useState<UnifiedCalendarEntry | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<UnifiedCalendarEntry | null>(null);
  const [userEventForm, setUserEventForm] = useState<UserEventFormState | null>(null);
  const [butlerEventDialogOpen, setButlerEventDialogOpen] = useState(false);
  const [butlerEventDialogMode, setButlerEventDialogMode] =
    useState<ButlerEventDialogMode>("create");
  const [butlerEventDraft, setButlerEventDraft] = useState<ButlerEventDraft | null>(null);
  const [editingButlerEntry, setEditingButlerEntry] = useState<UnifiedCalendarEntry | null>(null);

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

    if (view !== "user") {
      if (next.has("source")) {
        next.delete("source");
        changed = true;
      }
      if (next.has("calendar")) {
        next.delete("calendar");
        changed = true;
      }
    }

    if (changed) {
      setSearchParams(next, { replace: true });
    }
  }, [anchorParam, range, searchParams, setSearchParams, view]);

  useEffect(() => {
    if (view !== "user") {
      return;
    }

    const sourceValid =
      selectedSourceKey === "all" ||
      userSources.some((source) => source.source_key === selectedSourceKey);
    const calendarValid =
      selectedCalendarId === "all" ||
      userSources.some((source) => source.calendar_id === selectedCalendarId);

    if (!sourceValid || !calendarValid) {
      updateQuery({
        source: sourceValid ? selectedSourceKey : "all",
        calendar: calendarValid ? selectedCalendarId : "all",
      });
      return;
    }

    if (selectedSourceKey !== "all" && selectedCalendarId !== "all") {
      const source = userSources.find((item) => item.source_key === selectedSourceKey);
      if (source?.calendar_id !== selectedCalendarId) {
        updateQuery({ source: "all" });
      }
    }
  }, [selectedCalendarId, selectedSourceKey, userSources, view]);

  const entries = useMemo(() => {
    const rows = workspaceQuery.data?.data.entries ?? [];
    return [...rows].sort((a, b) => a.start_at.localeCompare(b.start_at));
  }, [workspaceQuery.data?.data.entries]);

  const sourceFreshness = workspaceQuery.data?.data.source_freshness ?? [];
  const laneSources =
    sourceFreshness.length > 0
      ? sourceFreshness
      : connectedSources;
  const visibleSources = laneSources.filter((source) => source.lane === view);

  const sourceByKey = useMemo(() => {
    const lookup = new Map<string, CalendarWorkspaceSourceFreshness>();
    connectedSources.forEach((source) => {
      lookup.set(source.source_key, source);
    });
    return lookup;
  }, [connectedSources]);

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

  const userEditableEntries = useMemo(
    () =>
      entries.filter(
        (entry) =>
          entry.view === "user" &&
          entry.source_type === "provider_event" &&
          !!entry.provider_event_id &&
          entry.editable,
      ),
    [entries],
  );

  const upcomingEntries = useMemo(
    () => userEditableEntries.slice(0, 8),
    [userEditableEntries],
  );

  const calendarFilterOptions = useMemo(() => {
    const deduped = new Map<string, string>();
    userSources.forEach((source) => {
      if (!source.calendar_id) return;
      if (!deduped.has(source.calendar_id)) {
        deduped.set(source.calendar_id, source.display_name || source.calendar_id);
      }
    });
    return Array.from(deduped.entries()).map(([calendarId, label]) => ({ calendarId, label }));
  }, [userSources]);

  const butlerLaneRows = useMemo(() => {
    const laneMap = new Map<string, { butlerName: string; title: string }>();

    lanes.forEach((lane) => {
      laneMap.set(lane.lane_id, {
        butlerName: lane.butler_name,
        title: lane.title,
      });
    });

    entries.forEach((entry) => {
      if (!entry.butler_name) {
        return;
      }
      if (!laneMap.has(entry.butler_name)) {
        laneMap.set(entry.butler_name, {
          butlerName: entry.butler_name,
          title: formatLaneTitle(entry.butler_name),
        });
      }
    });

    const grouped = new Map<string, UnifiedCalendarEntry[]>();
    entries.forEach((entry) => {
      if (!entry.butler_name) {
        return;
      }
      const key = entry.butler_name;
      const bucket = grouped.get(key) ?? [];
      bucket.push(entry);
      grouped.set(key, bucket);
    });

    return [...laneMap.entries()]
      .map(([laneId, descriptor]): ButlerLaneRows => ({
        laneId,
        butlerName: descriptor.butlerName,
        title: descriptor.title,
        entries: [...(grouped.get(descriptor.butlerName) ?? [])].sort((a, b) =>
          a.start_at.localeCompare(b.start_at),
        ),
      }))
      .sort((a, b) => a.title.localeCompare(b.title));
  }, [entries, lanes]);

  const availableButlers = useMemo(() => {
    const names = new Set<string>();
    butlerLaneRows.forEach((lane) => {
      if (lane.butlerName) {
        names.add(lane.butlerName);
      }
    });
    connectedSources.forEach((source) => {
      if (source.lane === "butler" && source.butler_name) {
        names.add(source.butler_name);
      }
    });
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [butlerLaneRows, connectedSources]);

  function updateQuery(nextValues: {
    view?: CalendarWorkspaceView;
    range?: CalendarRange;
    anchor?: Date;
    source?: string;
    calendar?: string;
  }) {
    const next = new URLSearchParams(searchParams);

    if (nextValues.view) next.set("view", nextValues.view);
    if (nextValues.range) next.set("range", nextValues.range);
    if (nextValues.anchor) next.set("anchor", serializeAnchor(nextValues.anchor));

    if (nextValues.source !== undefined) {
      if (!nextValues.source || nextValues.source === "all") {
        next.delete("source");
      } else {
        next.set("source", nextValues.source);
      }
    }

    if (nextValues.calendar !== undefined) {
      if (!nextValues.calendar || nextValues.calendar === "all") {
        next.delete("calendar");
      } else {
        next.set("calendar", nextValues.calendar);
      }
    }

    setSearchParams(next, { replace: true });
  }

  function resolveSourceForForm(sourceKey: string): CalendarWorkspaceWritableCalendar | undefined {
    return writableCalendars.find((calendar) => calendar.source_key === sourceKey);
  }

  function resolveEntryOwner(entry: UnifiedCalendarEntry): {
    butlerName: string | null;
    calendarId: string | null;
  } {
    const source = sourceByKey.get(entry.source_key);
    const writable = resolveSourceForForm(entry.source_key);

    const butlerName =
      entry.butler_name?.trim() ||
      source?.butler_name?.trim() ||
      writable?.butler_name?.trim() ||
      null;

    const calendarId =
      entry.calendar_id ||
      source?.calendar_id ||
      writable?.calendar_id ||
      null;

    return { butlerName, calendarId };
  }

  function openUserCreateDialog() {
    if (writableCalendars.length === 0) {
      toast.error("No writable calendar sources are available for user events.");
      return;
    }

    const preferredSource =
      selectedSourceKey !== "all" && writableCalendars.some((c) => c.source_key === selectedSourceKey)
        ? selectedSourceKey
        : writableCalendars[0].source_key;
    const { startAtLocal, endAtLocal } = defaultFormWindow(anchor);

    setUserEventDialogMode("create");
    setActiveUserEntry(null);
    setUserEventForm({
      sourceKey: preferredSource,
      title: "",
      startAtLocal,
      endAtLocal,
      timezone: defaultTimezone,
      description: "",
      location: "",
    });
    setUserEventDialogOpen(true);
  }

  function openUserEditDialog(entry: UnifiedCalendarEntry) {
    if (!entry.provider_event_id) {
      toast.error("This entry cannot be edited because it has no provider event id.");
      return;
    }

    const fallbackSource = writableCalendars[0]?.source_key ?? entry.source_key;

    setUserEventDialogMode("edit");
    setActiveUserEntry(entry);
    setUserEventForm({
      sourceKey: writableCalendars.some((calendar) => calendar.source_key === entry.source_key)
        ? entry.source_key
        : fallbackSource,
      title: entry.title,
      startAtLocal: toLocalDateTimeValue(entry.start_at),
      endAtLocal: toLocalDateTimeValue(entry.end_at),
      timezone: entry.timezone || defaultTimezone,
      description: maybeText(entry.metadata.description),
      location: maybeText(entry.metadata.location),
    });
    setUserEventDialogOpen(true);
  }

  async function handleSyncAll() {
    try {
      const result = await syncMutation.mutateAsync({ all: true });
      toast.success(`Sync triggered for ${result.data.triggered_count} source(s).`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to trigger sync.");
    }
  }

  async function handleSyncSource(source: CalendarWorkspaceSourceFreshness) {
    setSyncingSourceKey(source.source_key);
    try {
      const result = await syncMutation.mutateAsync({
        source_key: source.source_key,
        butler: source.butler_name || undefined,
      });
      const target = result.data.targets[0];
      if (target?.status === "failed") {
        toast.error(target.error || "Source sync failed.");
      } else {
        toast.success(target?.detail || `Sync triggered for ${sourceName(source)}.`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to trigger source sync.");
    } finally {
      setSyncingSourceKey(null);
    }
  }

  async function submitUserEventForm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!userEventForm) {
      return;
    }

    const startIso = toIsoFromLocalDateTime(userEventForm.startAtLocal);
    const endIso = toIsoFromLocalDateTime(userEventForm.endAtLocal);
    const trimmedTitle = userEventForm.title.trim();

    if (!trimmedTitle) {
      toast.error("Title is required.");
      return;
    }
    if (!startIso || !endIso) {
      toast.error("Start and end times must be valid.");
      return;
    }
    if (new Date(endIso) <= new Date(startIso)) {
      toast.error("End time must be after start time.");
      return;
    }

    const selectedCalendar = resolveSourceForForm(userEventForm.sourceKey);
    const fallbackOwner = activeUserEntry ? resolveEntryOwner(activeUserEntry) : null;
    const butlerName = selectedCalendar?.butler_name || fallbackOwner?.butlerName || null;
    const calendarId = selectedCalendar?.calendar_id || fallbackOwner?.calendarId || null;

    if (!butlerName) {
      toast.error("Could not resolve owning butler for this calendar source.");
      return;
    }

    const action: CalendarWorkspaceUserMutationAction =
      userEventDialogMode === "create" ? "create" : "update";

    const payload: Record<string, unknown> = {
      title: trimmedTitle,
      start_at: startIso,
      end_at: endIso,
      timezone: userEventForm.timezone.trim() || defaultTimezone,
    };
    if (calendarId) {
      payload.calendar_id = calendarId;
    }

    const description = userEventForm.description.trim();
    if (description) {
      payload.description = description;
    }

    const location = userEventForm.location.trim();
    if (location) {
      payload.location = location;
    }

    if (action === "update") {
      if (!activeUserEntry?.provider_event_id) {
        toast.error("Event id is missing for update.");
        return;
      }
      payload.event_id = activeUserEntry.provider_event_id;
    }

    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: butlerName,
        action,
        request_id: buildRequestId(action),
        payload,
      });
      const status = maybeText(result.data.result?.status);
      toast.success(
        action === "create"
          ? status
            ? `Created event (${status}).`
            : "Created calendar event."
          : status
            ? `Updated event (${status}).`
            : "Updated calendar event.",
      );
      setUserEventDialogOpen(false);
      setUserEventForm(null);
      setActiveUserEntry(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to save calendar event.");
    }
  }

  async function confirmDelete() {
    if (!deleteCandidate?.provider_event_id) {
      setDeleteCandidate(null);
      return;
    }

    const owner = resolveEntryOwner(deleteCandidate);
    if (!owner.butlerName) {
      toast.error("Could not resolve owning butler for this event.");
      return;
    }

    const payload: Record<string, unknown> = {
      event_id: deleteCandidate.provider_event_id,
    };
    if (owner.calendarId) {
      payload.calendar_id = owner.calendarId;
    }

    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: owner.butlerName,
        action: "delete",
        request_id: buildRequestId("delete"),
        payload,
      });
      const status = maybeText(result.data.result?.status);
      toast.success(status ? `Deleted event (${status}).` : "Deleted calendar event.");
      setDeleteCandidate(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to delete calendar event.");
    }
  }

  function openButlerCreateDialog(initialButler?: string) {
    const butlerName = initialButler ?? availableButlers[0] ?? "";
    setEditingButlerEntry(null);
    setButlerEventDialogMode("create");
    setButlerEventDraft(createDefaultButlerDraft(timezone, butlerName));
    setButlerEventDialogOpen(true);
  }

  function openButlerEditDialog(entry: UnifiedCalendarEntry) {
    const parsedRule = parseRRule(entry.rrule, entry.until_at);
    const startAt = new Date(entry.start_at);
    const endAt = new Date(entry.end_at);

    setEditingButlerEntry(entry);
    setButlerEventDialogMode("edit");
    setButlerEventDraft({
      butlerName: entry.butler_name ?? availableButlers[0] ?? "",
      eventKind: inferEventKind(entry),
      title: entry.title,
      startAtLocal: formatLocalDateTimeInput(startAt),
      endAtLocal: formatLocalDateTimeInput(endAt),
      timezone: entry.timezone || timezone,
      recurrenceFrequency: parsedRule.frequency,
      hasUntilAt: parsedRule.hasUntilAt,
      untilAtLocal: parsedRule.untilAtLocal,
      cron: entry.cron ?? "",
    });
    setButlerEventDialogOpen(true);
  }

  function closeButlerEventDialog(open: boolean) {
    setButlerEventDialogOpen(open);
    if (!open) {
      setEditingButlerEntry(null);
      setButlerEventDraft(null);
    }
  }

  function handleButlerToggle(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for toggle");
      return;
    }

    const enabled = isPausedEntry(entry);
    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "toggle",
        request_id: newButlerRequestId("toggle"),
        payload: {
          event_id: target.eventId,
          enabled,
          source_hint: target.sourceHint,
        },
      },
      {
        onSuccess: () => {
          toast.success(enabled ? "Event resumed" : "Event paused");
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Toggle failed");
        },
      },
    );
  }

  function handleButlerDelete(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for delete");
      return;
    }

    if (!globalThis.confirm(`Delete "${entry.title}"?`)) {
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "delete",
        request_id: newButlerRequestId("delete"),
        payload: {
          event_id: target.eventId,
          source_hint: target.sourceHint,
        },
      },
      {
        onSuccess: () => {
          toast.success("Event deleted");
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Delete failed");
        },
      },
    );
  }

  function handleSaveButlerEvent() {
    if (!butlerEventDraft) {
      return;
    }

    const startAt = parseLocalDateTimeInput(butlerEventDraft.startAtLocal);
    const endAt = parseLocalDateTimeInput(butlerEventDraft.endAtLocal);
    const untilAt = butlerEventDraft.hasUntilAt
      ? parseLocalDateTimeInput(butlerEventDraft.untilAtLocal)
      : null;

    if (!butlerEventDraft.butlerName.trim()) {
      toast.error("Select a butler lane before saving");
      return;
    }
    if (!butlerEventDraft.title.trim()) {
      toast.error("Title is required");
      return;
    }
    if (!startAt) {
      toast.error("Start time is required");
      return;
    }
    if (butlerEventDraft.eventKind === "scheduled_task") {
      if (!endAt) {
        toast.error("End time is required for scheduled events");
        return;
      }
      if (endAt <= startAt) {
        toast.error("End time must be after start time");
        return;
      }
      if (butlerEventDraft.recurrenceFrequency === "NONE" && !butlerEventDraft.cron.trim()) {
        toast.error("Scheduled events require either a recurrence frequency or cron expression");
        return;
      }
    }
    if (butlerEventDraft.hasUntilAt && !untilAt) {
      toast.error("Until boundary is invalid");
      return;
    }

    const recurrenceRule = buildRRule(butlerEventDraft.recurrenceFrequency, untilAt);
    const payload: Record<string, unknown> = {
      title: butlerEventDraft.title.trim(),
      start_at: startAt.toISOString(),
      timezone: butlerEventDraft.timezone.trim() || timezone,
      source_hint: butlerEventDraft.eventKind,
    };

    if (butlerEventDraft.eventKind === "scheduled_task") {
      payload.end_at = (endAt ?? addDays(startAt, 0)).toISOString();
      if (!recurrenceRule && butlerEventDraft.cron.trim()) {
        payload.cron = butlerEventDraft.cron.trim();
      }
    }

    if (recurrenceRule) {
      payload.recurrence_rule = recurrenceRule;
    }
    if (butlerEventDraft.hasUntilAt && untilAt) {
      payload.until_at = untilAt.toISOString();
    }

    const action: "create" | "update" = butlerEventDialogMode === "create" ? "create" : "update";
    if (action === "update") {
      if (!editingButlerEntry) {
        toast.error("Missing event context for update");
        return;
      }
      const target = resolveButlerEventTarget(editingButlerEntry);
      if (!target) {
        toast.error("Missing butler event linkage for update");
        return;
      }
      payload.event_id = target.eventId;
      payload.source_hint = target.sourceHint;
    }

    butlerMutation.mutate(
      {
        butler_name: butlerEventDraft.butlerName,
        action,
        request_id: newButlerRequestId(action),
        payload,
      },
      {
        onSuccess: () => {
          toast.success(action === "create" ? "Butler event created" : "Butler event updated");
          closeButlerEventDialog(false);
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Event mutation failed");
        },
      },
    );
  }

  const syncButtonLabel = syncMutation.isPending ? "Syncing..." : "Sync now";
  const canCreateUserEvents = view === "user" && writableCalendars.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Calendar Workspace</h1>
          <p className="text-muted-foreground mt-1">
            Unified user and butler calendar surface backed by workspace APIs.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {view === "butler" ? (
            <Button
              type="button"
              size="sm"
              onClick={() => openButlerCreateDialog()}
              disabled={butlerMutation.isPending}
            >
              Create Butler Event
            </Button>
          ) : null}
          <Badge variant="outline">{timezone}</Badge>
          <Badge variant="outline">{entries.length} entries</Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleSyncAll}
            disabled={syncMutation.isPending}
            aria-label="Sync all sources now"
          >
            {syncButtonLabel}
          </Button>
          {view === "user" ? (
            <Button
              type="button"
              size="sm"
              onClick={openUserCreateDialog}
              disabled={!canCreateUserEvents || userEventMutation.isPending}
              aria-label="Create user event"
            >
              Create Event
            </Button>
          ) : null}
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

          {view === "user" ? (
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor="calendar-filter" className="text-xs font-medium text-muted-foreground">
                Calendar
              </label>
              <select
                id="calendar-filter"
                className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 rounded-md border px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                value={selectedCalendarId}
                onChange={(event) =>
                  updateQuery({
                    calendar: event.target.value,
                    source: "all",
                  })
                }
              >
                <option value="all">All calendars</option>
                {calendarFilterOptions.map((option) => (
                  <option key={option.calendarId} value={option.calendarId}>
                    {option.label}
                  </option>
                ))}
              </select>

              <label htmlFor="source-filter" className="text-xs font-medium text-muted-foreground">
                Source
              </label>
              <select
                id="source-filter"
                className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 rounded-md border px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                value={selectedSourceKey}
                onChange={(event) => updateQuery({ source: event.target.value })}
              >
                <option value="all">All sources</option>
                {userSources
                  .filter((source) =>
                    selectedCalendarId === "all" ? true : source.calendar_id === selectedCalendarId,
                  )
                  .map((source) => (
                    <option key={source.source_key} value={source.source_key}>
                      {sourceName(source)}
                    </option>
                  ))}
              </select>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
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
              <p className="text-sm text-muted-foreground">No events in the selected range.</p>
            ) : view === "butler" ? (
              <div className="space-y-4">
                {butlerLaneRows.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No butler lanes found.</p>
                ) : (
                  butlerLaneRows.map((lane) => (
                    <div key={lane.laneId} className="rounded-md border border-border p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-semibold">{lane.title}</p>
                          <p className="text-xs text-muted-foreground">
                            {lane.entries.length} event{lane.entries.length === 1 ? "" : "s"}
                          </p>
                        </div>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => openButlerCreateDialog(lane.butlerName)}
                          disabled={butlerMutation.isPending}
                        >
                          Add event
                        </Button>
                      </div>
                      {lane.entries.length === 0 ? (
                        <p className="text-xs text-muted-foreground">No events in this lane.</p>
                      ) : (
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Time</TableHead>
                              <TableHead>Title</TableHead>
                              <TableHead>Type</TableHead>
                              <TableHead>Status</TableHead>
                              <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {lane.entries.map((entry) => (
                              <TableRow key={entry.entry_id}>
                                <TableCell>{formatEntryWindow(entry)}</TableCell>
                                <TableCell>{entry.title}</TableCell>
                                <TableCell>
                                  {entry.source_type === "scheduled_task" ? "Schedule" : "Reminder"}
                                </TableCell>
                                <TableCell>
                                  <Badge variant="outline">{entry.status}</Badge>
                                </TableCell>
                                <TableCell className="text-right">
                                  <div className="flex justify-end gap-2">
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="sm"
                                      onClick={() => openButlerEditDialog(entry)}
                                      disabled={butlerMutation.isPending}
                                    >
                                      Edit
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="sm"
                                      onClick={() => handleButlerToggle(entry)}
                                      disabled={butlerMutation.isPending}
                                    >
                                      {isPausedEntry(entry) ? "Resume" : "Pause"}
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="destructive"
                                      size="sm"
                                      onClick={() => handleButlerDelete(entry)}
                                      disabled={butlerMutation.isPending}
                                    >
                                      Delete
                                    </Button>
                                  </div>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                    </div>
                  ))
                )}
              </div>
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
                    {view === "user" ? <TableHead className="text-right">Actions</TableHead> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((entry) => {
                    const canMutate =
                      view === "user" &&
                      entry.source_type === "provider_event" &&
                      !!entry.provider_event_id &&
                      entry.editable;

                    return (
                      <TableRow key={entry.entry_id}>
                        <TableCell>{formatEntryWindow(entry)}</TableCell>
                        <TableCell>{entry.title}</TableCell>
                        <TableCell>{entry.butler_name ?? entry.source_key}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{entry.status}</Badge>
                        </TableCell>
                        {view === "user" ? (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-2">
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => openUserEditDialog(entry)}
                                disabled={!canMutate || userEventMutation.isPending}
                              >
                                Edit
                              </Button>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => setDeleteCandidate(entry)}
                                disabled={!canMutate || userEventMutation.isPending}
                              >
                                Delete
                              </Button>
                            </div>
                          </TableCell>
                        ) : null}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Sources</CardTitle>
              <CardDescription>Provider metadata, staleness, and sync controls</CardDescription>
            </CardHeader>
            <CardContent>
              {metaQuery.isLoading && visibleSources.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading source metadata...</p>
              ) : visibleSources.length === 0 ? (
                <p className="text-sm text-muted-foreground">No connected sources reported.</p>
              ) : (
                <div className="space-y-2">
                  {visibleSources.map((source) => (
                    <div key={source.source_key} className="rounded-md border border-border p-2">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium" title={sourceName(source)}>
                          {sourceName(source)}
                        </p>
                        <Badge variant={syncBadgeVariant(source.sync_state)}>{source.sync_state}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {source.lane} • {source.provider ?? source.source_kind}
                        {source.calendar_id ? ` • ${source.calendar_id}` : ""}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatStaleness(source.staleness_ms)}
                        {formatOptionalTimestamp(source.last_success_at)
                          ? ` • last success ${formatOptionalTimestamp(source.last_success_at)}`
                          : ""}
                      </p>
                      {source.last_error ? (
                        <p className="mt-1 text-xs text-destructive">{source.last_error}</p>
                      ) : null}
                      <div className="mt-2 flex justify-end">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() => handleSyncSource(source)}
                          disabled={syncMutation.isPending || !source.butler_name}
                        >
                          {syncingSourceKey === source.source_key ? "Syncing..." : "Sync now"}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {view === "user" ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Upcoming Events</CardTitle>
                <CardDescription>Editable provider events in this window</CardDescription>
              </CardHeader>
              <CardContent>
                {upcomingEntries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No editable provider events found.</p>
                ) : (
                  <div className="space-y-2">
                    {upcomingEntries.map((entry) => (
                      <div key={`upcoming-${entry.entry_id}`} className="rounded-md border border-border p-2">
                        <p className="text-sm font-medium">{entry.title}</p>
                        <p className="text-xs text-muted-foreground">{formatEntryWindow(entry)}</p>
                        <div className="mt-2 flex justify-end gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => openUserEditDialog(entry)}
                            disabled={userEventMutation.isPending}
                          >
                            Edit
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => setDeleteCandidate(entry)}
                            disabled={userEventMutation.isPending}
                          >
                            Delete
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Butler Lanes</CardTitle>
                <CardDescription>Lane metadata for butler-view grouping</CardDescription>
              </CardHeader>
              <CardContent>
                {butlerLaneRows.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No lane metadata available.</p>
                ) : (
                  <div className="space-y-2">
                    {butlerLaneRows.map((lane) => (
                      <div key={lane.laneId} className="rounded-md border border-border p-2">
                        <p className="text-sm font-medium">{lane.title}</p>
                        <p className="text-xs text-muted-foreground">
                          {lane.entries.length} event{lane.entries.length === 1 ? "" : "s"}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <Dialog
        open={userEventDialogOpen}
        onOpenChange={(open) => {
          setUserEventDialogOpen(open);
          if (!open) {
            setUserEventForm(null);
            setActiveUserEntry(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {userEventDialogMode === "create" ? "Create User Event" : "Edit User Event"}
            </DialogTitle>
            <DialogDescription>
              {userEventDialogMode === "create"
                ? "Create an event in a connected writable provider calendar."
                : "Update a provider event from the user workspace."}
            </DialogDescription>
          </DialogHeader>

          {userEventForm ? (
            <form className="space-y-4" onSubmit={submitUserEventForm}>
              <div className="space-y-2">
                <label htmlFor="event-source" className="text-sm font-medium">
                  Calendar Source
                </label>
                <select
                  id="event-source"
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                  value={userEventForm.sourceKey}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current ? { ...current, sourceKey: event.target.value } : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                >
                  {writableCalendars.map((calendar) => (
                    <option key={calendar.source_key} value={calendar.source_key}>
                      {calendar.display_name || calendar.calendar_id}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <label htmlFor="event-title" className="text-sm font-medium">
                  Title
                </label>
                <Input
                  id="event-title"
                  value={userEventForm.title}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current ? { ...current, title: event.target.value } : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                />
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="event-start" className="text-sm font-medium">
                    Start
                  </label>
                  <Input
                    id="event-start"
                    type="datetime-local"
                    value={userEventForm.startAtLocal}
                    onChange={(event) =>
                      setUserEventForm((current) =>
                        current ? { ...current, startAtLocal: event.target.value } : current,
                      )
                    }
                    disabled={userEventMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <label htmlFor="event-end" className="text-sm font-medium">
                    End
                  </label>
                  <Input
                    id="event-end"
                    type="datetime-local"
                    value={userEventForm.endAtLocal}
                    onChange={(event) =>
                      setUserEventForm((current) =>
                        current ? { ...current, endAtLocal: event.target.value } : current,
                      )
                    }
                    disabled={userEventMutation.isPending}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label htmlFor="event-timezone" className="text-sm font-medium">
                  Timezone
                </label>
                <Input
                  id="event-timezone"
                  value={userEventForm.timezone}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current ? { ...current, timezone: event.target.value } : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="event-description" className="text-sm font-medium">
                  Description
                </label>
                <Textarea
                  id="event-description"
                  value={userEventForm.description}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current ? { ...current, description: event.target.value } : current,
                    )
                  }
                  className="min-h-20"
                  disabled={userEventMutation.isPending}
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="event-location" className="text-sm font-medium">
                  Location
                </label>
                <Input
                  id="event-location"
                  value={userEventForm.location}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current ? { ...current, location: event.target.value } : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                />
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setUserEventDialogOpen(false)}
                  disabled={userEventMutation.isPending}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={userEventMutation.isPending}>
                  {userEventMutation.isPending
                    ? "Saving..."
                    : userEventDialogMode === "create"
                      ? "Create Event"
                      : "Update Event"}
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={butlerEventDialogOpen} onOpenChange={closeButlerEventDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {butlerEventDialogMode === "create" ? "Create Butler Event" : "Edit Butler Event"}
            </DialogTitle>
            <DialogDescription>
              Create or update schedule/reminder events in butler lanes, including recurring-until boundaries.
            </DialogDescription>
          </DialogHeader>

          {butlerEventDraft ? (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="calendar-butler-name" className="text-sm font-medium">Butler lane</label>
                  <select
                    id="calendar-butler-name"
                    value={butlerEventDraft.butlerName}
                    disabled={butlerEventDialogMode === "edit"}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              butlerName: event.target.value,
                            }
                          : current,
                      )
                    }
                    className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {availableButlers.length === 0 ? <option value="">No butlers available</option> : null}
                    {availableButlers.map((butlerName) => (
                      <option key={butlerName} value={butlerName}>
                        {formatLaneTitle(butlerName)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <label htmlFor="calendar-event-kind" className="text-sm font-medium">Event type</label>
                  <select
                    id="calendar-event-kind"
                    value={butlerEventDraft.eventKind}
                    disabled={butlerEventDialogMode === "edit"}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              eventKind: event.target.value as ButlerEventKind,
                            }
                          : current,
                      )
                    }
                    className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="butler_reminder">Reminder</option>
                    <option value="scheduled_task">Scheduled task</option>
                  </select>
                </div>
              </div>

              <div className="space-y-2">
                <label htmlFor="calendar-event-title" className="text-sm font-medium">Title</label>
                <Input
                  id="calendar-event-title"
                  value={butlerEventDraft.title}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            title: event.target.value,
                          }
                        : current,
                    )
                  }
                  placeholder="e.g. Daily medication"
                  disabled={butlerMutation.isPending}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="calendar-start-at" className="text-sm font-medium">Start</label>
                  <Input
                    id="calendar-start-at"
                    type="datetime-local"
                    value={butlerEventDraft.startAtLocal}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              startAtLocal: event.target.value,
                            }
                          : current,
                      )
                    }
                    disabled={butlerMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <label htmlFor="calendar-timezone" className="text-sm font-medium">Timezone</label>
                  <Input
                    id="calendar-timezone"
                    value={butlerEventDraft.timezone}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              timezone: event.target.value,
                            }
                          : current,
                      )
                    }
                    disabled={butlerMutation.isPending}
                  />
                </div>
              </div>

              {butlerEventDraft.eventKind === "scheduled_task" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <label htmlFor="calendar-end-at" className="text-sm font-medium">End</label>
                    <Input
                      id="calendar-end-at"
                      type="datetime-local"
                      value={butlerEventDraft.endAtLocal}
                      onChange={(event) =>
                        setButlerEventDraft((current) =>
                          current
                            ? {
                                ...current,
                                endAtLocal: event.target.value,
                              }
                            : current,
                        )
                      }
                      disabled={butlerMutation.isPending}
                    />
                  </div>
                  <div className="space-y-2">
                    <label htmlFor="calendar-cron" className="text-sm font-medium">Cron (optional)</label>
                    <Input
                      id="calendar-cron"
                      value={butlerEventDraft.cron}
                      onChange={(event) =>
                        setButlerEventDraft((current) =>
                          current
                            ? {
                                ...current,
                                cron: event.target.value,
                              }
                            : current,
                        )
                      }
                      placeholder="0 9 * * *"
                      disabled={butlerMutation.isPending}
                    />
                  </div>
                </div>
              ) : null}

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label htmlFor="calendar-frequency" className="text-sm font-medium">Recurrence</label>
                  <select
                    id="calendar-frequency"
                    value={butlerEventDraft.recurrenceFrequency}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              recurrenceFrequency: event.target.value as RecurrenceFrequency,
                            }
                          : current,
                      )
                    }
                    className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                    disabled={butlerMutation.isPending}
                  >
                    <option value="NONE">None</option>
                    <option value="DAILY">Daily</option>
                    <option value="WEEKLY">Weekly</option>
                    <option value="MONTHLY">Monthly</option>
                    <option value="YEARLY">Yearly</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 pt-7 text-sm font-medium">
                    <input
                      type="checkbox"
                      checked={butlerEventDraft.hasUntilAt}
                      onChange={(event) =>
                        setButlerEventDraft((current) =>
                          current
                            ? {
                                ...current,
                                hasUntilAt: event.target.checked,
                                untilAtLocal: event.target.checked
                                  ? current.untilAtLocal || current.startAtLocal
                                  : "",
                              }
                            : current,
                        )
                      }
                      disabled={butlerMutation.isPending}
                    />
                    Set until boundary
                  </label>
                </div>
              </div>

              {butlerEventDraft.hasUntilAt ? (
                <div className="space-y-2">
                  <label htmlFor="calendar-until-at" className="text-sm font-medium">Until</label>
                  <Input
                    id="calendar-until-at"
                    type="datetime-local"
                    value={butlerEventDraft.untilAtLocal}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              untilAtLocal: event.target.value,
                            }
                          : current,
                      )
                    }
                    disabled={butlerMutation.isPending}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => closeButlerEventDialog(false)}
              disabled={butlerMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSaveButlerEvent}
              disabled={butlerMutation.isPending || !butlerEventDraft}
            >
              {butlerMutation.isPending
                ? butlerEventDialogMode === "create"
                  ? "Creating..."
                  : "Saving..."
                : butlerEventDialogMode === "create"
                  ? "Create Event"
                  : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteCandidate} onOpenChange={(open) => (!open ? setDeleteCandidate(null) : null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Event</DialogTitle>
            <DialogDescription>
              {deleteCandidate
                ? `Delete "${deleteCandidate.title}" from the provider calendar?`
                : "Delete this event?"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteCandidate(null)}
              disabled={userEventMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={confirmDelete}
              disabled={userEventMutation.isPending}
            >
              {userEventMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
