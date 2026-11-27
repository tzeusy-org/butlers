/**
 * The calendar Needs-you queue: one ordered list over the reads the page already makes
 * (invitations, overlap issues, proposals, pending debriefs, organizer-made changes), and which kinds are unknown.
 * Pure: no query, no endpoint, no stored state.
 */

import type {
  CalendarChange,
  CalendarInvitationEntry,
  ConflictIssue,
  PendingDebrief,
  UnifiedCalendarEntry,
} from "@/api/types";

export type NeedsYouKind = "invitation" | "conflict" | "proposal" | "debrief" | "change";

export const KIND_LABEL: Record<NeedsYouKind, string> = {
  invitation: "invitations",
  conflict: "overlaps",
  proposal: "proposals",
  debrief: "debriefs",
  change: "changes",
};

export interface NeedsYouItem {
  /** `kind:source id`: stable across refetches so a selection survives them. */
  id: string;
  kind: NeedsYouKind;
  title: string;
  /** When the thing starts (ISO); `null` when it has no start. */
  startsAt: string | null;
  /** When it was raised (ISO); `null` when unknown, which sorts last among equal starts. */
  raisedAt: string | null;
  /** The calendar entry the owner opens as evidence, when there is one. */
  entryId: string | null;
  /** The change itself, for a `change` item: its before and after. */
  change: CalendarChange | null;
}

/** One kind's read: its rows and whether they are the complete set. */
export interface NeedsYouSource<T> {
  rows: readonly T[];
  complete: boolean;
  /** The kind is not read in this view at all (not a failed read): no "+", a fixed line instead. */
  notRead?: boolean;
}

export interface NeedsYouInputs {
  invitations: NeedsYouSource<CalendarInvitationEntry>;
  conflicts: NeedsYouSource<ConflictIssue>;
  proposals: NeedsYouSource<UnifiedCalendarEntry>;
  debriefs: NeedsYouSource<PendingDebrief>;
  changes: NeedsYouSource<CalendarChange>;
}

export interface NeedsYouQueue {
  items: NeedsYouItem[];
  /** Counted kinds whose read failed or is partial. */
  unknownKinds: NeedsYouKind[];
  /** Overlaps are only checked in the day and week views; the panel says so. */
  overlapsNotRead: boolean;
}

function ms(iso: string | null): number {
  if (iso === null) return Number.NaN;
  return new Date(iso).getTime();
}

/** Soonest start first, items with no start last; then the oldest raised; then id. Total. */
export function compareNeedsYou(a: NeedsYouItem, b: NeedsYouItem): number {
  const aStart = ms(a.startsAt);
  const bStart = ms(b.startsAt);
  const aHas = !Number.isNaN(aStart);
  const bHas = !Number.isNaN(bStart);
  if (aHas !== bHas) return aHas ? -1 : 1;
  if (aHas && aStart !== bStart) return aStart - bStart;
  const aRaised = ms(a.raisedAt);
  const bRaised = ms(b.raisedAt);
  const aRaisedKnown = !Number.isNaN(aRaised);
  const bRaisedKnown = !Number.isNaN(bRaised);
  if (aRaisedKnown !== bRaisedKnown) return aRaisedKnown ? -1 : 1;
  if (aRaisedKnown && aRaised !== bRaised) return aRaised - bRaised;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** The id part of a conflict item: the sorted entry ids of its events. */
export function overlapKey(issue: ConflictIssue): string {
  return issue.events
    .map((event) => event.entry_id)
    .sort()
    .join("+");
}

export function buildNeedsYouQueue(inputs: NeedsYouInputs): NeedsYouQueue {
  const items: NeedsYouItem[] = [];
  for (const entry of inputs.invitations.rows) {
    items.push({
      id: `invitation:${entry.entry_id}`,
      kind: "invitation",
      title: entry.title,
      startsAt: entry.start_at,
      raisedAt: null,
      entryId: entry.entry_id,
      change: null,
    });
  }
  for (const issue of inputs.conflicts.rows) {
    // Only an overlap needs a decision; back-to-back and a heavy day are context.
    if (issue.kind !== "overlap" || issue.events.length === 0) continue;
    const first = [...issue.events].sort((a, b) => a.start_at.localeCompare(b.start_at))[0];
    items.push({
      id: `conflict:${overlapKey(issue)}`,
      kind: "conflict",
      title: issue.summary,
      startsAt: first.start_at,
      raisedAt: null,
      entryId: first.entry_id,
      change: null,
    });
  }
  for (const entry of inputs.proposals.rows) {
    items.push({
      id: `proposal:${entry.entry_id}`,
      kind: "proposal",
      title: entry.title,
      startsAt: entry.start_at,
      raisedAt: null,
      entryId: null,
      change: null,
    });
  }
  for (const debrief of inputs.debriefs.rows) {
    items.push({
      id: `debrief:${debrief.debrief_id}`,
      kind: "debrief",
      title: debrief.event_title ?? "Meeting debrief",
      startsAt: debrief.occurrence_start,
      raisedAt: debrief.prompted_at,
      entryId: null,
      change: null,
    });
  }
  for (const change of inputs.changes.rows) {
    items.push({
      id: `change:${change.event_id}:${change.observed_at}`,
      kind: "change",
      title: change.title ?? "An event",
      startsAt: change.new_start_at ?? change.prior_start_at,
      raisedAt: change.observed_at,
      entryId: change.entry_id,
      change,
    });
  }
  items.sort(compareNeedsYou);

  const unknownKinds: NeedsYouKind[] = [];
  if (!inputs.invitations.complete) unknownKinds.push("invitation");
  if (!inputs.conflicts.complete && inputs.conflicts.notRead !== true) {
    unknownKinds.push("conflict");
  }
  if (!inputs.proposals.complete) unknownKinds.push("proposal");
  if (!inputs.debriefs.complete) unknownKinds.push("debrief");
  if (!inputs.changes.complete) unknownKinds.push("change");
  return { items, unknownKinds, overlapsNotRead: inputs.conflicts.notRead === true };
}

/** "3 need you", "3+ need you" or "0+ need you" while a kind is unknown; `null` for a complete, empty queue. */
export function needsYouCountText(queue: NeedsYouQueue): string | null {
  const count = queue.items.length;
  const partial = queue.unknownKinds.length > 0;
  if (count === 0 && !partial) return null;
  return `${count}${partial ? "+" : ""} need you`;
}
