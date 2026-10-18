/** Paths, verbs and query strings of the knowledge-gap client (bu-q7vx1q.73). */

import { afterEach, describe, expect, it, vi } from "vitest";

import { dismissMemoryGap, getMemoryGapDemand, getMemoryGaps } from "./memory-gaps.ts";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockJson(body: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

function lastRequest(): { url: string; init: RequestInit } {
  const [url, init] = mockFetch.mock.calls.at(-1)!;
  return { url: String(url), init: (init ?? {}) as RequestInit };
}

describe("getMemoryGaps", () => {
  it("asks for the default open plus answerable list with no query string", async () => {
    mockJson({ data: [], meta: { limit: 50, next_cursor: null, has_more: false } });

    const result = await getMemoryGaps();

    expect(result.data).toEqual([]);
    expect(lastRequest().url).toMatch(/\/memory\/gaps$/);
  });

  it("forwards status, limit and the opaque keyset cursor", async () => {
    mockJson({ data: [], meta: { limit: 10, next_cursor: null, has_more: false } });

    await getMemoryGaps({ status: "answerable", limit: 10, cursor: "abc_-=" });

    const { url } = lastRequest();
    expect(url).toContain("/memory/gaps?");
    const search = new URL(url, "http://x").searchParams;
    expect(search.get("status")).toBe("answerable");
    expect(search.get("limit")).toBe("10");
    expect(search.get("cursor")).toBe("abc_-=");
  });

  it("omits a null cursor and passes the abort signal through", async () => {
    mockJson({ data: [], meta: { limit: 50, next_cursor: null, has_more: false } });
    const signal = new AbortController().signal;

    await getMemoryGaps({ cursor: null, signal });

    expect(lastRequest().url).not.toContain("cursor");
    expect(lastRequest().init.signal).toBeDefined();
  });
});

describe("getMemoryGapDemand", () => {
  it("reads the per-predicate demand and its degraded meta", async () => {
    mockJson({
      data: [{ predicate: "lives_in", open_gaps: 2, asks: 3 }],
      meta: { sources_degraded: ["finance"] },
    });

    const result = await getMemoryGapDemand();

    expect(lastRequest().url).toMatch(/\/memory\/gaps\/demand$/);
    expect(result.data[0]).toEqual({ predicate: "lives_in", open_gaps: 2, asks: 3 });
    expect(result.meta.sources_degraded).toEqual(["finance"]);
  });
});

describe("dismissMemoryGap", () => {
  it("POSTs to the dismiss route with the id encoded", async () => {
    mockJson({ data: { gap_id: "a/b", status: "dismissed" }, meta: {} });

    const result = await dismissMemoryGap("a/b");

    const { url, init } = lastRequest();
    expect(url).toMatch(/\/memory\/gaps\/a%2Fb\/dismiss$/);
    expect(init.method).toBe("POST");
    expect(result.data.status).toBe("dismissed");
  });
});
