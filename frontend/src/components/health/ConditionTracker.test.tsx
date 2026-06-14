/**
 * ConditionTracker — direct CRUD wiring [bu-a7vw9]
 *
 * Verifies the conditions page mirrors the medications foundation scaffolding:
 *   - "Add condition" opens the shared ConditionForm dialog and a valid submit
 *     calls the create mutation with the typed request body.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the condition id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ConditionTracker from "@/components/health/ConditionTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useConditions: () => ({
    data: {
      data: [
        {
          id: "cond-1",
          name: "Hypertension",
          status: "managed",
          diagnosed_at: null,
          notes: "monitor BP",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useCreateCondition: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateCondition: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteCondition: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConditionTracker — direct CRUD", () => {
  it("creates a condition via the add dialog", async () => {
    render(<ConditionTracker />);

    fireEvent.click(screen.getByRole("button", { name: /add condition/i }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Asthma" } });

    fireEvent.click(screen.getByRole("button", { name: /^add condition$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      name: "Asthma",
      status: "active",
      diagnosed_at: null,
      notes: null,
    });
  });

  it("requires a name before creating", async () => {
    render(<ConditionTracker />);
    fireEvent.click(screen.getByRole("button", { name: /add condition/i }));
    // Submit with an empty name — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^add condition$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a condition via the edit dialog", async () => {
    render(<ConditionTracker />);

    fireEvent.click(screen.getByRole("button", { name: /edit hypertension/i }));

    // Dialog is pre-filled; change the status and save.
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    expect(status.value).toBe("managed");
    fireEvent.change(status, { target: { value: "resolved" } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "cond-1",
      body: expect.objectContaining({ status: "resolved", name: "Hypertension" }),
    });
  });

  it("deletes a condition after confirmation", async () => {
    render(<ConditionTracker />);

    fireEvent.click(screen.getByRole("button", { name: /delete hypertension/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("cond-1"));
  });
});
