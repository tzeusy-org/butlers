// ---------------------------------------------------------------------------
// SymptomTracker — direct add/edit/delete for logged symptoms [bu-gk38e]
//
// Mirrors ConditionTracker (bu-a7vw9): a list surface with a "Log symptom"
// toolbar affordance, per-row Edit / Delete actions, a delete confirmation
// dialog, and an add/edit dialog wrapping the shared SymptomForm. All writes
// go through the /api/health/symptoms fact-store path, so dashboard edits and
// butler edits stay in sync.
//
// Symptoms are TEMPORAL facts (occurrence log): the name + date-range filters
// from the original view-only page are preserved here.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { Symptom, SymptomParams } from "@/api/types";
import { SymptomForm } from "@/components/health/SymptomForm";
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
import { useDeleteSymptom, useSymptoms } from "@/hooks/use-health";

const PAGE_SIZE = 50;

/** Return a color for the severity 1-10 scale. */
function severityColor(severity: number): string {
  if (severity <= 3) return "var(--severity-low)";
  if (severity <= 6) return "var(--severity-medium)";
  return "var(--severity-high)";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
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
      title="No symptoms found."
      description="Log a symptom with the button above, or record one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// SymptomRow — a single symptom with edit/delete affordances
// ---------------------------------------------------------------------------

function SymptomRow({
  symptom,
  onEdit,
}: {
  symptom: Symptom;
  onEdit: (symptom: Symptom) => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteSymptom();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(symptom.id);
      toast.success("Symptom deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete symptom.");
    }
  }

  return (
    <TableRow>
      <TableCell className="font-medium">{symptom.name}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <div className="bg-muted h-2 w-16 overflow-hidden rounded-full">
            <div
              className="h-full rounded-full"
              style={{
                width: `${(symptom.severity / 10) * 100}%`,
                backgroundColor: severityColor(symptom.severity),
              }}
            />
          </div>
          <span className="text-sm tabular-nums">{symptom.severity}/10</span>
        </div>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        <Time value={symptom.occurred_at} mode="absolute" />
      </TableCell>
      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
        {symptom.notes ?? "—"}
      </TableCell>
      <TableCell className="text-sm">
        {symptom.condition_id ? (
          <Badge variant="outline" className="text-xs">
            {symptom.condition_id}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">{"—"}</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onEdit(symptom)}
            aria-label={`Edit ${symptom.name}`}
          >
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`Delete ${symptom.name}`}
          >
            Delete
          </Button>
        </div>
      </TableCell>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {symptom.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes this {symptom.name} entry from your symptom log. The record is
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
// SymptomTracker
// ---------------------------------------------------------------------------

export default function SymptomTracker() {
  const [nameFilter, setNameFilter] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a Symptom = edit mode.
  const [formTarget, setFormTarget] = useState<Symptom | null | undefined>(null);

  const params: SymptomParams = {
    name: nameFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useSymptoms(params);

  const symptoms = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  function handleNameChange(value: string) {
    setNameFilter(value);
    setPage(0);
  }

  return (
    <div className="space-y-4">
      {/* Toolbar: filters + add affordance */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            placeholder="Filter by name..."
            value={nameFilter}
            onChange={(e) => handleNameChange(e.target.value)}
            className="w-48"
          />
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
          {(nameFilter || since || until) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setNameFilter("");
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
          Log symptom
        </Button>
      </div>

      {!isLoading && symptoms.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Severity</TableHead>
              <TableHead>Occurred</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead>Condition</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              symptoms.map((symptom) => (
                <SymptomRow key={symptom.id} symptom={symptom} onEdit={setFormTarget} />
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
            <DialogTitle>{editing ? "Edit symptom" : "Log symptom"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this symptom entry's details."
                : "Log a symptom occurrence. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <SymptomForm
            symptom={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
