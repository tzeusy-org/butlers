// ---------------------------------------------------------------------------
// MeasurementForm — reusable add/edit form for logged measurements [bu-mqhas]
//
// Mirrors MealForm (bu-5oeoq): a controlled form with per-field state, a single
// `onSubmit` that builds the request body and calls a create/update mutation,
// inline validation for the required value field, a disabled submit while
// pending, and toast feedback that surfaces the API error message.
//
// Measurements are TEMPORAL facts: `measured_at` is the reading time and
// multiple readings coexist by design (no supersession). `type` is one of
// weight/blood_pressure/heart_rate/blood_sugar/temperature and is encoded in
// the backing predicate, so changing it rewrites the `measurement_{type}` fact.
// `value` is JSONB: blood_pressure is a compound { systolic, diastolic } dict;
// every other type is a scalar wrapped as { value: N }.
//
// Pair it with the `useCreateMeasurement` / `useUpdateMeasurement` hooks in
// hooks/use-health.ts.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { Measurement, MeasurementType } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateMeasurement, useUpdateMeasurement } from "@/hooks/use-health";

const MEASUREMENT_TYPES: readonly MeasurementType[] = [
  "weight",
  "blood_pressure",
  "heart_rate",
  "blood_sugar",
  "temperature",
] as const;

/** Human-readable labels + units for each measurement type. */
const TYPE_META: Record<MeasurementType, { label: string; unit: string }> = {
  weight: { label: "Weight", unit: "kg" },
  blood_pressure: { label: "Blood pressure", unit: "mmHg" },
  heart_rate: { label: "Heart rate", unit: "bpm" },
  blood_sugar: { label: "Blood sugar", unit: "mg/dL" },
  temperature: { label: "Temperature", unit: "°C" },
};

interface MeasurementFormProps {
  /** When provided, the form edits this measurement; otherwise it logs a new one. */
  measurement?: Measurement;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Coerce a date-only input value (YYYY-MM-DD) into an ISO-8601 timestamp the
 * backend can parse, or `null` when blank (the backend defaults to now).
 */
function measuredAtToIso(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = new Date(`${trimmed}T12:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** Extract the YYYY-MM-DD portion of an ISO timestamp for the date input. */
function isoToDateInput(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

/** Parse a numeric input value, returning null for blank/invalid entries. */
function parseNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** Read a numeric field out of a measurement's value record as an input string. */
function valueField(measurement: Measurement | undefined, key: string): string {
  const v = measurement?.value?.[key];
  return v == null ? "" : String(v);
}

export function MeasurementForm({ measurement, onDone, onCancel }: MeasurementFormProps) {
  const isEdit = measurement != null;

  const initialType = (measurement?.type as MeasurementType) ?? "weight";
  const [type, setType] = useState<MeasurementType>(initialType);
  // Scalar value (every type except blood_pressure).
  const [scalar, setScalar] = useState(valueField(measurement, "value"));
  // Compound value (blood_pressure).
  const [systolic, setSystolic] = useState(valueField(measurement, "systolic"));
  const [diastolic, setDiastolic] = useState(valueField(measurement, "diastolic"));
  const [measuredAt, setMeasuredAt] = useState(isoToDateInput(measurement?.measured_at));
  const [notes, setNotes] = useState(measurement?.notes ?? "");

  const createMutation = useCreateMeasurement();
  const updateMutation = useUpdateMeasurement();
  const isPending = createMutation.isPending || updateMutation.isPending;

  const isCompound = type === "blood_pressure";

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the measurement.";
    toast.error(message);
  }

  /** Build the JSONB value payload, or null when the inputs are invalid/blank. */
  function buildValue(): Record<string, number> | null {
    if (isCompound) {
      const sys = parseNumber(systolic);
      const dia = parseNumber(diastolic);
      if (sys == null || dia == null) return null;
      return { systolic: sys, diastolic: dia };
    }
    const v = parseNumber(scalar);
    if (v == null) return null;
    return { value: v };
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const value = buildValue();
    if (value == null) {
      toast.error(
        isCompound
          ? "Systolic and diastolic are required."
          : "A numeric value is required.",
      );
      return;
    }

    const trimmedNotes = notes.trim();
    const measuredIso = measuredAtToIso(measuredAt);

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: measurement.id,
          body: {
            type,
            value,
            measured_at: measuredIso,
            notes: trimmedNotes === "" ? null : trimmedNotes,
          },
        });
        toast.success("Measurement updated.");
      } else {
        await createMutation.mutateAsync({
          type,
          value,
          measured_at: measuredIso,
          notes: trimmedNotes === "" ? null : trimmedNotes,
        });
        toast.success("Measurement logged.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  const unit = TYPE_META[type].unit;

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="measurement-form">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="meas-type">Type</Label>
          <select
            id="meas-type"
            value={type}
            onChange={(e) => setType(e.target.value as MeasurementType)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            {MEASUREMENT_TYPES.map((opt) => (
              <option key={opt} value={opt}>
                {TYPE_META[opt].label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="meas-measured">Measured (optional)</Label>
          <Input
            id="meas-measured"
            type="date"
            value={measuredAt}
            onChange={(e) => setMeasuredAt(e.target.value)}
          />
        </div>
      </div>

      {isCompound ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="meas-systolic">Systolic ({unit})</Label>
            <Input
              id="meas-systolic"
              type="number"
              inputMode="numeric"
              value={systolic}
              onChange={(e) => setSystolic(e.target.value)}
              placeholder="e.g. 120"
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="meas-diastolic">Diastolic ({unit})</Label>
            <Input
              id="meas-diastolic"
              type="number"
              inputMode="numeric"
              value={diastolic}
              onChange={(e) => setDiastolic(e.target.value)}
              placeholder="e.g. 80"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <Label htmlFor="meas-value">Value ({unit})</Label>
          <Input
            id="meas-value"
            type="number"
            inputMode="decimal"
            value={scalar}
            onChange={(e) => setScalar(e.target.value)}
            placeholder={`e.g. value in ${unit}`}
            autoFocus
          />
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="meas-notes">Notes (optional)</Label>
        <Textarea
          id="meas-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything worth remembering about this reading."
          rows={3}
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Log measurement"}
        </Button>
      </div>
    </form>
  );
}
