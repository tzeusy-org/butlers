/**
 * verifyAllModels waits for the server's serialized sweep instead of the default 15s read
 * timeout (bu-3rd8wy): a sweep longer than 15s must not read as a timeout, and a hung one still
 * aborts at the derived worst case.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import {
  API_REQUEST_TIMEOUT_MS,
  VERIFY_ALL_MAX_SERIAL_PROBES,
  VERIFY_ALL_PROBE_BUDGET_MS,
  VERIFY_ALL_TIMEOUT_MS,
  verifyAllModels,
} from "./client.ts";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

function pendingUntilAborted() {
  return (_url: string, init: RequestInit) =>
    new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
}

describe("verifyAllModels timeout", () => {
  it("derives the timeout from the per-probe budget and the longest serial group", () => {
    expect(VERIFY_ALL_TIMEOUT_MS).toBe(VERIFY_ALL_PROBE_BUDGET_MS * VERIFY_ALL_MAX_SERIAL_PROBES);
    expect(VERIFY_ALL_TIMEOUT_MS).toBeGreaterThan(API_REQUEST_TIMEOUT_MS);
  });

  it("does not time out a sweep that outlasts the default 15s read timeout", async () => {
    const result = { data: { accepted: true, total: 3, ok: 3, failed: 0, unavailable: 0 } };
    mockFetch.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new Error("aborted")));
          setTimeout(
            () => resolve({ ok: true, status: 200, json: async () => result }),
            API_REQUEST_TIMEOUT_MS * 4,
          );
        }),
    );

    const promise = verifyAllModels();
    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS * 4);

    await expect(promise).resolves.toEqual(result);
  });

  it("still aborts a hung sweep at the derived worst case", async () => {
    mockFetch.mockImplementationOnce(pendingUntilAborted());

    const promise = verifyAllModels();
    const assertion = expect(promise).rejects.toMatchObject({ code: "TIMEOUT" });
    await vi.advanceTimersByTimeAsync(VERIFY_ALL_TIMEOUT_MS - 1);
    await vi.advanceTimersByTimeAsync(1);

    await assertion;
  });
});
