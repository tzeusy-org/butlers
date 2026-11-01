// @vitest-environment jsdom
/** Supply line shows when the count was recorded, never a fabricated date (bu-q7vx1q.58). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MedicationTracker from "@/components/health/MedicationTracker";

const NOW = Date.parse("2026-01-13T00:00:00Z");

let medication: Record<string, unknown>;

vi.mock("@/hooks/use-health", () => ({
  useMedications: () => ({
    data: { data: [medication], meta: { total: 1, has_more: false } },
    isLoading: false,
  }),
  useMedicationDoses: () => ({ data: [], isLoading: false }),
  useMedicationAdherence: () => ({ data: undefined, isLoading: false }),
  useLogMedicationDose: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteMedication: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function withSupply(quantity: number | null, quantity_updated_at: string | null) {
  medication = {
    id: "med-1",
    name: "Vitamin D",
    dosage: "1000IU",
    frequency: "daily",
    schedule: ["08:00"],
    active: true,
    notes: null,
    quantity,
    quantity_updated_at,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function supplyText(): string {
  const node = screen.getByText(/^Supply:/);
  return node.textContent ?? "";
}

beforeEach(() => {
  vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("MedicationTracker supply recorded date", () => {
  it("shows the count with a relative recorded time", () => {
    withSupply(30, "2026-01-01T00:00:00Z");
    render(<MedicationTracker />);

    expect(supplyText()).toMatch(/^Supply: 30 · recorded .*12 days? ago/);
    expect(document.querySelector("time")?.getAttribute("datetime")).toBe(
      "2026-01-01T00:00:00.000Z",
    );
  });

  it("reads exactly Supply: unknown when the count is unknown, even with a timestamp", () => {
    withSupply(null, "2026-01-01T00:00:00Z");
    render(<MedicationTracker />);

    expect(supplyText()).toBe("Supply: unknown");
  });

  it("says the date is unknown for a null timestamp", () => {
    withSupply(30, null);
    render(<MedicationTracker />);

    expect(supplyText()).toBe("Supply: 30 · recorded date unknown");
  });

  it("says the date is unknown for an unparseable timestamp", () => {
    withSupply(30, "not-a-date");
    render(<MedicationTracker />);

    expect(supplyText()).toBe("Supply: 30 · recorded date unknown");
    expect(document.body.textContent).not.toContain("Invalid");
  });
});
