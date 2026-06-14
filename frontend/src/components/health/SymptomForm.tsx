// ---------------------------------------------------------------------------
// SymptomForm — reusable add/edit form for logged symptoms [bu-gk38e]
//
// Mirrors ConditionForm (bu-a7vw9): a controlled form with per-field state, a
// single `onSubmit` that builds the request body and calls a create/update
// mutation, inline validation for the required name field, a disabled submit
// while pending, and toast feedback that surfaces the API error message.
//
// Symptoms are TEMPORAL facts: `occurred_at` is the occurrence time and
// multiple entries coexist by design (no supersession). `severity` is on a
// 1-10 scale.
//
// Pair it with the `useCreateSymptom` / `useUpdateSymptom` hooks in
// hooks/use-health.ts.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { Symptom } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateSymptom, useUpdateSymptom } from "@/hooks/use-health";

const SEVERITY_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] as const;

interface SymptomFormProps {
  /** When provided, the form edits this symptom; otherwise it logs a new one. */
  symptom?: Symptom;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Coerce a date-only input value (YYYY-MM-DD) into an ISO-8601 timestamp the
 * backend can parse, or `null` when blank (the backend defaults to now).
 */
function occurredAtToIso(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = new Date(`${trimmed}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** Extract the YYYY-MM-DD portion of an ISO timestamp for the date input. */
function isoToDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

export function SymptomForm({ symptom, onDone, onCancel }: SymptomFormProps) {
  const isEdit = symptom != null;

  const [name, setName] = useState(symptom?.name ?? "");
  const [severity, setSeverity] = useState<number>(symptom?.severity ?? 5);
  const [occurredAt, setOccurredAt] = useState(isoToDateInput(symptom?.occurred_at));
  const [notes, setNotes] = useState(symptom?.notes ?? "");

  const createMutation = useCreateSymptom();
  const updateMutation = useUpdateSymptom();
  const isPending = createMutation.isPending || updateMutation.isPending;

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the symptom.";
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
    const occurredIso = occurredAtToIso(occurredAt);

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: symptom.id,
          body: {
            name: trimmedName,
            severity,
            occurred_at: occurredIso,
            notes: trimmedNotes === "" ? null : trimmedNotes,
          },
        });
        toast.success("Symptom updated.");
      } else {
        await createMutation.mutateAsync({
          name: trimmedName,
          severity,
          occurred_at: occurredIso,
          notes: trimmedNotes === "" ? null : trimmedNotes,
        });
        toast.success("Symptom logged.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="symptom-form">
      <div className="space-y-2">
        <Label htmlFor="sym-name">Name</Label>
        <Input
          id="sym-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Headache"
          autoFocus
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="sym-severity">Severity (1-10)</Label>
          <select
            id="sym-severity"
            value={severity}
            onChange={(e) => setSeverity(Number(e.target.value))}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            {SEVERITY_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="sym-occurred">Occurred (optional)</Label>
          <Input
            id="sym-occurred"
            type="date"
            value={occurredAt}
            onChange={(e) => setOccurredAt(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="sym-notes">Notes (optional)</Label>
        <Textarea
          id="sym-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything worth remembering about this symptom."
          rows={3}
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Log symptom"}
        </Button>
      </div>
    </form>
  );
}
