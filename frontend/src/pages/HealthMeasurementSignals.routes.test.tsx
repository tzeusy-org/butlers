// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { getShellCapability } from "@/lib/shell-capability";
import type { ExpectedSignal, ExpectedSignalsResponse } from "@/api/types";

// Only SVG layout is outside jsdom; health hooks, API client and route loaders are real.
vi.mock("@/components/ui/TimeSeriesChart", () => ({ TimeSeriesChart: () => <div data-testid="observed-chart">Observed readings</div> }));
const clients: QueryClient[] = [];
const now = new Date().toISOString();
let signals: ExpectedSignalsResponse;
let readings = false;
let signalMode = "ok";
const requests: string[] = [];
function signal(type: string, measurability: ExpectedSignal["measurability"] = "unmeasurable"): ExpectedSignal {
  return { signal_key: `health:measurement-gap:${type}`, producer: "google_health", producer_endpoint_identity: "test-device", expected_cadence_seconds: 86400, last_observed_at: null, measurability, unmeasurable_reason: "producer_unavailable", evaluated_at: now };
}
function serve() {
  signals = { available: true, signals: [signal("weight")], degraded_reason: null };
  readings = false; signalMode = "ok"; requests.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost"); requests.push(url.pathname + url.search);
    let body: unknown;
    if (url.pathname.endsWith("/expected-signals")) {
      if (signalMode === "pending") return new Promise<Response>(() => {});
      if (signalMode === "error") return new Response('{}', { status: 503 });
      body = signals;
    } else if (url.pathname.endsWith("/measurements/types")) body = { types: ["weight", "heart_rate"].map(type => ({ type, label: type === "weight" ? "Weight" : "Heart rate", sample_count: 1, latest_at: now, unit: null, value_shape: "scalar", chart_eligible: true, kpi_eligible: true })) };
    else if (url.pathname.endsWith("/measurements/latest")) body = { measurements: readings ? { weight: { value: { value: 70 }, measured_at: now, unit: "kg", metadata: null } } : {} };
    else if (url.pathname.endsWith("/measurements/sources")) body = { sources: [] };
    else if (url.pathname.endsWith("/measurements/trend")) body = { buckets: readings ? [{ bucket_start: now, value_mean: 70, value_min: 70, value_max: 70, sample_count: 1 }] : [] };
    else if (url.pathname.endsWith("/measurements")) body = { data: readings ? [{ id: "reading-1", type: "weight", value: { value: 70 }, measured_at: now, created_at: now, notes: null }] : [], meta: { total: readings ? 1 : 0, offset: 0, limit: 500 } };
    else if (url.pathname.endsWith("/briefing")) body = { data: { greet: "Good day.", headline: "Health is ready.", elaboration: "Your health overview.", source: "fallback", generated_at: now } };
    else if (url.pathname.endsWith("/insights")) body = { data: [] };
    else throw new Error(`Unexpected test endpoint: ${url.pathname}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
async function route(path: string) {
  const pathname = path.split("?")[0];
  const { default: Page } = await getShellCapability(pathname)!.loader();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path={pathname} element={<Page />} /></Routes></MemoryRouter></QueryClientProvider>);
  return client;
}
function resetView() { cleanup(); clients.forEach(client => client.clear()); clients.length = 0; }
afterEach(() => { resetView(); vi.unstubAllGlobals(); });

describe("Health routed measurement acquisition state", () => {
  it("marks an absent vital unreachable through the real query and retains observed readings", async () => {
    serve(); await route("/health"); expect(await screen.findByText("Unreachable")).toBeTruthy();
    expect(within(screen.getByRole("group", { name: "Key performance indicators" })).getAllByText("—")).toHaveLength(4);
    resetView(); readings = true; signals = { available: false, signals: null, degraded_reason: "ledger_unavailable" };
    await route("/health"); expect(await screen.findByText("70")).toBeTruthy(); expect(await screen.findByTestId("expected-signals-degraded")).toBeTruthy(); expect(screen.queryByText("Unreachable")).toBeNull();
  });
  it("uses absent core identity without requesting an invented type or matching unrelated keys", async () => {
    serve(); signals.signals = [signal("blood_pressure"), signal("weight:other"), { ...signal("heart_rate"), signal_key: "other:measurement-gap:heart_rate" }];
    await route("/health"); expect(await screen.findByText("Unreachable")).toBeTruthy(); expect(screen.getAllByText("Unreachable")).toHaveLength(1);
    expect(requests.find(url => url.includes("/measurements/latest"))).not.toContain("blood_pressure");
  });
  it.each(["present", "absent", "none", "pending"])("keeps normal absence for %s signal state", async state => {
    serve(); signals.signals = state === "none" ? [] : [signal("weight", state === "present" ? "present" : "absent")]; signalMode = state;
    await route("/health"); await screen.findByText("Health is ready."); expect(screen.queryByText("Unreachable")).toBeNull(); expect(screen.queryByTestId("expected-signals-degraded")).toBeNull();
  });
  it.each(["unavailable", "null", "error"])("names %s degradation and retains normal absence", async state => {
    serve(); signals = { available: state === "null", signals: null, degraded_reason: "ledger_unavailable" }; signalMode = state;
    await route("/health"); expect(await screen.findByTestId("expected-signals-degraded")).toBeTruthy(); expect(screen.queryByText("Unreachable")).toBeNull(); expect(within(screen.getByRole("group", { name: "Key performance indicators" })).getAllByText("—")).toHaveLength(4);
  });
  it("isolates selected empty chart state and retains actual chart/trend data", async () => {
    serve(); await route("/health/measurements?type=weight"); expect(await screen.findByText("Weight instrument unreachable. No readings for this range.")).toBeTruthy();
    resetView(); await route("/health/measurements?type=heart_rate"); expect(await screen.findByText("No Heart rate readings for this range.")).toBeTruthy(); expect(screen.queryByText(/instrument unreachable/)).toBeNull();
    resetView(); readings = true; await route("/health/measurements?type=weight"); expect(await screen.findByTestId("observed-chart")).toBeTruthy(); expect(screen.queryByText(/instrument unreachable/)).toBeNull();
  });
  it("keeps chart absence honest when signal health fails", async () => {
    serve(); signalMode = "error"; await route("/health/measurements?type=weight"); expect(await screen.findByTestId("expected-signals-degraded")).toBeTruthy(); expect(await screen.findByText("No Weight readings for this range.")).toBeTruthy(); expect(screen.queryByText(/instrument unreachable/)).toBeNull();
  });
  it("withdraws a cached unreachable claim after a failed refresh without changing query cadence", async () => {
    serve(); const client = await route("/health"); expect(await screen.findByText("Unreachable")).toBeTruthy();
    // Assert the resolved cadence, rather than echoing the hook's own constant.
    // eslint-disable-next-line no-restricted-syntax -- actual cadence contract assertion
    expect(client.getQueryCache().find({ queryKey: ["health-expected-signals"] })?.options).toEqual(expect.objectContaining({ refetchInterval: 60_000 }));
    signalMode = "error";
    await act(async () => { await client.refetchQueries({ queryKey: ["health-expected-signals"] }); });
    expect(await screen.findByTestId("expected-signals-degraded")).toBeTruthy(); expect(screen.queryByText("Unreachable")).toBeNull();
  });
});
