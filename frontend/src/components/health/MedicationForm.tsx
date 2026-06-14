// ---------------------------------------------------------------------------
// MedicationForm — reusable add/edit form for health medications [bu-aisjm]
//
// This is the FOUNDATION scaffolding for direct health-dashboard CRUD. It is
// deliberately generic over the two write modes (create / edit) so that the
// five sibling health pages (conditions, symptoms, meals, research,
// measurements) can model their own forms on the same shape:
//
//   - A controlled form with per-field state.
//   - A single `onSubmit` that builds the request body and calls a mutation.
//   - Inline validation (required fields) with a disabled submit while pending.
//   - Toast feedback on success / error, surfacing the API error message.
//
// Pair it with the `useCreateMedication` / `useUpdateMedication` hooks in
// hooks/use-health.ts. Siblings should clone this pattern (form + hook) rather
// than reach for a bespoke modal each time.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { Medication } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateMedication,
  useUpdateMedication,
} from "@/hooks/use-health";

interface MedicationFormProps {
  /** When provided, the form edits this medication; otherwise it creates a new one. */
  medication?: Medication;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/** Parse a comma-separated schedule string into a trimmed, non-empty list. */
function parseSchedule(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function MedicationForm({ medication, onDone, onCancel }: MedicationFormProps) {
  const isEdit = medication != null;

  const [name, setName] = useState(medication?.name ?? "");
  const [dosage, setDosage] = useState(medication?.dosage ?? "");
  const [frequency, setFrequency] = useState(medication?.frequency ?? "");
  const [schedule, setSchedule] = useState(
    (medication?.schedule ?? []).map((s) => String(s)).join(", "),
  );
  const [notes, setNotes] = useState(medication?.notes ?? "");
  const [active, setActive] = useState(medication?.active ?? true);

  const createMutation = useCreateMedication();
  const updateMutation = useUpdateMedication();
  const isPending = createMutation.isPending || updateMutation.isPending;

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the medication.";
    toast.error(message);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const trimmedName = name.trim();
    const trimmedDosage = dosage.trim();
    const trimmedFrequency = frequency.trim();

    if (!trimmedName || !trimmedDosage || !trimmedFrequency) {
      toast.error("Name, dosage, and frequency are required.");
      return;
    }

    const scheduleList = parseSchedule(schedule);
    const trimmedNotes = notes.trim();

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: medication.id,
          body: {
            name: trimmedName,
            dosage: trimmedDosage,
            frequency: trimmedFrequency,
            schedule: scheduleList,
            active,
            notes: trimmedNotes === "" ? null : trimmedNotes,
          },
        });
        toast.success("Medication updated.");
      } else {
        await createMutation.mutateAsync({
          name: trimmedName,
          dosage: trimmedDosage,
          frequency: trimmedFrequency,
          schedule: scheduleList,
          notes: trimmedNotes === "" ? null : trimmedNotes,
        });
        toast.success("Medication added.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="medication-form">
      <div className="space-y-2">
        <Label htmlFor="med-name">Name</Label>
        <Input
          id="med-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Vitamin D"
          autoFocus
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="med-dosage">Dosage</Label>
          <Input
            id="med-dosage"
            value={dosage}
            onChange={(e) => setDosage(e.target.value)}
            placeholder="e.g. 1000IU"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="med-frequency">Frequency</Label>
          <Input
            id="med-frequency"
            value={frequency}
            onChange={(e) => setFrequency(e.target.value)}
            placeholder="e.g. daily"
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="med-schedule">Schedule (optional)</Label>
        <Input
          id="med-schedule"
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
          placeholder="Comma-separated times, e.g. 08:00, 20:00"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="med-notes">Notes (optional)</Label>
        <Textarea
          id="med-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything worth remembering about this medication."
          rows={3}
        />
      </div>

      {isEdit && (
        <div className="flex items-center gap-3">
          <Switch id="med-active" checked={active} onCheckedChange={setActive} />
          <Label htmlFor="med-active">Active</Label>
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Add medication"}
        </Button>
      </div>
    </form>
  );
}
