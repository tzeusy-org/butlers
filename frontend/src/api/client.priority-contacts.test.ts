/**
 * Tests for the priority-contacts API client functions.
 *
 * These target the runtime source of truth for priority senders —
 * public.priority_contacts — exposed at /api/ingestion/priority-contacts.
 *
 * priority_contacts is butler-agnostic (bu-gx13h).
 *
 * Verifies:
 * - getPriorityContacts hits GET /api/ingestion/priority-contacts and returns
 *   the PaginatedResponse envelope.
 * - addPriorityContact POSTs {contact_id}.
 * - removePriorityContact DELETEs /{contact_id}.
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

function mockEmptyResponse(status = 204) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status,
    json: async () => undefined,
    text: async () => "",
    headers: { get: () => null },
  });
}

import {
  addPriorityContact,
  getPriorityContacts,
  removePriorityContact,
} from "./client.ts";

describe("getPriorityContacts", () => {
  it("requests the priority-contacts endpoint", async () => {
    mockJsonResponse({ data: [], meta: { total: 0, offset: 0, limit: 100 } });
    await getPriorityContacts();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/ingestion/priority-contacts");
    expect(url).not.toContain("butler=");
  });

  it("returns the paginated envelope of priority-contact entries", async () => {
    const entry = {
      contact_id: "11111111-1111-1111-1111-111111111111",
      added_at: "2026-01-01T00:00:00Z",
      added_by: "dashboard",
      name: "VIP",
      contact_info_values: ["vip@example.com"],
      is_inert: false,
    };
    mockJsonResponse({ data: [entry], meta: { total: 1, offset: 0, limit: 100 } });
    const resp = await getPriorityContacts();
    expect(resp.data).toHaveLength(1);
    expect(resp.data[0].contact_id).toBe(entry.contact_id);
    expect(resp.data[0].contact_info_values).toEqual(["vip@example.com"]);
    expect(resp.data[0].is_inert).toBe(false);
  });

  it("passes pagination params when provided", async () => {
    mockJsonResponse({ data: [], meta: { total: 0, offset: 10, limit: 50 } });
    await getPriorityContacts({ offset: 10, limit: 50 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("offset=10");
    expect(url).toContain("limit=50");
  });
});

describe("addPriorityContact", () => {
  it("POSTs contact_id to the priority-contacts endpoint", async () => {
    mockJsonResponse(
      {
        contact_id: "22222222-2222-2222-2222-222222222222",
        added_at: "2026-01-01T00:00:00Z",
        added_by: "dashboard",
      },
      201,
    );
    await addPriorityContact({
      contact_id: "22222222-2222-2222-2222-222222222222",
    });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/api/ingestion/priority-contacts");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      contact_id: "22222222-2222-2222-2222-222222222222",
    });
  });
});

describe("removePriorityContact", () => {
  it("DELETEs /{contact_id}", async () => {
    mockEmptyResponse(204);
    await removePriorityContact("33333333-3333-3333-3333-333333333333");
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain(
      "/api/ingestion/priority-contacts/33333333-3333-3333-3333-333333333333",
    );
    expect(url).not.toContain("/gmail");
    expect(init.method).toBe("DELETE");
  });
});
