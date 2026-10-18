// ---------------------------------------------------------------------------
// EntityStillUnknown: the "Still unknown" section of an entity's detail page.
//
// The owner's open questions about this one entity (question, predicate, asked date,
// coverage in words), newest first, with the register's confirm-to-dismiss action. The
// entity filter is applied by the server, so the list is right past the first page.
//
// Quiet by construction: no gaps means no section at all (no empty chrome on every
// entity page). A failed fetch is named, and a pool that dropped out of the merge is
// named as well, never read as "nothing unknown". An answerable gap reads "answered,
// notice pending": the answer exists and the delivery has not landed yet.
// ---------------------------------------------------------------------------

import { DismissAction } from "@/components/memory/QuestionsRegister";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import { useMemoryGaps } from "@/hooks/use-memory-gaps";
import { coverageWord, gapDegradedSources, gapRows } from "@/lib/memory-gaps";
import type { MemoryGap } from "@/api/memory-gaps.ts";

function StillUnknownRow({ gap }: { gap: MemoryGap }) {
  const answered = gap.status === "answerable";
  return (
    <li data-testid="still-unknown-row" className="flex flex-col gap-1 py-2.5">
      <span className="text-sm leading-snug">{gap.question}</span>
      <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
        <span>{gap.predicate}</span>
        <span>{answered ? "answered, notice pending" : coverageWord(gap.coverage_state)}</span>
        {gap.delivery_failed && <span>delivery failed</span>}
        <span>
          asked <Time value={gap.asked_at} mode="absolute" precision="day" showTitle={false} />
        </span>
        <DismissAction gap={gap} />
      </span>
    </li>
  );
}

export default function EntityStillUnknown({ entityId }: { entityId: string }) {
  const gaps = useMemoryGaps({ entityId });

  if (gaps.isPending) return null;
  const rows = gapRows(gaps.data);
  const degraded = gapDegradedSources(gaps.data);

  if (gaps.isError && rows.length === 0) {
    // A failed fetch must not vanish as "nothing is unknown".
    return (
      <SourceDegradedNote
        testId="entity-still-unknown-error"
        label="Still unknown"
        onRetry={() => void gaps.refetch()}
      />
    );
  }
  if (rows.length === 0 && degraded.length === 0) return null;

  return (
    <section className="space-y-3" data-testid="entity-still-unknown">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold">Still unknown</h2>
        <span className="text-muted-foreground text-xs">{rows.length}</span>
      </div>
      {degraded.length > 0 && (
        <p role="status" data-testid="entity-still-unknown-degraded" className="text-xs italic">
          Some butlers could not be read: {degraded.join(", ")}.
        </p>
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-border border-y">
          {rows.map((gap) => (
            <StillUnknownRow key={gap.gap_id} gap={gap} />
          ))}
        </ul>
      )}
      {gaps.hasNextPage && (
        <button
          type="button"
          disabled={gaps.isFetchingNextPage}
          onClick={() => void gaps.fetchNextPage()}
          className="font-mono text-[11px] underline [text-underline-offset:4px]"
        >
          more
        </button>
      )}
    </section>
  );
}
