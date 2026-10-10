// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { respondToCalendarInvitation, undoCalendarWorkspaceMutation } from "@/api/index";
import type { CalendarInvitationEntry, CalendarResponseReceipt } from "@/api/types";
import { CalendarResponsePanel } from "@/components/calendar/CalendarResponsePanel";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";

vi.mock("@/api/index", () => ({
  respondToCalendarInvitation: vi.fn(), undoCalendarWorkspaceMutation: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const entry: CalendarInvitationEntry = {
  entry_id: "02b31c4c-c09c-464d-a401-227b239d50e9", title: "Actual invitation",
  start_at: "2026-10-11T10:00:00Z", end_at: "2026-10-11T11:00:00Z", timezone: "UTC",
  butler_name: "messenger", organizer: null, organizer_source: "unknown", conflict_issues: [],
  response_configured: true,
};
const receipt: CalendarResponseReceipt = {
  status: "applied", command_id: "fce214c5-924b-4d4c-a74a-119a38a80b0c",
  approval_id: "fce214c5-924b-4d4c-a74a-119a38a80b0c", source_butler: "messenger",
  projection_available: false, undo_available: true, reason: null,
};
function Palette() {
  const commands = useCommandMenuActions();
  return <div aria-label="Actual registered palette">
    {commands.map((command) => <button key={command.id} onClick={command.perform}>{command.label}</button>)}
  </div>;
}
function View({ entries = [entry], unavailable = false }: { entries?: CalendarInvitationEntry[]; unavailable?: boolean }) {
  return <CommandRegistryProvider><CalendarResponsePanel entries={entries} unavailable={unavailable} /><Palette /></CommandRegistryProvider>;
}

it("binds contextual keyboard and real palette to one key, honest receipt and single-use inverse", async () => {
  // REQ-dashboard-api-069; REQ-dashboard-api-070
  let complete: (value: Awaited<ReturnType<typeof respondToCalendarInvitation>>) => void = () => {};
  vi.mocked(respondToCalendarInvitation).mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
  const view = render(<View />);
  const user = userEvent.setup();
  const group = screen.getByRole("group", { name: "Respond to Actual invitation" });
  expect(group).toBeTruthy();
  screen.getByRole("button", { name: "Accept invitation Actual invitation" }).focus();
  await user.keyboard("a");
  await user.click(screen.getByRole("button", { name: "Accept invitation: Actual invitation" }));
  expect(respondToCalendarInvitation).toHaveBeenCalledTimes(1);
  const request = vi.mocked(respondToCalendarInvitation).mock.calls[0][0];
  expect(request).toEqual({ entry_id: entry.entry_id, response_status: "accepted", send_updates: "none", request_id: expect.any(String) });
  expect(screen.getByText("Submitting response…")).toBeTruthy();
  expect(screen.queryByText("Response applied.")).toBeNull();
  expect(screen.queryByRole("button", { name: "Undo response" })).toBeNull();
  await act(async () => { complete({ data: receipt, meta: {} }); });
  expect(screen.getByRole("status").textContent).toContain("Calendar projection unavailable");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Review response receipt" }));
  await user.click(screen.getByRole("button", { name: "Review response receipt" }));
  expect(screen.getByRole("region", { name: "Response receipt" }).textContent).toContain("messenger");
  expect(screen.getByRole("link", { name: "Open approval dossier" }).getAttribute("href"))
    .toBe(`/approvals/${receipt.approval_id}?review_source=calendar%3Amessenger`);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close receipt" }));
  await user.click(screen.getByRole("button", { name: "Close receipt" }));
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Review response receipt" }));
  view.rerender(<View entries={[]} />);
  expect(screen.getByRole("button", { name: "Undo response" })).toBeTruthy();
  vi.mocked(undoCalendarWorkspaceMutation).mockRejectedValue(new Error("controlled unavailable transport"));
  await user.click(screen.getByRole("button", { name: "Undo response" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("original response receipt is retained"));
  expect(undoCalendarWorkspaceMutation).toHaveBeenCalledExactlyOnceWith(receipt.command_id);
  expect(screen.queryByRole("button", { name: "Undo response" })).toBeNull();
  expect(screen.queryByText("Previous response restored.")).toBeNull();
});

it("refuses disabled capability and preserves an unknown request before exact-key outcome refresh", async () => {
  // REQ-dashboard-api-069
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: { ...receipt, status: "pending_approval", undo_available: false }, meta: {} });
  const view = render(<View unavailable />);
  fireEvent.keyDown(screen.getByRole("button", { name: "Decline invitation Actual invitation" }), { key: "d" });
  await userEvent.click(screen.getByRole("button", { name: "Decline invitation: Actual invitation" }));
  expect(respondToCalendarInvitation).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Accept invitation Actual invitation" }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<View entries={[{ ...entry, response_configured: false }]} />);
  expect(screen.queryByRole("group")).toBeNull();
  view.rerender(<View />);
  vi.mocked(respondToCalendarInvitation).mockRejectedValueOnce(new Error("controlled timeout"));
  await userEvent.click(screen.getByRole("button", { name: "Tentative invitation Actual invitation" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("outcome unavailable"));
  const first = vi.mocked(respondToCalendarInvitation).mock.calls[0][0];
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: { ...receipt, status: "uncertain", undo_available: false }, meta: {} });
  await userEvent.click(screen.getByRole("button", { name: "Check response outcome" }));
  expect(vi.mocked(respondToCalendarInvitation).mock.calls[1][0]).toEqual(first);
  expect(screen.getByRole("status").textContent).toContain("no second write");
  expect(screen.queryByRole("button", { name: "Undo response" })).toBeNull();
  view.unmount();
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: { ...receipt, status: "pending_approval", undo_available: false }, meta: {} });
  render(<View />);
  await userEvent.click(screen.getByRole("button", { name: "Accept invitation Actual invitation" }));
  const pendingRequest = vi.mocked(respondToCalendarInvitation).mock.calls.at(-1)?.[0];
  expect(screen.getByRole("button", { name: "Review response receipt" })).toBeTruthy();
  expect(screen.queryByText("Response applied.")).toBeNull();
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: { ...receipt, status: "approved", undo_available: false }, meta: {} });
  await userEvent.click(screen.getByRole("button", { name: "Check response outcome" }));
  expect(vi.mocked(respondToCalendarInvitation).mock.calls.at(-1)?.[0]).toEqual(pendingRequest);
  expect(screen.getByRole("status").textContent).toContain("awaiting approved execution");
  expect(screen.queryByRole("button", { name: "Undo response" })).toBeNull();
  for (const invalid of [
    { ...receipt, approval_id: null },
    { ...receipt, approval_id: "malformed" },
    { ...receipt, approval_id: "6fa76e12-1e6d-499a-8767-69c393d8159d" },
    { ...receipt, source_butler: "relationship" },
  ]) {
    cleanup();
    vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: invalid, meta: {} });
    render(<View />);
    await userEvent.click(screen.getByRole("button", { name: "Accept invitation Actual invitation" }));
    await userEvent.click(screen.getByRole("button", { name: "Review response receipt" }));
    expect(screen.queryByRole("link", { name: "Open approval dossier" })).toBeNull();
    expect(screen.getByText(/Approval review unavailable/)).toBeTruthy();
  }
});
