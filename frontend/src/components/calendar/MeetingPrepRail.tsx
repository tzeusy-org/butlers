/**
 * Meeting-prep rail — context for a selected entity-linked event (bu-rct3g).
 *
 * Renders the meeting-prep read (`GET /api/calendar/workspace/prep/{event_id}`)
 * for a selected event: resolved attendees with their Dunbar-tier relationship
 * letter-mark, durable relationship notes, last-met (from prior co-attended
 * events), and a per-attendee message-context panel.
 *
 * STRUCTURED v1 — there is NO per-open LLM call and NO generated prose. Every
 * field is drawn verbatim from the precomputed prep contribution envelope
 * (relationship butler's deterministic job). The per-attendee message-context
 * contribution is produced by a separate in-flight job (bu-tmtpb); this rail
 * renders whatever the endpoint returns and is gracefully empty until it lands.
 *
 * Honest empty-state:
 * - `hasPrepContext === false` (no specialist contributed for this event, or the
 *   cached view is absent/unreadable — the expected state for most events today)
 *   → render an explicit "No prep context yet" line rather than fabricating data.
 * - `hasPrepContext === true` with zero attendees → coverage ran but resolved no
 *   attendees; still rendered honestly as the empty-state.
 */

import type { CalendarPrepAttendee } from "@/api/types.ts";
import { TierBadge, tierLabel } from "@/components/ui/TierBadge.tsx";
import { useCalendarMeetingPrep } from "@/hooks/use-calendar-workspace.ts";

/** Title-case a `source_butler` identifier for the contributor footnote. */
function titleizeToken(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
    .trim();
}

/**
 * Best-effort extraction of a human label + secondary line from one opaque
 * message-context item. The contributing butler owns the shape (bu-tmtpb), so we
 * probe a small set of conventional keys and degrade gracefully when none match.
 */
function readMessageContext(item: Record<string, unknown>): {
  primary: string | null;
  secondary: string | null;
} {
  const str = (key: string): string | null => {
    const v = item[key];
    return typeof v === "string" && v.trim() ? v.trim() : null;
  };
  const primary = str("subject") ?? str("title") ?? str("snippet") ?? str("preview") ?? str("text");
  const secondary = str("from") ?? str("sender") ?? str("channel") ?? str("date") ?? str("when");
  return { primary, secondary };
}

/** One message-context item row inside an attendee's panel. */
function MessageContextItem({ item }: { item: Record<string, unknown> }) {
  const { primary, secondary } = readMessageContext(item);
  return (
    <div
      data-testid="prep-message-item"
      className="flex flex-col gap-0.5 rounded-[2px] border border-dashed border-[var(--border)] bg-foreground/[0.02] px-1.5 py-1"
    >
      <span className="truncate text-[11px] leading-none text-[var(--fg)]">
        {primary ?? "Message context"}
      </span>
      {secondary ? (
        <span className="truncate font-mono text-[10px] leading-none text-[var(--dim)]">
          {secondary}
        </span>
      ) : null}
    </div>
  );
}

/** One attendee card: name + tier letter-mark, last-met, notes, message context. */
function PrepAttendeeCard({ attendee }: { attendee: CalendarPrepAttendee }) {
  const hasTier = attendee.dunbar_tier != null;
  return (
    <div
      data-testid="prep-attendee"
      data-prep-entity={attendee.entity_id}
      className="flex flex-col gap-1.5 rounded-[3px] border border-[var(--border)] bg-foreground/[0.015] p-2"
    >
      <div className="flex items-center gap-1.5">
        <span className="truncate text-sm font-medium text-[var(--fg)]">{attendee.name}</span>
        {hasTier ? (
          <TierBadge
            tier={attendee.dunbar_tier as number}
            data-testid="prep-tier-mark"
            title={`Dunbar tier ${tierLabel(attendee.dunbar_tier as number)}`}
          />
        ) : (
          <span
            data-testid="prep-tier-mark"
            className="font-mono text-[9px] uppercase leading-none text-[var(--dim)]"
            title="No relationship tier"
          >
            —
          </span>
        )}
      </div>

      {attendee.last_met ? (
        <div data-testid="prep-last-met" className="font-mono text-[10px] text-[var(--mfg)]">
          <span className="text-[var(--dim)]">Last met</span> {attendee.last_met}
          {attendee.last_met_event ? (
            <span className="text-[var(--dim)]"> · {attendee.last_met_event}</span>
          ) : null}
        </div>
      ) : null}

      {attendee.notes.length > 0 ? (
        <ul data-testid="prep-notes" className="flex flex-col gap-0.5">
          {attendee.notes.map((note, idx) => (
            <li
              key={`${note.kind}-${idx}`}
              data-prep-note-kind={note.kind}
              className="flex gap-1 text-[11px] leading-snug text-[var(--fg)]"
            >
              <span className="shrink-0 font-mono text-[10px] uppercase text-[var(--dim)]">
                {note.kind}
              </span>
              <span className="min-w-0">{note.text}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {attendee.message_context.length > 0 ? (
        <div data-testid="prep-message-context" className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--mfg)]">
            Recent messages
          </span>
          <div className="flex flex-col gap-1">
            {attendee.message_context.map((item, idx) => (
              <MessageContextItem key={idx} item={item} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export interface MeetingPrepRailProps {
  /** Event label shown in the header (e.g. the event title). */
  heading?: string;
  /** Whether the underlying query is still loading (first fetch). */
  isLoading?: boolean;
  /** Whether at least one specialist contributed prep context for the event. */
  hasPrepContext: boolean;
  /** Resolved attendees with merged prep context. */
  attendees: CalendarPrepAttendee[];
  /** Butlers that contributed context (rendered as a provenance footnote). */
  sourceButlers?: string[];
}

/**
 * Presentational meeting-prep rail. Data fetching lives in
 * {@link MeetingPrepRailContainer}; this component only renders the structured
 * payload (kept prop-driven so it is trivially unit-testable).
 */
export function MeetingPrepRail({
  heading,
  isLoading = false,
  hasPrepContext,
  attendees,
  sourceButlers = [],
}: MeetingPrepRailProps) {
  const showEmpty = !isLoading && (!hasPrepContext || attendees.length === 0);
  return (
    <section
      data-testid="meeting-prep-rail"
      aria-label="Meeting prep"
      className="rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3"
    >
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--mfg)]">
          Meeting prep
        </h2>
        {heading ? (
          <span className="truncate font-mono text-[11px] text-[var(--fg)]">{heading}</span>
        ) : null}
      </header>

      {isLoading ? (
        <p className="font-mono text-[11px] text-[var(--dim)]">Loading…</p>
      ) : showEmpty ? (
        // Honest empty-state — no specialist contributed prep context for this
        // event (co-attended / contact-link coverage not yet populated, or the
        // cached view is unavailable). The expected state for most events today.
        <p
          data-testid="meeting-prep-empty"
          className="font-mono text-[11px] leading-snug text-[var(--mfg)]"
        >
          No prep context yet — attendee relationships and message history will
          appear here once they are linked.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-2">
            {attendees.map((attendee) => (
              <PrepAttendeeCard key={attendee.entity_id} attendee={attendee} />
            ))}
          </div>
          {sourceButlers.length > 0 ? (
            <p
              data-testid="prep-source-butlers"
              className="font-mono text-[10px] text-[var(--dim)]"
            >
              via {sourceButlers.map(titleizeToken).join(", ")}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}

export interface MeetingPrepRailContainerProps {
  /** Calendar event id whose prep context to fetch. */
  eventId: string | null | undefined;
  /** Gate the fetch (e.g. only for entity-relevant events). Defaults to `true`. */
  enabled?: boolean;
  /** Event label shown in the rail header. */
  heading?: string;
}

/**
 * Data-fetching wrapper around {@link MeetingPrepRail}. Reads the prep endpoint
 * via {@link useCalendarMeetingPrep} and projects the response into the rail.
 *
 * Fail-open: the endpoint never 500s for an unknown/uncovered event (it returns
 * the empty-state payload), and a transport error degrades to the same honest
 * "No prep context yet" empty-state rather than surfacing an error banner.
 */
export function MeetingPrepRailContainer({
  eventId,
  enabled = true,
  heading,
}: MeetingPrepRailContainerProps) {
  const { data, isLoading, isError } = useCalendarMeetingPrep(eventId, { enabled });
  const prep = data?.data;

  return (
    <MeetingPrepRail
      heading={heading}
      isLoading={enabled && !!eventId && isLoading}
      // A transport error degrades to the honest empty-state (fail-open).
      hasPrepContext={!isError && (prep?.has_prep_context ?? false)}
      attendees={prep?.attendees ?? []}
      sourceButlers={prep?.source_butlers ?? []}
    />
  );
}

export default MeetingPrepRail;
