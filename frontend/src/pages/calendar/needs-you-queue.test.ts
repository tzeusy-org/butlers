import { expect, it } from "vitest";

import type { CalendarChange, ConflictIssue, PendingDebrief, UnifiedCalendarEntry } from "@/api/types";
import { buildNeedsYouQueue, needsYouCountText } from "@/pages/calendar/needs-you-queue";

const complete = { complete: true } as const;
const none = { rows: [], ...complete };
const noChanges = { rows: [] as CalendarChange[], ...complete };

const overlap = (id: string, start: string): ConflictIssue => ({
  kind: "overlap",
  date: "2026-11-30",
  summary: `Overlap ${id}`,
  severity: "warning",
  events: [{ entry_id: id, title: id, start_at: start, end_at: start, timezone: "UTC", status: "confirmed" }],
  proposal_ids: [],
});
const change = (id: string, newStart: string): CalendarChange => ({
  event_id: id, calendar_id: "primary", entry_id: "entry-" + id, title: "Planning",
  observed_at: "2026-11-29T12:00:00Z", prior_known: true, all_day: false, fields: ["time"],
  prior_start_at: "2026-11-30T09:00:00Z", new_start_at: newStart,
  prior_end_at: "2026-11-30T10:00:00Z", new_end_at: "2026-11-30T12:00:00Z",
  prior_status: "confirmed", new_status: "confirmed", prior_location: null, new_location: null,
  organizer_self: false,
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
    changes: { rows: [change("ch", "2026-11-30T08:30:00Z")], ...complete },
  });
  expect(queue.items.map((item) => item.id)).toEqual([
    "conflict:c", expect.stringMatching(/^change:ch:/), "debrief:d1", "debrief:d2", "invitation:inv", "proposal:p",
  ]);
});

it("an unreadable kind is unknown and the count is a lower bound, never zero", () => {
  const queue = buildNeedsYouQueue({
    invitations: { rows: [], complete: false },
    conflicts: none,
    proposals: none,
    debriefs: none,
    changes: noChanges,
  });
  expect(queue.items).toEqual([]);
  expect(queue.unknownKinds).toContain("invitation");
  expect(needsYouCountText(queue)).toBe("0+ need you");
  const withOne = buildNeedsYouQueue({
    invitations: none,
    conflicts: none,
    proposals: { rows: [], complete: false },
    debriefs: { rows: [debrief("d", "2026-11-30T09:00:00Z", "2026-11-29T08:00:00Z")], ...complete },
    changes: noChanges,
  });
  expect(needsYouCountText(withOne)).toBe("1+ need you");
});

it("back-to-back is not a decision", () => {
  const queue = buildNeedsYouQueue({
    invitations: none,
    conflicts: { rows: [{ ...overlap("x", "2026-11-30T08:00:00Z"), kind: "back_to_back" }], ...complete },
    proposals: none,
    debriefs: none,
    changes: noChanges,
  });
  expect(queue.items).toEqual([]);
  expect(queue.unknownKinds).toEqual([]);
  expect(needsYouCountText(queue)).toBeNull();
});

it("a failed or truncated changes read is unknown, never calm", () => {
  const queue = buildNeedsYouQueue({
    invitations: none,
    conflicts: none,
    proposals: none,
    debriefs: none,
    changes: { rows: [], complete: false },
  });
  expect(queue.unknownKinds).toEqual(["change"]);
  expect(needsYouCountText(queue)).toBe("0+ need you");
});

it("a change is an item at its new start, carrying its before and after", () => {
  const queue = buildNeedsYouQueue({
    invitations: none,
    conflicts: none,
    proposals: none,
    debriefs: none,
    changes: { rows: [change("ch", "2026-11-30T11:00:00Z")], ...complete },
  });
  expect(queue.items).toHaveLength(1);
  expect(queue.items[0]).toMatchObject({ kind: "change", startsAt: "2026-11-30T11:00:00Z" });
  expect(queue.items[0].change?.prior_start_at).toBe("2026-11-30T09:00:00Z");
});
