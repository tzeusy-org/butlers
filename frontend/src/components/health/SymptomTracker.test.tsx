/**
 * SymptomTracker — direct CRUD wiring [bu-gk38e]
 *
 * Verifies the symptoms page mirrors the conditions foundation scaffolding:
 *   - "Log symptom" opens the shared SymptomForm dialog and a valid submit
 *     calls the create mutation with the typed request body.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the symptom id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import SymptomTracker from "@/components/health/SymptomTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useSymptoms: () => ({
    data: {
      data: [
        {
          id: "sym-1",
          name: "Headache",
          severity: 7,
          condition_id: null,
          occurred_at: "2026-01-01T00:00:00Z",
          notes: "after screen time",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useCreateSymptom: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateSymptom: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteSymptom: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SymptomTracker — direct CRUD", () => {
  it("logs a symptom via the add dialog", async () => {
    render(<SymptomTracker />);

    fireEvent.click(screen.getByRole("button", { name: /log symptom/i }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Nausea" } });

    fireEvent.click(screen.getByRole("button", { name: /^log symptom$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      name: "Nausea",
      severity: 5,
      occurred_at: null,
      notes: null,
    });
  });

  it("requires a name before logging", async () => {
    render(<SymptomTracker />);
    fireEvent.click(screen.getByRole("button", { name: /log symptom/i }));
    // Submit with an empty name — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^log symptom$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a symptom via the edit dialog", async () => {
    render(<SymptomTracker />);

    fireEvent.click(screen.getByRole("button", { name: /edit headache/i }));

    // Dialog is pre-filled; change the severity and save.
    const severity = screen.getByLabelText("Severity (1-10)") as HTMLSelectElement;
    expect(severity.value).toBe("7");
    fireEvent.change(severity, { target: { value: "9" } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "sym-1",
      body: expect.objectContaining({ severity: 9, name: "Headache" }),
    });
  });

  it("deletes a symptom after confirmation", async () => {
    render(<SymptomTracker />);

    fireEvent.click(screen.getByRole("button", { name: /delete headache/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("sym-1"));
  });
});
