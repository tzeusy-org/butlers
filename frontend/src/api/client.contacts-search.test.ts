/**
 * Tests for the contacts typeahead API client (searchContacts).
 *
 * Targets GET /api/contacts/search — the read-only person-entity typeahead that
 * powers the calendar People picker.
 *
 * Verifies:
 * - searchContacts hits GET /api/contacts/search with the URL-encoded query.
 * - the optional limit is forwarded as a query param.
 * - the ContactSearchResponse envelope is returned verbatim.
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

import { searchContacts } from "./client.ts";

describe("searchContacts", () => {
  it("requests the contacts search endpoint with the query", async () => {
    mockJsonResponse({ results: [] });
    await searchContacts("ada");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/contacts/search");
    expect(url).toContain("q=ada");
  });

  it("url-encodes the query", async () => {
    mockJsonResponse({ results: [] });
    await searchContacts("a b&c");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("q=a+b%26c");
  });

  it("forwards the limit when provided", async () => {
    mockJsonResponse({ results: [] });
    await searchContacts("ada", { limit: 5 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("limit=5");
  });

  it("returns the ContactSearchResponse envelope", async () => {
    mockJsonResponse({
      results: [
        {
          entity_id: "11111111-1111-1111-1111-111111111111",
          canonical_name: "Ada Lovelace",
          matched_identifier: { type: "email", value: "ada@example.com" },
        },
      ],
    });
    const res = await searchContacts("ada");
    expect(res.results).toHaveLength(1);
    expect(res.results[0].canonical_name).toBe("Ada Lovelace");
    expect(res.results[0].matched_identifier?.value).toBe("ada@example.com");
  });
});
