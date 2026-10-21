// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { HealthResearch } from "@/api/types";
import ResearchTracker from "@/components/health/ResearchTracker";

let note: HealthResearch;
const updateMutate = vi.fn().mockResolvedValue({});
const deleteMutate = vi.fn().mockResolvedValue(undefined);

vi.mock("@/hooks/use-health", () => ({
  useResearch: () => ({
    data: { data: [note], meta: { total: 1, has_more: false } },
    isLoading: false,
  }),
  useCreateResearch: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateResearch: () => ({ mutateAsync: updateMutate, isPending: false }),
  useDeleteResearch: () => ({ mutateAsync: deleteMutate, isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function research(title: string): HealthResearch {
  return Object.freeze({
    id: "original-research-id",
    title,
    content: "The original research body.",
    tags: [],
    source_url: null,
    condition_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("research note titles are display-only fallbacks", () => {
  it.each(["", "   "])(
    "names a blank row and its actions without replacing the original title (%j)",
    async (title) => {
      note = research(title);
      render(<ResearchTracker />);

      expect(screen.getByText("Untitled note")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Expand Untitled note" }));
      const collapse = screen.getByRole("button", { name: "Collapse Untitled note" });
      expect(collapse.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByText(note.content)).toBeTruthy();
      fireEvent.click(collapse);
      expect(screen.getByRole("button", { name: "Expand Untitled note" })
        .getAttribute("aria-expanded")).toBe("false");

      fireEvent.click(screen.getByRole("button", { name: "Edit Untitled note" }));
      expect(screen.getByLabelText("Title")).toHaveProperty("value", title);
      expect(screen.getByLabelText("Content")).toHaveProperty("value", note.content);
      // Existing validation remains: the display fallback cannot silently
      // become a persisted research subject when an unchanged blank title saves.
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
      expect(updateMutate).not.toHaveBeenCalled();
      fireEvent.change(screen.getByLabelText("Title"), {
        target: { value: "A title chosen by the user" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
      await waitFor(() => expect(updateMutate).toHaveBeenCalledWith({
        id: note.id,
        body: {
          title: "A title chosen by the user",
          content: note.content,
          tags: [],
          source_url: null,
        },
      }));

      fireEvent.click(screen.getByRole("button", { name: "Delete Untitled note" }));
      const confirmation = screen.getByRole("alertdialog", { name: "Delete Untitled note?" });
      expect(within(confirmation).getByText(/This removes Untitled note from your research list/))
        .toBeTruthy();
      fireEvent.click(within(confirmation).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(deleteMutate).toHaveBeenCalledWith("original-research-id"));
      expect(note.title).toBe(title);
      expect(note.id).toBe("original-research-id");
    },
  );

  it("preserves a nonblank title's exact presentation and names its controls", () => {
    note = research("  Magnesium and sleep  ");
    render(<ResearchTracker />);

    expect(screen.getByText("Magnesium and sleep").textContent).toBe(note.title);
    expect(screen.queryByText("Untitled note")).toBeNull();
    expect(screen.getByRole("button", { name: "Expand Magnesium and sleep" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit Magnesium and sleep" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete Magnesium and sleep" })).toBeTruthy();
    expect(note.title).toBe("  Magnesium and sleep  ");
  });
});
