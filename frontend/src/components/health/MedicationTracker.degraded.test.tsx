/**
 * MedicationTracker — degraded-source honesty [bu-hmdqz.13]
 *
 * A failing adherence or dose-history read MUST render a named SourceDegradedNote,
 * never the calm "No doses logged yet" / "No doses recorded yet" empty copy — the
 * fabricated-calm sin this move removes from a medication surface.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import MedicationTracker from "@/components/health/MedicationTracker";

const refetchAdherence = vi.fn();
const refetchDoses = vi.fn();

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
          notes: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
    isError: false,
  }),
  useMedicationDoses: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: refetchDoses,
  }),
  useMedicationAdherence: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: refetchAdherence,
  }),
  useLogMedicationDose: () => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false }),
  useCreateMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MedicationTracker — degraded source honesty", () => {
  it("names a failing adherence source instead of 'No doses logged yet'", () => {
    render(<MedicationTracker />);
    expect(screen.getByTestId("adherence-degraded")).toBeTruthy();
    expect(screen.queryByText("No doses logged yet")).toBeNull();
  });

  it("names a failing dose-history source instead of 'No doses recorded yet'", () => {
    render(<MedicationTracker />);
    // Expand the row's dose history.
    fireEvent.click(screen.getByRole("button", { name: /show dose history/i }));
    expect(screen.getByTestId("dose-history-degraded")).toBeTruthy();
    expect(screen.queryByText("No doses recorded yet.")).toBeNull();
  });
});
