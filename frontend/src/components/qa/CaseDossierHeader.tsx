import type { QaActiveDismissal, QaCaseSummary } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useDismissQaIssue, useRemoveDismissal } from "@/hooks/use-qa";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useTimezone } from "@/components/ui/timezone-context";
import { formatInTimeZone } from "date-fns-tz";

import { StateTrack, type QaStateTrackStage } from "./StateTrack";
import { formatQaDetectedTime, qaSeverityClassName } from "./utils";

// Terminal cases retain their history and cannot be dismissed as false positives.
const TERMINAL_STAGES: QaStateTrackStage[] = ["landed", "escalated", "failed"];

interface CaseDossierHeaderProps {
  case: QaCaseSummary;
  stage: QaStateTrackStage;
  /** Finding fingerprint — null when the case has no linked finding yet. */
  fingerprint: string | null;
  dismissal: QaActiveDismissal | null;
  className?: string;
}

function formatDismissalExpiry(expiresAt: string, timezone: string): string {
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) return expiresAt;
  const today = formatInTimeZone(new Date(), timezone, "yyyy-MM-dd");
  const day = formatInTimeZone(date, timezone, "yyyy-MM-dd");
  const time = formatInTimeZone(date, timezone, "h:mm a").toLowerCase();
  return day === today ? time : `${day} ${time}`;
}

export function CaseDossierHeader({
  case: qaCase,
  stage,
  fingerprint,
  dismissal,
  className,
}: CaseDossierHeaderProps) {
  const removeDismissal = useRemoveDismissal();
  const dismissIssue = useDismissQaIssue();
  const ownerTimezone = useTimezone();

  const isTerminal = TERMINAL_STAGES.includes(stage);
  const canDismiss = fingerprint !== null && dismissal === null && !isTerminal;

  return (
    <header className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn("h-2.5 w-2.5 shrink-0", qaSeverityClassName[qaCase.sev])}
          aria-label={`${qaCase.sev} severity`}
        />
        <p className="min-w-0 flex-1 truncate font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
          {qaCase.short_id} · {qaCase.butler} · detected {formatQaDetectedTime(qaCase.detected, ownerTimezone)}
        </p>
        <div className="ml-auto flex items-center gap-2">
          <StateTrack stage={stage} />
          {canDismiss ? (
            <Button
              type="button"
              variant="outline"
              size="xs"
              className="h-6 rounded-full px-2 font-mono text-[10px] uppercase tracking-[0.10em]"
              aria-label="Dismiss case"
              disabled={dismissIssue.isPending}
              onClick={() =>
                dismissIssue.mutate(
                  { fingerprint: fingerprint! },
                  {
                    onSuccess: () => toast.success("Case dismissed."),
                    onError: (err) =>
                      toast.error(`Dismiss failed: ${err instanceof Error ? err.message : "Unknown error"}`),
                  },
                )
              }
            >
              dismiss
            </Button>
          ) : null}
          {dismissal ? (
            <Button
              type="button"
              variant="outline"
              size="xs"
              className="h-6 rounded-full px-2 font-mono text-[10px] uppercase tracking-[0.10em]"
              aria-label="Remove dismissal"
              disabled={removeDismissal.isPending}
              onClick={() => removeDismissal.mutate(dismissal.fingerprint)}
            >
              remove dismissal
            </Button>
          ) : null}
        </div>
      </div>
      {dismissal ? (
        <p className="font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
              dismissed until {formatDismissalExpiry(dismissal.expires_at, ownerTimezone)}
        </p>
      ) : null}
      <h2 className="font-sans text-[22px] font-medium leading-[1.2] tracking-normal text-foreground">
        {qaCase.headline ?? "Untitled QA case"}
      </h2>
    </header>
  );
}
