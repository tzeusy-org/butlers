// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "@/api/index.ts";
import {
  ingestionEventKeys, useIngestionEvents, useIngestionEventDetail,
  useIngestionEventSessions, useIngestionWindowRollup, useIngestionEventsHistogram,
} from "./use-ingestion-events";

vi.mock("@/api/index.ts", () => ({
  listIngestionEvents: vi.fn(), getIngestionEvent: vi.fn(),
  getIngestionEventSessions: vi.fn(),
  getIngestionWindowRollup: vi.fn(), getIngestionEventsHistogram: vi.fn(),
  getIngestionEventReplays: vi.fn(), getIngestionEventSenderContact: vi.fn(),
  getIngestionEventPayload: vi.fn(),
}));

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}

const page = (id: string, cursor: string | null) => ({
  data: [{ id }], meta: { next_cursor: cursor, has_more: cursor !== null },
}) as Awaited<ReturnType<typeof api.listIngestionEvents>>;

afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

it("refreshes only the head while retaining paged history and its retry cursor", async () => {
  const { client, wrapper } = setup();
  vi.mocked(api.listIngestionEvents)
    .mockResolvedValueOnce(page("first", "older"))
    .mockResolvedValueOnce(page("second", "oldest"))
    .mockResolvedValueOnce(page("new", "first"))
    .mockRejectedValueOnce(new Error("unavailable"))
    .mockResolvedValueOnce(page("third", null));
  const { result } = renderHook(() => useIngestionEvents({}, { refetchInterval: false }), { wrapper });
  await waitFor(() => expect(result.current.data?.pages[0].data[0].id).toBe("first"));
  await act(async () => { await result.current.fetchNextPage(); });
  await act(async () => { await client.invalidateQueries({ queryKey: ingestionEventKeys.list({}) }); });
  expect(api.listIngestionEvents).toHaveBeenCalledTimes(3);
  expect(result.current.data?.pages.flatMap(p => p.data.map(e => e.id))).toEqual(["first", "second"]);
  await waitFor(() => expect(result.current.newCount).toBe(1));
  await act(async () => { await result.current.fetchNextPage(); });
  expect(result.current.isFetchNextPageError).toBe(true);
  expect(result.current.data?.pages).toHaveLength(2);
  await act(async () => { await result.current.fetchNextPage(); });
  expect(vi.mocked(api.listIngestionEvents).mock.calls.slice(3).map(c => c[0]?.cursor)).toEqual(["oldest", "oldest"]);
  expect(result.current.data?.pages).toHaveLength(3);
  act(() => result.current.showNewEvents());
  expect(result.current.data?.pages[0].data[0].id).toBe("new");
  expect(result.current.newCount).toBe(0);
});

it("aborts obsolete history on a filter change and ignores its late response", async () => {
  const { wrapper } = setup();
  let resolveOlder!: (value: ReturnType<typeof page>) => void;
  vi.mocked(api.listIngestionEvents)
    .mockResolvedValueOnce(page("first", "older"))
    .mockImplementationOnce(() => new Promise(resolve => { resolveOlder = resolve; }))
    .mockResolvedValueOnce(page("filtered", null));
  const { result, rerender } = renderHook(({ q }) => useIngestionEvents({ q }, { refetchInterval: false }), {
    wrapper, initialProps: { q: "one" },
  });
  await waitFor(() => expect(result.current.data?.pages[0].data[0].id).toBe("first"));
  act(() => { void result.current.fetchNextPage(); });
  const signal = vi.mocked(api.listIngestionEvents).mock.calls[1][1];
  rerender({ q: "two" });
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolveOlder(page("obsolete", null)); });
  await waitFor(() => expect(result.current.data?.pages[0].data[0].id).toBe("filtered"));
  expect(result.current.data?.pages).toHaveLength(1);
});

it("reconciles existing head rows without moving history, including after reactivation", async () => {
  const { wrapper } = setup();
  const initial = page("first", "older");
  initial.data[0].status = "replay_pending";
  const refreshed = page("first", "different-cursor");
  refreshed.data[0].status = "replay_complete";
  let resolveOldest!: (value: ReturnType<typeof page>) => void;
  vi.mocked(api.listIngestionEvents)
    .mockResolvedValueOnce(initial)
    .mockResolvedValueOnce(page("second", "oldest"))
    .mockImplementationOnce(() => new Promise(resolve => { resolveOldest = resolve; }))
    .mockResolvedValueOnce(refreshed)
    .mockResolvedValueOnce(page("new", "first"));
  const { result, rerender } = renderHook(({ enabled }) => useIngestionEvents({}, {
    enabled, refetchInterval: false,
  }), { wrapper, initialProps: { enabled: true } });
  await waitFor(() => expect(result.current.hasNextPage).toBe(true));
  await act(async () => { await result.current.fetchNextPage(); });
  rerender({ enabled: false });
  expect(result.current.data?.pages).toHaveLength(2);
  rerender({ enabled: true });
  let oldestRequest!: Promise<void>;
  act(() => { oldestRequest = result.current.fetchNextPage(); });
  await act(async () => { await result.current.refetch(); });
  await waitFor(() => expect(result.current.data?.pages[0].data[0].status).toBe("replay_complete"));
  expect(result.current.data?.pages.map(p => p.data[0].id)).toEqual(["first", "second"]);
  expect(result.current.data?.pages[0].meta.next_cursor).toBe("older");
  expect(result.current.newCount).toBe(0);
  expect(result.current.isFollowingLive).toBe(false);
  await act(async () => { await result.current.refetch(); });
  await waitFor(() => expect(result.current.newCount).toBe(1));
  expect(result.current.data?.pages[0].data[0].status).toBe("replay_complete");
  expect(result.current.data?.pages[0].meta.next_cursor).toBe("older");
  await act(async () => {
    resolveOldest(page("third", null));
    await oldestRequest;
  });
  expect(result.current.data?.pages.map(p => p.data[0].id)).toEqual(["first", "second", "third"]);
  expect(result.current.data?.pages[0].data[0].status).toBe("replay_complete");
});

it("aborts an in-flight detail read when its drawer unmounts", async () => {
  const { wrapper } = setup();
  vi.mocked(api.getIngestionEvent).mockImplementation(() => new Promise(() => {}));
  const { unmount } = renderHook(() => useIngestionEventDetail("event"), { wrapper });
  await waitFor(() => expect(api.getIngestionEvent).toHaveBeenCalledOnce());
  const signal = vi.mocked(api.getIngestionEvent).mock.calls[0][1];
  expect(signal?.aborted).toBe(false);
  unmount();
  expect(signal?.aborted).toBe(true);
});

it("polls the head only and disables pagination when the surface is inactive", async () => {
  const { wrapper } = setup();
  vi.mocked(api.listIngestionEvents).mockImplementation(async (params) =>
    params?.cursor ? page("second", "oldest") : page("first", "older"),
  );
  const { result, rerender } = renderHook(({ enabled }) => useIngestionEvents({}, {
    // eslint-disable-next-line no-restricted-syntax -- Accelerated timer exercises head-only polling without a 30-second test.
    enabled, refetchInterval: 40,
  }), { wrapper, initialProps: { enabled: true } });
  await waitFor(() => expect(result.current.hasNextPage).toBe(true));
  await act(async () => { await result.current.fetchNextPage(); });
  const headCalls = () => vi.mocked(api.listIngestionEvents).mock.calls.filter(([params]) => !params?.cursor).length;
  const previousCalls = headCalls();
  await waitFor(() => expect(headCalls()).toBeGreaterThan(previousCalls));
  expect(vi.mocked(api.listIngestionEvents).mock.calls.filter(([params]) => params?.cursor)).toHaveLength(1);
  rerender({ enabled: false });
  expect(result.current.hasNextPage).toBe(false);
  const count = vi.mocked(api.listIngestionEvents).mock.calls.length;
  await act(async () => { await result.current.fetchNextPage(); });
  expect(api.listIngestionEvents).toHaveBeenCalledTimes(count);
});

it("reconciles active drawer and aggregate reads every 30 seconds without bus events", async () => {
  vi.useFakeTimers();
  const { wrapper } = setup();
  vi.mocked(api.getIngestionEvent).mockResolvedValue({ data: {} } as Awaited<ReturnType<typeof api.getIngestionEvent>>);
  vi.mocked(api.getIngestionEventSessions).mockResolvedValue({ data: [], meta: {} });
  vi.mocked(api.getIngestionWindowRollup).mockResolvedValue({
    events: 0, sessions: 0, cost: null, window: { from: null, to: null },
  });
  vi.mocked(api.getIngestionEventsHistogram).mockResolvedValue({ buckets: [], bucket: "1m" });
  const readers = [api.getIngestionEvent, api.getIngestionEventSessions,
    api.getIngestionWindowRollup, api.getIngestionEventsHistogram];
  const { unmount, rerender } = renderHook(({ enabled }) => {
    useIngestionEventDetail("event", { enabled });
    useIngestionEventSessions("event", { enabled });
    useIngestionWindowRollup({}, { enabled });
    useIngestionEventsHistogram({ trace_id: "trace" }, { enabled });
  }, { wrapper, initialProps: { enabled: true } });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  for (const read of readers) expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  for (const read of readers) expect(read).toHaveBeenCalledTimes(2);
  rerender({ enabled: false });
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  for (const read of readers) expect(read).toHaveBeenCalledTimes(2);
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  for (const read of readers) expect(read).toHaveBeenCalledTimes(2);
});
