/**
 * QuickAddBar — natural-language calendar quick-add (parse-then-confirm).
 *
 * Verifies:
 *   - typing a phrase + Enter parses and renders an editable preview;
 *   - editing the title then confirming dispatches `onConfirm` with the edited
 *     draft (the page wires this to the normal create path + fresh request_id);
 *   - a degraded parse (parse_available=false) renders the reason, no preview,
 *     and never confirms;
 *   - confirm is never reachable without a successful parse (no auto-write).
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, fireEvent, screen, act, waitFor } from "@testing-library/react";

import { QuickAddBar } from "@/pages/calendar/QuickAddBar";
import { useParseCalendarQuickAdd } from "@/hooks/use-calendar-workspace";
import type { QuickAddDraft } from "@/api/types";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useParseCalendarQuickAdd: vi.fn(),
}));

const mockedUseParse = vi.mocked(useParseCalendarQuickAdd);

function mockParse(impl: (body: unknown) => Promise<unknown>) {
  const mutateAsync = vi.fn(impl);
  mockedUseParse.mockReturnValue({
    mutateAsync,
    isPending: false,
    reset: vi.fn(),
  } as unknown as ReturnType<typeof useParseCalendarQuickAdd>);
  return mutateAsync;
}

const DRAFT: QuickAddDraft = {
  title: "Lunch with Sarah",
  start_at: "2026-06-26T13:00:00+08:00",
  end_at: "2026-06-26T14:00:00+08:00",
  all_day: false,
  location: "Tartine",
  description: "with Sarah",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("QuickAddBar", () => {
  it("parses a phrase, then confirms the edited draft through onConfirm", async () => {
    const mutateAsync = mockParse(async () => ({
      data: { parse_available: true, draft: { ...DRAFT }, reason: null },
    }));
    const onConfirm = vi.fn().mockResolvedValue(undefined);

    render(<QuickAddBar timezone="Asia/Singapore" onConfirm={onConfirm} />);

    const input = screen.getByLabelText("Quick add event");
    fireEvent.change(input, { target: { value: "lunch with Sarah Fri 1pm at Tartine" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    // Parse called with the phrase + timezone.
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "lunch with Sarah Fri 1pm at Tartine",
        timezone: "Asia/Singapore",
      }),
    );

    // Editable preview rendered.
    const titleField = (await screen.findByLabelText("Draft title")) as HTMLInputElement;
    expect(titleField.value).toBe("Lunch with Sarah");

    // Edit the title, then confirm.
    fireEvent.change(titleField, { target: { value: "Lunch with Sarah B." } });
    await act(async () => {
      fireEvent.click(screen.getByText("Confirm & add"));
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Lunch with Sarah B.", location: "Tartine" }),
    );
  });

  it("renders the reason and no preview when parse is unavailable", async () => {
    mockParse(async () => ({
      data: { parse_available: false, draft: null, reason: "No cheap-tier model is configured." },
    }));
    const onConfirm = vi.fn();

    render(<QuickAddBar timezone="UTC" onConfirm={onConfirm} />);

    const input = screen.getByLabelText("Quick add event");
    fireEvent.change(input, { target: { value: "dentist tomorrow" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    expect(await screen.findByText("No cheap-tier model is configured.")).toBeTruthy();
    expect(screen.queryByLabelText("Draft title")).toBeNull();
    expect(screen.queryByText("Confirm & add")).toBeNull();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("does not parse blank input", async () => {
    const mutateAsync = mockParse(async () => ({
      data: { parse_available: true, draft: { ...DRAFT }, reason: null },
    }));
    render(<QuickAddBar timezone="UTC" onConfirm={vi.fn()} />);

    const input = screen.getByLabelText("Quick add event");
    fireEvent.change(input, { target: { value: "   " } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => expect(mutateAsync).not.toHaveBeenCalled());
    expect(screen.queryByLabelText("Draft title")).toBeNull();
  });
});
