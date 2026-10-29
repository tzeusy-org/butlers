/**
 * Conflict and overcommitment radar above the time grid, wired to the
 * proposal accept/dismiss mutations for its suggested fixes.
 */

import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ConflictRadarBanner } from "@/components/calendar/ConflictRadarBanner";
import type {
  useAcceptCalendarProposal,
  useCalendarConflicts,
  useDismissCalendarProposal,
} from "@/hooks/use-calendar-workspace";
import type { ConflictScanResponse } from "@/api/types.ts";

interface CalendarConflictRadarProps {
  issues: ConflictScanResponse["issues"];
  available: boolean;
  conflictsQuery: ReturnType<typeof useCalendarConflicts>;
  acceptMutation: ReturnType<typeof useAcceptCalendarProposal>;
  dismissMutation: ReturnType<typeof useDismissCalendarProposal>;
}

export function CalendarConflictRadar({
  issues,
  available,
  conflictsQuery,
  acceptMutation,
  dismissMutation,
}: CalendarConflictRadarProps) {
  const queryClient = useQueryClient();
  const refreshConflicts = () =>
    queryClient.invalidateQueries({ queryKey: ["calendar-conflicts"] });
  const messageOf = (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback;

  return (
    <ConflictRadarBanner
      issues={issues}
      available={available}
      isFetching={conflictsQuery.isFetching && !conflictsQuery.isLoading}
      isError={conflictsQuery.isError}
      error={conflictsQuery.error instanceof Error ? conflictsQuery.error : null}
      className="mb-2"
      isProposalActionPending={acceptMutation.isPending || dismissMutation.isPending}
      onAcceptProposal={(proposalId) => {
        acceptMutation.mutate(
          { proposalId },
          {
            onSuccess: () =>
              toast.success("Proposal accepted. Event added to the Butlers calendar."),
            onError: (error) =>
              toast.error(messageOf(error, "Failed to accept the proposed fix.")),
            onSettled: refreshConflicts,
          },
        );
      }}
      onDismissProposal={(proposalId) => {
        dismissMutation.mutate(
          { proposalId },
          {
            onSuccess: () => toast.success("Proposal dismissed."),
            onError: (error) =>
              toast.error(messageOf(error, "Failed to dismiss the proposed fix.")),
            onSettled: refreshConflicts,
          },
        );
      }}
    />
  );
}
