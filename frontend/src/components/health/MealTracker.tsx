// ---------------------------------------------------------------------------
// MealTracker — direct add/edit/delete for logged meals [bu-5oeoq]
//
// Mirrors SymptomTracker (bu-gk38e): a list surface with a "Log meal" toolbar
// affordance, per-row Edit / Delete actions, a delete confirmation dialog, and
// an add/edit dialog wrapping the shared MealForm. All writes go through the
// /api/health/meals fact-store path, so dashboard edits and butler edits stay
// in sync.
//
// Meals are TEMPORAL facts (eating log): the meal-type + date-range filters
// from the original view-only page are preserved here.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { Meal, MealParams } from "@/api/types";
import { MealForm } from "@/components/health/MealForm";
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
import { Input } from "@/components/ui/input";
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
import { useDeleteMeal, useMeals } from "@/hooks/use-health";

const PAGE_SIZE = 50;

const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"] as const;

/** Format nutrition JSONB as a short string. */
function formatNutrition(nutrition: Record<string, unknown> | null): string {
  if (!nutrition) return "—";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(nutrition)) {
    if (v == null) continue;
    parts.push(`${k}: ${v}`);
  }
  return parts.join(", ") || "—";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No meals found."
      description="Log a meal with the button above, or record one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// MealRow — a single meal with edit/delete affordances
// ---------------------------------------------------------------------------

function MealRow({ meal, onEdit }: { meal: Meal; onEdit: (meal: Meal) => void }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteMeal();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(meal.id);
      toast.success("Meal deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete meal.");
    }
  }

  return (
    <TableRow>
      <TableCell>
        <Badge variant="outline" className="text-xs capitalize">
          {meal.type}
        </Badge>
      </TableCell>
      <TableCell className="max-w-xs truncate font-medium text-sm">
        {meal.description}
      </TableCell>
      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
        {formatNutrition(meal.nutrition)}
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        <Time value={meal.eaten_at} mode="absolute" />
      </TableCell>
      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
        {meal.notes ?? "—"}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onEdit(meal)}
            aria-label={`Edit ${meal.description}`}
          >
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`Delete ${meal.description}`}
          >
            Delete
          </Button>
        </div>
      </TableCell>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {meal.description}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes this meal entry from your meal log. The record is
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
// MealTracker
// ---------------------------------------------------------------------------

export default function MealTracker() {
  const [typeFilter, setTypeFilter] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a Meal = edit mode.
  const [formTarget, setFormTarget] = useState<Meal | null | undefined>(null);

  const params: MealParams = {
    type: typeFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useMeals(params);

  const meals = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  return (
    <div className="space-y-4">
      {/* Toolbar: filters + add affordance */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge
              variant={typeFilter === "" ? "default" : "outline"}
              className="cursor-pointer"
              onClick={() => {
                setTypeFilter("");
                setPage(0);
              }}
            >
              All
            </Badge>
            {MEAL_TYPES.map((t) => (
              <Badge
                key={t}
                variant={typeFilter === t ? "default" : "outline"}
                className="cursor-pointer capitalize"
                onClick={() => {
                  setTypeFilter(typeFilter === t ? "" : t);
                  setPage(0);
                }}
              >
                {t}
              </Badge>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-sm">From</label>
            <Input
              type="date"
              value={since}
              onChange={(e) => {
                setSince(e.target.value);
                setPage(0);
              }}
              className="w-40"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-sm">To</label>
            <Input
              type="date"
              value={until}
              onChange={(e) => {
                setUntil(e.target.value);
                setPage(0);
              }}
              className="w-40"
            />
          </div>
          {(typeFilter || since || until) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTypeFilter("");
                setSince("");
                setUntil("");
                setPage(0);
              }}
            >
              Clear
            </Button>
          )}
        </div>
        <Button size="sm" onClick={() => setFormTarget(undefined)}>
          Log meal
        </Button>
      </div>

      {!isLoading && meals.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Nutrition</TableHead>
              <TableHead>Time</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              meals.map((meal) => (
                <MealRow key={meal.id} meal={meal} onEdit={setFormTarget} />
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
            <DialogTitle>{editing ? "Edit meal" : "Log meal"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this meal entry's details."
                : "Log a meal. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <MealForm
            meal={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
