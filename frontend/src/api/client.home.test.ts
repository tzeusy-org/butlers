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

describe("submitHomePersonMappings", () => {
  it("sends the opaque key in a header and the exact batch in the body", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        data: {
          receipt: "00000000-0000-4000-8000-000000000001",
          complete: true,
          received_count: 1,
          created_count: 1,
          unchanged_count: 0,
          conflict_count: 0,
          invalid_reference_count: 0,
        },
        meta: {},
      }),
    );
    const mappings = [
      {
        ha_person_id: "person.private_client_fixture",
        entity_id: "00000000-0000-4000-8000-000000000002",
      },
    ];

    await client.submitHomePersonMappings(mappings, "a".repeat(43));

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/home/person-mappings");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "idempotency-key": "a".repeat(43) });
    expect(JSON.parse(init.body as string)).toEqual({ mappings });
  });

  it("surfaces only the aggregate refusal and never copies identifiers into the URL", async () => {
    const aggregate = {
      receipt: "00000000-0000-4000-8000-000000000003",
      complete: false,
      received_count: 1,
      created_count: 0,
      unchanged_count: 0,
      conflict_count: 1,
      invalid_reference_count: 0,
    };
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: async () => ({
        error: {
          code: "MAPPING_CONFLICT",
          message: "Mapping request conflicts with existing state.",
          butler: null,
          details: aggregate,
        },
      }),
    });
    const mappings = [
      {
        ha_person_id: "person.private_client_refusal",
        entity_id: "00000000-0000-4000-8000-000000000004",
      },
    ];

    const error = await client
      .submitHomePersonMappings(mappings, "b".repeat(43))
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(client.ApiError);
    expect(error).toMatchObject({ code: "MAPPING_CONFLICT", status: 409, detail: aggregate });
    expect(mockFetch.mock.calls[0][0]).toBe("/api/home/person-mappings");
    const surfaced = JSON.stringify([String(error), (error as client.ApiError).detail]);
    for (const { ha_person_id, entity_id } of mappings) {
      expect(surfaced).not.toContain(ha_person_id);
      expect(surfaced).not.toContain(entity_id);
    }
  });
});
