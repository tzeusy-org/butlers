/**
 * Tests for getEntityFacts + getEntityNeighbours API client functions (bu-ekad9).
 *
 * Verifies the rewired contracts for the dashboard-relationship facts drill and
 * neighbour ranking (backend PR #2180):
 * - getEntityFacts sends keyset filters (predicate / validity / store / limit /
 *   cursor) and reads the {items, next_cursor, has_more} envelope.
 * - getEntityNeighbours sends rank / per_predicate and reads the remainders map.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

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

import { getEntityFacts, getEntityNeighbours } from "./client.ts";

// ---------------------------------------------------------------------------
// getEntityFacts — keyset envelope + filters
// ---------------------------------------------------------------------------

describe("getEntityFacts — keyset contract", () => {
  it("returns the {items, next_cursor, has_more} envelope", async () => {
    mockJson({
      items: [
        {
          id: "f1",
          subject: "e1",
          predicate: "works-at",
          object: "Acme",
          object_kind: "literal",
          src: "relationship",
          conf: 1,
          weight: 5,
          last_observed_at: null,
          verified: false,
          primary: null,
          validity: "active",
          created_at: "2025-01-01T00:00:00Z",
          store: "identity",
          staleness_band: "fresh",
        },
      ],
      next_cursor: "CURSOR_2",
      has_more: true,
    });

    const res = await getEntityFacts("e1");
    expect(res.items).toHaveLength(1);
    expect(res.items[0].store).toBe("identity");
    expect(res.items[0].staleness_band).toBe("fresh");
    expect(res.next_cursor).toBe("CURSOR_2");
    expect(res.has_more).toBe(true);
    // No filters → bare path.
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/entities/e1/facts");
    expect(url).not.toContain("?");
  });

  it("sends predicate / validity / store / limit / cursor query params", async () => {
    mockJson({ items: [], next_cursor: null, has_more: false });
    await getEntityFacts("e1", {
      predicate: "works-at",
      validity: "superseded",
      store: "all",
      limit: 50,
      cursor: "CURSOR_X",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("predicate=works-at");
    expect(url).toContain("validity=superseded");
    expect(url).toContain("store=all");
    expect(url).toContain("limit=50");
    expect(url).toContain("cursor=CURSOR_X");
    // Legacy offset/total params are gone.
    expect(url).not.toContain("offset");
  });

  it("does not send omitted params", async () => {
    mockJson({ items: [], next_cursor: null, has_more: false });
    await getEntityFacts("e1", { limit: 20 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("limit=20");
    expect(url).not.toContain("predicate");
    expect(url).not.toContain("validity");
    expect(url).not.toContain("store");
    expect(url).not.toContain("cursor");
  });
});

// ---------------------------------------------------------------------------
// getEntityNeighbours — ranked truncation + remainders
// ---------------------------------------------------------------------------

describe("getEntityNeighbours — ranking contract", () => {
  it("reads the remainders map from the response", async () => {
    mockJson({
      neighbours: { knows: [] },
      remainders: { knows: 34 },
    });
    const res = await getEntityNeighbours("e1", { rank: "weight" });
    expect(res.remainders.knows).toBe(34);
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("rank=weight");
  });

  it("sends per_predicate when provided", async () => {
    mockJson({ neighbours: {}, remainders: {} });
    await getEntityNeighbours("e1", { rank: "weight", per_predicate: 3 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("rank=weight");
    expect(url).toContain("per_predicate=3");
  });

  it("sends no rank params when omitted", async () => {
    mockJson({ neighbours: {}, remainders: {} });
    await getEntityNeighbours("e1");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/entities/e1/neighbours");
    expect(url).not.toContain("rank");
    expect(url).not.toContain("per_predicate");
  });
});
