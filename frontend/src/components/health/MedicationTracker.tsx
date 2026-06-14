import { useState } from "react";

import type { Medication } from "@/api/types";
import { MedicationForm } from "@/components/health/MedicationForm";
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
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { toast } from "sonner";
import {
  useDeleteMedication,
  useMedicationDoses,
  useMedications,
} from "@/hooks/use-health";
import { Time } from "@/components/ui/time";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MedicationSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-4 w-24" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No medications found."
      description="Add a medication with the button above, or log one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// DoseLog — expandable dose history for a single medication
// ---------------------------------------------------------------------------

function DoseLog({ medicationId, frequency }: { medicationId: string; frequency: string }) {
  const { data: doses, isLoading } = useMedicationDoses(medicationId);

  if (isLoading) {
    return (
      <div className="space-y-2 pt-2">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (!doses || doses.length === 0) {
    return (
      <p className="text-muted-foreground pt-2 text-sm">No dose records yet.</p>
    );
  }

  // Simple adherence calculation: taken doses / total doses
  const totalDoses = doses.length;
  const takenDoses = doses.filter((d) => !d.skipped).length;
  const adherencePct = totalDoses > 0 ? Math.round((takenDoses / totalDoses) * 100) : 0;

  return (
    <div className="space-y-3 pt-3">
      {/* Adherence indicator */}
      <div className="flex items-center gap-3">
        <span className="text-muted-foreground text-sm">Adherence:</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${adherencePct}%`,
              backgroundColor:
                adherencePct >= 80 ? "var(--severity-low)" : adherencePct >= 50 ? "var(--severity-medium)" : "var(--severity-high)",
            }}
          />
        </div>
        <span className="text-sm font-medium tabular-nums">{adherencePct}%</span>
      </div>
      <p className="text-muted-foreground text-xs">
        {takenDoses} of {totalDoses} doses taken ({frequency})
      </p>

      {/* Recent doses table */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Date</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Notes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {doses.slice(0, 20).map((dose) => (
            <TableRow key={dose.id}>
              <TableCell className="text-sm">
                <Time value={dose.taken_at} mode="absolute" precision="minute" compact />
              </TableCell>
              <TableCell>
                <Badge variant={dose.skipped ? "destructive" : "default"} className="text-xs">
                  {dose.skipped ? "Skipped" : "Taken"}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                {dose.notes ?? "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MedicationCard
// ---------------------------------------------------------------------------

function MedicationCard({
  medication,
  onEdit,
}: {
  medication: Medication;
  onEdit: (medication: Medication) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteMedication();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(medication.id);
      toast.success("Medication deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete medication.");
    }
  }

  return (
    <Card className="cursor-pointer" onClick={() => setExpanded((v) => !v)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{medication.name}</CardTitle>
          <Badge variant={medication.active ? "default" : "secondary"} className="text-xs">
            {medication.active ? "Active" : "Inactive"}
          </Badge>
        </div>
        <CardDescription>{medication.dosage}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">
          Frequency: {medication.frequency}
        </p>
        {medication.notes && (
          <p className="text-muted-foreground mt-1 text-xs">{medication.notes}</p>
        )}

        {/* Per-card actions — stopPropagation so they don't toggle the dose log. */}
        <div
          className="mt-3 flex items-center gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => onEdit(medication)}
            aria-label={`Edit ${medication.name}`}
          >
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`Delete ${medication.name}`}
          >
            Delete
          </Button>
        </div>

        {/* Expandable dose log */}
        {expanded && (
          <div onClick={(e) => e.stopPropagation()}>
            <DoseLog medicationId={medication.id} frequency={medication.frequency} />
          </div>
        )}
      </CardContent>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {medication.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes {medication.name} from your medication list. The record is
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
    </Card>
  );
}

// ---------------------------------------------------------------------------
// MedicationTracker
// ---------------------------------------------------------------------------

export default function MedicationTracker() {
  const [showAll, setShowAll] = useState(false);
  // `null` = closed; `undefined` = add mode; a Medication = edit mode.
  const [formTarget, setFormTarget] = useState<Medication | null | undefined>(null);

  const { data, isLoading } = useMedications({
    active: showAll ? undefined : true,
    limit: 100,
  });

  const medications = data?.data ?? [];
  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  return (
    <div className="space-y-4">
      {/* Toolbar: filter toggle + add affordance */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button
            variant={!showAll ? "default" : "outline"}
            size="sm"
            onClick={() => setShowAll(false)}
          >
            Active
          </Button>
          <Button
            variant={showAll ? "default" : "outline"}
            size="sm"
            onClick={() => setShowAll(true)}
          >
            All
          </Button>
        </div>
        <Button size="sm" onClick={() => setFormTarget(undefined)}>
          Add medication
        </Button>
      </div>

      {/* Medication cards */}
      {isLoading ? (
        <MedicationSkeleton />
      ) : medications.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {medications.map((med) => (
            <MedicationCard key={med.id} medication={med} onEdit={setFormTarget} />
          ))}
        </div>
      )}

      {/* Add / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && setFormTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit medication" : "Add medication"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this medication's details."
                : "Add a medication to your record. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <MedicationForm
            medication={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
