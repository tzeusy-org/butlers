// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import GoalsPage from "./GoalsPage";
import GoalPage from "./GoalPage";
import OverviewPage from "./OverviewPage";
import RegionsPage, { RegionPage } from "./RegionsPage";
import ApprovalsPage from "./ApprovalsPage";
import { D19Layout } from "./shared";
import fixture from "./__fixtures__/ledger.json";
import type { GoalDetail } from "@/api/goals";

vi.mock("@/components/finance/ButlerFinanceFinancesTab", () => ({ default: () => <p>Existing finance workspace</p> }));

let goal: GoalDetail;
let responses: Record<string, unknown>;
let failures: Record<string, number>;
let requests: string[];
let client: QueryClient;
beforeEach(() => {
  goal = structuredClone(fixture.goal);
  requests = [];
  failures = {};
  responses = {
    "/api/goals": structuredClone(fixture.page),
    [`/api/goals/${goal.id}`]: { data: goal },
    "/api/goals/regions": { data: structuredClone(fixture.regions) },
    "/api/butlers": { data: [fixture.butler, { ...fixture.butler, name: "switchboard", type: "staffer" }] },
    "/api/butlers/health": { data: fixture.butler },
    "/api/butlers/health/modules": { data: [{ name: "health", enabled: true, status: "connected", error: "PRIVATE RAW ERROR" }] },
    "/api/approvals": { data: [{ id: fixture.approval.id, tool_name: "email_send_message", status: "pending" }], meta: { total: 1, limit: 25, offset: 0, has_more: false } },
    [`/api/approvals/${fixture.approval.id}`]: { data: structuredClone(fixture.approval) },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input); requests.push(`${init?.method || "GET"} ${url}`);
    const path = new URL(url, "http://localhost").pathname;
    const status = failures[path] || (path in responses ? 200 : 503);
    return new Response(JSON.stringify(status === 200 ? responses[path] : { detail: status === 404 ? "Goal not found" : "Source unavailable" }), { status });
  }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
});
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); });

function open(path: string) {
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/d19" element={<D19Layout />}>
      <Route index element={<OverviewPage />} />
      <Route path="goals" element={<GoalsPage />} /><Route path="goals/:goalId" element={<GoalPage />} />
      <Route path="regions" element={<RegionsPage />} /><Route path="regions/:region" element={<RegionPage />} />
      <Route path="approvals" element={<ApprovalsPage />} /><Route path="approvals/:id" element={<ApprovalsPage />} />
    </Route>
  </Routes></MemoryRouter></QueryClientProvider>);
}

describe("D19 ledger pages (REQ-dashboard-goals-001 through 008)", () => {
  it("renders actual goal identity, origin, exposure and regions", async () => {
    open("/d19/goals");
    expect(await screen.findByText(goal.id)).toBeTruthy();
    expect(screen.getByText("owner · exposed")).toBeTruthy();
    expect(screen.getByText("health")).toBeTruthy();
  });
  it("sends URL filters and clears the cursor when a filter changes", async () => {
    open("/d19/goals?state=running&cursor=opaque");
    await screen.findByText(goal.id);
    expect(requests.some((r) => r.includes("state=running") && r.includes("cursor=opaque"))).toBe(true);
    fireEvent.change(screen.getByLabelText("origin"), { target: { value: "third_party" } });
    await waitFor(() => expect(requests.at(-1)).toContain("origin=third_party"));
    expect(requests.at(-1)).not.toContain("cursor=");
  });
  it("follows the opaque server cursor", async () => {
    responses["/api/goals"] = { ...fixture.page, next_cursor: "opaque+/=" };
    open("/d19/goals");
    fireEvent.click(await screen.findByText("Older goals"));
    await waitFor(() => expect(requests.some((r) => r.includes("cursor=opaque%2B%2F%3D"))).toBe(true));
  });
  it("distinguishes a confirmed empty page from a failed source", async () => {
    responses["/api/goals"] = { ...fixture.page, items: [] };
    open("/d19/goals");
    expect(await screen.findByText("No goals match these filters.")).toBeTruthy();
  });
  it("does not call a degraded empty ledger empty", async () => {
    responses["/api/goals"] = { ...fixture.page, items: [], sources_degraded: ["goal_ledger"] };
    open("/d19/goals");
    expect(await screen.findByText(/Partial coverage/)).toBeTruthy();
    expect(screen.queryByText("No goals match these filters.")).toBeNull();
  });
  it("offers read retry on an unavailable ledger", async () => {
    failures["/api/goals"] = 503; open("/d19/goals");
    expect(await screen.findByRole("alert")).toBeTruthy();
    delete failures["/api/goals"];
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText(goal.id)).toBeTruthy();
  });
  it.each([404, 503])("keeps detail HTTP %s distinct and never invents a goal", async (status) => {
    failures[`/api/goals/${goal.id}`] = status; open(`/d19/goals/${goal.id}`);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining(status === 404 ? "Goal not found" : "Source unavailable"));
    expect(screen.queryByText("Invocation tree")).toBeNull();
  });
  it("retains current and admission exposure with its recorded explanation", async () => {
    open(`/d19/goals/${goal.id}`);
    expect((await screen.findAllByText("Exposed")).length).toBeGreaterThan(0);
    expect(screen.getByText("Unexposed at admission")).toBeTruthy();
    expect(screen.getAllByText("third_party_read").length).toBeGreaterThan(0);
  });
  it("renders lead and child receipts under the same goal", async () => {
    goal.invocations.push({ ...goal.invocations[0], id: "child", parent_invocation_id: goal.invocations[0].id, role: "child", depth: 1, status: "completed" });
    open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText(/child · depth 1 · completed/)).toBeTruthy();
    expect(screen.getByText(/lead · depth 0/)).toBeTruthy();
  });
  it("labels missing exposure explanation rather than inferring it", async () => {
    goal.exposure_causes = []; open(`/d19/goals/${goal.id}`);
    expect((await screen.findAllByText("Explanation unavailable")).length).toBeGreaterThan(0);
  });
  it("unknown effects have no retry or commitment claim", async () => {
    open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText(/Unknown outcome/)).toBeTruthy();
    expect(screen.getByText("No commitment receipt")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });
  it("approved effects remain awaiting dispatch", async () => {
    goal.effects[0].status = "approved"; open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText("Approved, awaiting dispatch. Approval is not execution.")).toBeTruthy();
  });
  it("renders stored finite budgets and deadline without a reset", async () => {
    open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText("2 / 0 / 10")).toBeTruthy();
    expect(screen.getByText("2026-11-24T00:00:00Z")).toBeTruthy();
  });
  it("does not turn missing budget into zero or unlimited", async () => {
    goal.budget = null; open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText(/Budget unavailable. No budget receipt/)).toBeTruthy();
    expect(screen.queryByText("2 / 0 / 10")).toBeNull();
  });
  it("links only stored caused-by and approval identities", async () => {
    goal.caused_by_goal = "parent-goal"; open(`/d19/goals/${goal.id}`);
    expect((await screen.findByText("parent-goal")).closest("a")?.getAttribute("href")).toBe("/d19/goals/parent-goal");
    expect(screen.getByText(goal.effects[0].approval_ref!).closest("a")?.getAttribute("href")).toBe(`/d19/approvals/${fixture.approval.id}`);
  });
  it("never offers a nonfunctional Stop or research action", async () => {
    open(`/d19/goals/${goal.id}`);
    expect(await screen.findByText(/Stop unavailable/)).toBeTruthy();
    expect(screen.getByText(/Research-call ledger unavailable/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /stop|research|continue/i })).toBeNull();
    expect(requests.every((r) => r.startsWith("GET "))).toBe(true);
  });
});

describe("D19 overview and region seams", () => {
  it("does not derive completed-today from created-at rows or invocation counts", async () => {
    open("/d19"); await screen.findByText("Completed goals today");
    expect(screen.getByText("Unavailable")).toBeTruthy();
    expect(screen.getByText(/A daily total cannot be established/)).toBeTruthy();
  });
  it("shows active rows and due obligations with independent source coverage", async () => {
    responses["/api/goals/regions"] = { data: { ...fixture.regions, sources_degraded: ["obligations:travel"] } };
    open("/d19"); expect(await screen.findByText(/Daily check-in/)).toBeTruthy();
    expect(screen.getByText(/Unavailable: obligations:travel/)).toBeTruthy();
    expect(screen.getByText(/not a complete active-goal count/)).toBeTruthy();
  });
  it("global Hold status remains unavailable on each destination", async () => {
    open("/d19/regions");
    expect(screen.getByLabelText("Global Hold status").textContent).toContain("counts unavailable");
    expect(await screen.findByText("Health records and the obligations that keep them current.")).toBeTruthy();
    expect(screen.queryByText("switchboard")).toBeNull();
  });
  it("operator is URL-addressable, content-blind and read-only", async () => {
    open("/d19/regions/health?view=operator");
    expect(await screen.findByText("health-records")).toBeTruthy();
    expect(await screen.findByText(/health · connected/)).toBeTruthy();
    expect(screen.queryByText("PRIVATE RAW ERROR")).toBeNull();
    expect(screen.queryByRole("button", { name: /run|edit|enable|pause|grant/i })).toBeNull();
    expect(screen.queryByText("Health records", { exact: true })).toBeNull();
    expect(requests.every((r) => r.startsWith("GET "))).toBe(true);
  });
  it("resident region links the same goal and existing domain workspace", async () => {
    open("/d19/regions/health");
    expect(await screen.findByText(goal.id)).toBeTruthy();
    expect((await screen.findByText("Health records")).getAttribute("href")).toBe("/health");
    expect(screen.queryByText(/Sessions tab|Trigger input/)).toBeNull();
  });
  it("finance composes its existing domain component", async () => {
    responses["/api/butlers/finance"] = { data: { ...fixture.butler, name: "finance" } };
    open("/d19/regions/finance");
    expect(await screen.findByText("Existing finance workspace")).toBeTruthy();
  });
});

describe("D19 approval lineage", () => {
  it("cancelled dossier retains recipients, digest and lineage but no decision door", async () => {
    open(`/d19/approvals/${fixture.approval.id}`);
    expect(await screen.findByText("recipient@example.test")).toBeTruthy();
    expect(screen.getByText("synthetic-call-digest")).toBeTruthy();
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(screen.getByText(`Producing goal ${goal.id}`)).toBeTruthy();
    expect(screen.queryByText("Review this exact call in Approvals")).toBeNull();
  });
  it("eligible preview opens the existing exact-call review, without submitting", async () => {
    responses[`/api/approvals/${fixture.approval.id}`] = { data: { ...fixture.approval, status: "pending" } };
    open(`/d19/approvals/${fixture.approval.id}`);
    expect((await screen.findByText("Review this exact call in Approvals")).getAttribute("href")).toBe(`/approvals/${fixture.approval.id}`);
    expect(requests.every((r) => r.startsWith("GET "))).toBe(true);
  });
});
