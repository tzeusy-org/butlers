// ---------------------------------------------------------------------------
// ConditionTracker — direct add/edit/delete for health conditions [bu-a7vw9]
//
// Mirrors MedicationTracker (bu-aisjm): a list surface with an "Add condition"
// toolbar affordance, per-row Edit / Delete actions, a delete confirmation
// dialog, and an add/edit dialog wrapping the shared ConditionForm. All writes
// go through the /api/health/conditions fact-store path, so dashboard edits and
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Time } from "@/components/ui/time";
import { useConditions, useDeleteCondition } from "@/hooks/use-health";

const PAGE_SIZE = 50;

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/15 text-green-700 dark:text-green-400",
  resolved: "bg-gray-500/15 text-gray-600 dark:text-gray-400",
  managed: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No conditions found."
      description="Add a condition with the button above, or log one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// ConditionRow — a single condition with edit/delete affordances
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
    <TableRow>
      <TableCell className="font-medium">{condition.name}</TableCell>
      <TableCell>
        <Badge
          variant="secondary"
          className={STATUS_COLORS[condition.status.toLowerCase()] ?? ""}
        >
          {condition.status}
        </Badge>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {condition.diagnosed_at
          ? <Time value={condition.diagnosed_at} mode="absolute" precision="day" />
          : "—"}
      </TableCell>
      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
        {condition.notes ?? "—"}
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        <Time value={condition.updated_at} mode="absolute" precision="day" />
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
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
      </TableCell>

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
    </TableRow>
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
      <div className="flex items-center justify-end">
        <Button size="sm" onClick={() => setFormTarget(undefined)}>
          Add condition
        </Button>
      </div>

      {!isLoading && conditions.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Diagnosed</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              conditions.map((cond) => (
                <ConditionRow key={cond.id} condition={cond} onEdit={setFormTarget} />
              ))
            )}
          </TableBody>
        </Table>
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
