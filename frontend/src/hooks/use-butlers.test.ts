/**
 * Unit tests for the new operator-verb mutations on use-butlers.ts
 * (JARVIS audit move 6, bu-86c4c.15): usePingButler and useForceButlerTick.
 *
 * Strategy: mock @tanstack/react-query's useMutation + useQueryClient, capture
 * the options object passed by each hook, and verify the wired API call plus
 * cache invalidation on settle.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

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

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getButler: vi.fn(),
    forceButlerTick: vi.fn(),
  };
});

import { useMutation } from "@tanstack/react-query";
import { forceButlerTick, getButler } from "@/api/index.ts";
import { useForceButlerTick, usePingButler } from "@/hooks/use-butlers";

const mockUseMutation = vi.mocked(useMutation);

function capturedMutationOptions(): {
  mutationFn: (...args: unknown[]) => unknown;
  onSettled?: (...args: unknown[]) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("usePingButler", () => {
  it("calls getButler with the butler name", () => {
    usePingButler();
    const opts = capturedMutationOptions();
    opts.mutationFn("general");
    expect(getButler).toHaveBeenCalledWith("general");
  });

  it("invalidates the issues feed on settle so a resolved reachability issue clears", () => {
    usePingButler();
    const opts = capturedMutationOptions();
    opts.onSettled?.();
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["issues"] });
  });
});

describe("useForceButlerTick", () => {
  it("calls forceButlerTick with the butler name", () => {
    useForceButlerTick();
    const opts = capturedMutationOptions();
    opts.mutationFn("general");
    expect(forceButlerTick).toHaveBeenCalledWith("general");
  });

  it("invalidates the butlers board and issues feed on settle", () => {
    useForceButlerTick();
    const opts = capturedMutationOptions();
    opts.onSettled?.();
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["butlers"] });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["issues"] });
  });
});
