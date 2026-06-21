// @vitest-environment jsdom
/**
 * MeetingPrepRail — RTL tests (bu-rct3g).
 *
 * Covers:
 *  - Populated: attendees with Dunbar-tier letter-mark, notes, last-met, and the
 *    per-attendee message-context panel.
 *  - hasPrepContext=false → honest "No prep context yet" empty-state.
 *  - hasPrepContext=true but zero attendees → same honest empty-state.
 *  - Loading state.
 *  - Message context gracefully empty when absent.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import type { CalendarPrepAttendee } from "@/api/types.ts";

import { MeetingPrepRail } from "./MeetingPrepRail.tsx";

afterEach(cleanup);

function attendee(overrides: Partial<CalendarPrepAttendee> = {}): CalendarPrepAttendee {
  return {
    entity_id: "11111111-1111-1111-1111-111111111111",
    name: "Ada Lovelace",
    dunbar_tier: 5,
    notes: [{ kind: "context", text: "Leads the analytical-engine project." }],
    last_met: "2026-05-01",
    last_met_event: "Quarterly sync",
    message_context: [{ subject: "Re: agenda", from: "ada@example.com" }],
    ...overrides,
  };
}

describe("MeetingPrepRail", () => {
  it("renders attendees with tier mark, notes, last-met, and message context", () => {
    render(<MeetingPrepRail heading="Quarterly sync" hasPrepContext attendees={[attendee()]} sourceButlers={["relationship"]} />);

    const rail = screen.getByTestId("meeting-prep-rail");
    expect(within(rail).getByText("Quarterly sync")).toBeTruthy();

    const card = within(rail).getByTestId("prep-attendee");
    expect(within(card).getByText("Ada Lovelace")).toBeTruthy();
    // Dunbar tier 5 → letter-mark "S".
    expect(within(card).getByTestId("prep-tier-mark").textContent).toContain("S");
    // Relationship note.
    expect(within(card).getByText(/analytical-engine project/)).toBeTruthy();
    // Last-met line (date + co-attended event).
    const lastMet = within(card).getByTestId("prep-last-met");
    expect(lastMet.textContent).toMatch(/2026-05-01/);
    expect(lastMet.textContent).toMatch(/Quarterly sync/);
    // Message-context panel renders the contributed item.
    expect(within(card).getByTestId("prep-message-context")).toBeTruthy();
    expect(within(card).getByText("Re: agenda")).toBeTruthy();

    // Contributor provenance footnote.
    expect(within(rail).getByTestId("prep-source-butlers").textContent).toMatch(/Relationship/);
    expect(screen.queryByTestId("meeting-prep-empty")).toBeNull();
  });

  it("renders an honest empty-state when no specialist contributed", () => {
    render(<MeetingPrepRail heading="1:1" hasPrepContext={false} attendees={[]} />);

    const empty = screen.getByTestId("meeting-prep-empty");
    expect(empty.textContent).toMatch(/no prep context yet/i);
    expect(screen.queryByTestId("prep-attendee")).toBeNull();
  });

  it("renders the empty-state when context exists but resolved no attendees", () => {
    render(<MeetingPrepRail hasPrepContext attendees={[]} sourceButlers={["relationship"]} />);

    expect(screen.getByTestId("meeting-prep-empty").textContent).toMatch(/no prep context yet/i);
  });

  it("renders a loading state", () => {
    render(<MeetingPrepRail isLoading hasPrepContext={false} attendees={[]} />);

    expect(screen.getByText("Loading…")).toBeTruthy();
    expect(screen.queryByTestId("meeting-prep-empty")).toBeNull();
  });

  it("omits the message-context panel and last-met when absent (graceful empty)", () => {
    render(
      <MeetingPrepRail
        hasPrepContext
        attendees={[
          attendee({ message_context: [], last_met: null, last_met_event: null, notes: [] }),
        ]}
      />,
    );

    const card = screen.getByTestId("prep-attendee");
    expect(within(card).queryByTestId("prep-message-context")).toBeNull();
    expect(within(card).queryByTestId("prep-last-met")).toBeNull();
    expect(within(card).queryByTestId("prep-notes")).toBeNull();
    // Attendee + tier mark still render.
    expect(within(card).getByText("Ada Lovelace")).toBeTruthy();
    expect(within(card).getByTestId("prep-tier-mark")).toBeTruthy();
  });

  it("falls back to an em-dash tier mark when the attendee has no tier", () => {
    render(<MeetingPrepRail hasPrepContext attendees={[attendee({ dunbar_tier: null })]} />);

    expect(screen.getByTestId("prep-tier-mark").textContent).toContain("—");
  });
});
