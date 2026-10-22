/**
 * Right-docked detail panel for one calendar entry: upgrades the grid row with
 * the fresh server copy, shows provenance, attendees, linked people and the
 * meeting-prep rail, and saves inline edits on blur.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import {
  useCalendarWorkspaceEntry,
  useMutateCalendarWorkspaceButlerEvent,
  useMutateCalendarWorkspaceUserEvent,
} from "@/hooks/use-calendar-workspace";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { ContactPeoplePicker, type SelectedPerson } from "@/components/calendar/ContactPeoplePicker";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { LinkedPeopleChips } from "@/components/calendar/LinkedPeopleAvatars";
import { MeetingPrepRailContainer } from "@/components/calendar/MeetingPrepRail";
import { Mono } from "@/components/ui/Mono";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { StateDot } from "@/components/ui/StateDot";
import { Tip } from "@/components/ui/tip";
import { formatEventTime } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { KindTag, PillButton } from "@/pages/calendar/atoms";
import { calendarMutationErrorMessage, isCalendarMutationOk } from "@/pages/calendar/mutation-result";
import {
  isRecurringUserEntry,
  resolveButlerEventTarget,
  resolveOwnerFromEntry,
  syncDotState,
} from "@/pages/calendar/workspace-model";

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
    onCancel?: () => void,
    onFailure?: () => void,
  ) => void;
  userMutation: ReturnType<typeof useMutateCalendarWorkspaceUserEvent>;
  butlerMutation: ReturnType<typeof useMutateCalendarWorkspaceButlerEvent>;
  /** Workspace timezone used to render the event's start/end (not browser-local). */
  timezone: string;
}

/** Map an entry's read-model linked_people to the picker's SelectedPerson shape. */
function linkedPeopleToSelected(
  people: UnifiedCalendarEntry["linked_people"],
): SelectedPerson[] {
  return (people ?? []).map((p) => ({
    entity_id: p.entity_id,
    canonical_name: p.display_label,
  }));
}

/**
 * Set-identity key for a people list, for untouched-detection. Order-insensitive
 * (entity_ids are a SET — order carries no meaning to the backend), so a fresh
 * server copy that returns the same people in a different order is not mistaken
 * for a user edit.
 */
function peopleKey(people: SelectedPerson[]): string {
  return people
    .map((p) => p.entity_id)
    .sort()
    .join(",");
}

export function CalendarEntryDetailPanel({
  entry: gridEntry,
  onClose,
  onDelete,
  onRecurringEdit,
  userMutation,
  butlerMutation,
  timezone,
}: CalendarEntryDetailPanelProps) {
  // Fetch the fresh server copy of the selected entry (bu-rgq00). The grid row is
  // already loaded and shown immediately; when the single-entry read lands we
  // upgrade to it in place (provenance / sync_state / freshness that the grid
  // window may not reflect). On error we keep the grid row + a non-blocking
  // degraded note — never a blank panel. Everything below reads `entry`, so the
  // whole render (including save-on-blur baselines) flows through this one line.
  const detailQuery = useCalendarWorkspaceEntry(gridEntry.entry_id, {
    timezone,
  });
  const entry = detailQuery.data?.data ?? gridEntry;
  const detailFetchFailed = detailQuery.isError;

  const isUserEvent = entry.source_type === "provider_event";
  const isButlerEvent =
    entry.source_type === "scheduled_task" ||
    entry.source_type === "butler_reminder";
  const isPending = userMutation.isPending || butlerMutation.isPending;

  // Inline editable fields — seeded from the grid row on mount (state is reset on
  // mount via key={entry.entry_id} at the call site), then upgraded in place when
  // the fresh server copy lands (see the effect below).
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
  // Editable linked-people (bu-ya8uv): seed the picker from the event's existing
  // links so editing round-trips them instead of starting empty. linked_people
  // is {entity_id, display_label}; the picker speaks {entity_id, canonical_name}.
  const [peopleDraft, setPeopleDraft] = useState<SelectedPerson[]>(() =>
    linkedPeopleToSelected(entry.linked_people),
  );

  // Upgrade the editable drafts in place when the fresh server copy arrives,
  // but only for fields the user has not touched (draft still equals the last
  // value we seeded). This preserves in-flight edits while letting a fresher
  // server title/description/location flow through the same inputs.
  const lastSeededRef = useRef({
    title: gridEntry.title,
    description:
      typeof gridEntry.metadata?.description === "string"
        ? gridEntry.metadata.description
        : "",
    location:
      typeof gridEntry.metadata?.location === "string"
        ? gridEntry.metadata.location
        : "",
    people: peopleKey(linkedPeopleToSelected(gridEntry.linked_people)),
  });
  const freshEntry = detailQuery.data?.data;
  useEffect(() => {
    if (!freshEntry) return;
    const freshTitle = freshEntry.title;
    const freshDescription =
      typeof freshEntry.metadata?.description === "string"
        ? freshEntry.metadata.description
        : "";
    const freshLocation =
      typeof freshEntry.metadata?.location === "string"
        ? freshEntry.metadata.location
        : "";
    const freshPeople = linkedPeopleToSelected(freshEntry.linked_people);
    setTitleDraft((d) =>
      d === lastSeededRef.current.title ? freshTitle : d,
    );
    setDescriptionDraft((d) =>
      d === lastSeededRef.current.description ? freshDescription : d,
    );
    setLocationDraft((d) =>
      d === lastSeededRef.current.location ? freshLocation : d,
    );
    // Upgrade the people draft in place only if the user hasn't touched it.
    setPeopleDraft((d) =>
      peopleKey(d) === lastSeededRef.current.people ? freshPeople : d,
    );
    lastSeededRef.current = {
      title: freshTitle,
      description: freshDescription,
      location: freshLocation,
      people: peopleKey(freshPeople),
    };
  }, [freshEntry]);

  function fireUserUpdate(
    patch: Record<string, unknown>,
    label: string,
    onRecurringCancel?: () => void,
    onFailure?: () => void,
  ) {
    const { butlerName, calendarId } = resolveOwnerFromEntry(entry);
    if (!butlerName || !entry.provider_event_id) return;
    // A recurring occurrence must first ask the user which occurrences the edit
    // applies to (this / following / series); defer to the parent scope sheet.
    if (isRecurringUserEntry(entry) && onRecurringEdit) {
      onRecurringEdit(entry, patch, label, onRecurringCancel, onFailure);
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
            onFailure?.();
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
          onFailure?.();
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

  function handlePeopleChange(next: SelectedPerson[]) {
    if (!canMutateUser) return;
    const previous = peopleDraft;
    setPeopleDraft(next);
    // Non-empty edits replace the linked set. A zero-length replacement carries
    // an explicit destructive signal so omitted or incidental empty values still
    // preserve the existing links on the backend.
    const entityIds = next.map((person) => person.entity_id);
    const restorePeople = () => setPeopleDraft(previous);
    fireUserUpdate(
      entityIds.length === 0
        ? { entity_ids: [], clear_entity_ids: true }
        : { entity_ids: entityIds },
      "people",
      restorePeople,
      restorePeople,
    );
  }

  const startFmt = entry.all_day
    ? formatEventTime(entry.start_at, timezone, "EEE, MMM d")
    : formatEventTime(entry.start_at, timezone, "EEE, MMM d · HH:mm");
  const endFmt = entry.all_day
    ? formatEventTime(entry.end_at, timezone, "EEE, MMM d")
    : formatEventTime(entry.end_at, timezone, "HH:mm");

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
                  ? "text-[var(--red-text)]"
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
          className="font-mono text-[11px] text-[var(--mfg)] hover:text-fg"
        >
          ✕
        </button>
      </div>

      {/* Fresh-copy fetch failed — keep the grid-row data (already rendered
          below) and surface a non-blocking degraded note (bu-rgq00). */}
      {detailFetchFailed ? (
        <div data-testid="detail-degraded-note">
          <SourceDegradedNote
            label="Live entry detail"
            detail="showing cached row"
            onRetry={() => detailQuery.refetch()}
          />
        </div>
      ) : null}

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
            className="w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm font-medium text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"
          />
        ) : (
          <span className="text-sm font-medium text-fg">
            {entry.title}
          </span>
        )}
      </div>

      {/* Time range */}
      <div className="flex flex-col gap-0.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
          When
        </span>
        <Mono className="text-fg">
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
            <Tip content={`Session ${entry.source_session_id}`}>
              <Link
                to={`/sessions/${entry.source_session_id}`}
                data-testid="detail-session-link"
                className="font-mono text-[11px] text-[var(--mfg)] underline decoration-dotted hover:text-fg"
              >
                session ›
              </Link>
            </Tip>
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

      {/* Linked people (bu-qs64f): identity-layer contacts linked to this event
          via calendar_event_entities, hydrated on the read so existing events
          show the same people chips the create dialog captured.
          bu-ya8uv: for editable user events, this is now a live ContactPeoplePicker
          seeded from linked_people (edits round-trip through entity_ids); other
          entry kinds keep the read-only chips. */}
      {isUserEvent ? (
        <div className="flex flex-col gap-1" data-testid="detail-linked-people">
          <ContactPeoplePicker
            value={peopleDraft}
            onChange={handlePeopleChange}
            disabled={isPending || !canMutateUser}
          />
        </div>
      ) : entry.linked_people && entry.linked_people.length > 0 ? (
        <div className="flex flex-col gap-1" data-testid="detail-linked-people">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
            People
          </span>
          <LinkedPeopleChips people={entry.linked_people} />
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
            className="w-full resize-none rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"
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
            className="w-full rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 py-1.5 text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"
          />
        </div>
      ) : null}

      {/* Delete scope (user editable events only) */}
      {canMutateUser && onDelete ? (
        <div className="mt-auto border-t border-[var(--border)] pt-3">
          <PillButton
            data-testid="detail-delete-button"
            className="hover:border-[var(--red)] hover:text-[var(--red-text)]"
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
