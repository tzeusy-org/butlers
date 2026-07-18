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

  it("keeps prior issues visible but dimmed while a new window fetches", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available isFetching />);

    expect(screen.getByTestId("conflict-radar-banner").parentElement?.className).toContain(
      "opacity-60",
    );
  });

  it("shows a query error instead of retained issues", () => {
    render(
      <ConflictRadarBanner
        issues={[overlapIssue()]}
        available
        isError
        error={new Error("conflict scan failed")}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("conflict scan failed");
    expect(screen.queryByTestId("conflict-radar-banner")).toBeNull();
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

  it("uses the neutral Dispatch commit treatment for accepting a fix", () => {
    render(
      <ConflictRadarBanner
        issues={[overlapIssue({ proposal_ids: ["p1"] })]}
        available
      />,
    );

    fireEvent.click(screen.getByText("Review"));
    const acceptFix = screen.getByRole("button", { name: "Accept fix" });

    expect(acceptFix.getAttribute("type")).toBe("button");
    expect(acceptFix.classList.contains("inline-flex")).toBe(true);
    expect(acceptFix.classList.contains("items-center")).toBe(true);
    expect(acceptFix.classList.contains("justify-center")).toBe(true);
    expect(acceptFix.classList.contains("h-7")).toBe(true);
    expect(acceptFix.classList.contains("rounded-[3px]")).toBe(true);
    expect(acceptFix.classList.contains("border")).toBe(true);
    expect(acceptFix.classList.contains("px-2.5")).toBe(true);
    expect(acceptFix.classList.contains("font-mono")).toBe(true);
    expect(acceptFix.classList.contains("text-[11px]")).toBe(true);
    expect(acceptFix.classList.contains("leading-none")).toBe(true);
    expect(acceptFix.classList.contains("transition-colors")).toBe(true);
    expect(acceptFix.classList.contains("bg-fg")).toBe(true);
    expect(acceptFix.classList.contains("text-bg")).toBe(true);
    expect(acceptFix.classList.contains("border-fg")).toBe(true);
    expect(acceptFix.classList.contains("focus-visible:outline-none")).toBe(true);
    expect(acceptFix.classList.contains("focus-visible:ring-2")).toBe(true);
    expect(acceptFix.classList.contains("focus-visible:ring-fg/30")).toBe(true);
    expect(acceptFix.classList.contains("disabled:pointer-events-none")).toBe(true);
    expect(acceptFix.classList.contains("disabled:opacity-40")).toBe(true);
    expect(acceptFix.className).not.toMatch(
      /\b(?:bg|text|border)-(?:red|green|emerald|amber|yellow|orange)-(?:50|100|150|200|300|400|500|600|700|800|900|950)\b/,
    );
  });

  it("disables proposal actions while a proposal mutation is pending", () => {
    const onAccept = vi.fn();
    const onDismiss = vi.fn();
    render(
      <ConflictRadarBanner
        issues={[overlapIssue({ proposal_ids: ["p1"] })]}
        available
        isProposalActionPending
        onAcceptProposal={onAccept}
        onDismissProposal={onDismiss}
      />,
    );

    fireEvent.click(screen.getByText("Review"));
    const acceptFix = screen.getByRole("button", { name: "Accept fix" });
    const decline = screen.getByRole("button", { name: "Decline" });

    expect((acceptFix as HTMLButtonElement).disabled).toBe(true);
    expect((decline as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(acceptFix);
    fireEvent.click(decline);
    expect(onAccept).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
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
