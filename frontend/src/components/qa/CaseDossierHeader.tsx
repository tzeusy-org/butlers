import type { QaActiveDismissal, QaCaseSummary } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useDismissQaIssue, useRemoveDismissal, useRetryHealingAttempt } from "@/hooks/use-qa";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

import { StateTrack, type QaStateTrackStage } from "./StateTrack";
import { formatQaDetectedTime, qaSeverityClassName } from "./utils";

const TERMINAL_STAGES: QaStateTrackStage[] = ["landed", "escalated"];

interface CaseDossierHeaderProps {
  case: QaCaseSummary;
  stage: QaStateTrackStage;
  /** Finding fingerprint — null when the case has no linked finding yet. */
  fingerprint: string | null;
  dismissal: QaActiveDismissal | null;
  className?: string;
}

function formatDismissalExpiry(expiresAt: string): string {
  const expires = new Date(expiresAt);
  if (Number.isNaN(expires.getTime())) return expiresAt;

  const now = new Date();
  const isToday =
    expires.getFullYear() === now.getFullYear() &&
    expires.getMonth() === now.getMonth() &&
    expires.getDate() === now.getDate();

  if (!isToday) {
    return expires.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return expires.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
  const retryAttempt = useRetryHealingAttempt();

  const isTerminal = TERMINAL_STAGES.includes(stage);
  const canDismiss = fingerprint !== null && dismissal === null && !isTerminal;
  const canRetry = isTerminal;

  return (
    <header className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn("h-2.5 w-2.5 shrink-0", qaSeverityClassName[qaCase.sev])}
          aria-label={`${qaCase.sev} severity`}
        />
        <p className="min-w-0 flex-1 truncate font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
          {qaCase.short_id} · {qaCase.butler} · detected {formatQaDetectedTime(qaCase.detected)}
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
          {canRetry ? (
            <Button
              type="button"
              variant="outline"
              size="xs"
              className="h-6 rounded-full px-2 font-mono text-[10px] uppercase tracking-[0.10em]"
              aria-label="Retry investigation"
              disabled={retryAttempt.isPending}
              onClick={() =>
                retryAttempt.mutate(
                  String(qaCase.id),
                  {
                    onSuccess: (data) =>
                      data.dispatched
                        ? toast.success("Investigation re-dispatched.")
                        : toast.message("Retry queued.", {
                            description:
                              "Awaiting daemon dispatch. No investigation agent was spawned yet.",
                          }),
                    onError: (err) =>
                      toast.error(`Retry failed: ${err instanceof Error ? err.message : "Unknown error"}`),
                  },
                )
              }
            >
              retry
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
          dismissed until {formatDismissalExpiry(dismissal.expires_at)}
        </p>
      ) : null}
      <h2 className="font-sans text-[22px] font-medium leading-[1.2] tracking-normal text-foreground">
        {qaCase.headline ?? "Untitled QA case"}
      </h2>
    </header>
  );
}
