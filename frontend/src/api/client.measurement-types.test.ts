import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockResponse(data: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: { get: () => "application/json" },
  });
}

import { getMeasurementTypes } from "./client.ts";

describe("getMeasurementTypes", () => {
  it("returns the typed observed vocabulary from the Health endpoint", async () => {
    const vocabulary = {
      types: [
        {
          type: "hrv",
          label: "HRV",
          sample_count: 28,
          latest_at: "2026-07-20T00:00:00Z",
          unit: "ms",
          value_shape: "scalar",
          chart_eligible: true,
          kpi_eligible: false,
        },
      ],
    };
    mockResponse(vocabulary);

    await expect(getMeasurementTypes()).resolves.toEqual(vocabulary);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain("/health/measurements/types");
  });
});
