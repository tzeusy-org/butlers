// @vitest-environment jsdom

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiResponse,
  CalendarSourceToggleResponse,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceSourceFreshness,
} from "@/api/types.ts";

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    toggleCalendarSource: vi.fn(),
  };
});

import { toggleCalendarSource } from "@/api/index.ts";
import { useToggleCalendarSource } from "./use-calendar-workspace.ts";

const SHARED_SOURCE_KEY = "provider:google:primary";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function makeSource(butlerName: string): CalendarWorkspaceSourceFreshness {
  return {
    source_id: `${butlerName}-source-id`,
    source_key: SHARED_SOURCE_KEY,
    source_kind: "provider",
    lane: "user",
    provider: "google",
    calendar_id: "primary",
    butler_name: butlerName,
    display_name: "Primary",
    writable: true,
    metadata: {},
    cursor_name: null,
    last_synced_at: null,
    last_success_at: null,
    last_error_at: null,
    last_error: null,
    full_sync_required: false,
    sync_state: "fresh",
    staleness_ms: 0,
    error_kind: "none",
    sync_enabled: true,
  };
}

function workspaceResponse(
  source: CalendarWorkspaceSourceFreshness,
): ApiResponse<CalendarWorkspaceReadResponse> {
  return {
    data: {
      entries: [],
      source_freshness: [source],
      lanes: [],
      next_cursor: null,
      has_more: false,
    },
    meta: {},
  };
}

function wrapperFor(queryClient: QueryClient) {
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("useToggleCalendarSource", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates only the owning butler's source when source keys collide across workspace caches", async () => {
    const toggle = deferred<ApiResponse<CalendarSourceToggleResponse>>();
    vi.mocked(toggleCalendarSource).mockReturnValue(toggle.promise);

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    const generalKey = [
      "calendar-workspace",
      {
        view: "user",
        start: "2026-07-18T00:00:00Z",
        end: "2026-07-19T00:00:00Z",
        timezone: "UTC",
        butlers: ["general"],
      },
    ] as const;
    const relationshipKey = [
      "calendar-workspace",
      {
        view: "user",
        start: "2026-07-18T00:00:00Z",
        end: "2026-07-19T00:00:00Z",
        timezone: "UTC",
        butlers: ["relationship"],
      },
    ] as const;
    queryClient.setQueryData(generalKey, workspaceResponse(makeSource("general")));
    queryClient.setQueryData(relationshipKey, workspaceResponse(makeSource("relationship")));

    const { result } = renderHook(() => useToggleCalendarSource(), {
      wrapper: wrapperFor(queryClient),
    });

    let togglePromise!: Promise<ApiResponse<CalendarSourceToggleResponse>>;
    await act(async () => {
      togglePromise = result.current.mutateAsync({
        butler: "general",
        source_key: SHARED_SOURCE_KEY,
        enabled: false,
      });
    });

    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<ApiResponse<CalendarWorkspaceReadResponse>>(generalKey)
          ?.data.source_freshness[0]?.sync_enabled,
      ).toBe(false);
      expect(
        queryClient
          .getQueryData<ApiResponse<CalendarWorkspaceReadResponse>>(relationshipKey)
          ?.data.source_freshness[0]?.sync_enabled,
      ).toBe(true);
    });

    await act(async () => {
      toggle.resolve({
        data: {
          butler: "general",
          source_key: SHARED_SOURCE_KEY,
          source_id: "general-source-id",
          calendar_id: "primary",
          enabled: false,
        },
        meta: {},
      });
      await togglePromise;
    });
  });
});
