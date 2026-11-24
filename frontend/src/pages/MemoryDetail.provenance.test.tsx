// @vitest-environment jsdom
// REQ-module-memory-013: attribution displays persisted authority, never request metadata.
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { CommandRegistryProvider, useCommandMenuActions } from "@/lib/command-registry";
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

const legacyA = record({
  id: "legacy-a", held: true, content: "Remember the north gate",
  content_authority: null, owning_source: "general",
});
const legacyB = record({
  id: "legacy-b", held: true, content: "Keep the garden schedule",
  content_authority: null, owning_source: "general",
});
const correspondent = record({
  id: "remote", held: true, content: "Consider the correspondent's suggestion",
  content_authority: "third_party", owning_source: "general",
  authority_entity_id: "correspondent", authority_entity_name: "A correspondent",
  source_episode_id: "episode-a", source_episode_status: "available",
});
const confirmationDigest = "0".repeat(64);

function EndorseFromCommandMenu() {
  const command = useCommandMenuActions().find((item) => item.id === "memory-endorse-remote");
  return command
    ? <button type="button" onClick={command.perform}>Choose Endorse in command menu</button>
    : null;
}

async function mountHeld(
  fixtures: ReturnType<typeof record>[],
  handleConfirm: (body: unknown) => Promise<Response>,
  poolsFailed: string[] = [],
) {
  const writes: Array<{ path: string; body: unknown }> = [];
  const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) : null;
      writes.push({ path: url.pathname, body });
      if (url.pathname.endsWith("/preview")) {
        const chosen = (body as { rule_ids: string[] }).rule_ids.map((id) => {
          const rule = fixtures.find((item) => item.id === id)!;
          return {
            rule_id: id, content: rule.content, content_authority: null,
            scope: rule.scope, source_butler: rule.source_butler,
            source_episode_id: rule.source_episode_id, created_at: rule.created_at,
          };
        });
        return json({ data: { rules: chosen, confirmation_digest: confirmationDigest } });
      }
      if (url.pathname.endsWith("/remote/endorse")) {
        return json({ data: { rule_id: "remote", changed: true, endorsed_at: "2026-10-20T14:00Z" } });
      }
      return handleConfirm(body);
    }
    if (url.pathname === "/api/memory/rules") {
      return json({
        data: fixtures,
        meta: { total: fixtures.length, offset: 0, limit: 50, pools_failed: poolsFailed },
      });
    }
    return json(url.pathname === "/api/memory/stats"
      ? { data: null } : { data: [], meta: { total: 0, offset: 0, limit: 50 } });
  }));
  const { default: Browser } = await getShellCapability("/memory")!.loader();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/memory?register=rules&held=true"]}>
        <CommandRegistryProvider>
          <Browser />
          <EndorseFromCommandMenu />
        </CommandRegistryProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return writes;
}

it("reviews an exact legacy selection and sends committed receipts and palette endorsement through HTTP", async () => {
  let commit: (response: Response) => void = () => { throw new Error("No pending commit"); };
  const writes = await mountHeld(
    [legacyA, legacyB, correspondent, record({
      id: "legacy-c", content: "Keep a second calendar", content_authority: null,
      owning_source: "relationship", held: true,
    })],
    () => new Promise<Response>((resolve) => { commit = resolve; }),
  );
  expect(await screen.findByRole("link", { name: "A correspondent" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Source episode" }).getAttribute("href"))
    .toBe("/memory/episodes/episode-a");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Remember the north gate" }));
  expect((screen.getByRole("checkbox", { name: "Select Keep a second calendar" }) as HTMLInputElement).disabled)
    .toBe(true);
  expect(screen.queryByRole("checkbox", { name: /correspondent's suggestion/ })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Keep the garden schedule" }));
  fireEvent.click(screen.getByRole("button", { name: "Review 2 selected" }));
  const dialog = await screen.findByRole("alertdialog", { name: "Endorse selected legacy guidance" });
  expect(within(dialog).getByText("Remember the north gate")).toBeTruthy();
  expect(within(dialog).getByText("Keep the garden schedule")).toBeTruthy();
  expect(writes).toEqual([{
    path: "/api/memory/rules/endorse-legacy/preview",
    body: { rule_ids: ["legacy-a", "legacy-b"] },
  }]);
  const confirm = within(dialog).getByRole("button", { name: "Endorse guidance" });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1]).toEqual({
    path: "/api/memory/rules/endorse-legacy",
    body: { rule_ids: ["legacy-a", "legacy-b"], confirmation_digest: confirmationDigest },
  });
  expect((within(dialog).getByRole("button", { name: "Endorsing…" }) as HTMLButtonElement).disabled)
    .toBe(true);
  await act(async () => {
    commit(new Response(JSON.stringify({ data: [
      { rule_id: "legacy-a", changed: true, endorsed_at: "2026-10-20T14:00Z" },
      { rule_id: "legacy-b", changed: false, endorsed_at: "2026-10-20T13:00Z" },
    ] })));
  });
  expect(await screen.findByText("Endorsed · Remember the north gate")).toBeTruthy();
  expect(screen.getByText("Already endorsed · Keep the garden schedule")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Choose Endorse in command menu" }));
  const single = screen.getByRole("alertdialog", { name: "Endorse this guidance" });
  expect(within(single).getByText(correspondent.content)).toBeTruthy();
  expect(writes).toHaveLength(2);
  fireEvent.click(within(single).getByRole("button", { name: "Endorse guidance" }));
  await screen.findByText(`Endorsed · ${correspondent.content}`);
  expect(writes[2]).toEqual({ path: "/api/memory/rules/remote/endorse", body: null });
});

it("keeps a refused selection reviewable and treats a partial held-list result as incomplete", async () => {
  const writes = await mountHeld(
    [legacyA],
    async () => new Response(JSON.stringify({ detail: "The selection changed." }), { status: 409 }),
    ["relationship"],
  );
  expect(await screen.findByText(/relationship/)).toBeTruthy();
  expect(screen.queryByText("No guidance waiting for you.")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Remember the north gate" }));
  fireEvent.click(screen.getByRole("button", { name: "Review 1 selected" }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Endorse guidance" }));
  expect(await within(dialog).findByText(/The selection changed/)).toBeTruthy();
  expect(within(dialog).getByText("Remember the north gate")).toBeTruthy();
  expect(writes).toHaveLength(2);
  expect(screen.queryByText(/Endorsed ·/)).toBeNull();
});
