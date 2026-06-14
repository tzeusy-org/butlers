// ---------------------------------------------------------------------------
// ConditionForm — reusable add/edit form for health conditions [bu-a7vw9]
//
// Mirrors the MedicationForm foundation scaffolding (bu-aisjm): a controlled
// form with per-field state, a single `onSubmit` that builds the request body
// and calls a create/update mutation, inline validation for the required name
// field, a disabled submit while pending, and toast feedback that surfaces the
// API error message.
//
// Pair it with the `useCreateCondition` / `useUpdateCondition` hooks in
// hooks/use-health.ts.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { HealthCondition } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateCondition,
  useUpdateCondition,
} from "@/hooks/use-health";

type ConditionStatus = "active" | "managed" | "resolved";

const STATUS_OPTIONS: ConditionStatus[] = ["active", "managed", "resolved"];

interface ConditionFormProps {
  /** When provided, the form edits this condition; otherwise it creates a new one. */
  condition?: HealthCondition;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Coerce a date-only input value (YYYY-MM-DD) into an ISO-8601 timestamp the
 * backend can parse, or `null` when blank.
 */
function diagnosedAtToIso(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  // `YYYY-MM-DD` from <input type="date"> -> midnight UTC ISO string.
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** Extract the YYYY-MM-DD portion of an ISO timestamp for the date input. */
function isoToDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

export function ConditionForm({ condition, onDone, onCancel }: ConditionFormProps) {
  const isEdit = condition != null;

  const [name, setName] = useState(condition?.name ?? "");
  const [status, setStatus] = useState<ConditionStatus>(
    (condition?.status as ConditionStatus) ?? "active",
  );
  const [diagnosedAt, setDiagnosedAt] = useState(isoToDateInput(condition?.diagnosed_at));
  const [notes, setNotes] = useState(condition?.notes ?? "");

  const createMutation = useCreateCondition();
  const updateMutation = useUpdateCondition();
  const isPending = createMutation.isPending || updateMutation.isPending;

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the condition.";
    toast.error(message);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const trimmedName = name.trim();
    if (!trimmedName) {
      toast.error("Name is required.");
      return;
    }

    const trimmedNotes = notes.trim();
    const diagnosedIso = diagnosedAtToIso(diagnosedAt);

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: condition.id,
          body: {
            name: trimmedName,
            status,
            diagnosed_at: diagnosedIso,
            notes: trimmedNotes === "" ? null : trimmedNotes,
          },
        });
        toast.success("Condition updated.");
      } else {
        await createMutation.mutateAsync({
          name: trimmedName,
          status,
          diagnosed_at: diagnosedIso,
          notes: trimmedNotes === "" ? null : trimmedNotes,
        });
        toast.success("Condition added.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="condition-form">
      <div className="space-y-2">
        <Label htmlFor="cond-name">Name</Label>
        <Input
          id="cond-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Hypertension"
          autoFocus
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="cond-status">Status</Label>
          <select
            id="cond-status"
            value={status}
            onChange={(e) => setStatus(e.target.value as ConditionStatus)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt.charAt(0).toUpperCase() + opt.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="cond-diagnosed">Onset / diagnosed (optional)</Label>
          <Input
            id="cond-diagnosed"
            type="date"
            value={diagnosedAt}
            onChange={(e) => setDiagnosedAt(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="cond-notes">Notes (optional)</Label>
        <Textarea
          id="cond-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything worth remembering about this condition."
          rows={3}
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Add condition"}
        </Button>
      </div>
    </form>
  );
}
