import { formatInTimeZone } from "date-fns-tz";

import type { CalendarChange } from "@/api/types";

function when(iso: string | null, zone: string): string {
  return iso === null ? "unknown" : formatInTimeZone(new Date(iso), zone, "EEE d MMM HH:mm");
}

/** "Moved from Mon 30 Nov 09:00 to Mon 30 Nov 11:00", or what is known when there is no prior. */
export function describeChange(change: CalendarChange, zone: string): string {
  const parts: string[] = [];
  if (!change.prior_known) {
    parts.push(`Changed; no earlier version recorded. Now ${when(change.new_start_at, zone)}`);
  }
  if (change.fields.includes("status")) {
    parts.push(
      change.new_status === "cancelled"
        ? "Cancelled"
        : `Status ${change.prior_status ?? "unknown"} to ${change.new_status ?? "unknown"}`,
    );
  }
  if (change.fields.includes("time")) {
    parts.push(
      `Moved from ${when(change.prior_start_at, zone)} to ${when(change.new_start_at, zone)}`,
    );
  }
  if (change.fields.includes("location")) {
    parts.push(
      `Location ${change.prior_location ?? "none"} to ${change.new_location ?? "none"}`,
    );
  }
  const who = change.organizer_self === null ? " (organizer unknown)" : "";
  return parts.join("; ") + who;
}
