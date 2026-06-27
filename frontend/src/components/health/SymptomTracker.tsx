// ---------------------------------------------------------------------------
// SymptomTracker — direct add/edit/delete for logged symptoms [bu-gk38e]
//
// Dispatch reframe [bu-w7b18.4]: the symptom log is a Dispatch rule-list, not a
// Card-wrapped data table. Each row leads with a 6px SEVERITY GLYPH — a square,
// not a progress bar — coloured by the owner's own 1-10 severity (bands at
// 2/5/8 via --severity-low/medium/high). The raw 1-10 value is shown verbatim;
// no clinical adjective is layered onto it. Per-row Edit / Delete actions, the
// delete confirmation dialog, and the name + date-range filters are preserved.
// All writes go through the /api/health/symptoms fact-store path, so dashboard
// edits and butler edits stay in sync.
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
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Time } from "@/components/ui/time";
import { useConditions, useDeleteSymptom, useSymptoms } from "@/hooks/use-health";

const PAGE_SIZE = 50;

/**
 * Severity band colour for the 1-10 scale. Bands sit at 2 / 5 / 8 (low /
 * medium / high band centres): 1-3 low, 4-6 medium, 7-10 high. This colours the
 * glyph only — it never relabels the owner's numeric severity value.
 */
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
    <div className="divide-y divide-border/60 border-y border-border/60">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="grid grid-cols-[10px_1fr_auto] items-start gap-3 py-3">
          <span className="bg-muted mt-1.5 h-1.5 w-1.5 shrink-0 animate-pulse" />
          <div className="space-y-1.5">
            <div className="bg-muted h-3.5 w-40 animate-pulse rounded" />
            <div className="bg-muted h-2.5 w-28 animate-pulse rounded" />
          </div>
          <div className="bg-muted h-7 w-24 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SymptomRow — a single symptom rule-list row with edit/delete affordances
// ---------------------------------------------------------------------------

function SymptomRow({
  symptom,
  conditionName,
  onEdit,
}: {
  symptom: Symptom;
  conditionName?: string;
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
    <div className="grid grid-cols-[10px_1fr_auto] items-start gap-3 py-3">
      {/* 6px severity glyph — a square, coloured by the 1-10 band. */}
      <span
        className="mt-1.5 h-1.5 w-1.5 shrink-0"
        style={{ backgroundColor: severityColor(symptom.severity) }}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-foreground text-sm font-medium">{symptom.name}</span>
          <span className="text-muted-foreground font-mono text-[10px] tabular-nums">
            {symptom.severity}/10
          </span>
        </div>
        <div className="text-muted-foreground mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 font-mono text-[10px] tabular-nums">
          <span>
            <Time value={symptom.occurred_at} mode="absolute" />
          </span>
          {conditionName && (
            <span className="uppercase tracking-[0.1em]">· {conditionName}</span>
          )}
          {symptom.notes && (
            <span className="text-muted-foreground/80 min-w-0 truncate font-sans">
              · {symptom.notes}
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
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
    </div>
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
  // Fetch all conditions (up to 500) for ID → name resolution in rows.
  const { data: conditionsData } = useConditions({ limit: 500 });
  const conditionNameById = Object.fromEntries(
    (conditionsData?.data ?? []).map((c) => [c.id, c.name]),
  );

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

      {isLoading ? (
        <SkeletonRows />
      ) : symptoms.length === 0 ? (
        <p className="text-muted-foreground font-serif text-[15px] italic">
          Nothing logged yet. Log a symptom above, or tell your Health butler.
        </p>
      ) : (
        <div className="divide-y divide-border/60 border-y border-border/60">
          {symptoms.map((symptom) => (
            <SymptomRow
              key={symptom.id}
              symptom={symptom}
              conditionName={symptom.condition_id ? conditionNameById[symptom.condition_id] : undefined}
              onEdit={setFormTarget}
            />
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
