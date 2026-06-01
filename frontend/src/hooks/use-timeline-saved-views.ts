/**
 * TanStack Query hooks for Timeline custom saved views (bu-vgj88).
 *
 * Endpoints: GET/POST/PATCH/DELETE /api/timeline/saved-views
 *
 * Query key strategy:
 * - timelineSavedViewKeys.all   → broad invalidation anchor for list + individual items
 * - timelineSavedViewKeys.list  → GET /api/timeline/saved-views
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createTimelineSavedView,
  deleteTimelineSavedView,
  listTimelineSavedViews,
  updateTimelineSavedView,
} from "@/api/index.ts";
import type {
  TimelineSavedViewCreateRequest,
  TimelineSavedViewUpdateRequest,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Query key factory
// ---------------------------------------------------------------------------

export const timelineSavedViewKeys = {
  all: ["timeline", "saved-views"] as const,
  list: () => [...timelineSavedViewKeys.all, "list"] as const,
} as const;

// ---------------------------------------------------------------------------
// Query — list
// ---------------------------------------------------------------------------

/**
 * Fetch all custom saved views (newest first).
 *
 * Returns an empty array on success when none exist.
 * On backend error (e.g. 503 database unavailable), isError is true and the
 * toolbar should degrade gracefully — do not crash the toolbar.
 */
export function useTimelineSavedViews(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: timelineSavedViewKeys.list(),
    queryFn: () => listTimelineSavedViews(),
    staleTime: 30_000,
    enabled: options?.enabled !== false,
    // On error, return null — callers treat null as "unavailable, skip custom views".
    // We use onError-equivalent via select + error boundary semantics instead:
    // the hook simply returns { isError, data } and toolbar degrades.
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Create a new saved view.
 *
 * On success: invalidates the list cache so the toolbar re-fetches.
 */
export function useCreateTimelineSavedView() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TimelineSavedViewCreateRequest) => createTimelineSavedView(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: timelineSavedViewKeys.all });
    },
  });
}

/**
 * Update an existing saved view's name and/or filter_spec.
 *
 * On success: invalidates the list cache.
 */
export function useUpdateTimelineSavedView() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TimelineSavedViewUpdateRequest }) =>
      updateTimelineSavedView(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: timelineSavedViewKeys.all });
    },
  });
}

/**
 * Delete a saved view by ID.
 *
 * On success: invalidates the list cache.
 */
export function useDeleteTimelineSavedView() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteTimelineSavedView(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: timelineSavedViewKeys.all });
    },
  });
}
