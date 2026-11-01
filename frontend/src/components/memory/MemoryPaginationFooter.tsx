// ---------------------------------------------------------------------------
// MemoryPaginationFooter — the one pager shared by the three registers and
// search (bu-kqnum.14.5)
//
// `1–50 of 312` left, prev/next pills right (mono dim, no numbered pages).
// When a pool dropped out (`partial`) the total undercounts, so the range says
// "(partial)" rather than reading as the whole of memory.
// ---------------------------------------------------------------------------

import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { formatNumeral } from "@/lib/memory-overture";

export interface MemoryPaginationFooterProps {
  offset: number;
  /** Rows on the current page. */
  count: number;
  /** Exact total across reachable sources. */
  total: number;
  hasMore: boolean;
  pageSize: number;
  /** True when a pool dropped out, so the total undercounts. */
  partial: boolean;
  onOffset: (next: number) => void;
  /** Text before the range, e.g. "showing ". */
  prefix?: string;
  testId?: string;
}

export function MemoryPaginationFooter({
  offset,
  count,
  total,
  hasMore,
  pageSize,
  partial,
  onOffset,
  prefix = "",
  testId,
}: MemoryPaginationFooterProps) {
  if (total === 0) return null;

  // Paged past the last row: there is no range to show, only the total.
  const range =
    count === 0
      ? "0"
      : `${formatNumeral(offset + 1)}–${formatNumeral(offset + count)}`;

  return (
    <div className="flex items-baseline justify-between pt-1" data-testid={testId}>
      <Mono muted>
        {prefix}
        {range} of {formatNumeral(total)}
        {partial ? " (partial)" : ""}
      </Mono>
      <div className="flex gap-1.5">
        <Pill
          selected={false}
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - pageSize))}
        >
          prev
        </Pill>
        <Pill selected={false} disabled={!hasMore} onClick={() => onOffset(offset + pageSize)}>
          next
        </Pill>
      </div>
    </div>
  );
}
