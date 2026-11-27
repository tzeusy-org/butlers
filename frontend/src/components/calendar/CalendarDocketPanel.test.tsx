// @vitest-environment jsdom
/**
 * CalendarDocketPanel — RTL tests (bu-5ii4gj.9 S7).
 *
 * The panel shows each prepared answer with its exact call, policy and facts; nothing is ticked
 * until the owner ticks it; a submit carries only the ticked cards with the digests they were
 * shown; changed cards cannot be ticked and say why; unprepared and disagreeing invitations are
 * never hidden; everything is reachable from the keyboard.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Docket, DocketCard, DocketSubmitResult } from "@/api/rsvp-docket.ts";

import { CalendarDocketPanel } from "./CalendarDocketPanel.tsx";

afterEach(cleanup);

function card(overrides: Partial<DocketCard> = {}): DocketCard {
  return {
    action_id: "aaaaaaaa-0000-0000-0000-000000000001",
    entry_id: "e1",
    verdict: "declined",
    send_updates: "none",
    policy_id: "11111111-2222-3333-4444-555555555555",
    policy_version: 2,
    facts: [{ kind: "overlaps_focus_or_out_of_office", note: "policy wants is True; observed True" }],
    event: {
      title: "Board offsite",
      starts_at: "2026-07-03T09:00:00+00:00",
      ends_at: "2026-07-03T10:00:00+00:00",
    },
    args_digest: "d".repeat(64),
    state: "current",
    reason: null,
    ...overrides,
  };
}

function docket(overrides: Partial<Docket> = {}): Docket {
  return {
    cards: [card()],
    disagreed: [],
    unprepared: 0,
    unprepared_reasons: {},
    complete: true,
    degraded: [],
    policy_agreement: null,
    ...overrides,
  };
}

const NOTHING: DocketSubmitResult = { results: [], refused: [] };

function renderPanel(d: Docket, onSubmit = vi.fn().mockResolvedValue(NOTHING)) {
  render(<CalendarDocketPanel docket={d} timezone="UTC" onSubmit={onSubmit} />);
  return onSubmit;
}

describe("CalendarDocketPanel", () => {
  it("shows the exact call, the policy and its version, and the matched facts", () => {
    renderPanel(docket({ policy_agreement: [{ policy_id: "11111111-2222-3333-4444-555555555555", policy_version: 2, outcome: "approved_as_prepared", count: 4 }] }));
    const row = screen.getByTestId("docket-card");
    expect(within(row).getByText("Board offsite")).toBeTruthy();
    expect(within(row).getByTestId("docket-call").textContent).toBe("Decline, tells no one");
    expect(row.textContent).toContain("Policy 11111111 v2");
    expect(row.textContent).toContain("you've seen: 4 approved as prepared");
    expect(row.textContent).toContain("overlaps focus or out of office: policy wants is True; observed True");
  });

  it("ticks nothing by default and keeps submit off until a card is ticked", () => {
    renderPanel(docket());
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("docket-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("submits only the ticked cards with the digest each was shown", async () => {
    const second = card({ action_id: "aaaaaaaa-0000-0000-0000-000000000002", args_digest: "e".repeat(64) });
    const onSubmit = renderPanel(docket({ cards: [card(), second] }));
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByTestId("docket-submit"));
    expect(onSubmit).toHaveBeenCalledWith([
      { action_id: second.action_id, args_digest: "e".repeat(64) },
    ]);
  });

  it("tick-all ticks the ready cards and submit approves them all", () => {
    const second = card({ action_id: "aaaaaaaa-0000-0000-0000-000000000002" });
    const onSubmit = renderPanel(docket({ cards: [card(), second] }));
    fireEvent.click(screen.getByRole("button", { name: "Tick all 2 ready" }));
    fireEvent.click(screen.getByTestId("docket-submit"));
    expect(onSubmit.mock.calls[0][0]).toHaveLength(2);
  });

  it("a changed card cannot be ticked, says why, and stays out of a tick-all", () => {
    const changed = card({
      action_id: "aaaaaaaa-0000-0000-0000-000000000003",
      state: "changed",
      reason: "policy_changed",
    });
    const onSubmit = renderPanel(docket({ cards: [card(), changed] }));
    const [ok, moved] = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(ok.disabled).toBe(false);
    expect(moved.disabled).toBe(true);
    expect(screen.getByTestId("docket-card-reason").textContent).toContain(
      "The policy was edited since this was prepared",
    );
    fireEvent.click(screen.getByRole("button", { name: "Tick all 1 ready" }));
    fireEvent.click(screen.getByTestId("docket-submit"));
    expect(onSubmit.mock.calls[0][0].map((i: { action_id: string }) => i.action_id)).toEqual([
      card().action_id,
    ]);
  });

  it("an unverifiable card says it cannot be re-checked", () => {
    renderPanel(docket({ cards: [card({ state: "unverifiable", reason: "facts" })] }));
    expect(screen.getByTestId("docket-card-reason").textContent).toContain("Can't re-check");
  });

  it("names every refused card after a submit and still reports what was approved", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      results: [{ action_id: "a", status: "executed", dispatched: true, execution_failure: null }],
      refused: [
        {
          action_id: "x",
          entry_id: "e9",
          event: { title: "Standup" },
          reason: "call_changed",
        },
      ],
    });
    renderPanel(docket(), onSubmit);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("docket-submit"));
    const result = await screen.findByTestId("docket-result");
    expect(result.textContent).toContain("Sent 1, refused 1");
    expect(screen.getByTestId("docket-refusal").textContent).toContain(
      "Standup: The call changed after it was shown.",
    );
  });

  it("a card approved but not sent never reads as sent", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      results: [
        {
          action_id: card().action_id,
          status: "approved",
          dispatched: false,
          execution_failure: { code: "daemon_unreachable" },
        },
      ],
      refused: [],
    });
    renderPanel(docket(), onSubmit);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("docket-submit"));
    const result = await screen.findByTestId("docket-result");
    expect(result.textContent).toContain("Sent 0, approved but not sent 1");
    expect(screen.getByTestId("docket-not-sent").textContent).toContain("Board offsite");
  });

  it("a stopped batch names the unknown outcome and the cards not attempted", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      results: [],
      stopped: true,
      refused: [
        { action_id: "x", entry_id: "e1", event: { title: "Standup" }, reason: "outcome_unknown" },
        { action_id: "y", entry_id: null, event: null, reason: "not_attempted" },
      ],
    });
    renderPanel(docket(), onSubmit);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("docket-submit"));
    const result = await screen.findByTestId("docket-result");
    expect(result.textContent).toContain("The batch stopped early.");
    const lines = screen.getAllByTestId("docket-refusal").map((l) => l.textContent);
    expect(lines[0]).toContain("Standup: The outcome is unknown");
    expect(lines[1]).toContain("Not attempted");
  });

  it("a transport failure says the outcome is unknown, never that nothing was approved", async () => {
    renderPanel(docket(), vi.fn().mockRejectedValue(new Error("Request timed out after 15s")));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("docket-submit"));
    const text = (await screen.findByTestId("docket-submit-error")).textContent ?? "";
    expect(text).toBe(
      "The outcome is unknown; the docket has been refreshed. Check it before resubmitting.",
    );
    expect(text).not.toContain("Nothing was approved");
  });

  it("a card sent but rejected by the provider reads as rejected, not sent", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      results: [
        {
          action_id: card().action_id,
          status: "executed",
          dispatched: true,
          execution_failure: { code: "provider_rejected", label: "Rejected", terminal: true },
        },
      ],
      refused: [],
    });
    renderPanel(docket(), onSubmit);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("docket-submit"));
    const result = await screen.findByTestId("docket-result");
    expect(result.textContent).toContain("Sent 0, sent but rejected 1");
    expect(screen.getByTestId("docket-rejected").textContent).toContain(
      "Board offsite: sent but rejected (provider_rejected)",
    );
  });

  it("says agreement is unavailable when the ledger could not be read", () => {
    renderPanel(docket({ policy_agreement: null }));
    expect(screen.getByTestId("docket-card").textContent).toContain("agreement unavailable");
  });

  it("card reasons are plain text, not live regions", () => {
    renderPanel(docket({ cards: [card({ state: "changed", reason: "stale_event" })] }));
    expect(screen.getByTestId("docket-card-reason").getAttribute("role")).toBeNull();
  });

  it("an empty docket with unprepared invitations is not shown as clean", () => {
    renderPanel(
      docket({
        cards: [],
        unprepared: 3,
        unprepared_reasons: { facts_unreadable: 2, organizer_unreadable: 1 },
        complete: false,
        degraded: ["calendar"],
      }),
    );
    expect(screen.getByTestId("docket-empty")).toBeTruthy();
    const row = screen.getByTestId("docket-unprepared").textContent ?? "";
    expect(row).toContain("3 unprepared");
    expect(row).toContain("2 facts unreadable, 1 organizer unreadable");
    expect(screen.getByTestId("docket-degraded")).toBeTruthy();
  });

  it("a clean docket says every invitation was evaluated", () => {
    renderPanel(docket({ cards: [] }));
    expect(screen.getByTestId("docket-unprepared").textContent).toContain("0 unprepared");
  });

  it("names the disagreeing policies and prepares nothing for that invitation", () => {
    renderPanel(
      docket({
        cards: [],
        disagreed: [
          {
            entry_id: "e2",
            event: { title: "Standup", starts_at: "2026-07-03T09:00:00+00:00" },
            policies: [
              { id: "pa", position: 1, verdict: "accepted", send_updates: "none", version: 1 },
              { id: "pd", position: 3, verdict: "declined", send_updates: "none", version: 2 },
            ],
          },
        ],
      }),
    );
    const text = screen.getByTestId("docket-disagreement").textContent ?? "";
    expect(text).toContain("Policies disagree");
    expect(text).toContain("policy pa v1 (accept) and policy pd v2 (decline)");
    expect(text).toContain("Nothing was prepared");
  });

  it("is operable from the keyboard: space ticks, enter on the focused submit sends", async () => {
    const user = userEvent.setup();
    const onSubmit = renderPanel(docket());
    await user.tab(); // tick-all button
    await user.tab(); // the card checkbox
    expect(document.activeElement).toBe(screen.getByRole("checkbox"));
    await user.keyboard(" ");
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    await user.tab(); // submit
    expect(document.activeElement).toBe(screen.getByTestId("docket-submit"));
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("a read failure says nothing can be approved", () => {
    render(
      <CalendarDocketPanel
        docket={undefined}
        isError
        error={new Error("503")}
        timezone="UTC"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("nothing can be approved");
  });

  it("drops a tick when its card stops being approvable on a refetch", () => {
    const onSubmit = vi.fn().mockResolvedValue(NOTHING);
    const { rerender } = render(
      <CalendarDocketPanel docket={docket()} timezone="UTC" onSubmit={onSubmit} />,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    rerender(
      <CalendarDocketPanel
        docket={docket({ cards: [card({ state: "changed", reason: "stale_event" })] })}
        timezone="UTC"
        onSubmit={onSubmit}
      />,
    );
    expect((screen.getByTestId("docket-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("caps a batch at 50: Tick first 50, a 51st cannot be ticked, the rest are waiting", async () => {
    const many = Array.from({ length: 51 }, (_, i) =>
      card({ action_id: `bbbbbbbb-0000-0000-0000-${String(i).padStart(12, "0")}` }),
    );
    const onSubmit = renderPanel(docket({ cards: many }));
    fireEvent.click(screen.getByRole("button", { name: "Tick first 50" }));
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.filter((b) => b.checked)).toHaveLength(50);
    expect(boxes[50].checked).toBe(false);
    expect(boxes[50].disabled).toBe(true);
    fireEvent.click(boxes[50]);
    expect(boxes[50].checked).toBe(false);
    fireEvent.click(screen.getByTestId("docket-submit"));
    expect(onSubmit.mock.calls[0][0]).toHaveLength(50);
    expect((await screen.findByTestId("docket-result")).textContent).toContain(
      "1 more is waiting for the next submit.",
    );
  });

  it("unticking one of 50 frees the cap for the 51st", () => {
    const many = Array.from({ length: 51 }, (_, i) =>
      card({ action_id: `bbbbbbbb-0000-0000-0000-${String(i).padStart(12, "0")}` }),
    );
    renderPanel(docket({ cards: many }));
    fireEvent.click(screen.getByRole("button", { name: "Tick first 50" }));
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    fireEvent.click(boxes[0]);
    expect(boxes[50].disabled).toBe(false);
    fireEvent.click(boxes[50]);
    expect(boxes[50].checked).toBe(true);
  });
});
