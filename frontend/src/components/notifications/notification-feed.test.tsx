// @vitest-environment jsdom
/**
 * Tests for the NotificationFeed triage controls.
 *
 * Regression for bu-5gf99: triage controls (mark-read / dismiss) previously
 * rendered only on `failed` rows, so normal `sent` notifications had no triage
 * affordance. The backend PATCH /{id}/read works for any status, so the control
 * should appear on any actionable (unread) row and disappear once read.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { NotificationFeed } from "@/components/notifications/notification-feed";
import type { NotificationSummary } from "@/api/types";

function makeNotification(
  overrides: Partial<NotificationSummary> = {},
): NotificationSummary {
  return {
    id: "notif-1",
    source_butler: "switchboard",
    channel: "telegram",
    recipient: "@user",
    message: "Hello world",
    metadata: null,
    status: "sent",
    effective_status: "sent",
    error: null,
    session_id: null,
    trace_id: null,
    created_at: "2026-02-20T10:00:00Z",
    ...overrides,
  };
}

function renderFeed(props: Parameters<typeof NotificationFeed>[0]) {
  return render(
    <MemoryRouter>
      <NotificationFeed {...props} />
    </MemoryRouter>,
  );
}

describe("NotificationFeed triage controls", () => {
  afterEach(() => cleanup());

  it("renders mark-read on a sent row (not just failed)", () => {
    renderFeed({
      notifications: [makeNotification({ status: "sent", effective_status: "sent" })],
      onMarkRead: vi.fn(),
    });
    expect(screen.getByRole("button", { name: "Mark read" })).toBeDefined();
  });

  it("renders mark-read on a failed row", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "f", status: "failed", effective_status: "failed" }),
      ],
      onMarkRead: vi.fn(),
    });
    expect(screen.getByRole("button", { name: "Mark read" })).toBeDefined();
  });

  it("renders a dismiss affordance when onDismiss is provided", () => {
    renderFeed({
      notifications: [makeNotification()],
      onMarkRead: vi.fn(),
      onDismiss: vi.fn(),
    });
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDefined();
  });

  it("hides triage controls on an already-read row", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "r", status: "read", effective_status: "read" }),
      ],
      onMarkRead: vi.fn(),
      onDismiss: vi.fn(),
    });
    expect(screen.queryByRole("button", { name: "Mark read" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });

  it("fires onMarkRead with the row id when Mark read is clicked", () => {
    const onMarkRead = vi.fn();
    renderFeed({
      notifications: [makeNotification({ id: "wire-mark" })],
      onMarkRead,
    });
    fireEvent.click(screen.getByRole("button", { name: "Mark read" }));
    expect(onMarkRead).toHaveBeenCalledWith("wire-mark");
  });

  it("fires onDismiss with the row id when Dismiss is clicked", () => {
    const onDismiss = vi.fn();
    renderFeed({
      notifications: [makeNotification({ id: "wire-dismiss" })],
      onMarkRead: vi.fn(),
      onDismiss,
    });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledWith("wire-dismiss");
  });

  it("links the Trace cell to the ingestion timeline pre-filtered by that trace (bu-86c4c.3)", () => {
    renderFeed({
      notifications: [makeNotification({ trace_id: "trace-abc" })],
    });
    const traceLink = screen.getByRole("link", { name: /Trace/ });
    expect(traceLink.getAttribute("href")).toBe(
      `/ingestion?trace=${encodeURIComponent("trace-abc")}`,
    );
  });
});

// ---------------------------------------------------------------------------
// Degraded-source honesty (bu-jad4j.2): an empty page with source_available=
// false is the Switchboard source being unreachable, not a genuinely clear
// stream. It must render a named degraded note, never the calm empty state.
// ---------------------------------------------------------------------------

describe("NotificationFeed degraded-source honesty", () => {
  afterEach(() => cleanup());

  it("names the degraded source (not the empty state) when the list is empty and source is down", () => {
    renderFeed({ notifications: [], sourceUnavailable: true });

    const note = screen.getByTestId("notification-feed-source-unavailable");
    expect(note.getAttribute("role")).toBe("alert");
    // Named inline with an em-dash qualifier — never suppressed.
    expect(note.textContent).toContain("Notifications");
    expect(note.textContent).toContain("—");
    // The calm empty-state copy must NOT appear alongside the degraded note.
    expect(screen.queryByText("No notifications found.")).toBeNull();
  });

  it("keeps the honest empty state for a reachable-but-empty source", () => {
    // Mutation guard: the degraded note must depend on the flag. With the source
    // reachable, an empty list is a legitimate all-clear.
    renderFeed({ notifications: [], sourceUnavailable: false });

    expect(screen.queryByTestId("notification-feed-source-unavailable")).toBeNull();
    expect(screen.getByText("No notifications found.")).toBeDefined();
  });

  it("renders rows normally even if the flag is set (rows win over the note)", () => {
    // A non-empty page still shows its rows; the degraded note only stands in
    // for the empty state.
    renderFeed({
      notifications: [makeNotification({ message: "Delivered anyway" })],
      sourceUnavailable: true,
    });
    expect(screen.getByText("Delivered anyway")).toBeDefined();
    expect(screen.queryByTestId("notification-feed-source-unavailable")).toBeNull();
  });
});
