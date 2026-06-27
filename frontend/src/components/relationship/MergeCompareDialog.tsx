/**
 * MergeCompareDialog — the single-pair merge-review compare view.
 *
 * The one surface every entity-merge entry point routes through
 * (relationship-merge-review "Single-pair review UX"): the queue's
 * duplicate-candidate / unidentified cards, the Index bulk gutter (exactly two
 * rows), the Workbench duplicate-warning panel, and the detail-page `m` key.
 *
 * On open it POSTs /api/relationship/entities/compare and renders the
 * server-computed structural diff two-column:
 *   - shared evidence (identical identity-store rows = the duplicate evidence),
 *   - divergences (single-cardinality predicate conflicts a merge must resolve),
 *   - per-entity identity + narrative facts with full provenance.
 *
 * Commit actions: `merge` (choosing the surviving entity) and `dismiss`. No
 * merge may be committed without this view having rendered the diff first
 * (spec: "no merge bypasses review"). No scoring, ranking, or generated text —
 * the diff is purely deterministic and server-computed.
 *
 * Spec: openspec/changes/entity-v3-lifecycle-and-depth/specs/relationship-merge-review/spec.md
 */

import { useEffect, useState } from "react";
import { GitMergeIcon, Loader2Icon } from "lucide-react";
import { toast } from "sonner";

import type { CompareEntitiesResponse, CompareEntityBlock, CompareFact } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCompareEntities,
  useDismissEntityPair,
  useMergeRelationshipEntities,
} from "@/hooks/use-entities";

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
function FactRow({
  fact,
  highlighted = false,
}: {
  fact: CompareFact;
  highlighted?: boolean;
}) {
  return (
    <li
      className={`flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs ${
        highlighted ? "text-[var(--amber)]" : ""
      }`}
      data-testid="compare-fact"
      data-highlighted={highlighted ? "true" : undefined}
    >
      <span
        className={`font-medium capitalize ${highlighted ? "text-[var(--amber)]" : "text-foreground"}`}
      >
        {prettyPredicate(fact.predicate)}
      </span>
      <span
        className={`truncate ${highlighted ? "text-[var(--amber)]" : "text-muted-foreground"}`}
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
function FactGroup({
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

/** One entity column (the `a` or `b` block), selectable as the merge survivor. */
function EntityColumn({
  block,
  side,
  selected,
  onSelect,
}: {
  block: CompareEntityBlock;
  side: "A" | "B";
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      className={`flex-1 space-y-3 rounded-md border p-3 ${selected ? "border-primary ring-1 ring-primary" : "border-border"}`}
      data-testid={`compare-column-${side}`}
    >
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="radio"
          name="merge-survivor"
          className="mt-1"
          checked={selected}
          onChange={onSelect}
          aria-label={`Keep ${block.entity.canonical_name}`}
        />
        <span className="min-w-0">
          <span className="block truncate font-medium text-foreground">
            {block.entity.canonical_name}
          </span>
          <span className="text-xs text-muted-foreground">
            {block.entity.entity_type}
            {block.entity.tier != null ? ` · tier ${block.entity.tier}` : ""}
            {selected ? " · survives" : ""}
          </span>
          {block.entity.aliases.length > 0 && (
            <span className="block truncate text-xs text-muted-foreground">
              aka {block.entity.aliases.join(", ")}
            </span>
          )}
        </span>
      </label>
      <FactGroup
        label="Identity facts"
        facts={block.identity_facts}
        testid={`compare-identity-${side}`}
        emptyText="No identity facts."
      />
      <FactGroup
        label="Narrative facts"
        facts={block.narrative_facts}
        testid={`compare-narrative-${side}`}
      />
    </div>
  );
}

export interface MergeCompareDialogProps {
  /** The pair under review. ``null`` closes the dialog. */
  pair: { entityA: string; entityB: string } | null;
  onOpenChange: (open: boolean) => void;
  /** Fired after a successful merge or dismiss so callers can clear selection. */
  onResolved?: () => void;
  /**
   * The shared-evidence row that triggered this compare, if the entry point
   * carries it. The matching row in the shared-evidence group is pre-highlighted
   * so the duplicate evidence reads first. Matched on ``(predicate, object)``.
   */
  highlightFact?: CompareHighlightFact | null;
}

export function MergeCompareDialog({
  pair,
  onOpenChange,
  onResolved,
  highlightFact,
}: MergeCompareDialogProps) {
  const compare = useCompareEntities();
  const merge = useMergeRelationshipEntities();
  const dismiss = useDismissEntityPair();
  const [diff, setDiff] = useState<CompareEntitiesResponse | null>(null);
  const [keepAs, setKeepAs] = useState<"A" | "B">("A");
  const [error, setError] = useState<string | null>(null);

  const compareReset = compare.reset;
  // Fetch the structural diff whenever a new pair opens. The compare view MUST
  // render before any merge can be committed (spec: "no merge bypasses review").
  useEffect(() => {
    if (!pair) return;
    setDiff(null);
    setKeepAs("A");
    setError(null);
    let cancelled = false;
    compare
      .mutateAsync({ entity_a: pair.entityA, entity_b: pair.entityB })
      .then((res) => {
        if (!cancelled) setDiff(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load comparison.");
        }
      });
    return () => {
      cancelled = true;
      compareReset();
    };
    // The pair identity is the trigger; mutate fns are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pair?.entityA, pair?.entityB]);

  function handleClose(open: boolean) {
    onOpenChange(open);
  }

  async function handleMerge() {
    if (!pair || !diff) return;
    try {
      const survivor = keepAs === "A" ? diff.a.entity.canonical_name : diff.b.entity.canonical_name;
      const absorbed = keepAs === "A" ? diff.b.entity.canonical_name : diff.a.entity.canonical_name;
      await merge.mutateAsync({ entityA: pair.entityA, entityB: pair.entityB, keepAs });
      toast.success(`Merged ${absorbed} into ${survivor}`);
      onResolved?.();
      handleClose(false);
    } catch (err) {
      toast.error(`Merge failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  async function handleDismiss() {
    if (!pair) return;
    try {
      await dismiss.mutateAsync({ entity_a: pair.entityA, entity_b: pair.entityB });
      toast.success("Dismissed. Pair removed from the duplicate queue.");
      onResolved?.();
      handleClose(false);
    } catch (err) {
      toast.error(`Dismiss failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  const pending = merge.isPending || dismiss.isPending;

  return (
    <Dialog open={pair !== null} onOpenChange={handleClose}>
      <DialogContent className="max-w-3xl" data-testid="merge-compare-dialog">
        <DialogHeader>
          <DialogTitle>Review merge</DialogTitle>
          <DialogDescription>
            Compare the two entities, choose which one survives, then merge, or dismiss the pair if
            they are not duplicates.
          </DialogDescription>
        </DialogHeader>

        {compare.isPending && !diff && !error && (
          <div className="space-y-2" data-testid="compare-loading">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}

        {diff && (
          <div
            className="max-h-[60vh] space-y-4 overflow-y-auto pr-1"
            data-testid="compare-body"
          >
            <div className="flex gap-3">
              <EntityColumn
                block={diff.a}
                side="A"
                selected={keepAs === "A"}
                onSelect={() => setKeepAs("A")}
              />
              <EntityColumn
                block={diff.b}
                side="B"
                selected={keepAs === "B"}
                onSelect={() => setKeepAs("B")}
              />
            </div>

            <div className="border-t border-border pt-3">
              <FactGroup
                label="Shared evidence"
                count={diff.shared.length}
                facts={diff.shared}
                testid="compare-shared"
                emptyText="No shared identifiers."
                highlight={highlightFact}
              />
            </div>

            <div className="border-t border-border pt-3">
              <FactGroup
                label="Divergences"
                count={diff.divergent.length}
                facts={diff.divergent}
                testid="compare-divergent"
                emptyText="No conflicting facts."
              />
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            type="button"
            variant="outline"
            disabled={pending || !diff}
            onClick={handleDismiss}
            data-testid="compare-dismiss"
          >
            {dismiss.isPending ? "Dismissing..." : "Not a duplicate, dismiss"}
          </Button>
          <Button
            type="button"
            disabled={pending || !diff}
            onClick={handleMerge}
            data-testid="compare-merge"
          >
            {merge.isPending ? (
              <Loader2Icon className="animate-spin" />
            ) : (
              <GitMergeIcon />
            )}
            {merge.isPending ? "Merging..." : "Merge"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
