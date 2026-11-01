/**
 * Right-docked detail panel for one calendar entry: upgrades the grid row with
 * the fresh server copy, shows provenance, attendees, linked people and the
 * meeting-prep rail. Provider events are read-only (operator view); a butler
 * lane entry's title can be edited inline and saves on blur.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import {
  useCalendarWorkspaceEntry,
  useMutateCalendarWorkspaceButlerEvent,
} from "@/hooks/use-calendar-workspace";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { LinkedPeopleChips } from "@/components/calendar/LinkedPeopleAvatars";
import { MeetingPrepRailContainer } from "@/components/calendar/MeetingPrepRail";
import { Mono } from "@/components/ui/Mono";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { StateDot } from "@/components/ui/StateDot";
import { Tip } from "@/components/ui/tip";
import { formatEventTime } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";
import { KindTag } from "@/pages/calendar/atoms";
import { calendarMutationErrorMessage, isCalendarMutationOk } from "@/pages/calendar/mutation-result";
import { resolveButlerEventTarget, syncDotState } from "@/pages/calendar/workspace-model";

interface CalendarEntryDetailPanelProps {
  entry: UnifiedCalendarEntry;
  onClose: () => void;
  butlerMutation: ReturnType<typeof useMutateCalendarWorkspaceButlerEvent>;
  /** Workspace timezone used to render the event's start/end (not browser-local). */
  timezone: string;
}

const FIELD_LABEL_CLASS =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]";

/** Attendee emails/names from Google Calendar event metadata. */
function attendeesOf(entry: UnifiedCalendarEntry): string[] {
  const raw = entry.metadata?.event_metadata;
  if (!raw || typeof raw !== "object") return [];
  const list = (raw as Record<string, unknown>).attendees;
  if (!Array.isArray(list)) return [];
  return list
    .map((a) => {
      if (typeof a === "string") return a;
      if (typeof a === "object" && a !== null) {
        const obj = a as Record<string, unknown>;
        if (typeof obj.email === "string") return obj.email;
        return typeof obj.displayName === "string" ? obj.displayName : "";
      }
      return "";
    })
    .filter(Boolean);
}

function metadataText(entry: UnifiedCalendarEntry, key: "description" | "location"): string {
  const value = entry.metadata?.[key];
  return typeof value === "string" ? value : "";
}

function ReadOnlyField({ label, value, testId }: { label: string; value: string; testId: string }) {
  if (!value) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className={FIELD_LABEL_CLASS}>{label}</span>
      <span data-testid={testId} className="whitespace-pre-wrap text-sm text-fg">
        {value}
      </span>
    </div>
  );
}

export function CalendarEntryDetailPanel({
  entry: gridEntry,
  onClose,
  butlerMutation,
  timezone,
}: CalendarEntryDetailPanelProps) {
  // Fetch the fresh server copy of the selected entry (bu-rgq00). The grid row is
  // already loaded and shown immediately; when the single-entry read lands we
  // upgrade to it in place (provenance / sync_state / freshness that the grid
  // window may not reflect). On error we keep the grid row + a non-blocking
  // degraded note, never a blank panel.
  const detailQuery = useCalendarWorkspaceEntry(gridEntry.entry_id, { timezone });
  const entry = detailQuery.data?.data ?? gridEntry;

  const isUserEvent = entry.source_type === "provider_event";
  const canEditButler =
    entry.source_type === "scheduled_task" || entry.source_type === "butler_reminder";
  const isPending = butlerMutation.isPending;

  // The title draft is seeded from the grid row on mount (state resets via
  // key={entry.entry_id} at the call site), then upgraded in place when the
  // fresh server copy lands, but only while the user has not touched it.
  const [titleDraft, setTitleDraft] = useState(entry.title);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");
  const lastSeededTitle = useRef(gridEntry.title);
  const freshEntry = detailQuery.data?.data;
  useEffect(() => {
    if (!freshEntry) return;
    setTitleDraft((draft) =>
      draft === lastSeededTitle.current ? freshEntry.title : draft,
    );
    lastSeededTitle.current = freshEntry.title;
  }, [freshEntry]);

  function fireButlerUpdate(patch: Record<string, unknown>, label: string) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) return;
    setSaveStatus("idle");
    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "update",
        request_id: `detail-update-${Date.now()}`,
        payload: { event_id: target.eventId, source_hint: target.sourceHint, ...patch },
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
            error instanceof Error ? error.message : `Failed to update ${label}.`,
          );
          setSaveStatus("error");
        },
      },
    );
  }

  function handleTitleBlur() {
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === entry.title) return;
    fireButlerUpdate({ title: trimmed }, "title");
  }

  const startFmt = entry.all_day
    ? formatEventTime(entry.start_at, timezone, "EEE, MMM d")
    : formatEventTime(entry.start_at, timezone, "EEE, MMM d · HH:mm");
  const endFmt = entry.all_day
    ? formatEventTime(entry.end_at, timezone, "EEE, MMM d")
    : formatEventTime(entry.end_at, timezone, "HH:mm");
  const attendees = useMemo(() => attendeesOf(entry), [entry]);

  return (
    <div
      data-testid="entry-detail-panel"
      className="flex min-h-0 flex-col gap-4 overflow-y-auto pb-4"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Eyebrow as="div">Event detail</Eyebrow>
          {canEditButler ? (
            <span
              data-testid="detail-save-status"
              className={cn(
                "font-mono text-[10px] uppercase tracking-[0.12em]",
                saveStatus === "error" ? "text-[var(--red-text)]" : "text-[var(--mfg)]",
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

      {detailQuery.isError ? (
        <div data-testid="detail-degraded-note">
          <SourceDegradedNote
            label="Live entry detail"
            detail="showing cached row"
            onRetry={() => detailQuery.refetch()}
          />
        </div>
      ) : null}

      <div className="flex flex-col gap-1">
        <label htmlFor="detail-title" className={FIELD_LABEL_CLASS}>
          Title
        </label>
        {canEditButler ? (
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
          <span
            id="detail-title"
            data-testid="detail-title"
            className="text-sm font-medium text-fg"
          >
            {entry.title}
          </span>
        )}
      </div>

      <div className="flex flex-col gap-0.5">
        <span className={FIELD_LABEL_CLASS}>When</span>
        <Mono className="text-fg">
          {startFmt}
          {!entry.all_day && ` – ${endFmt}`}
        </Mono>
        <Mono muted>{entry.timezone}</Mono>
      </div>

      <div className="flex items-center gap-2">
        <StateDot state={syncDotState(entry.sync_state ?? "stale")} />
        <Mono muted>{entry.sync_state ?? "unknown"}</Mono>
        <KindTag>{entry.source_type.replace(/_/g, " ")}</KindTag>
      </div>

      <div className="flex flex-col gap-1">
        <span className={FIELD_LABEL_CLASS}>Source</span>
        <div className="flex items-center gap-2">
          {entry.butler_name ? <ButlerMark name={entry.butler_name} /> : null}
          <Mono muted className="truncate">
            {entry.source_key}
          </Mono>
        </div>
      </div>

      {entry.source_butler || entry.source_session_id ? (
        <div className="flex flex-col gap-1">
          <span className={FIELD_LABEL_CLASS}>Provenance</span>
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

      {attendees.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className={FIELD_LABEL_CLASS}>Attendees</span>
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

      {entry.linked_people && entry.linked_people.length > 0 ? (
        <div className="flex flex-col gap-1" data-testid="detail-linked-people">
          <span className={FIELD_LABEL_CLASS}>People</span>
          <LinkedPeopleChips people={entry.linked_people} />
        </div>
      ) : null}

      {/* Meeting-prep rail (bu-rct3g): attendee relationships, notes, last-met
          and per-attendee message context for an entity-linked meeting. Keyed on
          `event_id` (= `calendar_events.id`), not the per-instance `entry_id`;
          when the entry has no backing event row the container skips the fetch. */}
      {isUserEvent ? (
        <MeetingPrepRailContainer eventId={entry.event_id} heading={entry.title} />
      ) : null}

      {isUserEvent ? (
        <>
          <ReadOnlyField
            label="Description"
            value={metadataText(entry, "description")}
            testId="detail-description"
          />
          <ReadOnlyField
            label="Location"
            value={metadataText(entry, "location")}
            testId="detail-location"
          />
        </>
      ) : null}
    </div>
  );
}
