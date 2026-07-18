// @vitest-environment jsdom

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiResponse,
  CalendarDuplicateCluster,
  CalendarDuplicatesResponse,
  CalendarKeepSeparateResponse,
  UnifiedCalendarEntry,
} from "@/api/types.ts";

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    setCalendarKeepSeparate: vi.fn(),
  };
});

import { setCalendarKeepSeparate } from "@/api/index.ts";
import { useSetCalendarKeepSeparate } from "./use-calendar-workspace.ts";

const CLUSTER_A = "title\x01first event\x011750582800000";
const CLUSTER_B = "title\x01second event\x011750586400000";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function entry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "11111111-1111-1111-1111-111111111111",
    event_id: null,
    view: "user",
    source_type: "provider_event",
    source_key: "google:primary",
    title: "Team standup",
    start_at: "2026-06-22T09:00:00+00:00",
    end_at: "2026-06-22T09:30:00+00:00",
    timezone: "UTC",
    all_day: false,
    calendar_id: "primary",
    provider_event_id: "evt-1",
    butler_name: "general",
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "active",
    sync_state: null,
    editable: false,
    metadata: {},
    source_butler: "general",
    source_session_id: null,
    ...overrides,
  };
}

function cluster(clusterKey: string, keepSeparate = false): CalendarDuplicateCluster {
  return {
    cluster_key: clusterKey,
    match_pass: "title",
    member_count: 2,
    keep_separate: keepSeparate,
    kept_entry: entry({ entry_id: `${clusterKey}-kept` }),
    duplicate_entries: [entry({ entry_id: `${clusterKey}-duplicate`, source_key: "google:work" })],
  };
}

function duplicatesResponse(
  clusters: CalendarDuplicateCluster[],
): ApiResponse<CalendarDuplicatesResponse> {
  return {
    data: {
      clusters,
      rules: { match_strategy: "balanced", noisy_threshold: 2 },
      available: true,
    },
    meta: {},
  };
}

function wrapperFor(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useSetCalendarKeepSeparate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps B's later successful keep-separate update when A fails", async () => {
    const toggleA = deferred<ApiResponse<CalendarKeepSeparateResponse>>();
    const toggleB = deferred<ApiResponse<CalendarKeepSeparateResponse>>();
    vi.mocked(setCalendarKeepSeparate).mockImplementation(({ cluster_key }) =>
      cluster_key === CLUSTER_A ? toggleA.promise : toggleB.promise,
    );

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    const duplicatesKey = [
      "calendar-duplicates",
      {
        view: "user",
        start: "2026-07-18T00:00:00Z",
        end: "2026-07-19T00:00:00Z",
        timezone: "UTC",
      },
    ] as const;
    queryClient.setQueryData(duplicatesKey, duplicatesResponse([cluster(CLUSTER_A), cluster(CLUSTER_B)]));

    const { result } = renderHook(() => useSetCalendarKeepSeparate(), {
      wrapper: wrapperFor(queryClient),
    });

    let promiseA!: Promise<ApiResponse<CalendarKeepSeparateResponse>>;
    let promiseB!: Promise<ApiResponse<CalendarKeepSeparateResponse>>;
    await act(async () => {
      promiseA = result.current.mutateAsync({ cluster_key: CLUSTER_A, keep_separate: true });
    });
    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<ApiResponse<CalendarDuplicatesResponse>>(duplicatesKey)
          ?.data.clusters.find((item) => item.cluster_key === CLUSTER_A)?.keep_separate,
      ).toBe(true);
    });

    await act(async () => {
      promiseB = result.current.mutateAsync({ cluster_key: CLUSTER_B, keep_separate: true });
    });
    await waitFor(() => {
      const clusters = queryClient.getQueryData<ApiResponse<CalendarDuplicatesResponse>>(duplicatesKey)?.data
        .clusters;
      expect(clusters?.every((item) => item.keep_separate)).toBe(true);
    });

    await act(async () => {
      toggleB.resolve({ data: { cluster_key: CLUSTER_B, keep_separate: true }, meta: {} });
      await promiseB;
    });

    await act(async () => {
      toggleA.reject(new Error("A keep-separate toggle failed"));
      await expect(promiseA).rejects.toThrow("A keep-separate toggle failed");
    });

    const clusters = queryClient.getQueryData<ApiResponse<CalendarDuplicatesResponse>>(duplicatesKey)?.data
      .clusters;
    expect(clusters?.find((item) => item.cluster_key === CLUSTER_A)?.keep_separate).toBe(false);
    expect(clusters?.find((item) => item.cluster_key === CLUSTER_B)?.keep_separate).toBe(true);
  });
});
