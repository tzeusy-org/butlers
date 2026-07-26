import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import * as client from "./client.ts";

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("updateHomeAtmosphereLocation", () => {
  it("PATCHes the exact latitude and longitude to the home atmosphere endpoint", async () => {
    const updateHomeAtmosphereLocation = (
      client as unknown as {
        updateHomeAtmosphereLocation?: (coordinates: {
          latitude: number;
          longitude: number;
        }) => Promise<unknown>;
      }
    ).updateHomeAtmosphereLocation;

    expect(updateHomeAtmosphereLocation).toBeTypeOf("function");
    if (!updateHomeAtmosphereLocation) return;

    mockFetch.mockResolvedValueOnce(
      jsonResponse({ latitude: 1.3521, longitude: 103.8198 }),
    );

    await updateHomeAtmosphereLocation({ latitude: 1.3521, longitude: 103.8198 });

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/home/atmosphere/location");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({
      latitude: 1.3521,
      longitude: 103.8198,
    });
  });
});
