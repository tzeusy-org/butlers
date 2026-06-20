// ---------------------------------------------------------------------------
// ChroniclesPage: editorial archetype, date-navigable retrospective archive.
//
// Voice column on the left (date eyebrow with a prev/next day stepper, Display
// headline, Voice paragraph). Index rail on the right (attention list, KPI
// strip, navigable recent-days index). The Gantt / Map / Aggregations / Drawer
// surfaces live below the fold inside <ChroniclesDrilldownPanel>, driven by the
// same selected day.
//
// The selected day is URL state (?date=YYYY-MM-DD), defaulting to the most
// recent settled day (yesterday in owner tz) and clamped to
// [earliest_date, yesterday]. Navigation reuses the existing cached/templated
// briefing; it never initiates an LLM call.
//
// All copy obeys the voice rules from
// about/heart-and-soul/design-language.md: sentence case, no em-dashes,
// and no exclamation marks.
// ---------------------------------------------------------------------------

import { useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useSearchParams } from "react-router";

import { useTimezone } from "@/components/ui/timezone-context";
import { useChroniclesBriefing } from "@/hooks/use-chronicles-briefing";
import { Page } from "@/components/ui/page";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import { Headline } from "@/components/overview/Headline";
import { Elaboration } from "@/components/overview/Elaboration";
import { KpiStrip } from "@/components/overview/KpiStrip";
import { AttentionList, type AttentionListItem } from "@/components/overview/AttentionList";
import { Section } from "@/components/overview/Section";
import { ChroniclesDrilldownPanel } from "@/components/chronicles/ChroniclesDrilldownPanel";
import { RecentDaysIndex } from "@/components/chronicles/RecentDaysIndex";
import {
  clampIsoDay,
  greetSubject,
  isAtEarliest,
  isAtLatest,
  isValidIsoDay,
  nextIsoDay,
  prevIsoDay,
} from "@/pages/chronicles-date-nav";
import type { ChroniclesAttentionItem, ChroniclesKpi } from "@/api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** State-class predicate for the greeting line. Past tense, sentence case. */
const STATE_PREDICATE: Record<string, string> = {
  urgent: "had loose ends.",
  busy: "was full.",
  mild: "went mostly to plan.",
  quiet: "was quiet.",
};

/** Two-line greeting: a date-relative subject plus the briefing headline. */
function deriveHeadlineLines(stateClass: string, headline: string, subject: string) {
  const predicate = STATE_PREDICATE[stateClass] ?? STATE_PREDICATE.quiet;
  return { greet: `${subject} ${predicate}`, body: headline };
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

/** The most recent settled day: yesterday in the owner timezone. */
function yesterdayInTimeZone(timeZone: string): string {
  try {
    return previousIsoCalendarDate(formatDateInTimeZone(new Date(), timeZone));
  } catch {
    return previousIsoCalendarDate(formatDateInTimeZone(new Date(), "UTC"));
  }
}

function buildKpiCells(kpi: ChroniclesKpi): React.ComponentProps<typeof KpiStrip>["cells"] {
  const top = kpi.hours_by_top_lanes[0];
  const second = kpi.hours_by_top_lanes[1];
  return [
    {
      // The mega-number slot holds a number (hours); the lane name is the delta.
      eyebrow: "Top lane",
      value: top ? `${top.hours.toFixed(1)}h` : "—",
      delta: top ? (second ? `${top.lane}, then ${second.lane}` : top.lane) : "no lane data",
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

const EYEBROW_STYLE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "10px",
  letterSpacing: "0.14em",
  lineHeight: 1,
  color: "var(--muted-foreground)",
  textTransform: "uppercase",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  const ownerTz = useTimezone();
  const [searchParams, setSearchParams] = useSearchParams();

  // The most recent settled day is the default and the forward bound: today is
  // incomplete and is not shown.
  const latest = yesterdayInTimeZone(ownerTz);
  // Ignore malformed ?date= values so every downstream date is a real day.
  const dateParam = searchParams.get("date");
  const requestedDate = isValidIsoDay(dateParam) ? dateParam : latest;

  // Forward-clamp immediately; the backward (earliest) bound needs earliest_date
  // from the response, so it is applied after the first fetch.
  const fetchDate = clampIsoDay(requestedDate, undefined, latest);

  const { data, isFetching, isError, refetch } = useChroniclesBriefing({
    date: fetchDate,
    tz: ownerTz,
  });

  // earliest_date arrives with every briefing (it is a global minimum,
  // independent of the requested day), so it bounds backward navigation after
  // the first fetch.
  const earliest = data?.earliest_date ?? null;
  const selectedDate = clampIsoDay(requestedDate, earliest, latest);

  function selectDate(date: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("date", date);
        return next;
      },
      { replace: true },
    );
  }

  // Canonicalize the URL when the requested day is out of range (a future or
  // pre-data deep link), so the eyebrow, the briefing data, and the URL agree.
  useEffect(() => {
    if (selectedDate !== requestedDate) {
      selectDate(selectedDate);
    }
    // selectDate is stable for our purposes; depend on the resolved values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate, requestedDate]);

  const atEarliest = isAtEarliest(selectedDate, earliest);
  const atLatest = isAtLatest(selectedDate, latest);

  const subject = greetSubject(selectedDate, latest);
  const headlineLines = deriveHeadlineLines(
    data?.state_class ?? "quiet",
    data?.headline ?? "Quiet day.",
    subject,
  );
  const isStale = data?.voice_source === "stale";

  return (
    <Page
      archetype="editorial"
      title="Chronicles"
      description="Retrospective view of lived past time reconstructed from butler evidence."
      loading={!data && !isError}
      error={isError ? new Error("Failed to load chronicles briefing.") : null}
      onRetry={() => void refetch()}
    >
      <div className="grid max-w-[1280px] gap-10 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:gap-14">
        {/* Left column: Voice surface */}
        <div className="space-y-6">
          {/* Date eyebrow with a prev/next day stepper */}
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => selectDate(prevIsoDay(selectedDate))}
              disabled={atEarliest}
              aria-label="Previous day"
            >
              <ChevronLeft aria-hidden />
            </Button>
            <span
              className="tnum"
              style={EYEBROW_STYLE}
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <Time value={selectedDate} mode="absolute" precision="short-date" showTitle={false} />
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => selectDate(nextIsoDay(selectedDate))}
              disabled={atLatest}
              aria-label="Next day"
            >
              <ChevronRight aria-hidden />
            </Button>
            {isStale ? (
              <span
                style={{ ...EYEBROW_STYLE, fontSize: "9px", letterSpacing: "0.08em" }}
                title="The day-close summary may be out of date."
                aria-label="Day-close summary may be out of date"
              >
                stale
              </span>
            ) : null}
          </div>

          <Headline greet={headlineLines.greet} body={headlineLines.body} />

          <Elaboration
            text={data?.voice_paragraph ?? "The day is still being composed."}
            isFetching={isFetching}
          />
        </div>

        {/* Right column: attention leads, then KPI strip, then recent days */}
        <div className="space-y-8">
          <Section eyebrow="Attention">
            <AttentionList items={adaptAttention(data?.attention_items ?? [])} />
          </Section>
          {data?.kpi ? <KpiStrip cells={buildKpiCells(data.kpi)} /> : null}
          <RecentDaysIndex
            days={data?.recent_days ?? []}
            selectedDate={selectedDate}
            onSelect={selectDate}
          />
        </div>
      </div>

      <ChroniclesDrilldownPanel date={selectedDate} tz={ownerTz} />
    </Page>
  );
}
