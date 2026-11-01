// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { toast } from "sonner";

import {
  useAcceptCalendarProposal,
  useCalendarOverlays,
  useDismissCalendarProposal,
} from "@/hooks/use-calendar-workspace";
import {
  findButton,
  installCalendarPageHarness,
  latestWorkspaceParams,
  setConflictProposalIssue,
} from "@/test/calendar-page/harness";
import {
  flush,
  setWorkspaceState,
  type UseOverlaysResult,
} from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("CalendarWorkspacePage", () => {
  const { container, renderPage, getSearchText, openConflictProposalActions } =
    installCalendarPageHarness();

  it("restores view/range from deep-link query state", () => {
    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    expect(findButton("Butler")?.getAttribute("aria-pressed")).toBe("true");
    expect(findButton("List")?.getAttribute("aria-pressed")).toBe("true");
    expect(latestWorkspaceParams()?.view).toBe("butler");
    expect(getSearchText()).toContain("view=butler");
    expect(getSearchText()).toContain("range=list");
  });

  it("opens the calendar workspace with a labeled verdict region", () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    expect(container.querySelector('[aria-label="Calendar verdict"]')).not.toBeNull();
  });

  it("disables conflict proposal actions while accepting a fix", async () => {
    vi.mocked(useAcceptCalendarProposal).mockReturnValue({
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: true,
    } as unknown as ReturnType<typeof useAcceptCalendarProposal>);
    setConflictProposalIssue();
    await openConflictProposalActions();

    expect(findButton("Accept fix")?.disabled).toBe(true);
    expect(findButton("Decline")?.disabled).toBe(true);
  });

  it("shows an error toast when accepting a conflict fix fails", async () => {
    const acceptMutate = vi.fn(
      (_vars: { proposalId: string }, options?: { onError?: (error: Error) => void }) => {
        options?.onError?.(new Error("Proposal could not be accepted"));
      },
    );
    vi.mocked(useAcceptCalendarProposal).mockReturnValue({
      mutate: acceptMutate,
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcceptCalendarProposal>);
    setConflictProposalIssue();
    await openConflictProposalActions();
    await act(async () => {
      findButton("Accept fix")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(acceptMutate).toHaveBeenCalledWith(
      { proposalId: "proposal-1" },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
    expect(toast.error).toHaveBeenCalledWith("Proposal could not be accepted");
  });

  it("announces and refreshes after accepting a conflict fix", async () => {
    const acceptMutate = vi.fn(
      (
        _vars: { proposalId: string },
        options?: { onSuccess?: () => void; onSettled?: () => void },
      ) => {
        options?.onSuccess?.();
        options?.onSettled?.();
      },
    );
    vi.mocked(useAcceptCalendarProposal).mockReturnValue({
      mutate: acceptMutate,
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcceptCalendarProposal>);
    setConflictProposalIssue();

    const queryClient = await openConflictProposalActions();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      findButton("Accept fix")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(acceptMutate).toHaveBeenCalledWith(
      { proposalId: "proposal-1" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(toast.success).toHaveBeenCalledWith(
      "Proposal accepted. Event added to the Butlers calendar.",
    );
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["calendar-conflicts"],
    });
  });

  it("announces and refreshes after declining a conflict fix", async () => {
    const dismissMutate = vi.fn(
      (
        _vars: { proposalId: string },
        options?: { onSuccess?: () => void; onSettled?: () => void },
      ) => {
        options?.onSuccess?.();
        options?.onSettled?.();
      },
    );
    vi.mocked(useDismissCalendarProposal).mockReturnValue({
      mutate: dismissMutate,
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDismissCalendarProposal>);
    setConflictProposalIssue();

    const queryClient = await openConflictProposalActions();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      findButton("Decline")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(dismissMutate).toHaveBeenCalledWith(
      { proposalId: "proposal-1" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(toast.success).toHaveBeenCalledWith("Proposal dismissed.");
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["calendar-conflicts"],
    });
  });

  it("announces a failure when declining a conflict fix", async () => {
    const dismissMutate = vi.fn(
      (
        _vars: { proposalId: string },
        options?: { onError?: (error: Error) => void; onSettled?: () => void },
      ) => {
        options?.onError?.(new Error("Proposal could not be dismissed"));
        options?.onSettled?.();
      },
    );
    vi.mocked(useDismissCalendarProposal).mockReturnValue({
      mutate: dismissMutate,
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDismissCalendarProposal>);
    setConflictProposalIssue();

    const queryClient = await openConflictProposalActions();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    await act(async () => {
      findButton("Decline")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(dismissMutate).toHaveBeenCalledWith(
      { proposalId: "proposal-1" },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
    expect(toast.error).toHaveBeenCalledWith("Proposal could not be dismissed");
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["calendar-conflicts"],
    });
  });

  // -------------------------------------------------------------------------
  // Never-blank floor (bu-nhcp5): week/day-nav must keep the outgoing
  // window's entries on screen (dimmed), never fall back to "Drawing the
  // calendar…" on every step, and never let dimmed stale entries mask a
  // real error.
  // -------------------------------------------------------------------------

  it("keeps the outgoing window's entries visible while the next window is fetching", () => {
    setWorkspaceState({ isFetching: true });
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    expect(container.textContent).toContain("Morning planning");
    expect(container.textContent).not.toContain("Drawing the calendar…");
  });

  it("dims the grid with FetchingDim while a window navigation refetch is in flight", () => {
    setWorkspaceState({ isFetching: true });
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    expect(container.innerHTML).toContain("opacity-60");
  });

  it("undims once the new window's data settles", () => {
    setWorkspaceState({ isFetching: false });
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    expect(container.innerHTML).not.toContain("opacity-60");
  });

  it("surfaces the error state instead of masking it behind dimmed stale entries", () => {
    // Stale entries can still be cached (e.g. the previous week's window)
    // when a navigation request fails; the error must win, not the dimmed
    // stale grid.
    setWorkspaceState({
      isError: true,
      isFetching: false,
      error: new Error("workspace fetch failed"),
    });
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");

    expect(container.textContent).toContain(
      "The calendar workspace failed to load.",
    );
    expect(container.textContent).not.toContain("Morning planning");
  });

  it("dims retained overlay pills while the next visible window is fetching", () => {
    vi.mocked(useCalendarOverlays).mockReturnValue({
      isLoading: false,
      isFetching: true,
      isError: false,
      isFetched: true,
      error: null,
      data: {
        data: {
          entries: [
            {
              entry_id: "overlay-stale-1",
              event_id: null,
              view: "overlays",
              source_type: "overlay_contribution",
              source_key: "overlays",
              title: "Stale electricity bill",
              start_at: "2026-03-01T00:00:00Z",
              end_at: "2026-03-02T00:00:00Z",
              timezone: "UTC",
              all_day: true,
              calendar_id: null,
              provider_event_id: null,
              butler_name: "finance",
              schedule_id: null,
              reminder_id: null,
              rrule: null,
              cron: null,
              until_at: null,
              status: "active",
              sync_state: null,
              editable: false,
              metadata: {
                kind: "bill_due",
                priority: "high",
                source_butler: "finance",
              },
            },
          ],
          has_domain_context: true,
        },
      },
    } as unknown as UseOverlaysResult);

    renderPage("/calendar?view=user&range=month&anchor=2026-03-01&overlays=1");

    expect(container.textContent).toContain("Stale electricity bill");
    expect(container.innerHTML).toContain("opacity-60");
  });

  it("surfaces an overlays query error instead of retained overlay pills", () => {
    vi.mocked(useCalendarOverlays).mockReturnValue({
      isLoading: false,
      isFetching: false,
      isError: true,
      isFetched: true,
      error: new Error("overlay fetch failed"),
      data: {
        data: {
          entries: [
            {
              entry_id: "overlay-stale-2",
              event_id: null,
              view: "overlays",
              source_type: "overlay_contribution",
              source_key: "overlays",
              title: "Stale flight reminder",
              start_at: "2026-03-01T00:00:00Z",
              end_at: "2026-03-02T00:00:00Z",
              timezone: "UTC",
              all_day: true,
              calendar_id: null,
              provider_event_id: null,
              butler_name: "travel",
              schedule_id: null,
              reminder_id: null,
              rrule: null,
              cron: null,
              until_at: null,
              status: "active",
              sync_state: null,
              editable: false,
              metadata: {
                kind: "travel_leg",
                priority: "high",
                source_butler: "travel",
              },
            },
          ],
          has_domain_context: true,
        },
      },
    } as unknown as UseOverlaysResult);

    renderPage("/calendar?view=user&range=list&anchor=2026-03-01&overlays=1");

    expect(container.querySelector("[data-testid='calendar-overlays-error']")?.textContent).toContain(
      "overlay fetch failed",
    );
    expect(container.textContent).not.toContain("Stale flight reminder");
  });
});
