/**
 * Regression tests for the Notifications page.
 *
 * Covers the mismatch bug where summary stats showed non-zero counts but the
 * list panel rendered "No notifications found" due to sentinel filter values
 * ("all", "") being forwarded to the backend as literal WHERE conditions.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

import NotificationsPage from "@/pages/NotificationsPage";
import { useNotifications, useNotificationStats } from "@/hooks/use-notifications";

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications: vi.fn(),
  useNotificationStats: vi.fn(),
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
  error: "SMTP connection refused",
  session_id: null,
  trace_id: null,
  created_at: "2026-02-19T08:00:00Z",
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

function renderPage(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <NotificationsPage />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("NotificationsPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
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
});
