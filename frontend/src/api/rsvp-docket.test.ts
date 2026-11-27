import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/api/client.ts", () => ({ apiFetch }));

import { DOCKET_CARD_TIMEOUT_MS, docketSubmitTimeoutMs, submitDocket } from "./rsvp-docket.ts";

afterEach(() => vi.clearAllMocks());

describe("submitDocket", () => {
  it("gives a batch every card's dispatch bound plus a margin, not the 15s default", async () => {
    apiFetch.mockResolvedValue({ data: { results: [], refused: [] } });
    const items = [
      { action_id: "a", args_digest: "d" },
      { action_id: "b", args_digest: "d" },
    ];
    await submitDocket(items);
    const options = apiFetch.mock.calls[0][1];
    expect(options.timeoutMs).toBe(docketSubmitTimeoutMs(2));
    expect(options.timeoutMs).toBeGreaterThan(2 * DOCKET_CARD_TIMEOUT_MS);
    expect(JSON.parse(options.body)).toEqual({ items });
  });
});
