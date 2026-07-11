// @vitest-environment jsdom
/**
 * Tests for IssuesPanel.
 *
 * Covers:
 *  - isError state shows an error message (NOT the "No issues recorded" empty state)
 *  - empty state shows "No issues recorded."
 *  - Acknowledge button calls onDismiss with the full issue (real ack, not a
 *    per-browser localStorage write) -- acknowledge-until-recurrence
 *    (bu-86c4c.15), not dismiss-forever
 *  - Ping butler / Run schedule now remedies (bu-86c4c.15) render only for a
 *    single identified butler and call their handlers with the butler name
 */

import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import IssuesPanel from "./IssuesPanel";
import type { AuditLogEntry, Issue } from "../../api/types";

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

  it("calls onDismiss with the full issue when Acknowledge is clicked", () => {
    const onDismiss = vi.fn();
    const issue = makeIssue();
    renderPanel({ issues: [issue], onDismiss });

    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledWith(issue);
  });

  it("disables Acknowledge while an ack is in flight", () => {
    const issue = makeIssue();
    renderPanel({ issues: [issue], onDismiss: vi.fn(), isDismissing: true });

    const button = screen.getByRole("button", { name: "Acknowledge" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("renders a Restore button (not Acknowledge) in the dismissed view", () => {
    const onRestore = vi.fn();
    const issue = makeIssue({ dismissed: true });
    renderPanel({ issues: [issue], dismissedView: true, onRestore });

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith("audit_error_group:boom::general");
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
  });

  it("disables Restore while a restore is in flight", () => {
    const issue = makeIssue({ dismissed: true });
    renderPanel({ issues: [issue], dismissedView: true, onRestore: vi.fn(), isRestoring: true });

    const button = screen.getByRole("button", { name: "Restore" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("shows an acknowledged-specific empty state in the dismissed view", () => {
    renderPanel({ issues: [], dismissedView: true });

    expect(screen.getByText("No acknowledged issues.")).toBeTruthy();
    expect(screen.queryByText("No issues recorded.")).toBeNull();
  });

  // Degraded feed (bu-tpudw.3): a backend source that failed its query is
  // named in meta.sources_degraded. The panel must never let that read as an
  // honest all-clear — the three-way distinction below is tested both
  // directions so the flag can't be ignored.
  describe("degraded feed (bu-tpudw.3)", () => {
    it("names the degraded source in place of the all-clear when zero issues survived", () => {
      renderPanel({ issues: [], sourcesDegraded: ["audit-groups"] });

      const note = screen.getByTestId("issues-feed-degraded");
      expect(note.textContent).toContain("Issues feed");
      // Mutation strength: the source name is surfaced, not swallowed.
      expect(note.textContent).toContain("audit-groups");
      // The misleading all-clear MUST NOT render while a source is degraded.
      expect(screen.queryByText("No issues recorded.")).toBeNull();
    });

    it("renders the degraded note above the rows when some issues did survive", () => {
      renderPanel({ issues: [makeIssue()], sourcesDegraded: ["acks"] });

      const note = screen.getByTestId("issues-feed-degraded");
      expect(note.textContent).toContain("acks");
      // The surviving rows are still shown alongside the degraded note.
      expect(screen.getByText("boom (general)")).toBeTruthy();
    });

    it("keeps the honest empty state when no source is degraded", () => {
      renderPanel({ issues: [], sourcesDegraded: [] });

      expect(screen.getByText("No issues recorded.")).toBeTruthy();
      expect(screen.queryByTestId("issues-feed-degraded")).toBeNull();
    });

    it("does not render the degraded note on a transport error (error state wins)", () => {
      renderPanel({ issues: [], isError: true, sourcesDegraded: ["acks"] });

      // A hard fetch failure is the existing ErrorState's job, not the
      // partial-degraded note (which only applies to a 200 with a dropped
      // source).
      expect(screen.getByText("Could not load issues.")).toBeTruthy();
      expect(screen.queryByTestId("issues-feed-degraded")).toBeNull();
    });
  });

  describe("Ping butler remedy", () => {
    it("renders for a single-butler 'unreachable' issue and calls onPingButler with the butler name", () => {
      const onPingButler = vi.fn();
      const issue = makeIssue({ type: "unreachable", butler: "general" });
      renderPanel({ issues: [issue], onPingButler });

      fireEvent.click(screen.getByRole("button", { name: "Ping butler" }));

      expect(onPingButler).toHaveBeenCalledTimes(1);
      expect(onPingButler).toHaveBeenCalledWith("general");
    });

    it("does not render for a grouped multi-butler issue", () => {
      const onPingButler = vi.fn();
      const issue = makeIssue({
        type: "unreachable",
        butler: "multiple",
        butlers: ["general", "finance"],
      });
      renderPanel({ issues: [issue], onPingButler });

      expect(screen.queryByRole("button", { name: "Ping butler" })).toBeNull();
    });

    it("does not render for a non-'unreachable' issue type", () => {
      const onPingButler = vi.fn();
      const issue = makeIssue({ type: "audit_error_group:boom", butler: "general" });
      renderPanel({ issues: [issue], onPingButler });

      expect(screen.queryByRole("button", { name: "Ping butler" })).toBeNull();
    });

    it("shows a pending label and disables the control for the butler currently being pinged", () => {
      const issue = makeIssue({ type: "unreachable", butler: "general" });
      renderPanel({
        issues: [issue],
        onPingButler: vi.fn(),
        pendingPingButler: "general",
      });

      const button = screen.getByRole("button", { name: "Pinging…" }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    });

    it("does not render when onPingButler is not wired", () => {
      const issue = makeIssue({ type: "unreachable", butler: "general" });
      renderPanel({ issues: [issue] });

      expect(screen.queryByRole("button", { name: "Ping butler" })).toBeNull();
    });
  });

  describe("Run schedule now remedy", () => {
    it("renders for any single-butler issue and calls onRunScheduleNow with the butler name", () => {
      const onRunScheduleNow = vi.fn();
      const issue = makeIssue({ type: "audit_error_group:boom", butler: "general" });
      renderPanel({ issues: [issue], onRunScheduleNow });

      fireEvent.click(screen.getByRole("button", { name: "Run schedule now" }));

      expect(onRunScheduleNow).toHaveBeenCalledTimes(1);
      expect(onRunScheduleNow).toHaveBeenCalledWith("general");
    });

    it("does not render for a grouped multi-butler issue", () => {
      const onRunScheduleNow = vi.fn();
      const issue = makeIssue({ butler: "multiple", butlers: ["general", "finance"] });
      renderPanel({ issues: [issue], onRunScheduleNow });

      expect(screen.queryByRole("button", { name: "Run schedule now" })).toBeNull();
    });

    it("shows a pending label and disables the control for the butler currently ticking", () => {
      const issue = makeIssue({ butler: "general" });
      renderPanel({
        issues: [issue],
        onRunScheduleNow: vi.fn(),
        pendingRunNowButler: "general",
      });

      const button = screen.getByRole("button", { name: "Running…" }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    });

    it("does not render in the dismissed (acknowledged) view", () => {
      const onRunScheduleNow = vi.fn();
      const issue = makeIssue({ butler: "general", dismissed: true });
      renderPanel({ issues: [issue], dismissedView: true, onRunScheduleNow, onRestore: vi.fn() });

      expect(screen.queryByRole("button", { name: "Run schedule now" })).toBeNull();
    });
  });

  describe("Occurrences drill-down (JARVIS audit move 6)", () => {
    function makeOccurrence(overrides: Partial<AuditLogEntry> = {}): AuditLogEntry {
      return {
        id: 1,
        ts: "2026-06-14T10:30:00.000Z",
        actor: "general",
        action: "oauth_refresh",
        target: null,
        note: null,
        ip: null,
        request_id: null,
        ...overrides,
      };
    }

    it("does not render as a disclosure control when onToggleOccurrences is not wired", () => {
      const issue = makeIssue();
      renderPanel({ issues: [issue] });

      expect(screen.queryByRole("button", { name: /general/ })).toBeNull();
    });

    it("calls onToggleOccurrences with the issue key when an audit-derived row is clicked", () => {
      const onToggleOccurrences = vi.fn();
      const issue = makeIssue();
      renderPanel({ issues: [issue], onToggleOccurrences });

      // The row itself is the disclosure control (role="button" via DisclosureRow).
      const row = screen.getByRole("button", { expanded: false });
      fireEvent.click(row);

      expect(onToggleOccurrences).toHaveBeenCalledWith("audit_error_group:boom::general");
    });

    it("does not treat an 'unreachable' (non-drillable) issue as a disclosure control", () => {
      const onToggleOccurrences = vi.fn();
      const issue = makeIssue({ type: "unreachable" });
      renderPanel({ issues: [issue], onToggleOccurrences });

      expect(screen.queryByRole("button", { expanded: false })).toBeNull();
    });

    it("renders occurrences (with a session link) when the row is expanded", () => {
      const issue = makeIssue();
      const occurrence = makeOccurrence({ request_id: "req-abc" });
      renderPanel({
        issues: [issue],
        onToggleOccurrences: vi.fn(),
        expandedIssueKey: issue.issue_key,
        occurrences: [occurrence],
      });

      expect(screen.getByText("oauth_refresh")).toBeTruthy();
      const sessionLink = screen.getByRole("link", { name: /Session/ }) as HTMLAnchorElement;
      expect(sessionLink.getAttribute("href")).toBe("/sessions?request=req-abc");
    });

    it("shows a loading state while occurrences are being fetched", () => {
      const issue = makeIssue();
      renderPanel({
        issues: [issue],
        onToggleOccurrences: vi.fn(),
        expandedIssueKey: issue.issue_key,
        occurrences: [],
        occurrencesLoading: true,
      });

      expect(screen.queryByText("No occurrences found for this group.")).toBeNull();
    });

    it("clicking a nested action control (Acknowledge) does not also toggle the disclosure row", () => {
      const onToggleOccurrences = vi.fn();
      const onDismiss = vi.fn();
      const issue = makeIssue();
      renderPanel({ issues: [issue], onToggleOccurrences, onDismiss });

      fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));

      expect(onDismiss).toHaveBeenCalledTimes(1);
      expect(onToggleOccurrences).not.toHaveBeenCalled();
    });
  });
});
