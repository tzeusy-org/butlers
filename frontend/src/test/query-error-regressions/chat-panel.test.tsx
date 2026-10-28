// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

import { ChatContent } from "@/components/chat/ChatPanel";
import { PageContextProvider } from "@/lib/page-context";
import { queryClient as applicationClient } from "@/lib/query-client";
import { conversationKeys, messageSearchKeys } from "@/hooks/use-conversations";
import { ownerSessionSignal } from "@/api/owner-session";
import type { ConversationSummary, Message, MessageSearchResult } from "@/api/types";

// Real components, QueryClient, hooks, client and ownerFetch; only HTTP is stubbed.
// A disabled history query does not establish an empty discovered conversation.
const EMPTY_THREAD = "No messages yet. Start the conversation below.";
const BASE = "/api/butlers/switchboard/conversations";
const SEARCH = `${BASE}/search`;
const MESSAGE_SEARCH = "/api/conversations/messages/search";
const PRIVATE_ERROR = "synthetic-private-error-payload";
const timestamp = "2026-10-20T00:00:00Z";

function conversation(id = "existing", title = "Existing conversation"): ConversationSummary {
  return {
latest_assistant_reply_at: null, id, title, butler_name: "switchboard", status: "active", created_at: timestamp,
    updated_at: timestamp, message_count: 1, routed_butler: null };
}

function message(id: string): Message {
  return {
model_name: null,
sources: [],
citations: [],
routed_butler: null,
page_context: null,
captured_at: null, id: `message-${id}`, conversation_id: id, role: "user", content: `History for ${id}`,
    tool_calls: null, error: null, input_tokens: null, output_tokens: null, duration_ms: null,
    session_id: null, request_id: null, created_at: timestamp };
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(status === 200 ? { data, meta: { has_more: false, next_cursor: null } } : { detail: PRIVATE_ERROR }),
    { status, headers: { "Content-Type": "application/json" } });
}

function messageSearchPage(items: MessageSearchResult[]) {
  return new Response(JSON.stringify({ items, next_cursor: null, limit: 20 }),
    { status: 200, headers: { "Content-Type": "application/json" } });
}

let base: () => Response | Promise<Response>;
let search: () => Response | Promise<Response>;
let messageSearch: () => Response | Promise<Response>;
let history: (id: string) => Response | Promise<Response>;
let reads: string[];
let clients: QueryClient[];
let transportViolations: string[];

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  clients = [];
  reads = [];
  transportViolations = [];
  base = () => json([]);
  search = () => json([]);
  messageSearch = () => messageSearchPage([]);
  history = (id) => json([message(id)]);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), location.href);
    // Also check outside Query's caught rejection path, so a transport
    // regression cannot masquerade as the intended synthetic HTTP failure.
    if ((init?.method ?? "GET") !== "GET") transportViolations.push("unexpected mutation");
    if (init?.credentials !== "same-origin") transportViolations.push("credentials changed");
    if (new Headers(init?.headers).has("X-API-Key")) transportViolations.push("header authority added");
    reads.push(url.pathname);
    if (url.pathname === "/api/settings/pricing") return json({});
    if (url.pathname === BASE) return base();
    if (url.pathname === SEARCH) {
      expect(url.searchParams.get("q")).toBe("needle");
      return search();
    }
    if (url.pathname === MESSAGE_SEARCH) {
      expect(url.searchParams.get("q")).toBe("needle");
      return messageSearch();
    }
    const match = url.pathname.match(/^\/api\/butlers\/switchboard\/conversations\/([^/]+)\/messages$/);
    if (match) return history(match[1]);
    transportViolations.push("unexpected route");
    throw new Error("Unexpected synthetic HTTP route");
  }));
});

afterEach(async () => {
  cleanup();
  await Promise.all(clients.map((client) => client.cancelQueries()));
  clients.forEach((client) => client.clear());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  expect(transportViolations).toEqual([]);
});

function mount(authPolicy = false) {
  const queries = applicationClient.getDefaultOptions().queries;
  const client = new QueryClient({ defaultOptions: {
    queries: authPolicy ? queries : { ...queries, retry: false },
  } });
  clients.push(client);
  const view = render(<MemoryRouter><QueryClientProvider client={client}>
    <PageContextProvider><ChatContent butlerName="switchboard" /></PageContextProvider>
  </QueryClientProvider></MemoryRouter>);
  return { client, ...view };
}

async function settled(client: QueryClient, key: readonly unknown[], status: "success" | "error") {
  await waitFor(() => expect(client.getQueryState(key)?.status).toBe(status));
}

function discoveryAlert() {
  const alert = screen.getAllByRole("alert").find((node) => node.textContent?.includes("conversation discovery"));
  expect(alert).toBeDefined();
  return within(alert!);
}

async function activateSearch(client: QueryClient) {
  fireEvent.change(screen.getByPlaceholderText("Search..."), { target: { value: "needle" } });
  // Exercise the production 300ms debounce, not a hook-return replacement.
  await waitFor(() => expect(client.getQueryState(conversationKeys.search("switchboard", "needle"))).toBeDefined());
}

function count(path: string) { return reads.filter((read) => read === path).length; }

describe("ChatPanel source-specific discovery and search recovery", () => {
  it("keeps cold discovery unavailable independently of successful active search and retries only that read", async () => {
    base = () => json(null, 503);
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "error");
    expect(screen.queryByText(EMPTY_THREAD)).toBeNull();
    discoveryAlert();
    await activateSearch(client);
    await settled(client, conversationKeys.search("switchboard", "needle"), "success");
    await settled(client, messageSearchKeys.search("needle"), "success");
    expect(screen.queryByText(EMPTY_THREAD)).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Type a message..." }), { target: { value: "Unsent draft" } });
    const before = [...reads];
    base = () => json([]);
    const retry = discoveryAlert().getByRole("button", { name: "Try again" });
    retry.focus();
    expect(document.activeElement).toBe(retry);
    await userEvent.keyboard("{Enter}");
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(reads.slice(before.length)).toEqual([BASE]);
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Type a message..." }) as HTMLTextAreaElement).value).toBe("Unsent draft");
    expect(document.body.textContent).not.toContain(PRIVATE_ERROR);
  });

  it("labels a failed confirmed-cache refresh stale while retaining selection, history and the typed draft", async () => {
    base = () => json([conversation()]);
    const { client } = mount();
    await screen.findByText("History for existing");
    fireEvent.change(screen.getByRole("textbox", { name: "Type a message..." }), { target: { value: "Keep this draft" } });
    base = () => json(null, 503);
    await act(async () => { await client.refetchQueries({ queryKey: conversationKeys.list("switchboard"), exact: true }); });
    await settled(client, conversationKeys.list("switchboard"), "error");
    discoveryAlert();
    expect(screen.getByText(/cached.*out of date/i)).toBeTruthy();
    expect(screen.getByText("History for existing")).toBeTruthy();
    base = () => json([conversation("other", "Newly first row"), conversation()]);
    const before = [...reads];
    fireEvent.click(discoveryAlert().getByRole("button", { name: "Try again" }));
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(reads.slice(before.length)).toEqual([BASE]);
    expect(screen.getByText("History for existing")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Type a message..." }) as HTMLTextAreaElement).value).toBe("Keep this draft");
  });

  it("honors explicit New during failed discovery when Retry later discovers an old thread", async () => {
    base = () => json(null, 503);
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "error");
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Type a message..." }), { target: { value: "A new-thread draft" } });
    base = () => json([conversation()]);
    fireEvent.click(discoveryAlert().getByRole("button", { name: "Try again" }));
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(screen.getByText("New conversation")).toBeTruthy();
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
    expect(count(`${BASE}/existing/messages`)).toBe(0);
    expect((screen.getByRole("textbox", { name: "Type a message..." }) as HTMLTextAreaElement).value).toBe("A new-thread draft");
  });

  it("distinguishes pending discovery, successful zero and separately failed selected history", async () => {
    let release!: (response: Response) => void;
    base = () => new Promise<Response>((resolve) => { release = resolve; });
    const { client } = mount();
    expect(screen.queryByText(EMPTY_THREAD)).toBeNull();
    await act(async () => { release(json([])); });
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
    history = () => json(null, 503);
    base = () => json([conversation()]);
    await act(async () => { await client.refetchQueries({ queryKey: conversationKeys.list("switchboard"), exact: true }); });
    await settled(client, conversationKeys.messages("switchboard", "existing"), "error");
    expect(screen.queryByText(EMPTY_THREAD)).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("conversation history");
    expect(document.body.textContent).not.toContain("conversation discovery");
    const before = [...reads];
    history = () => json([message("existing")]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("History for existing");
    expect(reads.slice(before.length)).toEqual([`${BASE}/existing/messages`]);
  });

  it("keeps simultaneous base, conversation-search and message-search failures on distinct retry identities", async () => {
    base = () => json(null, 503);
    search = () => json(null, 503);
    messageSearch = () => json(null, 503);
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "error");
    await activateSearch(client);
    await settled(client, conversationKeys.search("switchboard", "needle"), "error");
    await settled(client, messageSearchKeys.search("needle"), "error");
    for (const [label, path, recover] of [
      ["conversation search results", SEARCH, () => { search = () => json([conversation("found", "Found conversation")]); }],
      ["message search results", MESSAGE_SEARCH, () => { messageSearch = () => messageSearchPage([]); }],
    ] as const) {
      const before = [...reads];
      recover();
      const alert = screen.getAllByRole("alert").find((node) => node.textContent?.includes(`Could not load ${label}.`));
      fireEvent.click(within(alert!).getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(screen.queryByText(`Could not load ${label}.`)).toBeNull());
      expect(reads.slice(before.length)).toEqual([path]);
      discoveryAlert();
    }
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() => expect(screen.queryByText("Found conversation")).toBeNull());
    expect(screen.getByText("Could not load conversations.")).toBeTruthy();
    discoveryAlert();
    expect(screen.queryByText("No conversations yet.")).toBeNull();
  });

  it("keeps successful search selection and cross-butler links usable during failed base discovery", async () => {
    base = () => json(null, 503);
    search = () => json([conversation("found", "Found conversation")]);
    const hits: MessageSearchResult[] = [
      { message_id: "message-found", conversation_id: "found", role: "user", created_at: timestamp,
        butler_name: "switchboard", session_id: null, snippet: "Same-butler needle", highlight_ranges: [], deep_link: "/butlers/switchboard" },
      { message_id: "foreign-message", conversation_id: "foreign", role: "assistant", created_at: timestamp,
        butler_name: "finance", session_id: "foreign-session", snippet: "Cross-butler needle", highlight_ranges: [], deep_link: "/sessions/foreign-session" },
    ];
    messageSearch = () => messageSearchPage(hits);
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "error");
    await activateSearch(client);
    await screen.findByText("Cross-butler needle");
    const link = screen.getByText("Cross-butler needle").closest("a")!;
    expect(link.getAttribute("href")).toBe("/sessions/foreign-session");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    fireEvent.click(screen.getByText("Same-butler needle"));
    await screen.findByText("History for found");
    discoveryAlert();
    base = () => json([conversation("existing"), conversation("found", "Found conversation")]);
    fireEvent.click(discoveryAlert().getByRole("button", { name: "Try again" }));
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(screen.getByText("History for found")).toBeTruthy();
    expect(count(`${BASE}/existing/messages`)).toBe(0);
  });

  it("retains the production 401 no-retry/session-loss policy and never exposes raw response details", async () => {
    const signal = ownerSessionSignal();
    base = () => json(null, 401);
    const { client } = mount(true);
    await settled(client, conversationKeys.list("switchboard"), "error");
    discoveryAlert();
    expect(signal.aborted).toBe(true);
    expect(count(BASE)).toBe(1);
    expect(screen.queryByText(EMPTY_THREAD)).toBeNull();
    expect(document.body.textContent).not.toContain(PRIVATE_ERROR);
  });

  it("preserves the newest implicit new-thread draft when failed discovery retries to an existing thread", async () => {
    base = () => json(null, 503);
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "error");
    const composer = screen.getByRole("textbox", { name: "Type a message..." });
    fireEvent.change(composer, { target: { value: "First new-thread draft" } });
    fireEvent.change(composer, { target: { value: "Newest new-thread draft" } });
    const before = [...reads];
    base = () => json([conversation()]);
    fireEvent.click(discoveryAlert().getByRole("button", { name: "Try again" }));
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect((screen.getByRole("textbox", { name: "Type a message..." }) as HTMLTextAreaElement).value).toBe("Newest new-thread draft");
    expect(screen.getByText("New conversation")).toBeTruthy();
    expect(reads.slice(before.length)).toEqual([BASE]);
    expect(count(`${BASE}/existing/messages`)).toBe(0);
    expect(screen.queryByText(/Could not load conversation discovery/)).toBeNull();
  });

  it("keeps implicit new-thread intent after enabled typing then clearing during pending discovery refresh", async () => {
    let release!: (response: Response) => void;
    const { client } = mount();
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
    base = () => new Promise<Response>((resolve) => { release = resolve; });
    let refresh!: Promise<void>;
    act(() => { refresh = client.refetchQueries({ queryKey: conversationKeys.list("switchboard"), exact: true }); });
    await waitFor(() => expect(count(BASE)).toBe(2));
    expect(client.getQueryState(conversationKeys.list("switchboard"))?.fetchStatus).toBe("fetching");
    const composer = screen.getByRole("textbox", { name: "Type a message..." });
    expect((composer as HTMLTextAreaElement).disabled).toBe(false);
    fireEvent.change(composer, { target: { value: "A deliberately new thread" } });
    expect((composer as HTMLTextAreaElement).value).toBe("A deliberately new thread");
    fireEvent.change(composer, { target: { value: "" } });
    expect((composer as HTMLTextAreaElement).value).toBe("");
    await act(async () => { release(json([conversation()])); await refresh; });
    await settled(client, conversationKeys.list("switchboard"), "success");
    expect(screen.getByText("New conversation")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Type a message..." }) as HTMLTextAreaElement).value).toBe("");
    expect(count(`${BASE}/existing/messages`)).toBe(0);
    expect(count(BASE)).toBe(2);
    expect(screen.getByText(EMPTY_THREAD)).toBeTruthy();
  });
});
