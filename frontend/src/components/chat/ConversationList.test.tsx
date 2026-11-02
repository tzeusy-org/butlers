// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import type { ConversationSummary } from "@/api/types.ts";
import { ConversationList } from "./ConversationList.tsx";

vi.mock("@/hooks/use-conversations.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/use-conversations.ts")>()),
  useConversations: vi.fn(),
  useConversationSearch: vi.fn(),
  useMessageSearch: vi.fn(),
}));

vi.mock("@/api/index.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/index.ts")>()),
  listConversations: vi.fn(),
  searchConversations: vi.fn(),
  updateConversation: vi.fn(),
}));

vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast: toastFn };
});

import {
  useConversations,
  useConversationSearch,
  useMessageSearch,
} from "@/hooks/use-conversations.ts";
import { listConversations, searchConversations, updateConversation } from "@/api/index.ts";

const actualHooks = await vi.importActual<typeof import("@/hooks/use-conversations.ts")>(
  "@/hooks/use-conversations.ts",
);

function renderConversationList(onSelectConversation = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ConversationList
        butlerName="switchboard"
        activeConversationId={null}
        onSelectConversation={onSelectConversation}
        onNewConversation={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useConversationSearch>);
  vi.mocked(useMessageSearch).mockReturnValue({
    data: { items: [], next_cursor: null, limit: 20 },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useMessageSearch>);
});

afterEach(() => cleanup());

describe("ConversationList — read recovery", () => {
  it("surfaces a list read error with an explicit retry instead of an empty-state lie", () => {
    const refetchConversations = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchConversations,
    } as unknown as ReturnType<typeof useConversations>);

    renderConversationList();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Could not load conversations.");
    expect(screen.queryByText("No conversations yet.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchConversations).toHaveBeenCalledTimes(1);
  });

  it("keeps cached list rows visible alongside a recoverable list error", () => {
    const refetchConversations = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: {
        data: [
          {
            id: "conversation-cached",
            title: "Cached conversation",
            updated_at: "2026-08-02T00:00:00Z",
            routed_butler: null,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: true,
      refetch: refetchConversations,
    } as unknown as ReturnType<typeof useConversations>);

    renderConversationList();

    expect(screen.getByText("Cached conversation")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Could not load conversations.");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchConversations).toHaveBeenCalledTimes(1);
  });

  it("keeps cached search rows visible alongside a recoverable search error", () => {
    vi.useFakeTimers();
    const refetchSearch = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useConversations>);
    vi.mocked(useConversationSearch).mockImplementation((_, query) => {
      if (query === "cached") {
        return {
          data: {
            data: [
              {
                id: "conversation-search-cached",
                title: "Cached search result",
                updated_at: "2026-08-02T00:00:00Z",
                routed_butler: null,
              },
            ],
            meta: {},
          },
          isLoading: false,
          isError: true,
          refetch: refetchSearch,
        } as unknown as ReturnType<typeof useConversationSearch>;
      }

      return {
        data: { data: [], meta: {} },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useConversationSearch>;
    });

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "cached" },
      });
      act(() => vi.advanceTimersByTime(300));

      expect(screen.getByText("Cached search result")).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toContain(
        "Could not load conversation search results.",
      );

      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(refetchSearch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ConversationList — message search (bu-0ynlk.9)", () => {
  it("renders snippet rows with a butler mark for an active query", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        items: [
          {
            message_id: "msg-1",
            conversation_id: "conv-1",
            role: "user",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "home",
            session_id: null,
            snippet: "call the landlord today",
            highlight_ranges: [[9, 17]],
            deep_link: "/butlers/home",
          },
        ],
        next_cursor: null,
        limit: 20,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      const row = screen.getByTestId("message-search-result");
      expect(row.textContent).toContain("call the landlord today");
      // Highlighted match is wrapped in a <mark>.
      const mark = row.querySelector("mark");
      expect(mark?.textContent).toBe("landlord");
      // ButlerMark renders the butler's initial letter.
      expect(row.textContent).toContain("H");
    } finally {
      vi.useRealTimers();
    }
  });

  it("jump-to-message calls onSelectConversation with the message id for a same-butler hit", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        items: [
          {
            message_id: "msg-1",
            conversation_id: "conv-1",
            role: "user",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "switchboard",
            session_id: null,
            snippet: "call the landlord today",
            highlight_ranges: [[9, 17]],
            deep_link: "/butlers/switchboard",
          },
        ],
        next_cursor: null,
        limit: 20,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    const onSelectConversation = vi.fn();
    try {
      renderConversationList(onSelectConversation);
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      fireEvent.click(screen.getByTestId("message-search-result"));

      expect(onSelectConversation).toHaveBeenCalledWith("conv-1", "msg-1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders a cross-butler hit as an external deep_link instead of an in-panel jump", () => {
    vi.useFakeTimers();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: {
        items: [
          {
            message_id: "msg-2",
            conversation_id: "conv-2",
            role: "assistant",
            created_at: "2026-08-02T00:00:00Z",
            butler_name: "finance",
            session_id: "sess-9",
            snippet: "budget review",
            highlight_ranges: [],
            deep_link: "/sessions/sess-9",
          },
        ],
        next_cursor: null,
        limit: 20,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "budget" },
      });
      act(() => vi.advanceTimersByTime(300));

      const row = screen.getByTestId("message-search-result");
      expect(row.tagName).toBe("A");
      expect(row.getAttribute("href")).toBe("/sessions/sess-9");
      expect(row.getAttribute("target")).toBe("_blank");
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces a message search read error with retry instead of a silent empty section", () => {
    vi.useFakeTimers();
    const refetchMessageSearch = vi.fn();
    vi.mocked(useMessageSearch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchMessageSearch,
    } as unknown as ReturnType<typeof useMessageSearch>);

    try {
      renderConversationList();
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "landlord" },
      });
      act(() => vi.advanceTimersByTime(300));

      expect(screen.queryByTestId("message-search-result")).toBeNull();
      const alerts = screen.getAllByRole("alert");
      const messageSearchAlert = alerts.find((el) =>
        el.textContent?.includes("Could not load message search results."),
      );
      expect(messageSearchAlert).toBeTruthy();

      fireEvent.click(
        messageSearchAlert!.querySelector("button") as HTMLButtonElement,
      );
      expect(refetchMessageSearch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// Rename and reversible archive (REQ-dashboard-chat-ui-017)
// ---------------------------------------------------------------------------

function conversation(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "conv-c",
    butler_name: "switchboard",
    title: "Trip planning",
    status: "active",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    message_count: 3,
    routed_butler: null,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function page(rows: ConversationSummary[]) {
  return { data: rows, meta: { total: rows.length, offset: 0, limit: 20 } };
}

/**
 * A stateful stand-in for the per-butler list and PATCH endpoints, so
 * post-settlement refetches return what the server actually holds. Queue a
 * failure with `failNext()`; a failed PATCH leaves server state unchanged.
 */
function fakeServer(rows: ConversationSummary[]) {
  let state = [...rows];
  const failures: Error[] = [];
  vi.mocked(useConversations).mockImplementation(actualHooks.useConversations);
  vi.mocked(listConversations).mockImplementation(async (_butler, params) =>
    page(state.filter((row) => row.status === (params?.status ?? "active"))),
  );
  vi.mocked(updateConversation).mockImplementation(async (_butler, id, body) => {
    const failure = failures.shift();
    if (failure) throw failure;
    let updated: ConversationSummary | undefined;
    state = state.map((row) =>
      row.id === id ? (updated = { ...row, ...body, updated_at: "2026-10-05T00:00:00Z" }) : row,
    );
    return updated!;
  });
  return {
    failNext: (error = new Error("network")) => failures.push(error),
  };
}

async function openRowActions(title: string) {
  const trigger = await screen.findByRole("button", { name: `Actions for ${title} (switchboard)` });
  fireEvent.keyDown(trigger, { key: "Enter" });
  return trigger;
}

function chooseAction(name: string) {
  fireEvent.click(screen.getByRole("menuitem", { name }));
}

function pressKey(target: Element, key: string) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

/** The Undo action handed to the archive toast. */
function undoActionFromToast() {
  const [, options] = vi.mocked(toast).mock.calls.at(-1)!;
  const action = (options as { action: { label: string; onClick: () => void } }).action;
  expect(action.label).toBe("Undo");
  return action.onClick;
}

async function startRename(title: string) {
  await openRowActions(title);
  chooseAction("Rename conversation");
  const input = (await screen.findByRole("textbox", { name: `Title for ${title}` })) as HTMLInputElement;
  return { input };
}

/** Focus is back on the (re-mounted) action trigger of the named row. */
async function expectFocusOnRowActions(title: string) {
  await waitFor(() =>
    expect(document.activeElement?.getAttribute("aria-label")).toBe(
      `Actions for ${title} (switchboard)`,
    ),
  );
}

describe("ConversationList — rename and archive (REQ-dashboard-chat-ui-017)", () => {
  it("renames optimistically with one title-only PATCH and returns focus to the row action", async () => {
    fakeServer([conversation()]);
    const confirmed = deferred<ConversationSummary>();
    vi.mocked(updateConversation).mockReturnValueOnce(confirmed.promise);
    renderConversationList();

    const { input } = await startRename("Trip planning");
    expect(input.value).toBe("Trip planning");
    await waitFor(() => expect(document.activeElement).toBe(input));

    fireEvent.change(input, { target: { value: "  Lisbon trip  " } });
    pressKey(input, "Enter");

    expect(await screen.findByText("Lisbon trip")).toBeTruthy();
    expect(updateConversation).toHaveBeenCalledTimes(1);
    expect(updateConversation).toHaveBeenCalledWith("switchboard", "conv-c", { title: "Lisbon trip" });
    await expectFocusOnRowActions("Lisbon trip");

    await act(async () => {
      confirmed.resolve(conversation({ title: "Lisbon trip", updated_at: "2026-10-03T00:00:00Z" }));
    });
    expect(screen.getByText("Lisbon trip")).toBeTruthy();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("keeps an invalid title editable with an associated inline error and no PATCH", async () => {
    fakeServer([conversation()]);
    renderConversationList();
    const { input } = await startRename("Trip planning");

    fireEvent.change(input, { target: { value: "   " } });
    pressKey(input, "Enter");
    let error = screen.getByRole("alert");
    expect(error.textContent).toContain("1 to 500 characters");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe(error.id);

    // 501 code points: astral characters count once each, not as two UTF-16 units.
    fireEvent.change(input, { target: { value: "😀".repeat(501) } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    error = screen.getByRole("alert");
    expect(error.textContent).toContain("501 characters");
    expect(input.value).toBe("😀".repeat(501));
    expect(document.activeElement).toBe(input);
    expect(updateConversation).not.toHaveBeenCalled();
  });

  it("cancels on Escape without a PATCH, consuming the key and returning focus", async () => {
    fakeServer([conversation()]);
    renderConversationList();
    const { input } = await startRename("Trip planning");

    fireEvent.change(input, { target: { value: "Changed my mind" } });
    const escape = pressKey(input, "Escape");

    expect(escape.defaultPrevented).toBe(true);
    expect(screen.queryByRole("textbox", { name: "Title for Trip planning" })).toBeNull();
    expect(screen.getByText("Trip planning")).toBeTruthy();
    expect(updateConversation).not.toHaveBeenCalled();
    await expectFocusOnRowActions("Trip planning");
  });

  it("rolls a failed rename back to the confirmed title and keeps the text for retry", async () => {
    fakeServer([conversation()]).failNext(new Error("timeout"));
    renderConversationList();
    const { input } = await startRename("Trip planning");

    fireEvent.change(input, { target: { value: "Lisbon trip" } });
    pressKey(input, "Enter");

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Couldn\'t rename "Trip planning"',
        expect.objectContaining({ description: expect.stringContaining("not confirmed") }),
      ),
    );
    const retry = (await screen.findByRole("textbox", {
      name: "Title for Trip planning",
    })) as HTMLInputElement;
    expect(retry.value).toBe("Lisbon trip");
    expect(screen.getByRole("alert").textContent).toContain("Rename not confirmed");
    expect(screen.queryByText("Lisbon trip")).toBeNull();
    // No automatic replay after an unconfirmed outcome.
    expect(updateConversation).toHaveBeenCalledTimes(1);
  });

  it("archives without confirmation, offers Undo, and undo issues the separate inverse PATCH", async () => {
    fakeServer([conversation(), conversation({ id: "conv-d", title: "Groceries" })]);
    renderConversationList();

    await openRowActions("Trip planning");
    chooseAction("Archive conversation");

    await waitFor(() => expect(screen.queryByText("Trip planning")).toBeNull());
    expect(screen.getByText("Groceries")).toBeTruthy();
    expect(updateConversation).toHaveBeenCalledWith("switchboard", "conv-c", { status: "archived" });
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Archived "Trip planning"', expect.anything()),
    );
    // The row is gone, but focus stays in the list rather than the document.
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Conversations");

    await act(async () => undoActionFromToast()());

    expect(updateConversation).toHaveBeenLastCalledWith("switchboard", "conv-c", { status: "active" });
    expect(await screen.findByText("Trip planning")).toBeTruthy();
    expect(toast.success).toHaveBeenCalledWith('Restored "Trip planning" to active conversations');
  });

  it("rolls a failed archive back into the Active listing with a named error", async () => {
    fakeServer([conversation()]).failNext();
    renderConversationList();

    await openRowActions("Trip planning");
    chooseAction("Archive conversation");

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Couldn\'t archive "Trip planning"',
        expect.objectContaining({ action: expect.objectContaining({ label: "Retry" }) }),
      ),
    );
    expect(screen.getByText("Trip planning")).toBeTruthy();
    expect(toast).not.toHaveBeenCalled();
  });

  it("keeps an archived conversation visibly archived when its undo is not confirmed", async () => {
    const server = fakeServer([conversation()]);
    renderConversationList();

    await openRowActions("Trip planning");
    chooseAction("Archive conversation");
    await waitFor(() => expect(toast).toHaveBeenCalled());
    server.failNext();

    await act(async () => undoActionFromToast()());

    expect(toast.error).toHaveBeenCalledWith(
      'Undo not confirmed. "Trip planning" is still archived.',
      expect.objectContaining({ action: expect.objectContaining({ label: "Retry undo" }) }),
    );
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.queryByText("Trip planning")).toBeNull();
  });

  it("opens on Active and requests Archived through the list API with its own empty state", async () => {
    fakeServer([conversation({ id: "conv-d", title: "Groceries" })]);
    renderConversationList();
    await screen.findByText("Groceries");

    const group = screen.getByRole("group", { name: "Conversation status" });
    const archived = within(group).getByRole("button", { name: "Archived" });
    expect(within(group).getByRole("button", { name: "Active" }).getAttribute("aria-pressed")).toBe("true");
    // Active is the list API's default status.
    expect(listConversations).toHaveBeenCalledWith("switchboard", undefined);

    fireEvent.click(archived);
    expect(archived.getAttribute("aria-pressed")).toBe("true");
    expect(await screen.findByText("No archived conversations.")).toBeTruthy();
    expect(screen.queryByText("No conversations yet.")).toBeNull();
    expect(listConversations).toHaveBeenCalledWith("switchboard", { status: "archived" });
  });

  it("unarchives from the Archived listing with a status-active PATCH", async () => {
    fakeServer([conversation({ status: "archived" })]);
    renderConversationList();
    fireEvent.click(screen.getByRole("button", { name: "Archived" }));
    await screen.findByText("Trip planning");
    expect(screen.getByTestId("conversation-status").textContent).toBe("Archived");

    await openRowActions("Trip planning");
    chooseAction("Unarchive conversation");

    await waitFor(() =>
      expect(updateConversation).toHaveBeenCalledWith("switchboard", "conv-c", { status: "active" }),
    );
    await waitFor(() => expect(screen.queryByText("Trip planning")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Active" }));
    expect(await screen.findByText("Trip planning")).toBeTruthy();
  });

  it("blocks duplicate metadata submission for a conversation until its PATCH settles", async () => {
    fakeServer([conversation(), conversation({ id: "conv-d", title: "Groceries" })]);
    const confirmed = deferred<ConversationSummary>();
    vi.mocked(updateConversation).mockReturnValueOnce(confirmed.promise);
    renderConversationList();

    const { input } = await startRename("Trip planning");
    fireEvent.change(input, { target: { value: "Lisbon trip" } });
    pressKey(input, "Enter");
    await screen.findByText("Lisbon trip");

    await openRowActions("Lisbon trip");
    expect(
      screen.getByRole("menuitem", { name: "Archive conversation" }).getAttribute("aria-disabled"),
    ).toBe("true");
    pressKey(screen.getByRole("menu"), "Escape");

    // Another conversation stays editable while C is pending.
    await openRowActions("Groceries");
    expect(
      screen.getByRole("menuitem", { name: "Archive conversation" }).getAttribute("aria-disabled"),
    ).toBeNull();

    await act(async () => confirmed.resolve(conversation({ title: "Lisbon trip" })));
    expect(updateConversation).toHaveBeenCalledTimes(1);
  });

  it("states the search scope, disables the status filter and names each result's status", async () => {
    fakeServer([]);
    vi.mocked(useConversationSearch).mockImplementation(actualHooks.useConversationSearch);
    vi.mocked(searchConversations).mockResolvedValue(
      page([conversation({ status: "archived" }), conversation({ id: "conv-d", title: "Groceries" })]),
    );
    renderConversationList();

    const archived = screen.getByRole("button", { name: "Archived" }) as HTMLButtonElement;
    fireEvent.click(archived);
    fireEvent.change(screen.getByPlaceholderText("Search..."), { target: { value: "trip" } });

    expect(await screen.findByText("Searching active and archived conversations")).toBeTruthy();
    expect(archived.disabled).toBe(true);
    expect(await screen.findByText("Trip planning")).toBeTruthy();
    expect(screen.getAllByTestId("conversation-status").map((badge) => badge.textContent)).toEqual([
      "Archived",
      "Active",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() => expect(archived.disabled).toBe(false));
    expect(archived.getAttribute("aria-pressed")).toBe("true");
  });

  it("identifies the selected filter when the sidebar is collapsed", async () => {
    fakeServer([]);
    renderConversationList();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    const toggle = screen.getByRole("button", { name: "Show archived conversations" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() =>
      expect(listConversations).toHaveBeenCalledWith("switchboard", { status: "archived" }),
    );
  });
});
