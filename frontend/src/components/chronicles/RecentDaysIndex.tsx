/**
 * RecentDaysIndex -- eyebrow-titled list of recent calendar days.
 *
 * Renders one row per day with: date label, total minutes (tabular),
 * episode count, and (optionally) the day's top lane label. No card
 * chrome; hairline border-bottom on each row except the last. The
 * index is read-only; clicking a row is not wired in v1.
 *
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 *   §Attention list (row anatomy reused: rule-separated rows, no card chrome).
 */

import { Section } from "@/components/overview/Section";
import type { ChroniclesRecentDay } from "@/api/types";

interface RecentDaysIndexProps {
  days: ChroniclesRecentDay[];
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

export function RecentDaysIndex({ days }: RecentDaysIndexProps) {
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
        {days.map((d, i) => (
          <li
            key={d.date}
            className="grid items-baseline"
            style={{
              gridTemplateColumns: "auto 1fr auto",
              columnGap: "16px",
              paddingTop: i === 0 ? 0 : "10px",
              paddingBottom: "10px",
              borderBottom:
                i === days.length - 1 ? undefined : "1px solid var(--border)",
            }}
          >
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
              }}
            >
              {formatDayLabel(d.date)}
            </span>
            <span
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "14px",
                color: "var(--foreground)",
              }}
            >
              {d.top_lane ?? "no top lane"}
            </span>
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
              }}
            >
              {formatMinutes(d.total_minutes)} · {d.episode_count}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
