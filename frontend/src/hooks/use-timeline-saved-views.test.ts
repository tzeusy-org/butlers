/**
 * Tests for the use-timeline-saved-views hooks and the API client
 * functions that back them (bu-vgj88).
 *
 * Covers:
 * - listTimelineSavedViews: returns ApiResponse-enveloped list
 * - createTimelineSavedView: POSTs body and returns created entry
 * - updateTimelineSavedView: PATCHes and returns updated entry
 * - deleteTimelineSavedView: DELETEs and returns void (204)
 * - Error cases: non-ok responses throw ApiError
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSavedViewEntry(overrides: Record<string, unknown> = {}) {
  return {
    id: "550e8400-e29b-41d4-a716-446655440000",
    name: "My errors view",
    filter_spec: { statuses: ["error", "replay_failed"], range: "24h" },
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

function mockJsonResponse(body: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

function mockNoContentResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 204,
    json: async () => undefined,
    text: async () => "",
    headers: { get: () => "" },
  });
}

// ---------------------------------------------------------------------------
// Import the functions under test (after mock setup)
// ---------------------------------------------------------------------------

import {
  listTimelineSavedViews,
  createTimelineSavedView,
  updateTimelineSavedView,
  deleteTimelineSavedView,
} from "@/api/client.ts";
import { ApiError } from "@/api/client.ts";

// ---------------------------------------------------------------------------
// listTimelineSavedViews
// ---------------------------------------------------------------------------

describe("listTimelineSavedViews", () => {
  it("GET /api/timeline/saved-views returns ApiResponse-enveloped list", async () => {
    const entries = [makeSavedViewEntry()];
    mockJsonResponse({ data: entries, meta: {} });

    const result = await listTimelineSavedViews();
    expect(result.data).toHaveLength(1);
    expect(result.data[0].name).toBe("My errors view");

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/timeline/saved-views");

    const init = mockFetch.mock.calls[0][1];
    expect(init?.method).toBeUndefined(); // default GET
  });

  it("returns an empty list when none exist", async () => {
    mockJsonResponse({ data: [], meta: {} });
    const result = await listTimelineSavedViews();
    expect(result.data).toEqual([]);
  });

  it("throws ApiError on non-ok response", async () => {
    mockJsonResponse({ detail: "Shared database is not available" }, 503);
    await expect(listTimelineSavedViews()).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// createTimelineSavedView
// ---------------------------------------------------------------------------

describe("createTimelineSavedView", () => {
  it("POST /api/timeline/saved-views with name and filter_spec", async () => {
    const entry = makeSavedViewEntry();
    mockJsonResponse(entry, 201);

    const result = await createTimelineSavedView({
      name: "My errors view",
      filter_spec: { statuses: ["error", "replay_failed"], range: "24h" },
    });
    expect(result.id).toBe(entry.id);
    expect(result.name).toBe("My errors view");

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/timeline/saved-views");

    const init = mockFetch.mock.calls[0][1];
    expect(init?.method).toBe("POST");

    const body = JSON.parse(init?.body as string);
    expect(body.name).toBe("My errors view");
    expect(body.filter_spec.statuses).toContain("error");
  });

  it("persists all filter_spec fields in the request body", async () => {
    mockJsonResponse(makeSavedViewEntry());
    await createTimelineSavedView({
      name: "Full view",
      filter_spec: {
        statuses: ["ingested"],
        range: "7d",
        q: "hello",
        channels: "gmail,telegram",
      },
    });

    const body = JSON.parse(mockFetch.mock.calls[0][1]?.body as string);
    expect(body.filter_spec).toMatchObject({
      statuses: ["ingested"],
      range: "7d",
      q: "hello",
      channels: "gmail,telegram",
    });
  });

  it("throws ApiError when name is invalid (400)", async () => {
    mockJsonResponse(
      { detail: [{ msg: "String should have at least 1 character" }] },
      400,
    );
    await expect(
      createTimelineSavedView({ name: "", filter_spec: {} }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// updateTimelineSavedView
// ---------------------------------------------------------------------------

describe("updateTimelineSavedView", () => {
  it("PATCH /api/timeline/saved-views/{id} updates the entry", async () => {
    const updated = makeSavedViewEntry({ name: "Renamed view" });
    mockJsonResponse(updated);

    const result = await updateTimelineSavedView(
      "550e8400-e29b-41d4-a716-446655440000",
      { name: "Renamed view" },
    );
    expect(result.name).toBe("Renamed view");

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/timeline/saved-views/550e8400-e29b-41d4-a716-446655440000");

    const init = mockFetch.mock.calls[0][1];
    expect(init?.method).toBe("PATCH");
  });

  it("throws ApiError on 404 when view not found", async () => {
    mockJsonResponse({ detail: "Saved view not found" }, 404);
    await expect(
      updateTimelineSavedView("not-a-uuid", { name: "x" }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// deleteTimelineSavedView
// ---------------------------------------------------------------------------

describe("deleteTimelineSavedView", () => {
  it("DELETE /api/timeline/saved-views/{id} returns void on 204", async () => {
    mockNoContentResponse();
    const result = await deleteTimelineSavedView("550e8400-e29b-41d4-a716-446655440000");
    expect(result).toBeUndefined();

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/timeline/saved-views/550e8400-e29b-41d4-a716-446655440000");

    const init = mockFetch.mock.calls[0][1];
    expect(init?.method).toBe("DELETE");
  });

  it("throws ApiError on 404 when view not found", async () => {
    mockJsonResponse({ detail: "Saved view not found" }, 404);
    await expect(
      deleteTimelineSavedView("missing-id"),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

// ---------------------------------------------------------------------------
// filter_spec round-trip
// ---------------------------------------------------------------------------

describe("filter_spec round-trip", () => {
  it("preserves all known filter keys through create → read cycle", async () => {
    const filterSpec = {
      statuses: ["error", "replay_failed", "replay_pending"],
      range: "7d",
      q: "something interesting",
      channels: "gmail,telegram",
    };
    mockJsonResponse(makeSavedViewEntry({ filter_spec: filterSpec }), 201);

    const created = await createTimelineSavedView({
      name: "Round-trip test",
      filter_spec: filterSpec,
    });

    expect(created.filter_spec.statuses).toEqual(filterSpec.statuses);
    expect(created.filter_spec.range).toBe("7d");
    expect(created.filter_spec.q).toBe("something interesting");
    expect(created.filter_spec.channels).toBe("gmail,telegram");
  });

  it("preserves extra unknown keys (forward compat)", async () => {
    const filterSpec = {
      statuses: ["ingested"],
      range: "24h",
      custom_future_key: "some-value",
    };
    const entry = {
      id: "550e8400-e29b-41d4-a716-446655440000",
      name: "Future view",
      filter_spec: filterSpec,
      created_at: "2026-06-01T10:00:00Z",
      updated_at: "2026-06-01T10:00:00Z",
    };
    mockJsonResponse({ data: [entry], meta: {} });

    const result = await listTimelineSavedViews();
    const spec = result.data[0].filter_spec;
    expect(spec.custom_future_key).toBe("some-value");
  });
});
