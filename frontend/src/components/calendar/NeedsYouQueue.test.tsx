// @vitest-environment jsdom

import { useEffect } from "react";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { respondToCalendarInvitation } from "@/api/index";
import type { CalendarInvitationEntry, CalendarResponseReceipt } from "@/api/types";
import { NeedsYouQueue } from "@/components/calendar/NeedsYouQueue";
import { useCalendarCommands } from "@/pages/calendar/use-calendar-commands";
import { useInvitationResponses } from "@/components/calendar/use-invitation-responses";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";
import { ShortcutRegistryProvider, useShortcutHintEntries } from "@/hooks/use-register-shortcut";
import { buildNeedsYouQueue } from "@/pages/calendar/needs-you-queue";

vi.mock("@/api/index", () => ({ respondToCalendarInvitation: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  for (const name of ["data-opened", "data-accepted", "data-dismissed"]) document.body.removeAttribute(name);
});

const invitation: CalendarInvitationEntry = {
  entry_id: "inv-1", title: "Planning", start_at: "2026-11-30T09:00:00Z", end_at: "2026-11-30T10:00:00Z",
  timezone: "UTC", organizer: null, organizer_source: "unknown", butler_name: "messenger",
  conflict_issues: [], response_configured: true,
};
const receipt: CalendarResponseReceipt = {
  status: "pending_approval", command_id: null, approval_id: null, source_butler: "messenger",
  projection_available: false, reason: null,
};

const today = vi.fn();
function PageKeys() {
  // The page's own bindings (t = jump to today, arrows, c, search), exactly as /calendar registers them.
  useCalendarCommands({
    view: "butler", range: "week", anchor: new Date("2026-11-30T00:00:00Z"),
    updateQuery: today, openButlerCreateDialog: () => {}, openSearch: () => {},
  });
  return null;
}

function Harness({
  proposalsComplete = true,
  conflictsNotRead = false,
}: {
  proposalsComplete?: boolean;
  conflictsNotRead?: boolean;
}) {
  const responses = useInvitationResponses({ entries: [invitation], unavailable: false });
  const queue = buildNeedsYouQueue({
    invitations: { rows: [invitation], complete: true },
    conflicts: {
      rows: [{ kind: "overlap", date: "2026-11-30", summary: "Planning overlaps lunch", severity: "warning",
        proposal_ids: [], events: [{ entry_id: "c-1", title: "Lunch", start_at: "2026-11-30T11:00:00Z",
          end_at: "2026-11-30T12:00:00Z", timezone: "UTC", status: "confirmed" }] }],
      complete: !conflictsNotRead,
      notRead: conflictsNotRead,
    },
    proposals: {
      rows: [{ entry_id: "prop-1", title: "Dinner", start_at: "2026-11-30T18:00:00Z" } as never],
      complete: proposalsComplete,
    },
    debriefs: { rows: [{ debrief_id: "deb-1", number: 1, event_title: "Standup", occurrence_start: "2026-11-30T12:30:00Z",
      prompted_at: "2026-11-30T13:00:00Z", attendees: [] }], complete: true },
  });
  return (
    <NeedsYouQueue
      queue={queue}
      invitations={[invitation]}
      responses={responses}
      onOpenEntry={(id) => void document.body.setAttribute("data-opened", id)}
      onAcceptProposal={(id) => void document.body.setAttribute("data-accepted", id)}
      onDismissProposal={(id) => void document.body.setAttribute("data-dismissed", id)}
    />
  );
}

const sheetHolder = { current: [] as string[] };
const keysHolder = { current: [] as string[] };
const paletteHolder = { current: [] as string[] };
function HintReader() {
  const entries = useShortcutHintEntries();
  const commands = useCommandMenuActions();
  useEffect(() => {
    paletteHolder.current = commands.map((command) => command.label);
    sheetHolder.current = entries.map((binding) => binding.description);
    keysHolder.current = entries.map((binding) => binding.key);
  });
  return null;
}

const view = (props = {}) =>
  render(
    <CommandRegistryProvider>
      <ShortcutRegistryProvider>
        <Harness {...props} />
        <PageKeys />
        <HintReader />
      </ShortcutRegistryProvider>
    </CommandRegistryProvider>,
  );

it("j/k navigate across kinds and the selected kind's verbs act", async () => {
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: receipt, meta: {} });
  view();
  const user = userEvent.setup();
  const rows = screen.getAllByRole("button");
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringContaining("Planning"),
    expect.stringContaining("Planning overlaps lunch"),
    expect.stringContaining("Standup"),
    expect.stringContaining("Dinner"),
  ]);
  await user.keyboard("j");
  expect(screen.getByRole("button", { name: /Planning$/ }).getAttribute("aria-current")).toBe("true");
  await user.keyboard("d");
  expect(respondToCalendarInvitation).toHaveBeenCalledTimes(1);
  expect(vi.mocked(respondToCalendarInvitation).mock.calls[0][0]).toMatchObject({
    entry_id: "inv-1", response_status: "declined",
  });
  await act(async () => {});
  expect(screen.getByText("Response awaiting approval")).toBeTruthy();
  // once answered, the choice keys leave with the receipt
  await user.keyboard("a");
  expect(respondToCalendarInvitation).toHaveBeenCalledTimes(1);
  await user.keyboard("jjj");
  await user.keyboard("a");
  expect(document.body.getAttribute("data-accepted")).toBe("prop-1");
  await user.keyboard("d");
  expect(document.body.getAttribute("data-dismissed")).toBe("prop-1");
});

it("an overlap opens evidence and has no accept key", async () => {
  view();
  const user = userEvent.setup();
  await user.keyboard("jj");
  await user.keyboard("a");
  expect(document.body.getAttribute("data-accepted")).toBeNull();
  expect(respondToCalendarInvitation).not.toHaveBeenCalled();
  await user.keyboard("{Enter}");
  expect(document.body.getAttribute("data-opened")).toBe("c-1");
});

it("an unknown kind is named and the count is a lower bound", () => {
  view({ proposalsComplete: false });
  expect(screen.getByRole("heading", { name: "4+ need you · proposals unknown" })).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("longer than shown");
});

it("every bound key reaches the ? sheet, and a key that does not apply is not listed", async () => {
  view();
  const user = userEvent.setup();
  await user.keyboard("j");
  expect(sheetHolder.current).toEqual(
    expect.arrayContaining([
      "Next item", "Previous item", "Open evidence",
      "Accept invitation", "Decline invitation", "Tentative (unsure) invitation",
    ]),
  );
  expect(sheetHolder.current).not.toContain("Accept proposal");
  await user.keyboard("jjj");
  expect(sheetHolder.current).toEqual(expect.arrayContaining(["Accept proposal", "Dismiss proposal"]));
  expect(sheetHolder.current).not.toContain("Decline invitation");
});

it("says moved-under-you changes are not tracked and still says nothing is unknown", () => {
  view();
  expect(screen.getByText("Moved-under-you changes are not tracked yet.")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
});

it("every bound key has its palette form", async () => {
  view();
  const user = userEvent.setup();
  await user.keyboard("j");
  expect(paletteHolder.current).toEqual(
    expect.arrayContaining([
      "Needs you: open evidence",
      "Needs you: accept invitation",
      "Needs you: decline invitation",
      "Needs you: tentative (unsure) invitation",
    ]),
  );
  await user.keyboard("jjj");
  expect(paletteHolder.current).toEqual(
    expect.arrayContaining(["Needs you: accept proposal", "Needs you: dismiss proposal"]),
  );
});

it("t stays the page's jump to today: it sends no reply, and no key is bound twice", async () => {
  vi.mocked(respondToCalendarInvitation).mockResolvedValue({ data: receipt, meta: {} });
  view();
  const user = userEvent.setup();
  await user.keyboard("j"); // an invitation is selected
  await user.keyboard("t");
  expect(today).toHaveBeenCalledTimes(1);
  expect(respondToCalendarInvitation).not.toHaveBeenCalled();
  // page + queue together: every registered key is unique
  expect(new Set(keysHolder.current).size).toBe(keysHolder.current.length);
  await user.keyboard("u");
  expect(vi.mocked(respondToCalendarInvitation).mock.calls[0][0]).toMatchObject({
    response_status: "tentative",
  });
});

it("in month view overlaps are not read: a fixed line, no unknown kind", () => {
  view({ conflictsNotRead: true });
  expect(screen.getByText("Overlaps are checked in day and week views.")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
});
