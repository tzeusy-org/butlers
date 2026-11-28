import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/api/client.ts", () => ({ apiFetch }));

import { DOCKET_SUBMIT_TIMEOUT_MS, submitDocket } from "./rsvp-docket.ts";

afterEach(() => vi.clearAllMocks());

describe("submitDocket", () => {
  it("passes the fixed 270s timeout, under Firefox's 300s limit and over the server's 255s deadline", async () => {
    apiFetch.mockResolvedValue({ data: { results: [], refused: [] } });
    const items = [
      { action_id: "a", args_digest: "d" },
      { action_id: "b", args_digest: "d" },
    ];
    await submitDocket(items);
    const options = apiFetch.mock.calls[0][1];
    expect(options.timeoutMs).toBe(270_000);
    expect(DOCKET_SUBMIT_TIMEOUT_MS).toBeGreaterThan(255_000);
    expect(DOCKET_SUBMIT_TIMEOUT_MS).toBeLessThan(300_000);
    expect(JSON.parse(options.body)).toEqual({ items });
  });
});
