// ---------------------------------------------------------------------------
// MeasurementTracker — direct add/edit/delete for logged measurements [bu-mqhas]
//
// Mirrors SymptomTracker (bu-gk38e): a list surface with a "Log measurement"
// toolbar affordance, per-row Edit / Delete actions, a delete confirmation
// dialog, and an add/edit dialog wrapping the shared MeasurementForm. All
// writes go through the /api/health/measurements fact-store path, so dashboard
// edits and butler edits stay in sync.
//
// Measurements are TEMPORAL facts (reading log): the type + date-range filters
// are preserved here. The completes the last of the six health-CRUD pages.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { Measurement, MeasurementParams, MeasurementType } from "@/api/types";
import { MeasurementForm } from "@/components/health/MeasurementForm";
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
import { useDeleteMeasurement, useMeasurements } from "@/hooks/use-health";

const PAGE_SIZE = 50;

const TYPE_FILTERS: readonly { value: "" | MeasurementType; label: string }[] = [
  { value: "", label: "All types" },
  { value: "weight", label: "Weight" },
  { value: "blood_pressure", label: "Blood pressure" },
  { value: "heart_rate", label: "Heart rate" },
  { value: "blood_sugar", label: "Blood sugar" },
  { value: "temperature", label: "Temperature" },
] as const;

const TYPE_LABELS: Record<string, string> = {
  weight: "Weight",
  blood_pressure: "Blood pressure",
  heart_rate: "Heart rate",
  blood_sugar: "Blood sugar",
  temperature: "Temperature",
};

/** Human-readable label for a measurement type (falls back to the raw type). */
function typeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}

/** Render a measurement's JSONB value as a readable string. */
function formatValue(measurement: Measurement): string {
  const v = measurement.value ?? {};
  if (
    measurement.type === "blood_pressure" &&
    v.systolic != null &&
    v.diastolic != null
  ) {
    return `${String(v.systolic)}/${String(v.diastolic)}`;
  }
  if ("value" in v && v.value != null) {
    return String(v.value);
  }
  // Compound or unexpected shape — show each key=value pair.
  const parts = Object.entries(v)
    .filter(([, val]) => val != null)
    .map(([k, val]) => `${k}=${String(val)}`);
  return parts.length > 0 ? parts.join(", ") : "—";
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
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No measurements found."
      description="Log a measurement with the button above, or record one by talking to your Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// MeasurementRow — a single measurement with edit/delete affordances
// ---------------------------------------------------------------------------

function MeasurementRow({
  measurement,
  onEdit,
}: {
  measurement: Measurement;
  onEdit: (measurement: Measurement) => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteMeasurement();

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(measurement.id);
      toast.success("Measurement deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete measurement.");
    }
  }

  const label = typeLabel(measurement.type);

  return (
    <TableRow>
      <TableCell className="font-medium">{label}</TableCell>
      <TableCell className="tabular-nums">{formatValue(measurement)}</TableCell>
      <TableCell className="text-muted-foreground text-sm">
        <Time value={measurement.measured_at} mode="absolute" />
      </TableCell>
      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
        {measurement.notes ?? "—"}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onEdit(measurement)}
            aria-label={`Edit ${label}`}
          >
            Edit
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`Delete ${label}`}
          >
            Delete
          </Button>
        </div>
      </TableCell>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this {label} reading?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes this {label} reading from your measurement log. The record is
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
// MeasurementTracker
// ---------------------------------------------------------------------------

export default function MeasurementTracker() {
  const [typeFilter, setTypeFilter] = useState<"" | MeasurementType>("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a Measurement = edit mode.
  const [formTarget, setFormTarget] = useState<Measurement | null | undefined>(null);

  const params: MeasurementParams = {
    type: typeFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useMeasurements(params);

  const measurements = data?.data ?? [];
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
          <select
            aria-label="Filter by type"
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value as "" | MeasurementType);
              setPage(0);
            }}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-44 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            {TYPE_FILTERS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-sm">From</label>
            <input
              type="date"
              value={since}
              onChange={(e) => {
                setSince(e.target.value);
                setPage(0);
              }}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-sm">To</label>
            <input
              type="date"
              value={until}
              onChange={(e) => {
                setUntil(e.target.value);
                setPage(0);
              }}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-40 rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
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
          Log measurement
        </Button>
      </div>

      {!isLoading && measurements.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Type</TableHead>
              <TableHead>Value</TableHead>
              <TableHead>Measured</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows />
            ) : (
              measurements.map((measurement) => (
                <MeasurementRow
                  key={measurement.id}
                  measurement={measurement}
                  onEdit={setFormTarget}
                />
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
            <DialogTitle>{editing ? "Edit measurement" : "Log measurement"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this measurement reading's details."
                : "Log a measurement reading. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <MeasurementForm
            measurement={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
