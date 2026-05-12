/**
 * Tests for cost API client functions — date-range params [bu-j1fbt],
 * butler scoping [bu-wyami].
 *
 * Verifies:
 * - getCostSummary builds correct query string for preset period
 * - getCostSummary sends from/to params when custom range is provided
 * - getCostSummary includes butler= when provided (supported since bu-iuol4.12)
 * - getDailyCosts sends from/to params when provided
 * - getDailyCosts omits params when called without arguments
 * - getDailyCosts includes butler= when provided (forwarded for bu-lryu6 compat)
 *
 * Note: getCostSummary and getDailyCosts both accept YYYY-MM-DD strings.
 * Timezone-aware formatting is the caller's responsibility (use formatCostDate
 * from @/hooks/use-costs for Date → string conversion).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mock fetch so we never hit the network
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: { get: () => "application/json" },
  });
}

import { getCostSummary, getDailyCosts } from "./client.ts";

// ---------------------------------------------------------------------------
// getCostSummary — preset period
// ---------------------------------------------------------------------------

describe("getCostSummary — preset period", () => {
  it("sends period=7d when called with period only", async () => {
    mockResponse({ data: { period: "7d", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary("7d");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("period=7d");
    expect(url).not.toContain("from=");
    expect(url).not.toContain("to=");
  });

  it("sends no query params when called with no args", async () => {
    mockResponse({ data: { period: "today", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("?");
  });
});

// ---------------------------------------------------------------------------
// getCostSummary — custom date range
// ---------------------------------------------------------------------------

describe("getCostSummary — date range params", () => {
  it("sends from/to ISO date strings when string dates are provided", async () => {
    mockResponse({ data: { period: "2026-03-01/2026-03-31", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary(undefined, "2026-03-01", "2026-03-31");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-03-01");
    expect(url).toContain("to=2026-03-31");
    expect(url).not.toContain("period=");
  });

  it("prefers from/to over period when both are provided", async () => {
    mockResponse({ data: { period: "2026-01-01/2026-01-31", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary("7d", "2026-01-01", "2026-01-31");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-01-01");
    expect(url).toContain("to=2026-01-31");
    expect(url).not.toContain("period=");
  });
});

// ---------------------------------------------------------------------------
// getDailyCosts — date range params
// ---------------------------------------------------------------------------

describe("getDailyCosts — date range params", () => {
  it("omits query params when called without arguments", async () => {
    mockResponse({ data: [] });
    await getDailyCosts();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/costs/daily");
    expect(url).not.toContain("?");
  });

  it("sends from/to ISO date strings when string dates are provided", async () => {
    mockResponse({ data: [] });
    await getDailyCosts("2026-04-01", "2026-04-30");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-04-01");
    expect(url).toContain("to=2026-04-30");
  });

  it("sends only from when only from is provided", async () => {
    mockResponse({ data: [] });
    await getDailyCosts("2026-04-01");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-04-01");
    expect(url).not.toContain("to=");
  });
});

// ---------------------------------------------------------------------------
// getCostSummary — butler param (bu-wyami / bu-iuol4.12)
// ---------------------------------------------------------------------------

describe("getCostSummary — butler param", () => {
  it("includes butler= when provided alongside period", async () => {
    mockResponse({ data: { period: "30d", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary("30d", undefined, undefined, "my-butler");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("period=30d");
    expect(url).toContain("butler=my-butler");
  });

  it("includes butler= when provided alongside from/to", async () => {
    mockResponse({ data: { period: "2026-04-01/2026-04-30", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary(undefined, "2026-04-01", "2026-04-30", "my-butler");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-04-01");
    expect(url).toContain("to=2026-04-30");
    expect(url).toContain("butler=my-butler");
  });

  it("omits butler= when not provided", async () => {
    mockResponse({ data: { period: "7d", total_cost_usd: 0, total_sessions: 0, total_input_tokens: 0, total_output_tokens: 0, by_butler: {}, by_model: {} } });
    await getCostSummary("7d");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("butler=");
  });
});

// ---------------------------------------------------------------------------
// getDailyCosts — butler param (bu-wyami / forward compat for bu-lryu6)
// ---------------------------------------------------------------------------

describe("getDailyCosts — butler param", () => {
  it("includes butler= in URL when provided", async () => {
    mockResponse({ data: [] });
    await getDailyCosts("2026-04-01", "2026-04-30", "my-butler");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("butler=my-butler");
    expect(url).toContain("from=2026-04-01");
    expect(url).toContain("to=2026-04-30");
  });

  it("omits butler= when not provided", async () => {
    mockResponse({ data: [] });
    await getDailyCosts("2026-04-01", "2026-04-30");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("butler=");
  });

  it("includes butler= alongside no date params when only butler is given", async () => {
    mockResponse({ data: [] });
    await getDailyCosts(undefined, undefined, "my-butler");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("butler=my-butler");
    expect(url).not.toContain("from=");
    expect(url).not.toContain("to=");
  });
});
