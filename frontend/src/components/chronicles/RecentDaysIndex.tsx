/**
 * RecentDaysIndex -- eyebrow-titled list of recent calendar days.
 *
 * Renders one row per day with: date label, the day's top lane, total minutes
 * (tabular), and episode count. No card chrome; hairline border-bottom on each
 * row except the last.
 *
 * When ``onSelect`` is provided each row is a button that re-anchors the
 * archive to that day (bu archive nav); the ``selectedDate`` row is marked
 * with ``aria-current``. Without ``onSelect`` the rows are static text.
 *
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 *   §Attention list (row anatomy reused: rule-separated rows, no card chrome).
 */

import { Section } from "@/components/overview/Section";
import type { ChroniclesRecentDay } from "@/api/types";

interface RecentDaysIndexProps {
  days: ChroniclesRecentDay[];
  /** When set, rows become buttons that select that day. */
  onSelect?: (date: string) => void;
  /** The currently-viewed day; its row is marked aria-current. */
  selectedDate?: string;
}

function formatDayLabel(iso: string): string {
  // Render as "Wed 7 May" in the owner's locale-neutral form.
  const d = new Date(`${iso}T00:00:00Z`);
  if (isNaN(d.getTime())) return iso;
  const weekday = d.toLocaleDateString("en-GB", {
    weekday: "short",
    timeZone: "UTC",
  });
  const day = d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  return `${weekday} ${day}`;
}

function formatMinutes(total: number): string {
  if (total <= 0) return "0m";
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h <= 0) return `${m}m`;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

const ROW_GRID = "auto 1fr auto";

function DayRowBody({ day, active }: { day: ChroniclesRecentDay; active: boolean }) {
  return (
    <>
      <span
        className="tnum"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
          color: active ? "var(--foreground)" : "var(--muted-foreground)",
        }}
      >
        {formatDayLabel(day.date)}
      </span>
      <span
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: "14px",
          fontWeight: active ? 500 : 400,
          color: "var(--foreground)",
        }}
      >
        {day.top_lane ?? "no top lane"}
      </span>
      <span
        className="tnum"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
          color: "var(--muted-foreground)",
        }}
      >
        {formatMinutes(day.total_minutes)} · {day.episode_count}
      </span>
    </>
  );
}

export function RecentDaysIndex({ days, onSelect, selectedDate }: RecentDaysIndexProps) {
  if (days.length === 0) {
    return (
      <Section eyebrow="Recent days">
        <p
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "16px",
            fontStyle: "italic",
            color: "var(--muted-foreground)",
            lineHeight: 1.6,
          }}
        >
          No prior days projected yet.
        </p>
      </Section>
    );
  }
  return (
    <Section eyebrow="Recent days">
      <ul role="list" className="m-0 list-none p-0">
        {days.map((d, i) => {
          const active = d.date === selectedDate;
          const border = i === days.length - 1 ? undefined : "1px solid var(--border)";
          if (onSelect) {
            return (
              <li key={d.date} style={{ borderBottom: border }}>
                <button
                  type="button"
                  onClick={() => onSelect(d.date)}
                  aria-current={active ? "true" : undefined}
                  aria-label={`View ${formatDayLabel(d.date)}`}
                  className="grid w-full cursor-pointer items-baseline rounded-sm text-left transition-colors hover:bg-[var(--accent)]"
                  style={{
                    gridTemplateColumns: ROW_GRID,
                    columnGap: "16px",
                    border: 0,
                    background: "transparent",
                    paddingTop: "10px",
                    paddingBottom: "10px",
                    paddingInline: "4px",
                    marginInline: "-4px",
                  }}
                >
                  <DayRowBody day={d} active={active} />
                </button>
              </li>
            );
          }
          return (
            <li
              key={d.date}
              className="grid items-baseline"
              style={{
                gridTemplateColumns: ROW_GRID,
                columnGap: "16px",
                paddingTop: i === 0 ? 0 : "10px",
                paddingBottom: "10px",
                borderBottom: border,
              }}
            >
              <DayRowBody day={d} active={active} />
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
