/**
 * DateEyebrow -- uppercase mono date/time row with a BriefingStatus slot.
 *
 * Format: "Overview · Wed, 7 May 2026 · 14:21"
 * Font: --font-mono, 10px, 0.14em letter-spacing, muted color.
 *
 * The slot is a ReactNode so the parent (DashboardPage) can pass
 * <BriefingStatus /> without DateEyebrow depending on briefing data.
 *
 * Topology: about/lay-and-land/frontend.md §Editorial archetype layout
 */

import { formatInTimeZone } from "date-fns-tz";

import { useTimezone } from "@/components/ui/timezone-context";

interface DateEyebrowProps {
  /** Slot for BriefingStatus pill. */
  statusSlot?: React.ReactNode;
}

function formatEyebrowDate(now: Date, timezone: string): string {
  const date = formatInTimeZone(now, timezone, "EEE, d MMMM yyyy");
  const time = formatInTimeZone(now, timezone, "HH:mm");
  return `Overview · ${date} · ${time}`;
}

export function DateEyebrow({ statusSlot }: DateEyebrowProps) {
  const timezone = useTimezone();
  const label = formatEyebrowDate(new Date(), timezone);

  return (
    <div className="flex items-center gap-3">
      <p
        className="tnum uppercase"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "10px",
          letterSpacing: "0.14em",
          lineHeight: 1,
          color: "var(--muted-foreground)",
        }}
      >
        {label}
      </p>
      {statusSlot}
    </div>
  );
}
