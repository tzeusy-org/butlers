// @vitest-environment jsdom
/**
 * ConflictRadarBanner — RTL tests (bu-q8o90x).
 *
 * Covers the acceptance contract:
 *  - renders ONLY when issues exist and the scan is available,
 *  - silent on degraded mode (available=false) and on a clean window,
 *  - expands to per-issue cards with contributing event titles,
 *  - Accept/Decline actions fire only when a pending proposal exists,
 *  - dismiss hides the banner for the session.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { ConflictIssue } from "@/api/types.ts";

import { ConflictRadarBanner } from "./ConflictRadarBanner.tsx";

afterEach(cleanup);

function overlapIssue(overrides: Partial<ConflictIssue> = {}): ConflictIssue {
  return {
    kind: "overlap",
    date: "2026-07-01",
    summary: "“Design review” and “1:1” overlap by 30 min",
    severity: "warning",
    events: [
      {
        entry_id: "a",
        title: "Design review",
        start_at: "2026-07-01T09:00:00Z",
        end_at: "2026-07-01T10:00:00Z",
        timezone: "UTC",
        status: "confirmed",
      },
      {
        entry_id: "b",
        title: "1:1",
        start_at: "2026-07-01T09:30:00Z",
        end_at: "2026-07-01T10:30:00Z",
        timezone: "UTC",
        status: "tentative",
      },
    ],
    proposal_ids: [],
    ...overrides,
  };
}

describe("ConflictRadarBanner", () => {
  it("renders a banner when issues exist in the window", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available />);
    expect(screen.getByTestId("conflict-radar-banner")).toBeTruthy();
    expect(screen.getByText(/overlap/i)).toBeTruthy();
  });

  it("renders nothing on a clean window", () => {
    const { container } = render(<ConflictRadarBanner issues={[]} available />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing in degraded mode (available=false)", () => {
    const { container } = render(
      <ConflictRadarBanner issues={[overlapIssue()]} available={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("expands to show contributing event titles", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available />);
    fireEvent.click(screen.getByText("Review"));
    expect(screen.getByText("Design review")).toBeTruthy();
    expect(screen.getByText("1:1")).toBeTruthy();
  });

  it("shows Accept/Decline only when a pending proposal exists", () => {
    const onAccept = vi.fn();
    const onDismiss = vi.fn();
    render(
      <ConflictRadarBanner
        issues={[overlapIssue({ proposal_ids: ["p1"] })]}
        available
        onAcceptProposal={onAccept}
        onDismissProposal={onDismiss}
      />,
    );
    fireEvent.click(screen.getByText("Review"));
    fireEvent.click(screen.getByText("Accept fix"));
    expect(onAccept).toHaveBeenCalledWith("p1");
    fireEvent.click(screen.getByText("Decline"));
    expect(onDismiss).toHaveBeenCalledWith("p1");
  });

  it("is informational (no fix button) when no proposal exists yet", () => {
    render(<ConflictRadarBanner issues={[overlapIssue({ proposal_ids: [] })]} available />);
    fireEvent.click(screen.getByText("Review"));
    expect(screen.queryByText("Accept fix")).toBeNull();
    expect(screen.getByText(/No suggested fix yet/i)).toBeTruthy();
  });

  it("dismiss hides the banner for the session", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available />);
    fireEvent.click(screen.getByLabelText("Dismiss conflict radar"));
    expect(screen.queryByTestId("conflict-radar-banner")).toBeNull();
  });
});
