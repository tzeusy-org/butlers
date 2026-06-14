// ---------------------------------------------------------------------------
// MealForm — reusable add/edit form for logged meals [bu-5oeoq]
//
// Mirrors SymptomForm (bu-gk38e): a controlled form with per-field state, a
// single `onSubmit` that builds the request body and calls a create/update
// mutation, inline validation for the required description field, a disabled
// submit while pending, and toast feedback that surfaces the API error message.
//
// Meals are TEMPORAL facts: `eaten_at` is the eating time and multiple entries
// coexist by design (no supersession). `type` is one of breakfast/lunch/
// dinner/snack. Optional nutrition (calories + macros) maps to the same
// metadata the `meal_log` MCP tool writes.
//
// Pair it with the `useCreateMeal` / `useUpdateMeal` hooks in
// hooks/use-health.ts.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { Meal, MealNutrition } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateMeal, useUpdateMeal } from "@/hooks/use-health";

const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"] as const;

interface MealFormProps {
  /** When provided, the form edits this meal; otherwise it logs a new one. */
  meal?: Meal;
  /** Called after a successful create/update so the caller can close the dialog. */
  onDone: () => void;
  /** Called when the user cancels. */
  onCancel: () => void;
}

/**
 * Coerce a date-only input value (YYYY-MM-DD) into an ISO-8601 timestamp the
 * backend can parse. Falls back to "now" when blank (eaten_at is required).
 */
function eatenAtToIso(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return new Date().toISOString();
  const parsed = new Date(`${trimmed}T12:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString();
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

/** Pull a numeric nutrition field out of a meal's nutrition record. */
function nutritionField(meal: Meal | undefined, key: string): string {
  const v = meal?.nutrition?.[key];
  return v == null ? "" : String(v);
}

export function MealForm({ meal, onDone, onCancel }: MealFormProps) {
  const isEdit = meal != null;

  const [type, setType] = useState<string>(meal?.type ?? "breakfast");
  const [description, setDescription] = useState(meal?.description ?? "");
  const [eatenAt, setEatenAt] = useState(isoToDateInput(meal?.eaten_at));
  const [calories, setCalories] = useState(nutritionField(meal, "calories"));
  const [protein, setProtein] = useState(nutritionField(meal, "protein_g"));
  const [carbs, setCarbs] = useState(nutritionField(meal, "carbs_g"));
  const [fat, setFat] = useState(nutritionField(meal, "fat_g"));
  const [notes, setNotes] = useState(meal?.notes ?? "");

  const createMutation = useCreateMeal();
  const updateMutation = useUpdateMeal();
  const isPending = createMutation.isPending || updateMutation.isPending;

  function handleError(err: unknown) {
    const message =
      err instanceof ApiError ? err.message : "Something went wrong saving the meal.";
    toast.error(message);
  }

  function buildNutrition(): MealNutrition | null {
    const c = parseNumber(calories);
    const p = parseNumber(protein);
    const cb = parseNumber(carbs);
    const f = parseNumber(fat);
    if (c == null && p == null && cb == null && f == null) return null;
    return { calories: c, protein_g: p, carbs_g: cb, fat_g: f };
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const trimmedDescription = description.trim();
    if (!trimmedDescription) {
      toast.error("Description is required.");
      return;
    }

    const trimmedNotes = notes.trim();
    const nutrition = buildNutrition();
    const eatenIso = eatenAtToIso(eatenAt);

    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          id: meal.id,
          body: {
            type,
            description: trimmedDescription,
            eaten_at: eatenIso,
            nutrition,
            notes: trimmedNotes === "" ? null : trimmedNotes,
          },
        });
        toast.success("Meal updated.");
      } else {
        await createMutation.mutateAsync({
          type,
          description: trimmedDescription,
          eaten_at: eatenIso,
          nutrition,
          notes: trimmedNotes === "" ? null : trimmedNotes,
        });
        toast.success("Meal logged.");
      }
      onDone();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" data-testid="meal-form">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="meal-type">Type</Label>
          <select
            id="meal-type"
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm capitalize focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            {MEAL_TYPES.map((opt) => (
              <option key={opt} value={opt} className="capitalize">
                {opt}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="meal-eaten">Eaten (optional)</Label>
          <Input
            id="meal-eaten"
            type="date"
            value={eatenAt}
            onChange={(e) => setEatenAt(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="meal-description">Description</Label>
        <Input
          id="meal-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="e.g. Grilled chicken salad"
          autoFocus
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-4">
        <div className="space-y-2">
          <Label htmlFor="meal-calories">Calories</Label>
          <Input
            id="meal-calories"
            type="number"
            inputMode="numeric"
            value={calories}
            onChange={(e) => setCalories(e.target.value)}
            placeholder="kcal"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="meal-protein">Protein (g)</Label>
          <Input
            id="meal-protein"
            type="number"
            inputMode="numeric"
            value={protein}
            onChange={(e) => setProtein(e.target.value)}
            placeholder="g"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="meal-carbs">Carbs (g)</Label>
          <Input
            id="meal-carbs"
            type="number"
            inputMode="numeric"
            value={carbs}
            onChange={(e) => setCarbs(e.target.value)}
            placeholder="g"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="meal-fat">Fat (g)</Label>
          <Input
            id="meal-fat"
            type="number"
            inputMode="numeric"
            value={fat}
            onChange={(e) => setFat(e.target.value)}
            placeholder="g"
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="meal-notes">Notes (optional)</Label>
        <Textarea
          id="meal-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Anything worth remembering about this meal."
          rows={3}
        />
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending}>
          {isEdit ? "Save changes" : "Log meal"}
        </Button>
      </div>
    </form>
  );
}
