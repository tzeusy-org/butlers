// ---------------------------------------------------------------------------
// CalendarVerdictOpener — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import { formatInTimeZone } from "date-fns-tz";
import type { CalendarInvitationEntry, ConflictIssue, DayVerdictResponse } from "@/api/types";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { SourceDegradedNote } from "@/components/ui/query-boundary";

/** The day verdict read, as the page's query reports it. */
export interface DayVerdictState {
  data?: DayVerdictResponse | null;
  isLoading: boolean;
  isError: boolean;
}

export interface CalendarVerdictOpenerProps {
  /** The deterministic day verdict; an error or `available: false` reads as unknown, never clear. */
  dayVerdict: DayVerdictState;
  workspaceLoading: boolean;
  workspaceError: boolean;
  sourceFreshnessLoading: boolean;
  sourceFreshnessError: boolean;
  freshnessDetail: string | null;
  conflictScanEnabled: boolean;
  conflictLoading: boolean;
  conflictError: boolean;
  conflictsAvailable: boolean;
  conflicts: ConflictIssue[];
  invitations?: CalendarInvitationEntry[];
  invitationsLoading?: boolean;
  invitationsRefreshing?: boolean;
  invitationsError?: boolean;
  invitationsAvailable?: boolean;
  invitationConflictsAvailable?: boolean;
  invitationSourcesDegraded?: string[];
  invitationsHasMore?: boolean;
  invitationsLoadingMore?: boolean;
  onMoreInvitations?: () => void;
  onOpenInvitation?: (entryId: string) => void;
  invitationDetailLoading?: boolean;
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function clock(iso: string, zone: string): string {
  return formatInTimeZone(new Date(iso), zone, "HH:mm");
}

function duration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [hours ? `${hours}h` : "", rest ? `${rest}m` : ""].filter(Boolean).join(" ");
}

/** "today", "tomorrow", or the weekday and date; always names the real date for a rolled day. */
function dayLabel(verdict: DayVerdictResponse): string {
  const date = formatInTimeZone(new Date(`${verdict.date}T12:00:00Z`), "UTC", "EEE d MMM");
  if (verdict.relative === "other") return date;
  return verdict.relative === "tomorrow" ? `tomorrow (${date})` : "today";
}

/** Clauses in urgency order: what is on now or next, how much is committed, where the air is. */
function verdictClauses(
  verdict: DayVerdictResponse,
  nowMs: number,
): { lead: VerdictClause[]; tail: VerdictClause[] } {
  const clauses: VerdictClause[] = [];
  const { zone } = verdict;
  if (verdict.freshness.state === "stale") {
    clauses.push({
      key: "day-stale",
      text: verdict.freshness.as_of
        ? `calendar read as of ${clock(verdict.freshness.as_of, zone)}, may be out of date`
        : "calendar read may be out of date",
    });
  }
  if (verdict.current) {
    clauses.push({
      key: "day-current",
      text: `In ${verdict.current.title} until ${clock(verdict.current.end, zone)}`,
      href: verdict.current.door,
    });
  }
  if (verdict.next) {
    const minutes = Math.round((new Date(verdict.next.start).getTime() - nowMs) / 60000);
    const countdown = verdict.relative === "today" && minutes > 0 ? ` (in ${duration(minutes)})` : "";
    clauses.push({
      key: "day-next",
      text: `Next ${verdict.next.title} at ${clock(verdict.next.start, zone)}${countdown}`,
      href: verdict.next.door,
    });
  }
  const lead = clauses.splice(0);
  if (verdict.committed_minutes > 0) {
    clauses.push({
      key: "day-committed",
      text: `${duration(verdict.committed_minutes)} committed across ${plural(verdict.committed_count, "meeting")}`,
    });
  }
  if (verdict.free_window) {
    clauses.push({
      key: "day-free",
      text: `Longest free window ${clock(verdict.free_window.start, zone)}-${clock(verdict.free_window.end, zone)}`,
    });
  }
  return { lead, tail: clauses };
}

function buildClauses({ freshnessDetail, conflicts }: Pick<CalendarVerdictOpenerProps, "freshnessDetail" | "conflicts">): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (freshnessDetail) {
    clauses.push({ key: "sync-freshness", text: `calendar sync ${freshnessDetail}` });
  }
  if (conflicts.length > 0) {
    clauses.push({
      key: "scheduling-conflicts",
      text: `${plural(conflicts.length, "scheduling conflict")} in view`,
    });
  }

  return clauses;
}

export function CalendarVerdictOpener({
  dayVerdict,
  workspaceLoading,
  workspaceError,
  sourceFreshnessLoading,
  sourceFreshnessError,
  freshnessDetail,
  conflictScanEnabled,
  conflictLoading,
  conflictError,
  conflictsAvailable,
  conflicts,
  invitations = [],
  invitationsLoading = false,
  invitationsRefreshing = false,
  invitationsError = false,
  invitationsAvailable = true,
  invitationConflictsAvailable = true,
  invitationSourcesDegraded = [],
  invitationsHasMore = false,
  invitationsLoadingMore = false,
  onMoreInvitations,
  onOpenInvitation,
  invitationDetailLoading = false,
}: CalendarVerdictOpenerProps) {
  const [selectedEvidence, setEvidence] = useState<{ entryId: string; index: number } | null>(null);
  const evidence = selectedEvidence
    ? invitations.find((entry) => entry.entry_id === selectedEvidence.entryId)?.conflict_issues[selectedEvidence.index] ?? null
    : null;
  const verdict = dayVerdict.data ?? null;
  // The countdown to the next meeting moves with the clock, not with a refetch.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const sources = [
    {
      label: "calendar day verdict",
      isLoading: dayVerdict.isLoading,
      isError: dayVerdict.isError || (verdict !== null && !verdict.available),
    },
    { label: "calendar workspace", isLoading: workspaceLoading, isError: workspaceError },
    { label: "calendar invitations", isLoading: invitationsLoading || invitationsRefreshing,
      isError: invitationsError || !invitationsAvailable },
    {
      label: "calendar source freshness",
      isLoading: sourceFreshnessLoading,
      isError: sourceFreshnessError,
    },
    ...(conflictScanEnabled
      ? [
          {
            label: "calendar conflict scan",
            isLoading: conflictLoading,
            isError: conflictError || !conflictsAvailable,
          },
        ]
      : []),
  ];

  const calmConflictText = conflictScanEnabled ? ", no scheduling conflicts in view" : "";
  const dayClauses = verdict?.available ? verdictClauses(verdict, nowMs) : { lead: [], tail: [] };
  // Urgency order: next commitment, what needs the owner, committed hours, free window.
  const invitationsText = `${plural(invitations.length, "unanswered invitation")}${invitationsHasMore ? " shown" : ""}`;
  const needsYou: VerdictClause[] =
    invitations.length > 0
      ? [
          {
            key: "unanswered-invitations",
            text: invitationsText,
            content: (
              <button
                type="button"
                className="underline decoration-border underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                onClick={() => {
                  const target = document.getElementById("unanswered-invitations");
                  target?.scrollIntoView?.({ block: "start" });
                  target?.focus();
                }}
              >
                {invitationsText}
              </button>
            ),
          },
        ]
      : [];
  const clauses = [
    ...dayClauses.lead,
    ...needsYou,
    ...dayClauses.tail,
    ...buildClauses({ freshnessDetail, conflicts }),
  ];
  // Only a readable, current, empty day is "nothing on"; a day that merely has no committed
  // meeting (tentative or butler holds only) says so instead of claiming it is empty.
  const calmDay = verdict?.available
    ? verdict.empty
      ? `Nothing on ${dayLabel(verdict)}`
      : `No committed meetings ${dayLabel(verdict)}`
    : "Calendar day unknown";
  const buttonClass = "text-left text-sm text-foreground underline decoration-border underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
  return (
    <div className="border-b border-border/60 pb-3">
    <DispatchVerdict
      testId="calendar"
      landmarkLabel="Calendar verdict"
      sources={sources}
      clauses={clauses}
      allClear={`${calmDay}${calmConflictText}`}
    />
    <section id="unanswered-invitations" tabIndex={-1} aria-label="Unanswered invitations" className="mt-2 space-y-2">
      {invitationsLoading ? <p role="status" className="text-sm text-muted-foreground">Loading invitations…</p> : null}
      {invitationsRefreshing && !invitationsLoading ? <p role="status" className="text-sm text-muted-foreground">Refreshing invitations…</p> : null}
      {invitationsError || !invitationsAvailable ? <SourceDegradedNote
        label="Calendar invitations" detail={invitationSourcesDegraded.length > 0
          ? invitationSourcesDegraded.join(", ") : "Invitation source unavailable"}
      /> : null}
      {!invitationsLoading && !invitationsRefreshing && !invitationsError && invitationsAvailable && invitations.length === 0
        ? <p className="text-sm text-muted-foreground">No unanswered invitations.</p> : null}
      {invitations.length > 0 ? <ul className="space-y-2">
        {invitations.map((invitation) => <li key={invitation.entry_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <button type="button" className={buttonClass} disabled={invitationDetailLoading}
            onClick={() => onOpenInvitation?.(invitation.entry_id)}>{invitation.title}</button>
          <span className="text-sm text-muted-foreground">{invitation.organizer
            ? `Organizer: ${invitation.organizer}${invitation.organizer_source === "attendee" ? " (attendee)" : ""}` : "Organizer unknown"}</span>
          {invitation.conflict_issues.map((issue, index) => <button type="button"
            className={buttonClass} key={`${issue.kind}:${issue.date}:${index}`}
            onClick={() => setEvidence({ entryId: invitation.entry_id, index })} aria-label={`Show ${issue.kind} conflict for ${invitation.title}`}>Conflict evidence</button>)}
          {!invitationConflictsAvailable ? <span className="text-sm text-muted-foreground">Conflict availability unknown</span> : null}
        </li>)}
      </ul> : null}
      {invitationDetailLoading ? <p role="status">Opening invitation…</p> : null}
      {invitationsHasMore ? <button type="button" className={buttonClass}
        onClick={onMoreInvitations} disabled={invitationsLoadingMore}>
        {invitationsLoadingMore ? "Loading more invitations…" : "More invitations"}</button> : null}
      {evidence ? <div role="region" aria-label="Invitation conflict evidence" className="rounded border border-border p-3 text-sm">
        <p>{evidence.summary}</p>
        <ul>{evidence.events.map((event) => <li key={event.entry_id}>
          <button type="button" className={buttonClass} onClick={() => onOpenInvitation?.(event.entry_id)}>{event.title}</button>
        </li>)}</ul>
        <button type="button" className={buttonClass} onClick={() => setEvidence(null)}>Close conflict evidence</button>
      </div> : null}
    </section>
    </div>
  );
}
