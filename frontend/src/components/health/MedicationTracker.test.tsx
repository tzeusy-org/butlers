/**
 * MedicationTracker — direct CRUD wiring [bu-aisjm]
 *
 * Verifies the foundation scaffolding end to end on the medications page:
 *   - "Add medication" opens the shared MedicationForm dialog and a valid submit
 *     calls the create mutation with the typed request body.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the medication id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MedicationTracker from "@/components/health/MedicationTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useMedications: () => ({
    data: {
      data: [
        {
          id: "med-1",
          name: "Vitamin D",
          dosage: "1000IU",
          frequency: "daily",
          schedule: ["08:00"],
          active: true,
          notes: "with breakfast",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useMedicationDoses: () => ({ data: [], isLoading: false }),
  useMedicationAdherence: () => ({ data: undefined, isLoading: false }),
  useLogMedicationDose: () => ({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  }),
  useCreateMedication: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateMedication: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteMedication: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MedicationTracker — direct CRUD", () => {
  it("creates a medication via the add dialog", async () => {
    render(<MedicationTracker />);

    fireEvent.click(screen.getByRole("button", { name: /add medication/i }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Magnesium" } });
    fireEvent.change(screen.getByLabelText("Dosage"), { target: { value: "200mg" } });
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "nightly" } });

    fireEvent.click(screen.getByRole("button", { name: /add medication/i, hidden: false }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      name: "Magnesium",
      dosage: "200mg",
      frequency: "nightly",
      schedule: [],
      notes: null,
    });
  });

  it("requires name, dosage, and frequency before creating", async () => {
    render(<MedicationTracker />);
    fireEvent.click(screen.getByRole("button", { name: /add medication/i }));
    // Submit with empty fields — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^add medication$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a medication via the edit dialog", async () => {
    render(<MedicationTracker />);

    fireEvent.click(screen.getByRole("button", { name: /edit vitamin d/i }));

    // Dialog is pre-filled; change the dosage and save.
    const dosage = screen.getByLabelText("Dosage") as HTMLInputElement;
    expect(dosage.value).toBe("1000IU");
    fireEvent.change(dosage, { target: { value: "2000IU" } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "med-1",
      body: expect.objectContaining({ dosage: "2000IU", name: "Vitamin D" }),
    });
  });

  it("deletes a medication after confirmation", async () => {
    render(<MedicationTracker />);

    fireEvent.click(screen.getByRole("button", { name: /delete vitamin d/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("med-1"));
  });
});
