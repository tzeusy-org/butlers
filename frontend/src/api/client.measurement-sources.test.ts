/**
 * Tests for the measurement-sources API client function.
 *
 * Regression guard: the backend returns an envelope `{ sources: [...] }`, but
 * the frontend consumers (HealthOverviewPage, ButlerHealthMeasurementsTab)
 * expect a bare array. `getMeasurementSources` must unwrap `.sources` so the
 * hook returns an array — otherwise `sources.map` throws at render time.
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

import { getMeasurementSources } from "./client.ts";

describe("getMeasurementSources", () => {
  it("unwraps the { sources } envelope into a bare array", async () => {
    const rows = [
      { name: "apple_health", last_sample_at: "2026-01-01T06:00:00Z", sample_count: 42 },
    ];
    mockResponse({ sources: rows });
    const result = await getMeasurementSources();
    expect(Array.isArray(result)).toBe(true);
    expect(result).toEqual(rows);
  });

  it("returns [] when the envelope has no sources", async () => {
    mockResponse({});
    const result = await getMeasurementSources();
    expect(result).toEqual([]);
  });
});
