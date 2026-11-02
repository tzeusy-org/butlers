// @vitest-environment jsdom
/** Existing dashboard-shell failed-read/empty contract; bu-xqcfbr R047/R081. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { PropsWithChildren } from "react";

import { FloatingChatWidget } from "@/components/chat/FloatingChatWidget";
import { CommandRegistryProvider } from "@/lib/command-registry";
import { PageContextProvider } from "@/lib/page-context";
import { queryClient as productionClient } from "@/lib/query-client";
import { conversationKeys } from "@/hooks/use-conversations";
import { __resetChatUnreadWatermarkForTests, useChatUnreadBadge } from "@/hooks/use-chat-unread";
import type { ConversationSummary } from "@/api/types";
import { announce } from "@/lib/shell-announcer";

vi.mock("@/lib/shell-announcer", () => ({ announce: vi.fn() }));

const listPath = "/api/butlers/switchboard/conversations";
const id = "00000000-0000-4000-8000-000000000001";
const messagesPath = `${listPath}/${id}/messages`;
const listKey = conversationKeys.list("switchboard");
const messagesKey = conversationKeys.messages("switchboard", id);
const calls: Array<{ path: string; method: string }> = [];
const clients: QueryClient[] = [];
let listResponse: () => Response | Promise<Response>;
let messagesResponse: () => Response;

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, meta: {} }), {
    status, headers: { "Content-Type": "application/json" },
  });
}
function failed(status = 503) {
  return new Response(JSON.stringify({ error: { code: "READ_UNAVAILABLE", message: "Read unavailable" } }), {
    status, headers: { "Content-Type": "application/json" },
  });
}
function conversation(reply: string | null): ConversationSummary {
  return {
    id, butler_name: "switchboard", title: "Confirmed thread", status: "active",
    created_at: "2026-07-05T00:00:00Z", updated_at: "2026-07-05T00:00:00Z",
    message_count: 1, routed_butler: null, latest_assistant_reply_at: reply,
  };
}
function client(productionRetry = false) {
  const value = new QueryClient(productionRetry ? { defaultOptions: productionClient.getDefaultOptions() } : {
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(value);
  return value;
}
function mount(value = client()) {
  render(
    <MemoryRouter>
      <QueryClientProvider client={value}>
        <CommandRegistryProvider>
          <PageContextProvider><FloatingChatWidget /></PageContextProvider>
        </CommandRegistryProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return value;
}
async function settled(value: QueryClient, status: "success" | "error") {
  await waitFor(() => {
    expect(value.getQueryState(listKey)?.status).toBe(status);
    expect(value.getQueryState(listKey)?.fetchStatus).toBe("idle");
  });
}
async function refresh(value: QueryClient, key: readonly unknown[] = listKey) {
  await act(async () => { await value.invalidateQueries({ queryKey: key, exact: true }); });
}
function listCalls() { return calls.filter((c) => c.path === listPath).length; }
function assertNoMutations() { expect(calls.every((c) => c.method === "GET")).toBe(true); }
function open() { fireEvent.click(screen.getByTestId("floating-chat-trigger")); }
function panel() { return within(screen.getByRole("dialog")); }

beforeEach(() => {
  calls.length = 0;
  localStorage.clear();
  __resetChatUnreadWatermarkForTests();
  vi.mocked(announce).mockClear();
  listResponse = () => json([]);
  messagesResponse = () => json([]);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), location.href).pathname;
    const method = options?.method ?? "GET";
    calls.push({ path, method });
    if (method !== "GET") throw new Error("Unexpected mutation in read recovery");
    if (path === listPath) return listResponse();
    if (path === messagesPath) return messagesResponse();
    if (path === "/api/settings/pricing") return json({});
    throw new Error(`Unexpected fixture request: ${path}`);
  }));
});
afterEach(() => {
  cleanup();
  for (const value of clients.splice(0)) value.clear();
  vi.unstubAllGlobals();
});

it("exposes a closed-trigger discovery failure and retries only the real list source", async () => {
  listResponse = () => failed();
  const value = mount();
  await settled(value, "error");
  expect((await screen.findByRole("alert")).textContent).toContain("Could not load conversation updates.");
  expect((screen.getByRole("button", { name: "Talk to Butlers" }) as HTMLButtonElement).disabled).toBe(false);
  const before = listCalls();
  listResponse = () => json([]);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await settled(value, "success");
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(listCalls()).toBe(before + 1);
  expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  expect(announce).not.toHaveBeenCalled();
  assertNoMutations();
});

it("keeps pending or failed default-thread discovery distinct from confirmed empty and retains typed text", async () => {
  let resolve!: (response: Response) => void;
  listResponse = () => new Promise<Response>((done) => { resolve = done; });
  const value = mount();
  expect(screen.getByTestId("floating-chat-trigger").getAttribute("aria-busy")).toBe("true");
  open();
  expect(screen.queryByText(/No messages yet/)).toBeNull();
  await act(async () => { resolve(failed()); });
  await settled(value, "error");
  expect((await panel().findByRole("alert")).textContent).toContain("Could not load conversations.");
  expect(screen.queryByText(/No messages yet/)).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsent draft" } });
  listResponse = () => failed();
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await settled(value, "error");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Unsent draft");
  listResponse = () => json([conversation(null)]);
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await settled(value, "success");
  expect(await screen.findByText(/No messages yet/)).toBeDefined();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Unsent draft");
  expect(calls.some((c) => c.path === messagesPath)).toBe(false);
  assertNoMutations();
});

it("labels a cached failed poll stale without advancing read watermarks or inventing reply announcements", async () => {
  let reply = "2026-07-05T00:00:20Z";
  listResponse = () => json([conversation(reply)]);
  const value = mount();
  await settled(value, "success");
  await waitFor(() => expect(localStorage.length).toBeGreaterThan(0));
  expect(screen.queryByTestId("chat-widget-unread-badge")).toBeNull();
  reply = "2026-07-05T00:00:45Z";
  await refresh(value);
  expect(await screen.findByTestId("chat-widget-unread-badge")).toBeDefined();
  expect(announce).toHaveBeenCalledTimes(1);
  const watermark = JSON.stringify({ ...localStorage });
  listResponse = () => failed();
  await refresh(value);
  expect((await screen.findByRole("alert")).textContent).toContain("Could not load conversation updates.");
  expect(screen.getByText(/Saved updates may be out of date/)).toBeDefined();
  open();
  expect(await panel().findByText(/Saved conversation details may be out of date/)).toBeDefined();
  expect(JSON.stringify({ ...localStorage })).toBe(watermark);
  expect(announce).toHaveBeenCalledTimes(1);
  listResponse = () => json([conversation(reply)]);
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await settled(value, "success");
  await waitFor(() => expect(JSON.stringify({ ...localStorage })).not.toBe(watermark));
  assertNoMutations();
});

it("keeps cached history and the draft while list and message retries retain their own identities", async () => {
  listResponse = () => json([conversation(null)]);
  messagesResponse = () => json([{ id: "message-1", conversation_id: id, role: "user", content: "Confirmed history", created_at: "2026-07-05T00:00:00Z", tool_calls: null, error: null }]);
  const value = mount();
  await settled(value, "success");
  open();
  expect(await screen.findByText("Confirmed history")).toBeDefined();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep this draft" } });
  listResponse = () => failed();
  await refresh(value);
  expect((await panel().findByRole("alert")).textContent).toContain("Could not load conversations.");
  const messageReads = calls.filter((c) => c.path === messagesPath).length;
  listResponse = () => json([conversation(null)]);
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await settled(value, "success");
  expect(calls.filter((c) => c.path === messagesPath)).toHaveLength(messageReads);
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Keep this draft");
  messagesResponse = () => failed();
  await refresh(value, messagesKey);
  expect((await panel().findByRole("alert")).textContent).toContain("Could not load conversation history.");
  const listsBefore = listCalls();
  messagesResponse = () => json([]);
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(panel().queryByRole("alert")).toBeNull());
  expect(listCalls()).toBe(listsBefore);
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("Keep this draft");
  assertNoMutations();
});

it("does not consume explicit new-conversation intent when discovery recovers", async () => {
  listResponse = () => failed();
  const value = mount();
  await settled(value, "error");
  open();
  await panel().findByRole("alert");
  fireEvent.click(panel().getByRole("button", { name: "New conversation" }));
  listResponse = () => json([conversation(null)]);
  fireEvent.click(panel().getByRole("button", { name: "Try again" }));
  await settled(value, "success");
  expect(screen.getByText(/No messages yet/)).toBeDefined();
  expect(calls.some((c) => c.path === messagesPath)).toBe(false);
  assertNoMutations();
});

it("retains the boolean unread API and disabled-query behavior through actual hooks", async () => {
  const value = client();
  function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={value}>{children}</QueryClientProvider>;
  }
  const hook = renderHook(() => useChatUnreadBadge("", false), { wrapper: Wrapper });
  expect(hook.result.current).toBe(false);
  expect(calls).toHaveLength(0);
  hook.unmount();
  mount(value);
  await settled(value, "success");
  expect(screen.getByTestId("floating-chat-trigger").getAttribute("aria-busy")).toBe("false");
  expect(screen.queryByRole("alert")).toBeNull();
  open();
  expect(await screen.findByText(/No messages yet/)).toBeDefined();
  assertNoMutations();
});

it("surfaces an owner denial without changing the production no-automatic-retry policy", async () => {
  listResponse = () => failed(401);
  const value = mount(client(true));
  await settled(value, "error");
  expect((await screen.findByRole("alert")).textContent).toContain("Could not load conversation updates.");
  expect(listCalls()).toBe(1);
  expect(value.getQueryState(listKey)?.fetchFailureCount).toBe(1);
  assertNoMutations();
});
