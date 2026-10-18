// ---------------------------------------------------------------------------
// QuestionsRegister — what the butlers still cannot answer (Band 3, register=questions)
//
// Owner questions no butler could answer yet, in the ledger's row language:
//
//   asked most                                                 (quiet mono list)
//   favorite_coffee · 3 asks · 2 open
//   ─────────────────────────────────────────────────────────────────────
//   What does Wei drink in the morning?        open · never looked   2 threads
//   Wei · favorite_coffee                      asked Jun 12, 2026       dismiss
//
// Rows are open plus answerable gaps, newest first, paged by keyset cursor. The
// per-predicate demand summary is aggregated server-side, because a count over
// the loaded pages is wrong past the first one. Coverage is words, never enums:
// "never looked", "looked, nothing there", "unavailable", "answered".
//
// Failure is never calm: a failed list reads "Couldn't load the questions", a pool
// that dropped out of the merge is named, and a failed demand fetch hides the
// summary behind its own line instead of reading as zero demand. Dismiss is an
// underlined word with an inline confirm; the list is refetched afterwards (also
// after a 404/409), so the row shows the gap's current state, never a ghost.
//
// Color discipline: this register renders no red/amber/green pixels of its own;
// the only state color is the shared load-error line.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { Link } from "react-router";

import { MemoryLoadError } from "@/components/memory/MemoryLoadError";
import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { Time } from "@/components/ui/time";
import { Voice } from "@/components/ui/Voice";
import { useDismissMemoryGap, useMemoryGapDemand, useMemoryGaps } from "@/hooks/use-memory-gaps";
import {
  coverageWord,
  demandDegradedSources,
  demandRowsOf,
  gapDegradedSources,
  gapRows,
} from "@/lib/memory-gaps";
import { formatNumeral } from "@/lib/memory-overture";
import { cn } from "@/lib/utils";
import type { MemoryGap, MemoryGapDemandRow } from "@/api/memory-gaps.ts";

// ---------------------------------------------------------------------------
// Words
// ---------------------------------------------------------------------------

function plural(n: number, one: string, many: string): string {
  return `${formatNumeral(n)} ${n === 1 ? one : many}`;
}

// ---------------------------------------------------------------------------
// Demand summary
// ---------------------------------------------------------------------------

function DemandSummary({ rows }: { rows: MemoryGapDemandRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div data-testid="questions-demand" className="flex flex-col gap-1">
      <Mono muted>asked most</Mono>
      {rows.map((row) => (
        <Mono key={row.predicate}>
          {row.predicate}
          <span className="text-[var(--mfg)]">
            {" · "}
            {plural(row.asks, "ask", "asks")}
            {" · "}
            {formatNumeral(row.open_gaps)} open
          </span>
        </Mono>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function DismissAction({ gap }: { gap: MemoryGap }) {
  const [confirming, setConfirming] = useState(false);
  const dismiss = useDismissMemoryGap();

  if (dismiss.isError) {
    // The refetch on settle shows the gap's real state; the failure stays named.
    return (
      <span className="flex items-baseline gap-2 font-mono text-[11px]">
        <span role="alert" className="text-[var(--red-text)]">
          dismiss failed
        </span>
        <button
          type="button"
          onClick={() => {
            dismiss.reset();
            setConfirming(true);
          }}
          className="underline [text-underline-offset:4px] hover:text-fg"
        >
          try again
        </button>
      </span>
    );
  }
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="font-mono text-[11px] underline [text-underline-offset:4px] hover:text-fg"
        aria-label={`Dismiss question: ${gap.question}`}
      >
        dismiss
      </button>
    );
  }
  return (
    <span className="flex items-baseline gap-2 font-mono text-[11px]">
      <button
        type="button"
        disabled={dismiss.isPending}
        onClick={() => dismiss.mutate(gap.gap_id, { onSettled: () => setConfirming(false) })}
        className="underline [text-underline-offset:4px] hover:text-fg disabled:opacity-60"
      >
        confirm dismiss
      </button>
      <button
        type="button"
        disabled={dismiss.isPending}
        onClick={() => setConfirming(false)}
        className="text-[var(--mfg)] underline [text-underline-offset:4px] hover:text-fg"
      >
        keep
      </button>
    </span>
  );
}

export function QuestionRow({ gap }: { gap: MemoryGap }) {
  const threads = gap.origins.length;
  return (
    <div
      data-testid="question-row"
      className={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-1",
        "border-b border-[var(--border-soft)] px-1 py-2.5 text-fg",
      )}
    >
      <span className="min-w-0 text-[13px] leading-snug">{gap.question}</span>
      <span className="flex items-baseline justify-end gap-1.5 whitespace-nowrap font-mono text-[11px]">
        <span>{gap.status}</span>
        <span className="text-[var(--mfg)]">{coverageWord(gap.coverage_state)}</span>
      </span>

      <span className="min-w-0 truncate text-[13px] leading-snug">
        <Link
          to={`/entities/${gap.entity_id}`}
          className="underline [text-underline-offset:4px] hover:text-fg"
        >
          {gap.entity_name ?? gap.entity_id.slice(0, 8)}
        </Link>
        <span className="px-1.5 font-mono text-[11px] text-[var(--mfg)]">·</span>
        <span className="font-mono text-[11px] text-[var(--mfg)]">{gap.predicate}</span>
      </span>
      <span className="flex items-baseline justify-end gap-3 whitespace-nowrap font-mono text-[11px] text-[var(--mfg)]">
        {gap.delivery_failed && <span>delivery failed</span>}
        <span>
          asked <Time value={gap.asked_at} mode="absolute" precision="day" showTitle={false} />
        </span>
        <span>{plural(threads, "thread", "threads")}</span>
        <DismissAction gap={gap} />
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QuestionsRegister
// ---------------------------------------------------------------------------

export default function QuestionsRegister() {
  const gaps = useMemoryGaps();
  const demand = useMemoryGapDemand();

  const rows = gapRows(gaps.data);
  const degraded = gapDegradedSources(gaps.data);
  const demandRows = demandRowsOf(demand.data);
  const demandDegraded = demandDegradedSources(demand.data);

  return (
    <div className="flex flex-col gap-4">
      {demand.isError ? (
        <Voice variant="italic" as="span" role="alert" className="text-[var(--red-text)]">
          Couldn&rsquo;t load which questions are asked most.
        </Voice>
      ) : (
        rows.length > 0 && <DemandSummary rows={demandRows} />
      )}

      {(degraded.length > 0 || demandDegraded.length > 0) && (
        <Voice variant="italic" role="status" data-testid="questions-degraded" className="py-1">
          Some butlers could not be read: {Array.from(new Set([...degraded, ...demandDegraded])).join(", ")}.
        </Voice>
      )}

      {gaps.isError && rows.length === 0 ? (
        // An errored fetch must not read as "No open questions." (bu-mkd5r).
        <MemoryLoadError
          label="the questions"
          onRetry={() => void gaps.refetch()}
          testId="memory-questions-error"
        />
      ) : rows.length === 0 ? (
        gaps.isPending ? null : (
          <Voice variant="italic" className="py-6">
            No open questions.
          </Voice>
        )
      ) : (
        <div className="flex flex-col">
          {rows.map((gap) => (
            <QuestionRow key={gap.gap_id} gap={gap} />
          ))}
        </div>
      )}

      {gaps.hasNextPage && (
        <div className="flex justify-end pt-1">
          <Pill
            selected={false}
            disabled={gaps.isFetchingNextPage}
            onClick={() => void gaps.fetchNextPage()}
          >
            more
          </Pill>
        </div>
      )}
    </div>
  );
}
