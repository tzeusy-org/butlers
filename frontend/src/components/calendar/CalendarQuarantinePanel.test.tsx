// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { decideCalendarOrganizer } from "@/api/index";
import type { CalendarQuarantinedInvitation } from "@/api/types";
import { CalendarQuarantinePanel } from "@/components/calendar/CalendarQuarantinePanel";

vi.mock("@/api/index", () => ({ decideCalendarOrganizer: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const item: CalendarQuarantinedInvitation = {
  entry_id: "02b31c4c-c09c-464d-a401-227b239d50e9",
  start_at: "2026-10-12T10:00:00Z",
  end_at: "2026-10-12T11:00:00Z",
  timezone: "UTC",
  verdict: "first_contact",
  lure_badges: ["mass_invite"],
};
const live = () => screen.getByRole("status", { name: "" }) as HTMLElement;
const ok = (over = {}) => ({
  data: {
    verb: "vouch" as const,
    endpoints: 1,
    rules_written: 1,
    rules_removed: 0,
    events_restamped: 1,
    restamp_degraded: false,
    ...over,
  },
  meta: {},
});

it("shows time, verdict and badges only, with labelled controls", () => {
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  const row = screen.getByRole("group", { name: /Quarantined invitation/ });
  expect(within(row).getByText("First contact")).toBeTruthy();
  expect(within(row).getByText("mass invite")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Vouch for the sender/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Block the sender/ })).toBeTruthy();
});

it("vouches at once, optimistically, with no confirmation", async () => {
  let done: (v: ReturnType<typeof ok>) => void = () => {};
  vi.mocked(decideCalendarOrganizer).mockReturnValue(
    new Promise((resolve) => {
      done = resolve as never;
    }),
  );
  const onDecided = vi.fn();
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} onDecided={onDecided} />);
  await userEvent.click(screen.getByRole("button", { name: /Vouch for the sender/ }));
  expect(decideCalendarOrganizer).toHaveBeenCalledWith({ entry_id: item.entry_id, verb: "vouch" });
  expect(screen.getByText("Vouched")).toBeTruthy(); // optimistic
  expect(screen.getByText("Vouching…")).toBeTruthy();
  done(ok());
  await waitFor(() => expect(live().textContent).toContain("Block undoes this"));
  expect(onDecided).toHaveBeenCalledTimes(1);
});

it("rolls back and alerts when the vouch fails", async () => {
  vi.mocked(decideCalendarOrganizer).mockRejectedValue(new Error("boom"));
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Vouch for the sender/ }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Nothing changed"));
  expect(screen.getByText("First contact")).toBeTruthy(); // rolled back
  expect(screen.queryByText("Vouched")).toBeNull();
});

it("asks before blocking, and does nothing on cancel", async () => {
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Block the sender/ }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByText(/email and messages are not affected/)).toBeTruthy();
  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(decideCalendarOrganizer).not.toHaveBeenCalled();
});

it("blocks after confirmation", async () => {
  vi.mocked(decideCalendarOrganizer).mockResolvedValue(ok({ verb: "block" }));
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Block the sender/ }));
  await userEvent.click(screen.getByRole("button", { name: "Block sender" }));
  expect(decideCalendarOrganizer).toHaveBeenCalledWith({ entry_id: item.entry_id, verb: "block" });
  await waitFor(() => expect(live().textContent).toContain("Sender blocked"));
});

it("surfaces a failed re-stamp from the response", async () => {
  vi.mocked(decideCalendarOrganizer).mockResolvedValue(ok({ restamp_degraded: true }));
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Vouch for the sender/ }));
  await waitFor(() => expect(live().textContent).toContain("will update shortly"));
});

it("hides Block for an already blocked sender and disables controls when unavailable", () => {
  const { rerender } = render(
    <CalendarQuarantinePanel items={[{ ...item, verdict: "blocked" }]} unavailable={false} />,
  );
  expect(screen.queryByRole("button", { name: /Block the sender/ })).toBeNull();
  rerender(<CalendarQuarantinePanel items={[item]} unavailable />);
  expect((screen.getByRole("button", { name: /Vouch for the sender/ }) as HTMLButtonElement).disabled).toBe(true);
});

it("renders nothing without quarantined invitations", () => {
  const { container } = render(<CalendarQuarantinePanel items={[]} unavailable={false} />);
  expect(container.firstChild).toBeNull();
});

it("returns focus to the Block button on Cancel", async () => {
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  const block = screen.getByRole("button", { name: /Block the sender/ });
  await userEvent.click(block);
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(document.activeElement).toBe(block));
});

it("moves focus to the panel after a confirmed Block", async () => {
  vi.mocked(decideCalendarOrganizer).mockResolvedValue(ok({ verb: "block" }));
  render(<CalendarQuarantinePanel items={[item]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Block the sender/ }));
  await userEvent.click(screen.getByRole("button", { name: "Block sender" }));
  const panel = screen.getByRole("region", { name: "Quarantined invitations" });
  await waitFor(() => expect(document.activeElement).toBe(panel));
});

it("says a vouch did not lift a global block, from the refetched verdict", async () => {
  vi.mocked(decideCalendarOrganizer).mockResolvedValue(ok());
  const blocked = { ...item, verdict: "blocked" as const };
  const { rerender } = render(<CalendarQuarantinePanel items={[blocked]} unavailable={false} />);
  await userEvent.click(screen.getByRole("button", { name: /Vouch for the sender/ }));
  await waitFor(() => expect(live().textContent).toContain("Still blocked by a global rule"));
  expect(live().textContent).not.toContain("Block undoes this");
  rerender(<CalendarQuarantinePanel items={[blocked]} unavailable={false} />);
});

it("announces a vouch whose row left the list and moves focus to the panel", async () => {
  vi.mocked(decideCalendarOrganizer).mockResolvedValue(ok());
  let items = [item];
  const onDecided = vi.fn(() => {
    items = [];
    rerender(<CalendarQuarantinePanel items={items} unavailable={false} onDecided={onDecided} />);
  });
  const { rerender } = render(
    <CalendarQuarantinePanel items={items} unavailable={false} onDecided={onDecided} />,
  );
  await userEvent.click(screen.getByRole("button", { name: /Vouch for the sender/ }));
  const panel = await screen.findByRole("region", { name: "Quarantined invitations" });
  await waitFor(() => expect(live().textContent).toContain("Sender vouched"));
  expect(document.activeElement).toBe(panel);
});
