import { expect, it } from "vitest";

import type { CalendarChange } from "@/api/types";
import { describeChange } from "@/pages/calendar/change-text";

const base: CalendarChange = {
  event_id: "e", calendar_id: "c", entry_id: null, title: null, observed_at: "2026-11-30T00:00:00Z",
  prior_known: true, all_day: false, fields: [], prior_start_at: "2026-11-30T09:00:00Z",
  new_start_at: "2026-11-30T11:00:00Z", prior_end_at: null, new_end_at: null,
  prior_status: "confirmed", new_status: "confirmed", prior_location: "Room 1", new_location: "Room 2",
  organizer_self: false,
};

it("says what changed in the display zone, and names a cancellation", () => {
  expect(describeChange({ ...base, fields: ["time"] }, "Asia/Singapore")).toBe(
    "Moved from Mon 30 Nov 17:00 to Mon 30 Nov 19:00",
  );
  expect(describeChange({ ...base, fields: ["status"], new_status: "cancelled" }, "UTC")).toBe(
    "Cancelled",
  );
  expect(describeChange({ ...base, fields: ["location"] }, "UTC")).toBe("Location Room 1 to Room 2");
});

it("marks an unknown organizer", () => {
  expect(describeChange({ ...base, fields: ["time"], organizer_self: null }, "UTC")).toContain(
    "(organizer unknown)",
  );
});
