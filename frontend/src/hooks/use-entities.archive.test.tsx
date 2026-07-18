// @vitest-environment jsdom

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  RelationshipEntityListResponse,
  RelationshipEntitySummary,
  RelationshipQueueEntry,
  RelationshipQueueResponse,
} from "@/api/types.ts";

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    archiveRelationshipEntity: vi.fn(),
  };
});

import { archiveRelationshipEntity } from "@/api/index.ts";
import { useArchiveRelationshipEntity } from "./use-entities.ts";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function makeEntity(id: string): RelationshipEntitySummary {
  return {
    id,
    canonical_name: id,
    entity_type: "person",
    aliases: [],
    roles: [],
    metadata: {},
    tier: null,
    last_seen: null,
    first_seen: null,
    contact_fact_count: 0,
    created_at: "2026-07-18T00:00:00Z",
    updated_at: "2026-07-18T00:00:00Z",
  };
}

function makeQueueEntry(entityId: string): RelationshipQueueEntry {
  return {
    entity_id: entityId,
    canonical_name: entityId,
    entity_type: "person",
    bucket: "stale",
    evidence: {},
    last_seen: null,
  };
}

function wrapperFor(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useArchiveRelationshipEntity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("restores only the failed entity when concurrent archives settle out of order", async () => {
    const archiveA = deferred<void>();
    const archiveB = deferred<void>();
    vi.mocked(archiveRelationshipEntity).mockImplementation((entityId) =>
      entityId === "entity-a" ? archiveA.promise : archiveB.promise,
    );

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    const entityListKey = ["relationship-entities", { limit: 20, offset: 0 }] as const;
    const queueKey = ["relationship-entity-queue", { limit: 20, offset: 0 }] as const;
    queryClient.setQueryData<RelationshipEntityListResponse>(entityListKey, {
      items: [makeEntity("entity-a"), makeEntity("entity-b")],
      total: 2,
      limit: 20,
      offset: 0,
    });
    queryClient.setQueryData<RelationshipQueueResponse>(queueKey, {
      items: [makeQueueEntry("entity-a"), makeQueueEntry("entity-b")],
      total: 2,
      limit: 20,
      offset: 0,
    });

    const { result } = renderHook(() => useArchiveRelationshipEntity(), {
      wrapper: wrapperFor(queryClient),
    });

    let archiveAPromise!: Promise<void>;
    let archiveBPromise!: Promise<void>;
    await act(async () => {
      archiveAPromise = result.current.mutateAsync("entity-a");
      archiveBPromise = result.current.mutateAsync("entity-b");
    });

    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<RelationshipEntityListResponse>(entityListKey)
          ?.items.map((entity) => entity.id),
      ).toEqual([]);
    });

    await act(async () => {
      archiveB.resolve();
      await archiveBPromise;
    });

    await act(async () => {
      archiveA.reject(new Error("archive A failed"));
      await expect(archiveAPromise).rejects.toThrow("archive A failed");
    });

    const entityList = queryClient.getQueryData<RelationshipEntityListResponse>(entityListKey);
    const queue = queryClient.getQueryData<RelationshipQueueResponse>(queueKey);
    expect(entityList?.items.map((entity) => entity.id)).toEqual(["entity-a"]);
    expect(entityList?.total).toBe(1);
    expect(queue?.items.map((entry) => entry.entity_id)).toEqual(["entity-a"]);
    expect(queue?.total).toBe(1);
  });

  it("restores a failed earlier archive before its surviving successor", async () => {
    const archiveA = deferred<void>();
    const archiveB = deferred<void>();
    vi.mocked(archiveRelationshipEntity).mockImplementation((entityId) =>
      entityId === "entity-a" ? archiveA.promise : archiveB.promise,
    );

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    const entityListKey = ["relationship-entities", { limit: 20, offset: 0 }] as const;
    const queueKey = ["relationship-entity-queue", { limit: 20, offset: 0 }] as const;
    queryClient.setQueryData<RelationshipEntityListResponse>(entityListKey, {
      items: [makeEntity("entity-a"), makeEntity("entity-b"), makeEntity("entity-c")],
      total: 3,
      limit: 20,
      offset: 0,
    });
    queryClient.setQueryData<RelationshipQueueResponse>(queueKey, {
      items: [makeQueueEntry("entity-a"), makeQueueEntry("entity-b"), makeQueueEntry("entity-c")],
      total: 3,
      limit: 20,
      offset: 0,
    });

    const { result } = renderHook(() => useArchiveRelationshipEntity(), {
      wrapper: wrapperFor(queryClient),
    });

    let archiveAPromise!: Promise<void>;
    let archiveBPromise!: Promise<void>;
    await act(async () => {
      archiveBPromise = result.current.mutateAsync("entity-b");
    });
    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<RelationshipEntityListResponse>(entityListKey)
          ?.items.map((entity) => entity.id),
      ).toEqual(["entity-a", "entity-c"]);
    });

    await act(async () => {
      archiveAPromise = result.current.mutateAsync("entity-a");
    });
    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<RelationshipEntityListResponse>(entityListKey)
          ?.items.map((entity) => entity.id),
      ).toEqual(["entity-c"]);
    });

    await act(async () => {
      archiveA.resolve();
      await archiveAPromise;
    });

    await act(async () => {
      archiveB.reject(new Error("archive B failed"));
      await expect(archiveBPromise).rejects.toThrow("archive B failed");
    });

    const entityList = queryClient.getQueryData<RelationshipEntityListResponse>(entityListKey);
    const queue = queryClient.getQueryData<RelationshipQueueResponse>(queueKey);
    expect(entityList?.items.map((entity) => entity.id)).toEqual(["entity-b", "entity-c"]);
    expect(entityList?.total).toBe(2);
    expect(queue?.items.map((entry) => entry.entity_id)).toEqual(["entity-b", "entity-c"]);
    expect(queue?.total).toBe(2);
  });
});
