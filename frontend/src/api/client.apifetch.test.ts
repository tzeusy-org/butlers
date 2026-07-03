/**
 * Tests for apiFetch's timeout + abort behavior (bu-86c4c.13, JARVIS audit
 * move 10). Every request now aborts after API_REQUEST_TIMEOUT_MS rather than
 * hanging indefinitely, and a caller-supplied AbortSignal (e.g. TanStack
 * Query's queryFn signal) is still honored so cancel-on-unmount/refetch
 * keeps working exactly as it did before this wrapper existed.
 *
 * Covers:
 *   - a request that resolves before the timeout is unaffected
 *   - a request that never resolves is aborted at the timeout and throws a
 *     TIMEOUT ApiError (not a raw AbortError)
 *   - the timeout is cleared on success (no leaked timer)
 *   - a caller-provided signal aborts the underlying fetch call
 *   - a caller-provided signal that is already aborted aborts immediately
 *   - a caller-initiated abort surfaces as the original AbortError, not
 *     wrapped into a TIMEOUT ApiError (so TanStack Query's own cancellation
 *     handling still recognizes it)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import { apiFetch, ApiError, API_REQUEST_TIMEOUT_MS } from "./client.ts";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("apiFetch — timeout", () => {
  it("resolves normally when the response arrives before the timeout", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));

    const result = await apiFetch("/health");

    expect(result).toEqual({ ok: true });
  });

  it("passes an AbortSignal to fetch even when the caller supplies no options", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await apiFetch("/health");

    const [, init] = mockFetch.mock.calls[0];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.signal.aborted).toBe(false);
  });

  it("aborts and throws a TIMEOUT ApiError when the request outlives the timeout", async () => {
    let capturedSignal: AbortSignal | undefined;
    mockFetch.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          capturedSignal = init.signal as AbortSignal;
          init.signal?.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );

    const promise = apiFetch("/slow");
    // Attach the rejection assertion before advancing timers so the rejection
    // is observed synchronously with the abort, avoiding an unhandled
    // rejection warning in between.
    const assertion = expect(promise).rejects.toMatchObject({
      name: "ApiError",
      code: "TIMEOUT",
      status: 0,
    });

    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS);

    await assertion;
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("clears the timeout on success so it never fires after the fact", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));
    const clearSpy = vi.spyOn(global, "clearTimeout");

    await apiFetch("/health");

    expect(clearSpy).toHaveBeenCalled();
  });
});

describe("apiFetch — caller-provided AbortSignal", () => {
  it("forwards a caller abort into the underlying fetch call", async () => {
    const callerController = new AbortController();
    let capturedSignal: AbortSignal | undefined;
    mockFetch.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          capturedSignal = init.signal as AbortSignal;
          init.signal?.addEventListener("abort", () => {
            const err = new Error("cancelled");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );

    const promise = apiFetch("/slow", { signal: callerController.signal });
    const assertion = expect(promise).rejects.toMatchObject({ name: "AbortError" });
    callerController.abort();

    await assertion;
    expect(capturedSignal?.aborted).toBe(true);
  });

  it("aborts immediately when the caller signal is already aborted", async () => {
    const callerController = new AbortController();
    callerController.abort();
    mockFetch.mockImplementationOnce(() =>
      Promise.reject(Object.assign(new Error("cancelled"), { name: "AbortError" })),
    );

    await expect(apiFetch("/slow", { signal: callerController.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
  });

  it("does not wrap a caller-initiated abort into a TIMEOUT ApiError", async () => {
    const callerController = new AbortController();
    mockFetch.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => {
            const err = new Error("cancelled");
            err.name = "AbortError";
            reject(err);
          });
        }),
    );

    const promise = apiFetch("/slow", { signal: callerController.signal });
    const assertion = expect(promise).rejects.not.toBeInstanceOf(ApiError);
    callerController.abort();
    await assertion;
  });
});
