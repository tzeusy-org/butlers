// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render as renderWithoutQuery,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import type { ConversationSummary } from "@/api/types.ts";
import type { PaletteCommand } from "@/lib/command-registry.tsx";
import { ConversationHeader } from "./ConversationHeader.tsx";

vi.mock("@/api/index.ts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/index.ts")>()),
  listConversations: vi.fn(),
  getConversationById: vi.fn(),
  updateConversation: vi.fn(),
}));

vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { toast: toastFn };
});

let registeredCommands: PaletteCommand[] = [];
vi.mock("@/lib/command-registry.tsx", () => ({
  useRegisterCommands: (commands: PaletteCommand[]) => {
    registeredCommands = commands;
  },
}));

import { listConversations, updateConversation } from "@/api/index.ts";
import { useConversations } from "@/hooks/use-conversations.ts";
import { useSelectedConversation } from "@/hooks/use-conversation-metadata.ts";

function QueryWrapper({ children }: { children: ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function render(ui: ReactNode) {
  return renderWithoutQuery(ui, { wrapper: QueryWrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
  registeredCommands = [];
});

afterEach(() => cleanup());

describe("ConversationHeader — current-turn accountability", () => {
  it("keeps the addressed Butler as plain text before any receipt", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={null}
        messages={[]}
        pricingMap={null}
      />,
    );

    expect(screen.getByText("switchboard")).toBeDefined();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("does not turn a historical route into current-turn accountability", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={{
          id: "conversation-1",
          butler_name: "switchboard",
          title: "Existing conversation",
          status: "active",
          created_at: "2026-08-02T00:00:00.000Z",
          updated_at: "2026-08-02T00:00:00.000Z",
          message_count: 1,
          routed_butler: "relationship",
        }}
        messages={[]}
        pricingMap={null}
      />,
    );

    expect(screen.getByText("switchboard")).toBeDefined();
    expect(screen.queryByRole("link", { name: "relationship" })).toBeNull();
  });

  it("links only the exact named receipt for the current stream", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={{
          id: "conversation-1",
          butler_name: "switchboard",
          title: "Existing conversation",
          status: "active",
          created_at: "2026-08-02T00:00:00.000Z",
          updated_at: "2026-08-02T00:00:00.000Z",
          message_count: 1,
          routed_butler: "relationship",
        }}
        messages={[]}
        pricingMap={null}
        routedButler="finance and planning"
      />,
    );

    const link = screen.getByRole("link", { name: "finance and planning" });
    expect(link.getAttribute("href")).toBe("/butlers/finance%20and%20planning");
    expect(link.parentElement?.textContent).toContain("Routed to");
    expect(link.parentElement?.textContent).not.toContain("Handled by");
    expect(screen.queryByRole("link", { name: "relationship" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Rename and reversible archive from the shared header (REQ-dashboard-chat-ui-017)
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

/** A chat surface: the Active list plus the selected conversation's header. */
function Surface({ selectedId }: { selectedId: string | null }) {
  const { data } = useConversations("switchboard");
  const selected = useSelectedConversation(selectedId, data?.data ?? []);
  return (
    <ConversationHeader
      butlerName="switchboard"
      conversation={selected}
      messages={[]}
      pricingMap={null}
    />
  );
}

/** Stateful list + PATCH endpoints; `failNext()` rejects one PATCH unapplied. */
function fakeServer(rows: ConversationSummary[]) {
  let state = [...rows];
  const failures: Error[] = [];
  vi.mocked(listConversations).mockImplementation(async (_butler, params) => ({
    data: state.filter((row) => row.status === (params?.status ?? "active")),
    meta: { total: state.length, offset: 0, limit: 20 },
  }));
  vi.mocked(updateConversation).mockImplementation(async (_butler, id, body) => {
    const failure = failures.shift();
    if (failure) throw failure;
    let updated: ConversationSummary | undefined;
    state = state.map((row) => (row.id === id ? (updated = { ...row, ...body }) : row));
    return updated!;
  });
  return { failNext: () => failures.push(new Error("network")) };
}

function command(prefix: string) {
  const found = registeredCommands.find((candidate) => candidate.label.startsWith(prefix));
  expect(found, `palette command starting "${prefix}"`).toBeDefined();
  return found!;
}

function pressKey(target: Element, key: string) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

describe("ConversationHeader — rename and archive (REQ-dashboard-chat-ui-017)", () => {
  it("exposes no metadata actions for a new conversation without a persisted identity", () => {
    render(<Surface selectedId={null} />);

    expect(screen.getByText("New conversation")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Actions for/ })).toBeNull();
    expect(registeredCommands).toEqual([]);
  });

  it("registers target-specific palette actions naming the title and owning butler", async () => {
    fakeServer([conversation()]);
    render(<Surface selectedId="conv-c" />);
    await screen.findByText("Trip planning");

    expect(registeredCommands.map((candidate) => candidate.label)).toEqual([
      "Rename conversation: Trip planning (switchboard)",
      "Archive conversation: Trip planning (switchboard)",
    ]);
    expect(
      screen.getByRole("button", { name: "Actions for Trip planning (switchboard)" }),
    ).toBeTruthy();
  });

  it("renames from the palette with Enter, and Escape cancels without a PATCH", async () => {
    fakeServer([conversation()]);
    render(<Surface selectedId="conv-c" />);
    await screen.findByText("Trip planning");

    act(() => command("Rename conversation").perform());
    let input = await screen.findByRole("textbox", { name: "Title for Trip planning" });
    const escape = pressKey(input, "Escape");
    expect(escape.defaultPrevented).toBe(true);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(updateConversation).not.toHaveBeenCalled();

    act(() => command("Rename conversation").perform());
    input = await screen.findByRole("textbox", { name: "Title for Trip planning" });
    fireEvent.change(input, { target: { value: "Lisbon trip" } });
    const enter = pressKey(input, "Enter");

    expect(enter.defaultPrevented).toBe(true);
    expect(await screen.findByText("Lisbon trip")).toBeTruthy();
    expect(updateConversation).toHaveBeenCalledWith("switchboard", "conv-c", { title: "Lisbon trip" });
    await waitFor(() =>
      expect(document.activeElement?.getAttribute("aria-label")).toBe(
        "Actions for Lisbon trip (switchboard)",
      ),
    );
  });

  it("keeps an archived selected conversation in the header, disclosed and unarchivable", async () => {
    fakeServer([conversation()]);
    render(<Surface selectedId="conv-c" />);
    await screen.findByText("Trip planning");

    await act(async () => command("Archive conversation").perform());

    expect(updateConversation).toHaveBeenCalledTimes(1);
    expect(updateConversation).toHaveBeenCalledWith("switchboard", "conv-c", { status: "archived" });
    // The selected thread survives leaving the Active list.
    await waitFor(() => expect(screen.getByText("Archived")).toBeTruthy());
    expect(screen.getByText("Trip planning")).toBeTruthy();
    expect(screen.getByText(/Sending a message makes it active again/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Unarchive" })).toBeTruthy();
    expect(registeredCommands.map((candidate) => candidate.label)).toContain(
      "Unarchive conversation: Trip planning (switchboard)",
    );
  });

  it("rolls a failed header unarchive back to the confirmed archived state", async () => {
    const server = fakeServer([conversation()]);
    render(<Surface selectedId="conv-c" />);
    await screen.findByText("Trip planning");
    await act(async () => command("Archive conversation").perform());
    await screen.findByRole("button", { name: "Unarchive" });

    server.failNext();
    fireEvent.click(screen.getByRole("button", { name: "Unarchive" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Couldn\'t unarchive "Trip planning"',
        expect.objectContaining({ description: expect.stringContaining("not confirmed") }),
      ),
    );
    expect(screen.getByText("Archived")).toBeTruthy();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("never reopens a late-failing rename on a different selected conversation", async () => {
    fakeServer([conversation(), conversation({ id: "conv-d", title: "Groceries" })]);
    let rejectRename!: (error: Error) => void;
    vi.mocked(updateConversation).mockImplementationOnce(
      () => new Promise((_, reject) => (rejectRename = reject)),
    );
    const view = render(<Surface selectedId="conv-c" />);
    await screen.findByText("Trip planning");

    act(() => command("Rename conversation").perform());
    const input = await screen.findByRole("textbox", { name: "Title for Trip planning" });
    fireEvent.change(input, { target: { value: "Lisbon trip" } });
    pressKey(input, "Enter");

    view.rerender(<Surface selectedId="conv-d" />);
    await screen.findByText("Groceries");
    await act(async () => rejectRename(new Error("timeout")));

    expect(toast.error).toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("Groceries")).toBeTruthy();
    expect(registeredCommands[0].label).toBe("Rename conversation: Groceries (switchboard)");
  });
});
