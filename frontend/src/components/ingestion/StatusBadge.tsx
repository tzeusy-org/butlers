/**
 * StatusBadge — color-coded badge for ingestion event lifecycle status.
 *
 * Status → color mapping:
 * - ingested      → green (default/success)
 * - skipped       → muted outline (stored but not dispatched — skip triage rule)
 * - filtered      → gray (secondary)
 * - error         → red (destructive)
 * - replay_pending → blue (custom)
 * - replay_complete → green outline
 * - replay_failed  → red outline
 *
 * For filtered and error statuses, wraps in a Tooltip showing filter_reason.
 * For error status, also appends error_detail when available.
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
  errorDetail?: string | null;
}

const STATUS_LABELS: Record<IngestionEventStatus, string> = {
  ingested: "ingested",
  skipped: "skipped",
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
    case "skipped":
      return (
        <Badge variant="outline" className="text-muted-foreground">
          {STATUS_LABELS.skipped}
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

export function StatusBadge({ status, filterReason, errorDetail }: StatusBadgeProps) {
  const hasTooltipContent =
    (status === "filtered" || status === "error") &&
    (!!filterReason || (status === "error" && !!errorDetail));

  if (!hasTooltipContent) {
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
          {filterReason && <p className="max-w-xs text-xs">{filterReason}</p>}
          {status === "error" && errorDetail && (
            <p className="max-w-xs text-xs">{errorDetail}</p>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// RowStatus — quiet dot + status word for dense ledger rows (bu-4utdw.4)
// ---------------------------------------------------------------------------
//
// The ledger row grid has no room for a filled pill, and the dispatch design
// language forbids background-color fills in rows (state colors are
// foreground/border only — the hour strip and dispatch ticks are the
// sanctioned data-viz color carriers). This renders a 6px dot plus a mono
// status word instead of a <Badge>.
//
// Word choice deliberately matches the badge vocabulary listed in bu-4utdw.4
// exactly ("replay complete", not the historical StatusBadge/STATUS_LABELS
// "replayed") — the two word lists are intentionally decoupled so this
// vocabulary correction doesn't perturb StatusBadge's own callers/tests.
//
// Exported so the Timeline toolbar's status filter chips (bu-4utdw.5) can
// import this exact map instead of maintaining a second, driftable word
// list — "one vocabulary" is enforced by sharing the constant, not by
// convention.
// eslint-disable-next-line react-refresh/only-export-components
export const ROW_STATUS_WORDS: Record<IngestionEventStatus, string> = {
  ingested: "ingested",
  skipped: "skipped",
  filtered: "filtered",
  error: "error",
  replay_pending: "replay pending",
  replay_complete: "replay complete",
  replay_failed: "replay failed",
};

interface RowStatusStyle {
  /** Dot fill/border classes. Filled dots for primary states; hollow (border-only) for secondary/noise states. */
  dot: string;
  /** Foreground text color class. */
  text: string;
}

const ROW_STATUS_STYLE: Record<IngestionEventStatus, RowStatusStyle> = {
  ingested: { dot: "bg-emerald-500", text: "text-emerald-600" },
  skipped: { dot: "border border-muted-foreground/40", text: "text-muted-foreground" },
  filtered: { dot: "border border-muted-foreground/40", text: "text-muted-foreground" },
  error: { dot: "bg-destructive", text: "text-destructive" },
  replay_pending: { dot: "border border-blue-500", text: "text-blue-600" },
  replay_complete: { dot: "border border-emerald-500", text: "text-emerald-600" },
  replay_failed: { dot: "border border-destructive", text: "text-destructive" },
};

export interface RowStatusProps {
  status: IngestionEventStatus;
  className?: string;
}

/** Quiet 6px-dot + mono status word for ledger rows. Never a filled pill. */
export function RowStatus({ status, className }: RowStatusProps) {
  const style = ROW_STATUS_STYLE[status];
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 font-mono text-[11px] whitespace-nowrap",
        style.text,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      data-testid="row-status"
      data-status={status}
    >
      <span className={["inline-block size-1.5 rounded-full shrink-0", style.dot].join(" ")} aria-hidden />
      {ROW_STATUS_WORDS[status]}
    </span>
  );
}
