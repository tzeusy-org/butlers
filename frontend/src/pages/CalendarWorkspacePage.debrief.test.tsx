// @vitest-environment jsdom
/**
 * The Debrief card is mounted on CalendarWorkspacePage (bu-q7vx1q.63). The page's own
 * workspace hooks are stubbed to a generic idle result; only the debrief hook is specific.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import { usePendingMeetingDebriefs } from "@/hooks/use-meeting-debriefs";

vi.mock("@/hooks/use-meeting-debriefs", () => ({
  PENDING_DEBRIEFS_KEY: ["relationship", "meeting-debriefs", "pending"],
  usePendingMeetingDebriefs: vi.fn(),
}));

vi.mock("@/hooks/use-calendar-workspace", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  const idle = () => ({
    isLoading: true,
    isPending: true,
    isFetching: false,
    isError: false,
    error: null,
    data: undefined,
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
  });
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === "function" && /^use[A-Z]/.test(name) ? idle : value,
    ]),
  );
});

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function renderPage(): { container: HTMLElement; unmount: () => void } {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/calendar"]}>
          <Routes>
            <Route path="/calendar" element={<CalendarWorkspacePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("CalendarWorkspacePage debrief card", () => {
  it("shows the Debrief card when debriefs are pending", () => {
    vi.mocked(usePendingMeetingDebriefs).mockReturnValue({
      isError: false,
      data: {
        debriefs_available: true,
        debriefs: [
          {
            debrief_id: "d1",
            number: 1,
            event_title: "Roadmap sync",
            occurrence_start: "2026-10-18T09:00:00Z",
            prompted_at: "2026-10-18T11:00:00Z",
            attendees: [{ entity_id: "e1", name: "Sam Example", email: "sam@example.test" }],
          },
        ],
      },
    } as unknown as ReturnType<typeof usePendingMeetingDebriefs>);

    const { container, unmount } = renderPage();

    expect(container.querySelector('[data-testid="debrief-card"]')?.textContent).toContain(
      "Roadmap sync",
    );
    unmount();
  });

  it("hides the card when nothing is pending", () => {
    vi.mocked(usePendingMeetingDebriefs).mockReturnValue({
      isError: false,
      data: { debriefs: [], debriefs_available: true },
    } as unknown as ReturnType<typeof usePendingMeetingDebriefs>);

    const { container, unmount } = renderPage();

    expect(container.querySelector('[data-testid="debrief-card"]')).toBeNull();
    expect(container.querySelector('[data-testid="debrief-card-degraded"]')).toBeNull();
    unmount();
  });
});
