import type { QaCaseSummary } from "@/api/types";

export const qaSeverityClassName: Record<QaCaseSummary["sev"], string> = {
  high: "bg-destructive",
  medium: "bg-amber-500",
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
export function formatQaDetectedTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;

  const now = new Date();
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  const time = date
    .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true })
    .toLowerCase();

  if (isToday) return time;

  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day} ${time}`;
}
