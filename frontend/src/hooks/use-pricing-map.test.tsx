// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { PricingMap } from "@/api/types.ts";

const { fetchPricingMapMock } = vi.hoisted(() => ({
  fetchPricingMapMock: vi.fn(),
}));

vi.mock("@/api/client.ts", () => ({
  fetchPricingMap: fetchPricingMapMock,
}));

import {
  PRICING_MAP_QUERY_KEY,
  PRICING_MAP_STALE_TIME_MS,
  usePricingMap,
} from "./use-pricing-map.ts";

const PRICING_MAP: PricingMap = {
  "codex-mini": {
    input_per_million: 1,
    output_per_million: 2,
  },
};

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 3,
        retryDelay: 0,
      },
    },
  });
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe("usePricingMap", () => {
  it("deduplicates concurrent consumers under one pricing-map query key", async () => {
    let resolveFetch: (value: { data: PricingMap }) => void;
    fetchPricingMapMock.mockImplementation(
      () =>
        new Promise<{ data: PricingMap }>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const queryClient = makeQueryClient();
    const wrapper = makeWrapper(queryClient);

    const first = renderHook(() => usePricingMap(), { wrapper });
    const second = renderHook(() => usePricingMap(), { wrapper });

    await waitFor(() => expect(fetchPricingMapMock).toHaveBeenCalledTimes(1));
    resolveFetch!({ data: PRICING_MAP });

    await waitFor(() => expect(first.result.current.data).toEqual(PRICING_MAP));
    expect(second.result.current.data).toEqual(PRICING_MAP);
    expect(queryClient.getQueryData(PRICING_MAP_QUERY_KEY)).toEqual(PRICING_MAP);
  });

  it("reuses the cached map across a chat-surface remount while it is fresh", async () => {
    fetchPricingMapMock.mockResolvedValue({ data: PRICING_MAP });
    const queryClient = makeQueryClient();
    const wrapper = makeWrapper(queryClient);

    const first = renderHook(() => usePricingMap(), { wrapper });
    await waitFor(() => expect(first.result.current.data).toEqual(PRICING_MAP));
    first.unmount();

    const second = renderHook(() => usePricingMap(), { wrapper });

    expect(second.result.current.data).toEqual(PRICING_MAP);
    expect(fetchPricingMapMock).toHaveBeenCalledTimes(1);
    expect(PRICING_MAP_STALE_TIME_MS).toBe(5 * 60_000);
  });

  it("keeps pricing optional when the endpoint fails without retrying", async () => {
    fetchPricingMapMock.mockRejectedValue(new Error("pricing unavailable"));
    const wrapper = makeWrapper(makeQueryClient());

    const result = renderHook(() => usePricingMap(), { wrapper });

    await waitFor(() => expect(result.result.current.isError).toBe(true));
    expect(result.result.current.data).toBeUndefined();
    expect(fetchPricingMapMock).toHaveBeenCalledTimes(1);
  });
});
