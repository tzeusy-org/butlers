/**
 * Tests for the fact Vouch client (bu-jv1moa.14): vouchFact posts to the owner-only
 * vouch route, and getFacts forwards the `held` filter.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockJsonResponse(body: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

import { getFacts, vouchFact } from "./client.ts";

describe("vouchFact", () => {
  it("POSTs to the fact vouch route with the id encoded", async () => {
    const receipt = {
      fact_id: "f/1",
      content_authority: "third_party",
      endorsed_at: "2026-10-18T00:00:00Z",
      endorsed_by: null,
      changed: true,
    };
    mockJsonResponse({ data: receipt, meta: {} });
    const res = await vouchFact("f/1");
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/memory/facts/f%2F1/vouch");
    expect((init as RequestInit).method).toBe("POST");
    expect(res.data.changed).toBe(true);
  });
});

describe("getFacts held filter", () => {
  it.each([
    [true, "held=true"],
    [false, "held=false"],
  ])("forwards held=%s", async (held, expected) => {
    mockJsonResponse({ data: [], meta: { total: 0, offset: 0, limit: 50 } });
    await getFacts({ held });
    expect(mockFetch.mock.calls[0][0]).toContain(expected);
  });

  it("omits held when unset", async () => {
    mockJsonResponse({ data: [], meta: { total: 0, offset: 0, limit: 50 } });
    await getFacts({ validity: "active" });
    expect(mockFetch.mock.calls[0][0]).not.toContain("held");
  });
});
