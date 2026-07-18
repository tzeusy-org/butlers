/**
 * Unit tests for the shared optimistic-mutation primitives (bu-86c4c.13).
 *
 * Strategy mirrors use-issues.test.ts (the hook this was extracted from):
 * mock @tanstack/react-query's useMutation + useQueryClient, capture the
 * options object passed to useMutation, then drive onMutate/onError/onSettled
 * directly against a fake QueryClient double to verify cache behaviour.
 *
 * Covers:
 *   - useOptimisticMutation: cancel -> apply -> context threading
 *   - useOptimisticMutation: rollback runs on error, using the onMutate snapshot
 *   - useOptimisticMutation: invalidate keys computed from variables + data
 *   - useOptimisticMutation: extra onSuccess/onError callbacks still fire
 *   - useOptimisticListMutation: filters items in every list matching the
 *     prefix (multi cache-entry, e.g. two different filter params)
 *   - useOptimisticListMutation: rollback restores every touched key verbatim
 *   - useOptimisticListMutation: defaults invalidation to the list prefix,
 *     but an explicit broader prefix overrides it (active+dismissed sibling)
 */

import { useMutation } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockCancelQueries = vi.fn(() => Promise.resolve());
const mockInvalidateQueries = vi.fn();
const mockGetQueriesData = vi.fn();
const mockSetQueriesData = vi.fn();
const mockSetQueryData = vi.fn();
const mockQueryClient = {
  cancelQueries: mockCancelQueries,
  invalidateQueries: mockInvalidateQueries,
  getQueriesData: mockGetQueriesData,
  setQueriesData: mockSetQueriesData,
  setQueryData: mockSetQueryData,
};

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((opts: unknown) => opts),
    useQueryClient: () => mockQueryClient,
  };
});

import {
  rollbackLists,
  snapshotAndUpdateQueries,
  useOptimisticListMutation,
  useOptimisticMutation,
} from "./use-optimistic-mutation";

const mockUseMutation = vi.mocked(useMutation);

interface CapturedOptions<TData, TVariables, TContext> {
  mutationFn: (variables: TVariables) => Promise<TData>;
  onMutate: (variables: TVariables) => Promise<TContext>;
  onError: (error: Error, variables: TVariables, context: TContext) => void;
  onSuccess: (data: TData, variables: TVariables) => void;
  onSettled: (data: TData | undefined, error: unknown, variables: TVariables) => void;
}

function lastOptions<TData = unknown, TVariables = unknown, TContext = unknown>() {
  const calls = mockUseMutation.mock.calls;
  return calls[calls.length - 1][0] as unknown as CapturedOptions<TData, TVariables, TContext>;
}

describe("useOptimisticMutation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("cancels the declared keys and applies the optimistic update in onMutate", async () => {
    const applyOptimisticUpdate = vi.fn(() => "snapshot-value");
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve(id),
      cancelQueryKeys: (id) => [["thing", id]],
      applyOptimisticUpdate,
      rollback: vi.fn(),
    });

    const context = await lastOptions().onMutate("abc");

    expect(mockCancelQueries).toHaveBeenCalledWith({ queryKey: ["thing", "abc"] });
    expect(applyOptimisticUpdate).toHaveBeenCalledWith("abc", mockQueryClient);
    expect(context).toEqual({ snapshot: "snapshot-value" });
  });

  it("rolls back using the onMutate snapshot and still runs the caller's onError", () => {
    const rollback = vi.fn();
    const onError = vi.fn();
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve(id),
      applyOptimisticUpdate: () => "snap",
      rollback,
      onError,
    });

    lastOptions().onError(new Error("boom"), "abc", { snapshot: "snap" });

    expect(rollback).toHaveBeenCalledWith("snap", mockQueryClient);
    expect(onError).toHaveBeenCalledWith(expect.any(Error), "abc");
  });

  it("does not roll back when onMutate never ran (no context)", () => {
    const rollback = vi.fn();
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve(id),
      applyOptimisticUpdate: () => "snap",
      rollback,
    });

    lastOptions().onError(
      new Error("boom"),
      "abc",
      undefined as unknown as { snapshot: string },
    );

    expect(rollback).not.toHaveBeenCalled();
  });

  it("keeps an optimistic resolution when the mutation classifies an error as already resolved", () => {
    const rollback = vi.fn();
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve(id),
      applyOptimisticUpdate: () => "snap",
      rollback,
      shouldRollback: () => false,
    });

    lastOptions().onError(new Error("already resolved"), "abc", { snapshot: "snap" });

    expect(rollback).not.toHaveBeenCalled();
  });

  it("invalidates keys computed from variables and the settled data", () => {
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve({ ok: true, id }),
      applyOptimisticUpdate: () => undefined,
      rollback: vi.fn(),
      invalidateQueryKeys: (id, data) => [["thing", id], ["thing-status", data?.id]],
    });

    lastOptions().onSettled({ ok: true, id: "abc" }, null, "abc");

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["thing", "abc"] });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["thing-status", "abc"] });
  });

  it("calls the caller's onSuccess with data and variables", () => {
    const onSuccess = vi.fn();
    useOptimisticMutation({
      mutationFn: (id: string) => Promise.resolve(id),
      applyOptimisticUpdate: () => undefined,
      rollback: vi.fn(),
      onSuccess,
    });

    lastOptions().onSuccess("result", "abc");

    expect(onSuccess).toHaveBeenCalledWith("result", "abc");
  });
});

describe("snapshotAndUpdateQueries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("snapshots every matching structured cache entry before applying an arbitrary update", () => {
    const snapshot: [readonly unknown[], unknown][] = [
      [["workspace", { view: "user" }], { data: { entries: [{ id: "proposal-1" }] } }],
      [["workspace", { view: "butler" }], { data: { entries: [{ id: "proposal-2" }] } }],
    ];
    mockGetQueriesData.mockReturnValue(snapshot);

    const captured = snapshotAndUpdateQueries(
      mockQueryClient as never,
      ["workspace"],
      (old: { data: { entries: Array<{ id: string }> } } | undefined) =>
        old
          ? { ...old, data: { ...old.data, entries: old.data.entries.filter((entry) => entry.id !== "proposal-1") } }
          : old,
    );

    expect(captured).toEqual(snapshot);
    expect(mockSetQueriesData).toHaveBeenCalledWith(
      { queryKey: ["workspace"] },
      expect.any(Function),
    );

    const updater = mockSetQueriesData.mock.calls[0][1] as (old: unknown) => unknown;
    expect(updater({ data: { entries: [{ id: "proposal-1" }, { id: "proposal-2" }] } })).toEqual({
      data: { entries: [{ id: "proposal-2" }] },
    });

    rollbackLists(mockQueryClient as never, captured);
    expect(mockSetQueryData).toHaveBeenCalledWith(
      ["workspace", { view: "user" }],
      { data: { entries: [{ id: "proposal-1" }] } },
    );
  });
});

interface Item {
  id: string;
  label: string;
}

describe("useOptimisticListMutation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("cancels and snapshots every cached query under the list prefix", async () => {
    const snapshot: [readonly unknown[], unknown][] = [
      [["things", { filter: "a" }], { data: [{ id: "1", label: "one" }] }],
      [["things", { filter: "b" }], { data: [{ id: "2", label: "two" }] }],
    ];
    mockGetQueriesData.mockReturnValue(snapshot);

    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: ["things"],
      updateItems: (items, id) => items.filter((item) => item.id !== id),
    });

    const context = await lastOptions().onMutate("1");

    expect(mockCancelQueries).toHaveBeenCalledWith({ queryKey: ["things"] });
    expect(mockGetQueriesData).toHaveBeenCalledWith({ queryKey: ["things"] });
    expect(mockSetQueriesData).toHaveBeenCalledWith(
      { queryKey: ["things"] },
      expect.any(Function),
    );

    // Drive the updater TanStack would call per matched cache entry.
    const updater = mockSetQueriesData.mock.calls[0][1] as (old: unknown) => unknown;
    expect(updater({ data: [{ id: "1", label: "one" }] })).toEqual({
      data: [],
    });
    expect(updater({ data: [{ id: "2", label: "two" }] })).toEqual({
      data: [{ id: "2", label: "two" }],
    });
    // Non-list-shaped cache entries (no array `data`) pass through untouched.
    expect(updater("not-a-list")).toBe("not-a-list");
    // A bare array (e.g. getLabels, which isn't envelope-wrapped) is
    // updated directly rather than being treated as "not a list".
    expect(updater([{ id: "1", label: "one" }])).toEqual([]);

    expect(context).toEqual({ snapshot });
  });

  it("rolls back every snapshotted key verbatim on error", () => {
    const snapshot: [readonly unknown[], unknown][] = [
      [["things", { filter: "a" }], { data: [{ id: "1", label: "one" }] }],
      [["things", { filter: "b" }], { data: [{ id: "2", label: "two" }] }],
    ];

    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: ["things"],
      updateItems: (items, id) => items.filter((item) => item.id !== id),
    });

    lastOptions().onError(new Error("boom"), "1", { snapshot });

    expect(mockSetQueryData).toHaveBeenCalledWith(
      ["things", { filter: "a" }],
      { data: [{ id: "1", label: "one" }] },
    );
    expect(mockSetQueryData).toHaveBeenCalledWith(
      ["things", { filter: "b" }],
      { data: [{ id: "2", label: "two" }] },
    );
  });

  it("defaults invalidation to the list prefix", () => {
    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: ["things"],
      updateItems: (items) => items,
    });

    lastOptions().onSettled(undefined, null, "1");

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["things"] });
    expect(mockInvalidateQueries).toHaveBeenCalledTimes(1);
  });

  it("honors an explicit broader invalidation prefix (sibling views refresh together)", () => {
    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: ["things", { dismissed: false }],
      updateItems: (items) => items,
      invalidateQueryKeys: [["things"]],
    });

    lastOptions().onSettled(undefined, null, "1");

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["things"] });
    expect(mockInvalidateQueries).toHaveBeenCalledTimes(1);
  });

  it("mirrors an item update across multiple list-key namespaces at once", async () => {
    mockGetQueriesData.mockImplementation(({ queryKey }: { queryKey: readonly unknown[] }) => {
      if (queryKey[0] === "notifications") {
        return [[["notifications"], { data: [{ id: "1", label: "one" }] }]];
      }
      return [[["butler-notifications", "finance"], { data: [{ id: "1", label: "one" }] }]];
    });

    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: [["notifications"], ["butler-notifications"]],
      updateItems: (items, id) => items.filter((item) => item.id !== id),
    });

    const context = await lastOptions().onMutate("1");

    expect(mockCancelQueries).toHaveBeenCalledWith({ queryKey: ["notifications"] });
    expect(mockCancelQueries).toHaveBeenCalledWith({ queryKey: ["butler-notifications"] });
    expect(mockSetQueriesData).toHaveBeenCalledWith(
      { queryKey: ["notifications"] },
      expect.any(Function),
    );
    expect(mockSetQueriesData).toHaveBeenCalledWith(
      { queryKey: ["butler-notifications"] },
      expect.any(Function),
    );
    // Snapshot is the concatenation of both namespaces' matched entries.
    expect((context as { snapshot: unknown[] }).snapshot).toHaveLength(2);

    lastOptions().onSettled(undefined, null, "1");
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["notifications"] });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["butler-notifications"] });
  });

  it("snapshots every prefix before mutating any of them, even when prefixes overlap", async () => {
    // A single cached query ["things", "urgent"] matches BOTH declared
    // prefixes below (["things"] is a strict prefix of it). A naive
    // snapshot-then-update-per-prefix loop would process ["things"] first —
    // mutating the shared entry — then snapshot ["things", "urgent"] against
    // the ALREADY-mutated data, silently corrupting rollback. Model the cache
    // as real (mutable, shared) state so the ordering bug would actually show.
    const cache = new Map<string, unknown>([
      [JSON.stringify(["things", "urgent"]), { data: [{ id: "1", label: "one" }] }],
    ]);
    const isPrefixOf = (prefix: unknown[], key: unknown[]) =>
      JSON.stringify(key.slice(0, prefix.length)) === JSON.stringify(prefix);

    mockGetQueriesData.mockImplementation(({ queryKey: prefix }: { queryKey: unknown[] }) =>
      [...cache.entries()]
        .filter(([key]) => isPrefixOf(prefix, JSON.parse(key)))
        .map(([key, value]) => [JSON.parse(key), value]),
    );
    mockSetQueriesData.mockImplementation(
      ({ queryKey: prefix }: { queryKey: unknown[] }, updater: (old: unknown) => unknown) => {
        for (const key of cache.keys()) {
          if (isPrefixOf(prefix, JSON.parse(key))) cache.set(key, updater(cache.get(key)));
        }
      },
    );

    useOptimisticListMutation<unknown, string, Item>({
      mutationFn: (id: string) => Promise.resolve(id),
      listKeyPrefix: [["things"], ["things", "urgent"]],
      updateItems: (items, id) => items.filter((item) => item.id !== id),
    });

    const context = await lastOptions<unknown, string, { snapshot: [unknown, unknown][] }>().onMutate(
      "1",
    );

    // Every snapshotted entry — including the one captured while processing
    // the second, narrower prefix — must reflect the PRE-mutation data.
    for (const [, value] of context.snapshot) {
      expect((value as { data: Item[] }).data).toHaveLength(1);
    }
  });
});
