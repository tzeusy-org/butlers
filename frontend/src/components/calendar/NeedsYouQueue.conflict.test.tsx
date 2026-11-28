// @vitest-environment jsdom
/**
 * Needs-you queue: the overlap verbs (bu-5ii4gj.11.2). 1-9 carry out the nth option the route
 * accepts, m the first move, x keeps both (recorded server-side, so it survives a reload) and leaves
 * a receipt. On this stack the route accepts keep both only: a key for any other kind would be
 * refused with a 422, so none is bound (the mock below refuses them the way the route does).
 */

import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { toast } from "sonner";

import { ApiError } from "@/api/client";
import { resolveCalendarConflict } from "@/api/index";
import type { ConflictIssue, ResolutionOption } from "@/api/types";
import { NeedsYouQueue } from "@/components/calendar/NeedsYouQueue";
import { useInvitationResponses } from "@/components/calendar/use-invitation-responses";
import { ShortcutRegistryProvider, useShortcutHintEntries } from "@/hooks/use-register-shortcut";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";
import { useCalendarCommands } from "@/pages/calendar/use-calendar-commands";
import { useConflictResolution } from "@/pages/calendar/use-conflict-resolution";
import { buildNeedsYouQueue } from "@/pages/calendar/needs-you-queue";

vi.mock("@/api/index", () => ({
  respondToCalendarInvitation: vi.fn(),
  resolveCalendarConflict: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const WINDOW = { start: "2026-11-30T00:00:00Z", end: "2026-12-07T00:00:00Z" };

function option(id: string, kind: ResolutionOption["kind"], entry = "c-1"): ResolutionOption {
  return {
    id,
    kind,
    authority: kind === "keep_both" ? "no_park_undo" : "parked_per_event",
    exact_call: { tool: "t", args: {} },
    rationale_fields: { entry_id: entry, slot_start: "2026-11-30T13:00:00Z" },
  };
}

function issue(overrides: Partial<ConflictIssue> = {}): ConflictIssue {
  return {
    kind: "overlap",
    date: "2026-11-30",
    summary: "Planning overlaps lunch",
    severity: "warning",
    proposal_ids: [],
    events: [
      {
        entry_id: "c-1",
        title: "Lunch",
        start_at: "2026-11-30T11:00:00Z",
        end_at: "2026-11-30T12:00:00Z",
        timezone: "UTC",
        status: "confirmed",
      },
    ],
    pair_id: "pair-1",
    issue_digest: "digest-1",
    owner_timezone: "UTC",
    options: [
      option("o-move", "move_block"),
      option("o-decline", "decline"),
      option("o-ask", "ask_organizer"),
      option("o-keep", "keep_both"),
    ],
    ...overrides,
  } as ConflictIssue;
}

const keys = { current: [] as string[] };
const sheet = { current: [] as string[] };
const palette = { current: [] as string[] };
function Reader() {
  const entries = useShortcutHintEntries();
  const commands = useCommandMenuActions();
  useEffect(() => {
    keys.current = entries.map((b) => b.key);
    sheet.current = entries.map((b) => b.description);
    palette.current = commands.map((c) => c.label);
  });
  return null;
}
function PageKeys() {
  useCalendarCommands({
    view: "user",
    range: "week",
    anchor: new Date("2026-11-30T00:00:00Z"),
    updateQuery: vi.fn(),
    openButlerCreateDialog: () => {},
    openSearch: () => {},
  });
  return null;
}

/** The queue wired exactly as the page wires it: the real resolution hook over the mocked API. */
function Queue({ issues }: { issues: ConflictIssue[] }) {
  const responses = useInvitationResponses({ entries: [], unavailable: false });
  const resolution = useConflictResolution(WINDOW);
  const queue = buildNeedsYouQueue({
    invitations: { rows: [], complete: true },
    conflicts: { rows: issues, complete: true },
    proposals: { rows: [], complete: true },
    debriefs: { rows: [], complete: true },
    changes: { rows: [], complete: true },
  });
  return (
    <NeedsYouQueue
      queue={queue}
      invitations={[]}
      responses={responses}
      timezone="UTC"
      conflicts={issues}
      onResolveConflict={resolution.resolve}
      isResolvingConflict={resolution.isResolving}
      conflictReceipts={resolution.receipts}
      onMarkChangesSeen={() => {}}
      onOpenEntry={() => {}}
      onAcceptProposal={() => {}}
      onDismissProposal={() => {}}
    />
  );
}

function view(issues: ConflictIssue[] = [issue()]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (list: ConflictIssue[]) => (
    <QueryClientProvider client={client}>
      <CommandRegistryProvider>
        <ShortcutRegistryProvider>
          <Queue issues={list} />
          <PageKeys />
          <Reader />
        </ShortcutRegistryProvider>
      </CommandRegistryProvider>
    </QueryClientProvider>
  );
  const utils = render(tree(issues));
  return { ...utils, rerenderWith: (list: ConflictIssue[]) => utils.rerender(tree(list)) };
}

const KINDS: Record<string, string> = {
  "o-move": "move_block",
  "o-own": "move_own",
  "o-decline": "decline",
  "o-ask": "ask_organizer",
  "o-keep": "keep_both",
};

/** The route as it is on this stack: keep both only, anything else is a 422. */
const answered = (status: string) =>
  vi.mocked(resolveCalendarConflict).mockImplementation((async (body: { option_id: string }) => {
    if (KINDS[body.option_id] !== "keep_both") throw new ApiError("invalid", "unsupported", 422);
    return { data: { status, pair_id: "pair-1", option_id: body.option_id }, meta: {} };
  }) as never);

async function select(user: ReturnType<typeof userEvent.setup>) {
  await user.keyboard("j");
}

it("1 carries out the first SUPPORTED option, bound to the digest and the scan window", async () => {
  answered("acknowledged");
  view();
  const user = userEvent.setup();
  await select(user);
  await user.keyboard("1");
  expect(resolveCalendarConflict).toHaveBeenCalledTimes(1);
  expect(vi.mocked(resolveCalendarConflict).mock.calls[0][0]).toEqual({
    pair_id: "pair-1",
    option_id: "o-keep",
    issue_digest: "digest-1",
    ...WINDOW,
  });
  expect(toast.error).not.toHaveBeenCalled(); // never a 422 from an unsupported kind
});

it("digits number the supported options only, so the labels stay contiguous", async () => {
  view();
  const user = userEvent.setup();
  await select(user);
  expect(keys.current.filter((k) => /^[1-9]$/.test(k))).toEqual(["1"]);
  await user.keyboard("2");
  await user.keyboard("3");
  await user.keyboard("4");
  expect(resolveCalendarConflict).not.toHaveBeenCalled();
});

it("an ask_organizer option gets no key", async () => {
  view([issue({ options: [option("o-ask", "ask_organizer")] })]);
  const user = userEvent.setup();
  await select(user);
  expect(keys.current.filter((k) => /^[1-9mx]$/.test(k))).toEqual([]);
  expect(sheet.current.join(" ")).not.toContain("Ask the organizer");
});

it("before R3 lands a move option gets no key: m is unbound", async () => {
  view([issue({ options: [option("o-own", "move_own"), option("o-keep", "keep_both")] })]);
  const user = userEvent.setup();
  await select(user);
  expect(keys.current).not.toContain("m");
  await user.keyboard("m");
  expect(resolveCalendarConflict).not.toHaveBeenCalled();
});

it("x keeps both, records the server's answer as a receipt, and the keys leave with it", async () => {
  answered("acknowledged");
  view();
  const user = userEvent.setup();
  await select(user);
  await user.keyboard("x");
  expect(vi.mocked(resolveCalendarConflict).mock.calls[0][0]).toMatchObject({
    option_id: "o-keep",
    pair_id: "pair-1",
    issue_digest: "digest-1",
  });
  await act(async () => {});
  expect(screen.getByText("Kept both. This stays quiet until one of them changes.")).toBeTruthy();
  await user.keyboard("x");
  await user.keyboard("1");
  expect(resolveCalendarConflict).toHaveBeenCalledTimes(1);
  expect(keys.current).not.toContain("x");
});

it("x keeps nothing on the client: a reload that still offers the overlap shows it undismissed", async () => {
  answered("acknowledged");
  const first = view();
  const user = userEvent.setup();
  await select(user);
  await user.keyboard("x");
  await act(async () => {});
  expect(screen.getByText(/Kept both/)).toBeTruthy();
  first.unmount();
  // The server is what remembers (the acknowledgement store): a fresh mount reads the scan again.
  view([issue()]);
  expect(screen.queryByText(/Kept both/)).toBeNull();
  expect(screen.getByText("Planning overlaps lunch")).toBeTruthy();
  // and when the server has recorded it, the scan no longer offers the overlap: nothing to show
  cleanup();
  view([]);
  expect(screen.queryByText("Planning overlaps lunch")).toBeNull();
});

it("x is unbound when keep both is not offered", async () => {
  view([issue({ options: [option("o-decline", "decline")] })]);
  const user = userEvent.setup();
  await select(user);
  expect(keys.current).not.toContain("x");
});

it("an overlap the server bound no digest to has no resolve keys", async () => {
  view([issue({ issue_digest: null })]);
  const user = userEvent.setup();
  await select(user);
  expect(keys.current).not.toContain("1");
  expect(keys.current).not.toContain("x");
  await user.keyboard("1");
  expect(resolveCalendarConflict).not.toHaveBeenCalled();
});

it("a stale-source overlap with no options has no resolve keys but still opens evidence", async () => {
  view([issue({ options: [] })]);
  const user = userEvent.setup();
  await select(user);
  expect(sheet.current).toContain("Open evidence");
  expect(keys.current).not.toContain("1");
});

it("every resolve key reaches the ? sheet and the palette, and no key is registered twice", async () => {
  view();
  const user = userEvent.setup();
  await select(user);
  expect(sheet.current).toEqual(expect.arrayContaining(["Keep both", "Dismiss (keep both)"]));
  expect(palette.current).toEqual(
    expect.arrayContaining(["Needs you: keep both", "Needs you: dismiss (keep both)"]),
  );
  expect(new Set(keys.current).size).toBe(keys.current.length);
});

it("a conflict key does not shadow the page's t", async () => {
  view();
  const user = userEvent.setup();
  await select(user);
  await user.keyboard("t");
  expect(resolveCalendarConflict).not.toHaveBeenCalled();
});

it("a 409 says the overlap changed, and no receipt is recorded", async () => {
  vi.mocked(resolveCalendarConflict).mockRejectedValue(new ApiError("stale", "stale", 409));
  view();
  const user = userEvent.setup();
  await select(user);
  await user.keyboard("x");
  await act(async () => {});
  expect(toast.error).toHaveBeenCalledWith("That overlap changed. Review it again.");
  expect(screen.queryByText(/Kept both/)).toBeNull();
});
