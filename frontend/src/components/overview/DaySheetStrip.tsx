/**
 * Day sheet strip: the owner's day on the Overview (bu-5ii4gj.2 S4).
 *
 * Renders the deterministic day sheet (`GET /api/calendar/workspace/day-sheet`), the same
 * projection the nightly Telegram message uses. It never says a day is clear when the read cannot
 * know: an error, an `unreadable` sheet and a `stale` sheet each render their own line, and
 * "Nothing on your calendar" appears only for a `current` sheet with no items. Third-party titles
 * are rendered as text (React escapes them), never as markup.
 */

import { formatInTimeZone } from "date-fns-tz";
import { Link } from "react-router";

import type { DaySheetGap, DaySheetItem, DaySheetResponse } from "@/api/types.ts";

function clock(iso: string, zone: string): string {
  return formatInTimeZone(new Date(iso), zone, "HH:mm");
}

/** "HH:mm", or "EEE d MMM HH:mm" when the instant is not on the sheet's own date. */
function stamp(iso: string, zone: string, sheetDate: string): string {
  const date = new Date(iso);
  return formatInTimeZone(date, zone, "yyyy-MM-dd") === sheetDate
    ? formatInTimeZone(date, zone, "HH:mm")
    : formatInTimeZone(date, zone, "EEE d MMM HH:mm");
}

function duration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return [hours ? `${hours}h` : "", rest ? `${rest}m` : ""].filter(Boolean).join(" ");
}

function itemWhen(item: DaySheetItem, zone: string): string {
  return item.all_day ? "All day" : `${clock(item.start, zone)}-${clock(item.end, zone)}`;
}

function gapText(gap: DaySheetGap, zone: string): string {
  return `${clock(gap.start, zone)}-${clock(gap.end, zone)}`;
}

export interface DaySheetStripProps {
  sheet?: DaySheetResponse | null;
  isLoading?: boolean;
  isError?: boolean;
  error?: Error | null;
}

export function DaySheetStrip({
  sheet = null,
  isLoading = false,
  isError = false,
  error = null,
}: DaySheetStripProps) {
  let body: React.ReactNode;
  if (isError) {
    body = (
      <p role="alert" data-testid="day-sheet-error" className="text-[var(--red-text)]">
        Couldn&apos;t load your day. {error?.message ?? "Unknown error"}
      </p>
    );
  } else if (isLoading || !sheet) {
    body = <p className="text-[var(--dim)]">Loading…</p>;
  } else {
    const { freshness, items, zone } = sheet;
    const unreadable = freshness.state === "unreadable";
    body = (
      <>
        {unreadable ? (
          <p role="alert" data-testid="day-sheet-unreadable" className="text-[var(--red-text)]">
            Calendar unreadable
            {freshness.sources_total > 0
              ? ` (${freshness.sources_unreadable.length} of ${freshness.sources_total} sources)`
              : ": no calendar source is known"}
            . Nothing is listed.
          </p>
        ) : null}
        {freshness.state === "stale" ? (
          <p data-testid="day-sheet-stale" className="text-[var(--amber-text)]">
            {freshness.as_of ? `As of ${stamp(freshness.as_of, zone, sheet.date)}: ` : ""}
            the calendar may be out of date.
          </p>
        ) : null}
        {!unreadable && items.length === 0 ? (
          <p data-testid="day-sheet-empty" className="text-[var(--mfg)]">
            {freshness.state === "current"
              ? "Nothing on your calendar."
              : "No events found, but this read is not current."}
          </p>
        ) : null}
        {!unreadable && items.length > 0 ? (
          <ul className="flex flex-col gap-0.5">
            {items.map((item) => (
              <li key={item.ref} data-day-sheet-item={item.ref} className="flex gap-2">
                <span className="w-24 shrink-0 tabular-nums text-[var(--mfg)]">
                  {itemWhen(item, zone)}
                </span>
                <Link
                  to={item.door}
                  title={item.title}
                  className="truncate text-fg hover:underline"
                >
                  {item.kind === "tentative" ? (
                    <>
                      <span aria-hidden="true">? </span>
                      <span className="sr-only">(tentative) </span>
                    </>
                  ) : null}
                  {item.title}
                  {item.kind === "butler" ? " (butler hold)" : ""}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
        {freshness.state === "current" && sheet.attended_minutes > 0 ? (
          <p data-testid="day-sheet-load" className="text-[var(--mfg)]">
            Committed {duration(sheet.attended_minutes)} across {sheet.attended_count}{" "}
            {sheet.attended_count === 1 ? "meeting" : "meetings"}.
          </p>
        ) : null}
        {freshness.state === "current" && sheet.longest_gap ? (
          <p data-testid="day-sheet-gap" className="text-[var(--mfg)]">
            Longest free: {gapText(sheet.longest_gap, zone)}.
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section
      data-testid="day-sheet-strip"
      aria-label="Your day"
      className="flex flex-col gap-1 font-mono text-[12px]"
    >
      {body}
    </section>
  );
}
