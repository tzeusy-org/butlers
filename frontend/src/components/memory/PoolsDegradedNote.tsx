// ---------------------------------------------------------------------------
// PoolsDegradedNote — names the memory pools a fan-out list dropped (bu-kqnum.14.5)
//
// The episode / fact / rule / inspect routes fan out across butler pools and,
// when one errors, drop it and name it in `meta.pools_failed` rather than failing
// the request. A page built from the surviving pools (even an empty one) is then
// incomplete, so it must say so instead of reading as a calm empty ledger.
// Renders nothing when every pool answered.
// ---------------------------------------------------------------------------

import { SourceDegradedNote } from "@/components/ui/query-boundary";

export interface PoolsDegradedNoteProps {
  /** What the list is, e.g. "Facts" or "Search results". */
  label: string;
  /** `meta.pools_failed` from the list response; absent or empty renders nothing. */
  poolsFailed?: string[];
  onRetry?: () => void;
  testId: string;
}

export function PoolsDegradedNote({ label, poolsFailed, onRetry, testId }: PoolsDegradedNoteProps) {
  if (!poolsFailed || poolsFailed.length === 0) return null;
  return (
    <SourceDegradedNote
      label={label}
      detail={`${poolsFailed.join(", ")} unreachable. Results may be incomplete`}
      onRetry={onRetry}
      testId={testId}
    />
  );
}
