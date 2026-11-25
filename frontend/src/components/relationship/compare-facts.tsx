/**
 * Fact presentation shared by the standalone merge review (MergeCompareDialog)
 * and the parked-merge approval review (components/approvals/entity-merge-review).
 * Deterministic rendering of server-computed compare rows only.
 */

import type { CompareFact } from "@/api/types";
import { Badge } from "@/components/ui/badge";

const STALENESS_BADGE_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  fresh: "secondary",
  aging: "outline",
  stale: "destructive",
};

function prettyPredicate(predicate: string): string {
  return predicate.replaceAll("-", " ").replaceAll("_", " ");
}

/**
 * The shared-evidence row that triggered this compare, when the entry point
 * carries it (a queue duplicate card or the detail-page duplicate panel). The
 * matching shared row is pre-highlighted so the operator sees the duplicate
 * evidence first. Matched deterministically on ``(predicate, object)``.
 */
export interface CompareHighlightFact {
  predicate: string;
  object: string;
}

function factMatchesHighlight(
  fact: CompareFact,
  highlight: CompareHighlightFact | null | undefined,
): boolean {
  return (
    highlight != null &&
    fact.predicate === highlight.predicate &&
    fact.object === highlight.object
  );
}

/** One fact row with predicate, object, and provenance badges. */
export function FactRow({
  fact,
  highlighted = false,
}: {
  fact: CompareFact;
  highlighted?: boolean;
}) {
  return (
    <li
      className={`flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs ${
        highlighted ? "text-[var(--amber-text)]" : ""
      }`}
      data-testid="compare-fact"
      data-highlighted={highlighted ? "true" : undefined}
    >
      <span
        className={`font-medium capitalize ${highlighted ? "text-[var(--amber-text)]" : "text-foreground"}`}
      >
        {prettyPredicate(fact.predicate)}
      </span>
      <span
        className={`truncate ${highlighted ? "text-[var(--amber-text)]" : "text-muted-foreground"}`}
        title={fact.object}
      >
        {fact.object}
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-1">
        {fact.verified && (
          <Badge variant="secondary" className="text-[10px]">
            verified
          </Badge>
        )}
        <Badge
          variant={STALENESS_BADGE_VARIANT[fact.staleness_band] ?? "outline"}
          className="text-[10px] capitalize"
        >
          {fact.staleness_band}
        </Badge>
        <span className="text-[10px] text-muted-foreground">{fact.src}</span>
      </span>
    </li>
  );
}

/** A labelled group of fact rows; renders nothing when empty unless `showEmpty`. */
export function FactGroup({
  label,
  count,
  facts,
  testid,
  emptyText,
  highlight,
}: {
  label: string;
  /** Optional tabular-nums count rendered after the label. */
  count?: number;
  facts: CompareFact[];
  testid: string;
  emptyText?: string;
  highlight?: CompareHighlightFact | null;
}) {
  if (facts.length === 0 && !emptyText) return null;
  return (
    <div data-testid={testid}>
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
        {count !== undefined && (
          <span className="ml-1 tabular-nums text-muted-foreground">{count}</span>
        )}
      </p>
      {facts.length === 0 ? (
        <p className="text-xs italic text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="space-y-1">
          {facts.map((fact) => (
            <FactRow
              key={`${fact.store}-${fact.id}`}
              fact={fact}
              highlighted={factMatchesHighlight(fact, highlight)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
