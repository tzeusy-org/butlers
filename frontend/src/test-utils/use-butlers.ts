import { vi } from "vitest";

import type { ApiMeta, ApiResponse, ButlerSummary } from "@/api/types";
import { useButlers } from "@/hooks/use-butlers";

type UseButlersResult = ReturnType<typeof useButlers>;

export type TestButlerSummary = Omit<ButlerSummary, "sessions_24h"> &
  Partial<Pick<ButlerSummary, "sessions_24h">>;

type TestButlersResponse = Omit<ApiResponse<ButlerSummary[]>, "data" | "meta"> & {
  data: TestButlerSummary[];
  meta?: ApiMeta;
};

export type TestUseButlersResult = Partial<
  Omit<UseButlersResult, "data"> & {
    data: TestButlersResponse;
  }
>;

let lastUseButlersState: TestUseButlersResult | null = null;

export function resetUseButlersMock() {
  lastUseButlersState = null;
  vi.mocked(useButlers).mockReset();
}

export function getLastUseButlersState() {
  return lastUseButlersState;
}

export function setUseButlersState(state: TestUseButlersResult) {
  lastUseButlersState = state;

  const { data: rawData, ...rest } = state;
  const data = rawData
    ? {
        ...rawData,
        meta: rawData.meta ?? {},
        data: rawData.data.map((butler) => ({ sessions_24h: 0, ...butler })),
      }
    : undefined;

  vi.mocked(useButlers).mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
    ...rest,
    data,
  } as UseButlersResult);
}
