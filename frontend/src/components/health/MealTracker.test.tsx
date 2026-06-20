/**
 * MealTracker — direct CRUD wiring [bu-5oeoq, bu-w7b18.5]
 *
 * Verifies the meals page mirrors the symptoms foundation scaffolding:
 *   - "Log meal" opens the shared MealForm dialog and a valid submit calls the
 *     create mutation with the typed request body.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the meal id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 *
 * Updated for bu-w7b18.5: MealTracker now accepts controlled filter props
 * (typeFilter, since, until, setTypeFilter, setSince, setUntil) lifted from
 * MealsPage so the right-column nutrition totals share the same date range.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MealTracker, { type MealTrackerProps } from "@/components/health/MealTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useMeals: () => ({
    data: {
      data: [
        {
          id: "meal-1",
          type: "lunch",
          description: "Grilled chicken salad",
          nutrition: { calories: 420, protein_g: 35, carbs_g: 12, fat_g: 18 },
          eaten_at: "2026-01-01T12:00:00Z",
          notes: "post-workout",
          created_at: "2026-01-01T12:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useCreateMeal: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateMeal: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteMeal: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Render MealTracker with controlled filter props (all empty/no-op by default). */
function renderTracker(overrides: Partial<MealTrackerProps> = {}) {
  const defaults: MealTrackerProps = {
    typeFilter: "",
    since: "",
    until: "",
    setTypeFilter: vi.fn(),
    setSince: vi.fn(),
    setUntil: vi.fn(),
    ...overrides,
  };
  return render(<MealTracker {...defaults} />);
}

describe("MealTracker — direct CRUD", () => {
  it("logs a meal via the add dialog", async () => {
    renderTracker();

    fireEvent.click(screen.getByRole("button", { name: /^log meal$/i }));

    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Oatmeal" },
    });
    fireEvent.change(screen.getByLabelText("Type") as HTMLSelectElement, {
      target: { value: "breakfast" },
    });
    fireEvent.change(screen.getByLabelText("Calories"), { target: { value: "250" } });

    fireEvent.click(screen.getByRole("button", { name: /^log meal$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "breakfast",
        description: "Oatmeal",
        nutrition: { calories: 250, protein_g: null, carbs_g: null, fat_g: null },
        notes: null,
      }),
    );
  });

  it("requires a description before logging", async () => {
    renderTracker();
    fireEvent.click(screen.getByRole("button", { name: /^log meal$/i }));
    // Submit with an empty description — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^log meal$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a meal via the edit dialog", async () => {
    renderTracker();

    fireEvent.click(screen.getByRole("button", { name: /edit grilled chicken salad/i }));

    // Dialog is pre-filled; change the type and save.
    const type = screen.getByLabelText("Type") as HTMLSelectElement;
    expect(type.value).toBe("lunch");
    fireEvent.change(type, { target: { value: "dinner" } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "meal-1",
      body: expect.objectContaining({
        type: "dinner",
        description: "Grilled chicken salad",
      }),
    });
  });

  it("deletes a meal after confirmation", async () => {
    renderTracker();

    fireEvent.click(screen.getByRole("button", { name: /delete grilled chicken salad/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("meal-1"));
  });
});
