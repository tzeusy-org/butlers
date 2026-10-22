import { useDraft } from "@/hooks/use-draft";
import { DraftNotice } from "@/components/ui/draft-notice";
import type { DraftSession, DraftSubmission } from "@/lib/draft-session";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  addDays,
  differenceInMinutes,
  format,
  isSameDay,
  isSameMonth,
  isToday,
  parseISO,
  startOfDay,
  startOfWeek,
} from "date-fns";
import { toast } from "sonner";
import { useSearchParams } from "react-router";

import { Tip } from "@/components/ui/tip";

import type {
  ApiResponse,
  CalendarAuditEntry,
  CalendarConflictEntry,
  CalendarFindTimeConstraints,
  CalendarFindTimePartOfDay,
  CalendarSuggestedSlot,
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
import { ApiError, getCalendarWorkspaceEntry } from "@/api/index.ts";
import {
  useAcceptCalendarProposal,
  useCalendarConflicts,
  useCalendarInvitations,
  useCalendarDayBriefing,
  useCalendarDuplicates,
  useCalendarOverlays,
  useCalendarProposals,
  useCalendarWorkspace,
  useDismissCalendarProposal,
  usePatchCalendarDedupRules,
  useSetCalendarKeepSeparate,
  useCalendarWorkspaceAudit,
  useCalendarWorkspaceMeta,
  useFindCalendarWorkspaceTime,
  useMutateCalendarWorkspaceUserEvent,
  useUndoCalendarWorkspaceMutation,
} from "@/hooks/use-calendar-workspace";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { CalendarAgendaView } from "@/components/calendar/CalendarAgendaView";
import { CalendarResponsePanel } from "@/components/calendar/CalendarResponsePanel";
import { CalendarVerdictOpener } from "@/components/calendar/CalendarVerdictOpener";
import { ConflictRadarBanner } from "@/components/calendar/ConflictRadarBanner";
import { CalendarPortabilityDialog } from "@/components/calendar/CalendarPortabilityDialog";
import { DayBriefingCard } from "@/components/calendar/DayBriefingCard";
import { DebriefCard } from "@/components/calendar/DebriefCard";
import {
  LinkedPeopleAvatars,
} from "@/components/calendar/LinkedPeopleAvatars";
import {
  ContactPeoplePicker,
  type SelectedPerson,
} from "@/components/calendar/ContactPeoplePicker";
import { CalendarProposalsPanel } from "@/components/calendar/CalendarProposalsPanel";
import { CalendarDuplicatesPanel } from "@/components/calendar/CalendarDuplicatesPanel";
import { QuickAddBar } from "@/pages/calendar/QuickAddBar";
import { Textarea } from "@/components/ui/textarea";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { StateDot } from "@/components/ui/StateDot";
import { Voice } from "@/components/ui/Voice";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { cn } from "@/lib/utils";
import {
  AVATAR_MIN_BLOCK_HEIGHT_PX,
  dateTimeLocalToIso,
  formatEventTime,
  HOUR_HEIGHT_PX,
  isoAtMinuteInTz,
  MINUTES_PER_DAY,
  minuteOfDayInTz,
  normalizeDragWindow,
  offsetToMinutes,
  resizeWindowEnd,
  shiftWindow,
  SNAP_MINUTES,
  snapMinutes,
  tzCalendarWindow,
  tzDateTimeLocalInput,
  tzDayKey,
} from "@/lib/calendar-grid";
import {
  overlaysByDay,
} from "@/lib/calendar-overlays";
import {
  CommitButton,
  KindTag,
  OverlayPill,
  PillButton,
  SnoozeMenu,
} from "@/pages/calendar/atoms";
import { CalendarActivityPanel } from "@/pages/calendar/CalendarActivityPanel";
import { CalendarEntryDetailPanel } from "@/pages/calendar/CalendarEntryDetailPanel";
import { CalendarSearchPalette } from "@/pages/calendar/CalendarSearchPalette";
import { ButlerEventDialog } from "@/pages/calendar/ButlerEventDialog";
import { SourcesDialog } from "@/pages/calendar/SourcesDialog";
import { useButlerEventEditor } from "@/pages/calendar/use-butler-event-editor";
import { useButlerLaneActions } from "@/pages/calendar/use-butler-lane-actions";
import { useCalendarSync } from "@/pages/calendar/use-calendar-sync";
import { useSourceControls } from "@/pages/calendar/use-source-controls";
import { RecurrenceScopeFieldset } from "@/pages/calendar/RecurrenceScopeFieldset";
import {
  computeFreshnessPlaque,
} from "@/pages/calendar/freshness";
import {
  calendarMutationErrorMessage,
  buildRequestId,
  isCalendarMutationOk,
  maybeText,
} from "@/pages/calendar/mutation-result";
import {
  FIELD_SELECT_CLASS,
  RANGE_OPTIONS,
  RECURRING_INSTANCE_CAP,
  SELECT_CLASS,
  SOURCE_TYPE_FACET_OPTIONS,
  STATUS_FACET_OPTIONS,
  VIEW_OPTIONS,
  capLaneEntriesByDay,
  formatLaneTitle,
  headlineLabel,
  isCancelledEntry,
  isOverflowSentinel,
  isPausedEntry,
  isRecurringUserEntry,
  occurrenceImpactText,
  parseAnchor,
  parseRange,
  parseView,
  resolveButlerEventTarget,
  resolveOwnerFromEntry,
  serializeAnchor,
  shiftAnchor,
  sourceName,
  titleize,
  truncateCalendarId,
  type ButlerLaneRows,
  type CalendarRange,
  type RecurrenceScope,
} from "@/pages/calendar/workspace-model";

type UserEventDialogMode = "create" | "edit";

interface UserEventFormState {
  sourceKey: string;
  title: string;
  startAtLocal: string;
  endAtLocal: string;
  timezone: string;
  description: string;
  location: string;
  /** People linked to the event; their entity ids thread into `entity_ids[]`. */
  people: SelectedPerson[];
}





// Default window for a toolbar "create" with no explicit time. `anchor` is the
// displayed calendar day (browser-local midnight from the query param); the
// emitted wall-clock values sit on that day and are interpreted in the form's
// timezone at submit. Slot/drag opens supply an explicit instant instead and
// bypass this (rendered directly in the workspace zone — see openUserCreateDialog).
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


/** Hour labels 0–23 for the y-axis. */
const HOURS = Array.from({ length: 24 }, (_, i) => i);
/** Default scroll-to position (in hours) when the time grid first mounts. */
const DEFAULT_SCROLL_HOUR = 7.5;
/** Pixel movement past which a pointer gesture counts as a drag (not a click). */
const DRAG_THRESHOLD_PX = 4;

/** Format a minute-of-day value as an `HH:mm` label. */
function formatMinuteLabel(minutes: number): string {
  const clamped = Math.min(MINUTES_PER_DAY, Math.max(0, minutes));
  if (clamped === MINUTES_PER_DAY) return "24:00";
  return format(
    new Date(2000, 0, 1, Math.floor(clamped / 60), clamped % 60),
    "HH:mm",
  );
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



interface CalendarFindTimePanelProps {
  butlerName: string | null;
  onSelectSlot: (slot: CalendarSuggestedSlot) => void;
  /**
   * Publishes the ranked slots of the latest search to the page so it can draw
   * them as ghost overlays on the grid. Called with `[]` when a search yields no
   * usable slots (empty result or degraded free/busy).
   */
  onSlotsChange: (slots: CalendarSuggestedSlot[]) => void;
  /** Workspace timezone used to render slot times (not the browser's zone). */
  timezone: string;
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
  onSlotsChange,
  timezone,
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
      onSlotsChange(
        response.data.available === false ? [] : response.data.slots,
      );
    } catch (error) {
      onSlotsChange([]);
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
          <p className="text-sm text-fg">
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
          <p className="text-sm text-fg">
            Free/busy is unavailable right now, so open time couldn&rsquo;t be
            checked.{" "}
            <span className="text-[var(--mfg)]">
              {result.reason ??
                "The calendar source is unreachable. Try again shortly."}
            </span>
          </p>
        </div>
      ) : result.slots.length === 0 ? (
        <Voice variant="italic" className="text-[var(--mfg)]">
          No open slots match those constraints in the selected window.
        </Voice>
      ) : (
        <ul
          className="flex max-h-48 flex-col gap-1.5 overflow-y-auto"
          data-testid="find-time-slots"
        >
          {result.slots.map((slot) => {
            return (
              <li key={`${slot.start_at}-${slot.end_at}`}>
                <button
                  type="button"
                  data-testid="find-time-slot"
                  className={cn(
                    "flex w-full items-center justify-between rounded-[3px] border border-[var(--border-strong)]",
                    "px-3 py-2 text-left transition-colors hover:bg-foreground/[0.06]",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                  )}
                  onClick={() => onSelectSlot(slot)}
                >
                  <span className="text-sm text-fg">
                    {formatEventTime(slot.start_at, timezone, "EEE, MMM d")}
                  </span>
                  <Mono muted className="tabular-nums">
                    {formatEventTime(slot.start_at, timezone, "HH:mm")}–
                    {formatEventTime(slot.end_at, timezone, "HH:mm")}
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

  const metaQuery = useCalendarWorkspaceMeta();
  const connectedSources = useMemo(
    () => metaQuery.data?.data.connected_sources ?? [],
    [metaQuery.data?.data.connected_sources],
  );
  const freshnessPlaque = useMemo(
    () =>
      computeFreshnessPlaque(
        connectedSources,
        metaQuery.isError ? "error" : metaQuery.isPending ? "pending" : "success",
        metaQuery.data?.data.sources_available !== false,
      ),
    [
      connectedSources,
      metaQuery.isError,
      metaQuery.isPending,
      metaQuery.data?.data.sources_available,
    ],
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

  // Visible window: derive columns/labels and the backend query bounds in the
  // *workspace* timezone so the day columns line up with how events are bucketed
  // (tzDayKey). `start`/`end` are workspace-tz calendar dates (browser-local
  // midnights of those dates); `queryStart`/`queryEnd` are the workspace-tz
  // midnight instants the backend fetch spans. When the browser zone equals the
  // workspace zone this is identical to the old browser-local behaviour.
  const { start, end, queryStart, queryEnd } = useMemo(
    () => tzCalendarWindow(range, anchor, defaultTimezone),
    [range, anchor, defaultTimezone],
  );

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
  const [agendaOpen, setAgendaOpen] = useState(false);
  const [portabilityOpen, setPortabilityOpen] = useState(false);
  const sourceControls = useSourceControls(connectedSources);
  const { hiddenSources } = sourceControls;
  const sync = useCalendarSync();
  const { syncMutation, handleSyncAll, syncButtonLabel } = sync;

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
    // The read filter is driven by the view-only hidden set, NOT the persisted
    // sync toggle: hiding a calendar removes it from the grid, while disabling
    // its sync does not (its already-synced events remain visible).
    const hasHidden = hiddenSources.size > 0;

    if (view === "user" && (hasCalendarFilter || hasHidden)) {
      const base = hasCalendarFilter
        ? sourceFilters
        : userSources.map((s) => s.source_key);
      return base.filter((key) => !hiddenSources.has(key));
    }
    return undefined;
  }, [
    hiddenSources,
    selectedCalendarId,
    selectedSourceKey,
    sourceFilters,
    userSources,
    view,
  ]);

  const workspaceQuery = useCalendarWorkspace({
    view,
    start: queryStart,
    end: queryEnd,
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

  // Conflict & overcommitment radar (bu-q8o90x): scan the visible window for
  // overlaps / dense days only on the time-grid views. Fail-open server-side, so
  // a degraded scan (`issues_available: false`) leaves the banner silent.
  const radarEnabled = range === "week" || range === "day";
  const conflictsQuery = useCalendarConflicts(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: radarEnabled },
  );
  const invitationsQuery = useCalendarInvitations({ start: queryStart, end: queryEnd, timezone });
  const invitationPages = invitationsQuery.data?.pages;
  const invitations = invitationPages
    ? [...new Map(invitationPages.flatMap((page) => page.data.entries)
      .map((entry) => [entry.entry_id, entry] as const)).values()]
    : undefined;
  const invitationsAvailable = invitationPages !== undefined && invitationPages.length > 0
    && invitationPages.every((page) => page.data.issues_available);
  const invitationConflictsAvailable = invitationPages !== undefined && invitationPages.length > 0
    && invitationPages.every((page) => page.data.conflicts_available);
  const invitationSourcesDegraded = invitationPages
    ? [...new Set(invitationPages.flatMap((page) => page.data.sources_degraded))]
    : undefined;
  const conflictScan = conflictsQuery.data?.data;
  const conflictIssues = useMemo(
    () => (radarEnabled && !conflictsQuery.isError ? (conflictScan?.issues ?? []) : []),
    [conflictsQuery.isError, radarEnabled, conflictScan?.issues],
  );
  const conflictsAvailable =
    radarEnabled && !conflictsQuery.isError && (conflictScan?.issues_available ?? false);
  // entry_ids appearing in any overlap issue get the amber grid edge.
  const overlapEntryIds = useMemo(() => {
    const ids = new Set<string>();
    if (!conflictsAvailable) return ids;
    for (const issue of conflictIssues) {
      if (issue.kind !== "overlap") continue;
      for (const event of issue.events) ids.add(event.entry_id);
    }
    return ids;
  }, [conflictsAvailable, conflictIssues]);

  const overlaysQuery = useCalendarOverlays(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: overlaysEnabled },
  );
  const overlayEntries = useMemo(
    () =>
      overlaysEnabled && !overlaysQuery.isError
        ? (overlaysQuery.data?.data.entries ?? [])
        : [],
    [overlaysEnabled, overlaysQuery.isError, overlaysQuery.data?.data.entries],
  );
  const hasDomainContext =
    !overlaysQuery.isError && (overlaysQuery.data?.data.has_domain_context ?? false);
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
  const calendarSurfaceFetching =
    workspaceQuery.isFetching ||
    (overlaysEnabled &&
      !overlaysQuery.isLoading &&
      !overlaysQuery.isError &&
      overlaysQuery.isFetching) ||
    (radarEnabled &&
      !conflictsQuery.isLoading &&
      !conflictsQuery.isError &&
      conflictsQuery.isFetching);

  const userEventMutation = useMutateCalendarWorkspaceUserEvent();
  const queryClient = useQueryClient();

  // Activity panel state
  const [activityPanelOpen, setActivityPanelOpen] = useState(false);
  // Find-time panel state (mutually exclusive with the activity panel).
  const [findTimePanelOpen, setFindTimePanelOpen] = useState(false);
  // Ranked free slots from the latest "Find time" search, lifted to page scope
  // so they can be drawn as ghost overlays on the week/day grid (synced with the
  // panel's list). Cleared whenever the find-time panel closes.
  const [findTimeSlots, setFindTimeSlots] = useState<CalendarSuggestedSlot[]>(
    [],
  );
  const [proposalsPanelOpen, setProposalsPanelOpen] = useState(false);

  // Proposals lane: butler-extracted candidate events awaiting accept/dismiss.
  // Fetched only while the panel is open; accept/dismiss mutations reconcile the
  // optimistic lane state inside the panel.
  const proposalsQuery = useCalendarProposals(
    { start: queryStart, end: queryEnd, timezone },
    { enabled: proposalsPanelOpen },
  );
  const proposalEntries = useMemo(
    () => (proposalsPanelOpen ? (proposalsQuery.data?.data.entries ?? []) : []),
    [proposalsPanelOpen, proposalsQuery.data?.data.entries],
  );
  const acceptProposalMutation = useAcceptCalendarProposal();
  const dismissProposalMutation = useDismissCalendarProposal();

  // Duplicate-review panel: the cross-source duplicate clusters the read collapses,
  // a per-cluster keep-separate toggle, and the match-strategy/threshold control.
  // Fetched only while the panel is open (duplicates supports user/butler views).
  const [duplicatesPanelOpen, setDuplicatesPanelOpen] = useState(false);
  const duplicatesQuery = useCalendarDuplicates(
    {
      view: view === "butler" ? "butler" : "user",
      start: queryStart,
      end: queryEnd,
      timezone,
      sources: sourcesForQuery,
    },
    { enabled: duplicatesPanelOpen },
  );
  const dedupRulesMutation = usePatchCalendarDedupRules();
  const keepSeparateMutation = useSetCalendarKeepSeparate();

  // Closing the find-time panel must drop its grid overlays, so they never
  // linger over the calendar after the search UI is dismissed.
  useEffect(() => {
    if (!findTimePanelOpen) setFindTimeSlots([]);
  }, [findTimePanelOpen]);

  const [auditOffset, setAuditOffset] = useState(0);
  const AUDIT_PAGE_SIZE = 50;
  const auditQuery = useCalendarWorkspaceAudit(
    { limit: AUDIT_PAGE_SIZE, offset: auditOffset },
    { enabled: activityPanelOpen },
  );

  // Durable undo of an applied audit row. The endpoint reverses the logged
  // mutation server-side with a freshly generated request_id; we track the
  // in-flight row and the set already reversed this session so the affordance
  // reflects state without needing a per-row "undone" flag on the audit read.
  const undoMutation = useUndoCalendarWorkspaceMutation();
  const [undoingActionId, setUndoingActionId] = useState<string | null>(null);
  const [undoneActionIds, setUndoneActionIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const handleUndoAuditEntry = useCallback(
    (entry: CalendarAuditEntry) => {
      if (undoingActionId) return;
      setUndoingActionId(entry.id);
      undoMutation.mutate(entry.id, {
        onSuccess: (response) => {
          setUndoneActionIds((prev) => new Set(prev).add(entry.id));
          toast.success(
            `Reversed ${entry.action_type} · undo ${response.data.request_id}`,
          );
        },
        onError: (error) => {
          // Map the fail-fast HTTP contract to honest, distinct messages —
          // never swallow the failure into a generic "try again".
          let message = "Couldn't undo this calendar change.";
          if (error instanceof ApiError) {
            if (error.status === 409) {
              // Already undone, or the row is no longer in an applied state.
              setUndoneActionIds((prev) => new Set(prev).add(entry.id));
              message =
                "This change was already undone (or is no longer reversible).";
            } else if (error.status === 422) {
              message =
                "Can't undo: the original state wasn't captured, so it can't be reconstructed.";
            } else if (error.status === 404) {
              message =
                "That action is no longer in the audit log, so it can't be undone.";
            } else if (error.message) {
              message = error.message;
            }
          } else if (error instanceof Error && error.message) {
            message = error.message;
          }
          toast.error(message);
        },
        onSettled: () => {
          setUndoingActionId((current) =>
            current === entry.id ? null : current,
          );
        },
      });
    },
    [undoMutation, undoingActionId],
  );

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
    onCancel?: () => void;
    onFailure?: () => void;
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
      draft?: { session: DraftSession; submission: DraftSubmission };
    };
  } | null>(null);
  const initialUserForm = useRef<UserEventFormState | null>(null);
  const userDraft = useDraft("calendar.user-event", [userEventForm?.sourceKey || "unselected", userEventDialogMode, activeUserEntry?.provider_event_id ?? "new"], {
    sourceKey: userEventForm?.sourceKey ?? "", title: userEventForm?.title ?? "", startAtLocal: userEventForm?.startAtLocal ?? "", endAtLocal: userEventForm?.endAtLocal ?? "",
    timezone: userEventForm?.timezone ?? "", description: userEventForm?.description ?? "", location: userEventForm?.location ?? "", entityIds: userEventForm?.people.map((person) => person.entity_id) ?? [],
  }, (v) => setUserEventForm((current) => current ? { ...current, sourceKey: v.sourceKey, title: v.title, startAtLocal: v.startAtLocal, endAtLocal: v.endAtLocal, timezone: v.timezone, description: v.description, location: v.location,
    // Labels are current view decoration, never persisted identity evidence.
    people: v.entityIds.map((id) => current.people.find((person) => person.entity_id === id) ?? { entity_id: id, canonical_name: id }),
  } : current), userEventDialogOpen && !!userEventForm?.sourceKey, {
    sourceKey: initialUserForm.current?.sourceKey ?? "", title: initialUserForm.current?.title ?? "", startAtLocal: initialUserForm.current?.startAtLocal ?? "", endAtLocal: initialUserForm.current?.endAtLocal ?? "",
    timezone: initialUserForm.current?.timezone ?? "", description: initialUserForm.current?.description ?? "", location: initialUserForm.current?.location ?? "", entityIds: initialUserForm.current?.people.map((person) => person.entity_id) ?? [],
  });
  const timeGridRef = useRef<HTMLDivElement>(null);
  // Detail panel — replaces modal-only editing with a right-docked panel
  const [selectedEntry, setSelectedEntry] =
    useState<UnifiedCalendarEntry | null>(null);
  const [invitationDetailLoading, setInvitationDetailLoading] = useState(false);

  async function openInvitation(entryId: string) {
    setInvitationDetailLoading(true);
    try {
      const response = await queryClient.fetchQuery({
        queryKey: ["calendar-workspace-entry", entryId],
        queryFn: () => getCalendarWorkspaceEntry(entryId),
      });
      if (response.data.entry_id !== entryId) throw new Error("Entry identity mismatch");
      setSelectedEntry(response.data);
    } catch {
      toast.error("Invitation details unavailable. Try again shortly.");
    } finally {
      setInvitationDetailLoading(false);
    }
  }

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

  // Linked-people resolution honesty (bu-qs64f): false only when the backend
  // reported the entity-resolution query failed for a schema. Absent/true means
  // it ran cleanly (empty linked_people is then genuinely "no one linked").
  const peopleSourceAvailable =
    workspaceQuery.data?.data.people_source_available !== false;

  // Events fan-out honesty: false only when a butler schema's workspace query
  // failed. A partial failure silently drops that schema's entries, so the grid
  // would otherwise read as a complete (merely emptier) calendar. Absent/true
  // means every source answered.
  const entriesSourceAvailable =
    workspaceQuery.data?.data.entries_source_available !== false;

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
      const key = tzDayKey(entry.start_at, defaultTimezone);
      const bucket = buckets.get(key) ?? [];
      bucket.push(entry);
      buckets.set(key, bucket);
    });
    return buckets;
  }, [entries, defaultTimezone]);

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

  const butlerEditor = useButlerEventEditor({ timezone, availableButlers });
  const { butlerMutation, openButlerCreateDialog } = butlerEditor;
  const {
    handleButlerToggle,
    handleButlerDelete,
    handleButlerSnooze,
    handleButlerDismiss,
  } = useButlerLaneActions(butlerMutation, defaultTimezone);

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
    let startAtLocal: string;
    let endAtLocal: string;
    if (forDate) {
      // Explicit instant from a suggested slot or a grid drag: render the exact
      // window in the WORKSPACE timezone so the prefilled inputs match the times
      // shown on the grid (and how the submit path parses them back).
      startAtLocal = tzDateTimeLocalInput(forDate, defaultTimezone);
      endAtLocal = tzDateTimeLocalInput(
        endDate ?? new Date(forDate.getTime() + 30 * 60 * 1_000),
        defaultTimezone,
      );
    } else {
      // Toolbar "create" with no explicit time: a default window on the anchor's
      // displayed calendar day (parsed in the form timezone at submit).
      ({ startAtLocal, endAtLocal } = defaultFormWindow(anchor));
    }

    setUserEventDialogMode("create");
    setActiveUserEntry(null);
    const initialForm: UserEventFormState = {
      sourceKey: preferredSource,
      title: "",
      startAtLocal,
      endAtLocal,
      timezone: defaultTimezone,
      description: "",
      location: "",
      people: [],
    };
    initialUserForm.current = initialForm;
    setUserEventForm(initialForm);
    setUserEventDialogOpen(true);
  }

  // Selecting a found free slot — from the panel list or a grid ghost overlay —
  // prefills the create dialog with that window and dismisses the find-time UI.
  function selectFindTimeSlot(slot: CalendarSuggestedSlot) {
    openUserCreateDialog(parseISO(slot.start_at), parseISO(slot.end_at));
    setFindTimePanelOpen(false);
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
      // The grid renders in the workspace timezone, so the dragged minute marks
      // are workspace-tz wall clock. Resolve them to instants in that zone (as
      // move/resize already do) so the prefilled create form — which also reads
      // the workspace zone — shows the times the user dragged over.
      const startDate = parseISO(
        isoAtMinuteInTz(day, startMin, defaultTimezone),
      );
      const endDate = parseISO(isoAtMinuteInTz(day, endMin, defaultTimezone));
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
        isoAtMinuteInTz(day, startMin, defaultTimezone),
        isoAtMinuteInTz(day, endMin, defaultTimezone),
      );
    } else {
      const day = weekDays[origin.dayIndex] ?? weekDays[0];
      const endMin = resizeWindowEnd(origin.startMin, pointerMin);
      commitDragTimeChange(
        origin.entry,
        isoAtMinuteInTz(day, origin.startMin, defaultTimezone),
        isoAtMinuteInTz(day, endMin, defaultTimezone),
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
    const originStartMin = minuteOfDayInTz(entry.start_at, defaultTimezone);
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
      startMin: minuteOfDayInTz(entry.start_at, defaultTimezone),
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
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
      draft?: { session: DraftSession; submission: DraftSubmission };
    },
  ) {
    const rawResult = responseData.result;
    const snapshot = pendingMutation.draft;
    // A retired dialog may still perform conditional storage cleanup, but cannot
    // announce results, render conflicts, or close a successor dialog.
    if (snapshot && !snapshot.session.current(snapshot.submission)) {
      if (isCalendarMutationOk(rawResult)) void snapshot.session.submitted(snapshot.submission, () => {});
      return;
    }
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
    const complete = () => { setUserEventDialogOpen(false); setUserEventForm(null); setActiveUserEntry(null); };
    if (snapshot) void snapshot.session.submitted(snapshot.submission, complete);
    else complete();
  }

  async function submitUserEventForm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!userEventForm) {
      return;
    }

    // The datetime-local inputs hold WORKSPACE-tz wall clock, so parse them in
    // the form's selected timezone (defaulting to the workspace zone) — not the
    // browser-local frame.
    const formTimezone = userEventForm.timezone.trim() || defaultTimezone;
    const startIso = dateTimeLocalToIso(userEventForm.startAtLocal, formTimezone);
    const endIso = dateTimeLocalToIso(userEventForm.endAtLocal, formTimezone);
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
      timezone: formTimezone,
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

    // Link selected people by their identity-layer entity ids. The backend
    // `calendar_create_event`/`calendar_update_event` tools accept `entity_ids`
    // and persist them to the calendar_event_entities join table.
    if (userEventForm.people.length > 0) {
      payload.entity_ids = userEventForm.people.map((p) => p.entity_id);
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

    const submission = userDraft.capture();
    const preparation = userDraft.prepare();
    if (preparation instanceof Promise ? !await preparation : !preparation) return;
    const requestId = buildRequestId(action);
    const pendingMutation = {
      butler_name: butlerName,
      action,
      payload,
      request_id: requestId,
      draft: userDraft.session && submission ? { session: userDraft.session, submission } : undefined,
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
      if (!userDraft.current(submission)) return;
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save calendar event.",
      );
    } finally { userDraft.finish(); }
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
    onCancel?: () => void,
    onFailure?: () => void,
  ) {
    setEditScope("this");
    setRecurringEdit({ entry, patch, label, onCancel, onFailure });
  }

  function cancelRecurringEdit() {
    recurringEdit?.onCancel?.();
    setRecurringEdit(null);
  }

  function failRecurringEdit() {
    if (!recurringEdit?.onFailure) return;
    recurringEdit.onFailure();
    setRecurringEdit(null);
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
      failRecurringEdit();
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
        failRecurringEdit();
        return;
      }
      if (!isCalendarMutationOk(result)) {
        toast.error(
          `Failed to update ${label}: ${calendarMutationErrorMessage(result, "Update failed.")}`,
        );
        failRecurringEdit();
        return;
      }
      toast.success(`Event ${label} updated.`);
      setRecurringEdit(null);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : `Failed to update ${label}.`,
      );
      failRecurringEdit();
    }
  }

  const canCreateUserEvents =
    view === "user" && submittableCalendars.length > 0;

  // ---------------------------------------------------------------------
  // Palette verbs + bindings (bu-t64p2 -- reachability sweep, bu-qvnce.11
  // slice 5). Calendar had the fleet's richest toolbar and its sole keydown
  // handler was the grid's own pointer/date-cell logic -- none of these
  // already-wired actions had a keyboard path or a command-menu entry.
  // ---------------------------------------------------------------------
  const canCreateEvent = view === "butler" || canCreateUserEvents;

  // openButlerCreateDialog/openUserCreateDialog/updateQuery are recreated
  // every render and close over dynamic state directly (anchor, searchParams,
  // availableButlers, submittableCalendars, ...). calendarCommands/
  // calendarShortcuts below only recompute when canCreateEvent/view change,
  // so capturing those functions straight in the memo deps would freeze
  // whichever closure was live at the last recompute -- e.g. pressing "c"
  // after navigating to a different day would reopen the create dialog
  // prefilled for the day shown when the memo last ran, not today's anchor
  // (gemini-code-assist, PR #2958). Route every invocation through a ref so
  // it always calls the latest closure regardless of memo identity.
  const calendarActionsRef = useRef({
    openButlerCreateDialog,
    openUserCreateDialog,
    updateQuery,
    anchor,
    range,
  });
  calendarActionsRef.current = {
    openButlerCreateDialog,
    openUserCreateDialog,
    updateQuery,
    anchor,
    range,
  };

  const calendarCommands = useMemo<PaletteCommand[]>(() => {
    const commands: PaletteCommand[] = [];
    if (canCreateEvent) {
      commands.push({
        id: "calendar-new-event",
        label: view === "butler" ? "Create butler event" : "Create event",
        keywords: ["new", "event", "create"],
        perform: () =>
          view === "butler"
            ? calendarActionsRef.current.openButlerCreateDialog()
            : calendarActionsRef.current.openUserCreateDialog(),
        binding: ["c"],
      });
    }
    commands.push(
      {
        id: "calendar-jump-today",
        label: "Jump to today",
        keywords: ["today", "anchor"],
        perform: () => calendarActionsRef.current.updateQuery({ anchor: new Date() }),
        binding: ["t"],
      },
      {
        id: "calendar-previous-range",
        label: "Previous range",
        keywords: ["calendar", "previous", "range"],
        perform: () => {
          const { anchor: currentAnchor, range: currentRange, updateQuery } =
            calendarActionsRef.current;
          updateQuery({ anchor: shiftAnchor(currentAnchor, currentRange, -1) });
        },
        binding: ["←"],
      },
      {
        id: "calendar-next-range",
        label: "Next range",
        keywords: ["calendar", "next", "range"],
        perform: () => {
          const { anchor: currentAnchor, range: currentRange, updateQuery } =
            calendarActionsRef.current;
          updateQuery({ anchor: shiftAnchor(currentAnchor, currentRange, 1) });
        },
        binding: ["→"],
      },
      {
        id: "calendar-search-events",
        label: "Search events",
        keywords: ["find", "search"],
        perform: () => setSearchPaletteOpen(true),
      },
    );
    return commands;
  }, [canCreateEvent, view]);
  useRegisterCommands(calendarCommands);

  const calendarShortcuts = useMemo<ShortcutBinding[]>(() => {
    const bindings: ShortcutBinding[] = [
      {
        key: "t",
        display: ["t"],
        description: "Jump to today",
        handler: () => calendarActionsRef.current.updateQuery({ anchor: new Date() }),
      },
      {
        key: "ArrowLeft",
        display: ["←"],
        description: "Previous range",
        handler: () => {
          const { anchor: currentAnchor, range: currentRange, updateQuery } =
            calendarActionsRef.current;
          updateQuery({ anchor: shiftAnchor(currentAnchor, currentRange, -1) });
        },
      },
      {
        key: "ArrowRight",
        display: ["→"],
        description: "Next range",
        handler: () => {
          const { anchor: currentAnchor, range: currentRange, updateQuery } =
            calendarActionsRef.current;
          updateQuery({ anchor: shiftAnchor(currentAnchor, currentRange, 1) });
        },
      },
    ];
    if (canCreateEvent) {
      bindings.push({
        key: "c",
        display: ["c"],
        description: view === "butler" ? "Create butler event" : "Create event",
        handler: () =>
          view === "butler"
            ? calendarActionsRef.current.openButlerCreateDialog()
            : calendarActionsRef.current.openUserCreateDialog(),
      });
    }
    return bindings;
  }, [canCreateEvent, view]);
  useRegisterShortcut(calendarShortcuts);

  return (
    <div className="flex h-full flex-col overflow-hidden" data-shell-scroll-owner="route">
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
            onClick={() => setAgendaOpen(true)}
            aria-label="Open printable agenda"
          >
            Agenda
          </PillButton>
          <PillButton
            onClick={() => setPortabilityOpen(true)}
            aria-label="Export or import calendar"
          >
            Export / Import
          </PillButton>
          <PillButton
            onClick={() => setSourcesDialogOpen(true)}
            aria-label="Configure sources"
          >
            Sources
            {hiddenSources.size > 0 ? (
              <span className="tabular-nums text-[var(--dim)]">
                · {hiddenSources.size} hidden
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

      <CalendarVerdictOpener
        entriesCount={entries.length}
        sourceCount={connectedSources.length}
        rangeLabel={range}
        workspaceLoading={workspaceQuery.isLoading}
        workspaceError={workspaceQuery.isError || !entriesSourceAvailable}
        sourceFreshnessLoading={metaQuery.isPending}
        sourceFreshnessError={metaQuery.isError || metaQuery.data?.data.sources_available === false}
        freshnessDetail={freshnessPlaque?.detail ?? null}
        conflictScanEnabled={radarEnabled}
        conflictLoading={conflictsQuery.isLoading}
        conflictError={conflictsQuery.isError}
        conflictsAvailable={conflictsAvailable}
        conflicts={conflictIssues}
        invitations={invitations}
        invitationsLoading={invitationsQuery.isPending || invitationsQuery.isPlaceholderData}
        invitationsRefreshing={invitationsQuery.isFetching}
        invitationsError={invitationsQuery.isError}
        invitationsAvailable={invitationsAvailable}
        invitationConflictsAvailable={invitationConflictsAvailable}
        invitationSourcesDegraded={invitationSourcesDegraded}
        invitationsHasMore={invitationsQuery.hasNextPage}
        invitationsLoadingMore={invitationsQuery.isFetchingNextPage}
        onMoreInvitations={() => { void invitationsQuery.fetchNextPage(); }}
        onOpenInvitation={(entryId) => { void openInvitation(entryId); }}
        invitationDetailLoading={invitationDetailLoading}
      />

      <CalendarResponsePanel entries={invitations ?? []}
        onObserved={(receipt) => {
          if (receipt.status === "applied" && receipt.projection_available) {
            void queryClient.invalidateQueries({ queryKey: ["calendar-invitations"] });
            void queryClient.invalidateQueries({ queryKey: ["calendar-workspace"] });
            void queryClient.invalidateQueries({ queryKey: ["calendar-workspace-audit"] });
          }
        }}
        unavailable={!invitationsAvailable || invitationsQuery.isPending || invitationsQuery.isPlaceholderData} />

      {freshnessPlaque ? (
        <SourceDegradedNote
          testId="calendar-freshness-plaque"
          label="Calendar sync"
          detail={freshnessPlaque.detail}
          onRetry={handleSyncAll}
          retryLabel={syncButtonLabel}
          className="mb-4"
        />
      ) : null}

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
            setDuplicatesPanelOpen(false);
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
            setDuplicatesPanelOpen(false);
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
            setDuplicatesPanelOpen(false);
          }}
        >
          Proposals
        </PillButton>

        <PillButton
          active={duplicatesPanelOpen}
          aria-pressed={duplicatesPanelOpen}
          onClick={() => {
            setDuplicatesPanelOpen((prev) => !prev);
            setActivityPanelOpen(false);
            setFindTimePanelOpen(false);
            setProposalsPanelOpen(false);
          }}
        >
          Duplicates
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
              "flex h-7 items-center gap-1.5 rounded-[3px] border px-2 font-mono text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              overlaysEnabled
                ? "border-fg bg-foreground/[0.06] text-fg"
                : "border-[var(--border-strong)] text-[var(--mfg)] hover:text-fg",
            )}
          >
            <StateDot
              state={
                overlaysEnabled && overlaysQuery.isError
                  ? "error"
                  : overlaysEnabled
                    ? "ok"
                    : "waiting"
              }
            />
            Overlays
            {overlaysEnabled && overlaysQuery.isError ? (
              <span className="text-[var(--red-text)]">· unavailable</span>
            ) : overlaysEnabled && overlaysQuery.isFetched && !hasDomainContext ? (
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
            isFetching={dayBriefingQuery.isFetching && !dayBriefingQuery.isLoading}
            isError={dayBriefingQuery.isError}
            error={
              dayBriefingQuery.error instanceof Error ? dayBriefingQuery.error : null
            }
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
            onUndo={handleUndoAuditEntry}
            undoingId={undoingActionId}
            undoneIds={undoneActionIds}
          />
        </div>
      ) : null}

      {/* Find-time controls + ranked list — a compact strip above the canvas so
          the same slots can be drawn as ghost overlays on the grid below. */}
      {findTimePanelOpen ? (
        <div className="flex shrink-0 flex-col pt-5">
          <CalendarFindTimePanel
            butlerName={findTimeButlerName}
            timezone={defaultTimezone}
            onSelectSlot={selectFindTimeSlot}
            onSlotsChange={setFindTimeSlots}
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
            timezone={defaultTimezone}
          />
        </div>
      ) : null}

      {/* Duplicate-review panel — overlays the canvas when open */}
      {duplicatesPanelOpen ? (
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <CalendarDuplicatesPanel
            data={duplicatesQuery.data?.data}
            isLoading={duplicatesQuery.isLoading}
            isFetching={duplicatesQuery.isFetching && !duplicatesQuery.isLoading}
            isError={duplicatesQuery.isError}
            error={
              duplicatesQuery.error instanceof Error
                ? duplicatesQuery.error
                : null
            }
            rulesMutation={dedupRulesMutation}
            keepSeparateMutation={keepSeparateMutation}
            timezone={defaultTimezone}
          />
        </div>
      ) : null}

      {/* Canvas + detail panel. The find-time panel does NOT hide the canvas —
          it stays visible so found slots can be drawn as ghost overlays on the
          week/day grid below the controls strip. */}
      <div
        className={
          activityPanelOpen || proposalsPanelOpen || duplicatesPanelOpen
            ? "hidden"
            : "flex min-h-0 flex-1"
        }
      >
        <div className="flex min-h-0 flex-1 flex-col pt-5">
          <DebriefCard />
          {/* Events fan-out degraded (bu-yjfk2): a butler schema's workspace
              query failed, so the grid may be missing that schema's events —
              name the degraded source rather than let a short grid read as an
              honest "nothing scheduled". */}
          {!entriesSourceAvailable ? (
            <div data-testid="entries-source-degraded" className="pb-2">
              <SourceDegradedNote
                label="Calendar events"
                detail="some sources unavailable: this view may be missing events"
              />
            </div>
          ) : null}
          {overlaysEnabled && overlaysQuery.isError ? (
            <div data-testid="calendar-overlays-error" className="pb-2">
              <SourceDegradedNote
                label="Cross-domain overlays"
                detail={
                  overlaysQuery.error instanceof Error
                    ? overlaysQuery.error.message
                    : "unavailable"
                }
              />
            </div>
          ) : null}
          {/* Linked-people resolution degraded (bu-qs64f): entries still render,
              but avatars may be incomplete — name the degraded source rather
              than let empty avatars read as "no one linked". */}
          {!peopleSourceAvailable ? (
            <div data-testid="linked-people-degraded" className="pb-2">
              <SourceDegradedNote
                label="Linked people"
                detail="unavailable: some events may not show their people"
              />
            </div>
          ) : null}
          {workspaceQuery.isLoading ? (
            <Voice variant="italic" className="text-[var(--mfg)]">
              Drawing the calendar…
            </Voice>
          ) : workspaceQuery.isError ? (
            <div role="alert" className="flex items-start gap-2 py-1">
              <StateDot state="error" className="mt-[7px]" />
              <p className="text-sm text-fg">
                The calendar workspace failed to load.{" "}
                <span className="text-[var(--mfg)]">
                  {workspaceQuery.error instanceof Error
                    ? workspaceQuery.error.message
                    : "Unknown error"}
                </span>
              </p>
            </div>
          ) : (
            // Never-blank floor (bu-nhcp5): placeholderData on useCalendarWorkspace
            // keeps the outgoing window's entries rendered the instant week/day
            // navigation fires a new query key; this dims them instead of the
            // grid replacing itself with "Drawing the calendar…" on every step.
            <FetchingDim isFetching={calendarSurfaceFetching}>
              {view === "butler" ? (
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
                        <span className="truncate text-[15px] font-medium text-fg">
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
                          defaultTimezone,
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
                                    : formatEventTime(
                                        item.start_at,
                                        defaultTimezone,
                                        "HH:mm",
                                      )}
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
                                      className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
                                    >
                                      Dismiss
                                    </PillButton>
                                  ) : null}
                                  <PillButton
                                    onClick={() => handleButlerDelete(item)}
                                    disabled={butlerMutation.isPending}
                                    className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
                                  >
                                    Delete
                                  </PillButton>
                                </div>
                              }
                            >
                              <div className="flex min-w-0 items-center gap-2">
                                <span className="truncate text-sm text-fg">
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
                          className="absolute inset-0 z-0 cursor-pointer transition-colors hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
                        />
                      ) : null}
                      <div className="pointer-events-none relative z-10">
                        <div className="mb-1 flex items-center justify-between">
                          {today ? (
                            <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-[4px] bg-fg px-1 font-mono text-[11px] tabular-nums text-bg">
                              {format(day, "d")}
                            </span>
                          ) : (
                            <span
                              className={cn(
                                "font-mono text-[11px] tabular-nums",
                                inMonth
                                  ? "text-fg"
                                  : "text-[var(--dim)]",
                              )}
                            >
                              {format(day, "d")}
                            </span>
                          )}
                        </div>
                        <div className="space-y-0.5">
                          {dayEntries.slice(0, 3).map((entry) => (
                            <Tip key={entry.entry_id} content={entry.title}>
                              <button
                                type="button"
                                data-calendar-entry-id={entry.entry_id}
                                onClick={() => openDetailPanel(entry)}
                                className="pointer-events-auto flex w-full items-center gap-1 truncate rounded-[2px] px-1 py-0.5 text-left text-[11px] text-fg transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                              >
                              {!entry.all_day ? (
                                <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                                  {formatEventTime(
                                    entry.start_at,
                                    defaultTimezone,
                                    "HH:mm",
                                  )}
                                </span>
                              ) : null}
                              <span className="truncate">{entry.title}</span>
                              </button>
                            </Tip>
                          ))}
                          {dayEntries.length > 3 ? (
                            <button
                              type="button"
                              aria-label={`${dayEntries.length - 3} more on ${format(day, "MMM d")}: open day view`}
                              onClick={() =>
                                updateQuery({ range: "day", anchor: day })
                              }
                              className="pointer-events-auto block px-1 font-mono text-[10px] tabular-nums text-[var(--mfg)] transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
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
              {/* Conflict & overcommitment radar — only when issues exist in range. */}
              <ConflictRadarBanner
                issues={conflictIssues}
                available={conflictsAvailable}
                isFetching={conflictsQuery.isFetching && !conflictsQuery.isLoading}
                isError={conflictsQuery.isError}
                error={
                  conflictsQuery.error instanceof Error ? conflictsQuery.error : null
                }
                className="mb-2"
                isProposalActionPending={
                  acceptProposalMutation.isPending || dismissProposalMutation.isPending
                }
                onAcceptProposal={(proposalId) => {
                  acceptProposalMutation.mutate(
                    { proposalId },
                    {
                      onSuccess: () =>
                        toast.success(
                          "Proposal accepted. Event added to the Butlers calendar.",
                        ),
                      onError: (error) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : "Failed to accept the proposed fix.",
                        ),
                      onSettled: () =>
                        queryClient.invalidateQueries({ queryKey: ["calendar-conflicts"] }),
                    },
                  );
                }}
                onDismissProposal={(proposalId) => {
                  dismissProposalMutation.mutate(
                    { proposalId },
                    {
                      onSuccess: () => toast.success("Proposal dismissed."),
                      onError: (error) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : "Failed to dismiss the proposed fix.",
                        ),
                      onSettled: () =>
                        queryClient.invalidateQueries({ queryKey: ["calendar-conflicts"] }),
                    },
                  );
                }}
              />
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
                    <Eyebrow className={cn(isToday(day) && "text-fg")}>
                      {format(day, "EEE")}
                    </Eyebrow>{" "}
                    <span
                      className={cn(
                        "font-mono text-[12px] tabular-nums",
                        isToday(day) ? "text-fg" : "text-[var(--mfg)]",
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
                            <Tip key={entry.entry_id} content={entry.title}>
                              <button
                                type="button"
                                data-calendar-entry-id={entry.entry_id}
                                onClick={(evt) => {
                                  evt.stopPropagation();
                                  openDetailPanel(entry);
                                }}
                                className="block w-full truncate rounded-[3px] border border-[var(--border)] px-1.5 py-0.5 text-left text-[11px] text-fg transition-colors hover:bg-foreground/[0.06]"
                              >
                                {entry.title}
                              </button>
                            </Tip>
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
                            className="absolute inset-0 z-0 cursor-pointer touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus"
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
                            className="pointer-events-none absolute inset-x-0.5 z-20 rounded-[3px] border border-dashed border-fg/60 bg-fg/10"
                            style={{
                              top: (gridDrag.startMin / 60) * HOUR_HEIGHT_PX,
                              height:
                                ((gridDrag.endMin - gridDrag.startMin) / 60) *
                                HOUR_HEIGHT_PX,
                              minHeight: 16,
                            }}
                          >
                            <span className="block px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-fg">
                              {formatMinuteLabel(gridDrag.startMin)}–
                              {formatMinuteLabel(gridDrag.endMin)}
                            </span>
                          </div>
                        ) : null}
                        {/* Find-time ghost overlays — ranked free slots from the
                            panel's latest search, drawn in place via the grid
                            geometry and synced with the list. Selecting one does
                            the same prefilled-create as the list. */}
                        {findTimeSlots.map((slot, slotIndex) => {
                          const slotStart = parseISO(slot.start_at);
                          const slotEnd = parseISO(slot.end_at);
                          // Bucket + place the slot in the workspace timezone so
                          // it lines up with the grid's events under that zone.
                          if (
                            tzDayKey(slot.start_at, defaultTimezone) !==
                            format(day, "yyyy-MM-dd")
                          )
                            return null;
                          const topMin = minuteOfDayInTz(
                            slot.start_at,
                            defaultTimezone,
                          );
                          const durationMin = Math.max(
                            differenceInMinutes(slotEnd, slotStart),
                            15,
                          );
                          const slotLabel = `${formatEventTime(slot.start_at, defaultTimezone, "HH:mm")}–${formatEventTime(slot.end_at, defaultTimezone, "HH:mm")}`;
                          return (
                            <button
                              key={`find-time-${slot.start_at}-${slot.end_at}`}
                              type="button"
                              data-testid="find-time-overlay"
                              title={`Open slot ${slotIndex + 1}: ${slotLabel}`}
                              onClick={(evt) => {
                                evt.stopPropagation();
                                selectFindTimeSlot(slot);
                              }}
                              className={cn(
                                "absolute inset-x-0.5 z-20 flex flex-col overflow-hidden rounded-[3px]",
                                "border border-dashed border-fg/50 bg-fg/[0.08]",
                                "px-1.5 py-0.5 text-left transition-colors hover:bg-fg/[0.16]",
                                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                              )}
                              style={{
                                top: (topMin / 60) * HOUR_HEIGHT_PX,
                                height: (durationMin / 60) * HOUR_HEIGHT_PX,
                                minHeight: 16,
                              }}
                            >
                              <span className="font-mono text-[10px] leading-none tabular-nums text-fg">
                                #{slotIndex + 1} · {slotLabel}
                              </span>
                            </button>
                          );
                        })}
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
                            className="absolute inset-x-0.5 z-20 flex items-center justify-center rounded-[3px] border border-dashed border-fg/40 bg-bg/70 text-[10px] font-medium text-[var(--mfg)] transition-colors hover:text-fg"
                            style={{
                              top:
                                (minuteOfDayInTz(
                                  ghost.prevStartIso,
                                  defaultTimezone,
                                ) /
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
                          const topMin = minuteOfDayInTz(s, defaultTimezone);
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
                              data-conflict-overlap={
                                overlapEntryIds.has(entry.entry_id)
                                  ? "true"
                                  : undefined
                              }
                              className={cn(
                                "absolute inset-x-0.5 z-10 overflow-hidden rounded-[3px] border border-[var(--border)] bg-bg px-1.5 py-0.5 text-left transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                                // Amber left edge marks an event caught in an overlap.
                                overlapEntryIds.has(entry.entry_id) &&
                                  "border-l-2 border-l-amber-500",
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
                              title={`${formatEventTime(s, defaultTimezone, "HH:mm")}–${formatEventTime(e, defaultTimezone, "HH:mm")} · ${entry.title}`}
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
                              <span className="block truncate text-[11px] font-medium leading-tight text-fg">
                                {entry.title}
                              </span>
                              {heightPx >= 32 ? (
                                <span className="mt-0.5 block truncate font-mono text-[10px] tabular-nums text-[var(--mfg)]">
                                  {formatEventTime(s, defaultTimezone, "HH:mm")}–
                                  {formatEventTime(e, defaultTimezone, "HH:mm")}
                                </span>
                              ) : null}
                              {/* Linked-people avatars (bu-01qmd) — reuse the agenda/detail
                                  cluster, gated by block height so short blocks don't overflow.
                                  pointer-events-none keeps it transparent to the block's drag
                                  handlers (no interactive elements inside the draggable block). */}
                              {heightPx >= AVATAR_MIN_BLOCK_HEIGHT_PX &&
                              entry.linked_people &&
                              entry.linked_people.length > 0 ? (
                                <LinkedPeopleAvatars
                                  people={entry.linked_people}
                                  className="pointer-events-none mt-0.5"
                                />
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
                // Group by the event's calendar day in the workspace timezone so
                // sections match the times rendered in each row.
                const todayKey = formatEventTime(
                  new Date(),
                  defaultTimezone,
                  "yyyy-MM-dd",
                );
                for (const entry of entries) {
                  const d = new Date(entry.start_at);
                  const dayKey = tzDayKey(d, defaultTimezone);
                  let gi = groupIndex.get(dayKey);
                  if (gi === undefined) {
                    gi = groups.length;
                    groupIndex.set(dayKey, gi);
                    groups.push({
                      day: dayKey,
                      date: d,
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
                          group.day === todayKey && "text-fg",
                        )}
                      >
                        {formatEventTime(
                          group.date,
                          defaultTimezone,
                          "EEE · MMM d",
                        )}
                      </Eyebrow>
                      {group.day === todayKey ? (
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
                                  : formatEventTime(
                                      entry.start_at,
                                      defaultTimezone,
                                      "HH:mm",
                                    )}
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
                                  className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
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
                                  "truncate text-sm text-fg",
                                  isCancelledEntry(entry) &&
                                    "text-[var(--mfg)] line-through",
                                )}
                              >
                                {entry.title}
                              </span>
                              {isCancelledEntry(entry) ? (
                                <KindTag
                                  data-testid="entry-cancelled-tag"
                                  className="text-[var(--red-text)]"
                                >
                                  cancelled
                                </KindTag>
                              ) : null}
                              {entry.linked_people &&
                              entry.linked_people.length > 0 ? (
                                <LinkedPeopleAvatars
                                  people={entry.linked_people}
                                />
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
            </FetchingDim>
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
              timezone={defaultTimezone}
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
          displayTimezone={defaultTimezone}
          onJump={handleSearchJump}
        />
      ) : null}

      {agendaOpen ? (
        <CalendarAgendaView
          entries={entries}
          rangeLabel={headlineLabel(range, start, end)}
          timezone={timezone}
          view={view === "butler" ? "butler" : "user"}
          onClose={() => setAgendaOpen(false)}
        />
      ) : null}

      <CalendarPortabilityDialog
        open={portabilityOpen}
        onOpenChange={setPortabilityOpen}
        exportParams={{
          view: view === "butler" ? "butler" : "user",
          start: queryStart,
          end: queryEnd,
          sources: sourcesForQuery,
          status:
            selectedStatus === "all"
              ? undefined
              : (selectedStatus as CalendarWorkspaceStatusFacet),
          source_type:
            selectedSourceType === "all"
              ? undefined
              : (selectedSourceType as UnifiedCalendarSourceType),
        }}
        rangeLabel={headlineLabel(range, start, end)}
        importTargets={submittableCalendars}
      />

      <SourcesDialog
        open={sourcesDialogOpen}
        onOpenChange={setSourcesDialogOpen}
        connectedSources={connectedSources}
        metaLoading={metaQuery.isLoading}
        primaryCalendarId={primaryCalendarId}
        controls={sourceControls}
        sync={sync}
      />

      <Dialog
        open={userEventDialogOpen}
        onOpenChange={(open) => {
          if (open) setUserEventDialogOpen(true);
          else userDraft.dismiss(() => { setUserEventDialogOpen(false); setUserEventForm(null); setActiveUserEntry(null); setUserEventConflict(null); });
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
              <DraftNotice session={userDraft.session} />
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

              {/* People — link known contacts to the event (entity_ids). */}
              <ContactPeoplePicker
                value={userEventForm.people}
                onChange={(people) =>
                  setUserEventForm((current) =>
                    current ? { ...current, people } : current,
                  )
                }
                disabled={userEventMutation.isPending}
              />

              {/* Conflict card — rendered when the backend returns status='conflict' */}
              {userEventConflict ? (
                <div
                  className="rounded-md border border-[var(--amber)] bg-[color-mix(in_srgb,var(--amber)_8%,transparent)] p-3 space-y-3"
                  data-testid="conflict-card"
                >
                  {/* Amber chip */}
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full bg-[var(--amber)] px-2.5 py-0.5 text-xs font-medium text-white">
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
                            {formatEventTime(c.start_at, defaultTimezone, "h:mm a")}–
                            {formatEventTime(c.end_at, defaultTimezone, "h:mm a")}
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
                            // "Different day than requested" is judged against
                            // the day the user entered in the create form. Both
                            // the requested start and the suggested slot are
                            // bucketed by their WORKSPACE-tz calendar day so the
                            // prefix agrees with the slot label rendered below
                            // (which is already in the workspace timezone).
                            const originalDay = tzDayKey(
                              userEventConflict.pendingMutation.payload
                                .start_at as string,
                              defaultTimezone,
                            );
                            const slotDay = tzDayKey(
                              slot.start_at,
                              defaultTimezone,
                            );
                            const isDifferentDay = slotDay !== originalDay;
                            return (
                              <button
                                key={idx}
                                type="button"
                                data-testid="conflict-slot-pill"
                                onClick={() => submitConflictSlot(slot)}
                                disabled={userEventMutation.isPending}
                                className="rounded-full border border-[var(--amber)] px-3 py-1 text-xs font-medium hover:bg-[color-mix(in_srgb,var(--amber)_15%,transparent)] transition-colors disabled:opacity-40"
                              >
                                {isDifferentDay
                                  ? `${formatEventTime(slot.start_at, defaultTimezone, "MMM d, h:mm a")} – ${formatEventTime(slot.end_at, defaultTimezone, "h:mm a")}`
                                  : `${formatEventTime(slot.start_at, defaultTimezone, "h:mm a")} – ${formatEventTime(slot.end_at, defaultTimezone, "h:mm a")}`}
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
                  onClick={() => userDraft.dismiss(() => setUserEventDialogOpen(false))}
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

      <ButlerEventDialog
        editor={butlerEditor}
        availableButlers={availableButlers}
        defaultTimezone={defaultTimezone}
      />

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
              className="border-[var(--red)] text-[var(--red-text)] hover:opacity-80"
            >
              {userEventMutation.isPending ? "Deleting..." : "Delete"}
            </PillButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!recurringEdit}
        onOpenChange={(open) => (!open ? cancelRecurringEdit() : null)}
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
              onClick={cancelRecurringEdit}
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
