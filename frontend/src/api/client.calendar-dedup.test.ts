// @vitest-environment jsdom
/**
 * Tests for the calendar duplicate-review client (bu-fol6y):
 *  - `getCalendarWorkspaceDuplicates` — view/range/filter param serialization
 *  - `patchCalendarDedupRules` — PATCH method + body passthrough
 *  - `setCalendarKeepSeparate` — POST to the keep-separate override endpoint
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockJson(body: unknown) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

import {
  getCalendarWorkspaceDuplicates,
  patchCalendarDedupRules,
  setCalendarKeepSeparate,
} from "./client.ts";

describe("getCalendarWorkspaceDuplicates", () => {
  it("serializes view, range, timezone, butlers, and sources", async () => {
    mockJson({ data: { clusters: [], rules: { match_strategy: "balanced", noisy_threshold: 2 }, available: true } });
    await getCalendarWorkspaceDuplicates({
      view: "butler",
      start: "2026-02-22T00:00:00Z",
      end: "2026-02-23T00:00:00Z",
      timezone: "Asia/Singapore",
      butlers: ["finance", "general"],
      sources: ["google:primary"],
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/calendar/workspace/duplicates?");
    expect(url).toContain("view=butler");
    expect(url).toContain("timezone=Asia%2FSingapore");
    expect(url).toContain("butlers=finance");
    expect(url).toContain("butlers=general");
    expect(url).toContain("sources=google%3Aprimary");
  });

  it("omits optional params when not provided", async () => {
    mockJson({ data: { clusters: [], rules: { match_strategy: "balanced", noisy_threshold: 2 }, available: true } });
    await getCalendarWorkspaceDuplicates({
      view: "user",
      start: "2026-02-22T00:00:00Z",
      end: "2026-02-23T00:00:00Z",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("timezone=");
    expect(url).not.toContain("butlers=");
    expect(url).not.toContain("sources=");
  });
});

describe("patchCalendarDedupRules", () => {
  it("PATCHes the dedup-rules endpoint with the partial body", async () => {
    mockJson({ data: { match_strategy: "aggressive", noisy_threshold: 3 } });
    await patchCalendarDedupRules({ match_strategy: "aggressive", noisy_threshold: 3 });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/calendar/workspace/dedup-rules");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ match_strategy: "aggressive", noisy_threshold: 3 });
  });
});

describe("setCalendarKeepSeparate", () => {
  it("POSTs to the keep-separate override endpoint", async () => {
    mockJson({ data: { cluster_key: "k", keep_separate: true } });
    await setCalendarKeepSeparate({
      cluster_key: "k",
      keep_separate: true,
      match_pass: "title",
      label: "Team standup",
    });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/calendar/workspace/duplicates/keep-separate");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      cluster_key: "k",
      keep_separate: true,
      match_pass: "title",
      label: "Team standup",
    });
  });
});
