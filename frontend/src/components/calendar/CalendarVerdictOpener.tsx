// ---------------------------------------------------------------------------
// CalendarVerdictOpener — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
// ---------------------------------------------------------------------------

import { useState } from "react";
import type { CalendarInvitationEntry, ConflictIssue } from "@/api/types";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { SourceDegradedNote } from "@/components/ui/query-boundary";

export interface CalendarVerdictOpenerProps {
  entriesCount: number;
  sourceCount: number;
  rangeLabel: string;
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
  entriesCount,
  sourceCount,
  rangeLabel,
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
  const sources = [
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

  const calmConflictText = conflictScanEnabled ? ", no scheduling conflicts" : "";
  const clauses = buildClauses({ freshnessDetail, conflicts });
  if (invitations.length > 0) clauses.push({
    key: "unanswered-invitations", text: `${plural(invitations.length, "unanswered invitation")}${invitationsHasMore ? " shown" : ""}`,
  });
  const buttonClass = "text-left text-sm text-foreground underline decoration-border underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";
  return (
    <div className="border-b border-border/60 pb-3">
    <DispatchVerdict
      testId="calendar"
      landmarkLabel="Calendar verdict"
      sources={sources}
      clauses={clauses}
      allClear={`Quiet ${rangeLabel}: ${plural(entriesCount, "event")} across ${plural(sourceCount, "source")}${calmConflictText}`}
    />
    <section aria-label="Unanswered invitations" className="mt-2 space-y-2">
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
