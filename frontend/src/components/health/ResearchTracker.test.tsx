/**
 * ResearchTracker — direct CRUD wiring [bu-wamzk]
 *
 * Verifies the research page mirrors the conditions foundation scaffolding:
 *   - "Add research" opens the shared ResearchForm dialog and a valid submit
 *     calls the create mutation with the typed request body.
 *   - "Edit" opens the dialog pre-filled and submits via the update mutation.
 *   - "Delete" confirms and calls the delete mutation with the note id.
 *
 * The use-health hooks are mocked so no real QueryClient / network is needed;
 * we assert the component wires user intent to the mutation hooks.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ResearchTracker from "@/components/health/ResearchTracker";

const createMutate = vi.fn().mockResolvedValue({});
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useResearch: () => ({
    data: {
      data: [
        {
          id: "research-1",
          title: "Magnesium and sleep",
          content: "Studies suggest magnesium improves sleep latency.",
          tags: ["sleep"],
          source_url: "https://example.com/study",
          condition_id: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      meta: { total: 1, has_more: false },
    },
    isLoading: false,
  }),
  useCreateResearch: () => ({ mutateAsync: createMutate, isPending: false }),
  useUpdateResearch: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteResearch: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ResearchTracker — direct CRUD", () => {
  it("creates a research note via the add dialog", async () => {
    render(<ResearchTracker />);

    fireEvent.click(screen.getByRole("button", { name: /add research/i }));

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Vitamin D and mood" },
    });
    fireEvent.change(screen.getByLabelText("Content"), {
      target: { value: "Some findings about vitamin D." },
    });

    fireEvent.click(screen.getByRole("button", { name: /^add research$/i }));

    await waitFor(() => expect(createMutate).toHaveBeenCalledTimes(1));
    expect(createMutate).toHaveBeenCalledWith({
      title: "Vitamin D and mood",
      content: "Some findings about vitamin D.",
      tags: [],
      source_url: null,
    });
  });

  it("requires a title before creating", async () => {
    render(<ResearchTracker />);
    fireEvent.click(screen.getByRole("button", { name: /add research/i }));
    // Submit with an empty title — the submit button label inside the form.
    fireEvent.click(screen.getByRole("button", { name: /^add research$/i }));
    await waitFor(() => expect(createMutate).not.toHaveBeenCalled());
  });

  it("edits a research note via the edit dialog", async () => {
    render(<ResearchTracker />);

    fireEvent.click(screen.getByRole("button", { name: /edit magnesium and sleep/i }));

    // Dialog is pre-filled; change the content and save.
    const content = screen.getByLabelText("Content") as HTMLTextAreaElement;
    expect(content.value).toContain("magnesium improves sleep latency");
    fireEvent.change(content, { target: { value: "Updated body." } });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate).toHaveBeenCalledWith({
      id: "research-1",
      body: expect.objectContaining({
        title: "Magnesium and sleep",
        content: "Updated body.",
      }),
    });
  });

  it("deletes a research note after confirmation", async () => {
    render(<ResearchTracker />);

    fireEvent.click(screen.getByRole("button", { name: /delete magnesium and sleep/i }));

    // Confirm in the alert dialog (the destructive action button).
    const confirm = screen
      .getAllByRole("button", { name: /^delete$/i })
      .at(-1) as HTMLElement;
    fireEvent.click(confirm);

    await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("research-1"));
  });
});
