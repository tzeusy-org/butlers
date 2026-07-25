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

import { useEffect, useMemo } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useSearchParams } from "react-router";

import { useTimezone } from "@/components/ui/timezone-context";
import { dayKeyInTimeZone, shiftDayKey } from "@/lib/day-window";
import { useChroniclesBriefing } from "@/hooks/use-chronicles-briefing";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { Page } from "@/components/ui/page";
import { FetchingDim } from "@/components/ui/fetching-dim";
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

/** State-class predicate for the greeting line. Past tense, sentence case.
 *
 * `no_data` / `unavailable` / `degraded` are non-content states: this day's
 * coverage or availability could not be affirmed, so they read as an honest
 * "could not be confirmed" rather than borrowing the quiet-day predicate
 * (clarify-chronicles-narrative-truth design.md decision 3 -- an outage or
 * an unproven historical day must never narrate as a quiet day). */
const STATE_PREDICATE: Record<string, string> = {
  urgent: "had loose ends.",
  busy: "was full.",
  mild: "went mostly to plan.",
  quiet: "was quiet.",
  no_data: "is before the chronicled archive.",
  unavailable: "could not be confirmed.",
  degraded: "has degraded coverage.",
};

/** Non-content states: coverage/availability for the day was not affirmed.
 * Never render these with quiet-day copy or the Attention/KPI content. */
const NON_CONTENT_STATES = new Set(["no_data", "unavailable", "degraded"]);

/** Two-line greeting: a date-relative subject plus the briefing headline.
 *
 * An unrecognized state_class (a backend value this build predates) falls
 * back to a neutral "could not be classified" rather than the quiet
 * predicate -- an unknown state is never presumed calm. */
function deriveHeadlineLines(stateClass: string, headline: string, subject: string) {
  const predicate = STATE_PREDICATE[stateClass] ?? "could not be classified.";
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

/** The most recent settled day: yesterday in the owner timezone. */
function yesterdayInTimeZone(timeZone: string): string {
  return shiftDayKey(dayKeyInTimeZone(new Date(), timeZone), -1);
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

  // -------------------------------------------------------------------
  // Palette verbs + bindings (bu-t64p2 -- reachability sweep, bu-qvnce.11
  // slice 5). Reuses the prev/next-day steppers and refetch already wired
  // above; "Jump to latest day" composes selectDate(latest), which was
  // computed but never itself exposed as a one-click affordance.
  // -------------------------------------------------------------------
  const atLatestDay = selectedDate === latest;
  const chroniclesCommands = useMemo<PaletteCommand[]>(() => {
    const commands: PaletteCommand[] = [];
    if (!atEarliest) {
      commands.push({
        id: "chronicles-prev-day",
        label: "Previous day",
        keywords: ["back", "earlier"],
        perform: () => selectDate(prevIsoDay(selectedDate)),
        binding: ["["],
      });
    }
    if (!atLatest) {
      commands.push({
        id: "chronicles-next-day",
        label: "Next day",
        keywords: ["forward", "later"],
        perform: () => selectDate(nextIsoDay(selectedDate)),
        binding: ["]"],
      });
    }
    if (!atLatestDay) {
      commands.push({
        id: "chronicles-jump-latest",
        label: "Jump to latest day",
        keywords: ["today", "latest", "recent"],
        perform: () => selectDate(latest),
        binding: ["t"],
      });
    }
    commands.push({
      id: "chronicles-reload-briefing",
      label: "Reload chronicles briefing",
      keywords: ["refresh", "reload"],
      perform: () => void refetch(),
    });
    return commands;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectDate/refetch are recreated every render and closed over directly; the listed values are what actually vary the resulting command set.
  }, [atEarliest, atLatest, atLatestDay, selectedDate, latest]);
  useRegisterCommands(chroniclesCommands);

  const chroniclesShortcuts = useMemo<ShortcutBinding[]>(() => {
    const bindings: ShortcutBinding[] = [];
    if (!atEarliest) {
      bindings.push({
        key: "[",
        display: ["["],
        description: "Previous day",
        handler: () => selectDate(prevIsoDay(selectedDate)),
      });
    }
    if (!atLatest) {
      bindings.push({
        key: "]",
        display: ["]"],
        description: "Next day",
        handler: () => selectDate(nextIsoDay(selectedDate)),
      });
    }
    if (!atLatestDay) {
      bindings.push({
        key: "t",
        display: ["t"],
        description: "Jump to latest day",
        handler: () => selectDate(latest),
      });
    }
    return bindings;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- same closures as chroniclesCommands above.
  }, [atEarliest, atLatest, atLatestDay, selectedDate, latest]);
  useRegisterShortcut(chroniclesShortcuts);

  const subject = greetSubject(selectedDate, latest);
  const headlineLines = deriveHeadlineLines(
    data?.state_class ?? "quiet",
    data?.headline ?? "Quiet day.",
    subject,
  );
  const isStale = data?.voice_source === "stale";
  const isNonContentState = data != null && NON_CONTENT_STATES.has(data.state_class);

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
            {isNonContentState ? (
              <span
                style={{
                  ...EYEBROW_STYLE,
                  fontSize: "9px",
                  letterSpacing: "0.08em",
                  color: "var(--destructive, var(--muted-foreground))",
                }}
                title="Coverage or availability for this day could not be affirmed."
                aria-label="Coverage or availability for this day could not be affirmed"
              >
                {data!.state_class.replace("_", " ")}
              </span>
            ) : null}
          </div>

          {/* Never-blank floor (bu-nhcp5): with placeholderData, `data` keeps
              showing the previous day's briefing the instant the day-step
              stepper fires; this wrapper dims it instead of the page falling
              back to the full skeleton. Elaboration's own isFetching dim is
              disabled here (false) so the two treatments don't compound. */}
          <FetchingDim isFetching={isFetching} className="space-y-6">
            <Headline greet={headlineLines.greet} body={headlineLines.body} />

            <Elaboration
              text={data?.voice_paragraph ?? "The day is still being composed."}
              isFetching={false}
            />
          </FetchingDim>
        </div>

        {/* Right column: attention leads, then KPI strip, then recent days.
            A non-content day (no_data/unavailable/degraded) has no reader-
            visible evidence to show as Attention/KPI content -- rendering
            "Nothing waiting." there would itself imply a checked-clear day,
            the same fabricated-calm failure this state exists to prevent. */}
        <FetchingDim isFetching={isFetching} className="space-y-8">
          {isNonContentState ? (
            <Section eyebrow="Coverage">
              <p className="text-sm text-muted-foreground" role="status">
                {data!.voice_paragraph}
              </p>
            </Section>
          ) : (
            <>
              <Section eyebrow="Attention">
                <AttentionList items={adaptAttention(data?.attention_items ?? [])} />
              </Section>
              {data?.kpi ? <KpiStrip cells={buildKpiCells(data.kpi)} /> : null}
            </>
          )}
          <RecentDaysIndex
            days={data?.recent_days ?? []}
            selectedDate={selectedDate}
            onSelect={selectDate}
          />
        </FetchingDim>
      </div>

      <ChroniclesDrilldownPanel date={selectedDate} tz={ownerTz} />
    </Page>
  );
}
