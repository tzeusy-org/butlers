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
// recent settled day (yesterday in owner tz). Future values are clamped, but
// a valid pre-floor deep link stays addressable so the backend can return its
// truthful no_data state. Navigation reuses the existing cached/templated
// briefing; it never initiates an LLM call.
//
// All copy obeys the voice rules from
// about/heart-and-soul/design-language.md: sentence case, no em-dashes,
// and no exclamation marks.
// ---------------------------------------------------------------------------

import { useEffect, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useSearchParams } from "react-router";

import { useTimezone } from "@/components/ui/timezone-context";
import { dayKeyInTimeZone, shiftDayKey } from "@/lib/day-window";
import { postChroniclerDayCloseRefresh } from "@/api/client.ts";
import type {
  ChroniclerDayCloseRefreshRequest,
  ChroniclerDayCloseRefreshResult,
} from "@/api/types.ts";
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
import type {
  ChroniclesAttentionItem,
  ChroniclesKpi,
  ChroniclesStateClass,
} from "@/api/types";

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
const STATE_PREDICATE: Record<ChroniclesStateClass, string> = {
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
const NON_CONTENT_STATES = new Set<ChroniclesStateClass>([
  "no_data",
  "unavailable",
  "degraded",
]);
const CONTENT_STATES = new Set<ChroniclesStateClass>(["urgent", "busy", "mild", "quiet"]);
const KNOWN_STATE_CLASSES = new Set<ChroniclesStateClass>([
  ...CONTENT_STATES,
  ...NON_CONTENT_STATES,
]);

const UNAVAILABLE_FALLBACK = {
  headline: "Coverage for this day could not be confirmed.",
  voiceParagraph: "Chronicler could not confirm whether this day was chronicled.",
} as const;

function isChroniclesStateClass(value: unknown): value is ChroniclesStateClass {
  return typeof value === "string" && KNOWN_STATE_CLASSES.has(value as ChroniclesStateClass);
}

/** Two-line greeting: a date-relative subject plus the briefing headline.
 *
 * Callers must first close the state union. Unknown or missing state values
 * are rendered with the deterministic unavailable fallback, never quiet-day
 * content. */
function deriveHeadlineLines(stateClass: ChroniclesStateClass, headline: string, subject: string) {
  const predicate = STATE_PREDICATE[stateClass];
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
function adaptAttention(
  items: ChroniclesAttentionItem[],
  onRetry: () => void,
): AttentionListItem[] {
  return items.map((it) => ({
    id: `chronicles:${it.kind}:${it.title}`,
    severity: it.severity,
    title: it.title,
    detail: it.detail,
    href: it.action_href,
    isSourceError: it.kind === "source_error",
    onRetry: it.kind === "source_error" ? onRetry : undefined,
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

  // Future dates are never settled, so canonicalize them immediately. A valid
  // historic pre-floor date deliberately remains requested: the backend must
  // distinguish its truthful no_data response from an unavailable gap.
  const fetchDate = clampIsoDay(requestedDate, undefined, latest);
  const selectedDate = fetchDate;

  const { data, isFetching, isError, isPlaceholderData, refetch } = useChroniclesBriefing({
    date: fetchDate,
    tz: ownerTz,
  });

  // TanStack Query retains placeholder data across a query-key change. A
  // same-date placeholder can still come from a different owner timezone, so
  // its date alone cannot establish the archive boundary or editorial content
  // for this request. Treat all placeholder data as pending until the requested
  // key resolves with real data.
  const briefing = !isPlaceholderData && data?.date === selectedDate ? data : undefined;

  // earliest_date arrives with every briefing (it is a global minimum,
  // independent of the requested day). It gates only *additional* backward
  // travel: a valid pre-floor deep link remains selected and can recover
  // forward instead of being silently rewritten to the floor.
  const earliest = briefing?.earliest_date ?? null;

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

  // Canonicalize only future dates. Pre-floor archive links are valid input and
  // must retain their addressability so no_data has a meaningful date.
  useEffect(() => {
    if (fetchDate !== requestedDate) {
      selectDate(fetchDate);
    }
    // selectDate is stable for our purposes; depend on the resolved values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchDate, requestedDate]);

  // A missing archive floor is not proof that earlier days can be read. Keep
  // the backward control disabled until the response establishes a truthful
  // boundary instead of allowing unbounded requests behind an unavailable
  // coverage query.
  const atEarliest = earliest === null || isAtEarliest(selectedDate, earliest);
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

  const receivedStateClass = briefing?.state_class;
  const hasKnownState = isChroniclesStateClass(receivedStateClass);
  const stateClass: ChroniclesStateClass = hasKnownState ? receivedStateClass : "unavailable";
  const isKnownContentState = hasKnownState && CONTENT_STATES.has(stateClass);
  const isKnownNonContentState = hasKnownState && NON_CONTENT_STATES.has(stateClass);
  // A response object without a recognized state is malformed rather than
  // quiet. It must not leak a cached headline, prose, KPI, or recent index.
  const isUnknownState = briefing != null && !hasKnownState;
  const isNonContentState = briefing != null && !isKnownContentState;
  const subject = greetSubject(selectedDate, latest);
  const headline = isUnknownState
    ? UNAVAILABLE_FALLBACK.headline
    : (briefing?.headline ?? UNAVAILABLE_FALLBACK.headline);
  const voiceParagraph = isUnknownState
    ? UNAVAILABLE_FALLBACK.voiceParagraph
    : (briefing?.voice_paragraph ?? UNAVAILABLE_FALLBACK.voiceParagraph);
  const headlineLines = deriveHeadlineLines(stateClass, headline, subject);
  const isStale = isKnownContentState && briefing?.voice_source === "stale";
  const regenerateDayClose = useMutation<
    ChroniclerDayCloseRefreshResult,
    Error,
    ChroniclerDayCloseRefreshRequest
  >({
    mutationFn: (tuple) => postChroniclerDayCloseRefresh(tuple),
    onSuccess: async (_result, refreshedTuple) => {
      // Do not refresh a newly selected date/timezone with completion state
      // from the prior tuple if the owner navigated while this was in flight.
      if (refreshedTuple.date === selectedDate && refreshedTuple.tz === ownerTz) {
        await refetch();
      }
    },
  });
  const isCurrentDayCloseRegeneration =
    regenerateDayClose.variables?.date === selectedDate &&
    regenerateDayClose.variables?.tz === ownerTz;
  const isCurrentDayCloseRegenerationPending =
    isCurrentDayCloseRegeneration && regenerateDayClose.isPending;
  const isCurrentDayCloseRegenerationError =
    isCurrentDayCloseRegeneration && regenerateDayClose.isError;
  const attentionItems = adaptAttention(
    isUnknownState ? [] : (briefing?.attention_items ?? []),
    () => void refetch(),
  );
  const sourceErrorItems = isKnownNonContentState
    ? attentionItems.filter((item) => item.isSourceError)
    : [];

  return (
    <Page
      archetype="editorial"
      title="Chronicles"
      description="Retrospective view of lived past time reconstructed from butler evidence."
      loading={!briefing && !isError}
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
              aria-label={
                earliest === null ? "Previous day: archive boundary unavailable" : "Previous day"
              }
              title={earliest === null ? "Archive boundary unavailable" : undefined}
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
              <>
                <span
                  style={{ ...EYEBROW_STYLE, fontSize: "9px", letterSpacing: "0.08em" }}
                  title="The day-close summary may be out of date."
                  aria-label="Day-close summary may be out of date"
                >
                  stale
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={isCurrentDayCloseRegenerationPending}
                  aria-busy={isCurrentDayCloseRegenerationPending}
                  aria-label="Regenerate day-close summary"
                  onClick={() =>
                    regenerateDayClose.mutate({ date: selectedDate, tz: ownerTz })
                  }
                >
                  {isCurrentDayCloseRegenerationPending ? "Regenerating" : "Regenerate"}
                </Button>
                {isCurrentDayCloseRegenerationError ? (
                  <span role="alert" style={{ ...EYEBROW_STYLE, color: "var(--destructive)" }}>
                    Regeneration failed.
                  </span>
                ) : null}
              </>
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
                {stateClass.replace("_", " ")}
              </span>
            ) : null}
          </div>

          {/* A matching response can be refreshed in place with a light dim.
              Cross-date placeholder data is rejected above and uses the page
              loading state instead, so it cannot narrate the newly selected
              day. Elaboration's own dim is disabled here (false) so the two
              treatments do not compound. */}
          <FetchingDim isFetching={isFetching} className="space-y-6">
            <Headline greet={headlineLines.greet} body={headlineLines.body} />

            <Elaboration
              text={voiceParagraph}
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
            <>
              <Section eyebrow="Coverage">
                <p className="text-sm text-muted-foreground" role="status">
                  {voiceParagraph}
                </p>
              </Section>
              {sourceErrorItems.length > 0 ? (
                <Section eyebrow="Attention">
                  <AttentionList items={sourceErrorItems} />
                </Section>
              ) : null}
            </>
          ) : (
            <>
              <Section eyebrow="Attention">
                <AttentionList items={attentionItems} />
              </Section>
              {briefing?.kpi ? <KpiStrip cells={buildKpiCells(briefing.kpi)} /> : null}
            </>
          )}
          {!isNonContentState ? (
            <RecentDaysIndex
              days={briefing?.recent_days ?? []}
              selectedDate={selectedDate}
              onSelect={selectDate}
            />
          ) : null}
        </FetchingDim>
      </div>

      {isKnownContentState ? <ChroniclesDrilldownPanel date={selectedDate} tz={ownerTz} /> : null}
    </Page>
  );
}
