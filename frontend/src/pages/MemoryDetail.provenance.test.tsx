// @vitest-environment jsdom
// REQ-module-memory-013: attribution displays persisted authority, never request metadata.
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { getShellCapability } from "@/lib/shell-capability";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: "record-a", content: "Remember the source", subject: "A subject", predicate: "is",
    metadata: {}, tags: [], importance: 1, reference_count: 0, source_butler: "general",
    session_id: null, source_episode_id: null, created_at: "2026-01-01T10:00:00Z",
    last_referenced_at: null, confidence: 0.9, decay_rate: 0, permanence: "standard",
    validity: "active", scope: "global", maturity: "candidate", applied_count: 0,
    success_count: 0, harmful_count: 0, effectiveness_score: 0, retired_at: null,
    request_id: "owner-scheduled-from-imaginary-device", ...overrides,
  };
}

async function mount(kind: "facts" | "rules", data: Record<string, unknown>) {
  const requests: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    requests.push(url);
    return new Response(JSON.stringify(url.pathname === `/api/memory/${kind}/record-a`
      ? { data } : url.pathname === "/api/memory/stats" ? { data: null } : { data: [], meta: { total: 0, offset: 0, limit: 50 } }), { status: 200 });
  }));
  const param = kind === "facts" ? "factId" : "ruleId";
  const { default: Detail } = await getShellCapability(`/memory/${kind}/:${param}`)!.loader();
  const { default: Browser } = await getShellCapability("/memory")!.loader();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/memory/${kind}/record-a`]}>
    <Routes><Route path={`/memory/${kind}/:${param}`} element={<Detail />} />
      <Route path="/memory" element={<Browser />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await screen.findByRole("heading", { name: "Remember the source" });
  return requests;
}

it.each(["facts", "rules"] as const)("renders the actual %s route from authority stamps without inferring a device or schedule", async (kind) => {
  const populations = [
    ["owner", "You told me"], ["owner_device", "You told me from an owner device"],
    ["system", "Recorded by the system"], ["mixed", "Combined from several sources"],
    [null, "Unverified source"], ["third_party", "Someone else told me (unverified sender)"],
  ] as const;
  for (const [authority, text] of populations) {
      await mount(kind, record({ content_authority: authority }));
      const line = screen.getByLabelText("Source attribution");
      expect(line.textContent).toBe(text);
      expect(line.textContent).not.toMatch(/scheduled|imaginary|legacy|iPhone/i);
      cleanup();
  }
});

it("links the resolved author rather than the fact subject, and keeps source availability independent", async () => {
  await mount("facts", record({
    content_authority: "third_party", authority_entity_id: "author-a", authority_entity_name: "A correspondent",
    entity_id: "subject-b", entity_name: "A subject", source_episode_id: "episode-a",
    source_episode_status: "available",
  }));
  const author = within(screen.getByLabelText("Source attribution")).getByRole("link", { name: "A correspondent" });
  expect(author.getAttribute("href")).toBe("/entities/author-a");
  expect(screen.getByRole("link", { name: /derived from episode/ }).getAttribute("href"))
    .toBe("/memory/episodes/episode-a");
  cleanup();
  for (const status of ["expired", "unresolved"]) {
    await mount("facts", record({ content_authority: null, source_episode_id: "episode-a", source_episode_status: status }));
    expect(screen.getByText(`Source ${status}`)).toBeTruthy();
    expect(document.querySelector('a[href="/memory/episodes/episode-a"]')).toBeNull();
    cleanup();
  }
});

it("uses recorded endorsement identity and date, including an unresolved identity without claiming You", async () => {
  await mount("rules", record({ content_authority: "third_party", endorsed_at: "2026-10-20T14:00:00Z",
    endorsed_by: "endorser-a", endorsed_by_name: "The owner", held: false }));
  const line = screen.getByLabelText("Source attribution");
  expect(within(line).getByRole("link", { name: "The owner" }).getAttribute("href")).toBe("/entities/endorser-a");
  expect(line.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-20T14:00:00Z");
  expect(screen.queryByRole("link", { name: "Guidance waiting for you" })).toBeNull();
  cleanup();
  await mount("rules", record({ content_authority: null, endorsed_at: "2026-10-20T14:00:00Z", endorsed_by: null }));
  expect(screen.getByLabelText("Source attribution").textContent).toContain("Endorsement recorded");
  expect(screen.getByLabelText("Source attribution").textContent).not.toContain("You endorsed");
});

it("takes a held rule through the real browser, URL state, query and API client with an escapable filter", async () => {
  const requests = await mount("rules", record({ content_authority: "third_party", held: true }));
  fireEvent.click(screen.getByRole("link", { name: "Guidance waiting for you" }));
  await waitFor(() => expect(requests.some((url) => url.pathname === "/api/memory/rules" && url.searchParams.get("held") === "true")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "proven" }));
  await waitFor(() => expect(requests.some((url) => url.searchParams.get("held") === "true" && url.searchParams.get("maturity") === "proven")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Show all guidance" }));
  await waitFor(() => expect(requests.some((url) => url.pathname === "/api/memory/rules"
    && url.searchParams.get("maturity") === "proven" && !url.searchParams.has("held"))).toBe(true));
});

it("withholds the waiting destination for terminal and endorsed rules even with a stale held flag", async () => {
  for (const terminal of [{ retired_at: "2026-10-20" }, { metadata: { forgotten: true } }, { endorsed_at: "2026-10-20" }]) {
    await mount("rules", record({ content_authority: "third_party", held: true, ...terminal }));
    expect(screen.queryByRole("link", { name: "Guidance waiting for you" })).toBeNull();
    cleanup();
  }
});
