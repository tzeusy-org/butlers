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
import { act } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import MeasurementTracker from "@/components/health/MeasurementTracker";

// MeasurementTracker's type/since/until filters are URL-backed (bu-qvnce.13),
// so it needs a Router context even for tests that don't touch the URL.
function renderTracker(initialPath = "/health/measurements") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <MeasurementTracker />
    </MemoryRouter>,
  );
}

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);
const refetchMeasurementTypes = vi.fn();

const defaultMeasurementTypesResult = {
  data: {
    types: [
      {
        type: "weight",
        label: "Weight",
        sample_count: 1,
        latest_at: "2026-01-01T00:00:00Z",
        unit: "kg",
        value_shape: "scalar" as const,
        chart_eligible: true,
        kpi_eligible: true,
      },
      {
        type: "hrv",
        label: "HRV",
        sample_count: 1,
        latest_at: "2026-01-02T00:00:00Z",
        unit: "ms",
        value_shape: "scalar" as const,
        chart_eligible: true,
        kpi_eligible: false,
      },
    ],
  },
  isLoading: false,
  isError: false,
  refetch: refetchMeasurementTypes,
};
type MeasurementTypesResult = {
  data: typeof defaultMeasurementTypesResult.data | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: typeof refetchMeasurementTypes;
};
let measurementTypesResult: MeasurementTypesResult = defaultMeasurementTypesResult;

vi.mock("@/hooks/use-health", () => ({
  useMeasurementTypes: () => measurementTypesResult,
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
        {
          id: "meas-hrv",
          type: "hrv",
          value: { daily_rmssd: 28.4, deep_rmssd: 31.2, coverage: 0.86 },
          measured_at: "2026-01-02T00:00:00Z",
          notes: null,
          created_at: "2026-01-02T00:00:00Z",
        },
      ],
      meta: { total: 2, has_more: false },
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
  measurementTypesResult = defaultMeasurementTypesResult;
});

describe("MeasurementTracker — direct CRUD", () => {
  it("logs a scalar measurement via the add dialog (value wrapped in { value })", async () => {
    renderTracker();

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
    renderTracker();

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
    renderTracker();
    fireEvent.click(screen.getByRole("button", { name: /log measurement/i }));
    // Submit with a blank value — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^log measurement$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a measurement via the edit dialog", async () => {
    renderTracker();

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

  it("edits an unknown compound reading without rewriting its type or value keys", async () => {
    renderTracker();

    fireEvent.click(screen.getByRole("button", { name: /edit hrv/i }));

    const type = screen.getByLabelText("Type") as HTMLInputElement;
    expect(type.value).toBe("hrv");
    expect(type.disabled).toBe(true);

    const value = screen.getByLabelText("Value (JSON)") as HTMLTextAreaElement;
    expect(JSON.parse(value.value)).toEqual({
      daily_rmssd: 28.4,
      deep_rmssd: 31.2,
      coverage: 0.86,
    });
    fireEvent.change(value, {
      target: {
        value: JSON.stringify({ daily_rmssd: 29.1, deep_rmssd: 31.2, coverage: 0.86 }),
      },
    });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "meas-hrv",
      body: {
        value: { daily_rmssd: 29.1, deep_rmssd: 31.2, coverage: 0.86 },
        measured_at: "2026-01-02T12:00:00.000Z",
        notes: null,
      },
    });
  });

  it("deletes a measurement after confirmation", async () => {
    renderTracker();

    fireEvent.click(screen.getByRole("button", { name: /delete weight/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("meas-1"));
  });
});

describe("MeasurementTracker — URL-backed type filter (bu-qvnce.13)", () => {
  it("hydrates the type filter from a ?type= deep link", () => {
    renderTracker("/health/measurements?type=blood_pressure");
    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.value).toBe("blood_pressure");
  });

  it("defaults to the 'All types' option with no ?type= param", () => {
    renderTracker();
    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.value).toBe("");
  });

  it("uses the observed vocabulary for filter options, including HRV", () => {
    renderTracker("/health/measurements?type=hrv");

    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.value).toBe("hrv");
    expect(Array.from(select.options).map((option) => option.text)).toEqual([
      "All types",
      "Weight",
      "HRV",
    ]);
  });

  it("keeps an unknown URL-selected type visible without inventing it", () => {
    renderTracker("/health/measurements?type=resting_hr");

    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.value).toBe("resting_hr");
    expect(Array.from(select.options).map((option) => option.text)).toContain(
      "resting_hr (selected)",
    );
  });

  it("keeps a controlled clear path while vocabulary loading is in progress", () => {
    measurementTypesResult = {
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: refetchMeasurementTypes,
    };
    renderTracker("/health/measurements?type=resting_hr");

    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(Array.from(select.options).map((option) => option.text)).toEqual([
      "All types",
      "resting_hr (selected)",
    ]);
    expect(screen.getByText("Loading measurement types…")).toBeTruthy();
  });

  it("keeps a controlled clear path for an unknown URL type when vocabulary loading fails", () => {
    measurementTypesResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchMeasurementTypes,
    };
    renderTracker("/health/measurements?type=resting_hr");

    const select = screen.getByLabelText("Filter by type") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(Array.from(select.options).map((option) => option.text)).toEqual([
      "All types",
      "resting_hr (selected)",
    ]);
    fireEvent.change(select, { target: { value: "" } });
    expect(select.value).toBe("");
    expect(screen.getByText(/measurement types: unavailable/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// "n" keyboard path (bu-mmdef, keyboard chassis remainder) -- health's six
// add/log actions were mouse-only, cut from #3586's scope. Asserts real DOM
// focus lands in the opened dialog (the #3586 focus-reality doctrine), not
// just that the dialog opened.
// ---------------------------------------------------------------------------

describe("MeasurementTracker — keyboard path (bu-mmdef)", () => {
  it("n opens the log dialog and moves real DOM focus onto its first field", () => {
    renderTracker();

    expect(screen.queryByRole("dialog")).toBeNull();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "n", bubbles: true, cancelable: true }));
    });

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText("Value (kg)"));
  });
});
