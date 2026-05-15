// ---------------------------------------------------------------------------
// ChroniclesPage (bu-i29ix): editorial archetype landing.
//
// Voice column on the left (date eyebrow, briefing status pill, Display
// headline, Voice paragraph). Index column on the right (KPI strip, attention
// list, recent-days index). The Gantt / Map / Aggregations / Drawer surfaces
// that used to be the primary view live below the fold inside
// <ChroniclesDrilldownPanel> and lazy-mount when opened.
//
// All copy obeys the voice rules from
// about/heart-and-soul/design-language.md: sentence case, no em-dashes,
// and no exclamation marks.
// ---------------------------------------------------------------------------

import { useMemo } from "react";

import { useTimezone } from "@/components/ui/timezone-context";
import { useChroniclesBriefing } from "@/hooks/use-chronicles-briefing";
import { Page } from "@/components/ui/page";
import { Headline } from "@/components/overview/Headline";
import { Elaboration } from "@/components/overview/Elaboration";
import { KpiStrip } from "@/components/overview/KpiStrip";
import { AttentionList, type AttentionListItem } from "@/components/overview/AttentionList";
import { Section } from "@/components/overview/Section";
import { ChroniclesDrilldownPanel } from "@/components/chronicles/ChroniclesDrilldownPanel";
import { RecentDaysIndex } from "@/components/chronicles/RecentDaysIndex";
import type {
  ChroniclesAttentionItem,
  ChroniclesKpi,
  ChroniclesVoiceSource,
} from "@/api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Eyebrow date string for the chronicles briefing. */
function formatBriefingEyebrow(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  if (isNaN(d.getTime())) return `Chronicles · ${date}`;
  const weekday = d.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
  const day = d.getUTCDate();
  const month = d.toLocaleDateString("en-GB", { month: "long", timeZone: "UTC" });
  const year = d.getUTCFullYear();
  return `Chronicles · ${weekday}, ${day} ${month} ${year}`;
}

/** Two-line greeting derived from the briefing state class. */
function deriveHeadlineLines(stateClass: string, headline: string) {
  // The briefing already provides a single sentence-case headline.
  // For the Display tier two-line component we synthesise a short greet
  // line and use the briefing headline as the body. The greet is templated
  // and matches the doctrinal voice rules.
  switch (stateClass) {
    case "urgent":
      return { greet: "Yesterday left work.", body: headline };
    case "busy":
      return { greet: "Yesterday was full.", body: headline };
    case "mild":
      return { greet: "Yesterday went mostly to plan.", body: headline };
    default:
      return { greet: "Yesterday is settled.", body: headline };
  }
}

/** Format minutes as "Hh MMm" or "MMm". */
function fmtMinutes(total: number): string {
  if (total <= 0) return "0";
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h <= 0) return `${m}m`;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

function formatDateInTimeZone(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const lookup = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year}-${lookup.month}-${lookup.day}`;
}

function previousIsoCalendarDate(dateIso: string): string {
  const [year, month, day] = dateIso.split("-").map(Number);
  const previous = new Date(Date.UTC(year, month - 1, day - 1));
  return previous.toISOString().slice(0, 10);
}

function yesterdayInTimeZone(timeZone: string): string {
  try {
    return previousIsoCalendarDate(formatDateInTimeZone(new Date(), timeZone));
  } catch {
    return previousIsoCalendarDate(formatDateInTimeZone(new Date(), "UTC"));
  }
}

function buildKpiCells(kpi: ChroniclesKpi): React.ComponentProps<typeof KpiStrip>["cells"] {
  const top = kpi.hours_by_top_lanes[0];
  const topLabel = top ? `${top.lane} · ${top.hours.toFixed(1)}h` : "no lane data";
  return [
    {
      eyebrow: "Top lane",
      value: topLabel,
      delta: kpi.hours_by_top_lanes[1]
        ? `then ${kpi.hours_by_top_lanes[1].lane}`
        : "",
    },
    {
      eyebrow: "Sleep",
      value: fmtMinutes(kpi.sleep_minutes),
      delta: kpi.streaks.sleep > 0 ? `${kpi.streaks.sleep}-day streak` : "",
    },
    {
      eyebrow: "Longest episode",
      value: fmtMinutes(kpi.longest_episode_minutes),
      delta: kpi.longest_episode_title ?? "",
    },
    {
      eyebrow: "Longest gap",
      value: fmtMinutes(kpi.longest_gap_minutes),
      delta: kpi.longest_gap_minutes >= 6 * 60 ? "above 6h waking" : "",
    },
  ];
}

/**
 * Adapt ``ChroniclesAttentionItem[]`` to the row shape the shared
 * ``AttentionList`` primitive consumes.
 */
function adaptAttention(items: ChroniclesAttentionItem[]): AttentionListItem[] {
  return items.map((it) => ({
    id: `chronicles:${it.kind}:${it.title}`,
    severity: it.severity,
    title: it.title,
    detail: it.detail,
    href: it.action_href,
  }));
}

/** Pill descriptor for the briefing status above the headline. */
function pillDescriptor(
  source: ChroniclesVoiceSource | undefined,
  isFetching: boolean,
): { label: string; tone: "amber" | "green" | "dim" } {
  if (isFetching) return { label: "composing…", tone: "amber" };
  if (source === "llm·cached") return { label: "llm · cached", tone: "green" };
  if (source === "stale") return { label: "stale cache", tone: "amber" };
  return { label: "templated", tone: "dim" };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  const ownerTz = useTimezone();
  // Brief default: yesterday in owner-tz. The owner can later add a date
  // picker; for v1 we always show the most recent settled day.
  const targetDate = useMemo(() => yesterdayInTimeZone(ownerTz), [ownerTz]);

  const { data, isFetching, isError, refetch } = useChroniclesBriefing({
    date: targetDate,
    tz: ownerTz,
  });

  const eyebrowLabel = formatBriefingEyebrow(data?.date ?? targetDate);
  const headlineLines = deriveHeadlineLines(
    data?.state_class ?? "quiet",
    data?.headline ?? "Quiet day.",
  );
  const pill = pillDescriptor(data?.voice_source, isFetching);

  return (
    <Page
      archetype="editorial"
      title="Chronicles"
      description="Retrospective view of lived past time reconstructed from butler evidence."
      loading={!data && !isError}
      error={isError ? new Error("Failed to load chronicles briefing.") : null}
      onRetry={() => void refetch()}
    >
      <div
        className="grid max-w-[1280px] gap-10 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:gap-14"
      >
        {/* Left column: Voice surface */}
        <div className="space-y-6">
          <div className="flex items-baseline gap-3">
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
              {eyebrowLabel}
            </p>
            <button
              type="button"
              onClick={() => void refetch()}
              className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "9px",
                letterSpacing: "0.08em",
                color: "var(--muted-foreground)",
                borderColor: "var(--border)",
                background: "transparent",
              }}
              aria-label={`Briefing source: ${pill.label}`}
            >
              <span
                aria-hidden
                className="inline-block h-[6px] w-[6px] rounded-full"
                style={{
                  backgroundColor:
                    pill.tone === "amber"
                      ? "var(--severity-medium)"
                      : pill.tone === "green"
                      ? "var(--severity-low)"
                      : "var(--muted-foreground)",
                }}
              />
              <span>{pill.label}</span>
            </button>
          </div>

          <Headline greet={headlineLines.greet} body={headlineLines.body} />

          <Elaboration
            text={data?.voice_paragraph ?? "The day is still being composed."}
            isFetching={isFetching}
          />
        </div>

        {/* Right column: KPI strip, attention, and recent-days index */}
        <div className="space-y-8">
          {data?.kpi ? <KpiStrip cells={buildKpiCells(data.kpi)} /> : null}
          <Section eyebrow="Attention">
            <AttentionList items={adaptAttention(data?.attention_items ?? [])} />
          </Section>
          <RecentDaysIndex days={data?.recent_days ?? []} />
        </div>
      </div>

      <ChroniclesDrilldownPanel />
    </Page>
  );
}
