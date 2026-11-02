/** TanStack Query hooks for the owner place registry. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createOwnerPlace,
  deleteOwnerPlace,
  getOwnerPlaces,
  updateOwnerPlace,
} from "@/api/home-places.ts";
import type { OwnerPlace, OwnerPlaceInput } from "@/api/home-places.ts";

export const ownerPlacesKey = ["home", "places"] as const;

export function useOwnerPlaces() {
  return useQuery<OwnerPlace[]>({
    queryKey: ownerPlacesKey,
    queryFn: () => getOwnerPlaces(),
    retry: false,
  });
}

export type SaveOwnerPlace =
  | { id: null; input: OwnerPlaceInput }
  | { id: string; input: OwnerPlaceInput; expectedVersion: number };

export function useSaveOwnerPlace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (save: SaveOwnerPlace) =>
      save.id === null
        ? createOwnerPlace(save.input)
        : updateOwnerPlace(save.id, save.input, save.expectedVersion),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ownerPlacesKey });
    },
  });
}

export function useDeleteOwnerPlace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (target: { id: string; expectedVersion: number }) =>
      deleteOwnerPlace(target.id, target.expectedVersion),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ownerPlacesKey });
    },
  });
}
