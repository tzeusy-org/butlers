// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { getShellCapability } from "@/lib/shell-capability";

const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe("memory detail failures through the actual route and API client", () => {
  it.each([
    ["episodes", "episodeId", "daybook"],
    ["facts", "factId", "ledger"],
    ["rules", "ruleId", "books"],
  ])("keeps %s absence distinct from source and transport failures", async (kind, param, absent) => {
    let status: number | "network" = 404;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname !== `/api/memory/${kind}/record-a`) {
        return new Response(JSON.stringify({ data: [] }), { status: 200 });
      }
      if (status === "network") throw new TypeError("Connection interrupted");
      if (status === 200) return new Response(JSON.stringify({ data: {
        id: "record-a", content: "Cached record", subject: "Record", predicate: "is",
        metadata: {}, tags: [], importance: 1, reference_count: 0, butler: "general", session_id: null,
        consolidation_status: "pending", consolidated: false, consolidation_attempts: 0,
        created_at: "2026-01-01T10:00:00Z", last_referenced_at: null, expires_at: null,
        confidence: 0.9, decay_rate: 0, permanence: "standard", validity: "active", scope: "global",
        maturity: "new", applied_count: 0, helpful_count: 0, harmful_count: 0, effectiveness_score: 0,
      } }), { status });
      return new Response(JSON.stringify({ detail: status === 503
        ? "Memory detail unavailable: source general is unreachable"
        : "Record not found" }), { status });
    });
    vi.stubGlobal("fetch", fetcher);
    const { default: Page } = await getShellCapability(`/memory/${kind}/:${param}`)!.loader();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/memory/${kind}/record-a`]}>
      <Routes><Route path={`/memory/${kind}/:${param}`} element={<Page />} /></Routes>
    </MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText(new RegExp(`not (?:in|on) the ${absent}`))).toBeTruthy();

    status = 200;
    await act(async () => { await client.refetchQueries({ type: "active" }); });
    expect(await screen.findByRole("heading", { name: "Cached record" })).toBeTruthy();

    status = 503;
    await act(async () => { await client.refetchQueries({ type: "active" }); });
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("source general"));
    expect(screen.queryByText(new RegExp(`not (?:in|on) the ${absent}`))).toBeNull();

    status = "network";
    const before = fetcher.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(before));
    expect(await screen.findByText("Connection interrupted")).toBeTruthy();
    expect(screen.queryByText(new RegExp(`not (?:in|on) the ${absent}`))).toBeNull();
    status = 404;
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(new RegExp(`not (?:in|on) the ${absent}`))).toBeTruthy();
  });
});
