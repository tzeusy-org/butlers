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

  it("keeps loading headers in loaded-column order and adds Actions only for triage controls", () => {
    const { rerender } = renderFeed({ notifications: [], isLoading: true });

    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Status",
      "Butler",
      "Recipient",
      "Channel",
      "Message",
      "Time",
    ]);

    rerender(
      <MemoryRouter>
        <NotificationFeed notifications={[]} isLoading onMarkRead={vi.fn()} />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Status",
      "Butler",
      "Recipient",
      "Channel",
      "Message",
      "Time",
      "Actions",
    ]);
  });

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
    // Named inline (source + reason), never suppressed.
    expect(note.textContent).toContain("Notifications");
    expect(note.textContent).toContain("incomplete");
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

describe("NotificationFeed truncated-cell detail affordance (bu-x7z84)", () => {
  afterEach(() => cleanup());

  const LONG_MESSAGE =
    "This is a very long notification message that comfortably exceeds the sixty " +
    "character collapsed preview clamp and must be reachable by keyboard.";

  it("offers no detail toggle for a short (unclipped) message", () => {
    renderFeed({ notifications: [makeNotification({ message: "short and sweet" })] });
    expect(screen.queryByTestId("notification-detail-toggle")).toBeNull();
  });

  it("offers a keyboard-reachable detail toggle when the message is clipped", () => {
    renderFeed({ notifications: [makeNotification({ message: LONG_MESSAGE })] });
    const toggle = screen.getByTestId("notification-detail-toggle");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Full message is not present until disclosed.
    expect(screen.queryByTestId("notification-detail-message")).toBeNull();
  });

  it("discloses the full message text on activation", () => {
    renderFeed({ notifications: [makeNotification({ message: LONG_MESSAGE })] });
    fireEvent.click(screen.getByTestId("notification-detail-toggle"));
    const full = screen.getByTestId("notification-detail-message");
    expect(full.textContent).toBe(LONG_MESSAGE);
    expect(screen.getByTestId("notification-detail-toggle").getAttribute("aria-expanded")).toBe(
      "true",
    );
  });

  it("offers the toggle and discloses the full error on a failed row with a long error", () => {
    const LONG_ERROR =
      "Delivery failed: upstream channel returned a 502 after three retries with an " +
      "extended diagnostic payload that overflows the eighty character error clamp.";
    renderFeed({
      notifications: [
        makeNotification({
          id: "f",
          status: "failed",
          effective_status: "failed",
          message: "short",
          error: LONG_ERROR,
        }),
      ],
    });
    fireEvent.click(screen.getByTestId("notification-detail-toggle"));
    expect(screen.getByTestId("notification-detail-error").textContent).toBe(LONG_ERROR);
  });

  it("offers no toggle for a short message with no error", () => {
    renderFeed({
      notifications: [
        makeNotification({ status: "failed", effective_status: "failed", message: "boom", error: null }),
      ],
    });
    expect(screen.queryByTestId("notification-detail-toggle")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Retry / Escalate (bu-ep4ks.4 -- delivery-receipt spine): a failed
// notification is not just a dead-end row -- it must offer an actual action
// to re-attempt delivery, not just prose saying "failed".
// ---------------------------------------------------------------------------

describe("NotificationFeed retry/escalate controls", () => {
  afterEach(() => cleanup());

  it("offers Retry and Escalate on a failed row when both handlers are wired", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "f", status: "failed", effective_status: "failed" }),
      ],
      onRetry: vi.fn(),
      onEscalate: vi.fn(),
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeDefined();
  });

  it("does not offer Retry/Escalate on a sent row", () => {
    renderFeed({
      notifications: [makeNotification({ status: "sent", effective_status: "sent" })],
      onRetry: vi.fn(),
      onEscalate: vi.fn(),
    });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Escalate" })).toBeNull();
  });

  it("hides Retry/Escalate once the row has been actioned (already read)", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "r", status: "read", effective_status: "retried" }),
      ],
      onRetry: vi.fn(),
      onEscalate: vi.fn(),
    });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Escalate" })).toBeNull();
  });

  it("fires onRetry with the row id when Retry is clicked", () => {
    const onRetry = vi.fn();
    renderFeed({
      notifications: [
        makeNotification({ id: "wire-retry", status: "failed", effective_status: "failed" }),
      ],
      onRetry,
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledWith("wire-retry");
  });

  it("fires onEscalate with the row id when Escalate is clicked", () => {
    const onEscalate = vi.fn();
    renderFeed({
      notifications: [
        makeNotification({ id: "wire-escalate", status: "failed", effective_status: "failed" }),
      ],
      onEscalate,
    });
    fireEvent.click(screen.getByRole("button", { name: "Escalate" }));
    expect(onEscalate).toHaveBeenCalledWith("wire-escalate");
  });

  it("shows a pending label and disables both buttons while a retry is in flight", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "pending-retry", status: "failed", effective_status: "failed" }),
      ],
      onRetry: vi.fn(),
      onEscalate: vi.fn(),
      pendingRetryIds: new Set(["pending-retry"]),
    });
    const retryButton = screen.getByRole("button", { name: "Retrying…" });
    expect(retryButton).toBeDefined();
    expect(retryButton.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Escalate" }).hasAttribute("disabled")).toBe(true);
  });

  it("renders the Escalated chip for a manually-escalated row", () => {
    renderFeed({
      notifications: [
        makeNotification({ id: "esc", status: "read", effective_status: "escalated" }),
      ],
    });
    expect(screen.getByText("Escalated")).toBeDefined();
  });
});
