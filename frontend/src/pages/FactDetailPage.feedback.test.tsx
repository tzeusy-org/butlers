// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { clearOwnerSession } from "@/api/owner-session";
import type { Fact } from "@/api/types";
import { getShellCapability } from "@/lib/shell-capability";

const id = "00000000-0000-4000-8000-000000000001";
const nextId = "00000000-0000-4000-8000-000000000002";
const clients: QueryClient[] = [];
type Action = "confirm" | "retract";
let fact: Fact;
let posts: Action[];
let outcome: number;
let release: (() => void) | undefined;
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
beforeEach(() => {
  posts = []; outcome = 200; release = undefined;
  fact = {
source_episode_status: null,
superseded_by: null,
content_authority: null,
authority_entity_id: null,
authority_entity_name: null,
tenant_id: "owner",
request_id: null,
retention_class: "operational",
sensitivity: "normal",
valid_at: null,
invalid_at: null,
idempotency_key: null,
    id, subject: "Owner", predicate: "preferred_drink", content: "A cup of tea",
    importance: 5, confidence: 0.9, decay_rate: 0.002, permanence: "standard",
    source_butler: "general", source_episode_id: null, session_id: null, supersedes_id: null,
    entity_id: null, entity_name: null, object_entity_id: null, object_entity_name: null,
    validity: "active", scope: "global", reference_count: 0, tags: [], metadata: {},
    created_at: "2026-01-01T10:00:00Z", last_referenced_at: null,
    last_confirmed_at: "2026-01-01T10:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/auth/owner/csrf")) return json({ csrf_token: "test-csrf", csrf_expires_at: "2099-01-01T00:00:00Z" });
    if (options?.method === "POST") {
      const action = url.pathname.endsWith("/confirm") ? "confirm" : "retract";
      expect(url.pathname).toBe(`/api/memory/facts/${id}/${action}`);
      expect(options.body).toBeUndefined(); posts.push(action);
      await new Promise<void>((resolve) => { release = resolve; });
      if (outcome !== 200) return json({ detail: "Memory source unavailable" }, outcome);
      fact = action === "confirm" ? { ...fact, last_confirmed_at: new Date().toISOString() } : { ...fact, validity: "retracted" };
      return json({ data: fact });
    }
    if (url.pathname.startsWith("/api/memory/facts/")) return json({ data: url.pathname.endsWith(nextId) ? { ...fact, id: nextId, content: "Another fact" } : fact });
    throw new Error(`Unexpected endpoint: ${url.pathname}`);
  }));
});
afterEach(() => {
  cleanup(); clients.splice(0).forEach((client) => client.clear());
  clearOwnerSession(); vi.unstubAllGlobals(); vi.useRealTimers();
});
async function route() {
  const { default: Page } = await getShellCapability("/memory/facts/:factId")!.loader();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["memory-facts", "active"], { data: [fact], meta: { total: 1 } }); clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/memory/facts/${id}`]}>
    <Link to={`/memory/facts/${nextId}`}>Another fact</Link><Link to="/away">Leave memory</Link>
    <Routes><Route path="/memory/facts/:factId" element={<Page />} /><Route path="/away" element={<h1>Elsewhere</h1>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await screen.findByRole("heading", { name: "A cup of tea" }); return client;
}
function button(action: Action) { return screen.getByRole("button", { name: action === "confirm" ? "Confirm" : /^Retract/ }); }
function activate(action: Action) {
  if (action === "retract") fireEvent.click(button(action));
  fireEvent.click(button(action));
}
async function finish() {
  await waitFor(() => expect(release).toBeTypeOf("function"));
  await act(async () => { release!(); });
}
function announcements() {
  const region = screen.getByRole("status", { name: "Fact action outcome" });
  const messages: string[] = [];
  const observer = new MutationObserver(() => { if (region.textContent) messages.push(region.textContent); });
  observer.observe(region, { subtree: true, childList: true, characterData: true });
  return { region, messages, stop: () => observer.disconnect() };
}
describe("fact commit feedback through the production route and mutation hooks", () => {
  it.each(["confirm", "retract"] as const)("announces %s only after success, once, retaining keyboard focus", async (action) => {
    await route(); const live = announcements(); const control = button(action); control.focus();
    if (action === "retract") { await userEvent.keyboard(" "); expect(posts).toEqual([]); }
    await userEvent.keyboard(action === "confirm" ? "{Enter}" : " ");
    await waitFor(() => expect(posts).toEqual([action]));
    expect(live.region.textContent).toBe(""); expect(control).toHaveProperty("disabled", true);
    expect(document.activeElement).toBe(control); await finish();
    const message = action === "confirm" ? "Confirmed." : "Retracted.";
    await waitFor(() => expect(live.region.textContent).toBe(message));
    expect(live.messages).toEqual([message]); expect(document.activeElement).toBe(control);
    expect(control.className).toContain("focus-visible:"); expect(control).toHaveProperty("disabled", false);
    expect(screen.queryAllByRole("alert")).toHaveLength(0); live.stop();
  });
  it.each(["confirm", "retract"] as const)("rolls back %s before a single visible failure, then retries the same action", async (action) => {
    outcome = 503; const client = await route();
    const original = client.getQueryData(["memory-fact", id]); const list = client.getQueryData(["memory-facts", "active"]);
    const live = announcements(); activate(action);
    await waitFor(() => expect(posts).toEqual([action]));
    expect(client.getQueryData(["memory-fact", id])).not.toEqual(original);
    expect(client.getQueryData(["memory-facts", "active"])).not.toEqual(list);
    expect(live.region.textContent).toBe(""); await finish();
    const alert = await screen.findByRole("alert", { name: "Fact action outcome" });
    expect(alert.textContent).toContain(`Could not ${action} this fact`); expect(alert.textContent).toContain("Memory source unavailable");
    expect(client.getQueryData(["memory-fact", id])).toEqual(original);
    expect(client.getQueryData(["memory-facts", "active"])).toEqual(list); expect(live.messages).toEqual([alert.textContent]);
    const retry = screen.getByRole("button", { name: `Retry ${action}` }); retry.focus(); outcome = 200;
    release = undefined; await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(posts).toEqual([action, action]));
    expect(document.activeElement).toBe(button(action));
    expect(screen.queryByRole("alert")).toBeNull(); expect(live.region.textContent).toBe(""); await finish();
    const status = await screen.findByRole("status", { name: "Fact action outcome" });
    await waitFor(() => expect(status.textContent).toBe(action === "confirm" ? "Confirmed." : "Retracted."));
    expect(live.messages).toHaveLength(2); expect(screen.queryByRole("button", { name: `Retry ${action}` })).toBeNull(); live.stop();
    expect(document.activeElement).toBe(button(action));
  });
  it("refuses same-tick repeated or competing activations before the disabled render", async () => {
    await route();
    act(() => { button("confirm").click(); button("confirm").click(); button("retract").click(); button("retract").click(); });
    await waitFor(() => expect(posts).toEqual(["confirm"])); await finish();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Confirmed.")); expect(posts).toEqual(["confirm"]);
  });
  it("clears a previous outcome for the next action without an optimistic success announcement", async () => {
    await route(); activate("confirm"); await finish();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Confirmed."));
    release = undefined; activate("retract"); await waitFor(() => expect(posts).toEqual(["confirm", "retract"]));
    expect(screen.getByRole("status").textContent).toBe(""); await finish();
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Retracted."));
  });
  it.each([
    { linkName: "Another fact", response: 200 },
    { linkName: "Leave memory", response: 503 },
  ])("does not announce a late outcome after navigation via $linkName", async ({ linkName, response }) => {
    outcome = response; await route(); activate("confirm"); await waitFor(() => expect(posts).toEqual(["confirm"]));
    const next = screen.getByRole("link", { name: linkName }); next.focus(); fireEvent.click(next);
    await screen.findByRole("heading", { name: linkName === "Another fact" ? "Another fact" : "Elsewhere" });
    await finish(); await waitFor(() => expect(clients[0].isMutating()).toBe(0));
    expect(screen.queryByRole("alert")).toBeNull(); expect(screen.queryByRole("status")?.textContent ?? "").toBe("");
    expect(document.activeElement).toBe(next);
  });
  it("expires the existing retract arm before allowing a later commit", async () => {
    await route(); vi.useFakeTimers(); fireEvent.click(button("retract"));
    expect(button("retract").textContent).toBe("Retract (confirm?)"); act(() => { vi.advanceTimersByTime(5000); });
    expect(button("retract").textContent).toBe("Retract"); fireEvent.click(button("retract"));
    expect(button("retract").textContent).toBe("Retract (confirm?)"); expect(posts).toEqual([]);
  });
});
