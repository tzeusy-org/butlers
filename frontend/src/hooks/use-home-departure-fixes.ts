/** TanStack Query hooks for the owner-declared Home departure fixes. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDepartureFix,
  getDepartureFixes,
  getDepartureWatch,
  putDepartureFix,
} from "@/api/home-departure-fixes.ts";
import type {
  HomeDepartureFix,
  HomeDepartureFixInput,
  HomeDepartureWatchItem,
} from "@/api/home-departure-fixes.ts";

export const homeDepartureFixesKey = ["home", "departure-fixes"] as const;
export const homeDepartureWatchKey = ["home", "departure-watch"] as const;

export function useHomeDepartureWatch() {
  return useQuery<HomeDepartureWatchItem[]>({
    queryKey: homeDepartureWatchKey,
    queryFn: () => getDepartureWatch(),
    retry: false,
  });
}

export function useHomeDepartureFixes() {
  return useQuery<HomeDepartureFix[]>({
    queryKey: homeDepartureFixesKey,
    queryFn: () => getDepartureFixes(),
    retry: false,
  });
}

function useFixMutation<T>(mutationFn: (input: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: homeDepartureFixesKey });
    },
  });
}

export function useSaveDepartureFix() {
  return useFixMutation((input: HomeDepartureFixInput) => putDepartureFix(input));
}

export function useDeleteDepartureFix() {
  return useFixMutation((entityId: string) => deleteDepartureFix(entityId));
}
