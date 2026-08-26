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

import type {
  CalendarPrepAttendee,
  CalendarPrepCommitment,
  CalendarPrepCommitmentDirection,
  CalendarPrepCommitmentKind,
  CalendarPrepMessageContext,
} from "@/api/types.ts";
import { TierBadge, tierLabel } from "@/components/ui/TierBadge.tsx";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils.ts";
import { useCalendarMeetingPrep } from "@/hooks/use-calendar-workspace.ts";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ClipboardCheck,
  Handshake,
  Hourglass,
  ListChecks,
  RotateCcw,
  Scale,
  type LucideIcon,
} from "lucide-react";

/** Title-case a `source_butler` identifier for the contributor footnote. */
function titleizeToken(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
    .trim();
}

/**
 * Extract the display label + secondary line from one typed message-context
 * thread (the {@link CalendarPrepMessageContext} envelope contributed by the
 * email-owning butlers' prep job). Optional fields may be empty/`null`, so we
 * trim and fall back gracefully rather than assuming presence.
 */
function readMessageContext(item: CalendarPrepMessageContext): {
  primary: string | null;
  secondary: string | null;
} {
  const clean = (value: string | null | undefined): string | null =>
    typeof value === "string" && value.trim() ? value.trim() : null;
  const primary = clean(item.subject) ?? clean(item.snippet);
  const secondary = clean(item.last_message_at) ?? clean(item.channel);
  return { primary, secondary };
}

/** One message-context item row inside an attendee's panel. */
function MessageContextItem({ item }: { item: CalendarPrepMessageContext }) {
  const { primary, secondary } = readMessageContext(item);
  return (
    <div
      data-testid="prep-message-item"
      className="flex flex-col gap-0.5 rounded-[2px] border border-dashed border-[var(--border)] bg-foreground/[0.02] px-1.5 py-1"
    >
      <span className="truncate text-[11px] leading-none text-fg">
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

const COMMITMENT_KIND_META: Record<
  CalendarPrepCommitmentKind,
  { label: string; Icon: LucideIcon }
> = {
  promise: { label: "PROMISE", Icon: Handshake },
  waiting_for: { label: "WAITING FOR", Icon: Hourglass },
  follow_up: { label: "FOLLOW UP", Icon: ListChecks },
  obligation: { label: "OBLIGATION", Icon: ClipboardCheck },
  decision: { label: "DECISION", Icon: Scale },
};

const COMMITMENT_DIRECTION_META: Record<
  CalendarPrepCommitmentDirection,
  { label: string; Icon: LucideIcon }
> = {
  owner_to_other: { label: "Owner owes", Icon: ArrowUpRight },
  other_to_owner: { label: "Counterparty owes owner", Icon: ArrowDownLeft },
  self: { label: "Owner commitment", Icon: RotateCcw },
};

/** The API uses labels such as L0 through L3; tolerate numeric labels too. */
function commitmentEscalationLevel(level: string): number {
  const match = /^L?([0-9]+)$/i.exec(level.trim());
  return match ? Number.parseInt(match[1], 10) : -1;
}

function CommitmentRow({ commitment }: { commitment: CalendarPrepCommitment }) {
  const kind = COMMITMENT_KIND_META[commitment.kind];
  const direction = COMMITMENT_DIRECTION_META[commitment.direction];
  const isEscalated = commitmentEscalationLevel(commitment.escalation_level) >= 2;
  const accessibleDeadline = commitment.deadline ? `; deadline ${commitment.deadline}` : "";
  const accessibleEscalation = commitment.escalation_level.trim()
    ? `; escalation ${commitment.escalation_level}`
    : "";
  const accessibleLabel = `${direction.label}; ${kind.label.toLowerCase()}; ${commitment.summary}${accessibleDeadline}${accessibleEscalation}`;

  return (
    <li
      data-testid="prep-commitment"
      data-kind={commitment.kind}
      data-direction={commitment.direction}
      data-escalation-level={commitment.escalation_level}
      data-escalated={isEscalated}
      aria-label={accessibleLabel}
      className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 border-b border-[var(--border)] py-2 last:border-b-0 font-mono text-[10px] leading-snug"
    >
      <kind.Icon
        data-testid="prep-commitment-kind-icon"
        aria-hidden="true"
        className="mt-0.5 size-3 shrink-0 text-[var(--dim)]"
        strokeWidth={1.5}
      />
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span
            data-testid="prep-commitment-kind"
            className="shrink-0 uppercase tracking-[0.08em] text-[var(--dim)]"
          >
            {kind.label}
          </span>
          <span className="inline-flex min-w-0 items-center gap-1 text-[var(--mfg)]">
            <direction.Icon
              data-testid="prep-commitment-direction-icon"
              aria-hidden="true"
              className="size-3 shrink-0"
              strokeWidth={1.5}
            />
            <span>{direction.label}</span>
          </span>
        </div>
        <p className="mt-1 min-w-0 break-words text-[11px] leading-snug text-fg">
          {commitment.summary}
        </p>
        {commitment.deadline ? (
          <span
            data-testid="prep-commitment-deadline"
            className="mt-1 inline-flex shrink-0 items-center gap-1 text-[var(--dim)]"
          >
            <span className="sr-only">Deadline </span>
            <Time value={commitment.deadline} mode="absolute" precision="day" compact />
          </span>
        ) : null}
      </div>
      <span
        data-testid="prep-commitment-escalation"
        aria-hidden="true"
        title={`Escalation ${commitment.escalation_level}`}
        className={cn(
          "shrink-0 tabular-nums",
          isEscalated ? "font-medium text-[var(--amber-text)]" : "text-[var(--dim)]",
        )}
      >
        {commitment.escalation_level}
      </span>
    </li>
  );
}

function CommitmentSection({ attendee }: { attendee: CalendarPrepAttendee }) {
  // The API normalizes legacy envelopes to [], but keep this projection
  // tolerant of retained pre-normalization data while a query refreshes.
  const commitments = attendee.commitments ?? [];
  if (commitments.length === 0) return null;

  return (
    <div data-testid="prep-commitments" className="flex min-w-0 flex-col gap-1">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--mfg)]">
        Commitments
      </h3>
      <ul
        aria-label={`Commitments for ${attendee.name}`}
        className="min-w-0 list-none p-0"
      >
        {commitments.map((item, index) => (
          <CommitmentRow key={`${item.fingerprint}-${index}`} commitment={item} />
        ))}
      </ul>
    </div>
  );
}

/** One attendee card: name + tier letter-mark, commitments, last-met, notes, message context. */
function PrepAttendeeCard({ attendee }: { attendee: CalendarPrepAttendee }) {
  const hasTier = attendee.dunbar_tier != null;
  return (
    <div
      data-testid="prep-attendee"
      data-prep-entity={attendee.entity_id}
      className="flex flex-col gap-1.5 rounded-[3px] border border-[var(--border)] bg-foreground/[0.015] p-2"
    >
      <div className="flex items-center gap-1.5">
        <span className="truncate text-sm font-medium text-fg">{attendee.name}</span>
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

      <CommitmentSection attendee={attendee} />

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
              className="flex gap-1 text-[11px] leading-snug text-fg"
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
  /** Whether retained context is refreshing for a newly selected event. */
  isFetching?: boolean;
  /** Whether the query failed; this always takes precedence over stale context. */
  isError?: boolean;
  /** Query error detail shown when the read could not complete. */
  error?: Error | null;
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
  isFetching = false,
  isError = false,
  error = null,
  hasPrepContext,
  attendees,
  sourceButlers = [],
}: MeetingPrepRailProps) {
  const showEmpty =
    !isLoading && !isError && (!hasPrepContext || attendees.length === 0);
  const content = (
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
          <span className="truncate font-mono text-[11px] text-fg">{heading}</span>
        ) : null}
      </header>

      {isLoading ? (
        <p className="font-mono text-[11px] text-[var(--dim)]">Loading…</p>
      ) : isError ? (
        <p role="alert" className="font-mono text-[11px] text-[var(--red-text)]">
          Couldn&apos;t load meeting prep. {error?.message ?? "Unknown error"}
        </p>
      ) : showEmpty ? (
        // Honest empty-state — no specialist contributed prep context for this
        // event (co-attended / contact-link coverage not yet populated, or the
        // cached view is unavailable). The expected state for most events today.
        <p
          data-testid="meeting-prep-empty"
          className="font-mono text-[11px] leading-snug text-[var(--mfg)]"
        >
          No prep context yet. Attendee relationships and message history will
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

  return (
    <FetchingDim isFetching={isFetching && !isLoading && !isError}>
      {content}
    </FetchingDim>
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
 * Unknown or uncovered events return a successful empty-state payload. A real
 * transport/query error remains visible so retained context never reads as a
 * successful response for the newly selected event.
 */
export function MeetingPrepRailContainer({
  eventId,
  enabled = true,
  heading,
}: MeetingPrepRailContainerProps) {
  const { data, error, isError, isFetching, isLoading } = useCalendarMeetingPrep(eventId, {
    enabled,
  });
  const prep = data?.data;

  return (
    <MeetingPrepRail
      heading={heading}
      isLoading={enabled && !!eventId && isLoading}
      isFetching={enabled && !!eventId && isFetching && !isLoading}
      isError={isError}
      error={error instanceof Error ? error : null}
      hasPrepContext={prep?.has_prep_context ?? false}
      attendees={prep?.attendees ?? []}
      sourceButlers={prep?.source_butlers ?? []}
    />
  );
}
