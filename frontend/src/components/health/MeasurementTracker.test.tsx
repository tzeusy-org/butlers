/**
 * MeasurementTracker — direct CRUD wiring [bu-mqhas]
 *
 * Verifies the measurements page mirrors the symptoms/meals foundation:
 *   - "Log measurement" opens the shared MeasurementForm dialog and a valid
 *     submit calls the create mutation with the typed request body. Scalar
 *     readings are wrapped as { value: N }.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the measurement id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MeasurementTracker from "@/components/health/MeasurementTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useMeasurements: () => ({
    data: {
      data: [
        {
          id: "meas-1",
          type: "weight",
          value: { value: 70 },
          measured_at: "2026-01-01T00:00:00Z",
          notes: "morning",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useCreateMeasurement: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateMeasurement: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteMeasurement: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MeasurementTracker — direct CRUD", () => {
  it("logs a scalar measurement via the add dialog (value wrapped in { value })", async () => {
    render(<MeasurementTracker />);

    fireEvent.click(screen.getByRole("button", { name: /log measurement/i }));

    fireEvent.change(screen.getByLabelText("Value (kg)"), { target: { value: "68" } });

    fireEvent.click(screen.getByRole("button", { name: /^log measurement$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      type: "weight",
      value: { value: 68 },
      measured_at: null,
      notes: null,
    });
  });

  it("logs a compound blood-pressure reading as { systolic, diastolic }", async () => {
    render(<MeasurementTracker />);

    fireEvent.click(screen.getByRole("button", { name: /log measurement/i }));

    // Switch the type select to blood_pressure to reveal the compound inputs.
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "blood_pressure" } });
    fireEvent.change(screen.getByLabelText("Systolic (mmHg)"), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("Diastolic (mmHg)"), { target: { value: "80" } });

    fireEvent.click(screen.getByRole("button", { name: /^log measurement$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      type: "blood_pressure",
      value: { systolic: 120, diastolic: 80 },
      measured_at: null,
      notes: null,
    });
  });

  it("requires a numeric value before logging", async () => {
    render(<MeasurementTracker />);
    fireEvent.click(screen.getByRole("button", { name: /log measurement/i }));
    // Submit with a blank value — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^log measurement$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a measurement via the edit dialog", async () => {
    render(<MeasurementTracker />);

    fireEvent.click(screen.getByRole("button", { name: /edit weight/i }));

    // Dialog is pre-filled with the existing scalar value; change it and save.
    const value = screen.getByLabelText("Value (kg)") as HTMLInputElement;
    expect(value.value).toBe("70");
    fireEvent.change(value, { target: { value: "72" } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "meas-1",
      body: expect.objectContaining({ type: "weight", value: { value: 72 } }),
    });
  });

  it("deletes a measurement after confirmation", async () => {
    render(<MeasurementTracker />);

    fireEvent.click(screen.getByRole("button", { name: /delete weight/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("meas-1"));
  });
});
