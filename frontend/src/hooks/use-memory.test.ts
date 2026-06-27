/**
 * Unit tests for the memory fact mutation hooks (bu-3mxat).
 *
 * Strategy mirrors use-secrets-mutations.test.ts: mock @tanstack/react-query's
 * useMutation + useQueryClient, capture the options object each hook passes,
 * then invoke onSuccess directly to assert the cache invalidation set.
 *
 * Regression under test: useConfirmFact / useRetractFact MUST invalidate the
 * ["memory-stats"] query key so the KPI counts (active vs fading facts) do not
 * go stale after a confirm / retract, in addition to the single-fact and
 * facts-list caches.
 */

import { describe, expect, it, beforeEach, vi } from "vitest";

const mockInvalidateQueries = vi.fn();
const mockQueryClient = { invalidateQueries: mockInvalidateQueries };

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((opts: unknown) => opts),
    useQueryClient: () => mockQueryClient,
  };
});

import { useMutation } from "@tanstack/react-query";
import { useConfirmFact, useRetractFact } from "@/hooks/use-memory";

const mockUseMutation = vi.mocked(useMutation);

function capturedMutationOptions(): {
  mutationFn: (...args: unknown[]) => unknown;
  onSuccess: (...args: unknown[]) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

describe("useConfirmFact", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it("onSuccess invalidates the single-fact, facts-list, AND memory-stats caches", () => {
    useConfirmFact();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, "fact-001", undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-fact", "fact-001"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-facts"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-stats"],
    });
  });
});

describe("useRetractFact", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it("onSuccess invalidates the single-fact, facts-list, AND memory-stats caches", () => {
    useRetractFact();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, "fact-001", undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-fact", "fact-001"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-facts"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-stats"],
    });
  });
});
