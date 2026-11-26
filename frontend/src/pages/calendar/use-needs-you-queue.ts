/**
 * Composes the page's own reads into the Needs-you queue. It makes no request of its own.
 */

import { useMemo } from "react";

import type {
  CalendarChange,
  CalendarInvitationEntry,
  ConflictIssue,
  PendingDebrief,
  UnifiedCalendarEntry,
} from "@/api/types";
import { buildNeedsYouQueue } from "@/pages/calendar/needs-you-queue";

interface Read<T> {
  /** Not read in this view (not a failure). */
  notRead?: boolean;
  /** `undefined` while the read has not produced rows yet. */
  rows: readonly T[] | undefined;
  /** The read finished and its rows are the whole set. */
  complete: boolean;
}

export interface UseNeedsYouQueueArgs {
  invitations: Read<CalendarInvitationEntry>;
  conflicts: Read<ConflictIssue>;
  proposals: Read<UnifiedCalendarEntry>;
  debriefs: Read<PendingDebrief>;
  changes: Read<CalendarChange>;
}

const NONE: readonly never[] = [];

export function useNeedsYouQueue({ invitations, conflicts, proposals, debriefs, changes }: UseNeedsYouQueueArgs) {
  return useMemo(
    () =>
      buildNeedsYouQueue({
        invitations: { rows: invitations.rows ?? NONE, complete: invitations.complete && invitations.rows !== undefined },
        conflicts: {
          rows: conflicts.rows ?? NONE,
          complete: conflicts.complete && conflicts.rows !== undefined,
          notRead: conflicts.notRead,
        },
        proposals: { rows: proposals.rows ?? NONE, complete: proposals.complete && proposals.rows !== undefined },
        debriefs: { rows: debriefs.rows ?? NONE, complete: debriefs.complete && debriefs.rows !== undefined },
        changes: { rows: changes.rows ?? NONE, complete: changes.complete && changes.rows !== undefined },
      }),
    [
      invitations.rows, invitations.complete,
      conflicts.rows, conflicts.complete, conflicts.notRead,
      proposals.rows, proposals.complete,
      debriefs.rows, debriefs.complete,
      changes.rows, changes.complete,
    ],
  );
}
