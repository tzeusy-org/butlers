import type { QaCaseSummary } from "@/api/types";
import { cn } from "@/lib/utils";

import { StateTrack, type QaStateTrackStage } from "./StateTrack";
import { formatQaDetectedTime, qaSeverityClassName } from "./utils";

interface CaseDossierHeaderProps {
  case: QaCaseSummary;
  stage: QaStateTrackStage;
  className?: string;
}

export function CaseDossierHeader({ case: qaCase, stage, className }: CaseDossierHeaderProps) {
  return (
    <header className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn("h-2.5 w-2.5 shrink-0", qaSeverityClassName[qaCase.sev])}
          aria-label={`${qaCase.sev} severity`}
        />
        <p className="min-w-0 flex-1 truncate font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
          #{qaCase.short_id} · {qaCase.butler} · detected {formatQaDetectedTime(qaCase.detected)}
        </p>
        <StateTrack stage={stage} className="ml-auto" />
      </div>
      <h2 className="font-sans text-[22px] font-medium leading-[1.2] tracking-normal text-foreground">
        {qaCase.headline ?? "Untitled QA case"}
      </h2>
    </header>
  );
}
