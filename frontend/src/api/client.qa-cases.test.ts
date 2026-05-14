/**
 * Tests for QA cases API client URL/querystring building [bu-cavyk].
 */

import { afterEach, describe, expect, it, vi } from "vitest";

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

import { getQaCase, getQaCaseJournal, getQaCases } from "./client.ts";

const EMPTY_PAGE = { data: [], meta: { total: 0, offset: 0, limit: 25, has_more: false } };
const EMPTY_DOSSIER = {
  data: {
    case: {
      id: "case-1",
      short_id: "#1",
      sev: "high",
      butler: "qa",
      headline: null,
      detected: "2026-05-15T00:00:00Z",
      age_seconds: 0,
      state: "detect",
      pr_state: null,
      pr_url: null,
    },
    state_track_stage: "detect",
    investigation_notes: null,
    pr: null,
    journal: [],
  },
  meta: {},
};

describe("getQaCases", () => {
  it("uses bare /qa/cases path when no params are provided", async () => {
    mockResponse(EMPTY_PAGE);
    await getQaCases();

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/qa/cases");
    expect(url).not.toContain("?");
  });

  it("appends severity, since, offset, and limit params", async () => {
    mockResponse(EMPTY_PAGE);
    await getQaCases({ sev: "high", since: "24h", offset: 10, limit: 5 });

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("sev=high");
    expect(url).toContain("since=24h");
    expect(url).toContain("offset=10");
    expect(url).toContain("limit=5");
  });
});

describe("getQaCase", () => {
  it("URI-encodes the case ID in the dossier path", async () => {
    mockResponse(EMPTY_DOSSIER);
    await getQaCase("case/with spaces");

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/qa/cases/case%2Fwith%20spaces");
  });
});

describe("getQaCaseJournal", () => {
  it("appends cursor and limit params to the journal path", async () => {
    mockResponse(EMPTY_PAGE);
    await getQaCaseJournal("case-1", {
      cursor: "2026-05-15T00:00:00Z",
      limit: 50,
    });

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/qa/cases/case-1/journal");
    expect(url).toContain("cursor=2026-05-15T00%3A00%3A00Z");
    expect(url).toContain("limit=50");
  });
});
