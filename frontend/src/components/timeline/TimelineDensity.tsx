import { useRef, useState } from "react";

import type { TimelineHistogramResponse } from "@/api/types";
import { Button } from "@/components/ui/button";
import { formatOwnerDateTime } from "@/components/ui/time";
import { useTimezone } from "@/components/ui/timezone-context";

interface TimelineDensityProps {
  histogram?: TimelineHistogramResponse;
  isLoading: boolean;
  isError: boolean;
  selectedSince?: string;
  onSelect: (since: string, until: string) => void;
  onRetry: () => void;
}

export function TimelineDensity({
  histogram,
  isLoading,
  isError,
  selectedSince,
  onSelect,
  onRetry,
}: TimelineDensityProps) {
  const timezone = useTimezone();
  const [activeIndex, setActiveIndex] = useState(0);
  const buttonsRef = useRef<Array<HTMLButtonElement | null>>([]);

  if (isLoading && !histogram) {
    return <div className="h-24 rounded border border-border bg-muted/20" aria-label="Loading timeline density" />;
  }

  if (isError && !histogram) {
    return (
      <div className="flex items-center justify-between rounded border border-destructive/40 px-3 py-2 text-sm" role="alert">
        <span>Could not load timeline density.</span>
        <Button type="button" variant="outline" size="xs" onClick={onRetry}>Retry</Button>
      </div>
    );
  }

  if (!histogram) return null;

  const buckets = histogram.data;
  const selectedIndex = buckets.findIndex(
    (bucket) => Date.parse(bucket.start) === Date.parse(selectedSince ?? ""),
  );
  const { meta } = histogram;
  const maxCount = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const total = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
  const unavailable = meta.availability === "unavailable";
  const partial = meta.availability === "partial";

  function moveFocus(nextIndex: number) {
    const bounded = Math.min(Math.max(nextIndex, 0), buckets.length - 1);
    setActiveIndex(bounded);
    buttonsRef.current[bounded]?.focus({ preventScroll: true });
  }

  return (
    <section className="space-y-2" aria-labelledby="timeline-density-title" data-testid="timeline-density">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 id="timeline-density-title" className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            All matching events
          </h2>
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {meta.expected_sources === 0
              ? "No matching event sources"
              : unavailable
                ? `Density unavailable · 0 of ${meta.expected_sources} sources available`
                : `${total} events · ${meta.healthy_sources} of ${meta.expected_sources} sources available${partial ? " · partial" : ""}`}
          </p>
        </div>
        {isError || partial || unavailable ? (
          <Button type="button" variant="outline" size="xs" onClick={onRetry}>Retry</Button>
        ) : null}
      </div>

      {meta.degraded_sources.length > 0 || meta.degraded_butlers.length > 0 ? (
        <p className="text-xs text-[var(--amber-text)]" role="status">
          {meta.degraded_sources.length > 0
            ? `Unavailable sources: ${meta.degraded_sources.join(", ")}. `
            : ""}
          {meta.degraded_butlers.length > 0
            ? `Unavailable butlers: ${meta.degraded_butlers.join(", ")}.`
            : ""}
        </p>
      ) : null}

      {unavailable ? (
        <div className="rounded border border-destructive/40 px-3 py-4 text-sm" role="status">
          Counts are not shown because every selected event source is unavailable.
        </div>
      ) : (
        <div
          className="flex h-20 items-end gap-px rounded border border-border bg-muted/10 px-2 pt-2"
          role="group"
          aria-label="Timeline event density by minute"
        >
          {buckets.map((bucket, index) => {
            const selected = index === selectedIndex;
            const label = `${formatOwnerDateTime(bucket.start, timezone, "minute", false)}, ${bucket.count} ${bucket.count === 1 ? "event" : "events"}`;
            return (
              <button
                key={bucket.start}
                ref={(node) => { buttonsRef.current[index] = node; }}
                type="button"
                aria-pressed={selected}
                aria-label={label}
                title={label}
                data-testid="timeline-density-bucket"
                tabIndex={index === activeIndex ? 0 : -1}
                className="group relative flex h-full min-w-0 flex-1 items-end focus-visible:z-10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
                onFocus={() => setActiveIndex(index)}
                onClick={() => onSelect(bucket.start, bucket.end)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowRight" || event.key === "ArrowUp") {
                    event.preventDefault();
                    moveFocus(activeIndex + 1);
                  } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
                    event.preventDefault();
                    moveFocus(activeIndex - 1);
                  } else if (event.key === "Home") {
                    event.preventDefault();
                    moveFocus(0);
                  } else if (event.key === "End") {
                    event.preventDefault();
                    moveFocus(buckets.length - 1);
                  }
                }}
              >
                <span
                  className={`block w-full min-h-px ${selected ? "bg-foreground" : "bg-[var(--chart-1)] group-hover:bg-foreground/70"}`}
                  style={{ height: `${Math.max(bucket.count === 0 ? 2 : 8, (bucket.count / maxCount) * 100)}%` }}
                />
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
