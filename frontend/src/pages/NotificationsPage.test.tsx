// @vitest-environment jsdom
/**
 * Regression tests for the Notifications page.
 *
 * Covers the mismatch bug where summary stats showed non-zero counts but the
 * list panel rendered "No notifications found" due to sentinel filter values
 * ("all", "") being forwarded to the backend as literal WHERE conditions.
 *
 * The j/k list-triage describe block below (bu-qvnce.11 slice 4) needs a
 * real DOM (createRoot + keydown dispatch + document.activeElement), hence
 * the jsdom environment pragma -- the rest of this file's tests only ever
 * used renderToStaticMarkup, which doesn't need one.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useLocation } from "react-router";
import { fireEvent } from "@testing-library/react";

import NotificationsPage, { STATUS_OPTIONS } from "@/pages/NotificationsPage";
import {
  useAcknowledgeAllFailed,
  useMarkNotificationRead,
  useNotifications,
  useNotificationStats,
} from "@/hooks/use-notifications";

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications: vi.fn(),
  useNotificationStats: vi.fn(),
  useMarkNotificationRead: vi.fn(),
  useAcknowledgeAllFailed: vi.fn(),
}));

type UseNotificationsResult = ReturnType<typeof useNotifications>;
type UseNotificationStatsResult = ReturnType<typeof useNotificationStats>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const NOTIFICATION_1 = {
  id: "notif-aaa",
  source_butler: "switchboard",
  channel: "telegram",
  recipient: "@user",
  message: "Task completed successfully",
  metadata: null,
  status: "sent",
  effective_status: "sent",
  error: null,
  session_id: null,
  trace_id: null,
  created_at: "2026-02-20T10:00:00Z",
};

const NOTIFICATION_2 = {
  id: "notif-bbb",
  source_butler: "general",
  channel: "email",
  recipient: "user@example.com",
  message: "Weekly summary report",
  metadata: null,
  status: "failed",
  effective_status: "failed",
  error: "SMTP connection refused",
  session_id: null,
  trace_id: null,
  created_at: "2026-02-19T08:00:00Z",
};

const NOTIFICATION_READ = {
  id: "notif-ccc",
  source_butler: "switchboard",
  channel: "telegram",
  recipient: "@user",
  message: "Already acknowledged",
  metadata: null,
  status: "read",
  effective_status: "read",
  error: null,
  session_id: null,
  trace_id: null,
  created_at: "2026-02-18T08:00:00Z",
};

function setNotificationsState(state: Partial<UseNotificationsResult>) {
  vi.mocked(useNotifications).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseNotificationsResult);
}

function setStatsState(state: Partial<UseNotificationStatsResult>) {
  vi.mocked(useNotificationStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseNotificationStatsResult);
}

function renderPage(initialPath = "/"): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[initialPath]}>
      <NotificationsPage />
    </MemoryRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("NotificationsPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Mutation hooks are not the focus of these tests, but the component calls
    // them on every render. Provide inert default implementations so renders do
    // not crash; resetAllMocks above clears these between tests.
    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);
    vi.mocked(useAcknowledgeAllFailed).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcknowledgeAllFailed>);
  });

  it("renders notification rows when list returns data", () => {
    setStatsState({
      data: {
        data: { total: 2, sent: 1, failed: 1, by_channel: { telegram: 1, email: 1 }, by_butler: {} },
        meta: {},
      },
    });
    setNotificationsState({
      data: {
        data: [NOTIFICATION_1, NOTIFICATION_2],
        meta: { total: 2, offset: 0, limit: 20, has_more: false },
      },
    });

    const html = renderPage();

    expect(html).not.toContain("No notifications found");
    expect(html).toContain("Task completed successfully");
    expect(html).toContain("Weekly summary report");
  });

  it("renders empty state when list returns no rows", () => {
    setStatsState({
      data: {
        data: { total: 0, sent: 0, failed: 0, by_channel: {}, by_butler: {} },
        meta: {},
      },
    });
    setNotificationsState({
      data: {
        data: [],
        meta: { total: 0, offset: 0, limit: 20, has_more: false },
      },
    });

    const html = renderPage();
    expect(html).toContain("No notifications found");
  });

  it("wires source_available=false into a named degraded feed state, not the empty state (bu-jad4j.2)", () => {
    // The Switchboard notifications source is unreachable: stats em-dashes its
    // tiles and the feed names the degraded source rather than claiming a clear
    // stream. An empty page here is NOT a truthful "no notifications" result.
    setStatsState({
      data: {
        data: { total: 0, sent: 0, failed: 0, by_channel: {}, by_butler: {}, source_available: false },
        meta: {},
      },
    });
    setNotificationsState({
      data: {
        data: [],
        meta: { total: 0, offset: 0, limit: 20, has_more: false },
        source_available: false,
      },
    });

    const html = renderPage();
    expect(html).toContain('data-testid="notification-feed-source-unavailable"');
    expect(html).not.toContain("No notifications found");
    // Stats tiles em-dash rather than fabricating a green 0.0%.
    expect(html).toContain('data-testid="stat-value-failure-rate"');
    expect(html).not.toContain("0.0%");
  });

  it("shows stats summary counts from stats endpoint", () => {
    setStatsState({
      data: {
        data: { total: 34, sent: 29, failed: 5, by_channel: { telegram: 34 }, by_butler: {} },
        meta: {},
      },
    });
    setNotificationsState({
      data: {
        data: [NOTIFICATION_1],
        meta: { total: 1, offset: 0, limit: 20, has_more: false },
      },
    });

    const html = renderPage();
    // Stats bar shows global totals
    expect(html).toContain("34");
    expect(html).toContain("29");
    expect(html).toContain("5");
  });

  it("renders loading skeleton when notifications are loading", () => {
    setStatsState({ isLoading: false, data: undefined });
    setNotificationsState({ isLoading: true });

    const html = renderPage();
    // Should not crash and should not render a notification list
    expect(html).not.toContain("No notifications found");
  });

  it("exposes Read and Retried in the status filter options", () => {
    // The status filter must surface read/retried so those rows are not hidden
    // (bu-5gf99). Assert against the exported options directly — the Radix
    // <Select> portals its items, so closed-state SSR markup omits them.
    const values = STATUS_OPTIONS.map((o) => o.value);
    expect(values).toContain("read");
    expect(values).toContain("retried");
    expect(values).toContain("sent");
    expect(values).toContain("failed");
  });

  it("calls useNotifications with params that omit sentinel filter values", () => {
    setStatsState({ data: undefined });
    setNotificationsState({ data: undefined });

    renderPage();

    // The default filter state uses channel="all", status="all", butler=""
    // These sentinel values must NOT appear in the params passed to the hook,
    // otherwise they would be forwarded to the backend as literal WHERE clauses.
    const callArgs = vi.mocked(useNotifications).mock.calls[0][0];
    expect(callArgs).toBeDefined();
    // channel, status, and butler should be absent or set to undefined since
    // the page strips sentinel values before building the params object.
    // The page only includes non-"all" channel/status and non-empty butler.
    expect(callArgs?.channel).toBeUndefined();
    expect(callArgs?.status).toBeUndefined();
    expect(callArgs?.butler).toBeUndefined();
  });

  // -------------------------------------------------------------------
  // URL-backed filters (bu-qvnce.13) — the filter bar and any inbound deep
  // link (e.g. the dashboard's "N failed notifications" tile) share the same
  // URL-derived state, so a `?status=failed` link always lands pre-filtered.
  // -------------------------------------------------------------------
  it("hydrates the status filter from a ?status=failed deep link", () => {
    setStatsState({ data: undefined });
    setNotificationsState({ data: undefined });

    renderPage("/notifications?status=failed");

    const callArgs = vi.mocked(useNotifications).mock.calls[0][0];
    expect(callArgs?.status).toBe("failed");
  });

  it("omits page from params when ?page= is absent", () => {
    setStatsState({ data: undefined });
    setNotificationsState({ data: undefined });

    renderPage("/notifications");

    const callArgs = vi.mocked(useNotifications).mock.calls[0][0];
    expect(callArgs?.offset).toBe(0);
  });

  it("computes offset from a ?page= deep link", () => {
    setStatsState({ data: undefined });
    setNotificationsState({ data: undefined });

    renderPage("/notifications?page=2");

    const callArgs = vi.mocked(useNotifications).mock.calls[0][0];
    // PAGE_SIZE is 20 (see NotificationsPage.tsx); page 2 -> offset 40.
    expect(callArgs?.offset).toBe(40);
  });
});

// ---------------------------------------------------------------------------
// j/k list-triage over notification rows (bu-qvnce.11 slice 4):
// NotificationsPage adopts the shared useListTriage hook extracted from
// ApprovalsPage's own former hand-rolled j/k/a/d/x implementation. Only the
// wiring is covered here -- useListTriage's own navigation/act-key mechanics
// are unit-tested directly in use-list-triage.test.tsx.
// ---------------------------------------------------------------------------

describe("NotificationsPage — j/k list-triage (bu-qvnce.11 slice 4)", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);
    vi.mocked(useAcknowledgeAllFailed).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcknowledgeAllFailed>);
    setStatsState({
      data: { data: { total: 2, sent: 1, failed: 1, by_channel: {}, by_butler: {} }, meta: {} },
    });
    setNotificationsState({
      data: {
        data: [NOTIFICATION_1, NOTIFICATION_2],
        meta: { total: 2, offset: 0, limit: 20, has_more: false },
      },
    });
  });

  afterEach(() => {
    if (root) {
      act(() => root!.unmount());
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive(initialPath = "/notifications") {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <NotificationsPage />
        </MemoryRouter>,
      );
    });
  }

  function press(key: string) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  }

  it("j selects the first notification row, moving focus onto it", () => {
    renderLive();
    act(() => press("j"));

    const rows = container!.querySelectorAll('[data-testid="notification-row"]');
    expect(rows.length).toBe(2);
    const first = rows[0] as HTMLElement;
    expect(first.getAttribute("data-notification-id")).toBe(
      document.activeElement?.getAttribute("data-notification-id"),
    );
  });

  it("a marks the selected row read via the shared mutation", () => {
    const markReadMutate = vi.fn();
    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: markReadMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);

    renderLive();
    act(() => press("j")); // select NOTIFICATION_1 (sent, actionable)
    act(() => press("a"));

    expect(markReadMutate).toHaveBeenCalledWith(NOTIFICATION_1.id, expect.anything());
  });

  it("renders the footer hint strip advertising the exact bound keys", () => {
    renderLive();
    act(() => press("j"));

    expect(container!.textContent).toContain("Next item");
    expect(container!.textContent).toContain("Previous item");
    expect(container!.textContent).toContain("Mark read");
  });

  it("renders no footer hint strip when there are no notifications", () => {
    setNotificationsState({
      data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
    });
    renderLive();
    expect(container!.querySelector('[aria-label="Keyboard shortcuts for this list"]')).toBeNull();
  });

  it("skips the mark-read act key for an already-read row (mirrors the feed's own gating)", () => {
    const markReadMutate = vi.fn();
    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: markReadMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);
    setNotificationsState({
      data: {
        data: [NOTIFICATION_READ],
        meta: { total: 1, offset: 0, limit: 20, has_more: false },
      },
    });

    renderLive();
    act(() => press("j")); // select the (only, already-read) row
    act(() => press("a"));

    expect(markReadMutate).not.toHaveBeenCalled();
    expect(container!.textContent).not.toContain("Mark read");
  });
});

// ---------------------------------------------------------------------------
// Debounced filter feedback
// ---------------------------------------------------------------------------

describe("NotificationsPage — debounced filter feedback", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);
    vi.mocked(useAcknowledgeAllFailed).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcknowledgeAllFailed>);
    setStatsState({
      data: { data: { total: 1, sent: 1, failed: 0, by_channel: {}, by_butler: {} }, meta: {} },
    });
    setNotificationsState({
      data: {
        data: [NOTIFICATION_1],
        meta: { total: 1, offset: 0, limit: 20, has_more: false },
      },
      isFetching: false,
    });
  });

  afterEach(() => {
    if (root) {
      act(() => root!.unmount());
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive(initialPath = "/notifications") {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const renderedRoot = root;
    act(() => {
      renderedRoot.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <NotificationsPage />
          <LocationProbe />
        </MemoryRouter>,
      );
    });
  }

  it("marks visible rows busy while the URL has a new butler filter before its query is debounced", () => {
    vi.useFakeTimers();
    try {
      renderLive();
      const input = container!.querySelector<HTMLInputElement>("#filter-butler");
      expect(input).not.toBeNull();

      fireEvent.change(input!, { target: { value: "relationship" } });

      expect(container!.querySelector('[data-testid="location-search"]')?.textContent).toContain(
        "butler=relationship",
      );
      expect(container!.querySelector("[aria-busy]")?.getAttribute("aria-busy")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(299);
      });
      expect(container!.querySelector("[aria-busy]")?.getAttribute("aria-busy")).toBe("true");
    } finally {
      vi.useRealTimers();
    }
  });
});
