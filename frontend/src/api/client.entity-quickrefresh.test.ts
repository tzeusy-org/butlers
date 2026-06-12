/**
 * Tests for the entity v3 quick-refresh API client functions (bu-xzh76):
 * getEntityActivityBins, getEntityDeltaFacts, markEntityView, getEntityCoreDates.
 *
 * Verifies each method targets the correct path/verb and reads the documented
 * response shape (backend PR #2183 / bu-bjvny).
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

import {
  getEntityActivityBins,
  getEntityCoreDates,
  getEntityDeltaFacts,
  markEntityView,
} from "./client.ts";

describe("getEntityActivityBins", () => {
  it("sends bins=daily&bins_only=true and reads the bins series", async () => {
    mockJson({ bins: [{ date: "2026-06-01", count: 0 }, { date: "2026-06-02", count: 3 }] });
    const res = await getEntityActivityBins("e1");
    expect(res.bins).toHaveLength(2);
    expect(res.bins[1].count).toBe(3);
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/entities/e1/activity?");
    expect(url).toContain("bins=daily");
    expect(url).toContain("bins_only=true");
  });

  it("sends the window param when provided", async () => {
    mockJson({ bins: [] });
    await getEntityActivityBins("e1", { window: "30d" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("window=30d");
  });
});

describe("getEntityDeltaFacts", () => {
  it("GETs delta-facts and reads {marked_at, items}", async () => {
    mockJson({
      marked_at: "2026-06-01T00:00:00Z",
      items: [
        {
          id: "f1",
          subject: "e1",
          predicate: "has-note",
          object: "x",
          object_kind: "literal",
          src: "memory",
          conf: 1,
          store: "narrative",
          validity: "active",
          created_at: "2026-06-02T00:00:00Z",
          changed_at: "2026-06-02T00:00:00Z",
        },
      ],
    });
    const res = await getEntityDeltaFacts("e1");
    expect(res.marked_at).toBe("2026-06-01T00:00:00Z");
    expect(res.items[0].store).toBe("narrative");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/entities/e1/delta-facts");
  });

  it("reads marked_at: null on a first visit", async () => {
    mockJson({ marked_at: null, items: [] });
    const res = await getEntityDeltaFacts("e1");
    expect(res.marked_at).toBeNull();
    expect(res.items).toHaveLength(0);
  });
});

describe("markEntityView", () => {
  it("POSTs view-mark and reads {entity_id, marked_at}", async () => {
    mockJson({ entity_id: "e1", marked_at: "2026-06-12T00:00:00Z" });
    const res = await markEntityView("e1");
    expect(res.entity_id).toBe("e1");
    expect(res.marked_at).toBe("2026-06-12T00:00:00Z");
    const url: string = mockFetch.mock.calls[0][0];
    const opts = mockFetch.mock.calls[0][1];
    expect(url).toContain("/entities/e1/view-mark");
    expect(opts.method).toBe("POST");
  });
});

describe("getEntityCoreDates", () => {
  it("GETs core-dates and reads the items list", async () => {
    mockJson({
      items: [
        {
          id: "d1",
          predicate: "has-birthday",
          value: "1990-06-15",
          month: 6,
          day: 15,
          year: 1990,
          next_occurrence: "2026-06-15",
          days_until: 3,
          src: "telegram",
          conf: 1,
          verified: true,
          staleness_band: "fresh",
        },
      ],
    });
    const res = await getEntityCoreDates("e1");
    expect(res.items).toHaveLength(1);
    expect(res.items[0].days_until).toBe(3);
    expect(res.items[0].next_occurrence).toBe("2026-06-15");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/entities/e1/core-dates");
  });
});
