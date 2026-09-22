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
const mockUseQuery = vi.hoisted(() => vi.fn((opts: unknown) => opts));
const mockUseInfiniteQuery = vi.hoisted(() => vi.fn((opts: unknown) => opts));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((opts: unknown) => opts),
    useInfiniteQuery: mockUseInfiniteQuery,
    useQuery: mockUseQuery,
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
    getEntityActivity: vi.fn(),
    createEntityNote: vi.fn(),
    createEntityInteraction: vi.fn(),
    createEntityGift: vi.fn(),
    mergeRelationshipEntities: vi.fn(),
    forgetRelationshipEntity: vi.fn(),
  };
});

// ---------------------------------------------------------------------------
// Import hooks and the mocked module AFTER mocks are set up.
// ---------------------------------------------------------------------------

import { useMutation } from "@tanstack/react-query";
import {
  useCreateEntityGift,
  useCreateEntityInteraction,
  useCreateEntityNote,
  useAddEntityContact,
  useEntityActivity,
  useUpdateEntityDunbarTier,
  useMergeRelationshipEntities,
  useForgetRelationshipEntity,
} from "@/hooks/use-entities.ts";
import type { MergeRelationshipEntitiesRequest } from "@/api/index.ts";

const mockUseMutation = vi.mocked(useMutation);

describe("useEntityActivity", () => {
  beforeEach(() => {
    mockUseInfiniteQuery.mockClear();
  });

  it("uses the canonical cache key and forwards TanStack cancellation", async () => {
    const { getEntityActivity } = await import("@/api/index.ts");
    const activity = vi.mocked(getEntityActivity);
    activity.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      degraded: false,
      degraded_reason: null,
    });

    useEntityActivity("entity-001", { limit: 50 });
    const options = mockUseInfiniteQuery.mock.calls.at(-1)?.[0] as {
      queryKey: unknown[];
      queryFn: (context: { signal: AbortSignal; pageParam: number }) => Promise<unknown>;
      getNextPageParam: (
        page: { items: unknown[]; total: number; offset: number },
        pages: Array<{ items: unknown[]; total: number; offset: number }>,
      ) => number | undefined;
    };
    const signal = new AbortController().signal;

    expect(options.queryKey).toEqual(["entity-activity", "entity-001", 50]);
    await options.queryFn({ signal, pageParam: 200 });
    expect(activity).toHaveBeenCalledWith("entity-001", { limit: 50, offset: 200, signal });
    const firstPage = {
      items: Array.from({ length: 200 }, (_, index) => ({
        id: `item-${index}`,
        src: "relationship",
        store: "narrative",
      })),
      total: 201,
      offset: 0,
    };
    const finalPage = { items: [{}], total: 201, offset: 200 };
    expect(options.getNextPageParam(firstPage, [firstPage])).toBe(200);
    expect(options.getNextPageParam(finalPage, [firstPage, finalPage])).toBeUndefined();
  });

  it.each([
    ["overlapping tuple identities", 300, "same-id"],
    ["a changed total", 299, "new-id"],
  ])("stops pagination after %s reveal a moving snapshot", (_label, secondTotal, secondId) => {
    useEntityActivity("entity-001", { limit: 1 });
    const options = mockUseInfiniteQuery.mock.calls.at(-1)?.[0] as {
      getNextPageParam: (
        page: { items: Array<Record<string, unknown>>; total: number; offset: number },
        pages: Array<{ items: Array<Record<string, unknown>>; total: number; offset: number }>,
      ) => number | undefined;
    };
    const firstPage = {
      items: [{ id: "same-id", src: "relationship", store: "narrative" }],
      total: 300,
      offset: 0,
    };
    const secondPage = {
      items: [{ id: secondId, src: "relationship", store: "narrative" }],
      total: secondTotal,
      offset: 1,
    };

    expect(options.getNextPageParam(secondPage, [firstPage, secondPage])).toBeUndefined();
  });
});

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

  it("onSuccess refreshes activity for survivor and tombstone", () => {
    useMergeRelationshipEntities();
    capturedMutationOptions().onSuccess(undefined, request, undefined);
    for (const entityId of ["entity-a-uuid", "entity-b-uuid"]) {
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity", entityId] });
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity-bins", entityId] });
    }
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

  it("onSuccess refreshes the forgotten entity activity family", () => {
    useForgetRelationshipEntity();
    capturedMutationOptions().onSuccess(undefined, "forgotten-uuid", undefined);
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity", "forgotten-uuid"] });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity-bins", "forgotten-uuid"] });
  });
});

describe("entity activity mutation invalidation", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it.each([
    ["note", useCreateEntityNote],
    ["interaction", useCreateEntityInteraction],
    ["gift", useCreateEntityGift],
  ])("%s success refreshes the stream and daily bins", (_label, useHook) => {
    useHook();
    const { onSuccess } = capturedMutationOptions();
    onSuccess(undefined, { entityId: "entity-001", request: {} }, undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["entity-activity", "entity-001"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["entity-activity-bins", "entity-001"],
    });
  });

  it.each([
    ["tier override", useUpdateEntityDunbarTier, { entityId: "entity-001", tier: 2 }],
    ["contact add", useAddEntityContact, { entityId: "entity-001", request: {} }],
  ])("%s refreshes the activity family", (_label, useHook, variables) => {
    useHook();
    capturedMutationOptions().onSuccess(undefined, variables, undefined);
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity", "entity-001"] });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["entity-activity-bins", "entity-001"] });
  });
});
