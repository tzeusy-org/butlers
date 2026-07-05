// @vitest-environment jsdom

/**
 * Unit tests for useSearch's never-blank floor wiring (bu-nhcp5).
 *
 * Strategy: mock @tanstack/react-query's useQuery and capture the options
 * object useSearch passes it, mirroring use-butlers.test.ts's approach for
 * useMutation. This verifies the placeholderData wiring directly without
 * needing to drive the real 300ms debounce timer through a full render.
 */

import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn(() => ({ data: undefined, isFetching: false })),
  };
});

vi.mock("@/api/index.ts", () => ({
  searchAll: vi.fn(),
}));

import { useQuery } from "@tanstack/react-query";
import { useSearch } from "@/hooks/use-search";

const mockUseQuery = vi.mocked(useQuery);

function lastQueryOptions(): { placeholderData?: (prev: unknown) => unknown } {
  const calls = mockUseQuery.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof lastQueryOptions>;
}

describe("useSearch — never-blank floor (bu-nhcp5)", () => {
  it("pairs the debounced query key with placeholderData so a keystroke never blanks the previous results", () => {
    renderHook(() => useSearch("hello"));
    const opts = lastQueryOptions();
    expect(typeof opts.placeholderData).toBe("function");
  });

  it("placeholderData returns the previous page's data unchanged (the keepPreviousData idiom)", () => {
    renderHook(() => useSearch("hello"));
    const opts = lastQueryOptions();
    const previous = { data: { sessions: [{ id: "s1", title: "a", url: "/a" }], state: [] } };
    expect(opts.placeholderData?.(previous)).toBe(previous);
  });
});
