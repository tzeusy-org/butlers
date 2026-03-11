/**
 * TanStack Query hooks for the model catalog and butler model override APIs.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createModelCatalogEntry,
  deleteModelCatalogEntry,
  deleteButlerModelOverride,
  listModelCatalog,
  listButlerModelOverrides,
  resolveButlerModel,
  updateModelCatalogEntry,
  upsertButlerModelOverrides,
} from "@/api/index.ts";
import type {
  ModelCatalogCreate,
  ModelCatalogUpdate,
  ButlerModelOverrideUpsert,
} from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Model catalog
// ---------------------------------------------------------------------------

/** Fetch all model catalog entries. */
export function useModelCatalog() {
  return useQuery({
    queryKey: ["model-catalog"],
    queryFn: listModelCatalog,
  });
}

/** Mutation to create a new catalog entry. */
export function useCreateModelCatalogEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelCatalogCreate) => createModelCatalogEntry(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["model-catalog"] });
    },
  });
}

/** Mutation to update an existing catalog entry. */
export function useUpdateModelCatalogEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ModelCatalogUpdate }) =>
      updateModelCatalogEntry(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["model-catalog"] });
    },
  });
}

/** Mutation to delete a catalog entry (cascades to butler overrides). */
export function useDeleteModelCatalogEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteModelCatalogEntry(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["model-catalog"] });
      // Overrides for all butlers are invalidated since cascade delete could affect them
      queryClient.invalidateQueries({ queryKey: ["butler-model-overrides"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Butler model overrides
// ---------------------------------------------------------------------------

/** Fetch model overrides for a specific butler. */
export function useButlerModelOverrides(butlerName: string) {
  return useQuery({
    queryKey: ["butler-model-overrides", butlerName],
    queryFn: () => listButlerModelOverrides(butlerName),
    enabled: !!butlerName,
  });
}

/** Mutation to batch upsert model overrides for a butler. */
export function useUpsertButlerModelOverrides(butlerName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ButlerModelOverrideUpsert[]) =>
      upsertButlerModelOverrides(butlerName, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["butler-model-overrides", butlerName],
      });
    },
  });
}

/** Mutation to delete a single butler model override. */
export function useDeleteButlerModelOverride(butlerName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (overrideId: string) =>
      deleteButlerModelOverride(butlerName, overrideId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["butler-model-overrides", butlerName],
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Model resolution preview
// ---------------------------------------------------------------------------

/** Preview which model would be selected for a butler + complexity tier. */
export function useResolveModel(butlerName: string, complexity: string) {
  return useQuery({
    queryKey: ["resolve-model", butlerName, complexity],
    queryFn: () => resolveButlerModel(butlerName, complexity),
    enabled: !!butlerName && !!complexity,
    staleTime: 30_000,
  });
}
