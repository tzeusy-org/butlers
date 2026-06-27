// ---------------------------------------------------------------------------
// ConditionTracker — direct add/edit/delete for health conditions [bu-a7vw9]
//
// Dispatch reframe [bu-w7b18.4]: the conditions surface is a Dispatch rule-list
// (status-dot · condition + status · onset-date), not a Card-wrapped data table.
// Each row carries per-row Edit / Delete actions, a delete confirmation dialog,
// and the add/edit dialog still wraps the shared ConditionForm. All writes go
// through the /api/health/conditions fact-store path, so dashboard edits and
// butler edits stay in sync.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { HealthCondition } from "@/api/types";
import { ConditionForm } from "@/components/health/ConditionForm";
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Time } from "@/components/ui/time";
import { useConditions, useDeleteCondition } from "@/hooks/use-health";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

// Status → single dot color. The dot replaces the old filled status badge:
// the colour alone carries the state, the word sits beside it in mono caps.
const STATUS_DOT: Record<string, string> = {
  active: "bg-[var(--severity-low)]", // green — currently ongoing
  managed: "bg-[var(--severity-medium)]", // amber — under management
  resolved: "bg-muted-foreground", // neutral — no longer active
};

function statusDotClass(status: string): string {
  return STATUS_DOT[status.toLowerCase()] ?? "bg-muted-foreground";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <div className="divide-y divide-border/60 border-y border-border/60">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="grid grid-cols-[10px_1fr_auto] items-start gap-3 py-3">
          <span className="bg-muted mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full" />
          <div className="space-y-1.5">
            <div className="bg-muted h-3.5 w-40 animate-pulse rounded" />
            <div className="bg-muted h-2.5 w-24 animate-pulse rounded" />
          </div>
          <div className="bg-muted h-7 w-24 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConditionRow — a single condition rule-list row with edit/delete affordances
// ---------------------------------------------------------------------------

function ConditionRow({
  condition,
  onEdit,
}: {
  condition: HealthCondition;
  onEdit: (condition: HealthCondition) => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteCondition();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(condition.id);
      toast.success("Condition deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete condition.");
    }
  }

  return (
    <div className="grid grid-cols-[10px_1fr_auto] items-start gap-3 py-3">
      <span
        className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", statusDotClass(condition.status))}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-foreground text-sm font-medium">{condition.name}</span>
          <span className="text-muted-foreground font-mono text-[10px] uppercase tracking-[0.1em]">
            {condition.status}
          </span>
        </div>
        <div className="text-muted-foreground mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 font-mono text-[10px] tabular-nums">
          <span>
            {condition.diagnosed_at ? (
              <>
                onset <Time value={condition.diagnosed_at} mode="absolute" precision="day" />
              </>
            ) : (
              "onset unknown"
            )}
          </span>
          {condition.notes && (
            <span className="text-muted-foreground/80 min-w-0 truncate font-sans normal-case">
              · {condition.notes}
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onEdit(condition)}
          aria-label={`Edit ${condition.name}`}
        >
          Edit
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive"
          onClick={() => setConfirmingDelete(true)}
          aria-label={`Delete ${condition.name}`}
        >
          Delete
        </Button>
      </div>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {condition.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes {condition.name} from your conditions list. The record is
              retained for history but will no longer appear here.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
              disabled={deleteMutation.isPending}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConditionTracker
// ---------------------------------------------------------------------------

export default function ConditionTracker() {
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a HealthCondition = edit mode.
  const [formTarget, setFormTarget] = useState<HealthCondition | null | undefined>(null);

  const { data, isLoading } = useConditions({
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const conditions = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  return (
    <div className="space-y-4">
      {/* Toolbar: add affordance */}
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground font-mono text-[10px] uppercase tracking-[0.14em]">
          Conditions{total > 0 ? ` · ${total.toLocaleString()}` : ""}
        </span>
        <Button size="sm" onClick={() => setFormTarget(undefined)}>
          Add condition
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows />
      ) : conditions.length === 0 ? (
        <p className="text-muted-foreground font-serif text-[15px] italic">
          Nothing on record yet. Add a condition above, or tell your Health butler.
        </p>
      ) : (
        <div className="divide-y divide-border/60 border-y border-border/60">
          {conditions.map((cond) => (
            <ConditionRow key={cond.id} condition={cond} onEdit={setFormTarget} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Add / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && setFormTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit condition" : "Add condition"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this condition's details."
                : "Add a condition to your record. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <ConditionForm
            condition={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
