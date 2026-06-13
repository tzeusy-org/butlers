// @vitest-environment jsdom
/**
 * Tests for IssuesPanel.
 *
 * Covers:
 *  - isError state shows an error message (NOT the "No issues recorded" empty state)
 *  - empty state shows "No issues recorded."
 *  - Dismiss button calls onDismiss with the issue's stable server key (real ack,
 *    not a per-browser localStorage write)
 */

import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import IssuesPanel from "./IssuesPanel";
import type { Issue } from "../../api/types";

function makeIssue(overrides: Partial<Issue> = {}): Issue {
  return {
    severity: "warning",
    type: "audit_error_group:boom",
    butler: "general",
    description: "boom (general)",
    link: "/audit-log?butler=general",
    error_message: "boom",
    occurrences: 2,
    first_seen_at: "2026-06-14T10:00:00.000Z",
    last_seen_at: "2026-06-14T11:00:00.000Z",
    butlers: ["general"],
    issue_key: "audit_error_group:boom::general",
    ...overrides,
  };
}

function renderPanel(props: Partial<ComponentProps<typeof IssuesPanel>>) {
  return render(
    <MemoryRouter>
      <IssuesPanel issues={[]} {...props} />
    </MemoryRouter>,
  );
}

afterEach(() => cleanup());

describe("IssuesPanel", () => {
  it("shows an error state (not the empty 'No issues recorded') when isError", () => {
    renderPanel({ issues: [], isError: true });

    expect(screen.getByText("Could not load issues.")).toBeTruthy();
    // The misleading empty state must NOT be shown on a fetch failure.
    expect(screen.queryByText("No issues recorded.")).toBeNull();
  });

  it("shows the empty state when there are genuinely no issues", () => {
    renderPanel({ issues: [], isError: false });

    expect(screen.getByText("No issues recorded.")).toBeTruthy();
    expect(screen.queryByText("Could not load issues.")).toBeNull();
  });

  it("calls onDismiss with the issue's stable server key when Dismiss is clicked", () => {
    const onDismiss = vi.fn();
    const issue = makeIssue();
    renderPanel({ issues: [issue], onDismiss });

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledWith("audit_error_group:boom::general");
  });

  it("disables Dismiss while a dismissal is in flight", () => {
    const issue = makeIssue();
    renderPanel({ issues: [issue], onDismiss: vi.fn(), isDismissing: true });

    const button = screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
