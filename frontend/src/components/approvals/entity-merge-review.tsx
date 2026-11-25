/**
 * Structural review of a parked entity merge (REQ-dashboard-approvals-902/903).
 *
 * A pending Relationship merge carries a typed `entity_merge_review` instead of
 * a generic call preview. This loads the existing owner-authenticated compare
 * view for exactly the stored pair and renders it with the direction fixed:
 * the source is absorbed, the target survives and keeps its type. There is no
 * survivor picker here; changing the survivor is a separate operation.
 *
 * Approval eligibility is keyed to (action, tool, digest, source, target): a
 * comparison for any other key, the inverse pair, or a loading/failed read
 * never unlocks this call. Confirmation goes through the original approval
 * (the caller's approve path with `displayed_call`), never the standalone
 * merge mutation, so the parked request is resolved and audited.
 */

import { useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import { ApiError } from "@/api/index.ts";
import type {
  CompareEntitiesResponse,
  CompareEntityBlock,
  EntityMergeReview,
  OwnerDeliveryCard,
} from "@/api/types";
import { FactGroup } from "@/components/relationship/compare-facts";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import type { ViewedApproval } from "@/hooks/use-approval-decisions";
import { useEntityMergeComparison } from "@/hooks/use-entity-merge-review";

const LABEL = "text-[10px] font-mono uppercase tracking-wider text-muted-foreground";

function comparisonError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "One of these entities no longer exists or was already merged or forgotten. This merge cannot be approved; Deny or Defer still work.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "The comparison is only available to the owner's dashboard session.";
  }
  return "The comparison could not be loaded. Approval stays unavailable until it does.";
}

function EntityHeader({ block, role }: { block: CompareEntityBlock; role: "source" | "target" }) {
  const { entity } = block;
  return (
    <div className="min-w-0 space-y-0.5">
      <p className={LABEL}>{role === "source" ? "Absorb source" : "Keep target"}</p>
      <p className="font-medium text-foreground break-words">{entity.canonical_name}</p>
      <p className="text-xs text-muted-foreground">
        <span className="font-mono">{entity.entity_type}</span>
        {entity.tier != null ? ` · tier ${entity.tier}` : ""}
        {role === "source" ? " · will be tombstoned" : " · survives"}
      </p>
      <code className="block font-mono text-[11px] text-muted-foreground break-all">
        {entity.id}
      </code>
      {entity.aliases.length > 0 && (
        <p className="text-xs text-muted-foreground break-words">
          aka {entity.aliases.join(", ")}
        </p>
      )}
    </div>
  );
}

function EntityColumn({ block, role }: { block: CompareEntityBlock; role: "source" | "target" }) {
  return (
    <div
      className={`min-w-0 flex-1 space-y-3 rounded-md border p-3 ${
        role === "target" ? "border-foreground/40" : "border-border"
      }`}
      data-testid={`merge-review-${role}`}
    >
      <EntityHeader block={block} role={role} />
      <FactGroup
        label="Identity facts"
        facts={block.identity_facts}
        testid={`merge-review-identity-${role}`}
        emptyText="No identity facts."
      />
      <FactGroup
        label="Narrative facts"
        facts={block.narrative_facts}
        testid={`merge-review-narrative-${role}`}
      />
    </div>
  );
}

/** The comparison body for one parked merge, with loading/error/retry states. */
export function EntityMergeReviewPanel({
  comparison,
}: {
  comparison: UseQueryResult<CompareEntitiesResponse>;
}) {
  const diff = comparison.data;
  return (
    <section
      aria-labelledby="entity-merge-review-title"
      className="mb-6 space-y-4"
      data-testid="entity-merge-review"
    >
      <h3 id="entity-merge-review-title" className="font-medium">
        Review merge
      </h3>
      {comparison.isPending && (
        <div className="space-y-2" data-testid="merge-review-loading" aria-busy="true">
          <p className="text-sm text-muted-foreground">Loading the comparison…</p>
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}
      {comparison.isError && (
        <div className="space-y-2" data-testid="merge-review-error">
          <p className="text-sm text-destructive" role="alert">
            {comparisonError(comparison.error)}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={comparison.isFetching}
            onClick={() => void comparison.refetch()}
          >
            {comparison.isFetching ? "Retrying…" : "Retry comparison"}
          </Button>
        </div>
      )}
      {diff && (
        <>
          <p className="text-sm">
            Approving merges <strong>{diff.a.entity.canonical_name}</strong> into{" "}
            <strong>{diff.b.entity.canonical_name}</strong>. The direction is fixed by the stored
            request.
          </p>
          <div className="flex flex-col gap-3 md:flex-row">
            <EntityColumn block={diff.a} role="source" />
            <EntityColumn block={diff.b} role="target" />
          </div>
          <div className="border-t border-border pt-3">
            <FactGroup
              label="Shared evidence"
              count={diff.shared.length}
              facts={diff.shared}
              testid="merge-review-shared"
              emptyText="No shared identifiers."
            />
          </div>
          <div className="border-t border-border pt-3">
            <FactGroup
              label="Divergences"
              count={diff.divergent.length}
              facts={diff.divergent}
              testid="merge-review-divergent"
              emptyText="No conflicting facts."
            />
          </div>
          <div className="border-t border-border pt-3 text-sm" data-testid="merge-review-consequences">
            <p className={`${LABEL} mb-1`}>If approved</p>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>
                The source is tombstoned. Its facts, aliases and metadata move to the target under
                the existing conflict rules.
              </li>
              <li>
                The surviving record stays a <span className="font-mono">{diff.b.entity.entity_type}</span>
                {diff.a.entity.entity_type !== diff.b.entity.entity_type
                  ? ` (the source is a ${diff.a.entity.entity_type}; its type is not kept).`
                  : "."}
              </li>
              <li>
                Other butlers' references are rebound afterwards; some may still be pending when
                the approval completes.
              </li>
              <li>A matching name is a candidate, not proof that these are the same.</li>
            </ul>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * Final, accessible merge confirmation. Cancel, Escape and closing issue no
 * request; Confirm fires once and is disabled while the approval is in flight.
 */
export function EntityMergeConfirmDialog({
  open,
  diff,
  submitting,
  onConfirm,
  onOpenChange,
}: {
  open: boolean;
  diff: CompareEntitiesResponse | undefined;
  submitting: boolean;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}) {
  if (!diff) return null;
  const source = diff.a.entity;
  const target = diff.b.entity;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent data-testid="merge-approval-confirmation">
        <AlertDialogHeader>
          <AlertDialogTitle>
            Merge {source.canonical_name} into {target.canonical_name}?
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm">
              <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
                <dt className="text-muted-foreground">Absorbed</dt>
                <dd className="break-words">
                  {source.canonical_name} · {source.entity_type} ·{" "}
                  <code className="font-mono text-xs break-all">{source.id}</code>
                </dd>
                <dt className="text-muted-foreground">Survives</dt>
                <dd className="break-words">
                  {target.canonical_name} · {target.entity_type} ·{" "}
                  <code className="font-mono text-xs break-all">{target.id}</code>
                </dd>
              </dl>
              <p>
                The source is tombstoned and the survivor stays a {target.entity_type}. This
                approves the original request; it cannot be undone from here.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel data-testid="merge-approval-cancel" disabled={submitting}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            data-testid="merge-approval-confirm"
            disabled={submitting}
            onClick={(event) => {
              event.preventDefault();
              if (!submitting) onConfirm();
            }}
          >
            {submitting ? "Approving…" : "Confirm merge"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * Summary-surface review (Overview attention list): the same comparison and
 * confirmation as the dossier, in a dialog. Confirm hands back the reviewed call.
 */
export function EntityMergeSummaryReview({
  id,
  review,
  ownerDelivery,
  onConfirm,
  onCancel,
}: {
  id: string;
  review: EntityMergeReview;
  ownerDelivery?: OwnerDeliveryCard | null;
  onConfirm: (view: ViewedApproval) => void;
  onCancel: () => void;
}) {
  const comparison = useEntityMergeComparison(id, review);
  const [confirming, setConfirming] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const ready = comparison.isSuccess && !comparison.isFetching;
  return (
    <>
      <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Review merge before approving</DialogTitle>
            <DialogDescription>
              Compare both records. The stored direction cannot be changed here.
            </DialogDescription>
          </DialogHeader>
          <EntityMergeReviewPanel comparison={comparison} />
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={onCancel}>Cancel</Button>
            <Button disabled={!ready} onClick={() => setConfirming(true)}>
              Approve merge…
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <EntityMergeConfirmDialog
        open={confirming && ready}
        diff={comparison.data}
        submitting={submitted}
        onOpenChange={setConfirming}
        onConfirm={() => {
          setSubmitted(true);
          onConfirm({
            id,
            displayed_call: { ...review.displayed_call },
            owner_delivery: ownerDelivery,
          });
        }}
      />
    </>
  );
}
