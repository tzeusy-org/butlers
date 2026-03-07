/**
 * TanStack Query hooks for the source-filter CRUD API.
 *
 * Endpoints:
 *   GET    /api/switchboard/source-filters
 *   POST   /api/switchboard/source-filters
 *   PATCH  /api/switchboard/source-filters/:id
 *   DELETE /api/switchboard/source-filters/:id
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSourceFilter,
  deleteSourceFilter,
  listSourceFilters,
  updateSourceFilter,
} from "@/api/index.ts";
import type { SourceFilterCreate, SourceFilterUpdate } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const sourceFilterKeys = {
  all: ["source-filters"] as const,
  list: () => [...sourceFilterKeys.all, "list"] as const,
} as const;

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Fetch the full list of named source filters. Staleness: 60s. */
export function useSourceFilters(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: sourceFilterKeys.list(),
    queryFn: () => listSourceFilters(),
    staleTime: 60_000,
    enabled: options?.enabled !== false,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/** Create a new source filter. Invalidates the list on success. */
export function useCreateSourceFilter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SourceFilterCreate) => createSourceFilter(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sourceFilterKeys.all });
    },
  });
}

/** Partially update a source filter. Invalidates the list on success. */
export function useUpdateSourceFilter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SourceFilterUpdate }) =>
      updateSourceFilter(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sourceFilterKeys.all });
    },
  });
}

/** Delete a source filter (connector assignments cascade). Invalidates the list. */
export function useDeleteSourceFilter() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (filterId: string) => deleteSourceFilter(filterId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: sourceFilterKeys.all });
    },
  });
}
