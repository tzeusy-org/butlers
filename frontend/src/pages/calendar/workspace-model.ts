/**
 * Pure model for the calendar workspace page: range/anchor query-param parsing,
 * butler-lane event drafts, recurrence rules and the recurring-instance cap.
 * No React and no network; everything here is unit-testable on its own.
 */

import { addDays, addMonths, addWeeks, format, isSameMonth, isValid, parseISO } from "date-fns";

import type {
  CalendarAccountEntry,
  CalendarWorkspaceSourceFreshness,
  CalendarWorkspaceStatusFacet,
  CalendarWorkspaceView,
  UnifiedCalendarEntry,
  UnifiedCalendarSourceType,
} from "@/api/types.ts";
import { tzDayKey } from "@/lib/calendar-grid";

export type CalendarRange = "month" | "week" | "day" | "list";

export type ButlerEventKind = "scheduled_task" | "butler_reminder";
export type RecurrenceFrequency = "NONE" | "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";

export type ButlerEventDialogMode = "create" | "edit";
export interface ButlerEventDraft {
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

export interface ButlerLaneRows {
  laneId: string;
  butlerName: string;
  title: string;
  entries: UnifiedCalendarEntry[];
}
export const DEFAULT_RANGE: CalendarRange = "week";

export const VIEW_OPTIONS: Array<{ value: CalendarWorkspaceView; label: string }> = [
  { value: "user", label: "User" },
  { value: "butler", label: "Butler" },
];

export const RANGE_OPTIONS: Array<{ value: CalendarRange; label: string }> = [
  { value: "month", label: "Month" },
  { value: "week", label: "Week" },
  { value: "day", label: "Day" },
  { value: "list", label: "List" },
];

export function parseView(raw: string | null): CalendarWorkspaceView {
  return raw === "butler" ? "butler" : "user";
}

export function parseRange(raw: string | null): CalendarRange {
  if (raw === "month" || raw === "week" || raw === "day" || raw === "list") {
    return raw;
  }
  return DEFAULT_RANGE;
}

export function parseAnchor(raw: string | null): Date {
  if (!raw) return new Date();
  const parsed = parseISO(raw);
  return isValid(parsed) ? parsed : new Date();
}

export function serializeAnchor(value: Date): string {
  return format(value, "yyyy-MM-dd");
}

export function shiftAnchor(
  anchor: Date,
  range: CalendarRange,
  direction: -1 | 1,
): Date {
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

/** Compact period headline for the masthead Display line. The year lives in the eyebrow. */
export function headlineLabel(range: CalendarRange, start: Date, end: Date): string {
  if (range === "month") {
    return format(start, "MMMM yyyy");
  }
  if (range === "day") {
    return format(start, "EEEE, MMM d");
  }
  if (range === "list") {
    return "Next 30 days";
  }
  // week
  const last = addDays(end, -1);
  if (isSameMonth(start, last)) {
    return `${format(start, "MMM d")} – ${format(last, "d")}`;
  }
  return `${format(start, "MMM d")} – ${format(last, "MMM d")}`;
}

/** Titleize a raw identifier: replace separators, capitalize words. Skip email addresses. */
export function titleize(value: string): string {
  if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) return value;
  const result = value.replace(/[_-]/g, " ");
  return result === result.toLowerCase()
    ? result.replace(/\b\w/g, (s) => s.toUpperCase())
    : result;
}

/** Truncate hashed Google Calendar IDs (e.g. 07fd80d3…10b@group.calendar.google.com). */
export function truncateCalendarId(value: string): string {
  const match = value.match(
    /^([a-f0-9]{20,})@(group\.calendar\.google\.com)$/i,
  );
  if (match) {
    const hash = match[1];
    return `${hash.slice(0, 8)}\u2026${hash.slice(-3)}@${match[2]}`;
  }
  return value;
}

export function sourceName(source: CalendarWorkspaceSourceFreshness): string {
  const raw = truncateCalendarId(source.display_name || source.source_key);
  const isButlerSpecific = Boolean(source.metadata?.butler_specific);

  // Butler-lane internal sources (scheduler, reminders).
  if (source.butler_name && source.lane === "butler") {
    return `[Butler] ${titleize(source.butler_name)}`;
  }
  // User-lane provider sources configured for a specific butler.
  if (source.butler_name && isButlerSpecific) {
    return `[Butler] ${titleize(source.butler_name)}`;
  }
  if (source.provider === "google") {
    return `[Google] ${titleize(raw)}`;
  }
  return titleize(raw);
}

export function formatLaneTitle(butlerName: string): string {
  return butlerName
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (s) => s.toUpperCase());
}
export function formatLocalDateTimeInput(value: Date): string {
  return format(value, "yyyy-MM-dd'T'HH:mm");
}

export function parseLocalDateTimeInput(value: string): Date | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    return null;
  }
  return parsed;
}

export function toRRuleUntilToken(value: Date): string {
  const compact = value.toISOString().replace(/[-:]/g, "").replace(".000", "");
  return `${compact.slice(0, 15)}Z`;
}

export function buildRRule(
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
export function createDefaultButlerDraft(
  timezone: string,
  butlerName: string,
): ButlerEventDraft {
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

export function resolveButlerEventTarget(
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
    typeof metadata.origin_ref === "string" &&
    metadata.origin_ref.trim().length > 0
      ? metadata.origin_ref.trim()
      : null;
  if (!originRef) {
    return null;
  }

  return {
    eventId: originRef,
    sourceHint:
      entry.source_type === "scheduled_task"
        ? "scheduled_task"
        : "butler_reminder",
  };
}

export function isPausedEntry(entry: UnifiedCalendarEntry): boolean {
  const normalized = entry.status.toLowerCase();
  return normalized === "paused" || normalized === "inactive";
}

/** A cancelled occurrence (e.g. a recurring instance EXDATE-d off its series). */
export function isCancelledEntry(entry: UnifiedCalendarEntry): boolean {
  return entry.status.toLowerCase() === "cancelled";
}

export function newButlerRequestId(prefix: string): string {
  return `calendar-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
/** Maximum recurring instances of the same parent event to show per day per lane or grid cell. */
export const RECURRING_INSTANCE_CAP = 10;

/**
 * Returns the grouping key for a butler event entry.
 * Recurring instances from the same calendar_events row share the same schedule_id or reminder_id.
 * Falls back to entry_id for one-off events so they are never grouped together.
 */
export function recurringGroupKey(entry: UnifiedCalendarEntry): string {
  return entry.schedule_id ?? entry.reminder_id ?? entry.entry_id;
}
export interface RecurringOverflowSentinel {
  readonly _kind: "overflow";
  readonly sentinelKey: string;
  readonly title: string;
  readonly hiddenCount: number;
}

export type LaneRowItem = UnifiedCalendarEntry | RecurringOverflowSentinel;

export function isOverflowSentinel(
  item: LaneRowItem,
): item is RecurringOverflowSentinel {
  return (item as RecurringOverflowSentinel)._kind === "overflow";
}

/**
 * Groups entries by (day, parentGroupKey), caps each group at `cap`, and appends a
 * RecurringOverflowSentinel after each truncated group so the UI can render "... and N more".
 * Order within each group is preserved (caller should pre-sort by start_at).
 */
export function capLaneEntriesByDay(
  entries: UnifiedCalendarEntry[],
  cap: number,
  tz: string,
): LaneRowItem[] {
  // Build ordered list of (dayKey, groupKey) pairs while preserving insertion order
  const order: Array<[string, string]> = [];
  const seen = new Set<string>();
  const buckets = new Map<string, UnifiedCalendarEntry[]>();

  for (const entry of entries) {
    const dayKey = tzDayKey(entry.start_at, tz);
    const groupKey = recurringGroupKey(entry);
    const bucketKey = `${dayKey}::${groupKey}`;

    if (!seen.has(bucketKey)) {
      seen.add(bucketKey);
      order.push([dayKey, groupKey]);
    }

    const bucket = buckets.get(bucketKey) ?? [];
    bucket.push(entry);
    buckets.set(bucketKey, bucket);
  }

  const result: LaneRowItem[] = [];
  for (const [dayKey, groupKey] of order) {
    const bucketKey = `${dayKey}::${groupKey}`;
    const group = buckets.get(bucketKey) ?? [];
    const visible = group.slice(0, cap);
    result.push(...visible);
    const overflow = group.length - visible.length;
    if (overflow > 0) {
      result.push({
        _kind: "overflow",
        sentinelKey: `overflow::${bucketKey}`,
        title: group[0]?.title ?? groupKey,
        hiddenCount: overflow,
      });
    }
  }
  return result;
}

/** Server-side status facet options for GET /api/calendar/workspace. */
export const STATUS_FACET_OPTIONS: Array<{
  value: CalendarWorkspaceStatusFacet;
  label: string;
}> = [
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "error", label: "Error" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

/** Server-side source-type facet options for GET /api/calendar/workspace. */
export const SOURCE_TYPE_FACET_OPTIONS: Array<{
  value: UnifiedCalendarSourceType;
  label: string;
}> = [
  { value: "provider_event", label: "Provider event" },
  { value: "scheduled_task", label: "Schedule" },
  { value: "butler_reminder", label: "Reminder" },
  { value: "manual_butler_event", label: "Butler event" },
];

/** Native <select> in the toolbar's hairline-pill register. */
export const SELECT_CLASS =
  "h-7 rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2 " +
  "font-mono text-[11px] text-fg " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

/** Form <select> (taller, full-width) used inside dialogs. */
export const FIELD_SELECT_CLASS =
  "flex h-9 w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 " +
  "text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus " +
  "disabled:cursor-not-allowed disabled:opacity-50";

/** Map a source sync_state to a Dispatch StateDot state. Benign in-progress reads as waiting. */
export function syncDotState(
  syncState: string,
): "ok" | "degraded" | "error" | "waiting" {
  if (syncState === "fresh") return "ok";
  if (syncState === "failed") return "error";
  if (syncState === "stale") return "degraded";
  return "waiting";
}

/** Map a calendar account connector health state to a StateDot state. */
export function accountHealthDotState(
  state: CalendarAccountEntry["health"]["state"],
): "ok" | "degraded" | "error" | "waiting" {
  if (state === "healthy") return "ok";
  if (state === "error") return "error";
  if (state === "degraded") return "degraded";
  return "waiting";
}
