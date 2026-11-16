/** TanStack Query hooks for the raw location retention policy (/api/chronicler/location-retention). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getLocationRetention,
  setLocationRetention,
  type LocationRetentionUpdate,
} from "@/api/location-retention.ts";

export const LOCATION_RETENTION_KEY = ["location-retention"] as const;

export function useLocationRetention() {
  return useQuery({
    queryKey: LOCATION_RETENTION_KEY,
    queryFn: getLocationRetention,
    staleTime: 10_000,
  });
}

// HONEST-PENDING, never optimistic: the switch decides whether raw location evidence is deleted,
// so the page shows only what the server holds. Every settle (a refusal or a 409 included)
// refetches, so a stale version is replaced by the current one.
export function useSetLocationRetention() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (update: LocationRetentionUpdate) => setLocationRetention(update),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LOCATION_RETENTION_KEY }),
  });
}
