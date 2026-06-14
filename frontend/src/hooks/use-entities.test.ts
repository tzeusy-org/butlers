/**
 * Unit tests for use-entities relationship-entity mutation hooks (bu-j820n.3).
 *
 * Strategy: mock @tanstack/react-query's useMutation + useQueryClient, capture
 * the options object passed by each hook, then call onSuccess directly to
 * verify cache invalidation.
 *
 * Focus: the merge (and forget) mutations must ALSO invalidate the
 * ["memory-entity", id] cache key that the entity DETAIL page reads
 * (use-memory.ts useEntity), not only the ["relationship-entity", id] key —
 * otherwise the detail page shows stale pre-merge data until a manual reload.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query BEFORE importing hooks.
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Mock API client functions used by the hooks under test.
// ---------------------------------------------------------------------------

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    mergeRelationshipEntities: vi.fn(),
    forgetRelationshipEntity: vi.fn(),
  };
});

// ---------------------------------------------------------------------------
// Import hooks and the mocked module AFTER mocks are set up.
// ---------------------------------------------------------------------------

import { useMutation } from "@tanstack/react-query";
import {
  useMergeRelationshipEntities,
  useForgetRelationshipEntity,
} from "@/hooks/use-entities.ts";
import type { MergeRelationshipEntitiesRequest } from "@/api/index.ts";

const mockUseMutation = vi.mocked(useMutation);

/**
 * Call the hook-under-test (which calls mockUseMutation) and return the
 * options object captured by the mock.
 */
function capturedMutationOptions(): {
  mutationFn: (...args: unknown[]) => unknown;
  onSuccess: (...args: unknown[]) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

// ---------------------------------------------------------------------------
// useMergeRelationshipEntities
// ---------------------------------------------------------------------------

describe("useMergeRelationshipEntities", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  const request: MergeRelationshipEntitiesRequest = {
    entityA: "entity-a-uuid",
    entityB: "entity-b-uuid",
    keepAs: "A",
  };

  it("onSuccess invalidates the relationship-entity keys for both ids", () => {
    useMergeRelationshipEntities();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, request, undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["relationship-entity", "entity-a-uuid"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["relationship-entity", "entity-b-uuid"],
    });
  });

  it("onSuccess ALSO invalidates the memory-entity detail-page key for both ids", () => {
    useMergeRelationshipEntities();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, request, undefined);

    // The entity detail page (use-memory.ts useEntity) reads ["memory-entity", id].
    // Both the surviving and merged-away ids must be invalidated so whichever
    // the route shows refreshes immediately rather than showing stale data.
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-entity", "entity-a-uuid"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-entity", "entity-b-uuid"],
    });
  });

  it("onSuccess invalidates the relationship index/queue/finder caches", () => {
    useMergeRelationshipEntities();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, request, undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["relationship-entities"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["relationship-entity-queue"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["entity-finder-search"],
    });
  });
});

// ---------------------------------------------------------------------------
// useForgetRelationshipEntity (use-entities.ts variant)
// ---------------------------------------------------------------------------

describe("useForgetRelationshipEntity (use-entities)", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it("onSuccess invalidates the relationship-entity key for the forgotten id", () => {
    useForgetRelationshipEntity();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, "forgotten-uuid", undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["relationship-entity", "forgotten-uuid"],
    });
  });

  it("onSuccess ALSO invalidates the memory-entity detail-page key", () => {
    useForgetRelationshipEntity();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, "forgotten-uuid", undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory-entity", "forgotten-uuid"],
    });
  });
});
