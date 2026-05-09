/**
 * React Query hooks for the travel butler API.
 *
 * Covers three endpoints:
 *  - GET /api/travel/upcoming   → useUpcomingTravel
 *  - GET /api/travel/trips      → useTravelTrips
 *  - GET /api/travel/trips/:id  → useTravelTripSummary
 *
 * No new HTTP routes are introduced — all calls route through existing
 * API functions in client.ts.
 *
 * bead: bu-0eac9
 */

import { useQuery } from "@tanstack/react-query";

import { getTravelTrips, getTravelTripSummary, getTravelUpcoming } from "@/api/index.ts";
import type { TravelTripsParams } from "@/api/index.ts";

/** Fetch upcoming travel overview with urgency-ranked pre-trip actions. */
export function useUpcomingTravel(withinDays?: number) {
  return useQuery({
    queryKey: ["travel", "upcoming", withinDays],
    queryFn: () => getTravelUpcoming(withinDays),
    refetchInterval: 60_000,
  });
}

/** List trips with optional status/date filters and pagination. */
export function useTravelTrips(params?: TravelTripsParams) {
  return useQuery({
    queryKey: ["travel", "trips", params],
    queryFn: () => getTravelTrips(params),
    refetchInterval: 60_000,
  });
}

/** Fetch full trip summary with legs, accommodations, reservations, docs, alerts. */
export function useTravelTripSummary(tripId: string | null) {
  return useQuery({
    queryKey: ["travel", "trip-summary", tripId],
    queryFn: () => getTravelTripSummary(tripId!),
    enabled: !!tripId,
  });
}
