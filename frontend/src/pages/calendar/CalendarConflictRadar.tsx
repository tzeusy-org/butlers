/**
 * Conflict and overcommitment radar above the time grid. An overlap's offered options
 * (bu-5ii4gj.11) post to the resolve route bound to the digest the owner saw.
 */

import { toast } from "sonner";

import { ConflictRadarBanner } from "@/components/calendar/ConflictRadarBanner";
import { optionLabel } from "@/components/calendar/conflict-option-label.ts";
import { ApiError } from "@/api/client.ts";
import { useResolveCalendarConflict } from "@/hooks/use-calendar-workspace";
import type { useCalendarConflicts } from "@/hooks/use-calendar-workspace";
import type { ConflictResolveResponse, ConflictScanResponse } from "@/api/types.ts";

interface CalendarConflictRadarProps {
  issues: ConflictScanResponse["issues"];
  available: boolean;
  conflictsQuery: ReturnType<typeof useCalendarConflicts>;
  /** The scanned window: a resolve must name the one its digest was read under. */
  scanWindow: { start: string; end: string };
}

function outcomeMessage(result: ConflictResolveResponse): string {
  switch (result.status) {
    case "acknowledged":
    case "already_acknowledged":
      return "Kept both. The radar stays quiet until one of them changes.";
    case "parked":
      return "Move sent for your approval.";
    case "responded":
      return result.response_outcome === "pending_approval"
        ? "Response sent for your approval."
        : "Response recorded on your calendar.";
  }
}

export function CalendarConflictRadar({
  issues,
  available,
  conflictsQuery,
  scanWindow,
}: CalendarConflictRadarProps) {
  const resolve = useResolveCalendarConflict();

  return (
    <ConflictRadarBanner
      issues={issues}
      available={available}
      isFetching={conflictsQuery.isFetching && !conflictsQuery.isLoading}
      isError={conflictsQuery.isError}
      error={conflictsQuery.error instanceof Error ? conflictsQuery.error : null}
      className="mb-2"
      isResolving={resolve.isPending}
      onResolve={(issue, option) => {
        const digest = issue.issue_digest;
        const pairId = issue.pair_id;
        if (!digest || !pairId) return;
        resolve.mutate(
          {
            pair_id: pairId,
            option_id: option.id,
            issue_digest: digest,
            start: scanWindow.start,
            end: scanWindow.end,
          },
          {
            onSuccess: (response) => toast.success(outcomeMessage(response.data)),
            onError: (error) =>
              toast.error(
                error instanceof ApiError && error.status === 409
                  ? "That overlap changed. Review it again."
                  : `Could not ${optionLabel(issue, option).toLowerCase()}.`,
              ),
          },
        );
      }}
    />
  );
}
