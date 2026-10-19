/**
 * A busy schedule trigger reaches the schedules tab with the daemon's message (bu-0w76ze).
 *
 * POST /api/butlers/{name}/schedules/{id}/trigger answers 409 with a string `detail` while a
 * scheduler tick is running. The tab's error toast shows `err.message`, so apiFetch must
 * surface that detail as the ApiError message rather than a generic failure.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import { ApiError, triggerButlerSchedule } from "./client.ts";

afterEach(() => {
  vi.clearAllMocks();
});

describe("triggerButlerSchedule", () => {
  it("rejects a busy 409 with the daemon's message", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: "a scheduler tick is already running; try again when it finishes" }),
    });

    const error = await triggerButlerSchedule("switchboard", "s-1").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe(
      "a scheduler tick is already running; try again when it finishes",
    );
  });
});
