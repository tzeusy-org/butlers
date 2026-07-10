// ---------------------------------------------------------------------------
// DayNarrative — the selected day's optional LLM prose summary + flag labels
//
// The chronicler runs a bounded, once-daily LLM labeling pass over each local
// day's rollup (migration chronicler_020): a one-line prose summary of the day
// and an optional natural-language label per anomaly flag. This lens surfaces
// that narration for the day currently in focus, read from GET /chronicler/
// rollups for the single selected day.
//
// Three-state honesty contract (butlers/CLAUDE.md API Conventions):
//   - Absent narrative is NORMAL, not an error: the labeling pass is optional,
//     has not run for this day (e.g. days before the feature), or produced no
//     text. Render nothing — never an error, never a fabricated placeholder.
//   - A genuine fetch failure (rollups_source_error / isError) degrades
//     honestly via SourceDegradedNote.
//
// Presentational: takes the query result pieces as props (renderToStaticMarkup
// testable), so it holds no fetch of its own.
// ---------------------------------------------------------------------------

import type { ChroniclerRollupsResponse } from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";

export interface DayNarrativeProps {
  /** GET /chronicler/rollups fetched for the single selected day. */
  data: ChroniclerRollupsResponse | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}

/** Friendly severity colour for a flag label dot. */
function severityColor(severity: string): string {
  // --amber is the shared warning/severity-medium token (see index.css); info
  // flags use the neutral muted-foreground.
  return severity === "warning" ? "var(--amber)" : "var(--muted-foreground)";
}

export function DayNarrative({ data, isLoading, isError, onRetry }: DayNarrativeProps) {
  if (isLoading) {
    return (
      <div role="status" aria-label="Loading day summary" data-testid="day-narrative-skeleton">
        <Skeleton className="h-5 w-3/4 rounded-md" />
      </div>
    );
  }

  // A genuine query failure — never a truthful-empty summary.
  if (isError || data?.rollups_source_error) {
    return (
      <SourceDegradedNote
        label="Day summary"
        detail="data source unreachable"
        onRetry={onRetry}
      />
    );
  }

  if (!data) return null;

  // Single-day fetch → one day. Guard defensively against an empty array.
  const day = data.days[0];
  if (!day) return null;

  const dayNarrative = day.narrative?.trim() || null;
  const flagNarratives = day.flags.filter((f) => (f.narrative?.trim() ?? "") !== "");

  // Absent narration is the normal case — render nothing, not a placeholder.
  if (!dayNarrative && flagNarratives.length === 0) return null;

  return (
    <div className="space-y-2" data-testid="day-narrative">
      {dayNarrative && (
        <p
          className="text-sm leading-relaxed text-foreground"
          data-testid="day-narrative-summary"
        >
          {dayNarrative}
        </p>
      )}
      {flagNarratives.length > 0 && (
        <ul className="space-y-1" data-testid="day-narrative-flags">
          {flagNarratives.map((f) => (
            <li
              key={f.flag_type}
              className="flex items-start gap-2 text-xs text-muted-foreground"
              data-testid={`day-narrative-flag-${f.flag_type}`}
            >
              <span
                aria-hidden
                className="mt-1 size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: severityColor(f.severity) }}
              />
              <span className="min-w-0 flex-1">{f.narrative}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
