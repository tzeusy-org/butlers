import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  addDays,
  addHours,
  addMonths,
  addWeeks,
  differenceInMinutes,
  format,
  getHours,
  getMinutes,
  isSameDay,
  isSameMonth,
  isToday,
  isValid,
  parseISO,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { toast } from "sonner";
import { Link, useSearchParams } from "react-router";

import type {
  ApiResponse,
  CalendarAccountEntry,
  CalendarAuditEntry,
  CalendarConflictEntry,
  CalendarFindTimeConstraints,
  CalendarFindTimePartOfDay,
  CalendarSuggestedSlot,
  CalendarWorkspaceButlerEventPreviewRequest,
  CalendarWorkspaceFindTimeResponse,
  CalendarWorkspaceMutationResponse,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceSourceFreshness,
  CalendarWorkspaceStatusFacet,
  CalendarWorkspaceUserMutationAction,
  CalendarWorkspaceView,
  CalendarWorkspaceWritableCalendar,
  QuickAddDraft,
  UnifiedCalendarEntry,
  UnifiedCalendarSourceType,
} from "@/api/types.ts";
import {
  useCalendarAccounts,
  useAcceptCalendarProposal,
  useCalendarDayBriefing,
  useCalendarOverlays,
  useCalendarProposals,
  useCalendarWorkspace,
  useDismissCalendarProposal,
  useCalendarWorkspaceAudit,
  useCalendarWorkspaceMeta,
  useCalendarWorkspaceSearch,
  useFindCalendarWorkspaceTime,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
  usePreviewCalendarWorkspaceButlerEvent,
  useSetPrimaryCalendar,
  useSyncCalendarWorkspace,
  useToggleCalendarSource,
} from "@/hooks/use-calendar-workspace";
import { useDebounce } from "@/hooks/use-debounce";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DayBriefingCard } from "@/components/calendar/DayBriefingCard";
import { MeetingPrepRailContainer } from "@/components/calendar/MeetingPrepRail";
import { CalendarProposalsPanel } from "@/components/calendar/CalendarProposalsPanel";
import { QuickAddBar } from "@/pages/calendar/QuickAddBar";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { StateDot } from "@/components/ui/StateDot";
import { Voice } from "@/components/ui/Voice";
import { cn } from "@/lib/utils";
import {
  HOUR_HEIGHT_PX,
  MINUTES_PER_DAY,
  normalizeDragWindow,
  offsetToMinutes,
  resizeWindowEnd,
  shiftWindow,
  SNAP_MINUTES,
  snapMinutes,
} from "@/lib/calendar-grid";
import {
  overlayAmountBadge,
  overlayButlerAccent,
  overlayKindGlyph,
  overlayMetadata,
  overlaysByDay,
} from "@/lib/calendar-overlays";

type CalendarRange = "month" | "week" | "day" | "list";

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

function computeWindow(
  range: CalendarRange,
  anchor: Date,
): { start: Date; end: Date } {
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

function shiftAnchor(
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
function headlineLabel(range: CalendarRange, start: Date, end: Date): string {
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
function titleize(value: string): string {
  if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value)) return value;
  const result = value.replace(/[_-]/g, " ");
  return result === result.toLowerCase()
    ? result.replace(/\b\w/g, (s) => s.toUpperCase())
    : result;
}

/** Truncate hashed Google Calendar IDs (e.g. 07fd80d3…10b@group.calendar.google.com). */
function truncateCalendarId(value: string): string {
  const match = value.match(
    /^([a-f0-9]{20,})@(group\.calendar\.google\.com)$/i,
  );
  if (match) {
    const hash = match[1];
    return `${hash.slice(0, 8)}\u2026${hash.slice(-3)}@${match[2]}`;
  }
  return value;
}

function sourceName(source: CalendarWorkspaceSourceFreshness): string {
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

function formatLaneTitle(butlerName: string): string {
  return butlerName
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (s) => s.toUpperCase());
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

/**
 * Soft-failure status values returned by the calendar MCP mutation tools inside
 * an HTTP 200 response. The genuine-success statuses are open-ended
 * (`created` / `updated` / `deleted` / `ok` / queued approvals, etc.), so the
 * gate is a denylist of known failure states rather than an `ok`-only allowlist
 * — that keeps every real success path intact while catching soft failures.
 */
// NOTE: 'conflict' is intentionally excluded — it is handled as an interactive
// conflict-resolution flow (ConflictCard), not a terminal failure.
const CALENDAR_MUTATION_FAILURE_STATUSES = new Set([
  "error",
  "not_found",
  "failed",
]);

/**
 * Inspect a calendar MCP mutation result envelope to distinguish genuine
 * success from a soft failure.
 *
 * The calendar MCP tools fail SOFT: they return `status: 'error' | 'conflict' |
 * 'not_found' | 'failed'` (and `set-primary` can return `status: 'ok',
 * persisted: false`) inside an HTTP 200 response. Treating any 200 as success
 * makes the UI claim a change happened when it did not. A mutation is only OK
 * when its `status` is not a known failure value AND `persisted` is not `false`.
 *
 * `result` is the `data.result` payload from a calendar workspace mutation
 * response (or any object exposing `status` / `persisted`).
 */
function isCalendarMutationOk(result: unknown): boolean {
  if (typeof result !== "object" || result === null) {
    return false;
  }
  const record = result as Record<string, unknown>;
  if (record.persisted === false) {
    return false;
  }
  const status = maybeText(record.status);
  if (status && CALENDAR_MUTATION_FAILURE_STATUSES.has(status)) {
    return false;
  }
  return true;
}

/**
 * Build a human-readable failure message from a soft-failed mutation envelope,
 * preferring an explicit `error`/`message`/`detail` field and falling back to
 * the `status` string.
 */
function calendarMutationErrorMessage(
  result: unknown,
  fallback: string,
): string {
  if (typeof result === "object" && result !== null) {
    const record = result as Record<string, unknown>;
    const explicit =
      maybeText(record.error) ||
      maybeText(record.message) ||
      maybeText(record.detail);
    if (explicit) {
      return explicit;
    }
    if (record.persisted === false) {
      return "no change was persisted";
    }
    const status = maybeText(record.status);
    if (status) {
      return status;
    }
  }
  return fallback;
}

function buildRequestId(action: CalendarWorkspaceUserMutationAction): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return `calendar-${action}-${crypto.randomUUID()}`;
  }
  return `calendar-${action}-${Date.now()}`;
}

function defaultFormWindow(anchor: Date): {
  startAtLocal: string;
  endAtLocal: string;
} {
  const start = new Date(anchor);
  // If the caller provided an explicit time (hours or minutes set), use it as-is.
  // Otherwise round to the next whole hour, bumping forward if in the past.
  const hasExplicitTime = start.getHours() !== 0 || start.getMinutes() !== 0;
  if (!hasExplicitTime) {
    start.setMinutes(0, 0, 0);
    if (start.getTime() < Date.now()) {
      start.setHours(start.getHours() + 1);
    }
  } else {
    start.setSeconds(0, 0);
  }
  const end = new Date(start.getTime() + 30 * 60 * 1_000);
  return {
    startAtLocal: format(start, "yyyy-MM-dd'T'HH:mm"),
    endAtLocal: format(end, "yyyy-MM-dd'T'HH:mm"),
  };
}

function createDefaultButlerDraft(
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

function isPausedEntry(entry: UnifiedCalendarEntry): boolean {
  const normalized = entry.status.toLowerCase();
  return normalized === "paused" || normalized === "inactive";
}

/** A cancelled occurrence (e.g. a recurring instance EXDATE-d off its series). */
function isCancelledEntry(entry: UnifiedCalendarEntry): boolean {
  return entry.status.toLowerCase() === "cancelled";
}

function newButlerRequestId(prefix: string): string {
  return `calendar-${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Hour labels 0–23 for the y-axis. */
const HOURS = Array.from({ length: 24 }, (_, i) => i);
/** Default scroll-to position (in hours) when the time grid first mounts. */
const DEFAULT_SCROLL_HOUR = 7.5;
/** Pixel movement past which a pointer gesture counts as a drag (not a click). */
const DRAG_THRESHOLD_PX = 4;

/** Minute-of-day for a Date (local time). */
function minuteOfDay(date: Date): number {
  return getHours(date) * 60 + getMinutes(date);
}

/** Format a minute-of-day value as an `HH:mm` label. */
function formatMinuteLabel(minutes: number): string {
  const clamped = Math.min(MINUTES_PER_DAY, Math.max(0, minutes));
  if (clamped === MINUTES_PER_DAY) return "24:00";
  return format(
    new Date(2000, 0, 1, Math.floor(clamped / 60), clamped % 60),
    "HH:mm",
  );
}

/** Build a UTC ISO string for a given calendar day at `minutes` past local midnight. */
function isoAtMinute(day: Date, minutes: number): string {
  const d = new Date(day);
  d.setHours(0, 0, 0, 0);
  d.setMinutes(minutes);
  return d.toISOString();
}

/** Whether an entry carries an RRULE (i.e. is a recurring occurrence). */
function isRecurringInstance(entry: UnifiedCalendarEntry): boolean {
  return typeof entry.rrule === "string" && entry.rrule.trim().length > 0;
}

/** Whether a grid entry can be dragged to move/resize via existing update endpoints. */
function isGridDraggable(entry: UnifiedCalendarEntry): boolean {
  if (entry.all_day) {
    return false;
  }
  if (entry.source_type === "provider_event") {
    // Recurring provider occurrences are draggable; the drop is routed through the
    // recurrence scope sheet (this / following / series) instead of committing directly.
    return !!entry.provider_event_id && entry.editable;
  }
  // Butler-lane events (scheduled tasks / reminders) update via
  // calendar_update_butler_event, which has no scope-aware path yet, so keep
  // recurring butler instances non-draggable.
  if (isRecurringInstance(entry)) {
    return false;
  }
  return resolveButlerEventTarget(entry) !== null && !!entry.butler_name;
}

/** Immutable origin of an in-flight grid drag gesture. */
type DragOrigin =
  | {
      mode: "create";
      pointerId: number;
      downX: number;
      downY: number;
      dayIndex: number;
      anchorMin: number;
      moved: boolean;
    }
  | {
      mode: "move";
      pointerId: number;
      downX: number;
      downY: number;
      entry: UnifiedCalendarEntry;
      originStartMin: number;
      durationMin: number;
      grabOffsetMin: number;
      moved: boolean;
    }
  | {
      mode: "resize";
      pointerId: number;
      downX: number;
      downY: number;
      entry: UnifiedCalendarEntry;
      dayIndex: number;
      startMin: number;
      moved: boolean;
    };

/** Live preview of the current drag, rendered as a translucent block. */
type GridDragPreview =
  | { mode: "create"; dayIndex: number; startMin: number; endMin: number }
  | {
      mode: "move";
      dayIndex: number;
      startMin: number;
      endMin: number;
      entryId: string;
    }
  | {
      mode: "resize";
      dayIndex: number;
      startMin: number;
      endMin: number;
      entryId: string;
    };

/** Ghost left at an event's prior slot after a successful move, enabling one-click undo. */
interface MovedGhost {
  entry: UnifiedCalendarEntry;
  prevStartIso: string;
  prevEndIso: string;
  nextStartIso: string;
  nextEndIso: string;
}

/** Maximum recurring instances of the same parent event to show per day per lane or grid cell. */
const RECURRING_INSTANCE_CAP = 10;

/**
 * Returns the grouping key for a butler event entry.
 * Recurring instances from the same calendar_events row share the same schedule_id or reminder_id.
 * Falls back to entry_id for one-off events so they are never grouped together.
 */
function recurringGroupKey(entry: UnifiedCalendarEntry): string {
  return entry.schedule_id ?? entry.reminder_id ?? entry.entry_id;
}

/** Recurrence-edit scope mirroring the backend `recurrence_scope` literal. */
type RecurrenceScope = "this" | "following" | "series";

/**
 * A provider (user-lane) calendar entry is a recurring occurrence when it
 * carries an RRULE. The `provider_event_id` is the shared base recurring event,
 * and `start_at` is the occurrence's original start (the EXDATE/UNTIL anchor).
 */
function isRecurringUserEntry(entry: UnifiedCalendarEntry): boolean {
  return Boolean(entry.rrule) && Boolean(entry.provider_event_id);
}

/** Human-readable label for each recurrence scope option. */
const RECURRENCE_SCOPE_LABELS: Record<RecurrenceScope, string> = {
  this: "This occurrence",
  following: "This and following",
  series: "All events",
};

/**
 * Impact line for the "This occurrence" option, estimated from the number of
 * loaded occurrences sharing the same base provider event.
 */
function occurrenceImpactText(
  entries: UnifiedCalendarEntry[],
  providerEventId: string | null | undefined,
): string {
  // Without a base provider event id we cannot count siblings; matching on a
  // falsy id would wrongly bucket every non-provider entry together.
  if (!providerEventId) {
    return "Changes only this occurrence.";
  }
  const loaded = entries.filter(
    (e) => e.provider_event_id === providerEventId,
  ).length;
  return loaded > 1
    ? `Changes 1 of ~${loaded} loaded occurrences.`
    : "Changes only this occurrence.";
}

/**
 * Three-option recurrence scope chooser (this / following / series) shared by the
 * delete and edit confirmation sheets. The caller supplies the per-option impact
 * copy so the same control can describe a deletion or an edit.
 */
function RecurrenceScopeFieldset({
  fieldsetTestId,
  optionPrefix,
  name,
  scope,
  onChange,
  impacts,
}: {
  fieldsetTestId: string;
  optionPrefix: string;
  name: string;
  scope: RecurrenceScope;
  onChange: (scope: RecurrenceScope) => void;
  impacts: Record<RecurrenceScope, string>;
}) {
  return (
    <fieldset
      data-testid={fieldsetTestId}
      className="flex flex-col gap-2 border-t border-[var(--border)] pt-3"
    >
      <legend className="sr-only">Recurrence scope</legend>
      {(["this", "following", "series"] as const).map((value) => (
        <label
          key={value}
          data-testid={`${optionPrefix}-${value}`}
          className="flex cursor-pointer items-start gap-2 text-sm"
        >
          <input
            type="radio"
            name={name}
            value={value}
            checked={scope === value}
            onChange={() => onChange(value)}
            className="mt-1"
          />
          <span className="flex flex-col">
            <span className="font-medium">
              {RECURRENCE_SCOPE_LABELS[value]}
            </span>
            <Mono muted className="text-xs">
              {impacts[value]}
            </Mono>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

interface RecurringOverflowSentinel {
  readonly _kind: "overflow";
  readonly sentinelKey: string;
  readonly title: string;
  readonly hiddenCount: number;
}

type LaneRowItem = UnifiedCalendarEntry | RecurringOverflowSentinel;

function isOverflowSentinel(
  item: LaneRowItem,
): item is RecurringOverflowSentinel {
  return (item as RecurringOverflowSentinel)._kind === "overflow";
}

/**
 * Groups entries by (day, parentGroupKey), caps each group at `cap`, and appends a
 * RecurringOverflowSentinel after each truncated group so the UI can render "... and N more".
 * Order within each group is preserved (caller should pre-sort by start_at).
 */
function capLaneEntriesByDay(
  entries: UnifiedCalendarEntry[],
  cap: number,
): LaneRowItem[] {
  // Build ordered list of (dayKey, groupKey) pairs while preserving insertion order
  const order: Array<[string, string]> = [];
  const seen = new Set<string>();
  const buckets = new Map<string, UnifiedCalendarEntry[]>();

  for (const entry of entries) {
    const dayKey = format(new Date(entry.start_at), "yyyy-MM-dd");
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

// ---------------------------------------------------------------------------
// Dispatch presentational helpers (calendar-local)
//
// The calendar is drawn in the Dispatch language: surfaces not cards, hairline
// rules, hierarchy from type and rule, butler hue only on the letter-mark,
// state color only when state demands. These local pieces compose the shipped
// Dispatch kit (Eyebrow / Mono / Voice / Row / StateDot / ButlerMark) into the
// calendar-specific chrome.
// ---------------------------------------------------------------------------

/** Shared pill geometry per Design Language §4c (4px 10px / 1px border / 3px radius / mono 11px). */
const PILL_BASE =
  "inline-flex items-center justify-center gap-1.5 h-7 rounded-[3px] border px-2.5 " +
  "font-mono text-[11px] leading-none transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 " +
  "disabled:pointer-events-none disabled:opacity-40";

/** Pill button (§4c). `active` inverts bg/fg for the selected state. Never colored. */
function PillButton({
  active = false,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        PILL_BASE,
        active
          ? "bg-[var(--fg)] text-[var(--bg)] border-[var(--fg)]"
          : "bg-transparent text-[var(--mfg)] border-[var(--border-strong)] hover:text-[var(--fg)]",
        className,
      )}
      {...props}
    />
  );
}

/** Commit button (§4c) — fg background, bg text. At most one per surface. */
function CommitButton({
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        PILL_BASE,
        "bg-[var(--fg)] text-[var(--bg)] border-[var(--fg)] hover:opacity-90",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Inline snooze affordance for a due reminder / butler event. Clicking "Snooze"
 * reveals quick presets (+1h, +3h, tomorrow morning) plus a custom datetime so
 * the user can reschedule the item's due time without leaving the grid. The
 * chosen time is handed back as an ISO string; the parent fires the snooze
 * mutation.
 */
function SnoozeMenu({
  disabled,
  onSnooze,
}: {
  disabled?: boolean;
  onSnooze: (iso: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");

  function choose(date: Date) {
    onSnooze(date.toISOString());
    setOpen(false);
    setCustom("");
  }

  function chooseCustom() {
    const parsed = parseLocalDateTimeInput(custom);
    if (!parsed) {
      toast.error("Enter a valid snooze time");
      return;
    }
    choose(parsed);
  }

  const now = new Date();
  const tomorrowMorning = startOfDay(addDays(now, 1));
  tomorrowMorning.setHours(9, 0, 0, 0);
  const presets: Array<{ label: string; date: Date }> = [
    { label: "1 hour", date: addHours(now, 1) },
    { label: "3 hours", date: addHours(now, 3) },
    { label: "Tomorrow 9am", date: tomorrowMorning },
  ];

  return (
    <div className="relative inline-block">
      <PillButton
        data-testid="butler-snooze-button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        active={open}
      >
        Snooze
      </PillButton>
      {open ? (
        <div
          data-testid="butler-snooze-menu"
          className="absolute right-0 z-20 mt-1 flex w-56 flex-col gap-2 rounded-[4px] border border-[var(--border-strong)] bg-[var(--bg)] p-2 shadow-md"
        >
          <div className="flex flex-wrap gap-1.5">
            {presets.map((preset) => (
              <PillButton
                key={preset.label}
                data-testid={`butler-snooze-preset-${preset.label}`}
                onClick={() => choose(preset.date)}
                disabled={disabled}
              >
                {preset.label}
              </PillButton>
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <input
              type="datetime-local"
              data-testid="butler-snooze-custom-input"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              disabled={disabled}
              className="min-w-0 flex-1 rounded-[3px] border border-[var(--border-strong)] bg-transparent px-1.5 py-1 text-xs text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 disabled:opacity-50"
            />
            <CommitButton
              data-testid="butler-snooze-custom-confirm"
              onClick={chooseCustom}
              disabled={disabled || !custom.trim()}
            >
              Set
            </CommitButton>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Mono uppercase kind tag (§4d) — labels a kind, never celebrates it. */
function KindTag({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.08em] leading-none text-[var(--mfg)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * A single read-only cross-domain overlay pill (finance bill/renewal, travel
 * leg, relationship date, health appointment). Structured only — title + a
 * kind glyph + an optional structured amount badge, all drawn verbatim from the
 * projected entry's `metadata`. Non-interactive context, so it renders as a
 * static chip, not a button.
 */
function OverlayPill({ entry }: { entry: UnifiedCalendarEntry }) {
  const md = overlayMetadata(entry);
  const badge = overlayAmountBadge(md.meta);
  const accent = overlayButlerAccent(md.source_butler);
  const title = `${md.source_butler ?? "overlay"} · ${md.kind || "context"}${
    md.priority ? ` (${md.priority})` : ""
  }: ${entry.title}${badge ? ` — ${badge}` : ""}`;
  return (
    <div
      data-overlay-entry-id={entry.entry_id}
      data-overlay-kind={md.kind}
      data-overlay-butler={md.source_butler ?? ""}
      title={title}
      className={cn(
        "pointer-events-auto flex w-full items-center gap-1 truncate rounded-[2px] border border-dashed bg-foreground/[0.02] px-1 py-0.5 text-left text-[10px] leading-none",
        accent,
      )}
    >
      <span aria-hidden className="shrink-0 font-mono">
        {overlayKindGlyph(md.kind)}
      </span>
      <span className="truncate text-[var(--fg)]">{entry.title}</span>
      {badge ? (
        <span className="ml-auto shrink-0 font-mono tabular-nums text-[var(--mfg)]">
          {badge}
        </span>
      ) : null}
    </div>
  );
}

/** Server-side status facet options for GET /api/calendar/workspace. */
const STATUS_FACET_OPTIONS: Array<{
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
const SOURCE_TYPE_FACET_OPTIONS: Array<{
  value: UnifiedCalendarSourceType;
  label: string;
}> = [
  { value: "provider_event", label: "Provider event" },
  { value: "scheduled_task", label: "Schedule" },
  { value: "butler_reminder", label: "Reminder" },
  { value: "manual_butler_event", label: "Butler event" },
];

/** Native <select> in the toolbar's hairline-pill register. */
const SELECT_CLASS =
  "h-7 rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2 " +
  "font-mono text-[11px] text-[var(--fg)] " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30";

/** Form <select> (taller, full-width) used inside dialogs. */
const FIELD_SELECT_CLASS =
  "flex h-9 w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 " +
  "text-sm text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 " +
  "disabled:cursor-not-allowed disabled:opacity-50";

/** Map a source sync_state to a Dispatch StateDot state. Benign in-progress reads as waiting. */
function syncDotState(
  syncState: string,
): "ok" | "degraded" | "error" | "waiting" {
  if (syncState === "fresh") return "ok";
  if (syncState === "failed") return "error";
  if (syncState === "stale") return "degraded";
  return "waiting";
}

/** Map a calendar account connector health state to a StateDot state. */
function accountHealthDotState(
  state: CalendarAccountEntry["health"]["state"],
): "ok" | "degraded" | "error" | "waiting" {
  if (state === "healthy") return "ok";
  if (state === "error") return "error";
  if (state === "degraded") return "degraded";
  return "waiting";
}

// ---------------------------------------------------------------------------
// CalendarActivityPanel — audit log view for the Activity tab
// ---------------------------------------------------------------------------

function auditStatusLabel(status: CalendarAuditEntry["action_status"]): string {
  switch (status) {
    case "applied":
      return "applied";
    case "pending":
      return "pending";
    case "failed":
      return "failed";
    case "noop":
      return "noop";
    default:
      return String(status);
  }
}

function auditStatusColor(status: CalendarAuditEntry["action_status"]): string {
  switch (status) {
    case "applied":
      return "text-[var(--green)]";
    case "pending":
      return "text-[var(--yellow)]";
    case "failed":
      return "text-[var(--red)]";
    case "noop":
      return "text-[var(--mfg)]";
    default:
      return "text-[var(--mfg)]";
  }
}

interface CalendarActivityPanelProps {
  auditQuery: {
    isLoading: boolean;
    isError: boolean;
    error: Error | null;
    data?: {
      data?: {
        entries?: CalendarAuditEntry[];
        total?: number;
        offset?: number;
        limit?: number;
      };
    };
  };
  offset: number;
  limit: number;
  onPageChange: (offset: number) => void;
}

interface CalendarFindTimePanelProps {
  butlerName: string | null;
  onSelectSlot: (slot: CalendarSuggestedSlot) => void;
}

const FIND_TIME_HORIZON_OPTIONS: ReadonlyArray<{
  label: string;
  days: number;
}> = [
  { label: "Next 7 days", days: 7 },
  { label: "Next 14 days", days: 14 },
  { label: "Next 30 days", days: 30 },
];

const FIND_TIME_DURATION_OPTIONS: ReadonlyArray<{
  label: string;
  minutes: number;
}> = [
  { label: "30 minutes", minutes: 30 },
  { label: "45 minutes", minutes: 45 },
  { label: "1 hour", minutes: 60 },
  { label: "90 minutes", minutes: 90 },
  { label: "2 hours", minutes: 120 },
];

const FIND_TIME_PART_OF_DAY_OPTIONS: ReadonlyArray<{
  label: string;
  value: string;
}> = [
  { label: "Any time of day", value: "any" },
  { label: "Mornings", value: "morning" },
  { label: "Afternoons", value: "afternoon" },
  { label: "Evenings", value: "evening" },
];

/**
 * "Find time" panel: pick a duration + soft constraints, fetch ranked open
 * slots, and select one to prefill the create-event form. Read-only — selecting
 * a slot drives a separate create call; this panel never mutates an event.
 */
function CalendarFindTimePanel({
  butlerName,
  onSelectSlot,
}: CalendarFindTimePanelProps) {
  const [durationMinutes, setDurationMinutes] = useState(30);
  const [partOfDay, setPartOfDay] = useState<"any" | CalendarFindTimePartOfDay>(
    "any",
  );
  const [avoidWeekends, setAvoidWeekends] = useState(false);
  const [horizonDays, setHorizonDays] = useState(14);
  const [result, setResult] =
    useState<CalendarWorkspaceFindTimeResponse | null>(null);

  const findMutation = useFindCalendarWorkspaceTime();

  async function runSearch(event: React.FormEvent) {
    event.preventDefault();
    if (!butlerName) {
      toast.error("No writable calendar is available to search for free time.");
      return;
    }
    const now = new Date();
    const constraints: CalendarFindTimeConstraints = {};
    if (partOfDay !== "any") {
      constraints.part_of_day = partOfDay;
    }
    if (avoidWeekends) {
      constraints.avoid_weekdays = ["SA", "SU"];
    }
    try {
      const response = await findMutation.mutateAsync({
        butler_name: butlerName,
        duration_minutes: durationMinutes,
        search_start: now.toISOString(),
        search_end: addDays(now, horizonDays).toISOString(),
        constraints:
          Object.keys(constraints).length > 0 ? constraints : undefined,
        limit: 12,
      });
      setResult(response.data);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to find open time slots.",
      );
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <form className="flex flex-wrap items-end gap-3" onSubmit={runSearch}>
        <div className="flex flex-col gap-1">
          <label
            htmlFor="find-time-duration"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
          >
            Duration
          </label>
          <select
            id="find-time-duration"
            className={SELECT_CLASS}
            value={durationMinutes}
            onChange={(e) => setDurationMinutes(Number(e.target.value))}
          >
            {FIND_TIME_DURATION_OPTIONS.map((option) => (
              <option key={option.minutes} value={option.minutes}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="find-time-part-of-day"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
          >
            When
          </label>
          <select
            id="find-time-part-of-day"
            className={SELECT_CLASS}
            value={partOfDay}
            onChange={(e) =>
              setPartOfDay(e.target.value as "any" | CalendarFindTimePartOfDay)
            }
          >
            {FIND_TIME_PART_OF_DAY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="find-time-horizon"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
          >
            Window
          </label>
          <select
            id="find-time-horizon"
            className={SELECT_CLASS}
            value={horizonDays}
            onChange={(e) => setHorizonDays(Number(e.target.value))}
          >
            {FIND_TIME_HORIZON_OPTIONS.map((option) => (
              <option key={option.days} value={option.days}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <label className="flex items-center gap-2 text-[11px] text-[var(--mfg)]">
          <input
            type="checkbox"
            checked={avoidWeekends}
            onChange={(e) => setAvoidWeekends(e.target.checked)}
          />
          Avoid weekends
        </label>

        <PillButton type="submit" active disabled={findMutation.isPending}>
          {findMutation.isPending ? "Searching…" : "Find time"}
        </PillButton>
      </form>

      {findMutation.isError ? (
        <div role="alert" className="flex items-start gap-2">
          <StateDot state="error" className="mt-[7px]" />
          <p className="text-sm text-[var(--fg)]">
            Failed to find open time slots.
          </p>
        </div>
      ) : result === null ? (
        <Voice variant="italic" className="text-[var(--mfg)]">
          Choose a duration and search for open time.
        </Voice>
      ) : result.available === false ? (
        <div
          role="status"
          className="flex items-start gap-2"
          data-testid="find-time-unavailable"
        >
          <StateDot state="degraded" className="mt-[7px]" />
          <p className="text-sm text-[var(--fg)]">
            Free/busy is unavailable right now, so open time couldn&rsquo;t be
            checked.{" "}
            <span className="text-[var(--mfg)]">
              {result.reason ??
                "The calendar source is unreachable — try again shortly."}
            </span>
          </p>
        </div>
      ) : result.slots.length === 0 ? (
        <Voice variant="italic" className="text-[var(--mfg)]">
          No open slots match those constraints in the selected window.
        </Voice>
      ) : (
        <ul className="flex flex-col gap-1.5" data-testid="find-time-slots">
          {result.slots.map((slot) => {
            const start = parseISO(slot.start_at);
            const end = parseISO(slot.end_at);
            return (
              <li key={`${slot.start_at}-${slot.end_at}`}>
                <button
                  type="button"
                  data-testid="find-time-slot"
                  className={cn(
                    "flex w-full items-center justify-between rounded-[3px] border border-[var(--border-strong)]",
                    "px-3 py-2 text-left transition-colors hover:bg-foreground/[0.06]",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
                  )}
                  onClick={() => onSelectSlot(slot)}
                >
                  <span className="text-sm text-[var(--fg)]">
                    {format(start, "EEE, MMM d")}
                  </span>
                  <Mono muted className="tabular-nums">
                    {format(start, "HH:mm")}–{format(end, "HH:mm")}
                  </Mono>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function CalendarActivityPanel({
  auditQuery,
  offset,
  limit,
  onPageChange,
}: CalendarActivityPanelProps) {
  const entries = auditQuery.data?.data?.entries ?? [];
  const total = auditQuery.data?.data?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  if (auditQuery.isLoading) {
    return (
      <Voice variant="italic" className="text-[var(--mfg)]">
        Loading activity log…
      </Voice>
    );
  }
  if (auditQuery.isError) {
    return (
      <div role="alert" className="flex items-start gap-2 py-1">
        <StateDot state="error" className="mt-[7px]" />
        <p className="text-sm text-[var(--fg)]">
          Failed to load activity log.{" "}
          <span className="text-[var(--mfg)]">
            {auditQuery.error instanceof Error
              ? auditQuery.error.message
              : "Unknown error"}
          </span>
        </p>
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <Voice variant="italic" className="text-[var(--mfg)]">
        No calendar mutations logged yet.
      </Voice>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center justify-between gap-4">
        <Mono muted className="tabular-nums">
          {total} {total === 1 ? "entry" : "entries"} total · showing{" "}
          {offset + 1}–{Math.min(offset + limit, total)}
        </Mono>
        <div className="flex items-center gap-1">
          <PillButton
            disabled={!hasPrev}
            onClick={() => onPageChange(Math.max(0, offset - limit))}
          >
            ‹ Prev
          </PillButton>
          <PillButton
            disabled={!hasNext}
            onClick={() => onPageChange(offset + limit)}
          >
            Next ›
          </PillButton>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1" role="list">
        {entries.map((entry) => {
          const createdAt = entry.created_at
            ? format(new Date(entry.created_at), "yyyy-MM-dd HH:mm:ss")
            : "";
          const summaryTitle =
            typeof entry.payload_summary?.title === "string"
              ? entry.payload_summary.title
              : null;

          return (
            <Row
              key={entry.id}
              mark={
                <span
                  className={`font-mono text-[10px] uppercase tracking-[0.12em] ${auditStatusColor(entry.action_status)}`}
                >
                  {auditStatusLabel(entry.action_status)}
                </span>
              }
              meta={
                entry.source_session_id ? (
                  <Link
                    to={`/sessions/${entry.source_session_id}`}
                    className="font-mono text-[10px] text-[var(--mfg)] underline decoration-dotted hover:text-[var(--fg)]"
                    title={`Session ${entry.source_session_id}`}
                  >
                    session ›
                  </Link>
                ) : null
              }
            >
              <div className="flex min-w-0 flex-col gap-0.5">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-[var(--fg)]">
                    {entry.action_type}
                    {summaryTitle ? `: ${summaryTitle}` : ""}
                  </span>
                  {entry.source_butler ? (
                    <KindTag>{entry.source_butler}</KindTag>
                  ) : null}
                </div>
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
                  <Mono muted className="tabular-nums">
                    {createdAt}
                  </Mono>
                  {entry.error ? (
                    <span
                      className="max-w-[20rem] truncate text-[11px] text-[var(--red)]"
                      title={entry.error}
                    >
                      {entry.error}
                    </span>
                  ) : null}
                  {entry.origin_ref ? (
                    <Mono muted className="truncate">
                      {entry.origin_ref}
                    </Mono>
                  ) : null}
                </div>
              </div>
            </Row>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CalendarEntryDetailPanel
// ---------------------------------------------------------------------------

interface CalendarEntryDetailPanelProps {
  entry: UnifiedCalendarEntry;
  onClose: () => void;
  onDelete?: (entry: UnifiedCalendarEntry) => void;
  onUserEdit?: (entry: UnifiedCalendarEntry) => void;
  onButlerEdit?: (entry: UnifiedCalendarEntry) => void;
  /**
   * Defers an inline edit of a recurring provider occurrence to the parent so it
   * can open the recurrence scope sheet (this / following / series) before
   * committing the patch. When omitted, recurring edits commit directly.
   */
  onRecurringEdit?: (
    entry: UnifiedCalendarEntry,
    patch: Record<string, unknown>,
    label: string,
  ) => void;
  userMutation: ReturnType<typeof useMutateCalendarWorkspaceUserEvent>;
  butlerMutation: ReturnType<typeof useMutateCalendarWorkspaceButlerEvent>;
}

function CalendarEntryDetailPanel({
  entry,
  onClose,
  onDelete,
  onRecurringEdit,
  userMutation,
  butlerMutation,
}: CalendarEntryDetailPanelProps) {
  const isUserEvent = entry.source_type === "provider_event";
  const isButlerEvent =
    entry.source_type === "scheduled_task" ||
    entry.source_type === "butler_reminder";
  const isPending = userMutation.isPending || butlerMutation.isPending;

  // Inline editable fields — initialized from entry, updated on server response
  // Inline-editable drafts — state is reset on mount (key={entry.entry_id} at call site)
  const [titleDraft, setTitleDraft] = useState(entry.title);
  const [descriptionDraft, setDescriptionDraft] = useState(
    typeof entry.metadata?.description === "string"
      ? entry.metadata.description
      : "",
  );
  const [locationDraft, setLocationDraft] = useState(
    typeof entry.metadata?.location === "string" ? entry.metadata.location : "",
  );
  // Auto-save feedback: edits commit on blur, so surface the outcome explicitly
  // ("Saving…/Saved/Save failed") since there is no Save button to anchor it.
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">(
    "idle",
  );

  function fireUserUpdate(patch: Record<string, unknown>, label: string) {
    const { butlerName, calendarId } = resolveOwnerFromEntry(entry);
    if (!butlerName || !entry.provider_event_id) return;
    // A recurring occurrence must first ask the user which occurrences the edit
    // applies to (this / following / series); defer to the parent scope sheet.
    if (isRecurringUserEntry(entry) && onRecurringEdit) {
      onRecurringEdit(entry, patch, label);
      return;
    }
    setSaveStatus("idle");
    userMutation.mutate(
      {
        butler_name: butlerName,
        action: "update",
        request_id: `detail-update-${Date.now()}`,
        payload: {
          event_id: entry.provider_event_id,
          calendar_id: calendarId ?? undefined,
          ...patch,
        },
      },
      {
        onSuccess: (response) => {
          const result = response.data.result;
          if (!isCalendarMutationOk(result)) {
            toast.error(
              `Failed to update ${label}: ${calendarMutationErrorMessage(result, "Update failed.")}`,
            );
            setSaveStatus("error");
            return;
          }
          toast.success(`Event ${label} updated.`);
          setSaveStatus("saved");
        },
        onError: (error) => {
          toast.error(
            error instanceof Error
              ? error.message
              : `Failed to update ${label}.`,
          );
          setSaveStatus("error");
        },
      },
    );
  }

  function fireButlerUpdate(patch: Record<string, unknown>, label: string) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) return;
    setSaveStatus("idle");
    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "update",
        request_id: `detail-update-${Date.now()}`,
        payload: {
          event_id: target.eventId,
          source_hint: target.sourceHint,
          ...patch,
        },
      },
      {
        onSuccess: (response) => {
          const result = response.data.result;
          if (!isCalendarMutationOk(result)) {
            toast.error(
              `Failed to update ${label}: ${calendarMutationErrorMessage(result, "Update failed.")}`,
            );
            setSaveStatus("error");
            return;
          }
          toast.success(`Event ${label} updated.`);
          setSaveStatus("saved");
        },
        onError: (error) => {
          toast.error(
            error instanceof Error
              ? error.message
              : `Failed to update ${label}.`,
          );
          setSaveStatus("error");
        },
      },
    );
  }

  function handleTitleBlur() {
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === entry.title) return;
    if (isUserEvent) fireUserUpdate({ title: trimmed }, "title");
    else if (isButlerEvent) fireButlerUpdate({ title: trimmed }, "title");
  }

  function handleDescriptionBlur() {
    const trimmed = descriptionDraft.trim();
    const current =
      typeof entry.metadata?.description === "string"
        ? entry.metadata.description
        : "";
    if (trimmed === current) return;
    if (isUserEvent) fireUserUpdate({ description: trimmed }, "description");
  }

  function handleLocationBlur() {
    const trimmed = locationDraft.trim();
    const current =
      typeof entry.metadata?.location === "string"
        ? entry.metadata.location
        : "";
    if (trimmed === current) return;
    if (isUserEvent) fireUserUpdate({ location: trimmed }, "location");
  }

  const startDate = new Date(entry.start_at);
  const endDate = new Date(entry.end_at);
  const startFmt = entry.all_day
    ? format(startDate, "EEE, MMM d")
    : format(startDate, "EEE, MMM d · HH:mm");
  const endFmt = entry.all_day
    ? format(endDate, "EEE, MMM d")
    : format(endDate, "HH:mm");

  // Attendees from Google Calendar event metadata
  const attendees = useMemo(() => {
    const raw = entry.metadata?.event_metadata;
    if (!raw || typeof raw !== "object") return [] as string[];
    const list = (raw as Record<string, unknown>).attendees;
    if (!Array.isArray(list)) return [] as string[];
    return list
      .map((a) => {
        if (typeof a === "string") return a;
        if (typeof a === "object" && a !== null) {
          const obj = a as Record<string, unknown>;
          return typeof obj.email === "string"
            ? obj.email
            : typeof obj.displayName === "string"
              ? obj.displayName
              : "";
        }
        return "";
      })
      .filter(Boolean);
  }, [entry.metadata?.event_metadata]);

  const canMutateUser =
    isUserEvent && !!entry.provider_event_id && entry.editable;
  const canMutateButler = isButlerEvent;

  return (
    <div
      data-testid="entry-detail-panel"
      className="flex min-h-0 flex-col gap-4 overflow-y-auto pb-4"
    >
      {/* Panel header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Eyebrow as="div">Event detail</Eyebrow>
          {canMutateUser || canMutateButler ? (
            <span
              data-testid="detail-save-status"
              className={cn(
                "font-mono text-[10px] uppercase tracking-[0.12em]",
                saveStatus === "error"
                  ? "text-[var(--red)]"
                  : "text-[var(--mfg)]",
              )}
            >
              {isPending
                ? "Saving…"
                : saveStatus === "saved"
                  ? "Saved ✓"
                  : saveStatus === "error"
                    ? "Save failed"
                    : "Edits save on blur"}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          aria-label="Close detail panel"
          onClick={onClose}
          className="font-mono text-[11px] text-[var(--mfg)] hover:text-[var(--fg)]"
        >
          ✕
        </button>
      </div>

      {/* Title */}
      <div className="flex flex-col gap-1">
        <label
          htmlFor="detail-title"
          className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]"
        >
          Title
        </label>
        {canMutateUser || canMutateButler ? (
          <input
            id="detail-title"
            data-testid="detail-title-input"
            value={titleDraft}
            onChange={(e) => {
              setTitleDraft(e.target.value);
              setSaveStatus("idle");
            }}
            onBlur={handleTitleBlur}
            disabled={isPending}
            className="w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm font-medium text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 disabled:opacity-50"
          />
        ) : (
          <span className="text-sm font-medium text-[var(--fg)]">
            {entry.title}
          </span>
        )}
      </div>

      {/* Time range */}
      <div className="flex flex-col gap-0.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
          When
        </span>
        <Mono className="text-[var(--fg)]">
          {startFmt}
          {!entry.all_day && ` – ${endFmt}`}
        </Mono>
        <Mono muted>{entry.timezone}</Mono>
      </div>

      {/* Sync state chip */}
      <div className="flex items-center gap-2">
        <StateDot state={syncDotState(entry.sync_state ?? "stale")} />
        <Mono muted>{entry.sync_state ?? "unknown"}</Mono>
        <KindTag>{entry.source_type.replace(/_/g, " ")}</KindTag>
      </div>

      {/* Source + butler mark */}
      <div className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
          Source
        </span>
        <div className="flex items-center gap-2">
          {entry.butler_name ? <ButlerMark name={entry.butler_name} /> : null}
          <Mono muted className="truncate">
            {entry.source_key}
          </Mono>
        </div>
      </div>

      {/* Provenance */}
      {entry.source_butler || entry.source_session_id ? (
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
            Provenance
          </span>
          {entry.source_butler ? (
            <Mono muted data-testid="detail-source-butler">
              {entry.source_butler}
            </Mono>
          ) : null}
          {entry.source_session_id ? (
            <Link
              to={`/sessions/${entry.source_session_id}`}
              data-testid="detail-session-link"
              className="font-mono text-[11px] text-[var(--mfg)] underline decoration-dotted hover:text-[var(--fg)]"
              title={`Session ${entry.source_session_id}`}
            >
              session ›
            </Link>
          ) : null}
        </div>
      ) : null}

      {/* Attendees */}
      {attendees.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
            Attendees
          </span>
          <div className="flex flex-wrap gap-1.5">
            {attendees.map((a) => (
              <span
                key={a}
                data-testid="detail-attendee-chip"
                className="inline-flex items-center rounded-[3px] border border-[var(--border-strong)] px-2 py-0.5 font-mono text-[10px] text-[var(--mfg)]"
              >
                {a}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {/* Meeting-prep rail (bu-rct3g): attendee relationships, notes, last-met,
          and per-attendee message context for an entity-linked meeting. Reads the
          precomputed prep view — fail-open to an honest "No prep context yet"
          empty-state (the expected state for most events today). Shown only for
          provider (user-calendar) meetings, where attendee prep is meaningful.
          Keyed on `event_id` (= `calendar_events.id`, bu-jemrk) — the id the prep
          contributions are keyed on — not the per-instance `entry_id`. When the
          entry has no backing event row, `event_id` is null and the container
          gates the fetch (no spurious prep request). */}
      {isUserEvent ? (
        <MeetingPrepRailContainer eventId={entry.event_id} heading={entry.title} />
      ) : null}

      {/* Description (user events only) */}
      {isUserEvent ? (
        <div className="flex flex-col gap-1">
          <label
            htmlFor="detail-description"
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]"
          >
            Description
          </label>
          <textarea
            id="detail-description"
            data-testid="detail-description-input"
            value={descriptionDraft}
            onChange={(e) => {
              setDescriptionDraft(e.target.value);
              setSaveStatus("idle");
            }}
            onBlur={handleDescriptionBlur}
            disabled={isPending || !canMutateUser}
            rows={3}
            className="w-full resize-none rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 disabled:opacity-50"
          />
        </div>
      ) : null}

      {/* Location (user events only) */}
      {isUserEvent ? (
        <div className="flex flex-col gap-1">
          <label
            htmlFor="detail-location"
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]"
          >
            Location
          </label>
          <input
            id="detail-location"
            data-testid="detail-location-input"
            value={locationDraft}
            onChange={(e) => {
              setLocationDraft(e.target.value);
              setSaveStatus("idle");
            }}
            onBlur={handleLocationBlur}
            disabled={isPending || !canMutateUser}
            className="w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 disabled:opacity-50"
          />
        </div>
      ) : null}

      {/* Delete scope (user editable events only) */}
      {canMutateUser && onDelete ? (
        <div className="mt-auto border-t border-[var(--border)] pt-3">
          <PillButton
            data-testid="detail-delete-button"
            className="hover:border-[var(--red)] hover:text-[var(--red)]"
            disabled={isPending}
            onClick={() => onDelete(entry)}
          >
            Delete event
          </PillButton>
        </div>
      ) : null}
    </div>
  );
}

function resolveOwnerFromEntry(entry: UnifiedCalendarEntry): {
  butlerName: string | null;
  calendarId: string | null;
} {
  return {
    butlerName: entry.butler_name ?? null,
    calendarId: entry.calendar_id ?? null,
  };
}

// ---------------------------------------------------------------------------
// CalendarSearchPalette — command-palette-style full-text event search
// ---------------------------------------------------------------------------

/**
 * Full-text search palette over the calendar projection.  Lists matches grouped
 * by day (ranked by trigram relevance from the backend); selecting a match (or
 * pressing Enter on the top result) calls ``onJump`` so the page can navigate
 * the grid to that day and flash the event.
 */
function CalendarSearchPalette({
  open,
  onOpenChange,
  view,
  timezone,
  onJump,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  view: CalendarWorkspaceView;
  timezone: string;
  onJump: (entry: UnifiedCalendarEntry) => void;
}) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");

  // Debounce keystrokes before hitting the search endpoint. (Internal state is
  // reset by remounting: the parent only renders this component while open.)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  const searchView: CalendarWorkspaceView =
    view === "butler" ? "butler" : "user";
  const searchQuery = useCalendarWorkspaceSearch(
    { q: debounced, view: searchView, timezone, limit: 50 },
    { enabled: open },
  );

  const entriesData = searchQuery.data?.data.entries;
  const results = entriesData ?? [];
  // Honest degraded signal: false only when every calendar schema failed to
  // respond, so an empty result means "could not search", not "no matches".
  const searchAvailable = searchQuery.data?.data.available !== false;
  const groups = useMemo(() => {
    const byDay: Array<{
      day: string;
      date: Date;
      items: UnifiedCalendarEntry[];
    }> = [];
    const idx = new Map<string, number>();
    for (const entry of entriesData ?? []) {
      const d = new Date(entry.start_at);
      const key = format(d, "yyyy-MM-dd");
      let gi = idx.get(key);
      if (gi === undefined) {
        gi = byDay.length;
        idx.set(key, gi);
        byDay.push({ day: key, date: startOfDay(d), items: [] });
      }
      byDay[gi].items.push(entry);
    }
    return byDay;
  }, [entriesData]);

  const trimmed = debounced.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] w-[90vw] max-w-[90vw] flex-col overflow-hidden sm:w-[34rem] sm:max-w-[34rem]">
        <DialogHeader>
          <DialogTitle>Search events</DialogTitle>
          <DialogDescription>
            Find an event by title, description, or location. Press Enter to
            jump to the top match.
          </DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && results.length > 0) {
              event.preventDefault();
              onJump(results[0]);
            }
          }}
          placeholder="Search calendar…"
          aria-label="Search calendar events"
        />
        <div className="mt-2 min-h-0 flex-1 overflow-y-auto">
          {trimmed.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Type to search across all events.
            </Voice>
          ) : searchQuery.isLoading ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Searching…
            </Voice>
          ) : !searchAvailable ? (
            <div
              role="status"
              className="flex items-start gap-2"
              data-testid="search-unavailable"
            >
              <StateDot state="degraded" className="mt-[7px]" />
              <p className="text-sm text-[var(--fg)]">
                Search is unavailable right now.{" "}
                <span className="text-[var(--mfg)]">
                  The calendar index couldn&rsquo;t be reached — results may be
                  incomplete. Try again shortly.
                </span>
              </p>
            </div>
          ) : results.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              No matching events.
            </Voice>
          ) : (
            groups.map((group) => (
              <section key={group.day} className="mb-4">
                <div className="mb-1 border-b border-[var(--border)] pb-1">
                  <Eyebrow>{format(group.date, "EEE · MMM d, yyyy")}</Eyebrow>
                </div>
                <div role="list">
                  {group.items.map((entry) => (
                    <button
                      key={entry.entry_id}
                      type="button"
                      onClick={() => onJump(entry)}
                      className="flex w-full items-center gap-2 rounded-[3px] px-2 py-1.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                    >
                      <Mono muted className="w-14 shrink-0 tabular-nums">
                        {entry.all_day
                          ? "all day"
                          : format(new Date(entry.start_at), "HH:mm")}
                      </Mono>
                      <span className="truncate text-sm text-[var(--fg)]">
                        {entry.title}
                      </span>
                      {entry.butler_name ? (
                        <Mono
                          muted
                          className="ml-auto hidden shrink-0 sm:inline"
                        >
                          {entry.butler_name}
                        </Mono>
                      ) : null}
                    </button>
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// CalendarWorkspacePage
// ---------------------------------------------------------------------------

export default function CalendarWorkspacePage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const view = parseView(searchParams.get("view"));
  const range = parseRange(searchParams.get("range"));
  const anchor = parseAnchor(searchParams.get("anchor"));
  const anchorParam = serializeAnchor(anchor);
  const selectedSourceKey = searchParams.get("source") ?? "all";
  const selectedCalendarId = searchParams.get("calendar") ?? "all";
  const selectedStatus = searchParams.get("status") ?? "all";
  const selectedSourceType = searchParams.get("kind") ?? "all";
  // Cross-domain overlays are an additive, read-only context layer toggled on
  // top of the primary user/butler view (not a separate view mode). The toggle
  // is persisted in the URL so the layered view is shareable.
  const overlaysEnabled = searchParams.get("overlays") === "1";

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const { start, end } = useMemo(
    () => computeWindow(range, anchor),
    [range, anchor],
  );

  const metaQuery = useCalendarWorkspaceMeta();
  const connectedSources = useMemo(
    () => metaQuery.data?.data.connected_sources ?? [],
    [metaQuery.data?.data.connected_sources],
  );
  const writableCalendars = useMemo(
    () => metaQuery.data?.data.writable_calendars ?? [],
    [metaQuery.data?.data.writable_calendars],
  );
  // Only calendars that resolve to an owning butler can actually be submitted;
  // a null butler_name fails at submit with "Could not resolve owning butler".
  // Filter the create-event dropdown to these so users can't pick an
  // unsubmittable calendar.
  const submittableCalendars = useMemo(
    () => writableCalendars.filter((calendar) => Boolean(calendar.butler_name)),
    [writableCalendars],
  );
  const defaultTimezone = metaQuery.data?.data.default_timezone || timezone;
  const primaryCalendarId = metaQuery.data?.data.primary_calendar_id ?? null;

  // The butler the "Find time" panel queries: the selected writable source's
  // owner if one is chosen, otherwise the first writable calendar's owner.
  const findTimeButlerName = useMemo(() => {
    const selected =
      selectedSourceKey !== "all"
        ? submittableCalendars.find((c) => c.source_key === selectedSourceKey)
        : undefined;
    return (
      selected?.butler_name || submittableCalendars[0]?.butler_name || null
    );
  }, [submittableCalendars, selectedSourceKey]);

  const userSources = useMemo(
    () => connectedSources.filter((source) => source.lane === "user"),
    [connectedSources],
  );

  const [sourcesDialogOpen, setSourcesDialogOpen] = useState(false);
  // The backend is the source of truth for whether a calendar is enabled as a
  // sync source (``sync_enabled`` on each connected source). We mirror that into
  // a local Set for snappy optimistic toggling; it is re-seeded whenever meta
  // refreshes.
  const accountsQuery = useCalendarAccounts();
  const toggleSourceMutation = useToggleCalendarSource();
  const [disabledSources, setDisabledSources] = useState<Set<string>>(
    new Set<string>(),
  );

  useEffect(() => {
    if (toggleSourceMutation.isPending) return;
    const serverDisabled = connectedSources
      .filter((source) => source.sync_enabled === false)
      .map((source) => source.source_key);
    setDisabledSources(new Set(serverDisabled));
  }, [connectedSources, toggleSourceMutation.isPending]);

  function toggleSourceEnabled(source: CalendarWorkspaceSourceFreshness) {
    const sourceKey = source.source_key;
    const willEnable = disabledSources.has(sourceKey);
    // Optimistically reflect the new state.
    setDisabledSources((prev) => {
      const next = new Set(prev);
      if (willEnable) next.delete(sourceKey);
      else next.add(sourceKey);
      return next;
    });
    if (!source.butler_name) {
      toast.error("Cannot toggle this source: no owning butler resolved");
      return;
    }
    toggleSourceMutation.mutate(
      {
        butler: source.butler_name,
        source_key: sourceKey,
        enabled: willEnable,
      },
      {
        onError: (err) => {
          // Revert the optimistic update on failure.
          setDisabledSources((prev) => {
            const next = new Set(prev);
            if (willEnable) next.add(sourceKey);
            else next.delete(sourceKey);
            return next;
          });
          toast.error(`Failed to update source: ${err.message}`);
        },
        onSuccess: () => {
          toast.success(
            willEnable ? "Source enabled for sync" : "Source disabled",
          );
        },
      },
    );
  }

  const sourceFilters = useMemo(() => {
    let filtered = userSources;
    if (selectedCalendarId !== "all") {
      filtered = filtered.filter(
        (source) => source.calendar_id === selectedCalendarId,
      );
    }
    if (selectedSourceKey !== "all") {
      filtered = filtered.filter(
        (source) => source.source_key === selectedSourceKey,
      );
    }
    return filtered.map((source) => source.source_key);
  }, [selectedCalendarId, selectedSourceKey, userSources]);

  const sourcesForQuery = useMemo(() => {
    const hasCalendarFilter =
      selectedSourceKey !== "all" || selectedCalendarId !== "all";
    const hasDisabled = disabledSources.size > 0;

    if (view === "user" && (hasCalendarFilter || hasDisabled)) {
      const base = hasCalendarFilter
        ? sourceFilters
        : userSources.map((s) => s.source_key);
      return base.filter((key) => !disabledSources.has(key));
    }
    return undefined;
  }, [
    disabledSources,
    selectedCalendarId,
    selectedSourceKey,
    sourceFilters,
    userSources,
    view,
  ]);

  const workspaceQuery = useCalendarWorkspace({
    view,
    start: start.toISOString(),
    end: end.toISOString(),
    timezone,
    sources: sourcesForQuery,
    status:
      selectedStatus === "all"
        ? undefined
        : (selectedStatus as CalendarWorkspaceStatusFacet),
    source_type:
      selectedSourceType === "all"
        ? undefined
        : (selectedSourceType as UnifiedCalendarSourceType),
  });

  const overlaysQuery = useCalendarOverlays(
    { start: start.toISOString(), end: end.toISOString(), timezone },
    { enabled: overlaysEnabled },
  );
  const overlayEntries = useMemo(
    () => (overlaysEnabled ? (overlaysQuery.data?.data.entries ?? []) : []),
    [overlaysEnabled, overlaysQuery.data?.data.entries],
  );
  const hasDomainContext = overlaysQuery.data?.data.has_domain_context ?? false;
  const overlaysByDayMap = useMemo(
    () => overlaysByDay(overlayEntries),
    [overlayEntries],
  );

  // Day-briefing card ("tomorrow at a glance"): the precomputed overlay
  // contributions for tomorrow, grouped by butler/kind. Reads the same cached
  // view as the overlays lane (no per-open LLM); only fetched while overlays are
  // toggled on, so it shares that opt-in surface.
  const briefingDate = useMemo(() => addDays(startOfDay(new Date()), 1), []);
  const briefingDateParam = useMemo(
    () => format(briefingDate, "yyyy-MM-dd"),
    [briefingDate],
  );
  const dayBriefingQuery = useCalendarDayBriefing(
    { date: briefingDateParam, timezone },
    { enabled: overlaysEnabled },
  );
  const dayBriefing = dayBriefingQuery.data?.data;

  const syncMutation = useSyncCalendarWorkspace();
  const butlerMutation = useMutateCalendarWorkspaceButlerEvent();
  const userEventMutation = useMutateCalendarWorkspaceUserEvent();
  const primaryMutation = useSetPrimaryCalendar();
  const queryClient = useQueryClient();

  // Activity panel state
  const [activityPanelOpen, setActivityPanelOpen] = useState(false);
  // Find-time panel state (mutually exclusive with the activity panel).
  const [findTimePanelOpen, setFindTimePanelOpen] = useState(false);
  const [proposalsPanelOpen, setProposalsPanelOpen] = useState(false);

  // Proposals lane: butler-extracted candidate events awaiting accept/dismiss.
  // Fetched only while the panel is open; accept/dismiss mutations reconcile the
  // optimistic lane state inside the panel.
  const proposalsQuery = useCalendarProposals(
    { start: start.toISOString(), end: end.toISOString(), timezone },
    { enabled: proposalsPanelOpen },
  );
  const proposalEntries = useMemo(
    () => (proposalsPanelOpen ? (proposalsQuery.data?.data.entries ?? []) : []),
    [proposalsPanelOpen, proposalsQuery.data?.data.entries],
  );
  const acceptProposalMutation = useAcceptCalendarProposal();
  const dismissProposalMutation = useDismissCalendarProposal();

  const [auditOffset, setAuditOffset] = useState(0);
  const AUDIT_PAGE_SIZE = 50;
  const auditQuery = useCalendarWorkspaceAudit(
    { limit: AUDIT_PAGE_SIZE, offset: auditOffset },
    { enabled: activityPanelOpen },
  );

  const [syncingSourceKey, setSyncingSourceKey] = useState<string | null>(null);
  const [userEventDialogOpen, setUserEventDialogOpen] = useState(false);
  const [userEventDialogMode, setUserEventDialogMode] =
    useState<UserEventDialogMode>("create");
  const [activeUserEntry, setActiveUserEntry] =
    useState<UnifiedCalendarEntry | null>(null);
  const [deleteCandidate, setDeleteCandidate] =
    useState<UnifiedCalendarEntry | null>(null);
  // Recurrence scope for deleting a recurring occurrence: 'this' (just this
  // occurrence, EXDATE), 'following' (this + later, UNTIL split), or 'series'
  // (the whole recurring event). Reset to 'this' whenever a new candidate opens.
  const [deleteScope, setDeleteScope] = useState<RecurrenceScope>("this");
  // Pending edit of a recurring provider occurrence (detail-panel field edit or a
  // drag/resize). Holding the patch here lets the scope sheet pick this/following/
  // series before the update fires. `editScope` resets to 'this' on each open.
  const [recurringEdit, setRecurringEdit] = useState<{
    entry: UnifiedCalendarEntry;
    patch: Record<string, unknown>;
    label: string;
  } | null>(null);
  const [editScope, setEditScope] = useState<RecurrenceScope>("this");
  const [userEventForm, setUserEventForm] = useState<UserEventFormState | null>(
    null,
  );
  // Conflict state: set when the server returns status='conflict' for a user-event mutation.
  // Holds the detected conflicts, suggested slots, and the pending mutation args so re-submission
  // (slot pill or "Book anyway") can replay the same mutation with adjusted parameters.
  const [userEventConflict, setUserEventConflict] = useState<{
    conflicts: CalendarConflictEntry[];
    suggested_slots: CalendarSuggestedSlot[];
    pendingMutation: {
      butler_name: string;
      action: CalendarWorkspaceUserMutationAction;
      payload: Record<string, unknown>;
      request_id: string;
    };
  } | null>(null);
  const [butlerEventDialogOpen, setButlerEventDialogOpen] = useState(false);
  const [butlerEventDialogMode, setButlerEventDialogMode] =
    useState<ButlerEventDialogMode>("create");
  const [butlerEventDraft, setButlerEventDraft] =
    useState<ButlerEventDraft | null>(null);
  const [editingButlerEntry, setEditingButlerEntry] =
    useState<UnifiedCalendarEntry | null>(null);

  // Live recurrence dry-run preview for the butler-event dialog. Debounced so we
  // don't fire a request on every keystroke; only runs when a recurrence is set.
  const recurrencePreview = usePreviewCalendarWorkspaceButlerEvent();
  const debouncedButlerDraft = useDebounce(butlerEventDraft, 400);
  const recurrencePreviewRequest =
    useMemo<CalendarWorkspaceButlerEventPreviewRequest | null>(() => {
      if (!debouncedButlerDraft) return null;
      const startAt = parseLocalDateTimeInput(
        debouncedButlerDraft.startAtLocal,
      );
      if (!startAt) return null;
      const untilAt = debouncedButlerDraft.hasUntilAt
        ? parseLocalDateTimeInput(debouncedButlerDraft.untilAtLocal)
        : null;
      const tz = debouncedButlerDraft.timezone.trim() || timezone;
      if (debouncedButlerDraft.recurrenceFrequency !== "NONE") {
        return {
          rrule: buildRRule(debouncedButlerDraft.recurrenceFrequency, untilAt),
          start_at: startAt.toISOString(),
          timezone: tz,
          limit: 6,
        };
      }
      if (
        debouncedButlerDraft.eventKind === "scheduled_task" &&
        debouncedButlerDraft.cron.trim()
      ) {
        return {
          cron: debouncedButlerDraft.cron.trim(),
          start_at: startAt.toISOString(),
          until_at: untilAt ? untilAt.toISOString() : null,
          timezone: tz,
          limit: 6,
        };
      }
      return null;
    }, [debouncedButlerDraft, timezone]);
  const { mutate: runRecurrencePreview, reset: resetRecurrencePreview } =
    recurrencePreview;
  useEffect(() => {
    if (!butlerEventDialogOpen || !recurrencePreviewRequest) {
      resetRecurrencePreview();
      return;
    }
    runRecurrencePreview(recurrencePreviewRequest);
  }, [
    recurrencePreviewRequest,
    butlerEventDialogOpen,
    runRecurrencePreview,
    resetRecurrencePreview,
  ]);
  const recurrencePreviewData = recurrencePreview.data?.data ?? null;
  const timeGridRef = useRef<HTMLDivElement>(null);
  // Detail panel — replaces modal-only editing with a right-docked panel
  const [selectedEntry, setSelectedEntry] =
    useState<UnifiedCalendarEntry | null>(null);

  // Search command palette + jump-to-and-flash
  const [searchPaletteOpen, setSearchPaletteOpen] = useState(false);
  const [flashedEntryId, setFlashedEntryId] = useState<string | null>(null);

  function openDetailPanel(entry: UnifiedCalendarEntry) {
    setSelectedEntry(entry);
  }

  function closeDetailPanel() {
    setSelectedEntry(null);
  }

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

  const updateQuery = useCallback(
    (nextValues: {
      view?: CalendarWorkspaceView;
      range?: CalendarRange;
      anchor?: Date;
      source?: string;
      calendar?: string;
      status?: string;
      kind?: string;
      overlays?: boolean;
    }) => {
      const next = new URLSearchParams(searchParams);

      if (nextValues.view) next.set("view", nextValues.view);
      if (nextValues.range) next.set("range", nextValues.range);
      if (nextValues.anchor)
        next.set("anchor", serializeAnchor(nextValues.anchor));

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

      if (nextValues.status !== undefined) {
        if (!nextValues.status || nextValues.status === "all") {
          next.delete("status");
        } else {
          next.set("status", nextValues.status);
        }
      }

      if (nextValues.kind !== undefined) {
        if (!nextValues.kind || nextValues.kind === "all") {
          next.delete("kind");
        } else {
          next.set("kind", nextValues.kind);
        }
      }

      if (nextValues.overlays !== undefined) {
        if (nextValues.overlays) {
          next.set("overlays", "1");
        } else {
          next.delete("overlays");
        }
      }

      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

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
      const source = userSources.find(
        (item) => item.source_key === selectedSourceKey,
      );
      if (source?.calendar_id !== selectedCalendarId) {
        updateQuery({ source: "all" });
      }
    }
  }, [selectedCalendarId, selectedSourceKey, updateQuery, userSources, view]);

  // Scroll time grid to default hour when it mounts or the range/anchor changes
  useEffect(() => {
    if ((range === "week" || range === "day") && timeGridRef.current) {
      timeGridRef.current.scrollTop = DEFAULT_SCROLL_HOUR * HOUR_HEIGHT_PX;
    }
  }, [range, anchor]);

  const entries = useMemo(() => {
    const rows = workspaceQuery.data?.data.entries ?? [];
    return [...rows].sort((a, b) => a.start_at.localeCompare(b.start_at));
  }, [workspaceQuery.data?.data.entries]);

  // Jump from a search match: navigate the grid to the match's day, then flash it.
  const handleSearchJump = useCallback(
    (entry: UnifiedCalendarEntry) => {
      setSearchPaletteOpen(false);
      const target = new Date(entry.start_at);
      updateQuery({
        view: entry.view === "butler" ? "butler" : "user",
        range: "day",
        anchor: target,
      });
      setFlashedEntryId(entry.entry_id);
    },
    [updateQuery],
  );

  // Link a day-briefing chip to its underlying item: navigate the grid to the
  // overlay entry's day (day view). Overlay entries are read-only domain context
  // (synthetic ids), so the "link" is to the day they live on, not a detail dialog.
  const handleBriefingSelect = useCallback(
    (entry: UnifiedCalendarEntry) => {
      const target = new Date(entry.start_at);
      updateQuery({ range: "day", anchor: target });
    },
    [updateQuery],
  );

  // Once the grid has the flashed entry rendered, scroll it into view and pulse it.
  useEffect(() => {
    if (!flashedEntryId) return;
    if (workspaceQuery.isFetching) return;
    const el = document.querySelector<HTMLElement>(
      `[data-calendar-entry-id="${CSS.escape(flashedEntryId)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("calendar-entry-flash");
    const timer = window.setTimeout(() => {
      el.classList.remove("calendar-entry-flash");
      setFlashedEntryId(null);
    }, 2200);
    return () => {
      window.clearTimeout(timer);
      el.classList.remove("calendar-entry-flash");
    };
  }, [flashedEntryId, entries, workspaceQuery.isFetching]);

  const sourceByKey = useMemo(() => {
    const lookup = new Map<string, CalendarWorkspaceSourceFreshness>();
    connectedSources.forEach((source) => {
      lookup.set(source.source_key, source);
    });
    return lookup;
  }, [connectedSources]);

  const lanes = useMemo(
    () =>
      workspaceQuery.data?.data.lanes.length
        ? workspaceQuery.data.data.lanes
        : (metaQuery.data?.data.lane_definitions ?? []),
    [workspaceQuery.data?.data.lanes, metaQuery.data?.data.lane_definitions],
  );

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

  const weekDays = useMemo(() => {
    if (range === "week")
      return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    if (range === "day") return [start];
    return [] as Date[];
  }, [range, start]);

  // Collect the set of Google account emails from source metadata.
  // The backend stores `account_email` on each source during calendar discovery.
  const googleAccountEmails = useMemo(() => {
    const emails = new Set<string>();
    for (const source of connectedSources) {
      const email = source.metadata?.account_email;
      if (typeof email === "string" && email) {
        emails.add(email);
      }
    }
    return emails;
  }, [connectedSources]);

  const calendarFilterOptions = useMemo(() => {
    const deduped = new Map<string, string>();
    userSources.forEach((source) => {
      if (!source.calendar_id) return;
      if (!deduped.has(source.calendar_id)) {
        const raw = truncateCalendarId(
          source.display_name || source.calendar_id,
        );
        deduped.set(source.calendar_id, titleize(raw));
      }
    });
    return Array.from(deduped.entries()).map(([calendarId, label]) => ({
      calendarId,
      label,
    }));
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
      .map(
        ([laneId, descriptor]): ButlerLaneRows => ({
          laneId,
          butlerName: descriptor.butlerName,
          title: descriptor.title,
          entries: [...(grouped.get(descriptor.butlerName) ?? [])].sort(
            (a, b) => a.start_at.localeCompare(b.start_at),
          ),
        }),
      )
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

  function resolveSourceForForm(
    sourceKey: string,
  ): CalendarWorkspaceWritableCalendar | undefined {
    return writableCalendars.find(
      (calendar) => calendar.source_key === sourceKey,
    );
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
      entry.calendar_id || source?.calendar_id || writable?.calendar_id || null;

    return { butlerName, calendarId };
  }

  function openUserCreateDialog(forDate?: Date, endDate?: Date) {
    if (submittableCalendars.length === 0) {
      toast.error(
        "No writable calendar sources are available for user events.",
      );
      return;
    }

    const preferredSource =
      selectedSourceKey !== "all" &&
      submittableCalendars.some((c) => c.source_key === selectedSourceKey)
        ? selectedSourceKey
        : submittableCalendars[0].source_key;
    const { startAtLocal, endAtLocal } = defaultFormWindow(forDate ?? anchor);
    // A drag gesture supplies an explicit end; prefer it over the default window.
    const resolvedEndLocal = endDate
      ? format(endDate, "yyyy-MM-dd'T'HH:mm")
      : endAtLocal;

    setUserEventDialogMode("create");
    setActiveUserEntry(null);
    setUserEventForm({
      sourceKey: preferredSource,
      title: "",
      startAtLocal,
      endAtLocal: resolvedEndLocal,
      timezone: defaultTimezone,
      description: "",
      location: "",
    });
    setUserEventDialogOpen(true);
  }

  // -------------------------------------------------------------------------
  // Time-grid drag interactions: create / move / resize (week + day views)
  // -------------------------------------------------------------------------
  // Geometry is measured live from the grid body so cross-day moves and snapping
  // stay correct regardless of column count, gutter width, or scroll position.
  const gridBodyRef = useRef<HTMLDivElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  // Immutable origin of the in-flight gesture (read inside pointer handlers).
  const dragOriginRef = useRef<DragOrigin | null>(null);
  // Set when a gesture exceeded the drag threshold so the trailing click is ignored.
  const suppressClickRef = useRef(false);
  // Live preview rendered while dragging.
  const [gridDrag, setGridDrag] = useState<GridDragPreview | null>(null);
  // "Moved" ghost left at the previous slot after a successful move/resize, for one-click undo.
  const [movedGhost, setMovedGhost] = useState<MovedGhost | null>(null);

  const pointerToMinutes = useCallback((clientY: number) => {
    const el = gridBodyRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    return offsetToMinutes(clientY - rect.top);
  }, []);

  const pointerToDayIndex = useCallback(
    (clientX: number) => {
      const el = gridBodyRef.current;
      if (!el || weekDays.length === 0) return 0;
      const rect = el.getBoundingClientRect();
      const gutter = gutterRef.current?.getBoundingClientRect().width ?? 0;
      const usable = rect.width - gutter;
      if (usable <= 0) return 0;
      const idx = Math.floor(
        (clientX - rect.left - gutter) / (usable / weekDays.length),
      );
      return Math.min(weekDays.length - 1, Math.max(0, idx));
    },
    [weekDays.length],
  );

  /** Optimistically rewrite a cached entry's start/end across all workspace queries. */
  const patchEntryTimeInCache = useCallback(
    (entryId: string, startIso: string, endIso: string) => {
      queryClient.setQueriesData<ApiResponse<CalendarWorkspaceReadResponse>>(
        { queryKey: ["calendar-workspace"] },
        (old) => {
          if (!old?.data?.entries) return old;
          return {
            ...old,
            data: {
              ...old.data,
              entries: old.data.entries.map((item) =>
                item.entry_id === entryId
                  ? { ...item, start_at: startIso, end_at: endIso }
                  : item,
              ),
            },
          };
        },
      );
    },
    [queryClient],
  );

  /**
   * Persist a new time window for an event through the existing update endpoints,
   * with optimistic UI and snap-back on soft-failure (`persisted=false`) or error.
   */
  const commitTimeChange = useCallback(
    async (
      entry: UnifiedCalendarEntry,
      nextStartIso: string,
      nextEndIso: string,
      options?: { isUndo?: boolean },
    ) => {
      const prevStartIso = entry.start_at;
      const prevEndIso = entry.end_at;
      if (nextStartIso === prevStartIso && nextEndIso === prevEndIso) {
        return;
      }

      patchEntryTimeInCache(entry.entry_id, nextStartIso, nextEndIso);
      const rollback = () =>
        patchEntryTimeInCache(entry.entry_id, prevStartIso, prevEndIso);

      try {
        let result: unknown;
        if (entry.source_type === "provider_event") {
          const { butlerName, calendarId } = resolveOwnerFromEntry(entry);
          if (!butlerName || !entry.provider_event_id) {
            rollback();
            toast.error("Could not resolve calendar owner for this event.");
            return;
          }
          const response = await userEventMutation.mutateAsync({
            butler_name: butlerName,
            action: "update",
            request_id: buildRequestId("update"),
            payload: {
              event_id: entry.provider_event_id,
              calendar_id: calendarId ?? undefined,
              start_at: nextStartIso,
              end_at: nextEndIso,
              timezone: entry.timezone || defaultTimezone,
            },
          });
          result = response.data.result;
        } else {
          const target = resolveButlerEventTarget(entry);
          if (!target || !entry.butler_name) {
            rollback();
            toast.error("Could not resolve butler event for this drag.");
            return;
          }
          const response = await butlerMutation.mutateAsync({
            butler_name: entry.butler_name,
            action: "update",
            request_id: buildRequestId("update"),
            payload: {
              event_id: target.eventId,
              source_hint: target.sourceHint,
              start_at: nextStartIso,
              end_at: nextEndIso,
            },
          });
          result = response.data.result;
        }

        if (!isCalendarMutationOk(result)) {
          rollback();
          setMovedGhost(null);
          toast.error(
            `Change not saved: ${calendarMutationErrorMessage(result, "no change was persisted")}`,
          );
          return;
        }

        if (options?.isUndo) {
          setMovedGhost(null);
          toast.success("Reverted.");
        } else {
          // Leave a ghost at the old window so the move can be undone in one click.
          setMovedGhost({
            entry,
            prevStartIso,
            prevEndIso,
            nextStartIso,
            nextEndIso,
          });
          toast.success("Event moved.");
        }
      } catch (error) {
        rollback();
        setMovedGhost(null);
        toast.error(
          error instanceof Error
            ? error.message
            : "Failed to save calendar change.",
        );
      }
    },
    [butlerMutation, defaultTimezone, patchEntryTimeInCache, userEventMutation],
  );

  const undoMove = useCallback(() => {
    setMovedGhost((ghost) => {
      if (ghost) {
        const moved: UnifiedCalendarEntry = {
          ...ghost.entry,
          start_at: ghost.nextStartIso,
          end_at: ghost.nextEndIso,
        };
        void commitTimeChange(moved, ghost.prevStartIso, ghost.prevEndIso, {
          isUndo: true,
        });
      }
      return null;
    });
  }, [commitTimeChange]);

  // Auto-dismiss the undo ghost after a short window.
  useEffect(() => {
    if (!movedGhost) return;
    const timer = setTimeout(() => setMovedGhost(null), 6_000);
    return () => clearTimeout(timer);
  }, [movedGhost]);

  function handleGridPointerMove(event: React.PointerEvent) {
    const origin = dragOriginRef.current;
    if (!origin || event.pointerId !== origin.pointerId) return;

    if (
      !origin.moved &&
      Math.abs(event.clientX - origin.downX) +
        Math.abs(event.clientY - origin.downY) >
        DRAG_THRESHOLD_PX
    ) {
      origin.moved = true;
    }
    if (!origin.moved) return;

    const pointerMin = pointerToMinutes(event.clientY);
    if (origin.mode === "create") {
      const { startMin, endMin } = normalizeDragWindow(
        origin.anchorMin,
        pointerMin,
      );
      setGridDrag({
        mode: "create",
        dayIndex: origin.dayIndex,
        startMin,
        endMin,
      });
    } else if (origin.mode === "move") {
      const dayIndex = pointerToDayIndex(event.clientX);
      const deltaMin =
        pointerMin - origin.grabOffsetMin - origin.originStartMin;
      const { startMin, endMin } = shiftWindow(
        origin.originStartMin,
        origin.durationMin,
        deltaMin,
      );
      setGridDrag({
        mode: "move",
        dayIndex,
        startMin,
        endMin,
        entryId: origin.entry.entry_id,
      });
    } else {
      const endMin = resizeWindowEnd(origin.startMin, pointerMin);
      setGridDrag({
        mode: "resize",
        dayIndex: origin.dayIndex,
        startMin: origin.startMin,
        endMin,
        entryId: origin.entry.entry_id,
      });
    }
  }

  function handleGridPointerUp(event: React.PointerEvent) {
    const origin = dragOriginRef.current;
    dragOriginRef.current = null;
    setGridDrag(null);
    if (!origin || event.pointerId !== origin.pointerId) return;
    try {
      event.currentTarget.releasePointerCapture?.(origin.pointerId);
    } catch {
      /* capture may already be released */
    }

    if (!origin.moved) {
      // No drag — let the element's onClick handle it (create dialog / detail panel).
      return;
    }
    suppressClickRef.current = true;

    const pointerMin = pointerToMinutes(event.clientY);
    if (origin.mode === "create") {
      const { startMin, endMin } = normalizeDragWindow(
        origin.anchorMin,
        pointerMin,
      );
      const day = weekDays[origin.dayIndex] ?? weekDays[0];
      const startDate = new Date(day);
      startDate.setHours(0, 0, 0, 0);
      startDate.setMinutes(startMin);
      const endDate = new Date(day);
      endDate.setHours(0, 0, 0, 0);
      endDate.setMinutes(endMin);
      openUserCreateDialog(startDate, endDate);
    } else if (origin.mode === "move") {
      const dayIndex = pointerToDayIndex(event.clientX);
      const day = weekDays[dayIndex] ?? weekDays[0];
      const deltaMin =
        pointerMin - origin.grabOffsetMin - origin.originStartMin;
      const { startMin, endMin } = shiftWindow(
        origin.originStartMin,
        origin.durationMin,
        deltaMin,
      );
      commitDragTimeChange(
        origin.entry,
        isoAtMinute(day, startMin),
        isoAtMinute(day, endMin),
      );
    } else {
      const day = weekDays[origin.dayIndex] ?? weekDays[0];
      const endMin = resizeWindowEnd(origin.startMin, pointerMin);
      commitDragTimeChange(
        origin.entry,
        isoAtMinute(day, origin.startMin),
        isoAtMinute(day, endMin),
      );
    }
  }

  /**
   * Persist a drag/resize. A recurring occurrence routes through the scope sheet
   * (this / following / series) so the user picks which occurrences shift; a
   * one-off event commits directly with optimistic UI + snap-back.
   */
  function commitDragTimeChange(
    entry: UnifiedCalendarEntry,
    nextStartIso: string,
    nextEndIso: string,
  ) {
    if (isRecurringUserEntry(entry)) {
      if (nextStartIso === entry.start_at && nextEndIso === entry.end_at)
        return;
      openRecurringEdit(
        entry,
        {
          start_at: nextStartIso,
          end_at: nextEndIso,
          timezone: entry.timezone || defaultTimezone,
        },
        "time",
      );
      return;
    }
    void commitTimeChange(entry, nextStartIso, nextEndIso);
  }

  function handleGridPointerCancel(event: React.PointerEvent) {
    const origin = dragOriginRef.current;
    dragOriginRef.current = null;
    setGridDrag(null);
    if (origin) {
      try {
        event.currentTarget.releasePointerCapture?.(origin.pointerId);
      } catch {
        /* capture may already be released */
      }
    }
  }

  function beginCreateDrag(event: React.PointerEvent, dayIndex: number) {
    if (event.button !== 0 || view !== "user") return;
    dragOriginRef.current = {
      mode: "create",
      pointerId: event.pointerId,
      downX: event.clientX,
      downY: event.clientY,
      dayIndex,
      anchorMin: snapMinutes(pointerToMinutes(event.clientY)),
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function beginMoveDrag(
    event: React.PointerEvent,
    entry: UnifiedCalendarEntry,
  ) {
    if (event.button !== 0 || !isGridDraggable(entry)) return;
    const originStartMin = minuteOfDay(new Date(entry.start_at));
    const durationMin = Math.max(
      differenceInMinutes(new Date(entry.end_at), new Date(entry.start_at)),
      SNAP_MINUTES,
    );
    dragOriginRef.current = {
      mode: "move",
      pointerId: event.pointerId,
      downX: event.clientX,
      downY: event.clientY,
      entry,
      originStartMin,
      durationMin,
      // Offset from the event's top to where the pointer grabbed it (keeps the grab point under the cursor).
      grabOffsetMin: pointerToMinutes(event.clientY) - originStartMin,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function beginResizeDrag(
    event: React.PointerEvent,
    entry: UnifiedCalendarEntry,
    dayIndex: number,
  ) {
    if (event.button !== 0 || !isGridDraggable(entry)) return;
    event.stopPropagation();
    dragOriginRef.current = {
      mode: "resize",
      pointerId: event.pointerId,
      downX: event.clientX,
      downY: event.clientY,
      entry,
      dayIndex,
      startMin: minuteOfDay(new Date(entry.start_at)),
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  async function handleSyncAll() {
    try {
      const result = await syncMutation.mutateAsync({ all: true });
      toast.success(
        `Sync triggered for ${result.data.triggered_count} source(s).`,
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to trigger sync.",
      );
    }
  }

  async function handleSyncSource(
    source: CalendarWorkspaceSourceFreshness,
    { full = false }: { full?: boolean } = {},
  ) {
    setSyncingSourceKey(source.source_key);
    try {
      const result = await syncMutation.mutateAsync({
        source_key: source.source_key,
        butler: source.butler_name || undefined,
        full,
      });
      const target = result.data.targets[0];
      if (target?.status === "failed") {
        toast.error(target.error || "Source sync failed.");
      } else if (full) {
        toast.success(
          target?.recovery
            ? `Full re-sync (recovery) ran for ${sourceName(source)}.`
            : `Recovery sync triggered for ${sourceName(source)}.`,
        );
      } else {
        toast.success(
          target?.detail || `Sync triggered for ${sourceName(source)}.`,
        );
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to trigger source sync.",
      );
    } finally {
      setSyncingSourceKey(null);
    }
  }

  /**
   * Shared result handler for all user-event mutations (initial submit, slot
   * pill re-submit, and "Book anyway" override).  Handles the three outcomes:
   *   - conflict → morph dialog into conflict card (keep open)
   *   - other soft-failure → error toast, keep dialog open
   *   - success → success toast, close dialog
   */
  function _handleUserMutationResult(
    responseData: CalendarWorkspaceMutationResponse,
    action: CalendarWorkspaceUserMutationAction,
    pendingMutation: {
      butler_name: string;
      action: CalendarWorkspaceUserMutationAction;
      payload: Record<string, unknown>;
      request_id: string;
    },
  ) {
    const rawResult = responseData.result;
    const status = maybeText(rawResult?.status);

    if (status === "conflict") {
      // Surface the conflict card — do NOT close the dialog or show error toast.
      setUserEventConflict({
        conflicts: responseData.conflicts ?? [],
        suggested_slots: responseData.suggested_slots ?? [],
        pendingMutation,
      });
      return;
    }

    if (!isCalendarMutationOk(rawResult)) {
      const detail = calendarMutationErrorMessage(
        rawResult,
        action === "create"
          ? "Failed to create calendar event."
          : "Failed to update calendar event.",
      );
      toast.error(
        action === "create"
          ? `Failed to create event: ${detail}`
          : `Failed to update event: ${detail}`,
      );
      return;
    }

    // Success path: clear conflict state, close dialog.
    setUserEventConflict(null);
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
    const fallbackOwner = activeUserEntry
      ? resolveEntryOwner(activeUserEntry)
      : null;
    const butlerName =
      selectedCalendar?.butler_name || fallbackOwner?.butlerName || null;
    const calendarId =
      selectedCalendar?.calendar_id || fallbackOwner?.calendarId || null;

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

    // Clear any stale conflict state from a previous attempt.
    setUserEventConflict(null);

    const requestId = buildRequestId(action);
    const pendingMutation = {
      butler_name: butlerName,
      action,
      payload,
      request_id: requestId,
    };

    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: butlerName,
        action,
        request_id: requestId,
        payload,
      });
      _handleUserMutationResult(result.data, action, pendingMutation);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save calendar event.",
      );
    }
  }

  /**
   * Confirm a natural-language quick-add draft. The draft is advisory; this
   * routes it through the SAME user-event create path the structured form uses,
   * with a fresh `request_id` — no separate write path. The owning butler /
   * calendar is resolved exactly like the create dialog (preferred selected
   * source, else the first submittable calendar).
   */
  async function confirmQuickAddDraft(draft: QuickAddDraft) {
    if (submittableCalendars.length === 0) {
      toast.error(
        "No writable calendar sources are available for user events.",
      );
      return;
    }
    const title = draft.title.trim();
    if (!title) {
      toast.error("Title is required.");
      return;
    }

    const selectedCalendar =
      (selectedSourceKey !== "all" &&
        submittableCalendars.find((c) => c.source_key === selectedSourceKey)) ||
      submittableCalendars[0];
    const butlerName = selectedCalendar.butler_name;
    if (!butlerName) {
      toast.error("Could not resolve owning butler for this calendar source.");
      return;
    }

    const payload: Record<string, unknown> = {
      title,
      timezone: defaultTimezone,
      all_day: draft.all_day,
    };
    if (draft.start_at) payload.start_at = draft.start_at;
    if (draft.end_at) payload.end_at = draft.end_at;
    if (draft.location) payload.location = draft.location;
    if (draft.description) payload.description = draft.description;
    if (selectedCalendar.calendar_id)
      payload.calendar_id = selectedCalendar.calendar_id;

    const requestId = buildRequestId("create");
    const pendingMutation = {
      butler_name: butlerName,
      action: "create" as const,
      payload,
      request_id: requestId,
    };
    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: butlerName,
        action: "create",
        request_id: requestId,
        payload,
      });
      _handleUserMutationResult(result.data, "create", pendingMutation);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to add calendar event.",
      );
    }
  }

  /**
   * Re-submit the pending user-event mutation with a different time slot
   * (suggested by the conflict response).  Preserves the original request_id
   * so the audit log can correlate the retry with the initial attempt.
   */
  async function submitConflictSlot(slot: CalendarSuggestedSlot) {
    if (!userEventConflict) {
      return;
    }
    const { pendingMutation } = userEventConflict;
    const updatedPayload = {
      ...pendingMutation.payload,
      start_at: slot.start_at,
      end_at: slot.end_at,
      timezone: slot.timezone,
    };
    const updatedPending = { ...pendingMutation, payload: updatedPayload };
    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: pendingMutation.butler_name,
        action: pendingMutation.action,
        request_id: pendingMutation.request_id, // same request_id per spec
        payload: updatedPayload,
      });
      _handleUserMutationResult(
        result.data,
        pendingMutation.action,
        updatedPending,
      );
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save calendar event.",
      );
    }
  }

  /**
   * Re-submit the pending user-event mutation with conflict_policy='allow_overlap',
   * bypassing the conflict check and booking regardless of overlap.
   */
  async function submitConflictOverride() {
    if (!userEventConflict) {
      return;
    }
    const { pendingMutation } = userEventConflict;
    const overridePayload = {
      ...pendingMutation.payload,
      conflict_policy: "allow_overlap",
    };
    // Use a new request_id since this is a distinct user decision (override, not retry).
    const overrideRequestId = buildRequestId(pendingMutation.action);
    const overridePending = {
      ...pendingMutation,
      payload: overridePayload,
      request_id: overrideRequestId,
    };
    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: pendingMutation.butler_name,
        action: pendingMutation.action,
        request_id: overrideRequestId,
        payload: overridePayload,
      });
      _handleUserMutationResult(
        result.data,
        pendingMutation.action,
        overridePending,
      );
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save calendar event.",
      );
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
    // For a recurring occurrence, pass the chosen scope and the occurrence
    // anchor so the backend can EXDATE ('this'), UNTIL-split ('following'), or
    // delete the whole series ('series').
    if (isRecurringUserEntry(deleteCandidate) && deleteScope !== "series") {
      payload.recurrence_scope = deleteScope;
      payload.instance_start_at = deleteCandidate.start_at;
    }

    try {
      const result = await userEventMutation.mutateAsync({
        butler_name: owner.butlerName,
        action: "delete",
        request_id: buildRequestId("delete"),
        payload,
      });
      const mutationResult = result.data.result;
      if (!isCalendarMutationOk(mutationResult)) {
        const detail = calendarMutationErrorMessage(
          mutationResult,
          "Failed to delete calendar event.",
        );
        toast.error(`Failed to delete event: ${detail}`);
        return;
      }
      const status = maybeText(mutationResult?.status);
      toast.success(
        status ? `Deleted event (${status}).` : "Deleted calendar event.",
      );
      setDeleteCandidate(null);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to delete calendar event.",
      );
    }
  }

  /** Stage a recurring-occurrence edit and open the scope sheet (defaults to 'this'). */
  function openRecurringEdit(
    entry: UnifiedCalendarEntry,
    patch: Record<string, unknown>,
    label: string,
  ) {
    setEditScope("this");
    setRecurringEdit({ entry, patch, label });
  }

  /**
   * Apply the staged recurring edit with the chosen scope. For 'this'/'following'
   * the occurrence anchor (instance_start_at) and recurrence_scope ride along so
   * the backend EXDATE/UNTIL-splits; 'series' edits the whole recurring event.
   */
  async function confirmRecurringEdit() {
    if (!recurringEdit) return;
    const { entry, patch, label } = recurringEdit;
    const { butlerName, calendarId } = resolveOwnerFromEntry(entry);
    if (!butlerName || !entry.provider_event_id) {
      toast.error("Could not resolve calendar owner for this event.");
      return;
    }

    const payload: Record<string, unknown> = {
      event_id: entry.provider_event_id,
      calendar_id: calendarId ?? undefined,
      ...patch,
    };
    if (editScope !== "series") {
      payload.recurrence_scope = editScope;
      payload.instance_start_at = entry.start_at;
    }

    try {
      const response = await userEventMutation.mutateAsync({
        butler_name: butlerName,
        action: "update",
        request_id: buildRequestId("update"),
        payload,
      });
      const result = response.data.result;
      // `isCalendarMutationOk` intentionally treats 'conflict' as non-terminal
      // (handled interactively elsewhere), so check it explicitly here — this
      // sheet has no conflict-resolution path, and silently reporting success
      // would close the dialog without the edit ever landing.
      const status = maybeText(result?.status);
      if (status === "conflict") {
        toast.error(
          `Could not update ${label}: the new time conflicts with another event.`,
        );
        return;
      }
      if (!isCalendarMutationOk(result)) {
        toast.error(
          `Failed to update ${label}: ${calendarMutationErrorMessage(result, "Update failed.")}`,
        );
        return;
      }
      toast.success(`Event ${label} updated.`);
      setRecurringEdit(null);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : `Failed to update ${label}.`,
      );
    }
  }

  function openButlerCreateDialog(initialButler?: string) {
    const butlerName = initialButler ?? availableButlers[0] ?? "";
    setEditingButlerEntry(null);
    setButlerEventDialogMode("create");
    setButlerEventDraft(createDefaultButlerDraft(timezone, butlerName));
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
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Toggle failed",
            );
            toast.error(`${enabled ? "Resume" : "Pause"} failed: ${detail}`);
            return;
          }
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
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Delete failed",
            );
            toast.error(`Delete failed: ${detail}`);
            return;
          }
          toast.success("Event deleted");
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Delete failed");
        },
      },
    );
  }

  function handleButlerSnooze(entry: UnifiedCalendarEntry, iso: string) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for snooze");
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "snooze",
        request_id: newButlerRequestId("snooze"),
        payload: {
          event_id: target.eventId,
          source_hint: target.sourceHint,
          due_at: iso,
        },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Snooze failed",
            );
            toast.error(`Snooze failed: ${detail}`);
            return;
          }
          toast.success(`Snoozed to ${format(new Date(iso), "MMM d, HH:mm")}`);
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Snooze failed");
        },
      },
    );
  }

  function handleButlerDismiss(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for dismiss");
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "dismiss",
        request_id: newButlerRequestId("dismiss"),
        payload: { event_id: target.eventId },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Dismiss failed",
            );
            toast.error(`Dismiss failed: ${detail}`);
            return;
          }
          toast.success("Reminder dismissed");
        },
        onError: (error) => {
          toast.error(
            error instanceof Error ? error.message : "Dismiss failed",
          );
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
      if (
        butlerEventDraft.recurrenceFrequency === "NONE" &&
        !butlerEventDraft.cron.trim()
      ) {
        toast.error(
          "Scheduled events require either a recurrence frequency or cron expression",
        );
        return;
      }
    }
    if (butlerEventDraft.hasUntilAt && !untilAt) {
      toast.error("Until boundary is invalid");
      return;
    }

    const recurrenceRule = buildRRule(
      butlerEventDraft.recurrenceFrequency,
      untilAt,
    );
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

    const action: "create" | "update" =
      butlerEventDialogMode === "create" ? "create" : "update";
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
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Event mutation failed",
            );
            toast.error(
              action === "create"
                ? `Failed to create butler event: ${detail}`
                : `Failed to update butler event: ${detail}`,
            );
            return;
          }
          toast.success(
            action === "create"
              ? "Butler event created"
              : "Butler event updated",
          );
          closeButlerEventDialog(false);
        },
        onError: (error) => {
          toast.error(
            error instanceof Error ? error.message : "Event mutation failed",
          );
        },
      },
    );
  }

  const syncButtonLabel = syncMutation.isPending ? "Syncing..." : "Sync now";
  const canCreateUserEvents =
    view === "user" && submittableCalendars.length > 0;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Masthead */}
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 pb-5">
        <div className="min-w-0">
          <Eyebrow as="div" className="mb-2.5">
            Calendar · {view === "user" ? "User" : "Butler"} view · {timezone} ·{" "}
            {format(anchor, "yyyy")}
          </Eyebrow>
          <Display>{headlineLabel(range, start, end)}</Display>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Mono muted className="mr-1 tabular-nums">
            {entries.length} {entries.length === 1 ? "event" : "events"}
          </Mono>
          <PillButton
            onClick={handleSyncAll}
            disabled={syncMutation.isPending}
            aria-label="Sync all sources now"
          >
            {syncButtonLabel}
          </PillButton>
          <PillButton
            onClick={() => setSearchPaletteOpen(true)}
            aria-label="Search events"
          >
            Search
          </PillButton>
          <PillButton
            onClick={() => setSourcesDialogOpen(true)}
            aria-label="Configure sources"
          >
            Sources
            {disabledSources.size > 0 ? (
              <span className="tabular-nums text-[var(--dim)]">
                · {disabledSources.size} hidden
              </span>
            ) : null}
          </PillButton>
          {view === "butler" ? (
            <CommitButton
              onClick={() => openButlerCreateDialog()}
              disabled={butlerMutation.isPending}
            >
              Create butler event
            </CommitButton>
          ) : (
            <CommitButton
              onClick={() => openUserCreateDialog()}
              disabled={!canCreateUserEvents || userEventMutation.isPending}
              aria-label="Create user event"
            >
              Create event
            </CommitButton>
          )}
        </div>
      </header>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-y border-[var(--border)] py-3">
        <div className="flex items-center gap-2">
          <Eyebrow>View</Eyebrow>
          <div className="flex items-center gap-1">
            {VIEW_OPTIONS.map((option) => (
              <PillButton
                key={option.value}
                active={view === option.value}
                aria-pressed={view === option.value}
                onClick={() => updateQuery({ view: option.value })}
              >
                {option.label}
              </PillButton>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Eyebrow>Range</Eyebrow>
          <div className="flex items-center gap-1">
            {RANGE_OPTIONS.map((option) => (
              <PillButton
                key={option.value}
                active={range === option.value}
                aria-pressed={range === option.value}
                onClick={() => updateQuery({ range: option.value })}
              >
                {option.label}
              </PillButton>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-1">
          <PillButton
            aria-label="Previous range"
            onClick={() =>
              updateQuery({ anchor: shiftAnchor(anchor, range, -1) })
            }
          >
            ‹
          </PillButton>
          <PillButton
            aria-label="Jump to today"
            onClick={() => updateQuery({ anchor: new Date() })}
          >
            Today
          </PillButton>
          <PillButton
            aria-label="Next range"
            onClick={() =>
              updateQuery({ anchor: shiftAnchor(anchor, range, 1) })
            }
          >
            ›
          </PillButton>
        </div>

        <PillButton
          active={activityPanelOpen}
          aria-pressed={activityPanelOpen}
          onClick={() => {
            setActivityPanelOpen((prev) => !prev);
            setFindTimePanelOpen(false);
            setProposalsPanelOpen(false);
            setAuditOffset(0);
          }}
        >
          Activity
        </PillButton>

        <PillButton
          active={findTimePanelOpen}
          aria-pressed={findTimePanelOpen}
          onClick={() => {
            setFindTimePanelOpen((prev) => !prev);
            setActivityPanelOpen(false);
            setProposalsPanelOpen(false);
          }}
        >
          Find time
        </PillButton>

        <PillButton
          active={proposalsPanelOpen}
          aria-pressed={proposalsPanelOpen}
          onClick={() => {
            setProposalsPanelOpen((prev) => !prev);
            setActivityPanelOpen(false);
            setFindTimePanelOpen(false);
          }}
        >
          Proposals
        </PillButton>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 sm:ml-auto">
          <div className="flex items-center gap-2">
            <label
              htmlFor="status-filter"
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
            >
              Status
            </label>
            <select
              id="status-filter"
              className={SELECT_CLASS}
              value={selectedStatus}
              onChange={(event) => updateQuery({ status: event.target.value })}
            >
              <option value="all">All statuses</option>
              {STATUS_FACET_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <label
              htmlFor="type-filter"
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
            >
              Type
            </label>
            <select
              id="type-filter"
              className={SELECT_CLASS}
              value={selectedSourceType}
              onChange={(event) => updateQuery({ kind: event.target.value })}
            >
              <option value="all">All types</option>
              {SOURCE_TYPE_FACET_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            role="switch"
            aria-checked={overlaysEnabled}
            aria-label="Cross-domain overlays"
            title="Domain overlays: finance bills/renewals, travel, relationship dates, health appointments"
            onClick={() => updateQuery({ overlays: !overlaysEnabled })}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-[3px] border px-2 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
              overlaysEnabled
                ? "border-[var(--fg)] bg-foreground/[0.06] text-[var(--fg)]"
                : "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
            )}
          >
            <StateDot state={overlaysEnabled ? "ok" : "waiting"} />
            Overlays
            {overlaysEnabled && overlaysQuery.isFetched && !hasDomainContext ? (
              <span className="text-[var(--dim)]">· none</span>
            ) : null}
          </button>

          {view === "user" ? (
            <>
              <div className="flex items-center gap-2">
                <label
                  htmlFor="calendar-filter"
                  className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
                >
                  Calendar
                </label>
                <select
                  id="calendar-filter"
                  className={SELECT_CLASS}
                  value={selectedCalendarId}
                  onChange={(event) =>
                    updateQuery({ calendar: event.target.value, source: "all" })
                  }
                >
                  <option value="all">All calendars</option>
                  {calendarFilterOptions.map((option) => (
                    <option key={option.calendarId} value={option.calendarId}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2">
                <label
                  htmlFor="source-filter"
                  className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--mfg)]"
                >
                  Source
                </label>
                <select
                  id="source-filter"
                  className={SELECT_CLASS}
                  value={selectedSourceKey}
                  onChange={(event) =>
                    updateQuery({ source: event.target.value })
                  }
                >
                  <option value="all">All sources</option>
                  {userSources
                    .filter((source) =>
                      selectedCalendarId === "all"
                        ? true
                        : source.calendar_id === selectedCalendarId,
                    )
                    .map((source) => (
                      <option key={source.source_key} value={source.source_key}>
                        {sourceName(source)}
                      </option>
                    ))}
                </select>
              </div>
            </>
          ) : null}
        </div>
      </div>

      {/* Natural-language quick-add (parse-then-confirm) — user view only */}
      {view === "user" ? (
        <div className="flex items-start border-b border-[var(--border)] py-3">
          <QuickAddBar
            timezone={defaultTimezone}
            butlerName={
              (selectedSourceKey !== "all"
                ? submittableCalendars.find(
                    (c) => c.source_key === selectedSourceKey,
                  )
                : submittableCalendars[0]
              )?.butler_name ?? undefined
            }
            disabled={!canCreateUserEvents}
            onConfirm={confirmQuickAddDraft}
          />
        </div>
      ) : null}

      {/* Day-briefing card ("tomorrow at a glance") — structured overlay summary,
          shown alongside the overlays layer. No per-open LLM call. */}
      {overlaysEnabled ? (
        <div className="border-b border-[var(--border)] py-3">
          <DayBriefingCard
            heading={`Tomorrow · ${format(briefingDate, "EEE, MMM d")}`}
            isLoading={dayBriefingQuery.isLoading}
            groups={dayBriefing?.groups ?? []}
            hasDomainContext={dayBriefing?.has_domain_context ?? false}
            hasEntries={dayBriefing?.has_entries ?? false}
            onSelectEntry={handleBriefingSelect}
          />
        </div>
      ) : null}

      {/* Activity panel — overlays the canvas when open */}
      {activityPanelOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarActivityPanel
            auditQuery={auditQuery}
            offset={auditOffset}
            limit={AUDIT_PAGE_SIZE}
            onPageChange={setAuditOffset}
          />
        </div>
      ) : null}

      {/* Find-time panel — overlays the canvas when open */}
      {findTimePanelOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarFindTimePanel
            butlerName={findTimeButlerName}
            onSelectSlot={(slot) => {
              openUserCreateDialog(
                parseISO(slot.start_at),
                parseISO(slot.end_at),
              );
              setFindTimePanelOpen(false);
            }}
          />
        </div>
      ) : null}

      {/* Proposals panel — overlays the canvas when open */}
      {proposalsPanelOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarProposalsPanel
            entries={proposalEntries}
            isLoading={proposalsQuery.isLoading}
            isError={proposalsQuery.isError}
            error={
              proposalsQuery.error instanceof Error
                ? proposalsQuery.error
                : null
            }
            acceptMutation={acceptProposalMutation}
            dismissMutation={dismissProposalMutation}
          />
        </div>
      ) : null}

      {/* Canvas + detail panel */}
      <div
        className={
          activityPanelOpen || findTimePanelOpen || proposalsPanelOpen
            ? "hidden"
            : "flex min-h-0 flex-1"
        }
      >
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          {workspaceQuery.isLoading ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Drawing the calendar…
            </Voice>
          ) : workspaceQuery.isError ? (
            <div role="alert" className="flex items-start gap-2 py-1">
              <StateDot state="error" className="mt-[7px]" />
              <p className="text-sm text-[var(--fg)]">
                The calendar workspace failed to load.{" "}
                <span className="text-[var(--mfg)]">
                  {workspaceQuery.error instanceof Error
                    ? workspaceQuery.error.message
                    : "Unknown error"}
                </span>
              </p>
            </div>
          ) : view === "butler" ? (
            /* ---- Butler lanes ---- */
            butlerLaneRows.length === 0 ? (
              <Voice variant="italic" className="text-[var(--mfg)]">
                No butler lanes yet.
              </Voice>
            ) : (
              <div className="min-h-0 flex-1 space-y-8 overflow-y-auto pr-1">
                {butlerLaneRows.map((lane) => (
                  <section key={lane.laneId}>
                    <div className="mb-1 flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2">
                      <div className="flex min-w-0 items-center gap-2.5">
                        <ButlerMark name={lane.butlerName} tone="fill" />
                        <span className="truncate text-[15px] font-medium text-[var(--fg)]">
                          {lane.title}
                        </span>
                        <Mono muted className="tabular-nums">
                          {lane.entries.length}{" "}
                          {lane.entries.length === 1 ? "event" : "events"}
                        </Mono>
                      </div>
                      <PillButton
                        onClick={() => openButlerCreateDialog(lane.butlerName)}
                        disabled={butlerMutation.isPending}
                      >
                        Add event
                      </PillButton>
                    </div>

                    {lane.entries.length === 0 ? (
                      <Voice
                        variant="italic"
                        className="py-2 text-[var(--mfg)]"
                      >
                        No events in this lane.
                      </Voice>
                    ) : (
                      <div role="list">
                        {capLaneEntriesByDay(
                          lane.entries,
                          RECURRING_INSTANCE_CAP,
                        ).map((item) =>
                          isOverflowSentinel(item) ? (
                            <div
                              key={item.sentinelKey}
                              data-testid="butler-lane-row"
                              className="border-b border-[var(--border-soft)] py-2 pl-[68px]"
                            >
                              <span className="font-serif text-[13px] italic text-[var(--mfg)]">
                                and {item.hiddenCount} more instance
                                {item.hiddenCount === 1 ? "" : "s"} of &ldquo;
                                {item.title}&rdquo;
                              </span>
                            </div>
                          ) : (
                            <Row
                              key={item.entry_id}
                              data-testid="butler-lane-row"
                              mark={
                                <Mono
                                  muted
                                  className="inline-block w-14 tabular-nums"
                                >
                                  {item.all_day
                                    ? "all day"
                                    : format(new Date(item.start_at), "HH:mm")}
                                </Mono>
                              }
                              meta={
                                <div className="flex items-center gap-1.5">
                                  <PillButton
                                    onClick={() => openDetailPanel(item)}
                                    disabled={butlerMutation.isPending}
                                  >
                                    Edit
                                  </PillButton>
                                  <PillButton
                                    onClick={() => handleButlerToggle(item)}
                                    disabled={butlerMutation.isPending}
                                  >
                                    {isPausedEntry(item) ? "Resume" : "Pause"}
                                  </PillButton>
                                  <SnoozeMenu
                                    disabled={butlerMutation.isPending}
                                    onSnooze={(iso) =>
                                      handleButlerSnooze(item, iso)
                                    }
                                  />
                                  {item.source_type === "butler_reminder" ? (
                                    <PillButton
                                      data-testid="butler-dismiss-button"
                                      onClick={() => handleButlerDismiss(item)}
                                      disabled={butlerMutation.isPending}
                                      className="hover:border-[var(--red)] hover:text-[var(--red)]"
                                    >
                                      Dismiss
                                    </PillButton>
                                  ) : null}
                                  <PillButton
                                    onClick={() => handleButlerDelete(item)}
                                    disabled={butlerMutation.isPending}
                                    className="hover:border-[var(--red)] hover:text-[var(--red)]"
                                  >
                                    Delete
                                  </PillButton>
                                </div>
                              }
                            >
                              <div className="flex min-w-0 items-center gap-2">
                                <span className="truncate text-sm text-[var(--fg)]">
                                  {item.title}
                                </span>
                                <KindTag>
                                  {item.source_type === "scheduled_task"
                                    ? "schedule"
                                    : "reminder"}
                                </KindTag>
                                {isPausedEntry(item) ? (
                                  <KindTag className="text-[var(--mfg)]">
                                    paused
                                  </KindTag>
                                ) : null}
                              </div>
                            </Row>
                          ),
                        )}
                      </div>
                    )}
                  </section>
                ))}
              </div>
            )
          ) : range === "month" ? (
            /* ---- Month matrix ---- */
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="mb-2 grid grid-cols-7">
                {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                  (label) => (
                    <Eyebrow key={label} as="div" className="px-2">
                      {label}
                    </Eyebrow>
                  ),
                )}
              </div>
              <div className="grid min-h-0 flex-1 grid-cols-7 grid-rows-6 overflow-y-auto border-l border-t border-[var(--border)]">
                {monthDays.map((day) => {
                  const key = format(day, "yyyy-MM-dd");
                  const dayEntries = entriesByDay.get(key) ?? [];
                  const dayOverlays = overlaysEnabled
                    ? (overlaysByDayMap.get(key) ?? [])
                    : [];
                  const inMonth = isSameMonth(day, start);
                  const today = isToday(day);
                  return (
                    <div
                      key={key}
                      className={cn(
                        "relative min-h-[5.5rem] overflow-hidden border-b border-r border-[var(--border)] p-1.5",
                        !inMonth && "bg-foreground/[0.02]",
                      )}
                    >
                      {view === "user" ? (
                        <button
                          type="button"
                          aria-label={`Create event on ${format(day, "EEE, MMM d")}`}
                          onClick={() => openUserCreateDialog(day)}
                          className="absolute inset-0 z-0 cursor-pointer transition-colors hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--fg)]/30"
                        />
                      ) : null}
                      <div className="pointer-events-none relative z-10">
                        <div className="mb-1 flex items-center justify-between">
                          {today ? (
                            <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-[4px] bg-[var(--fg)] px-1 font-mono text-[11px] tabular-nums text-[var(--bg)]">
                              {format(day, "d")}
                            </span>
                          ) : (
                            <span
                              className={cn(
                                "font-mono text-[11px] tabular-nums",
                                inMonth
                                  ? "text-[var(--fg)]"
                                  : "text-[var(--dim)]",
                              )}
                            >
                              {format(day, "d")}
                            </span>
                          )}
                        </div>
                        <div className="space-y-0.5">
                          {dayEntries.slice(0, 3).map((entry) => (
                            <button
                              key={entry.entry_id}
                              type="button"
                              data-calendar-entry-id={entry.entry_id}
                              title={entry.title}
                              onClick={() => openDetailPanel(entry)}
                              className="pointer-events-auto flex w-full items-center gap-1 truncate rounded-[2px] px-1 py-0.5 text-left text-[11px] text-[var(--fg)] transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                            >
                              {!entry.all_day ? (
                                <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                                  {format(new Date(entry.start_at), "HH:mm")}
                                </span>
                              ) : null}
                              <span className="truncate">{entry.title}</span>
                            </button>
                          ))}
                          {dayEntries.length > 3 ? (
                            <button
                              type="button"
                              aria-label={`${dayEntries.length - 3} more on ${format(day, "MMM d")} — open day view`}
                              onClick={() =>
                                updateQuery({ range: "day", anchor: day })
                              }
                              className="pointer-events-auto block px-1 font-mono text-[10px] tabular-nums text-[var(--mfg)] transition-colors hover:text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
                            >
                              +{dayEntries.length - 3} more
                            </button>
                          ) : null}
                          {dayOverlays.length > 0 ? (
                            <div className="mt-1 space-y-0.5 border-t border-dashed border-[var(--border)] pt-1">
                              {dayOverlays.slice(0, 3).map((overlay) => (
                                <OverlayPill
                                  key={overlay.entry_id}
                                  entry={overlay}
                                />
                              ))}
                              {dayOverlays.length > 3 ? (
                                <span className="block px-1 font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                                  +{dayOverlays.length - 3} overlay
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : range === "week" || range === "day" ? (
            /* ---- Time grid — mono gutter, no hour rules (Design Language: Calendar) ---- */
            <div className="flex min-h-0 flex-1 flex-col">
              {/* Column headers */}
              <div
                className="mb-2 grid"
                style={{
                  gridTemplateColumns:
                    range === "week"
                      ? "3.25rem repeat(7, minmax(0, 1fr))"
                      : "3.25rem minmax(0, 1fr)",
                }}
              >
                <div />
                {weekDays.map((day) => (
                  <div
                    key={format(day, "yyyy-MM-dd")}
                    className="px-2 text-center"
                  >
                    <Eyebrow className={cn(isToday(day) && "text-[var(--fg)]")}>
                      {format(day, "EEE")}
                    </Eyebrow>{" "}
                    <span
                      className={cn(
                        "font-mono text-[12px] tabular-nums",
                        isToday(day) ? "text-[var(--fg)]" : "text-[var(--mfg)]",
                      )}
                    >
                      {format(day, "d")}
                    </span>
                  </div>
                ))}
              </div>

              {/* All-day row */}
              {(() => {
                const hasAllDay = weekDays.some((day) =>
                  (entriesByDay.get(format(day, "yyyy-MM-dd")) ?? []).some(
                    (e) => e.all_day,
                  ),
                );
                const hasOverlays =
                  overlaysEnabled &&
                  weekDays.some(
                    (day) =>
                      (overlaysByDayMap.get(format(day, "yyyy-MM-dd")) ?? [])
                        .length > 0,
                  );
                if (!hasAllDay && !hasOverlays) return null;
                return (
                  <div
                    className="mb-2 grid border-b border-[var(--border)] pb-2"
                    style={{
                      gridTemplateColumns:
                        range === "week"
                          ? "3.25rem repeat(7, minmax(0, 1fr))"
                          : "3.25rem minmax(0, 1fr)",
                    }}
                  >
                    <div className="pr-2 pt-0.5 text-right">
                      <Eyebrow>All day</Eyebrow>
                    </div>
                    {weekDays.map((day) => {
                      const key = format(day, "yyyy-MM-dd");
                      const allDayEntries = (
                        entriesByDay.get(key) ?? []
                      ).filter((e) => e.all_day);
                      const dayOverlays = overlaysEnabled
                        ? (overlaysByDayMap.get(key) ?? [])
                        : [];
                      return (
                        <div key={key} className="space-y-1 px-1">
                          {allDayEntries.map((entry) => (
                            <button
                              key={entry.entry_id}
                              type="button"
                              data-calendar-entry-id={entry.entry_id}
                              title={entry.title}
                              onClick={(evt) => {
                                evt.stopPropagation();
                                openDetailPanel(entry);
                              }}
                              className="block w-full truncate rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 text-left text-[11px] text-[var(--fg)] transition-colors hover:bg-foreground/[0.06]"
                            >
                              {entry.title}
                            </button>
                          ))}
                          {dayOverlays.map((overlay) => (
                            <OverlayPill
                              key={overlay.entry_id}
                              entry={overlay}
                            />
                          ))}
                        </div>
                      );
                    })}
                  </div>
                );
              })()}

              {/* Scrollable time grid */}
              <div ref={timeGridRef} className="min-h-0 flex-1 overflow-y-auto">
                <div
                  ref={gridBodyRef}
                  className="grid h-[var(--calendar-grid-height)]"
                  style={{
                    gridTemplateColumns:
                      range === "week"
                        ? "3.25rem repeat(7, minmax(0, 1fr))"
                        : "3.25rem minmax(0, 1fr)",
                  }}
                >
                  {/* Hour gutter */}
                  <div ref={gutterRef} className="relative">
                    {HOURS.map((h) => (
                      <div
                        key={h}
                        className="absolute right-2 -translate-y-1/2 font-mono text-[10px] leading-none tabular-nums text-[var(--mfg)]"
                        style={{ top: h * HOUR_HEIGHT_PX }}
                      >
                        {h === 0
                          ? ""
                          : format(new Date(2000, 0, 1, h), "HH:mm")}
                      </div>
                    ))}
                  </div>

                  {/* Day columns */}
                  {weekDays.map((day, dayIndex) => {
                    const key = format(day, "yyyy-MM-dd");
                    const dayEntries = (entriesByDay.get(key) ?? []).filter(
                      (e) => !e.all_day,
                    );
                    const ghost =
                      movedGhost &&
                      isSameDay(new Date(movedGhost.prevStartIso), day)
                        ? movedGhost
                        : null;
                    return (
                      <div
                        key={key}
                        className="relative border-l border-[var(--border)]"
                      >
                        {view === "user" ? (
                          <button
                            type="button"
                            aria-label={`Create event on ${format(day, "EEE, MMM d")}`}
                            className="absolute inset-0 z-0 cursor-pointer touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--fg)]/30"
                            onPointerDown={(evt) =>
                              beginCreateDrag(evt, dayIndex)
                            }
                            onPointerMove={handleGridPointerMove}
                            onPointerUp={handleGridPointerUp}
                            onPointerCancel={handleGridPointerCancel}
                            onClick={(evt) => {
                              // Swallow the click that trails a completed drag-create.
                              if (suppressClickRef.current) {
                                suppressClickRef.current = false;
                                return;
                              }
                              const rect =
                                evt.currentTarget.getBoundingClientRect();
                              // Keyboard activation (detail === 0) carries no pointer Y — default to the day.
                              if (evt.detail === 0) {
                                openUserCreateDialog(day);
                                return;
                              }
                              const yPx = evt.clientY - rect.top;
                              const snappedMin = Math.max(
                                0,
                                Math.floor(((yPx / HOUR_HEIGHT_PX) * 60) / 30) *
                                  30,
                              );
                              const clickedDate = new Date(day);
                              clickedDate.setHours(
                                Math.floor(snappedMin / 60),
                                snappedMin % 60,
                                0,
                                0,
                              );
                              openUserCreateDialog(clickedDate);
                            }}
                          />
                        ) : null}
                        {/* Drag preview (create / move / resize landing in this column). */}
                        {gridDrag && gridDrag.dayIndex === dayIndex ? (
                          <div
                            aria-hidden
                            data-testid="calendar-drag-preview"
                            className="pointer-events-none absolute inset-x-0.5 z-20 rounded-[3px] border border-dashed border-[var(--fg)]/60 bg-[var(--fg)]/10"
                            style={{
                              top: (gridDrag.startMin / 60) * HOUR_HEIGHT_PX,
                              height:
                                ((gridDrag.endMin - gridDrag.startMin) / 60) *
                                HOUR_HEIGHT_PX,
                              minHeight: 16,
                            }}
                          >
                            <span className="block px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-[var(--fg)]">
                              {formatMinuteLabel(gridDrag.startMin)}–
                              {formatMinuteLabel(gridDrag.endMin)}
                            </span>
                          </div>
                        ) : null}
                        {/* "Moved" ghost — one-click undo of the last move/resize. */}
                        {ghost ? (
                          <button
                            type="button"
                            data-testid="calendar-move-ghost"
                            title="Undo move"
                            onClick={(evt) => {
                              evt.stopPropagation();
                              undoMove();
                            }}
                            className="absolute inset-x-0.5 z-20 flex items-center justify-center rounded-[3px] border border-dashed border-[var(--fg)]/40 bg-[var(--bg)]/70 text-[10px] font-medium text-[var(--mfg)] transition-colors hover:text-[var(--fg)]"
                            style={{
                              top:
                                (minuteOfDay(new Date(ghost.prevStartIso)) /
                                  60) *
                                HOUR_HEIGHT_PX,
                              height: Math.max(
                                (differenceInMinutes(
                                  new Date(ghost.prevEndIso),
                                  new Date(ghost.prevStartIso),
                                ) /
                                  60) *
                                  HOUR_HEIGHT_PX,
                                16,
                              ),
                            }}
                          >
                            Undo
                          </button>
                        ) : null}
                        {dayEntries.map((entry) => {
                          const s = new Date(entry.start_at);
                          const e = new Date(entry.end_at);
                          const topMin = getHours(s) * 60 + getMinutes(s);
                          const durationMin = Math.max(
                            differenceInMinutes(e, s),
                            15,
                          );
                          const topPx = (topMin / 60) * HOUR_HEIGHT_PX;
                          const heightPx = (durationMin / 60) * HOUR_HEIGHT_PX;
                          const paused = isPausedEntry(entry);
                          const draggable = isGridDraggable(entry);
                          const isDragSource =
                            gridDrag &&
                            gridDrag.mode !== "create" &&
                            gridDrag.entryId === entry.entry_id;
                          return (
                            <button
                              key={entry.entry_id}
                              type="button"
                              data-calendar-entry-id={entry.entry_id}
                              className={cn(
                                "absolute inset-x-0.5 z-10 overflow-hidden rounded-[3px] border border-[var(--border)] bg-[var(--bg)] px-1.5 py-0.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
                                paused && "opacity-50",
                                draggable &&
                                  "cursor-grab touch-none active:cursor-grabbing",
                                isDragSource && "opacity-40",
                              )}
                              style={{
                                top: topPx,
                                height: heightPx,
                                minHeight: 16,
                              }}
                              title={`${format(s, "HH:mm")}–${format(e, "HH:mm")} · ${entry.title}`}
                              onPointerDown={
                                draggable
                                  ? (evt) => beginMoveDrag(evt, entry)
                                  : undefined
                              }
                              onPointerMove={
                                draggable ? handleGridPointerMove : undefined
                              }
                              onPointerUp={
                                draggable ? handleGridPointerUp : undefined
                              }
                              onPointerCancel={
                                draggable ? handleGridPointerCancel : undefined
                              }
                              onClick={() => {
                                // Swallow the click that trails a completed move/resize drag.
                                if (suppressClickRef.current) {
                                  suppressClickRef.current = false;
                                  return;
                                }
                                openDetailPanel(entry);
                              }}
                            >
                              <span className="block truncate text-[11px] font-medium leading-tight text-[var(--fg)]">
                                {entry.title}
                              </span>
                              {heightPx >= 32 ? (
                                <span className="mt-0.5 block truncate font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                                  {format(s, "HH:mm")}–{format(e, "HH:mm")}
                                </span>
                              ) : null}
                              {draggable ? (
                                <span
                                  aria-hidden
                                  data-testid="calendar-resize-handle"
                                  className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize touch-none"
                                  onPointerDown={(evt) =>
                                    beginResizeDrag(evt, entry, dayIndex)
                                  }
                                  onPointerMove={handleGridPointerMove}
                                  onPointerUp={handleGridPointerUp}
                                  onPointerCancel={handleGridPointerCancel}
                                />
                              ) : null}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : entries.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              No events in this range.
            </Voice>
          ) : (
            /* ---- Agenda list, grouped by day ---- */
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              {(() => {
                const groups: Array<{
                  day: string;
                  date: Date;
                  items: UnifiedCalendarEntry[];
                }> = [];
                const groupIndex = new Map<string, number>();
                for (const entry of entries) {
                  const d = new Date(entry.start_at);
                  const dayKey = format(d, "yyyy-MM-dd");
                  let gi = groupIndex.get(dayKey);
                  if (gi === undefined) {
                    gi = groups.length;
                    groupIndex.set(dayKey, gi);
                    groups.push({
                      day: dayKey,
                      date: startOfDay(d),
                      items: [],
                    });
                  }
                  groups[gi].items.push(entry);
                }
                return groups.map((group) => (
                  <section key={group.day} className="mb-6">
                    <div className="mb-1 flex items-baseline gap-2 border-b border-[var(--border)] pb-1.5">
                      <Eyebrow
                        className={cn(
                          isToday(group.date) && "text-[var(--fg)]",
                        )}
                      >
                        {format(group.date, "EEE · MMM d")}
                      </Eyebrow>
                      {isToday(group.date) ? (
                        <KindTag className="text-[var(--mfg)]">today</KindTag>
                      ) : null}
                    </div>
                    <div role="list">
                      {group.items.map((entry) => {
                        const canMutate =
                          view === "user" &&
                          entry.source_type === "provider_event" &&
                          !!entry.provider_event_id &&
                          entry.editable;
                        return (
                          <Row
                            key={entry.entry_id}
                            mark={
                              <Mono
                                muted
                                className="inline-block w-14 tabular-nums"
                              >
                                {entry.all_day
                                  ? "all day"
                                  : format(new Date(entry.start_at), "HH:mm")}
                              </Mono>
                            }
                            meta={
                              <div className="flex items-center gap-1.5">
                                <PillButton
                                  onClick={() => openDetailPanel(entry)}
                                  disabled={userEventMutation.isPending}
                                >
                                  Detail
                                </PillButton>
                                <PillButton
                                  onClick={() => {
                                    setDeleteScope(
                                      isRecurringUserEntry(entry)
                                        ? "this"
                                        : "series",
                                    );
                                    setDeleteCandidate(entry);
                                  }}
                                  disabled={
                                    !canMutate || userEventMutation.isPending
                                  }
                                  className="hover:border-[var(--red)] hover:text-[var(--red)]"
                                >
                                  Delete
                                </PillButton>
                              </div>
                            }
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              {entry.butler_name ? (
                                <ButlerMark name={entry.butler_name} />
                              ) : null}
                              <span
                                className={cn(
                                  "truncate text-sm text-[var(--fg)]",
                                  isCancelledEntry(entry) &&
                                    "text-[var(--mfg)] line-through",
                                )}
                              >
                                {entry.title}
                              </span>
                              {isCancelledEntry(entry) ? (
                                <KindTag
                                  data-testid="entry-cancelled-tag"
                                  className="text-[var(--red)]"
                                >
                                  cancelled
                                </KindTag>
                              ) : null}
                              <Mono muted className="hidden truncate sm:inline">
                                {entry.butler_name ?? entry.source_key}
                              </Mono>
                            </div>
                          </Row>
                        );
                      })}
                    </div>
                  </section>
                ));
              })()}
            </div>
          )}
        </div>

        {/* Right-docked detail panel */}
        {selectedEntry ? (
          <aside
            data-testid="entry-detail-panel-aside"
            className="flex min-h-0 w-80 shrink-0 flex-col border-l border-[var(--border)] pl-5 pt-5"
          >
            <CalendarEntryDetailPanel
              key={selectedEntry.entry_id}
              entry={selectedEntry}
              onClose={closeDetailPanel}
              onDelete={(entry) => {
                setDeleteScope(isRecurringUserEntry(entry) ? "this" : "series");
                setDeleteCandidate(entry);
                closeDetailPanel();
              }}
              onRecurringEdit={openRecurringEdit}
              userMutation={userEventMutation}
              butlerMutation={butlerMutation}
            />
          </aside>
        ) : null}
      </div>

      {searchPaletteOpen ? (
        <CalendarSearchPalette
          open={searchPaletteOpen}
          onOpenChange={setSearchPaletteOpen}
          view={view}
          timezone={timezone}
          onJump={handleSearchJump}
        />
      ) : null}

      <Dialog open={sourcesDialogOpen} onOpenChange={setSourcesDialogOpen}>
        <DialogContent className="w-[90vw] max-w-[90vw] sm:w-[80vw] sm:max-w-[80vw] max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Configure Sources</DialogTitle>
            <DialogDescription>
              Connected Google accounts and their calendars. Toggle a calendar
              to enable or disable it as a sync source; a disabled calendar is
              skipped by sync and hidden from the view.
            </DialogDescription>
          </DialogHeader>
          {(() => {
            const accounts = accountsQuery.data?.data.accounts ?? [];
            const healthAvailable =
              accountsQuery.data?.data.health_available ?? true;
            if (accounts.length === 0) return null;
            return (
              <div
                className="mb-3 space-y-1.5"
                role="list"
                aria-label="Connected accounts"
              >
                <Mono muted className="text-[11px] uppercase tracking-wide">
                  Accounts
                </Mono>
                {accounts.map((account) => (
                  <Row
                    key={account.account_id}
                    meta={
                      <span className="inline-flex items-center gap-1.5">
                        <StateDot
                          state={accountHealthDotState(account.health.state)}
                        />
                        <Mono muted>
                          {healthAvailable
                            ? account.health.state
                            : "health unavailable"}
                        </Mono>
                      </span>
                    }
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        className="truncate text-sm font-medium text-[var(--fg)]"
                        title={
                          account.email ??
                          account.display_name ??
                          account.account_id
                        }
                      >
                        {account.email ||
                          account.display_name ||
                          account.account_id}
                      </span>
                      {account.is_primary ? (
                        <KindTag className="text-[var(--fg)]">primary</KindTag>
                      ) : null}
                      <KindTag>{account.status}</KindTag>
                      {account.health.error_message ? (
                        <span
                          className="max-w-[16rem] truncate text-[11px] text-[var(--red)]"
                          title={account.health.error_message}
                        >
                          {account.health.error_message}
                        </span>
                      ) : null}
                    </div>
                  </Row>
                ))}
              </div>
            );
          })()}
          {metaQuery.isLoading && connectedSources.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Reading source metadata…
            </Voice>
          ) : connectedSources.length === 0 ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              No connected sources.
            </Voice>
          ) : (
            <div role="list">
              {[...connectedSources]
                .sort((a, b) => {
                  // Sort: primary first, then user-email calendars, then enabled, then disabled
                  const aPrimary =
                    a.lane === "user" && a.calendar_id === primaryCalendarId
                      ? 1
                      : 0;
                  const bPrimary =
                    b.lane === "user" && b.calendar_id === primaryCalendarId
                      ? 1
                      : 0;
                  if (aPrimary !== bPrimary) return bPrimary - aPrimary;

                  const aIsAcct =
                    a.provider === "google" &&
                    a.calendar_id &&
                    googleAccountEmails.has(a.calendar_id)
                      ? 1
                      : 0;
                  const bIsAcct =
                    b.provider === "google" &&
                    b.calendar_id &&
                    googleAccountEmails.has(b.calendar_id)
                      ? 1
                      : 0;
                  if (aIsAcct !== bIsAcct) return bIsAcct - aIsAcct;

                  const aOff = disabledSources.has(a.source_key) ? 1 : 0;
                  const bOff = disabledSources.has(b.source_key) ? 1 : 0;
                  return aOff - bOff;
                })
                .map((source) => {
                  const isPrimary =
                    source.lane === "user" &&
                    source.calendar_id != null &&
                    source.calendar_id === primaryCalendarId;
                  const canSetPrimary =
                    source.lane === "user" &&
                    source.writable &&
                    source.calendar_id != null &&
                    source.calendar_id !== primaryCalendarId &&
                    source.butler_name != null;
                  const isEnabled = !disabledSources.has(source.source_key);
                  const acctEmail =
                    typeof source.metadata?.account_email === "string"
                      ? source.metadata.account_email
                      : undefined;
                  const calIdDisplay = (() => {
                    if (
                      acctEmail &&
                      source.calendar_id &&
                      source.calendar_id !== acctEmail
                    ) {
                      return `${acctEmail} ${truncateCalendarId(source.calendar_id)}`;
                    }
                    return truncateCalendarId(
                      source.calendar_id ??
                        source.provider ??
                        source.source_kind,
                    );
                  })();

                  return (
                    <Row
                      key={source.source_key}
                      className={cn(!isEnabled && "opacity-50")}
                      mark={
                        <Checkbox
                          checked={isEnabled}
                          onCheckedChange={() => toggleSourceEnabled(source)}
                          aria-label={`Toggle ${sourceName(source)}`}
                        />
                      }
                      meta={
                        <div className="flex items-center gap-1.5">
                          {canSetPrimary ? (
                            <PillButton
                              onClick={() => {
                                primaryMutation.mutate(
                                  {
                                    butler_name: source.butler_name!,
                                    calendar_id: source.calendar_id!,
                                  },
                                  {
                                    onSuccess: (response) => {
                                      if (response.data.persisted === false) {
                                        toast.error(
                                          "Failed to set primary: change was not persisted",
                                        );
                                        return;
                                      }
                                      toast.success("Primary calendar updated");
                                    },
                                    onError: (err) =>
                                      toast.error(
                                        `Failed to set primary: ${err.message}`,
                                      ),
                                  },
                                );
                              }}
                              disabled={primaryMutation.isPending}
                            >
                              Set as primary
                            </PillButton>
                          ) : null}
                          <PillButton
                            onClick={() => handleSyncSource(source)}
                            disabled={
                              syncMutation.isPending || !source.butler_name
                            }
                          >
                            {syncingSourceKey === source.source_key
                              ? "Syncing..."
                              : "Sync now"}
                          </PillButton>
                          <PillButton
                            onClick={() =>
                              handleSyncSource(source, { full: true })
                            }
                            disabled={
                              syncMutation.isPending || !source.butler_name
                            }
                            title="Full re-sync from scratch (cursor recovery)"
                          >
                            Recover
                          </PillButton>
                          {source.error_kind === "token_expired" ||
                          source.error_kind === "auth" ? (
                            <Link
                              to="/ingestion?tab=connectors"
                              className="inline-flex items-center rounded-[3px] border border-[var(--red)] px-2 py-0.5 text-[11px] font-medium text-[var(--red)] transition-colors hover:bg-[var(--red)]/10"
                              title="This source needs re-authorization"
                            >
                              Reconnect
                            </Link>
                          ) : null}
                        </div>
                      }
                    >
                      <div className="flex min-w-0 flex-col gap-1">
                        <div className="flex min-w-0 items-center gap-2">
                          {source.butler_name ? (
                            <ButlerMark name={source.butler_name} />
                          ) : null}
                          <span
                            className="truncate text-sm font-medium text-[var(--fg)]"
                            title={sourceName(source)}
                          >
                            {sourceName(source)}
                          </span>
                          {isPrimary ? (
                            <KindTag className="text-[var(--fg)]">
                              primary
                            </KindTag>
                          ) : null}
                          <KindTag>{source.lane}</KindTag>
                        </div>
                        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                          <span
                            className="min-w-0 max-w-full truncate"
                            title={source.calendar_id ?? undefined}
                          >
                            <Mono muted>{calIdDisplay}</Mono>
                          </span>
                          <span className="inline-flex items-center gap-1.5">
                            <StateDot state={syncDotState(source.sync_state)} />
                            <Mono muted>{source.sync_state}</Mono>
                          </span>
                          <Mono muted>
                            {formatStaleness(source.staleness_ms)}
                            {formatOptionalTimestamp(source.last_success_at)
                              ? ` \u00b7 ${formatOptionalTimestamp(source.last_success_at)}`
                              : ""}
                          </Mono>
                          {source.last_error ? (
                            <span
                              className="inline-flex min-w-0 items-center gap-1.5"
                              title={source.last_error}
                            >
                              <StateDot state="error" />
                              {source.error_kind &&
                              source.error_kind !== "none" ? (
                                <KindTag className="text-[var(--red)]">
                                  {source.error_kind}
                                </KindTag>
                              ) : null}
                              <span className="max-w-[16rem] truncate text-[11px] text-[var(--red)]">
                                {source.last_error}
                              </span>
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </Row>
                  );
                })}
            </div>
          )}
          <DialogFooter>
            <PillButton onClick={() => setSourcesDialogOpen(false)}>
              Close
            </PillButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={userEventDialogOpen}
        onOpenChange={(open) => {
          setUserEventDialogOpen(open);
          if (!open) {
            setUserEventForm(null);
            setActiveUserEntry(null);
            setUserEventConflict(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {userEventDialogMode === "create"
                ? "Create user event"
                : "Edit user event"}
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
                  className={FIELD_SELECT_CLASS}
                  value={userEventForm.sourceKey}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current
                        ? { ...current, sourceKey: event.target.value }
                        : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                >
                  {submittableCalendars.map((calendar) => (
                    <option
                      key={calendar.source_key}
                      value={calendar.source_key}
                    >
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
                      current
                        ? { ...current, title: event.target.value }
                        : current,
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
                        current
                          ? { ...current, startAtLocal: event.target.value }
                          : current,
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
                        current
                          ? { ...current, endAtLocal: event.target.value }
                          : current,
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
                      current
                        ? { ...current, timezone: event.target.value }
                        : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                />
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="event-description"
                  className="text-sm font-medium"
                >
                  Description
                </label>
                <Textarea
                  id="event-description"
                  value={userEventForm.description}
                  onChange={(event) =>
                    setUserEventForm((current) =>
                      current
                        ? { ...current, description: event.target.value }
                        : current,
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
                      current
                        ? { ...current, location: event.target.value }
                        : current,
                    )
                  }
                  disabled={userEventMutation.isPending}
                />
              </div>

              {/* Conflict card — rendered when the backend returns status='conflict' */}
              {userEventConflict ? (
                <div
                  className="rounded-md border border-[var(--amber,#f59e0b)] bg-[color-mix(in_srgb,var(--amber,#f59e0b)_8%,transparent)] p-3 space-y-3"
                  data-testid="conflict-card"
                >
                  {/* Amber chip */}
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full bg-[var(--amber,#f59e0b)] px-2.5 py-0.5 text-xs font-medium text-white">
                      Overlaps {userEventConflict.conflicts.length} event
                      {userEventConflict.conflicts.length !== 1 ? "s" : ""}
                    </span>
                  </div>

                  {/* Conflicting events — muted ghost blocks */}
                  {userEventConflict.conflicts.length > 0 ? (
                    <ul className="space-y-1">
                      {userEventConflict.conflicts.slice(0, 3).map((c) => (
                        <li
                          key={c.event_id}
                          className="flex items-baseline gap-2 text-sm opacity-60"
                        >
                          <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0 mt-1.5" />
                          <span className="min-w-0 truncate font-medium">
                            {c.title}
                          </span>
                          <span className="shrink-0 tabular-nums text-xs">
                            {format(parseISO(c.start_at), "h:mm a")}–
                            {format(parseISO(c.end_at), "h:mm a")}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}

                  {/* Suggested-slot pills */}
                  {userEventConflict.suggested_slots.length > 0 ? (
                    <div className="space-y-1.5">
                      <p className="text-xs font-medium opacity-70">
                        Suggested times:
                      </p>
                      <div className="flex flex-wrap gap-2">
                        {userEventConflict.suggested_slots
                          .slice(0, 3)
                          .map((slot, idx) => {
                            const originalDay = format(
                              parseISO(
                                userEventConflict.pendingMutation.payload
                                  .start_at as string,
                              ),
                              "yyyy-MM-dd",
                            );
                            const slotDay = format(
                              parseISO(slot.start_at),
                              "yyyy-MM-dd",
                            );
                            const isDifferentDay = slotDay !== originalDay;
                            return (
                              <button
                                key={idx}
                                type="button"
                                data-testid="conflict-slot-pill"
                                onClick={() => submitConflictSlot(slot)}
                                disabled={userEventMutation.isPending}
                                className="rounded-full border border-[var(--amber,#f59e0b)] px-3 py-1 text-xs font-medium hover:bg-[color-mix(in_srgb,var(--amber,#f59e0b)_15%,transparent)] transition-colors disabled:opacity-40"
                              >
                                {isDifferentDay
                                  ? `${format(parseISO(slot.start_at), "MMM d, h:mm a")} – ${format(parseISO(slot.end_at), "h:mm a")}`
                                  : `${format(parseISO(slot.start_at), "h:mm a")} – ${format(parseISO(slot.end_at), "h:mm a")}`}
                              </button>
                            );
                          })}
                      </div>
                    </div>
                  ) : null}

                  {/* Book anyway escape hatch */}
                  <button
                    type="button"
                    data-testid="conflict-book-anyway"
                    onClick={submitConflictOverride}
                    disabled={userEventMutation.isPending}
                    className="text-xs opacity-50 hover:opacity-80 underline underline-offset-2 transition-opacity disabled:pointer-events-none"
                  >
                    Book anyway (overlap)
                  </button>
                </div>
              ) : null}

              <DialogFooter>
                <PillButton
                  onClick={() => setUserEventDialogOpen(false)}
                  disabled={userEventMutation.isPending}
                >
                  Cancel
                </PillButton>
                <CommitButton
                  type="submit"
                  disabled={userEventMutation.isPending}
                >
                  {userEventMutation.isPending
                    ? "Saving..."
                    : userEventDialogMode === "create"
                      ? "Create event"
                      : "Update event"}
                </CommitButton>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={butlerEventDialogOpen}
        onOpenChange={closeButlerEventDialog}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {butlerEventDialogMode === "create"
                ? "Create butler event"
                : "Edit butler event"}
            </DialogTitle>
            <DialogDescription>
              Create or update schedule/reminder events in butler lanes,
              including recurring-until boundaries.
            </DialogDescription>
          </DialogHeader>

          {butlerEventDraft ? (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label
                    htmlFor="calendar-butler-name"
                    className="text-sm font-medium"
                  >
                    Butler lane
                  </label>
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
                    className={FIELD_SELECT_CLASS}
                  >
                    {availableButlers.length === 0 ? (
                      <option value="">No butlers available</option>
                    ) : null}
                    {availableButlers.map((butlerName) => (
                      <option key={butlerName} value={butlerName}>
                        {formatLaneTitle(butlerName)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <label
                    htmlFor="calendar-event-kind"
                    className="text-sm font-medium"
                  >
                    Event type
                  </label>
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
                    className={FIELD_SELECT_CLASS}
                  >
                    <option value="butler_reminder">Reminder</option>
                    <option value="scheduled_task">Scheduled task</option>
                  </select>
                </div>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="calendar-event-title"
                  className="text-sm font-medium"
                >
                  Title
                </label>
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
                  <label
                    htmlFor="calendar-start-at"
                    className="text-sm font-medium"
                  >
                    Start
                  </label>
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
                  <label
                    htmlFor="calendar-timezone"
                    className="text-sm font-medium"
                  >
                    Timezone
                  </label>
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
                    <label
                      htmlFor="calendar-end-at"
                      className="text-sm font-medium"
                    >
                      End
                    </label>
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
                    <label
                      htmlFor="calendar-cron"
                      className="text-sm font-medium"
                    >
                      Cron (optional)
                    </label>
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
                  <label
                    htmlFor="calendar-frequency"
                    className="text-sm font-medium"
                  >
                    Recurrence
                  </label>
                  <select
                    id="calendar-frequency"
                    value={butlerEventDraft.recurrenceFrequency}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              recurrenceFrequency: event.target
                                .value as RecurrenceFrequency,
                            }
                          : current,
                      )
                    }
                    className={FIELD_SELECT_CLASS}
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
                  <label
                    htmlFor="calendar-until-at"
                    className="text-sm font-medium"
                  >
                    Until
                  </label>
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

              {recurrencePreviewRequest ? (
                <div
                  data-testid="recurrence-preview"
                  className="space-y-1.5 rounded-[3px] border border-[var(--border)] p-3"
                >
                  <Eyebrow>
                    Preview ({Intl.DateTimeFormat().resolvedOptions().timeZone})
                  </Eyebrow>
                  {recurrencePreview.isError ? (
                    <p className="text-sm text-[var(--mfg)]">
                      Couldn’t preview this recurrence — check the rule or cron
                      expression.
                    </p>
                  ) : recurrencePreviewData ? (
                    recurrencePreviewData.occurrences.length === 0 ? (
                      <p className="text-sm text-[var(--mfg)]">
                        No occurrences in the next 90 days.
                      </p>
                    ) : (
                      <>
                        <ul className="space-y-0.5 text-sm tabular-nums text-[var(--fg)]">
                          {recurrencePreviewData.occurrences.map((iso) => (
                            <li key={iso}>
                              {format(parseISO(iso), "EEE, MMM d · h:mm a")}
                            </li>
                          ))}
                        </ul>
                        {recurrencePreviewData.more_count > 0 ? (
                          <p
                            data-testid="recurrence-preview-more"
                            className="font-serif text-[13px] italic text-[var(--mfg)]"
                          >
                            +{recurrencePreviewData.more_count} more in 90 days
                          </p>
                        ) : null}
                        {recurrencePreviewData.notes.length > 0 ? (
                          <div className="space-y-0.5 pt-1">
                            {recurrencePreviewData.notes.map((note) => (
                              <p
                                key={note}
                                data-testid="recurrence-preview-note"
                                className="text-xs italic text-[var(--mfg)]"
                              >
                                {note}
                              </p>
                            ))}
                          </div>
                        ) : null}
                      </>
                    )
                  ) : (
                    <p className="text-sm text-[var(--mfg)]">Calculating…</p>
                  )}
                </div>
              ) : null}
            </div>
          ) : null}

          <DialogFooter>
            <PillButton
              onClick={() => closeButlerEventDialog(false)}
              disabled={butlerMutation.isPending}
            >
              Cancel
            </PillButton>
            <CommitButton
              onClick={handleSaveButlerEvent}
              disabled={butlerMutation.isPending || !butlerEventDraft}
            >
              {butlerMutation.isPending
                ? butlerEventDialogMode === "create"
                  ? "Creating..."
                  : "Saving..."
                : butlerEventDialogMode === "create"
                  ? "Create event"
                  : "Save changes"}
            </CommitButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!deleteCandidate}
        onOpenChange={(open) => (!open ? setDeleteCandidate(null) : null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Event</DialogTitle>
            <DialogDescription>
              {deleteCandidate
                ? `Delete "${deleteCandidate.title}" from the provider calendar?`
                : "Delete this event?"}
            </DialogDescription>
          </DialogHeader>
          {deleteCandidate && isRecurringUserEntry(deleteCandidate) ? (
            <RecurrenceScopeFieldset
              fieldsetTestId="delete-recurrence-scope"
              optionPrefix="delete-scope"
              name="delete-recurrence-scope"
              scope={deleteScope}
              onChange={setDeleteScope}
              impacts={{
                this: occurrenceImpactText(
                  entries,
                  deleteCandidate.provider_event_id,
                ),
                following: "Removes this occurrence and every later one.",
                series: "Deletes the entire recurring series.",
              }}
            />
          ) : null}
          <DialogFooter>
            <PillButton
              onClick={() => setDeleteCandidate(null)}
              disabled={userEventMutation.isPending}
            >
              Cancel
            </PillButton>
            <PillButton
              onClick={confirmDelete}
              disabled={userEventMutation.isPending}
              className="border-[var(--red)] text-[var(--red)] hover:opacity-80"
            >
              {userEventMutation.isPending ? "Deleting..." : "Delete"}
            </PillButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!recurringEdit}
        onOpenChange={(open) => (!open ? setRecurringEdit(null) : null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit recurring event</DialogTitle>
            <DialogDescription>
              {recurringEdit
                ? `Apply your change to "${recurringEdit.entry.title}".`
                : "Apply your change to this recurring event."}
            </DialogDescription>
          </DialogHeader>
          {recurringEdit ? (
            <RecurrenceScopeFieldset
              fieldsetTestId="edit-recurrence-scope"
              optionPrefix="edit-scope"
              name="edit-recurrence-scope"
              scope={editScope}
              onChange={setEditScope}
              impacts={{
                this: occurrenceImpactText(
                  entries,
                  recurringEdit.entry.provider_event_id,
                ),
                following: "Updates this occurrence and every later one.",
                series: "Updates the entire recurring series.",
              }}
            />
          ) : null}
          <DialogFooter>
            <PillButton
              onClick={() => setRecurringEdit(null)}
              disabled={userEventMutation.isPending}
            >
              Cancel
            </PillButton>
            <CommitButton
              onClick={confirmRecurringEdit}
              disabled={userEventMutation.isPending}
            >
              {userEventMutation.isPending ? "Saving..." : "Save changes"}
            </CommitButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
