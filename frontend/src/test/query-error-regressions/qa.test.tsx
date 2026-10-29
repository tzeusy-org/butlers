// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import Sidebar from "@/components/layout/Sidebar";
import { OwnerGate } from "@/components/auth/OwnerGate";

// Only unrelated peripheral sources are replaced. Roster, QA summary, the badge
// registry, owner session and Sidebar all execute their actual production path.
vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: () => ({ data: { data: { total_cost_usd: 1 } }, isLoading: false }),
}));
vi.mock("@/hooks/use-approvals", async (original) => ({
  ...await original<typeof import("@/hooks/use-approvals")>(),
  useApprovalMetrics: () => ({ data: { data: { total_pending: 0 }, meta: {} } }),
}));
vi.mock("@/hooks/use-nav-intent", () => ({
  useNavIntent: () => ({ onActivate() {}, onPointerEnter() {}, onPointerLeave() {}, onFocus() {}, onBlur() {} }),
}));

const roster = (names = ["qa"]) => ({ data: names.map(name => ({ name, status: "ok" })), meta: {} });
const summary = (count = 7) => ({ data: { active_breakdown: { escalated_open_cases: count } }, meta: {} });
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
let rosterStatus: number;
let summaryStatus: number;
let rosterNames: string[];
let count: number;
let authenticated: boolean;
let rosterWait: Promise<void> | undefined;
let calls: { roster: number; summary: number };
const clients: QueryClient[] = [];

beforeEach(() => {
  rosterStatus = summaryStatus = 200;
  rosterNames = ["qa"];
  count = 7;
  authenticated = true;
  rosterWait = undefined;
  calls = { roster: 0, summary: 0 };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), location.href).pathname;
    if (path.endsWith("/auth/owner/status")) return response({ data: {
      state: "keyless_enrolled", authenticated,
      session_expires_at: authenticated ? new Date(Date.now() + 300_000).toISOString() : null,
    } });
    if (path.endsWith("/auth/owner/csrf")) return response({ data: {
      csrf_token: "synthetic-qa-boundary-csrf",
      csrf_expires_at: new Date(Date.now() + 300_000).toISOString(),
      session_expires_at: new Date(Date.now() + 300_000).toISOString(),
    } });
    if (path.endsWith("/butlers")) {
      calls.roster++;
      await rosterWait;
      return response(rosterStatus === 200 ? roster(rosterNames) : { message: "diagnostic-detail" }, rosterStatus);
    }
    if (path.endsWith("/qa/summary")) {
      calls.summary++;
      return response(summaryStatus === 200 ? summary(count) : { message: "diagnostic-detail" }, summaryStatus);
    }
    throw new Error("Unexpected consumer request");
  }));
});
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.unstubAllGlobals();
});

function mount(variant: "expanded" | "rail" | "mobile" = "expanded", options?: {
  rosterCache?: string[]; summaryCache?: number; gate?: boolean;
}) {
  const client = new QueryClient({ defaultOptions: { queries: {
    retry: false, gcTime: Infinity, refetchOnWindowFocus: false,
  } } });
  clients.push(client);
  if (options?.rosterCache) client.setQueryData(["butlers"], roster(options.rosterCache));
  if (options?.summaryCache !== undefined) client.setQueryData(["qa-summary"], summary(options.summaryCache));
  const sidebar = <Sidebar collapsed={variant === "rail"} mobileExpanded={variant === "mobile"} />;
  render(<QueryClientProvider client={client}><MemoryRouter>
    {options?.gate ? <OwnerGate>{sidebar}</OwnerGate> : sidebar}
  </MemoryRouter></QueryClientProvider>);
  return client;
}
async function refresh(client: QueryClient, key: string) {
  await act(async () => { await client.refetchQueries({ queryKey: [key], exact: true }); });
}
function retry(source: "discovery" | "summary") {
  return screen.getByRole("button", { name: `Retry QA ${source}` });
}

// Existing failed-read and QA Navigation Integration contracts: exercise the
// actual read-to-visible-feedback path, not a mocked badge return value.
describe("QA badge prerequisite failure and real source retry", () => {
  it.each(["expanded", "rail", "mobile"] as const)("recovers cold discovery failure through an accessible sibling retry in %s", async variant => {
    rosterStatus = variant === "mobile" ? 403 : 503;
    mount(variant);
    await waitFor(() => expect(calls.roster).toBe(1));
    await screen.findByText("QA discovery unavailable");
    expect(calls.summary).toBe(0);
    const button = retry("discovery");
    expect(button.closest("a")).toBeNull();
    button.focus();
    expect(document.activeElement).toBe(button);
    expect(screen.getByRole("link", { name: /^QA/ }).getAttribute("href")).toBe("/qa");
    expect(document.body.textContent).not.toContain("diagnostic-detail");
    await userEvent.setup().keyboard("{Enter}");
    await waitFor(() => expect(calls.roster).toBe(2));
    await waitFor(() => expect(retry("discovery").hasAttribute("disabled")).toBe(false));
    expect(document.activeElement).toBe(retry("discovery"));
    rosterStatus = 200;
    await userEvent.setup().keyboard("{Enter}");
    await waitFor(() => expect(calls.roster).toBe(3));
    await waitFor(() => expect(calls.summary).toBe(1));
    await waitFor(() => expect(screen.queryByText("QA discovery unavailable")).toBeNull());
    expect(screen.getByText("7")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("link", { name: /^QA/ })));
  });

  it.each([{ names: ["qa"] }, { names: ["health"] }])("qualifies cached discovery failure without certifying stale membership $names", async ({ names }) => {
    const client = mount("expanded", { rosterCache: names, summaryCache: 7 });
    rosterStatus = 503;
    await refresh(client, "butlers");
    await screen.findByText("QA discovery unavailable; cached data is stale");
    const before = { ...calls };
    rosterStatus = 200;
    rosterNames = ["qa"];
    fireEvent.click(retry("discovery"));
    await waitFor(() => expect(calls.roster).toBe(before.roster + 1));
    await waitFor(() => expect(screen.queryByText(/QA discovery unavailable/)).toBeNull());
    expect(screen.getByText("7")).toBeTruthy();
    // A disabled summary cannot stand in for a roster retry.
    expect(calls.summary).toBeLessThanOrEqual(before.summary + 1);
  });

  it("keeps successful absence and genuine empty roster disabled even with an old summary cache", async () => {
    rosterNames = ["health"];
    const client = mount("expanded", { summaryCache: 9 });
    await waitFor(() => expect(calls.roster).toBe(1));
    await waitFor(() => expect(screen.queryByRole("link", { name: /^QA/ })).toBeNull());
    expect(screen.queryByText("9")).toBeNull();
    expect(calls.summary).toBe(0);
    rosterNames = [];
    await refresh(client, "butlers");
    expect(screen.queryByRole("link", { name: /^QA/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry QA/ })).toBeNull();
    expect(calls.summary).toBe(0);
  });

  it.each([false, true])("retries only failed summary and qualifies any cached count (cached=%s)", async cached => {
    summaryStatus = 503;
    const client = mount("expanded", { rosterCache: cached ? ["qa"] : undefined, summaryCache: cached ? 7 : undefined });
    if (cached) {
      await screen.findByRole("link", { name: /^QA/ });
      await refresh(client, "qa-summary");
    }
    await screen.findByText(cached ? "QA summary unavailable; cached data is stale" : "QA summary unavailable");
    if (cached) expect(screen.getByText("7")).toBeTruthy();
    const before = { ...calls };
    count = 8;
    summaryStatus = 200;
    fireEvent.click(retry("summary"));
    await waitFor(() => expect(calls.summary).toBe(before.summary + 1));
    await screen.findByText("8");
    expect(calls.roster).toBe(before.roster);
    expect(screen.queryByText(/QA summary unavailable/)).toBeNull();
  });

  it("recovers simultaneous failures in causal order without treating discovery alone as full recovery", async () => {
    const client = mount("expanded", { rosterCache: ["qa"], summaryCache: 7 });
    rosterStatus = summaryStatus = 503;
    await refresh(client, "butlers");
    await refresh(client, "qa-summary");
    await screen.findByText("QA discovery unavailable; cached data is stale");
    const before = { ...calls };
    rosterStatus = 200;
    fireEvent.click(retry("discovery"));
    await waitFor(() => expect(calls.roster).toBe(before.roster + 1));
    await screen.findByText("QA summary unavailable; cached data is stale");
    expect(calls.summary).toBe(before.summary);
    summaryStatus = 200;
    fireEvent.click(retry("summary"));
    await waitFor(() => expect(calls.summary).toBe(before.summary + 1));
    await waitFor(() => expect(screen.queryByRole("button", { name: /Retry QA/ })).toBeNull());
  });

  it("preserves loading then the healthy positive and genuine zero badge", async () => {
    let release!: () => void;
    rosterWait = new Promise<void>(resolve => { release = resolve; });
    const client = mount();
    expect(screen.queryByRole("button", { name: /Retry QA/ })).toBeNull();
    expect(calls.summary).toBe(0);
    await act(async () => { release(); });
    await screen.findByText("7");
    count = 0;
    await refresh(client, "qa-summary");
    await waitFor(() => expect(screen.queryByText("7")).toBeNull());
    expect(screen.queryByRole("button", { name: /Retry QA/ })).toBeNull();
    expect(screen.queryByLabelText("QA escalations unavailable")).toBeNull();
  });

  it("lets real 401 owner-session loss remove private cached counts and retry controls", async () => {
    const client = mount("expanded", { gate: true });
    await screen.findByText("7");
    authenticated = false;
    rosterStatus = 401;
    await refresh(client, "butlers");
    await waitFor(() => expect(screen.queryByRole("link", { name: /^QA/ })).toBeNull());
    expect(screen.queryByText("7")).toBeNull();
    expect(screen.queryByRole("button", { name: /Retry QA/ })).toBeNull();
    expect(client.getQueryData(["qa-summary"])).toBeUndefined();
    expect(client.getQueryData(["butlers"])).toBeUndefined();
  });
});
