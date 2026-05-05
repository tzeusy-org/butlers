/**
 * Tests for cost API client functions — date-range params [bu-j1fbt].
 *
 * Verifies:
 * - getCostSummary builds correct query string for preset period
 * - getCostSummary sends from/to params when custom range is provided
 * - getDailyCosts sends from/to params when provided
 * - getDailyCosts omits params when called without arguments
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
