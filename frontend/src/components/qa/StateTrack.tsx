import { cn } from "@/lib/utils";

export type QaStateTrackStage = "detect" | "diagnose" | "pr" | "landed" | "escalated";

interface StateTrackProps {
  stage: QaStateTrackStage;
  className?: string;
}

const TRACK_STAGES: Exclude<QaStateTrackStage, "escalated">[] = [
  "detect",
  "diagnose",
  "pr",
  "landed",
];

function stageClass(stage: Exclude<QaStateTrackStage, "escalated">, activeStage: QaStateTrackStage) {
  if (activeStage === "escalated") {
    return stage === "pr" || stage === "landed" ? "text-amber-500" : "text-foreground";
  }

  const currentIndex = TRACK_STAGES.indexOf(activeStage);
  const stageIndex = TRACK_STAGES.indexOf(stage);
  return stageIndex <= currentIndex ? "text-foreground" : "text-muted-foreground";
}

export function StateTrack({ stage, className }: StateTrackProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1 font-mono text-[10px] uppercase tracking-[0.12em] tnum",
        className,
      )}
      aria-label={`QA state: ${stage}`}
    >
      {TRACK_STAGES.map((trackStage, index) => (
        <span key={trackStage} className="inline-flex items-center gap-1">
          <span
            className={cn("transition-colors duration-fast", stageClass(trackStage, stage))}
            data-testid={`qa-state-track-${trackStage}`}
          >
            {trackStage}
          </span>
          {index < TRACK_STAGES.length - 1 ? (
            <span className="text-muted-foreground" aria-hidden="true">
              —
            </span>
          ) : null}
        </span>
      ))}
      {stage === "escalated" ? (
        <span
          className="text-amber-500"
          data-testid="qa-state-track-escalated-label"
        >
          · escalated
        </span>
      ) : null}
    </div>
  );
}
