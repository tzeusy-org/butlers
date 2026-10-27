// Shared render harness for the CalendarWorkspacePage test files. Call
// `installCalendarPageHarness()` inside a `describe`; it registers the
// `beforeEach`/`afterEach` hooks and returns the render helpers.

import { afterEach, beforeEach, expect, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

import CalendarWorkspacePage from "@/pages/CalendarWorkspacePage";
import { SearchEcho } from "@/test/calendar-page/search-echo";
import {
  useCalendarConflicts,
  useCalendarInvitations,
  useCalendarWorkspace,
} from "@/hooks/use-calendar-workspace";
import {
  flush,
  mutateButlerEvent,
  mutateUserEvent,
  previewMutate,
  setButlerMutationState,
  setEntryDetailState,
  setPrimaryCalendarState,
  setRecurrencePreviewState,
  setSyncState,
  setUserMutationState,
  setWorkspaceMetaState,
  setWorkspaceState,
} from "@/test/calendar-page/state";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function setEmptyInvitations() {
  vi.mocked(useCalendarInvitations).mockReturnValue({
    isPending: false,
    isFetching: false,
    isError: false,
    isPlaceholderData: false,
    data: {
      pages: [
        {
          data: {
            entries: [],
            issues_available: true,
            conflicts_available: true,
            sources_degraded: [],
            has_more: false,
            next_cursor: null,
          },
        },
      ],
      pageParams: [],
    },
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  } as unknown as ReturnType<typeof useCalendarInvitations>);
}

export function setConflictProposalIssue() {
  vi.mocked(useCalendarConflicts).mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      data: {
        issues: [
          {
            kind: "overlap",
            date: "2026-07-01",
            summary: "Two meetings overlap",
            severity: "warning",
            events: [
              {
                entry_id: "entry-1",
                title: "Morning planning",
                start_at: "2026-07-01T09:00:00Z",
                end_at: "2026-07-01T10:00:00Z",
                timezone: "UTC",
                status: "confirmed",
              },
            ],
            proposal_ids: ["proposal-1"],
          },
        ],
        scan_window: {
          start: "2026-07-01T00:00:00Z",
          end: "2026-07-08T00:00:00Z",
        },
        issues_available: true,
      },
    },
  } as unknown as ReturnType<typeof useCalendarConflicts>);
}

export function latestWorkspaceParams() {
  const calls = vi.mocked(useCalendarWorkspace).mock.calls;
  return calls.at(-1)?.[0];
}

export function findButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find(
    (button) => button.textContent?.trim() === label,
  );
}

export function findDialogByTitle(title: string): Element | undefined {
  return Array.from(document.querySelectorAll('[data-slot="dialog-content"]')).find(
    (dialog) => dialog.textContent?.includes(title),
  );
}

/** Clicks the first "Detail" button to open the entry detail panel. */
export async function openDetailPanel() {
  const detailButton = findButton("Detail");
  expect(detailButton).toBeDefined();
  await act(async () => {
    detailButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flush();
  });
}

export function installCalendarPageHarness() {
  const container = document.createElement("div");
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    mutateButlerEvent.mockReset();
    mutateUserEvent.mockReset();
    previewMutate.mockReset();
    setWorkspaceState();
    setWorkspaceMetaState();
    setButlerMutationState();
    setSyncState();
    setUserMutationState();
    setPrimaryCalendarState();
    setEntryDetailState();
    setEmptyInvitations();
    setRecurrencePreviewState();
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );

    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function renderPage(initialEntry: string) {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
            <Routes>
              <Route
                path="/calendar"
                element={
                  <>
                    <CalendarWorkspacePage />
                    <SearchEcho />
                  </>
                }
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    return queryClient;
  }

  function getSearchText() {
    return container.querySelector('[data-testid="search"]')?.textContent ?? "";
  }

  async function openConflictProposalActions() {
    const queryClient = renderPage("/calendar?view=user&range=week&anchor=2026-07-01");

    await act(async () => {
      findButton("Review")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    return queryClient;
  }

  return { container, renderPage, getSearchText, openConflictProposalActions };
}
