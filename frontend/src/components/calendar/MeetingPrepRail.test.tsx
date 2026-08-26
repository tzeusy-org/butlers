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

import type { CalendarPrepAttendee, CalendarPrepCommitment } from "@/api/types.ts";

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
    message_context: [
      {
        channel: "email",
        thread_id: "thread-1",
        subject: "Re: agenda",
        snippet: "Looping back on the agenda for our sync.",
        last_message_at: "2026-05-02T09:30:00+00:00",
        message_count: 3,
      },
    ],
    commitments: [],
    ...overrides,
  };
}

function commitment(overrides: Partial<CalendarPrepCommitment> = {}): CalendarPrepCommitment {
  return {
    kind: "promise",
    direction: "owner_to_other",
    summary: "Send the book",
    deadline: "2026-08-30T09:00:00Z",
    escalation_level: "L2",
    fingerprint: "commitment-1",
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

  it("keeps prior prep context visible but dimmed while another event fetches", () => {
    render(
      <MeetingPrepRail
        heading="Quarterly sync"
        isFetching
        hasPrepContext
        attendees={[attendee()]}
        sourceButlers={["relationship"]}
      />,
    );

    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByTestId("meeting-prep-rail").parentElement?.className).toContain(
      "opacity-60",
    );
  });

  it("shows a transport error instead of retained prep context", () => {
    render(
      <MeetingPrepRail
        heading="Quarterly sync"
        isError
        error={new Error("prep fetch failed")}
        hasPrepContext
        attendees={[attendee()]}
        sourceButlers={["relationship"]}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("prep fetch failed");
    expect(screen.queryByText("Ada Lovelace")).toBeNull();
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

  it("renders commitment rows with kind, direction, summary, deadline, and escalation", () => {
    render(
      <MeetingPrepRail
        hasPrepContext
        attendees={[
          attendee({
            commitments: [
              commitment(),
              commitment({
                kind: "waiting_for",
                direction: "other_to_owner",
                summary: "Confirm the venue",
                deadline: null,
                escalation_level: "L1",
                fingerprint: "commitment-2",
              }),
            ],
          }),
        ]}
      />,
    );

    const section = screen.getByTestId("prep-commitments");
    expect(within(section).getByRole("heading", { name: "Commitments" })).toBeTruthy();
    const list = within(section).getByRole("list", { name: "Commitments for Ada Lovelace" });
    expect(list.className).not.toContain("flex-wrap");

    const rows = within(section).getAllByTestId("prep-commitment");
    expect(rows).toHaveLength(2);

    expect(rows[0].textContent).toContain("PROMISE");
    expect(rows[0].textContent).toContain("Owner owes");
    expect(rows[0].textContent).toContain("Send the book");
    expect(within(rows[0]).getByTestId("prep-commitment-deadline")).toBeTruthy();
    expect(within(rows[0]).getByTestId("prep-commitment-kind-icon")).toBeTruthy();
    expect(within(rows[0]).getByTestId("prep-commitment-direction-icon")).toBeTruthy();
    const kindTag = within(rows[0]).getByTestId("prep-commitment-kind");
    expect(kindTag.className).toContain("text-[var(--dim)]");
    expect(kindTag.className).toContain("uppercase");
    expect(kindTag.className).not.toContain("border");
    expect(kindTag.className).not.toContain("bg-");
    const escalatedSignal = within(rows[0]).getByTestId("prep-commitment-escalation");
    expect(escalatedSignal.textContent).toBe("L2");
    expect(escalatedSignal.getAttribute("title")).toBe("Escalation L2");
    expect(rows[0].getAttribute("aria-label")).toMatch(/Owner owes/);
    expect(rows[0].getAttribute("aria-label")).toMatch(/deadline 2026-08-30T09:00:00Z/);
    expect(rows[0].getAttribute("aria-label")).toMatch(/escalation L2/);
    expect(rows[0].className).toContain("grid");
    expect(rows[0].className).toContain("border-b");
    expect(rows[0].className).not.toContain("rounded");
    expect(rows[0].className).not.toContain("bg-");
    expect(within(rows[0]).getByTestId("prep-commitment-escalation").className).toContain(
      "text-[var(--amber-text)]",
    );
    expect(rows[0].getAttribute("data-escalated")).toBe("true");

    expect(rows[1].textContent).toContain("WAITING FOR");
    expect(rows[1].textContent).toContain("Counterparty owes owner");
    expect(rows[1].textContent).toContain("Confirm the venue");
    expect(within(rows[1]).queryByTestId("prep-commitment-deadline")).toBeNull();
    const standardSignal = within(rows[1]).getByTestId("prep-commitment-escalation");
    expect(standardSignal.textContent).toBe("L1");
    expect(standardSignal.getAttribute("title")).toBe("Escalation L1");
    expect(standardSignal.className).not.toContain(
      "text-[var(--amber-text)]",
    );
    expect(rows[1].className).not.toContain("border-[var(--amber)]");
    expect(rows[1].className).not.toContain("bg-");
    expect(rows[1].getAttribute("aria-label")).toMatch(/Counterparty owes owner/);
    expect(rows[1].getAttribute("data-escalated")).toBe("false");
  });

  it("does not render a commitment section for attendees without commitments", () => {
    render(<MeetingPrepRail hasPrepContext attendees={[attendee()]} />);

    expect(screen.queryByTestId("prep-commitments")).toBeNull();
    expect(screen.queryByTestId("prep-commitment")).toBeNull();
  });

  it("falls back to an em-dash tier mark when the attendee has no tier", () => {
    render(<MeetingPrepRail hasPrepContext attendees={[attendee({ dunbar_tier: null })]} />);

    expect(screen.getByTestId("prep-tier-mark").textContent).toContain("—");
  });
});
