/** TanStack Query hooks for the owner-declared Home safety sensors. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteHomeSafetyWatch,
  getHomeSafetyWatch,
  putHomeSafetyWatch,
} from "@/api/home-safety-watch.ts";
import type {
  HomeSafetyRole,
  HomeSafetyWatchEntry,
  HomeSafetyWatchInput,
} from "@/api/home-safety-watch.ts";

export const homeSafetyWatchKey = ["home", "safety-watch"] as const;

export function useHomeSafetyWatch() {
  return useQuery<HomeSafetyWatchEntry[]>({
    queryKey: homeSafetyWatchKey,
    queryFn: () => getHomeSafetyWatch(),
    retry: false,
  });
}

export function useSaveHomeSafetyWatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: HomeSafetyWatchInput) => putHomeSafetyWatch(input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: homeSafetyWatchKey });
    },
  });
}

export function useDeleteHomeSafetyWatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (target: { entityId: string; role: HomeSafetyRole }) =>
      deleteHomeSafetyWatch(target.entityId, target.role),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: homeSafetyWatchKey });
    },
  });
}
