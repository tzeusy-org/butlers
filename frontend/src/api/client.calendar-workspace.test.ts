/**
 * Tests for getCalendarWorkspace API client param serialization (bu-xr1i95).
 *
 * Verifies the server-side facet + keyset pagination params are forwarded:
 * - `status`, `source_type`, `editable` facets
 * - `limit` and opaque `cursor`
 * - none of them appear when omitted (back-compat)
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockWorkspaceResponse() {
  const body = {
    data: { entries: [], source_freshness: [], lanes: [], next_cursor: null, has_more: false },
  };
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

import { getCalendarWorkspace } from "./client.ts";

const BASE = {
  view: "user" as const,
  start: "2026-02-22T00:00:00Z",
  end: "2026-02-23T00:00:00Z",
};

describe("getCalendarWorkspace — facets + pagination params", () => {
  it("forwards status, source_type, editable, limit, and cursor", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({
      ...BASE,
      status: "paused",
      source_type: "scheduled_task",
      editable: true,
      limit: 50,
      cursor: "opaque-token",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("status=paused");
    expect(url).toContain("source_type=scheduled_task");
    expect(url).toContain("editable=true");
    expect(url).toContain("limit=50");
    expect(url).toContain("cursor=opaque-token");
  });

  it("omits the facet/pagination params when not provided (back-compat)", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({ ...BASE });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("status=");
    expect(url).not.toContain("source_type=");
    expect(url).not.toContain("editable=");
    expect(url).not.toContain("limit=");
    expect(url).not.toContain("cursor=");
  });

  it("serializes editable=false explicitly", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({ ...BASE, editable: false });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("editable=false");
  });
});
