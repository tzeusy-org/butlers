/**
 * StatusBadge — color-coded badge for ingestion event lifecycle status.
 *
 * Status → color mapping:
 * - ingested      → green (default/success)
 * - filtered      → gray (secondary)
 * - error         → red (destructive)
 * - replay_pending → blue (custom)
 * - replay_complete → green outline
 * - replay_failed  → red outline
 *
 * For filtered and error statuses, wraps in a Tooltip showing filter_reason.
 */

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { IngestionEventStatus } from "@/api/index.ts";

interface StatusBadgeProps {
  status: IngestionEventStatus;
  filterReason?: string | null;
}

const STATUS_LABELS: Record<IngestionEventStatus, string> = {
  ingested: "ingested",
  filtered: "filtered",
  error: "error",
  replay_pending: "replay pending",
  replay_complete: "replayed",
  replay_failed: "replay failed",
};

function BadgeInner({ status }: { status: IngestionEventStatus }) {
  switch (status) {
    case "ingested":
      return (
        <Badge className="bg-emerald-500 text-white hover:bg-emerald-600">
          {STATUS_LABELS.ingested}
        </Badge>
      );
    case "filtered":
      return (
        <Badge variant="secondary">{STATUS_LABELS.filtered}</Badge>
      );
    case "error":
      return (
        <Badge variant="destructive">{STATUS_LABELS.error}</Badge>
      );
    case "replay_pending":
      return (
        <Badge className="bg-blue-500 text-white hover:bg-blue-600">
          {STATUS_LABELS.replay_pending}
        </Badge>
      );
    case "replay_complete":
      return (
        <Badge
          variant="outline"
          className="border-emerald-500 text-emerald-600"
        >
          {STATUS_LABELS.replay_complete}
        </Badge>
      );
    case "replay_failed":
      return (
        <Badge
          variant="outline"
          className="border-destructive text-destructive"
        >
          {STATUS_LABELS.replay_failed}
        </Badge>
      );
    default:
      return <Badge variant="outline">{String(status)}</Badge>;
  }
}

export function StatusBadge({ status, filterReason }: StatusBadgeProps) {
  const showTooltip =
    (status === "filtered" || status === "error") && !!filterReason;

  if (!showTooltip) {
    return <BadgeInner status={status} />;
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help">
            <BadgeInner status={status} />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p className="max-w-xs text-xs">{filterReason}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
