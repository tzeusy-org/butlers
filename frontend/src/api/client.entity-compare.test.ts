/**
 * Tests for the merge-review compare API client functions (bu-b2qg8):
 * compareRelationshipEntities, dismissRelationshipEntityPair.
 *
 * Verifies each method targets the correct path/verb and reads the documented
 * response shape (backend PR #2187 / bu-9wcxm).
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

import { compareRelationshipEntities, dismissRelationshipEntityPair } from "./client.ts";

describe("compareRelationshipEntities", () => {
  it("POSTs {entity_a, entity_b} and reads the a/b + shared/divergent diff", async () => {
    mockJson({
      a: {
        entity: {
          id: "a1",
          canonical_name: "Alice",
          entity_type: "person",
          aliases: [],
          tier: null,
          state: "active",
        },
        identity_facts: [
          {
            id: "f1",
            entity_id: "a1",
            predicate: "has-email",
            object: "alice@x.com",
            object_kind: "literal",
            store: "identity",
            src: "telegram",
            conf: 1,
            verified: true,
            primary: true,
            observed_at: "2026-06-01T00:00:00Z",
            last_seen: "2026-06-01T00:00:00Z",
            staleness_band: "fresh",
          },
        ],
        narrative_facts: [],
      },
      b: {
        entity: {
          id: "b1",
          canonical_name: "Alice B",
          entity_type: "person",
          aliases: ["Al"],
          tier: 5,
          state: "active",
        },
        identity_facts: [],
        narrative_facts: [],
      },
      shared: [
        {
          id: "f1",
          entity_id: "a1",
          predicate: "has-email",
          object: "alice@x.com",
          object_kind: "literal",
          store: "identity",
          src: "telegram",
          conf: 1,
          verified: true,
          staleness_band: "fresh",
        },
      ],
      divergent: [
        {
          id: "f2",
          entity_id: "a1",
          predicate: "has-birthday",
          object: "1990-06-15",
          object_kind: "literal",
          store: "identity",
          src: "telegram",
          conf: 1,
          verified: true,
          staleness_band: "fresh",
        },
      ],
    });

    const res = await compareRelationshipEntities({ entity_a: "a1", entity_b: "b1" });

    expect(res.a.entity.canonical_name).toBe("Alice");
    expect(res.b.entity.tier).toBe(5);
    expect(res.shared).toHaveLength(1);
    expect(res.shared[0].predicate).toBe("has-email");
    expect(res.divergent[0].predicate).toBe("has-birthday");

    const url: string = mockFetch.mock.calls[0][0];
    const opts = mockFetch.mock.calls[0][1];
    expect(url).toContain("/relationship/entities/compare");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toEqual({ entity_a: "a1", entity_b: "b1" });
  });
});

describe("dismissRelationshipEntityPair", () => {
  it("POSTs {entity_a, entity_b} and reads {review_id, outcome, shared_facts}", async () => {
    mockJson({
      review_id: "r1",
      entity_a: "a1",
      entity_b: "b1",
      outcome: "dismissed",
      shared_facts: [],
    });

    const res = await dismissRelationshipEntityPair({ entity_a: "a1", entity_b: "b1" });

    expect(res.review_id).toBe("r1");
    expect(res.outcome).toBe("dismissed");
    expect(res.shared_facts).toEqual([]);

    const url: string = mockFetch.mock.calls[0][0];
    const opts = mockFetch.mock.calls[0][1];
    expect(url).toContain("/relationship/entities/dismiss-pair");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body as string)).toEqual({ entity_a: "a1", entity_b: "b1" });
  });
});
