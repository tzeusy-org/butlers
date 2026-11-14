/** TanStack Query hooks for the owner-declared Home away-safety values. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteHomeAwayDoor,
  deleteHomeBlindDwell,
  deleteHomeFreezer,
  getHomeAwaySafety,
  putHomeAwayDoor,
  putHomeBlindDwell,
  putHomeFreezer,
} from "@/api/home-away-safety.ts";
import type { HomeAwaySafetyConfig, HomeFreezerInput } from "@/api/home-away-safety.ts";

export const homeAwaySafetyKey = ["home", "away-safety"] as const;

export function useHomeAwaySafety() {
  return useQuery<HomeAwaySafetyConfig>({
    queryKey: homeAwaySafetyKey,
    queryFn: () => getHomeAwaySafety(),
    retry: false,
  });
}

function useAwaySafetyMutation<T>(mutationFn: (input: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: homeAwaySafetyKey });
    },
  });
}

export function useSaveHomeFreezer() {
  return useAwaySafetyMutation((input: HomeFreezerInput) => putHomeFreezer(input));
}

export function useDeleteHomeFreezer() {
  return useAwaySafetyMutation((entityId: string) => deleteHomeFreezer(entityId));
}

export function useArmHomeAwayDoor() {
  return useAwaySafetyMutation((target: { entityId: string; minutes: number }) =>
    putHomeAwayDoor(target.entityId, target.minutes),
  );
}

export function useDisarmHomeAwayDoor() {
  return useAwaySafetyMutation((entityId: string) => deleteHomeAwayDoor(entityId));
}

export function useSaveHomeBlindDwell() {
  return useAwaySafetyMutation((minutes: number) => putHomeBlindDwell(minutes));
}

export function useClearHomeBlindDwell() {
  return useAwaySafetyMutation<void>(() => deleteHomeBlindDwell());
}
