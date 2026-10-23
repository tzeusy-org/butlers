// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { toast } from "sonner";

import { useParseCalendarQuickAdd } from "@/hooks/use-calendar-workspace";
import { findButton, installCalendarPageHarness } from "@/test/calendar-page/harness";
import { flush, setInputValue, setUserMutationState } from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const draft = {
  title: "Lunch with Sarah",
  start_at: "2026-03-02T12:00:00Z",
  end_at: "2026-03-02T13:00:00Z",
  all_day: false,
  location: null,
  description: null,
};

function userEventResponse(result: Record<string, unknown>, conflicts: unknown[] = []) {
  return {
    data: {
      action: "create",
      tool_name: "calendar_create_event",
      request_id: "req-1",
      result,
      conflicts,
      suggested_slots: [],
      projection_version: null,
      staleness_ms: null,
      projection_freshness: null,
    },
    meta: {},
  };
}

describe("CalendarWorkspacePage quick-add (confirmed provider-event creation)", () => {
  const { renderPage } = installCalendarPageHarness();

  async function parseAndConfirm() {
    vi.mocked(useParseCalendarQuickAdd).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        data: { parse_available: true, draft, reason: null },
        meta: {},
      }),
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useParseCalendarQuickAdd>);
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    const input = document.querySelector(
      'input[aria-label="Quick add event"]',
    ) as HTMLInputElement;
    await act(async () => {
      setInputValue(input, "lunch with Sarah tomorrow noon");
      await flush();
    });
    await act(async () => {
      findButton("Parse")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
    await act(async () => {
      findButton("Confirm & add")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
  }

  it("creates the confirmed draft through the user-event endpoint", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(userEventResponse({ status: "created" }));
    setUserMutationState({ mutateAsync } as never);

    await parseAndConfirm();

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(mutateAsync.mock.calls[0]?.[0]).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "create",
        request_id: expect.stringMatching(/^calendar-create-/),
        payload: expect.objectContaining({
          title: "Lunch with Sarah",
          calendar_id: "primary",
          start_at: draft.start_at,
          end_at: draft.end_at,
        }),
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Created event (created).");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("reports an overlap as an error toast instead of booking over it", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(userEventResponse({ status: "conflict" }, [{ title: "Standup" }]));
    setUserMutationState({ mutateAsync } as never);

    await parseAndConfirm();

    expect(mutateAsync).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("overlaps another event"));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("reports a hard failure as an error toast, not success", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue(userEventResponse({ status: "error", error: "provider rejected" }));
    setUserMutationState({ mutateAsync } as never);

    await parseAndConfirm();

    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("provider rejected"));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows provider events read-only: no inputs and no delete in the detail panel", async () => {
    renderPage("/calendar?view=user&range=list&anchor=2026-03-01");
    await act(async () => {
      findButton("Detail")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = document.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).not.toBeNull();
    expect(panel?.querySelector("input, textarea")).toBeNull();
    expect(findButton("Delete")).toBeUndefined();
  });
});
