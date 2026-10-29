// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { cleanup, render as mount, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { CalendarVerdictOpener } from "@/components/calendar/CalendarVerdictOpener";

function render(overrides: Partial<React.ComponentProps<typeof CalendarVerdictOpener>> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <CalendarVerdictOpener
        entriesCount={14}
        sourceCount={3}
        rangeLabel="week"
        workspaceLoading={false}
        workspaceError={false}
        sourceFreshnessLoading={false}
        sourceFreshnessError={false}
        freshnessDetail={null}
        conflictScanEnabled={true}
        conflictLoading={false}
        conflictError={false}
        conflictsAvailable={true}
        conflicts={[]}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

describe("CalendarVerdictOpener", () => {
  it("states a calm, conflict-checked week only when every source is available", () => {
    const html = render();

    expect(html).toContain("Quiet week: 14 events across 3 sources, no scheduling conflicts");
    expect(html).toContain("calendar-verdict-all-clear");
  });

  it("names an unavailable conflict scan instead of calling the week quiet", () => {
    const html = render({ conflictsAvailable: false });

    expect(html).toContain("calendar conflict scan unavailable");
    expect(html).not.toContain("calendar-verdict-all-clear");
    const unknown = render({ invitationsAvailable: false });
    expect(unknown).toContain("calendar invitations unavailable");
    expect(unknown).not.toContain("No unanswered invitations.");
    expect(unknown).not.toContain("calendar-verdict-all-clear");
    const loading = render({ invitationsLoading: true });
    expect(loading).toContain("Loading invitations");
    expect(loading).not.toContain("No unanswered invitations.");
    expect(loading).not.toContain("calendar-verdict-all-clear");
    const refreshing = render({ invitationsRefreshing: true });
    expect(refreshing).toContain("Refreshing invitations");
    expect(refreshing).not.toContain("No unanswered invitations.");
    expect(refreshing).not.toContain("calendar-verdict-all-clear");
  });

  it("adds a concrete conflict clause when the scan finds conflicts", () => {
    const html = render({ conflicts: [{} as never] });

    expect(html).toContain("1 scheduling conflict in view");
    expect(html).not.toContain("calendar-verdict-all-clear");
  });

  it("opens only actual invitation and radar evidence doors with the keyboard", async () => {
    const open = vi.fn();
    const more = vi.fn();
    const issue = { kind: "overlap" as const, date: "2026-07-01", summary: "Actual overlap",
      severity: "warning" as const, proposal_ids: [], events: [{ entry_id: "invited", title: "Invitation",
        start_at: "2026-07-01T09:00:00Z", end_at: "2026-07-01T10:00:00Z", timezone: "UTC", status: "confirmed" }] };
    const props = { entriesCount: 1, sourceCount: 1, rangeLabel: "week", workspaceLoading: false,
      workspaceError: false, sourceFreshnessLoading: false, sourceFreshnessError: false,
      freshnessDetail: null, conflictScanEnabled: true, conflictLoading: false, conflictError: false,
      conflictsAvailable: true, conflicts: [], invitations: [{ entry_id: "invited", title: "Invitation",
        start_at: "2026-07-01T09:00:00Z", end_at: "2026-07-01T10:00:00Z", timezone: "UTC", butler_name: "general",
        organizer: null, organizer_source: "unknown" as const, conflict_issues: [issue] }],
      invitationsHasMore: true, onOpenInvitation: open, onMoreInvitations: more };
    try {
      mount(<MemoryRouter><CalendarVerdictOpener {...props} /></MemoryRouter>);
      const user = userEvent.setup();
      expect(screen.getByText("Organizer unknown")).toBeTruthy();
      expect(screen.queryByTestId("calendar-verdict-all-clear")).toBeNull();
      await user.tab();
      await user.keyboard("{Enter}");
      expect(open).toHaveBeenCalledWith("invited");
      await user.tab();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("region", { name: "Invitation conflict evidence" }).textContent).toContain("Actual overlap");
      await user.click(screen.getByRole("button", { name: "More invitations" }));
      expect(more).toHaveBeenCalledTimes(1);
    } finally { cleanup(); }
  });
});
