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
});
