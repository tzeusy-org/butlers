/**
 * Tests for travel API client functions — URL/querystring building [bu-0eac9].
 *
 * Verifies:
 * - getTravelTrips builds the correct path with no params
 * - getTravelTrips appends status, from_date, to_date, offset, limit
 * - getTravelTripSummary builds the correct path with URI encoding
 * - getTravelUpcoming builds the correct path with no params
 * - getTravelUpcoming appends within_days when provided
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

function mockResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: { get: () => "application/json" },
  });
}

const EMPTY_TRIPS = { items: [], total: 0, offset: 0, limit: 20 };
const EMPTY_UPCOMING = { upcoming_trips: [], actions: [], window_start: "", window_end: "" };
const TRIP_SUMMARY = {
  trip: { id: "t1", name: "Test", destination: "X", start_date: "2025-01-01", end_date: "2025-01-07", status: "planned", metadata: {}, created_at: "", updated_at: "" },
  legs: [], accommodations: [], reservations: [], documents: [], timeline: [], alerts: [],
};

import { getTravelTrips, getTravelTripSummary, getTravelUpcoming } from "./client.ts";

// ---------------------------------------------------------------------------
// getTravelTrips
// ---------------------------------------------------------------------------

describe("getTravelTrips — URL building", () => {
  it("uses bare /travel/trips path when no params given", async () => {
    mockResponse(EMPTY_TRIPS);
    await getTravelTrips();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/travel/trips");
    expect(url).not.toContain("?");
  });

  it("appends status param when provided", async () => {
    mockResponse(EMPTY_TRIPS);
    await getTravelTrips({ status: "planned" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("status=planned");
  });

  it("appends from_date and to_date when provided", async () => {
    mockResponse(EMPTY_TRIPS);
    await getTravelTrips({ from_date: "2025-01-01", to_date: "2025-03-31" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from_date=2025-01-01");
    expect(url).toContain("to_date=2025-03-31");
  });

  it("appends offset and limit when provided", async () => {
    mockResponse(EMPTY_TRIPS);
    await getTravelTrips({ offset: 20, limit: 10 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("offset=20");
    expect(url).toContain("limit=10");
  });

  it("combines all params in one request", async () => {
    mockResponse(EMPTY_TRIPS);
    await getTravelTrips({ status: "active", from_date: "2025-06-01", to_date: "2025-12-31", offset: 0, limit: 5 });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("status=active");
    expect(url).toContain("from_date=2025-06-01");
    expect(url).toContain("to_date=2025-12-31");
    expect(url).toContain("offset=0");
    expect(url).toContain("limit=5");
  });
});

// ---------------------------------------------------------------------------
// getTravelTripSummary
// ---------------------------------------------------------------------------

describe("getTravelTripSummary — URL building", () => {
  it("includes the trip ID in the path", async () => {
    mockResponse(TRIP_SUMMARY);
    await getTravelTripSummary("trip-abc-123");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/travel/trips/trip-abc-123");
  });

  it("URI-encodes special characters in trip ID", async () => {
    mockResponse(TRIP_SUMMARY);
    await getTravelTripSummary("trip/with spaces");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("trip%2Fwith%20spaces");
  });
});

// ---------------------------------------------------------------------------
// getTravelUpcoming
// ---------------------------------------------------------------------------

describe("getTravelUpcoming — URL building", () => {
  it("uses bare /travel/upcoming path when no params given", async () => {
    mockResponse(EMPTY_UPCOMING);
    await getTravelUpcoming();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/travel/upcoming");
    expect(url).not.toContain("?");
  });

  it("appends within_days when provided", async () => {
    mockResponse(EMPTY_UPCOMING);
    await getTravelUpcoming(30);
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("within_days=30");
  });
});
