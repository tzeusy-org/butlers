// ---------------------------------------------------------------------------
// CorrectionPromptsPanel — low-confidence activities as gentle prompts (IEA §10)
//
// Surfaces the day's low-confidence activities ("best guess: errands —
// correct?") the owner can confirm or relabel. The write path reuses the
// EXISTING corrections overlay: clicking a prompt opens the EpisodeDrawer,
// whose correction form posts to /episodes/{id}/corrections. Once an override
// exists the prompt drops off the list server-side.
//
// Degraded-mode: the correction-prompts envelope has no dedicated source-error
// flag (a query failure surfaces as isError). A genuine query failure →
// SourceDegradedNote; an empty list is a legitimate "nothing to confirm" good
// state, NOT a degraded source.
//
// Presentational: takes the query result pieces + an onSelectEpisode callback
// (renderToStaticMarkup testable).
// ---------------------------------------------------------------------------

import type { ChroniclerCorrectionPrompts } from "@/api/types";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { LANE_TAXONOMY, type Category } from "./lane-taxonomy";
import { useChroniclesTimezone } from "./use-chronicles-timezone";
import { formatTimeInTz } from "./tz-format";

export interface CorrectionPromptsPanelProps {
  data: ChroniclerCorrectionPrompts | undefined;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  /** Opens the EpisodeDrawer (correction overlay) for the chosen activity. */
  onSelectEpisode?: (episodeId: string) => void;
}

function laneConfig(lane: string | null) {
  if (!lane) return LANE_TAXONOMY.other;
  return (LANE_TAXONOMY as Record<string, (typeof LANE_TAXONOMY)[Category]>)[lane] ?? LANE_TAXONOMY.other;
}

export function CorrectionPromptsPanel({
  data,
  isLoading,
  isError,
  onRetry,
  onSelectEpisode,
}: CorrectionPromptsPanelProps) {
  const tz = useChroniclesTimezone();

  if (isLoading) {
    return (
      <div
        className="space-y-2"
        role="status"
        aria-label="Loading correction prompts"
        data-testid="prompts-skeleton"
      >
        {Array.from({ length: 2 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <SourceDegradedNote
        label="Correction prompts"
        detail="data source unreachable"
        onRetry={onRetry}
      />
    );
  }

  if (!data) return null;

  if (data.prompts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="prompts-empty">
        Nothing to confirm. Every activity today is confidently labelled.
      </p>
    );
  }

  return (
    <ul className="space-y-2" data-testid="correction-prompts">
      {data.prompts.map((p) => {
        const config = laneConfig(p.best_guess_lane);
        const Icon = config.icon;
        return (
          <li
            key={p.episode_id}
            className="flex items-center gap-3 rounded-md border p-2.5 text-sm"
            style={{ borderColor: "var(--border)" }}
            data-testid={`correction-prompt-${p.episode_id}`}
          >
            <span
              className="flex size-7 shrink-0 items-center justify-center rounded-md"
              style={{
                backgroundColor: `color-mix(in oklch, ${config.color} 14%, transparent)`,
                color: config.color,
              }}
              aria-hidden
            >
              <Icon className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate">
                Best guess:{" "}
                <span className="font-medium">
                  {p.best_guess_lane ? config.label.toLowerCase() : "unclear"}
                </span>
                {p.title ? ` — ${p.title}` : ""}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatTimeInTz(p.start_at, tz)}
                {" · "}
                {p.evidence_count === 1 ? "1 signal" : `${p.evidence_count} signals`}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onSelectEpisode?.(p.episode_id)}
              data-testid={`correction-prompt-confirm-${p.episode_id}`}
            >
              Confirm or relabel
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
