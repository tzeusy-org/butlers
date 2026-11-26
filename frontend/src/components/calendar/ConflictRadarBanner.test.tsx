// @vitest-environment jsdom
/**
 * ConflictRadarBanner — RTL tests (bu-q8o90x).
 *
 * Covers the acceptance contract:
 *  - renders ONLY when issues exist and the scan is available,
 *  - silent on degraded mode (available=false) and on a clean window,
 *  - expands to per-issue cards with contributing event titles,
 *  - an overlap's offered options render and resolve with the issue they belong to,
 *  - digits 1-9 pick the nth option, and never while typing,
 *  - there is no session dismiss: "keep both" is the recorded way to quiet an overlap.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { ConflictIssue, ResolutionOption } from "@/api/types.ts";

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
    ...overrides,
  };
}

function option(
  kind: ResolutionOption["kind"],
  rationale: Record<string, unknown> = {},
): ResolutionOption {
  return {
    id: `${kind}:b`,
    kind,
    authority: "owner_submit",
    exact_call: { tool: "x", args: { pair_id: "pair-1" } },
    rationale_fields: rationale,
  };
}

const OFFERED = {
  issue_digest: "d1",
  options: [
    option("move_own", { entry_id: "b", slot_start: "2026-07-01T14:00:00Z" }),
    option("decline", { entry_id: "b" }),
    option("keep_both"),
  ],
};

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

  it("renders each offered option and resolves it with its issue", () => {
    const onResolve = vi.fn();
    const issue = overlapIssue(OFFERED);
    render(<ConflictRadarBanner issues={[issue]} available onResolve={onResolve} />);
    fireEvent.click(screen.getByText("Review"));

    fireEvent.click(screen.getByRole("button", { name: /Decline 1:1/ }));

    expect(onResolve).toHaveBeenCalledWith(issue, OFFERED.options[1]);
    expect(screen.getByRole("button", { name: /Keep both/ })).toBeTruthy();
  });

  it("offers nothing to click when the scan compiled no options", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available />);
    fireEvent.click(screen.getByText("Review"));
    expect(screen.queryByRole("button", { name: /Keep both/ })).toBeNull();
  });

  it("digit keys pick the nth option of the first overlap with options", () => {
    const onResolve = vi.fn();
    const issue = overlapIssue(OFFERED);
    render(<ConflictRadarBanner issues={[issue]} available onResolve={onResolve} />);
    fireEvent.click(screen.getByText("Review"));

    fireEvent.keyDown(window, { key: "3" });

    expect(onResolve).toHaveBeenCalledWith(issue, OFFERED.options[2]);
    fireEvent.keyDown(window, { key: "9" }); // no ninth option
    expect(onResolve).toHaveBeenCalledTimes(1);
  });

  it("digit keys do nothing while collapsed, typing, or resolving", () => {
    const onResolve = vi.fn();
    const issue = overlapIssue(OFFERED);
    const { rerender } = render(
      <div>
        <input aria-label="note" />
        <ConflictRadarBanner issues={[issue]} available onResolve={onResolve} />
      </div>,
    );
    fireEvent.keyDown(window, { key: "1" }); // collapsed
    fireEvent.click(screen.getByText("Review"));
    fireEvent.keyDown(screen.getByLabelText("note"), { key: "1" }); // typing
    expect(onResolve).not.toHaveBeenCalled();

    rerender(
      <div>
        <input aria-label="note" />
        <ConflictRadarBanner issues={[issue]} available onResolve={onResolve} isResolving />
      </div>,
    );
    fireEvent.keyDown(window, { key: "1" });
    expect(onResolve).not.toHaveBeenCalled();
  });

  it("disables the options while a resolve is in flight", () => {
    const onResolve = vi.fn();
    render(
      <ConflictRadarBanner
        issues={[overlapIssue(OFFERED)]}
        available
        isResolving
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByText("Review"));
    const keep = screen.getByRole("button", { name: /Keep both/ });
    expect((keep as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(keep);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it("has no session dismiss: keeping both is recorded on the server instead", () => {
    render(<ConflictRadarBanner issues={[overlapIssue()]} available />);
    expect(screen.queryByLabelText("Dismiss conflict radar")).toBeNull();
  });

  it("names an event outside the declared meeting hours in the collapsed summary", () => {
    render(
      <ConflictRadarBanner
        issues={[
          overlapIssue({
            kind: "outside_declared_hours",
            summary: "This event starts before your earliest meeting time",
            reasons: ["before_earliest"],
            events: [overlapIssue().events[0]],
          }),
        ]}
        available
      />,
    );
    expect(screen.getByText(/1 event outside your meeting hours/)).toBeTruthy();
  });

  it("leaves an info-level outside-hours flag out of the day headline", () => {
    render(
      <ConflictRadarBanner
        issues={[
          overlapIssue({
            kind: "outside_declared_hours",
            severity: "info",
            summary: "This event starts before your earliest meeting time",
            reasons: ["before_earliest"],
            events: [overlapIssue().events[0]],
          }),
        ]}
        available
      />,
    );
    expect(screen.queryByText(/outside your meeting hours/)).toBeNull();
  });
});
