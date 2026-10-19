/**
 * HealthBaselinesPanel
 *
 * The owner's usual range for resting heart rate, HRV and sleep duration, with
 * the honest denominator it rests on ("54 of 60 days"). A band is drawn only
 * for a ready, fresh baseline; insufficient history, an unmeasurable
 * instrument, a stale baseline and a missing baseline render as one sentence,
 * never as a range. An unreadable store says so; the rest of the tab keeps
 * rendering.
 *
 * bead: bu-q7vx1q.77
 */

import type { HealthBaseline, HealthBaselinesResponse } from "@/api/index.ts";
import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { Time } from "@/components/ui/time";

const METRIC_LABEL: Record<string, string> = {
  resting_hr: "Resting heart rate",
  hrv: "Heart rate variability",
  sleep_duration: "Sleep duration",
};

function fmtBound(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** The one sentence a baseline without a band is allowed to say. */
function baselineSentence(b: HealthBaseline): React.ReactNode {
  switch (b.state) {
    case "insufficient_history":
      return `Not enough days yet: ${b.n_observed} of ${b.n_expected}`;
    case "unmeasurable":
      return "The instrument is not reporting, so there is no usual range yet";
    case "stale":
      return (
        <>
          Last computed{" "}
          {b.computed_at ? (
            <Time value={b.computed_at} mode="absolute" precision="day" compact />
          ) : (
            "a while ago"
          )}
          ; the usual range is hidden until it refreshes
        </>
      );
    case "no_baseline":
      return "No baseline yet";
    default:
      return null;
  }
}

function BaselineRow({ baseline }: { baseline: HealthBaseline }) {
  const label = METRIC_LABEL[baseline.metric_key] ?? baseline.metric_key.replaceAll("_", " ");
  const band = baseline.state === "ready" ? baseline.band : null;
  const unit = baseline.unit ? ` ${baseline.unit}` : "";
  return (
    <li className="py-2" data-testid={`baseline-row-${baseline.metric_key}`}>
      <p className="text-sm font-medium">{label}</p>
      {band ? (
        <div className="mt-1 space-y-1">
          <div
            className="h-2 rounded-full bg-muted"
            role="img"
            aria-label={`Usual ${label.toLowerCase()} range ${fmtBound(band.low)} to ${fmtBound(band.high)}${unit}`}
          >
            <div
              className="h-2 rounded-full bg-primary/40"
              data-testid={`baseline-band-${baseline.metric_key}`}
            />
          </div>
          <p className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
            <span className="font-mono tnum" data-testid={`baseline-range-${baseline.metric_key}`}>
              {`Usual ${fmtBound(band.low)} to ${fmtBound(band.high)}${unit}`}
            </span>
            <span
              className="text-muted-foreground"
              data-testid={`baseline-plaque-${baseline.metric_key}`}
            >
              {`${baseline.n_observed} of ${baseline.n_expected} days`}
            </span>
          </p>
        </div>
      ) : (
        <p
          className="text-xs text-muted-foreground"
          data-testid={`baseline-words-${baseline.metric_key}`}
        >
          {baselineSentence(baseline)}
        </p>
      )}
    </li>
  );
}

export function HealthBaselinesPanel({
  data,
  isLoading,
  isError,
}: {
  data: HealthBaselinesResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  const unreadable = isError || (data !== undefined && (!data.available || !data.baselines));
  return (
    <Section data-testid="health-baselines-panel">
      <SectionHeader className="pb-2">
        <SectionTitle>Usual range</SectionTitle>
      </SectionHeader>
      <SectionContent>
        {isLoading ? (
          <p className="text-sm text-muted-foreground" data-testid="health-baselines-loading">
            Loading…
          </p>
        ) : unreadable || !data?.baselines ? (
          <p
            className="text-sm text-[var(--amber-text)]"
            role="status"
            data-testid="health-baselines-unavailable"
          >
            The usual ranges could not be read. Trends below are unaffected.
          </p>
        ) : (
          <ul className="divide-y" data-testid="health-baselines-list">
            {data.baselines.map((baseline) => (
              <BaselineRow key={baseline.metric_key} baseline={baseline} />
            ))}
          </ul>
        )}
      </SectionContent>
    </Section>
  );
}
