// @vitest-environment jsdom

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ApiResponse,
  CalendarSourceToggleResponse,
  CalendarWorkspaceMetaResponse,
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
  sources: CalendarWorkspaceSourceFreshness | CalendarWorkspaceSourceFreshness[],
): ApiResponse<CalendarWorkspaceReadResponse> {
  return {
    data: {
      entries: [],
      source_freshness: Array.isArray(sources) ? sources : [sources],
      lanes: [],
      next_cursor: null,
      has_more: false,
    },
    meta: {},
  };
}

function metaResponse(
  connected_sources: CalendarWorkspaceSourceFreshness[],
): ApiResponse<CalendarWorkspaceMetaResponse> {
  return {
    data: {
      capabilities: {
        views: ["user", "butler"],
        filters: {},
        sync: { global: true, by_source: true },
      },
      connected_sources,
      writable_calendars: [],
      lane_definitions: [],
      default_timezone: "UTC",
      primary_calendar_id: null,
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

  it("keeps a later successful source toggle when an earlier toggle fails", async () => {
    const toggleGeneral = deferred<ApiResponse<CalendarSourceToggleResponse>>();
    const toggleRelationship = deferred<ApiResponse<CalendarSourceToggleResponse>>();
    vi.mocked(toggleCalendarSource).mockImplementation(({ butler }) =>
      butler === "general" ? toggleGeneral.promise : toggleRelationship.promise,
    );

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
    const metaKey = ["calendar-workspace-meta"] as const;
    const userKey = [
      "calendar-workspace",
      {
        view: "user",
        start: "2026-07-18T00:00:00Z",
        end: "2026-07-19T00:00:00Z",
        timezone: "UTC",
      },
    ] as const;
    const butlerKey = [
      "calendar-workspace",
      {
        view: "butler",
        start: "2026-07-18T00:00:00Z",
        end: "2026-07-19T00:00:00Z",
        timezone: "UTC",
      },
    ] as const;
    const generalSource = makeSource("general");
    const relationshipSource = makeSource("relationship");
    queryClient.setQueryData(metaKey, metaResponse([generalSource, relationshipSource]));
    queryClient.setQueryData(userKey, workspaceResponse([generalSource, relationshipSource]));
    queryClient.setQueryData(butlerKey, workspaceResponse([generalSource, relationshipSource]));

    const { result } = renderHook(() => useToggleCalendarSource(), {
      wrapper: wrapperFor(queryClient),
    });

    let generalPromise!: Promise<ApiResponse<CalendarSourceToggleResponse>>;
    let relationshipPromise!: Promise<ApiResponse<CalendarSourceToggleResponse>>;
    await act(async () => {
      generalPromise = result.current.mutateAsync({
        butler: "general",
        source_key: SHARED_SOURCE_KEY,
        enabled: false,
      });
    });
    await waitFor(() => {
      expect(
        queryClient
          .getQueryData<ApiResponse<CalendarWorkspaceMetaResponse>>(metaKey)
          ?.data.connected_sources.find((source) => source.butler_name === "general")?.sync_enabled,
      ).toBe(false);
    });

    await act(async () => {
      relationshipPromise = result.current.mutateAsync({
        butler: "relationship",
        source_key: SHARED_SOURCE_KEY,
        enabled: false,
      });
    });
    await waitFor(() => {
      const metaSources = queryClient.getQueryData<ApiResponse<CalendarWorkspaceMetaResponse>>(metaKey)
        ?.data.connected_sources;
      expect(metaSources?.every((source) => source.sync_enabled === false)).toBe(true);
    });

    await act(async () => {
      toggleRelationship.resolve({
        data: {
          butler: "relationship",
          source_key: SHARED_SOURCE_KEY,
          source_id: "relationship-source-id",
          calendar_id: "primary",
          enabled: false,
        },
        meta: {},
      });
      await relationshipPromise;
    });

    await act(async () => {
      toggleGeneral.reject(new Error("general toggle failed"));
      await expect(generalPromise).rejects.toThrow("general toggle failed");
    });

    const metaSources = queryClient.getQueryData<ApiResponse<CalendarWorkspaceMetaResponse>>(metaKey)
      ?.data.connected_sources;
    expect(metaSources?.find((source) => source.butler_name === "general")?.sync_enabled).toBe(true);
    expect(metaSources?.find((source) => source.butler_name === "relationship")?.sync_enabled).toBe(false);

    for (const workspaceKey of [userKey, butlerKey]) {
      const sources = queryClient
        .getQueryData<ApiResponse<CalendarWorkspaceReadResponse>>(workspaceKey)
        ?.data.source_freshness;
      expect(sources?.find((source) => source.butler_name === "general")?.sync_enabled).toBe(true);
      expect(sources?.find((source) => source.butler_name === "relationship")?.sync_enabled).toBe(false);
    }
  });
});
