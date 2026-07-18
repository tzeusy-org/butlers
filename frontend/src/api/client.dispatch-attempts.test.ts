/**
 * Dispatch-attempt query modes: session provenance versus fleet-wide outcome.
 *
 * The compile-time assertions make the mutually exclusive public request
 * contract regress if either mode becomes broad enough to accept a mixed or
 * selector-less request. The fetch assertions cover the resulting wire shape.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { DispatchAttemptsParams } from "./types.ts";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({ data: [], meta: { total: 0, limit: 20, offset: 0 } }),
    text: async () => "",
    headers: { get: () => "application/json" },
  });
}

function queryForLatestRequest(): URLSearchParams {
  const url: string = mockFetch.mock.calls[0][0];
  return new URL(url, "http://localhost").searchParams;
}

import { getDispatchAttempts } from "./client.ts";

describe("DispatchAttemptsParams", () => {
  it("models session selectors and fleet outcome as exclusive modes", () => {
    const validModes: DispatchAttemptsParams[] = [
      { session_id: "session-1" },
      { logical_session_id: "logical-session-1" },
      { session_id: "session-1", logical_session_id: "logical-session-1" },
      {
        outcome: "quota_skip",
        reason_prefix: "Monthly spend ceiling reached",
        since: "2026-07-01T00:00:00.000Z",
        order: "desc",
        limit: 20,
      },
    ];

    // @ts-expect-error a request needs a session selector or fleet outcome
    const noMode: DispatchAttemptsParams = {};
    // @ts-expect-error fleet outcome cannot be combined with a session selector
    const mixedSessionAndFleet: DispatchAttemptsParams = { session_id: "session-1", outcome: "quota_skip" };
    // @ts-expect-error fleet outcome cannot be combined with a logical session selector
    const mixedLogicalSessionAndFleet: DispatchAttemptsParams = { logical_session_id: "logical-session-1", outcome: "quota_skip" };
    // @ts-expect-error fleet refinements require the fleet outcome selector
    const fleetFilterWithoutOutcome: DispatchAttemptsParams = { reason_prefix: "Monthly spend ceiling reached" };

    expect(validModes).toHaveLength(4);
    expect([noMode, mixedSessionAndFleet, mixedLogicalSessionAndFleet, fleetFilterWithoutOutcome]).toHaveLength(4);
  });

  it("serializes both session selectors together", async () => {
    mockResponse();

    await getDispatchAttempts({
      session_id: "session-1",
      logical_session_id: "logical-session-1",
      limit: 12,
    });

    const query = queryForLatestRequest();
    expect(query.get("session_id")).toBe("session-1");
    expect(query.get("logical_session_id")).toBe("logical-session-1");
    expect(query.get("limit")).toBe("12");
    expect(query.has("outcome")).toBe(false);
  });

  it("serializes fleet outcome filters without session selectors", async () => {
    mockResponse();

    await getDispatchAttempts({
      outcome: "quota_skip",
      reason_prefix: "Monthly spend ceiling reached",
      since: "2026-07-01T00:00:00.000Z",
      order: "desc",
      limit: 20,
    });

    const query = queryForLatestRequest();
    expect(query.get("outcome")).toBe("quota_skip");
    expect(query.get("reason_prefix")).toBe("Monthly spend ceiling reached");
    expect(query.get("since")).toBe("2026-07-01T00:00:00.000Z");
    expect(query.get("order")).toBe("desc");
    expect(query.get("limit")).toBe("20");
    expect(query.has("session_id")).toBe(false);
    expect(query.has("logical_session_id")).toBe(false);
  });
});
