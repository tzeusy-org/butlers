// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { getShellCapability } from "@/lib/shell-capability";
import { clearOwnerSession } from "@/api/owner-session";
import type { Episode } from "@/api/types";

const id = "00000000-0000-4000-8000-000000000001";
const nextId = "00000000-0000-4000-8000-000000000002";
const clients: QueryClient[] = [];
let episode: Episode;
let posts: number;
let reads: number;
let outcome: number;
let release: (() => void) | undefined;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  posts = 0; reads = 0; outcome = 200; release = undefined;
  episode = {
tenant_id: "owner",
request_id: null,
retention_class: "transient",
sensitivity: "normal",
    id, butler: "general", session_id: null, content: "Recovery dossier", importance: 5,
    reference_count: 0, consolidated: false, consolidation_status: "dead_letter",
    created_at: "2026-01-01T10:00:00Z", last_referenced_at: null, expires_at: null, metadata: {},
    consolidation_attempts: 0, last_consolidation_error: "Write-up unavailable",
    dead_letter_reason: "Attempt limit reached", next_consolidation_retry_at: "2026-01-02T10:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/auth/owner/csrf")) return json({ csrf_token: "test-csrf", csrf_expires_at: "2099-01-01T00:00:00Z" });
    if (options?.method === "POST") {
      expect(url.pathname).toBe(`/api/memory/episodes/${id}/requeue`);
      posts += 1;
      if (release) await new Promise<void>((resolve) => { release = resolve; });
      if (outcome === 0) throw new TypeError("Connection interrupted");
      if (outcome === 409) {
        episode = { ...episode, consolidation_status: "pending" };
        return json({ detail: "Episode is no longer dead-lettered" }, 409);
      }
      if (outcome !== 200) return json({ detail: "Recovery source general unavailable" }, outcome);
      episode = { ...episode, consolidation_status: "pending", consolidation_attempts: 0,
        last_consolidation_error: null, dead_letter_reason: null, next_consolidation_retry_at: null };
      return json({ data: episode, meta: { queued: true, consolidation_started: false } });
    }
    if (url.pathname.startsWith("/api/memory/episodes/")) {
      reads += 1;
      return json({ data: url.pathname.endsWith(nextId) ? { ...episode, id: nextId, content: "Another dossier" } : episode });
    }
    if (url.pathname === "/api/memory/facts") return json({ data: [], meta: { total: 0 } });
    throw new Error(`Unexpected endpoint: ${url.pathname}`);
  }));
});

afterEach(() => {
  cleanup(); clients.splice(0).forEach((client) => client.clear());
  clearOwnerSession(); vi.unstubAllGlobals();
});

async function route() {
  const { default: Page } = await getShellCapability("/memory/episodes/:episodeId")!.loader();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/memory/episodes/${id}`]}>
    <Link to={`/memory/episodes/${nextId}`}>Another episode</Link>
    <Routes><Route path="/memory/episodes/:episodeId" element={<Page />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await screen.findByRole("heading", { name: "Recovery dossier" });
  return client;
}

describe("episode dossier and bounded owner recovery", () => {
  it("shows only available public lifecycle evidence, including zero attempts", async () => {
    const client = await route();
    expect(screen.getByText("attempts").nextElementSibling?.textContent).toBe("0");
    expect(screen.getByText("Write-up unavailable")).toBeTruthy();
    expect(screen.getByText("Attempt limit reached")).toBeTruthy();
    expect(screen.getByText("next retry")).toBeTruthy();
    expect(screen.getByText("2026-01-02 18:00 (Asia/Singapore)")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/lease holder|lease deadline/i);
    episode = { ...episode, last_consolidation_error: null, dead_letter_reason: null, next_consolidation_retry_at: null };
    await act(async () => { await client.refetchQueries({ type: "active" }); });
    await waitFor(() => expect(screen.queryByText("Write-up unavailable")).toBeNull());
    expect(screen.queryByText("next retry")).toBeNull();
  });

  it("offers recovery only for a dead letter, leaving all automatic states unavailable", async () => {
    const client = await route();
    expect(screen.getByRole("button", { name: "Queue for next write-up" })).toBeTruthy();
    for (const status of ["pending", "failed", "consolidated"]) {
      episode = { ...episode, consolidation_status: status };
      await act(async () => { await client.refetchQueries({ type: "active" }); });
      await waitFor(() => expect(screen.queryByRole("button", { name: "Queue for next write-up" })).toBeNull());
    }
    expect(posts).toBe(0);
  });

  it("keeps focus and refuses rapid duplicates, then announces queued without running once", async () => {
    release = () => {};
    await route();
    const button = screen.getByRole("button", { name: "Queue for next write-up" });
    expect(button.className).toContain("focus-visible:");
    button.focus();
    await userEvent.keyboard(" ");
    await waitFor(() => expect(posts).toBe(1));
    expect(button).toHaveProperty("disabled", true);
    expect(document.activeElement).toBe(button);
    fireEvent.click(button); fireEvent.click(button);
    expect(posts).toBe(1);
    await act(async () => { release!(); });
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status.textContent).toContain("Queued for the next scheduled write-up"));
    expect(status.textContent).toContain("No write-up started");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(document.querySelectorAll('[aria-live="polite"]')).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Queue for next write-up" })).toBeNull());
    expect(reads).toBeGreaterThan(1);
    expect(status.textContent).toContain("Queued");
    expect(posts).toBe(1);
  });

  it("announces conflict and refreshes the actual state without claiming queued", async () => {
    outcome = 409;
    await route();
    const button = screen.getByRole("button", { name: "Queue for next write-up" });
    button.focus(); await userEvent.keyboard("{Enter}");
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status.textContent).toContain("no longer dead-lettered"));
    expect(status.textContent).not.toContain("Queued");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Queue for next write-up" })).toBeNull());
    expect(reads).toBeGreaterThan(1);
  });

  it("keeps a failed recovery visible and allows one deliberate retry", async () => {
    outcome = 503;
    await route();
    fireEvent.click(screen.getByRole("button", { name: "Queue for next write-up" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("source general"));
    expect(screen.getByRole("status").textContent).not.toContain("Queued");
    const button = screen.getByRole("button", { name: "Queue for next write-up" });
    expect(button).toHaveProperty("disabled", false);
    outcome = 0;
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Connection interrupted"));
    expect(screen.getByRole("status").textContent).not.toContain("Queued");
    outcome = 200;
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Queued"));
    expect(posts).toBe(3);
  });

  it("does not carry a late episode outcome or focus into another dossier", async () => {
    outcome = 503;
    release = () => {};
    await route();
    fireEvent.click(screen.getByRole("button", { name: "Queue for next write-up" }));
    await waitFor(() => expect(posts).toBe(1));
    const next = screen.getByRole("link", { name: "Another episode" });
    next.focus(); fireEvent.click(next);
    await screen.findByRole("heading", { name: "Another dossier" });
    await act(async () => { release!(); });
    expect(screen.getByRole("status").textContent).toBe("");
    expect(document.activeElement).toBe(next);
  });
});
