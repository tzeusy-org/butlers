/**
 * Shared TanStack Query source for the model-pricing map used by chat cost
 * estimates. Pricing changes infrequently, so all chat surfaces share one
 * fresh cache entry instead of fetching again each time a panel opens.
 */

import { useQuery } from "@tanstack/react-query";

import { fetchPricingMap } from "@/api/client.ts";

export const PRICING_MAP_QUERY_KEY = ["pricing-map"] as const;
export const PRICING_MAP_STALE_TIME_MS = 5 * 60_000;

export function usePricingMap() {
  return useQuery({
    queryKey: PRICING_MAP_QUERY_KEY,
    queryFn: async () => (await fetchPricingMap()).data,
    staleTime: PRICING_MAP_STALE_TIME_MS,
    // Pricing only decorates cost estimates. Preserve the former one-shot,
    // optional behavior rather than retrying a failed panel-open request.
    retry: false,
  });
}
