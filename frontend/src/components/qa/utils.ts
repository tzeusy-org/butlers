import type { QaCaseSummary } from "@/api/types";
import { formatInTimeZone } from "date-fns-tz";

export const qaSeverityClassName: Record<QaCaseSummary["sev"], string> = {
  high: "bg-destructive",
  // bu-86c4c.6: raw Tailwind amber-500 -> the Dispatch --amber state token.
  medium: "bg-[var(--amber)]",
  low: "bg-muted-foreground",
};

/**
 * Format a QA "detected" timestamp for inline use in case rows and headers.
 *
 * - If the timestamp falls on the viewer's local "today", render the time only:
 *     "2:19 pm"
 * - Otherwise render an ISO-style date plus the time:
 *     "2026-05-09 2:19 pm"
 *
 * Lower-case am/pm is used everywhere for consistency with the dossier's
 * mono/uppercase typographic palette. Timestamps render in the viewer's
 * local timezone -- matching the page-level `Time` component.
 */
export function formatQaDetectedTime(ts: string, timezone = "Asia/Singapore"): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  const day = formatInTimeZone(date, timezone, "yyyy-MM-dd");
  const today = formatInTimeZone(new Date(), timezone, "yyyy-MM-dd");
  const time = formatInTimeZone(date, timezone, "h:mm a").toLowerCase();
  return day === today ? time : `${day} ${time}`;
}
