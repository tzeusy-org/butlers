// @vitest-environment jsdom
/** Dashboard-shell Error Boundary / A failed read never renders as empty. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { ReactNode } from "react";

import * as api from "@/api/index.ts";
import EntityFinder from "@/components/layout/EntityFinder";
import { GlobalActionsRegistrar } from "@/components/layout/GlobalActionsRegistrar";
import Sidebar from "@/components/layout/Sidebar";
import { dispatchOpenEntityFinder } from "@/lib/entity-finder";
import { CommandRegistryProvider } from "@/lib/command-registry";

vi.mock("@/api/index.ts", async (original) => ({
  ...await original<typeof import("@/api/index.ts")>(),
  getButlers: vi.fn(), getOwnerSetupStatus: vi.fn(), getEntityNeighbours: vi.fn(),
  searchRelationshipEntities: vi.fn(), searchAll: vi.fn(), getCostSummary: vi.fn(),
  triggerButler: vi.fn(),
}));
// These independent shell feeds are not the read identities under test.
vi.mock("@/hooks/use-qa-badge", () => ({ useBadgeCounts: () => ({}) }));
vi.mock("@/hooks/use-bus-aware-poll-interval", () => ({ useBusAwarePollInterval: () => 60_000 }));

const clients: QueryClient[] = [];
const entity = { entity_id: "entity-alice", canonical_name: "Alice", entity_type: "person", match_kind: "prefix", score: 100 };
const general = { name: "general", status: "ok", port: 9000, type: "butler", sessions_24h: 0 };
const searchEmpty = { data: { entities: [], contacts: [], butlers: [], sessions: [], state: [] }, meta: {} };

function mount(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: Infinity } } });
  clients.push(client);
  const view = render(<QueryClientProvider client={client}><MemoryRouter><CommandRegistryProvider>{children}</CommandRegistryProvider></MemoryRouter></QueryClientProvider>);
  return { ...view, client };
}

async function openFinder(query: string) {
  act(() => dispatchOpenEntityFinder());
  fireEvent.change(await screen.findByTestId("entity-finder-input"), { target: { value: query } });
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.getButlers).mockResolvedValue({ data: [general], meta: {} } as Awaited<ReturnType<typeof api.getButlers>>);
  vi.mocked(api.getOwnerSetupStatus).mockResolvedValue({ entity_id: null } as Awaited<ReturnType<typeof api.getOwnerSetupStatus>>);
  vi.mocked(api.searchRelationshipEntities).mockResolvedValue({ results: [], total: 0, q: "", limit: 12 });
  vi.mocked(api.searchAll).mockResolvedValue(searchEmpty as Awaited<ReturnType<typeof api.searchAll>>);
  vi.mocked(api.getEntityNeighbours).mockResolvedValue({ neighbours: {} } as Awaited<ReturnType<typeof api.getEntityNeighbours>>);
  vi.mocked(api.getCostSummary).mockResolvedValue({ data: { total_cost_usd: 12, source_error: false, unpriced_models: [] } } as unknown as Awaited<ReturnType<typeof api.getCostSummary>>);
});
afterEach(() => { cleanup(); for (const c of clients.splice(0)) c.clear(); });

it("keeps preview relation failure distinct from empty and retries the same relation read", async () => {
  vi.mocked(api.searchRelationshipEntities).mockResolvedValue({ results: [entity], total: 1 } as Awaited<ReturnType<typeof api.searchRelationshipEntities>>);
  vi.mocked(api.getEntityNeighbours).mockRejectedValue(new Error("private relation payload"));
  const { client } = mount(<EntityFinder />);
  expect(api.getEntityNeighbours).not.toHaveBeenCalled();
  await openFinder("Alice");
  const preview = await screen.findByTestId("entity-finder-preview");
  await waitFor(() => expect(within(preview).getByRole("alert").textContent).toMatch(/relations.*unavailable/i));
  expect(within(preview).queryByText("No relations.")).toBeNull();
  expect(preview.getAttribute("aria-hidden")).not.toBe("true");
  expect(screen.queryByText("private relation payload")).toBeNull();
  const count = vi.mocked(api.getEntityNeighbours).mock.calls.length;
  vi.mocked(api.getEntityNeighbours).mockResolvedValue({ neighbours: {} } as Awaited<ReturnType<typeof api.getEntityNeighbours>>);
  fireEvent.click(within(preview).getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(api.getEntityNeighbours).toHaveBeenCalledTimes(count + 1));
  await waitFor(() => expect(within(preview).getByText("No relations.")).not.toBeNull());
  expect(api.getEntityNeighbours).toHaveBeenLastCalledWith("entity-alice", { rank: "weight", per_predicate: 5 });
  vi.mocked(api.getEntityNeighbours).mockResolvedValue({ neighbours: { knows: [{ entity_id: "bob", canonical_name: "Bob", entity_type: "person", weight: 2 }] } } as unknown as Awaited<ReturnType<typeof api.getEntityNeighbours>>);
  await act(() => client.refetchQueries({ queryKey: ["entity-neighbours", "entity-alice"] }));
  await waitFor(() => expect(within(preview).getByText("Bob")).not.toBeNull());
  vi.mocked(api.getEntityNeighbours).mockRejectedValue(new Error("refetch failed"));
  await act(() => client.refetchQueries({ queryKey: ["entity-neighbours", "entity-alice"] }));
  await waitFor(() => expect(within(preview).getByRole("alert").textContent).toMatch(/stale/i));
  expect(within(preview).getByText("Bob")).not.toBeNull();
});

it("separates generic search transport errors, successful empty and partial payloads", async () => {
  vi.mocked(api.searchAll).mockRejectedValue(new Error("private search payload"));
  const { client } = mount(<EntityFinder />);
  await openFinder("zzznomatch");
  const note = await screen.findByTestId("entity-finder-generic-error");
  expect(note.textContent).toMatch(/sessions.*state.*unavailable/i);
  expect(screen.queryByText(/No results for/)).toBeNull();
  expect(screen.queryByText("private search payload")).toBeNull();
  const entityReads = vi.mocked(api.searchRelationshipEntities).mock.calls.length;
  let finish!: (value: Awaited<ReturnType<typeof api.searchAll>>) => void;
  vi.mocked(api.searchAll).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(within(note).getByRole("button", { name: "Retry" }));
  await screen.findByText("Searching sessions & state…");
  expect(screen.queryByText(/No results for/)).toBeNull();
  await act(async () => finish(searchEmpty as Awaited<ReturnType<typeof api.searchAll>>));
  await screen.findByText(/No results for/);
  expect(api.searchRelationshipEntities).toHaveBeenCalledTimes(entityReads);
  vi.mocked(api.searchAll).mockResolvedValue({ ...searchEmpty, data: { ...searchEmpty.data, sessions: [{ id: "session-1", title: "Cached session", url: "/sessions/session-1" }] }, meta: { sources_degraded: ["health"] } } as unknown as Awaited<ReturnType<typeof api.searchAll>>);
  await act(() => client.refetchQueries({ queryKey: ["search"] }));
  await screen.findByText("Cached session");
  expect(screen.getByTestId("entity-finder-generic-degraded").textContent).toMatch(/health/);
  vi.mocked(api.searchAll).mockRejectedValue(new Error("failed refetch"));
  await act(() => client.refetchQueries({ queryKey: ["search"] }));
  await waitFor(() => expect(screen.getByTestId("entity-finder-generic-error").textContent).toMatch(/stale/i));
  expect(screen.getByText("Cached session")).not.toBeNull();
  expect(screen.getByTestId("entity-finder-generic-degraded").textContent).toMatch(/health/);
});

it("registers a real discovery retry and labels cached actions stale without losing trigger behavior", async () => {
  vi.mocked(api.getButlers).mockRejectedValue(new Error("private discovery payload"));
  const { client } = mount(<><GlobalActionsRegistrar /><EntityFinder /></>);
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/butler.*unavailable/i));
  await openFinder("");
  const retry = await screen.findByRole("option", { name: /butler.*unavailable.*retry/i });
  expect(screen.queryByRole("option", { name: /Run general/ })).toBeNull();
  vi.mocked(api.getButlers).mockResolvedValue({ data: [general], meta: {} } as Awaited<ReturnType<typeof api.getButlers>>);
  fireEvent.click(retry);
  await waitFor(() => expect(client.getQueryState(["butlers"])?.status).toBe("success"));
  await openFinder("");
  await screen.findByRole("option", { name: /Run general/ });
  vi.mocked(api.getButlers).mockRejectedValue(new Error("failed refetch"));
  await act(() => client.refetchQueries({ queryKey: ["butlers"] }));
  const actions = within(screen.getByTestId("entity-finder-actions-group"));
  await actions.findByRole("option", { name: /retry.*discovery/i });
  const cached = actions.getByRole("option", { name: /Run general.*stale/i });
  vi.mocked(api.triggerButler).mockResolvedValue({ session_id: "s1", success: true, output: "" } as Awaited<ReturnType<typeof api.triggerButler>>);
  fireEvent.click(cached);
  await waitFor(() => expect(api.triggerButler).toHaveBeenCalledWith("general", "Run your scheduled tick now.", "workhorse"));
  expect(screen.queryByText("private discovery payload")).toBeNull();
});

it("never turns a spend transport failure into all-clear and preserves health and coverage", async () => {
  vi.mocked(api.getCostSummary).mockRejectedValue(new Error("private spend payload"));
  const { client } = mount(<Sidebar />);
  const note = await screen.findByRole("alert");
  expect(note.textContent).toMatch(/spend.*unavailable/i);
  expect(screen.queryByLabelText("All systems ok")).toBeNull();
  expect(screen.queryByText(/\$0\.00/)).toBeNull();
  let finish!: (value: Awaited<ReturnType<typeof api.getCostSummary>>) => void;
  vi.mocked(api.getCostSummary).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  fireEvent.click(within(note).getByRole("button", { name: "Retry" }));
  expect(screen.queryByLabelText("All systems ok")).toBeNull();
  expect(screen.queryByText(/\$0\.00/)).toBeNull();
  await act(async () => finish({ data: { total_cost_usd: 12, source_error: false, unpriced_models: [] } } as unknown as Awaited<ReturnType<typeof api.getCostSummary>>));
  await screen.findByLabelText("$12.00 today");
  vi.mocked(api.getCostSummary).mockRejectedValue(new Error("failed refetch"));
  await act(() => client.refetchQueries({ queryKey: ["cost-summary"] }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/stale/i));
  expect(screen.getByLabelText(/\$12\.00 today.*stale/i)).not.toBeNull();
  vi.mocked(api.getCostSummary).mockResolvedValue({ data: { total_cost_usd: 12, source_error: false, unpriced_models: [{ model: "unpriced-model" }] } } as unknown as Awaited<ReturnType<typeof api.getCostSummary>>);
  await act(() => client.refetchQueries({ queryKey: ["cost-summary"] }));
  await screen.findByLabelText(/Spend coverage incomplete: unpriced-model/);
  expect(screen.queryByLabelText(/\$12\.00 today/)).toBeNull();
  vi.mocked(api.getCostSummary).mockResolvedValue({ data: { total_cost_usd: 0, source_error: true, unpriced_models: [] } } as unknown as Awaited<ReturnType<typeof api.getCostSummary>>);
  vi.mocked(api.getButlers).mockResolvedValue({ data: [{ ...general, status: "degraded" }], meta: {} } as Awaited<ReturnType<typeof api.getButlers>>);
  await act(async () => { await client.refetchQueries({ queryKey: ["cost-summary"] }); await client.refetchQueries({ queryKey: ["butlers"] }); });
  await screen.findByLabelText("1 degraded · Spend source unavailable");
  expect(screen.queryByText(/\$0\.00/)).toBeNull();
});
