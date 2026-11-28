import { expect, it } from "vitest";

import type { ConflictIssue, PendingDebrief, UnifiedCalendarEntry } from "@/api/types";
import { buildNeedsYouQueue, needsYouCountText } from "@/pages/calendar/needs-you-queue";

const complete = { complete: true } as const;
const none = { rows: [], ...complete };

const overlap = (id: string, start: string): ConflictIssue => ({
  kind: "overlap",
  date: "2026-11-30",
  summary: `Overlap ${id}`,
  severity: "warning",
  events: [{ entry_id: id, title: id, start_at: start, end_at: start, timezone: "UTC", status: "confirmed" }],
});
const debrief = (id: string, start: string, raised: string): PendingDebrief => ({
  debrief_id: id,
  number: 1,
  event_title: `Debrief ${id}`,
  occurrence_start: start,
  prompted_at: raised,
  attendees: [],
});

it("orders by start, then oldest raised, then id, and is stable", () => {
  const queue = buildNeedsYouQueue({
    invitations: {
      rows: [
        { entry_id: "inv", title: "Inv", start_at: "2026-11-30T09:00:00Z", end_at: "", timezone: "UTC",
          organizer: null, organizer_source: "unknown", butler_name: null, conflict_issues: [] },
      ],
      ...complete,
    },
    conflicts: { rows: [overlap("c", "2026-11-30T08:00:00Z")], ...complete },
    proposals: {
      rows: [{ entry_id: "p", title: "Prop", start_at: "2026-11-30T09:00:00Z" } as UnifiedCalendarEntry],
      ...complete,
    },
    debriefs: {
      rows: [debrief("d2", "2026-11-30T09:00:00Z", "2026-11-29T10:00:00Z"),
        debrief("d1", "2026-11-30T09:00:00Z", "2026-11-29T08:00:00Z")],
      ...complete,
    },
  });
  expect(queue.items.map((item) => item.id)).toEqual([
    "conflict:c", "debrief:d1", "debrief:d2", "invitation:inv", "proposal:p",
  ]);
});

it("an unreadable kind is unknown and the count is a lower bound, never zero", () => {
  const queue = buildNeedsYouQueue({
    invitations: { rows: [], complete: false },
    conflicts: none,
    proposals: none,
    debriefs: none,
  });
  expect(queue.items).toEqual([]);
  expect(queue.unknownKinds).toContain("invitation");
  expect(needsYouCountText(queue)).toBe("0+ need you");
  const withOne = buildNeedsYouQueue({
    invitations: none,
    conflicts: none,
    proposals: { rows: [], complete: false },
    debriefs: { rows: [debrief("d", "2026-11-30T09:00:00Z", "2026-11-29T08:00:00Z")], ...complete },
  });
  expect(needsYouCountText(withOne)).toBe("1+ need you");
});

it("the changes feed is not a counted kind, and back-to-back is not a decision", () => {
  const queue = buildNeedsYouQueue({
    invitations: none,
    conflicts: { rows: [{ ...overlap("x", "2026-11-30T08:00:00Z"), kind: "back_to_back" }], ...complete },
    proposals: none,
    debriefs: none,
  });
  expect(queue.items).toEqual([]);
  expect(queue.unknownKinds).toEqual([]);
  expect(needsYouCountText(queue)).toBeNull();
});
