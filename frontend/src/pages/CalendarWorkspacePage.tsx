import { useDraft } from "@/hooks/use-draft";
import { DraftNotice } from "@/components/ui/draft-notice";
import type { DraftSession, DraftSubmission } from "@/lib/draft-session";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  addDays,
  differenceInMinutes,
  format,
  parseISO,
  startOfDay,
  startOfWeek,
} from "date-fns";
import { toast } from "sonner";


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
  ContactPeoplePicker,
  type SelectedPerson,
} from "@/components/calendar/ContactPeoplePicker";
import { CalendarProposalsPanel } from "@/components/calendar/CalendarProposalsPanel";
import { CalendarDuplicatesPanel } from "@/components/calendar/CalendarDuplicatesPanel";
import { QuickAddBar } from "@/pages/calendar/QuickAddBar";
import { Textarea } from "@/components/ui/textarea";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Mono } from "@/components/ui/Mono";
import { StateDot } from "@/components/ui/StateDot";
import { Voice } from "@/components/ui/Voice";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { cn } from "@/lib/utils";
import {
  dateTimeLocalToIso,
  formatEventTime,
  HOUR_HEIGHT_PX,
  isoAtMinuteInTz,
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
  PillButton,
} from "@/pages/calendar/atoms";
import { CalendarActivityPanel } from "@/pages/calendar/CalendarActivityPanel";
import { CalendarEntryDetailPanel } from "@/pages/calendar/CalendarEntryDetailPanel";
import { CalendarSearchPalette } from "@/pages/calendar/CalendarSearchPalette";
import { ButlerEventDialog } from "@/pages/calendar/ButlerEventDialog";
import { AgendaList } from "@/pages/calendar/AgendaList";
import { ButlerLanes } from "@/pages/calendar/ButlerLanes";
import { MonthGrid } from "@/pages/calendar/MonthGrid";
import { TimeGrid } from "@/pages/calendar/TimeGrid";
import {
  DRAG_THRESHOLD_PX,
  isGridDraggable,
  type DragOrigin,
  type GridDragPreview,
  type MovedGhost,
} from "@/pages/calendar/grid-drag";
import { CalendarHeader } from "@/pages/calendar/CalendarHeader";
import { CalendarToolbar, type CalendarPanel } from "@/pages/calendar/CalendarToolbar";
import { SourcesDialog } from "@/pages/calendar/SourcesDialog";
import { useButlerEventEditor } from "@/pages/calendar/use-butler-event-editor";
import { useButlerLaneActions } from "@/pages/calendar/use-butler-lane-actions";
import {
  useCalendarUrlState,
  useSourceSelectionGuard,
} from "@/pages/calendar/use-calendar-url-state";
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
  SELECT_CLASS,
  formatLaneTitle,
  headlineLabel,
  isRecurringUserEntry,
  occurrenceImpactText,
  resolveButlerEventTarget,
  resolveOwnerFromEntry,
  shiftAnchor,
  titleize,
  truncateCalendarId,
  type ButlerLaneRows,
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

/** Default scroll-to position (in hours) when the time grid first mounts. */
const DEFAULT_SCROLL_HOUR = 7.5;



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

  const {
    view,
    range,
    anchor,
    selectedSourceKey,
    selectedCalendarId,
    selectedStatus,
    selectedSourceType,
    overlaysEnabled,
    updateQuery,
  } = useCalendarUrlState();

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
  const { handleSyncAll, syncButtonLabel } = sync;

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

  useSourceSelectionGuard({
    view,
    selectedSourceKey,
    selectedCalendarId,
    userSources,
    updateQuery,
  });

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

  // The four panels are mutually exclusive: opening one closes the others.
  function togglePanel(panel: CalendarPanel) {
    setActivityPanelOpen(panel === "activity" ? (prev) => !prev : false);
    setFindTimePanelOpen(panel === "findTime" ? (prev) => !prev : false);
    setProposalsPanelOpen(panel === "proposals" ? (prev) => !prev : false);
    setDuplicatesPanelOpen(panel === "duplicates" ? (prev) => !prev : false);
    if (panel === "activity") setAuditOffset(0);
  }

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
      <CalendarHeader
        view={view}
        range={range}
        timezone={timezone}
        anchor={anchor}
        start={start}
        end={end}
        entryCount={entries.length}
        sync={sync}
        hiddenSourceCount={hiddenSources.size}
        butlerPending={butlerMutation.isPending}
        userEventPending={userEventMutation.isPending}
        canCreateUserEvents={canCreateUserEvents}
        onSearch={() => setSearchPaletteOpen(true)}
        onAgenda={() => setAgendaOpen(true)}
        onPortability={() => setPortabilityOpen(true)}
        onSources={() => setSourcesDialogOpen(true)}
        onCreateButlerEvent={() => openButlerCreateDialog()}
        onCreateUserEvent={() => openUserCreateDialog()}
      />

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

      <CalendarToolbar
        view={view}
        range={range}
        anchor={anchor}
        updateQuery={updateQuery}
        panels={{
          activity: activityPanelOpen,
          findTime: findTimePanelOpen,
          proposals: proposalsPanelOpen,
          duplicates: duplicatesPanelOpen,
        }}
        onTogglePanel={togglePanel}
        selectedStatus={selectedStatus}
        selectedSourceType={selectedSourceType}
        selectedCalendarId={selectedCalendarId}
        selectedSourceKey={selectedSourceKey}
        overlaysEnabled={overlaysEnabled}
        overlaysError={overlaysQuery.isError}
        overlaysFetched={overlaysQuery.isFetched}
        hasDomainContext={hasDomainContext}
        userSources={userSources}
        calendarFilterOptions={calendarFilterOptions}
      />

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
            <ButlerLanes
              lanes={butlerLaneRows}
              defaultTimezone={defaultTimezone}
              pending={butlerMutation.isPending}
              onAddEvent={openButlerCreateDialog}
              onOpenDetail={openDetailPanel}
              onToggle={handleButlerToggle}
              onSnooze={handleButlerSnooze}
              onDismiss={handleButlerDismiss}
              onDelete={handleButlerDelete}
            />
          ) : range === "month" ? (
            /* ---- Month matrix ---- */
            <MonthGrid
              monthDays={monthDays}
              start={start}
              entriesByDay={entriesByDay}
              overlaysEnabled={overlaysEnabled}
              overlaysByDayMap={overlaysByDayMap}
              defaultTimezone={defaultTimezone}
              onCreateOnDay={view === "user" ? openUserCreateDialog : undefined}
              onOpenDetail={openDetailPanel}
              updateQuery={updateQuery}
            />
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
              <TimeGrid
                range={range}
                weekDays={weekDays}
                entriesByDay={entriesByDay}
                overlaysEnabled={overlaysEnabled}
                overlaysByDayMap={overlaysByDayMap}
                overlapEntryIds={overlapEntryIds}
                defaultTimezone={defaultTimezone}
                timeGridRef={timeGridRef}
                gridBodyRef={gridBodyRef}
                gutterRef={gutterRef}
                onOpenDetail={openDetailPanel}
                interaction={
                  view === "user"
                    ? {
                        gridDrag,
                        movedGhost,
                        findTimeSlots,
                        suppressClickRef,
                        openCreateDialog: openUserCreateDialog,
                        selectFindTimeSlot,
                        undoMove,
                        beginCreateDrag,
                        beginMoveDrag,
                        beginResizeDrag,
                        handleGridPointerMove,
                        handleGridPointerUp,
                        handleGridPointerCancel,
                      }
                    : undefined
                }
              />
            </div>
          ) : (
            <AgendaList
              entries={entries}
              view={view}
              defaultTimezone={defaultTimezone}
              pending={userEventMutation.isPending}
              onOpenDetail={openDetailPanel}
              onDelete={(entry) => {
                setDeleteScope(isRecurringUserEntry(entry) ? "this" : "series");
                setDeleteCandidate(entry);
              }}
            />
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
